from django.urls import include, path

urlpatterns = [
    path("auth/", include("accounts.urls")),
    path("", include("portal.urls")),
]

handler403 = "portal.views.forbidden"
handler404 = "portal.views.not_found"
