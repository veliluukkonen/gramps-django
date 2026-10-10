from django.urls import path

from . import types, views
from .translations import TranslationsDetailView, TranslationsListView

urlpatterns = [
    path("metadata/", views.MetadataView.as_view(), name="metadata"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("translations/", TranslationsListView.as_view(), name="translations_list"),
    path(
        "translations/<str:language>",
        TranslationsDetailView.as_view(),
        name="translations_detail",
    ),
    path("types/", types.TypesView.as_view(), name="types"),
    path("types/default/", types.DefaultTypesView.as_view(), name="types_default"),
    path(
        "types/default/<str:datatype>/map",
        types.DefaultTypeMapView.as_view(),
        name="types_default_map",
    ),
    path(
        "types/default/<str:datatype>",
        types.DefaultTypeView.as_view(),
        name="types_default_detail",
    ),
    path("types/custom/", types.CustomTypesView.as_view(), name="types_custom"),
    path(
        "types/custom/<str:datatype>",
        types.CustomTypeView.as_view(),
        name="types_custom_detail",
    ),
]
