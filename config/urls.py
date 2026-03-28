from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("apps.auth.urls")),
    path("api/", include("apps.media.urls")),
    path("api/", include("apps.special.urls")),
    path("api/", include("apps.core.urls")),
]
