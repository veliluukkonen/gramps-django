from django.urls import include, path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter(trailing_slash=False)
router.register(r"people", views.PersonViewSet)
router.register(r"families", views.FamilyViewSet)
router.register(r"events", views.EventViewSet)
router.register(r"places", views.PlaceViewSet)
router.register(r"sources", views.SourceViewSet)
router.register(r"citations", views.CitationViewSet)
router.register(r"repositories", views.RepositoryViewSet)
router.register(r"media", views.MediaObjectViewSet)
router.register(r"notes", views.NoteViewSet)
router.register(r"tags", views.TagViewSet)

urlpatterns = [
    path("", include(router.urls)),
]
