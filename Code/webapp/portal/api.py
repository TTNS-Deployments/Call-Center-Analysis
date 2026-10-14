"""Read-only JSON API for the CRM and other downstream systems.

Auth: a signed-in session, or header ``X-API-Key`` matching one of ``CRM_API_KEYS``.

    GET /api/calls?from=2026-10-01&to=2026-10-31&agent=A1001&customer=CUST-00042&status=completed
    GET /api/calls/<call_id>               full document incl. redacted transcript
"""

import hmac

from django.conf import settings
from django.http import JsonResponse

from callcenter import analytics
from callcenter.factory import get_call_store

from .data import can_view_call, date_range


def _authorize(request):
    """Return (ok, user_or_None). API-key callers see all calls."""
    if getattr(request, "user", None) is not None and request.user.is_authenticated:
        return True, request.user
    key = request.headers.get("X-API-Key", "")
    if key and any(hmac.compare_digest(key, k) for k in settings.CRM_API_KEYS):
        return True, None
    return False, None


def _unauthorized():
    return JsonResponse({"error": "unauthorized"}, status=401)


def calls(request):
    ok, user = _authorize(request)
    if not ok:
        return _unauthorized()
    rng = date_range(request) if (request.GET.get("from") or request.GET.get("to") or request.GET.get("range")) else None
    items = get_call_store().list_summaries(rng.date_from if rng else None, rng.date_to if rng else None)
    if user is not None:
        items = [c for c in items if can_view_call(user, c)]
    customer = request.GET.get("customer")
    if customer:
        items = [c for c in items if c.get("customer_id") == customer]
    items = analytics.filter_calls(items, {k: request.GET.get(k, "") for k in ("agent", "team", "reason", "status", "flag")})
    limit = min(int(request.GET.get("limit", 100) or 100), 1000)
    return JsonResponse({"count": len(items), "items": items[:limit]})


def call_detail(request, call_id):
    ok, user = _authorize(request)
    if not ok:
        return _unauthorized()
    doc = get_call_store().get(call_id)
    if not doc or (user is not None and not can_view_call(user, doc)):
        return JsonResponse({"error": "not found"}, status=404)
    doc.pop("history", None)
    return JsonResponse(doc)
