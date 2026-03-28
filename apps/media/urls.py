from django.urls import path

from . import views

urlpatterns = [
    path(
        "media/<str:handle>/file",
        views.MediaFileView.as_view(),
        name="media_file",
    ),
    path(
        "media/<str:handle>/thumbnail/<int:size>",
        views.MediaThumbnailView.as_view(),
        name="media_thumbnail",
    ),
    path(
        "media/<str:handle>/cropped/<str:x1>/<str:y1>/<str:x2>/<str:y2>",
        views.MediaCroppedView.as_view(),
        name="media_cropped",
    ),
    path(
        "media/<str:handle>/cropped/<str:x1>/<str:y1>/<str:x2>/<str:y2>/thumbnail/<int:size>",
        views.MediaCroppedThumbnailView.as_view(),
        name="media_cropped_thumbnail",
    ),
]
