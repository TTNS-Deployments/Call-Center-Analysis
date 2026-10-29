import hmac
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from .roles import ADMIN, AGENT, ALL_ROLES, QA, SUPERVISOR, User

log = logging.getLogger(__name__)


def _safe_next(request, url: str | None) -> str:
    if url and url_has_allowed_host_and_scheme(url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return url
    return reverse("portal:overview")


def _sign_in(request, user: User, next_url: str):
    request.session.cycle_key()
    request.session["user"] = user.to_session()
    return redirect(_safe_next(request, next_url))


# ---------------------------------------------------------------------------- dev mode

def dev_users() -> list[User]:
    """Demo personas for local mode: one per role plus every sample agent."""
    users = [
        User(id="dev-admin", name="Asha Rao", email="asha.rao@contoso.com", roles=[ADMIN]),
        User(id="dev-supervisor", name="Kabir Khan", email="kabir.khan@contoso.com", roles=[SUPERVISOR]),
        User(id="dev-qa", name="Leela Thomas", email="leela.thomas@contoso.com", roles=[QA]),
    ]
    try:
        from callcenter.samples import load_scenarios

        for a in load_scenarios().get("agents", []):
            users.append(User(id=f"dev-{a['id']}", name=a["name"], email=a["email"], roles=[AGENT], agent_id=a["id"]))
    except Exception:  # noqa: BLE001 - sample data is optional
        pass
    return users


def _client_ip(request) -> str:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")  # App Service front end
    return (forwarded.split(",")[0].split(":")[0].strip() if forwarded else "") or request.META.get("REMOTE_ADDR", "")


@require_http_methods(["GET", "POST"])
def login(request):
    next_url = request.GET.get("next") or request.POST.get("next")
    if settings.AUTH_MODE == "entra":
        return render(request, "accounts/login.html", {"mode": "entra", "next": next_url,
                                                       "configured": bool(settings.ENTRA_CLIENT_ID)})

    mode = settings.AUTH_MODE  # "dev" or "password"
    context = {"mode": mode, "next": next_url, "configured": mode == "dev" or bool(settings.DEMO_PASSWORD)}
    if not context["configured"]:
        return render(request, "accounts/login.html", context)

    if mode == "password":  # one administrator account, protected by the demo password
        context["admin_name"] = settings.DEMO_ADMIN_NAME
        users = [User(id="demo-admin", name=settings.DEMO_ADMIN_NAME, email=settings.DEMO_ADMIN_EMAIL, roles=[ADMIN])]
    else:
        users = dev_users()
        context["users"] = users
    if request.method == "POST":
        chosen = users[0] if mode == "password" else next((u for u in users if u.id == request.POST.get("user")), None)
        if not chosen:
            return HttpResponseBadRequest("Unknown user")
        if mode == "password":
            key = f"demo-login-failures:{_client_ip(request)}"
            failures = cache.get(key, 0)
            if failures >= settings.DEMO_PASSWORD_MAX_FAILURES:
                messages.error(request, "Too many wrong passwords. Try again in 15 minutes.")
                return render(request, "accounts/login.html", context, status=429)
            given = request.POST.get("password", "")
            if not hmac.compare_digest(given.encode(), settings.DEMO_PASSWORD.encode()):
                cache.set(key, failures + 1, 15 * 60)
                log.warning("Wrong demo password from %s", _client_ip(request))
                messages.error(request, "Wrong password.")
                return render(request, "accounts/login.html", context, status=403)
            cache.delete(key)
        return _sign_in(request, chosen, next_url)
    return render(request, "accounts/login.html", context)


# ---------------------------------------------------------------------------- Entra ID

def _msal_app():
    import msal

    return msal.ConfidentialClientApplication(
        settings.ENTRA_CLIENT_ID, authority=settings.ENTRA_AUTHORITY, client_credential=settings.ENTRA_CLIENT_SECRET,
    )


def entra_start(request):
    if settings.AUTH_MODE != "entra" or not settings.ENTRA_CLIENT_ID:
        return redirect("accounts:login")
    flow = _msal_app().initiate_auth_code_flow(
        scopes=[], redirect_uri=request.build_absolute_uri(reverse("accounts:callback")),
    )
    request.session["auth_flow"] = flow
    request.session["auth_next"] = request.GET.get("next") or ""
    return redirect(flow["auth_uri"])


def entra_callback(request):
    flow = request.session.pop("auth_flow", None)
    next_url = request.session.pop("auth_next", "")
    if not flow:
        return redirect("accounts:login")
    try:
        result = _msal_app().acquire_token_by_auth_code_flow(flow, request.GET.dict())
    except ValueError:  # state mismatch / replay
        log.warning("Rejected auth response (state mismatch)")
        return redirect("accounts:login")
    if "error" in result:
        log.warning("Entra sign-in failed: %s %s", result.get("error"), result.get("error_description"))
        messages.error(request, "Sign-in failed. Please try again or contact your administrator.")
        return redirect("accounts:login")

    claims = result.get("id_token_claims", {})
    roles = [r for r in claims.get("roles", []) if r in ALL_ROLES]
    if not roles and settings.ENTRA_DEFAULT_ROLE in ALL_ROLES:
        roles = [settings.ENTRA_DEFAULT_ROLE]
    if not roles:
        messages.error(request, "Your account has no role in this application. Ask an administrator for access.")
        return redirect("accounts:login")
    if roles == [AGENT] and not settings.ALLOW_AGENT_SIGNIN:
        messages.error(request, "The portal is for administrators, supervisors and QA analysts. Agents do not sign in.")
        return redirect("accounts:login")

    user = User(
        id=claims.get("oid", claims.get("sub", "")),
        name=claims.get("name", "User"),
        email=(claims.get("preferred_username") or claims.get("email") or "").lower(),
        roles=roles,
    )
    return _sign_in(request, user, next_url)


@require_POST
def logout(request):
    request.session.flush()
    if settings.AUTH_MODE == "entra" and settings.ENTRA_CLIENT_ID:
        params = urlencode({"post_logout_redirect_uri": request.build_absolute_uri(reverse("accounts:login"))})
        return redirect(f"{settings.ENTRA_AUTHORITY}/oauth2/v2.0/logout?{params}")
    return redirect("accounts:login")
