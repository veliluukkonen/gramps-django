from django.urls import path

from . import views

urlpatterns = [
    path("metadata/", views.MetadataView.as_view(), name="metadata"),
    path("search/", views.SearchView.as_view(), name="search"),
    path("translations/", views.TranslationsListView.as_view(), name="translations_list"),
    path(
        "translations/<str:language>",
        views.TranslationsDetailView.as_view(),
        name="translations_detail",
    ),
]
