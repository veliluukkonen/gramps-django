"""
Transaction history and raw transaction endpoints.

- GET  /api/transactions/history/            list (X-Total-Count header)
- GET  /api/transactions/history/<id>        single transaction
- GET  /api/transactions/history/<id>/undo   conflict check for an undo
- POST /api/transactions/history/<id>/undo   undo the transaction
- POST /api/transactions/?undo=1&force=1     apply (or reverse) a transaction list

Response shapes follow gramps-web-api ``history.py`` / ``transactions.py``.
"""

from django.db import IntegrityError
from rest_framework import status
from rest_framework.response import Response

from apps.auth.permissions import (
    PERM_ADD_OBJ,
    PERM_DEL_OBJ,
    PERM_EDIT_OBJ,
    PERM_VIEW_PRIVATE,
)
from apps.core.models import Transaction, TransactionChange
from apps.core.writes import (
    NotFound,
    WriteError,
    model_for_class,
    normalize_class_name,
    run_transaction,
    serialize,
)

from .common import TreeAPIView, error_response, is_true

TRANS_TYPE_NAMES = {
    TransactionChange.TXN_ADD: "add",
    TransactionChange.TXN_UPDATE: "update",
    TransactionChange.TXN_DELETE: "delete",
}
WRITE_PERMS = [PERM_ADD_OBJ, PERM_EDIT_OBJ, PERM_DEL_OBJ]


# --- serialization ---------------------------------------------------------


def user_info(user):
    """``connection.user`` entry as in gramps-web-api ``fix_transaction_user``."""
    if user is None:
        return None
    return {"name": user.username, "full_name": user.full_name}


def change_to_dict(change, old_data=True, new_data=True):
    data = {
        "id": change.id,
        "obj_class": change.obj_class,
        "trans_type": change.trans_type,
        "obj_handle": change.obj_handle,
        "ref_handle": None,
        "timestamp": change.transaction.timestamp,
    }
    if old_data:
        data["old_data"] = change.old_data
    if new_data:
        data["new_data"] = change.new_data
    return data


def transaction_to_dict(txn, old_data=True, new_data=True):
    changes = list(txn.changes.all())
    return {
        "id": txn.id,
        "connection": {
            "id": txn.id,
            "timestamp": txn.timestamp,
            "user": user_info(txn.user),
        },
        "description": txn.description,
        "first": changes[0].id if changes else None,
        "last": changes[-1].id if changes else None,
        "undo": bool(txn.undone),
        "timestamp": txn.timestamp,
        "changes": [change_to_dict(c, old_data, new_data) for c in changes],
    }


def transaction_to_payload(txn):
    """Transaction JSON list (``type/handle/_class/old/new``) of a stored transaction."""
    return [
        {
            "type": TRANS_TYPE_NAMES[c.trans_type],
            "handle": c.obj_handle,
            "_class": c.obj_class,
            "old": c.old_data,
            "new": c.new_data,
        }
        for c in txn.changes.all()
    ]


def reverse_transaction(payload):
    """Reverse a transaction JSON list (gramps-web-api ``reverse_transaction``)."""
    type_reversed = {"add": "delete", "delete": "add", "update": "update"}
    return [
        {
            "type": type_reversed[item["type"]],
            "handle": item["handle"],
            "_class": item["_class"],
            "old": item.get("new"),
            "new": item.get("old"),
        }
        for item in reversed(payload)
    ]


# --- applying transactions -------------------------------------------------


def _current_object(class_name, handle):
    model = model_for_class(class_name)
    try:
        return model.objects.get(pk=handle)
    except model.DoesNotExist:
        return None


def old_unchanged(class_name, handle, old_data):
    """
    Check that the object still matches ``old_data`` (the state the
    transaction expects). ``change`` timestamps are ignored.
    """
    instance = _current_object(class_name, handle)
    if instance is None:
        return not old_data
    if not old_data:
        return False
    current = serialize(instance)
    for key, value in old_data.items():
        if key in ("change", "_class"):
            continue
        if current.get(key) != value:
            return False
    return True


def find_conflicts(payload):
    """Conflicts that would prevent applying ``payload`` (gramps-web-api shape)."""
    conflicts = []
    for item in payload:
        class_name = normalize_class_name(item.get("_class"))
        handle = item.get("handle")
        trans_type = item.get("type")
        try:
            if trans_type == "add":
                if _current_object(class_name, handle) is not None:
                    conflicts.append({
                        "change_index": len(conflicts),
                        "object_class": class_name,
                        "handle": handle,
                        "conflict_type": "object_exists",
                        "description": f"Cannot add: object with handle {handle} already exists",
                    })
            elif not old_unchanged(class_name, handle, item.get("old")):
                conflicts.append({
                    "change_index": len(conflicts),
                    "object_class": class_name,
                    "handle": handle,
                    "conflict_type": "object_changed",
                    "description": (
                        f"Object {class_name} with handle {handle} has been modified "
                        "since the original transaction"
                    ),
                })
        except WriteError as exc:
            conflicts.append({
                "change_index": len(conflicts),
                "object_class": class_name,
                "handle": handle,
                "conflict_type": "check_failed",
                "description": f"Could not verify object state: {exc}",
            })
    return conflicts


def validate_payload(payload):
    if not isinstance(payload, list) or not payload:
        raise WriteError("Empty payload")
    for item in payload:
        if not isinstance(item, dict):
            raise WriteError("Transaction items must be objects")
        if item.get("type") not in ("add", "update", "delete"):
            raise WriteError(f"Unexpected transaction type '{item.get('type')}'")
        if not item.get("handle") or not item.get("_class"):
            raise WriteError("Transaction items need a handle and a _class")


