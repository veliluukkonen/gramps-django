from django.urls import path

from . import views

urlpatterns = [
    path("token/", views.TokenObtainView.as_view(), name="token_obtain"),
    path("token/refresh/", views.TokenRefreshView.as_view(), name="token_refresh"),
    path(
        "token/create_owner/",
        views.TokenCreateOwnerView.as_view(),
        name="token_create_owner",
    ),
    path("users/", views.UserListCreateView.as_view(), name="user_list_create"),
    path(
        "users/<str:username>/",
        views.UserDetailView.as_view(),
        name="user_detail",
    ),
    path(
        "users/<str:username>/password/change",
        views.PasswordChangeView.as_view(),
        name="password_change",
    ),
]
