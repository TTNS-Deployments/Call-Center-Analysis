from django.conf import settings
from django.urls import reverse

from accounts.roles import PERMISSIONS
from callcenter.analytics import FLAG_LABELS

NAV = [
    ("overview", "Overview", "portal:overview", None, None),
    ("calls", "Calls", "portal:calls", None, None),
    ("agents", "Agents", "portal:agents", "view_agents", None),
    ("me", "My scorecard", "portal:agent_detail", "-view_agents", ["me"]),
    ("insights", "Insights", "portal:insights", "view_insights", None),
    ("pipeline", "Pipeline", "portal:pipeline", "manage_pipeline", None),
    ("settings", "Settings", "portal:settings", "edit_settings", None),
]


def _allowed(user, perm):
    if not perm:
        return True
    if perm.startswith("-"):  # shown only to users WITHOUT the permission
        return not user.can(perm[1:])
    return user.can(perm)


def portal(request):
    user = getattr(request, "user", None)
    items = []
    if user is not None and user.is_authenticated:
        items = [{"key": key, "label": label, "href": reverse(name, args=args)}
                 for key, label, name, perm, args in NAV if _allowed(user, perm)]
    perms = {p: user.can(p) for p in PERMISSIONS} if user is not None else {}
    return {"nav_items": items, "active_nav": _active(request), "perms": perms, "flag_labels": FLAG_LABELS, "cc_mode": settings.CC_MODE,
            "auth_mode": settings.AUTH_MODE}


def _active(request):
    match = getattr(request, "resolver_match", None)
    if not match:
        return ""
    name = match.url_name or ""
    if name.startswith("agent"):
        return "me" if match.kwargs.get("agent_id") == "me" else "agents"
    return {"call_detail": "calls", "upload": "pipeline", "settings": "settings"}.get(name, name.replace("_view", ""))
