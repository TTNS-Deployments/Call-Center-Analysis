"""Data access for views: date ranges and per-user visibility."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from callcenter.factory import get_call_store

RANGES = {"7": "Last 7 days", "30": "Last 30 days", "90": "Last 90 days", "all": "All time"}
DEFAULT_RANGE = "30"


@dataclass
class DateRange:
    key: str
    label: str
    date_from: str | None
    date_to: str | None
    custom_from: str = ""
    custom_to: str = ""


def date_range(request) -> DateRange:
    """Preset (?range=7|30|90|all) or custom (?from=YYYY-MM-DD&to=YYYY-MM-DD)."""
    start, end = request.GET.get("from", ""), request.GET.get("to", "")
    if start or end:
        try:
            df = datetime.combine(date.fromisoformat(start), time.min, timezone.utc).isoformat() if start else None
            dt = datetime.combine(date.fromisoformat(end), time.max, timezone.utc).isoformat() if end else None
            return DateRange("custom", f"{start or '…'} to {end or '…'}", df, dt, start, end)
        except ValueError:
            pass
    key = request.GET.get("range", DEFAULT_RANGE)
    if key not in RANGES:
        key = DEFAULT_RANGE
    if key == "all":
        return DateRange(key, RANGES[key], None, None)
    since = datetime.now(timezone.utc) - timedelta(days=int(key))
    return DateRange(key, RANGES[key], since.isoformat(timespec="seconds"), None)


def can_view_call(user, call: dict) -> bool:
    if user.can("view_all_calls"):
        return True
    if user.agent_id and call.get("agent_id") == user.agent_id:
        return True
    return bool(user.email) and (call.get("agent_email") or "").lower() == user.email.lower()


def visible_calls(request, rng: DateRange | None = None) -> list[dict]:
    rng = rng or date_range(request)
    items = get_call_store().list_summaries(rng.date_from, rng.date_to)
    if request.user.can("view_all_calls"):
        return items
    return [c for c in items if can_view_call(request.user, c)]


def filter_options(items: list[dict]) -> dict:
    agents = sorted({(c.get("agent_id"), c.get("agent_name") or c.get("agent_id")) for c in items if c.get("agent_id")},
                    key=lambda a: a[1])
    reasons = sorted({(c.get("call_reason"), c.get("call_reason_name") or c.get("call_reason"))
                      for c in items if c.get("call_reason")}, key=lambda r: r[1])
    teams = sorted({c.get("team") for c in items if c.get("team")})
    return {"agents": agents, "reasons": reasons, "teams": teams}
