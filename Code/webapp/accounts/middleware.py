from django.shortcuts import redirect
from django.urls import reverse

from .roles import AnonymousUser, User

PUBLIC_PREFIXES = ("/auth/", "/static/", "/healthz", "/api/")


class AuthenticationMiddleware:
    """Puts ``request.user`` on every request and sends anonymous users to sign in.

    The API (``/api/``) does its own auth (session or CRM API key).
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        data = request.session.get("user")
        request.user = User.from_session(data) if data else AnonymousUser()
        if not request.user.is_authenticated and not request.path.startswith(PUBLIC_PREFIXES):
            return redirect(f"{reverse('accounts:login')}?next={request.get_full_path()}")
        return self.get_response(request)
