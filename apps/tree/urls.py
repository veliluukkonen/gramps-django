from django.urls import re_path

from . import views

# The frontend mixes trailing and non-trailing slashes, so most routes
# accept both.
urlpatterns = [
    # transaction history / raw transactions
    re_path(
        r"^transactions/history/?$",
        views.TransactionsHistoryView.as_view(),
        name="transactions_history",
    ),
    re_path(
        r"^transactions/history/(?P<transaction_id>\d+)/?$",
        views.TransactionHistoryView.as_view(),
        name="transaction_history",
    ),
    re_path(
        r"^transactions/history/(?P<transaction_id>\d+)/undo/?$",
        views.TransactionUndoView.as_view(),
        name="transaction_undo",
    ),
    re_path(r"^transactions/?$", views.TransactionsView.as_view(), name="transactions"),
    # trees
    re_path(r"^trees/?$", views.TreesView.as_view(), name="trees"),
    re_path(r"^trees/(?P<tree_id>[^/]+)/repair/?$", views.TreeRepairView.as_view(), name="tree_repair"),
    re_path(r"^trees/(?P<tree_id>[^/]+)/migrate/?$", views.TreeMigrateView.as_view(), name="tree_migrate"),
    re_path(r"^trees/(?P<tree_id>[^/]+)/?$", views.TreeView.as_view(), name="tree"),
    # config
    re_path(r"^config/?$", views.ConfigsView.as_view(), name="configs"),
    re_path(r"^config/(?P<key>[^/]+)/?$", views.ConfigView.as_view(), name="config"),
    # tasks
    re_path(r"^tasks/(?P<task_id>[^/]+)/?$", views.TaskView.as_view(), name="task"),
    # batch delete, search index
    re_path(r"^objects/delete/?$", views.DeleteObjectsView.as_view(), name="objects_delete"),
    re_path(r"^search/index/?$", views.SearchIndexView.as_view(), name="search_index"),
    # importers
    re_path(r"^importers/?$", views.ImportersView.as_view(), name="importers"),
    re_path(r"^importers/(?P<extension>[^/]+)/file/?$", views.ImporterFileView.as_view(), name="importer_file"),
    re_path(r"^importers/(?P<extension>[^/]+)/?$", views.ImporterView.as_view(), name="importer"),
    # exporters
    re_path(r"^exporters/?$", views.ExportersView.as_view(), name="exporters"),
    re_path(
        r"^exporters/(?P<extension>[^/]+)/file/processed/(?P<filename>[^/]+)/?$",
        views.ExporterFileResultView.as_view(),
        name="exporter_file_result",
    ),
    re_path(r"^exporters/(?P<extension>[^/]+)/file/?$", views.ExporterFileView.as_view(), name="exporter_file"),
    re_path(r"^exporters/(?P<extension>[^/]+)/?$", views.ExporterView.as_view(), name="exporter"),
    # reports
    re_path(r"^reports/?$", views.ReportsView.as_view(), name="reports"),
    re_path(r"^reports/(?P<report_id>[^/]+)/file/?$", views.ReportView.as_view(), name="report_file"),
    re_path(r"^reports/(?P<report_id>[^/]+)/?$", views.ReportView.as_view(), name="report"),
]
