from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login", views.login, name="login"),
    path("entra", views.entra_start, name="entra"),
    path("callback", views.entra_callback, name="callback"),
    path("logout", views.logout, name="logout"),
]
