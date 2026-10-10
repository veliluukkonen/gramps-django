from django.db.models import QuerySet
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class GrampsPagination(PageNumberPagination):
    """
    Pagination compatible with Gramps Web API.

    - page=0 or no page param → return all results (no pagination)
    - page>=1 → paginate with X-Total-Count header
    - pagesize param controls page size (default 20)
    Works with both querysets and plain lists.
    """

    page_query_param = "page"
    page_size_query_param = "pagesize"
    page_size = 20
    max_page_size = 100000

    def paginate_queryset(self, queryset, request, view=None):
        page = request.query_params.get(self.page_query_param, None)
        if page is None or page == "0":
            # No pagination — return all results
            self._no_pagination = True
            if isinstance(queryset, QuerySet):
                items = list(queryset)
            else:
                items = list(queryset)
            self._total_count = len(items)
            return items
        self._no_pagination = False
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        if getattr(self, "_no_pagination", False):
            response = Response(data)
            response["X-Total-Count"] = self._total_count
            return response
        response = Response(data)
        response["X-Total-Count"] = self.page.paginator.count
        return response
