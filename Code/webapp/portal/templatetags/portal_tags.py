import re
from datetime import datetime

from django import template
from django.utils import timezone
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

PII_TOKEN = re.compile(r"\[([A-Z]{2,20})\]")


@register.filter
def duration(seconds):
    if seconds in (None, ""):
        return "–"
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


@register.filter
def clock(ms):
    secs = int(ms or 0) // 1000
    return f"{secs // 60:02d}:{secs % 60:02d}"


@register.filter
def seconds(ms):
    return round((ms or 0) / 1000, 2)


@register.filter
def pii(text):
    """Escape, then render redaction placeholders like [PHONE] as chips."""
    return mark_safe(PII_TOKEN.sub(r'<span class="pii" title="Redacted personal data">\1</span>', escape(text or "")))


@register.filter
def dt(value, fmt="d M Y, H:i"):
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.utc)
    from django.utils.dateformat import format as dformat

    return dformat(timezone.localtime(parsed), fmt)


@register.filter
def score_class(percent):
    if percent is None or percent == "":
        return "muted"
    percent = float(percent)
    return "good" if percent >= 85 else "ok" if percent >= 70 else "bad"


@register.filter
def pct(value, digits=0):
    if value is None or value == "":
        return "–"
    return f"{float(value):.{int(digits)}f}%"


@register.filter
def ratio_pct(score, max_points):
    return round(100 * float(score) / float(max_points)) if max_points else 0


@register.filter
def humanize(value):
    return str(value or "").replace("_", " ").capitalize()


@register.filter
def get(mapping, key):
    return (mapping or {}).get(key)


@register.filter
def signed(value, digits=2):
    if value is None:
        return "–"
    return f"{float(value):+.{int(digits)}f}"


@register.simple_tag(takes_context=True)
def qs(context, **updates):
    """Current query string with some parameters replaced (None/'' removes them)."""
    params = context["request"].GET.copy()
    for key, value in updates.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    if "page" not in updates:
        params.pop("page", None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"


@register.simple_tag
def sort_toggle(current, field):
    """Next sort value for a column header: desc first, then asc."""
    return field if current == f"-{field}" else f"-{field}"
