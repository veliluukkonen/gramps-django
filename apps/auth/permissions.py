"""
Gramps role-based permission system.

Mirrors gramps-web-api auth/const.py exactly so JWT claims
are compatible with the existing frontend.
"""

from rest_framework.permissions import BasePermission

# Role levels
ROLE_DISABLED = -1
ROLE_UNCONFIRMED = -2
ROLE_GUEST = 0
ROLE_MEMBER = 1
ROLE_CONTRIBUTOR = 2
ROLE_EDITOR = 3
ROLE_OWNER = 4
ROLE_ADMIN = 5

ROLE_CHOICES = [
    (ROLE_DISABLED, "Disabled"),
    (ROLE_UNCONFIRMED, "Unconfirmed"),
    (ROLE_GUEST, "Guest"),
    (ROLE_MEMBER, "Member"),
    (ROLE_CONTRIBUTOR, "Contributor"),
    (ROLE_EDITOR, "Editor"),
    (ROLE_OWNER, "Owner"),
    (ROLE_ADMIN, "Admin"),
]

# Permission strings — must match frontend expectations exactly
PERM_EDIT_OWN_USER = "EditOwnUser"
PERM_VIEW_PRIVATE = "ViewPrivate"
PERM_ADD_OBJ = "AddObject"
PERM_EDIT_OBJ = "EditObject"
PERM_DEL_OBJ = "DeleteObject"
PERM_DEL_OBJ_BATCH = "BatchDeleteObjects"
PERM_EDIT_NAME_GROUP = "EditNameGroup"
PERM_ADD_USER = "AddUser"
PERM_DEL_USER = "DeleteUser"
PERM_EDIT_OTHER_USER = "EditOtherUser"
PERM_EDIT_USER_ROLE = "EditUserRole"
PERM_MAKE_ADMIN = "MakeAdmin"
PERM_VIEW_OTHER_USER = "ViewOtherUser"
PERM_IMPORT_FILE = "ImportFile"
PERM_TRIGGER_REINDEX = "TriggerReindex"
PERM_EDIT_TREE = "EditTree"
PERM_REPAIR_TREE = "RepairTree"
PERM_UPGRADE_TREE_SCHEMA = "UpgradeSchema"
PERM_EDIT_TREE_MIN_ROLE_AI = "EditTreeMinRoleAI"
PERM_USE_CHAT = "UseChat"
PERM_VIEW_SETTINGS = "ViewSettings"
PERM_EDIT_SETTINGS = "EditSettings"
PERM_VIEW_OTHER_TREE = "ViewOtherTree"
PERM_EDIT_OTHER_TREE = "EditOtherTree"
PERM_ADD_TREE = "AddTree"
PERM_EDIT_TREE_QUOTA = "EditTreeQuota"
PERM_DISABLE_TREE = "DisableTree"
PERM_ADD_OTHER_TREE_USER = "AddOtherTreeUser"
PERM_EDIT_OTHER_TREE_USER = "EditOtherTreeUser"
PERM_EDIT_OTHER_TREE_USER_ROLE = "EditOtherTreeUserRole"
PERM_VIEW_OTHER_TREE_USER = "ViewOtherTreeUser"
PERM_DEL_OTHER_TREE_USER = "DeleteOtherTreeUser"
PERM_EDIT_USER_TREE = "EditUserTree"

# Role → permissions mapping (hierarchical)
PERMISSIONS = {}

PERMISSIONS[ROLE_GUEST] = {
    PERM_EDIT_OWN_USER,
}

PERMISSIONS[ROLE_MEMBER] = PERMISSIONS[ROLE_GUEST] | {
    PERM_VIEW_PRIVATE,
}

PERMISSIONS[ROLE_CONTRIBUTOR] = PERMISSIONS[ROLE_MEMBER] | {
    PERM_ADD_OBJ,
}

PERMISSIONS[ROLE_EDITOR] = PERMISSIONS[ROLE_CONTRIBUTOR] | {
    PERM_EDIT_OBJ,
    PERM_DEL_OBJ,
    PERM_EDIT_NAME_GROUP,
}

PERMISSIONS[ROLE_OWNER] = PERMISSIONS[ROLE_EDITOR] | {
    PERM_ADD_USER,
    PERM_DEL_USER,
    PERM_EDIT_OTHER_USER,
    PERM_EDIT_USER_ROLE,
    PERM_VIEW_OTHER_USER,
    PERM_IMPORT_FILE,
    PERM_TRIGGER_REINDEX,
    PERM_EDIT_TREE,
    PERM_REPAIR_TREE,
    PERM_UPGRADE_TREE_SCHEMA,
    PERM_EDIT_TREE_MIN_ROLE_AI,
    PERM_DEL_OBJ_BATCH,
}

PERMISSIONS[ROLE_ADMIN] = PERMISSIONS[ROLE_OWNER] | {
    PERM_ADD_OTHER_TREE_USER,
    PERM_VIEW_OTHER_TREE_USER,
    PERM_EDIT_OTHER_TREE_USER,
    PERM_EDIT_OTHER_TREE_USER_ROLE,
    PERM_EDIT_USER_TREE,
    PERM_MAKE_ADMIN,
    PERM_DEL_OTHER_TREE_USER,
    PERM_VIEW_SETTINGS,
    PERM_EDIT_SETTINGS,
    PERM_VIEW_OTHER_TREE,
    PERM_EDIT_OTHER_TREE,
    PERM_EDIT_TREE_QUOTA,
    PERM_ADD_TREE,
    PERM_DISABLE_TREE,
}


def get_permissions_for_role(role):
    """Return the set of permission strings for a given role level."""
    return PERMISSIONS.get(role, set())


class HasGrampsPermission(BasePermission):
    """
    DRF permission class that checks Gramps permissions from JWT claims.

    Usage in views:
        permission_classes = [IsAuthenticated, HasGrampsPermission]
        required_permissions = [PERM_EDIT_OBJ]
    """

    def has_permission(self, request, view):
        required = getattr(view, "required_permissions", None)
        if not required:
            return True

        user = request.user
        if not user or not user.is_authenticated:
            return False

        user_permissions = get_permissions_for_role(user.role)
        return all(perm in user_permissions for perm in required)
