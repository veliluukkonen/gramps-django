from django.urls import path

from . import views

# Archive routes come first so "archive" is never taken for a media handle.
urlpatterns = [
    path(
        "media/archive/",
        views.MediaArchiveView.as_view(),
        name="media_archive",
    ),
    path(
        "media/archive/upload/zip",
        views.MediaArchiveUploadView.as_view(),
        name="media_archive_upload_zip",
    ),
    path(
        "media/archive/<str:filename>",
        views.MediaArchiveFileView.as_view(),
        name="media_archive_file",
    ),
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
