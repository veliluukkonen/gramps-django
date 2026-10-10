from django.contrib import admin
from django.urls import include, path

# Order matters: specific paths (media files, timeline, tree admin) must come
# before the generic object router in apps.core.urls.
urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.auth.urls")),
    path("api/", include("apps.media.urls")),
    path("api/", include("apps.tree.urls")),
    path("api/", include("apps.analysis.urls")),
    path("api/", include("apps.special.urls")),
    path("api/", include("apps.core.urls")),
]