def apply_payload(user, description, payload, force=False):
    """Apply a transaction JSON list; returns the resulting transaction list."""
    validate_payload(payload)

    def func(builder):
        for item in payload:
            class_name = normalize_class_name(item["_class"])
            handle = item["handle"]
            trans_type = item["type"]
            # Objects already touched by a cascade (e.g. family links on
            # people) inside this transaction are not checked again.
            if not force and (class_name, handle) not in builder._touched:
                if not old_unchanged(class_name, handle, item.get("old")):
                    raise WriteError("Object has changed")
            new_data = item.get("new")
            if trans_type == "delete":
                builder.delete(class_name, handle)
            elif trans_type == "add":
                if not isinstance(new_data, dict):
                    raise WriteError("Missing object data for add")
                builder.add({**new_data, "handle": handle}, class_name)
            else:
                if not isinstance(new_data, dict):
                    raise WriteError("Missing object data for update")
                builder.update(new_data, class_name, handle)

    return run_transaction(user, description, func)


# --- views -------------------------------------------------------------------


class TransactionsHistoryView(TreeAPIView):
    """GET /api/transactions/history/"""

    permissions_by_method = {"GET": [PERM_VIEW_PRIVATE]}

    def get(self, request):
        params = request.query_params
        try:
            page = int(params.get("page", 0) or 0)
            pagesize = int(params.get("pagesize", 20) or 20)
            before = float(params["before"]) if params.get("before") else None
            after = float(params["after"]) if params.get("after") else None
        except ValueError:
            return error_response("Invalid query parameter")
        if page < 0 or pagesize < 1:
            return error_response("Invalid pagination parameters")

        qs = Transaction.objects.select_related("user").prefetch_related("changes")
        if before is not None:
            qs = qs.filter(timestamp__lt=before)
        if after is not None:
            qs = qs.filter(timestamp__gt=after)
        count = qs.count()
        qs = qs.order_by("-id" if params.get("sort") == "-id" else "id")
        if page:
            qs = qs[(page - 1) * pagesize:page * pagesize]
        old = is_true(params.get("old", ""))
        new = is_true(params.get("new", ""))
        data = [transaction_to_dict(t, old, new) for t in qs]
        response = Response(data)
        response["X-Total-Count"] = count
        return response


def _get_transaction(transaction_id):
    try:
        return (
            Transaction.objects.select_related("user")
            .prefetch_related("changes")
            .get(pk=transaction_id)
        )
    except Transaction.DoesNotExist:
        return None


class TransactionHistoryView(TreeAPIView):
    """GET /api/transactions/history/<id>"""

    permissions_by_method = {"GET": [PERM_VIEW_PRIVATE]}

    def get(self, request, transaction_id):
        txn = _get_transaction(transaction_id)
        if txn is None:
            return error_response(f"Transaction {transaction_id} not found", status.HTTP_404_NOT_FOUND)
        old = is_true(request.query_params.get("old", ""))
        new = is_true(request.query_params.get("new", ""))
        return Response(transaction_to_dict(txn, old, new))


class TransactionUndoView(TreeAPIView):
    """GET (check) / POST (perform) /api/transactions/history/<id>/undo"""

    permissions_by_method = {"GET": [PERM_VIEW_PRIVATE], "POST": WRITE_PERMS}

    def get(self, request, transaction_id):
        txn = _get_transaction(transaction_id)
        if txn is None:
            return error_response(f"Transaction {transaction_id} not found", status.HTTP_404_NOT_FOUND)
        payload = reverse_transaction(transaction_to_payload(txn))
        conflicts = find_conflicts(payload)
        return Response({
            "transaction_id": txn.id,
            "can_undo_without_force": not conflicts,
            "total_changes": len(payload),
            "conflicts_count": len(conflicts),
            "conflicts": conflicts,
        })

    def post(self, request, transaction_id):
        txn = _get_transaction(transaction_id)
        if txn is None:
            return error_response(f"Transaction {transaction_id} not found", status.HTTP_404_NOT_FOUND)
        payload = reverse_transaction(transaction_to_payload(txn))
        force = is_true(request.query_params.get("force", ""))
        try:
            result = apply_payload(
                request.user, f"Undo: {txn.description}" if txn.description else "Undo", payload, force
            )
        except NotFound as exc:
            return error_response(str(exc), status.HTTP_404_NOT_FOUND)
        except (WriteError, ValueError, TypeError, KeyError) as exc:
            return error_response(str(exc))
        except IntegrityError as exc:
            return error_response(f"Referenced object does not exist: {exc}")
        Transaction.objects.filter(pk=txn.id).update(undone=True)
        response = Response(result)
        response["X-Total-Count"] = len(result)
        return response


class TransactionsView(TreeAPIView):
    """POST /api/transactions/?undo=1&force=1"""

    permissions_by_method = {"POST": WRITE_PERMS}

    def post(self, request):
        payload = request.data
        if not payload:
            return error_response("Empty payload")
        is_undo = is_true(request.query_params.get("undo", ""))
        force = is_true(request.query_params.get("force", ""))
        try:
            validate_payload(payload)
            if is_undo:
                payload = reverse_transaction(payload)
            result = apply_payload(
                request.user, "Undo transaction" if is_undo else "Raw transaction", payload, force
            )
        except NotFound as exc:
            return error_response(str(exc), status.HTTP_404_NOT_FOUND)
        except (WriteError, ValueError, TypeError, KeyError) as exc:
            return error_response(str(exc))
        except IntegrityError as exc:
            return error_response(f"Referenced object does not exist: {exc}")
        response = Response(result)
        response["X-Total-Count"] = len(result)
        return response


__all__ = [
    "TransactionsHistoryView",
    "TransactionHistoryView",
    "TransactionUndoView",
    "TransactionsView",
    "reverse_transaction",
    "apply_payload",
]
