import logging
import mimetypes
import re
import uuid
from pathlib import PurePosixPath

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts.decorators import permission_required
from callcenter import analytics, pipeline, rules, speech
from callcenter import audio as audio_format
from callcenter.config import get_settings
from callcenter.factory import get_blob_store, get_call_store, get_dispatcher

from . import powerbi
from .data import RANGES, can_view_call, date_range, filter_options, visible_calls

log = logging.getLogger(__name__)

PAGE_SIZE = 25
FILTER_KEYS = ("q", "agent", "team", "reason", "sentiment", "resolution", "status", "flag", "qa_min", "qa_max")


def _range_context(request, rng):
    params = request.GET.copy()
    for k in ("range", "from", "to", "page"):
        params.pop(k, None)
    return {"range": rng, "ranges": RANGES, "keep_params": params.urlencode()}


def healthz(request):
    return HttpResponse("ok", content_type="text/plain")


# --------------------------------------------------------------------------- overview

def overview(request):
    rng = date_range(request)
    items = visible_calls(request, rng)
    team = request.GET.get("team", "")
    if team:
        items = [c for c in items if c.get("team") == team]
    data = analytics.overview(items)
    return render(request, "portal/overview.html", {
        **_range_context(request, rng),
        "data": data,
        "team": team,
        "teams": filter_options(items)["teams"] if not team else [team],
        "chart_data": {
            "trend": data["trend"],
            "reasons": data["reasons"][:8],
            "sentiment": data["sentiment"],
            "resolution": data["resolution"],
        },
    })


# --------------------------------------------------------------------------- calls

def calls(request):
    rng = date_range(request)
    items = visible_calls(request, rng)
    options = filter_options(items)
    filters = {k: request.GET.get(k, "") for k in FILTER_KEYS}
    results = analytics.sort_calls(analytics.filter_calls(items, filters), request.GET.get("sort", "-started_at"))
    page = Paginator(results, PAGE_SIZE).get_page(request.GET.get("page"))

    params = request.GET.copy()
    params.pop("page", None)
    context = {
        **_range_context(request, rng),
        "page": page,
        "total": len(results),
        "filters": filters,
        "options": options,
        "sort": request.GET.get("sort", "-started_at"),
        "page_params": params.urlencode(),
        "active_filters": sum(1 for v in filters.values() if v),
    }
    template = "portal/partials/calls_table.html" if request.headers.get("HX-Request") else "portal/calls.html"
    return render(request, template, context)


def _get_call(request, call_id):
    call = get_call_store().get(call_id)
    if not call:
        raise Http404("Call not found")
    if not can_view_call(request.user, call):
        raise PermissionDenied
    return call


def call_detail(request, call_id):
    call = _get_call(request, call_id)
    timeline = [{"t": u["start_ms"] / 1000, "v": u["score"], "s": u["speaker"]} for u in call.get("transcript", [])]
    return render(request, "portal/call_detail.html", {
        "call": call,
        "timeline": timeline,
        "call_language": speech.language_name(call.get("locale") or "en-US"),
        "can_retry": request.user.can("manage_pipeline"),
    })


RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
CHUNK = 1024 * 1024


def call_audio(request, call_id):
    """Stream the recording with HTTP Range support so the browser can seek."""
    call = _get_call(request, call_id)
    blob = call.get("audio_blob")
    container = get_settings().container_recordings
    store = get_blob_store()
    if not blob or not store.exists(container, blob):
        raise Http404("Recording not found")
    size = store.size(container, blob)
    content_type = mimetypes.guess_type(blob)[0] or "application/octet-stream"

    start, end, status = 0, size - 1, 200
    match = RANGE_RE.match(request.headers.get("Range", ""))
    if match and (match.group(1) or match.group(2)):
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), size - 1) if match.group(2) else size - 1
        else:  # suffix range: last N bytes
            start = max(0, size - int(match.group(2)))
        if start > end or start >= size:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        status = 206

    def chunks():
        pos = start
        while pos <= end:
            length = min(CHUNK, end - pos + 1)
            yield store.get_range(container, blob, pos, length)
            pos += length

    response = StreamingHttpResponse(chunks(), status=status, content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(end - start + 1)
    response["Cache-Control"] = "private, max-age=600"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


@require_POST
@permission_required("manage_pipeline")
def call_retry(request, call_id):
    call = get_call_store().get(call_id)
    if not call:
        raise Http404
    get_dispatcher().retry(call_id)
    messages.success(request, f"Retry queued for {call_id}.")
    next_url = request.POST.get("next")
    if not (next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()},
                                                         require_https=request.is_secure())):
        next_url = reverse("portal:call_detail", args=[call_id])
    return redirect(next_url)


# --------------------------------------------------------------------------- agents

@permission_required("view_agents")
def agents(request):
    rng = date_range(request)
    items = visible_calls(request, rng)
    return render(request, "portal/agents.html", {
        **_range_context(request, rng),
        "agents": analytics.agents(items),
        "criteria": analytics.criteria_averages(items),
    })


def agent_detail(request, agent_id):
    rng = date_range(request)
    items = visible_calls(request, rng)
    if agent_id == "me":
        if request.user.can("view_all_calls"):
            return redirect("portal:agents")
        agent_calls = items  # already limited to the agent's own calls
    else:
        if not request.user.can("view_agents") and agent_id != request.user.agent_id:
            raise PermissionDenied
        agent_calls = [c for c in items if c.get("agent_id") == agent_id]
    stats = analytics.agents(agent_calls)
    data = analytics.overview(agent_calls)
    team_avg = analytics.criteria_averages(items) if request.user.can("view_agents") else []
    return render(request, "portal/agent_detail.html", {
        **_range_context(request, rng),
        "agent": stats[0] if stats else {"agent_name": request.user.name if agent_id == "me" else agent_id, "calls": 0},
        "agent_id": agent_id,
        "data": data,
        "team_avg": {c["id"]: c["avg"] for c in team_avg},
        "recent": analytics.sort_calls(agent_calls, "-started_at")[:15],
        "chart_data": {"trend": data["trend"]},
    })


# --------------------------------------------------------------------------- insights

@permission_required("view_insights")
def insights(request):
    if powerbi.is_configured() and request.GET.get("view") != "builtin":
        return render(request, "portal/insights_powerbi.html", {"powerbi": True})
    rng = date_range(request)
    items = visible_calls(request, rng)
    data = analytics.overview(items)
    return render(request, "portal/insights.html", {
        **_range_context(request, rng),
        "data": data,
        "powerbi_configured": powerbi.is_configured(),
        "chart_data": {
            "criteria": data["criteria"],
            "sentiment": data["sentiment"],
            "resolution": data["resolution"],
            "emotion": data["emotion_end"],
            "reasons": data["reasons"],
            "trend": data["trend"],
        },
    })


@permission_required("view_insights")
def powerbi_token(request):
    try:
        config = powerbi.embed_config()
    except Exception:  # noqa: BLE001
        log.exception("Power BI embed token failed")
        return JsonResponse({"error": "Could not get a Power BI embed token. Check the configuration."}, status=502)
    return JsonResponse({k: config[k] for k in ("embedUrl", "reportId", "token", "expiry")})


# --------------------------------------------------------------------------- pipeline

@permission_required("manage_pipeline")
def pipeline_view(request):
    items = get_call_store().list_summaries()
    status_counts = {s: 0 for s in ("uploaded", "transcribing", "transcribed", "enriching", "completed", "failed")}
    for c in items:
        status_counts[c.get("status", "uploaded")] = status_counts.get(c.get("status", "uploaded"), 0) + 1
    active = [c for c in items if c.get("status") != "completed"]
    recent = sorted((c for c in items if c.get("status") == "completed"),
                    key=lambda c: c.get("processed_at") or "", reverse=True)[:10]
    context = {
        "status_counts": status_counts,
        "active": sorted(active, key=lambda c: c.get("updated_at") or "", reverse=True),
        "recent": recent,
        "success_rate": analytics.kpis(items)["success_rate"],
        "max_upload_mb": settings.MAX_UPLOAD_MB,
        "audio_ext": ", ".join(sorted(pipeline.AUDIO_EXTENSIONS)),
        "languages": speech.LANGUAGES,
        "default_locale": get_settings().speech_locale,
        "local": settings.CC_MODE == "local",
    }
    template = "portal/partials/pipeline_tables.html" if request.headers.get("HX-Request") else "portal/pipeline.html"
    return render(request, template, context)


SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@require_POST
@permission_required("manage_pipeline")
def upload(request):
    """Simulates the telephony server: stores the recording (+ metadata) in the recordings container."""
    audio = request.FILES.get("audio")
    if not audio:
        messages.error(request, "Choose an audio file to upload.")
        return redirect("portal:pipeline")
    suffix = PurePosixPath(audio.name).suffix.lower()
    if suffix not in pipeline.AUDIO_EXTENSIONS:
        messages.error(request, f"Unsupported file type {suffix}.")
        return redirect("portal:pipeline")
    if audio.size > settings.MAX_UPLOAD_MB * 1024 * 1024:
        messages.error(request, f"File is larger than {settings.MAX_UPLOAD_MB} MB.")
        return redirect("portal:pipeline")
    data = audio.read()
    detected = audio_format.detect_format(data)
    if detected not in pipeline.AUDIO_EXTENSIONS:
        messages.error(request, f"{audio.name} is not an audio file the speech service can read "
                                f"(its content is {audio_format.describe(detected)}). Check that it plays, "
                                f"or convert it to MP3 or WAV.")
        return redirect("portal:pipeline")
    renamed = not audio_format.matches(suffix, detected)
    if renamed:
        suffix = detected  # e.g. an M4A recording named .mp3: store it under its real extension
    locale = request.POST.get("locale", "")
    if locale not in speech.LANGUAGES and locale != speech.AUTO:
        locale = ""

    call_id = SAFE_NAME.sub("-", request.POST.get("call_id", "").strip()) or f"CALL-{uuid.uuid4().hex[:12].upper()}"
    blob_name = f"{call_id}{suffix}"
    s = get_settings()
    store = get_blob_store()
    if get_call_store().get(call_id) or store.exists(s.container_recordings, blob_name):
        messages.error(request, f"A call with ID {call_id} already exists.")
        return redirect("portal:pipeline")

    metadata = {k: request.POST.get(k, "").strip() for k in
                ("agent_id", "agent_name", "agent_email", "team", "customer_id", "direction")}
    metadata = {k: v for k, v in metadata.items() if v}
    metadata["channels"] = 2 if request.POST.get("stereo") else 1
    if locale:
        metadata["locale"] = locale
    if request.POST.get("started_at"):
        metadata["started_at"] = pipeline.normalize_timestamp(request.POST["started_at"])
    # Sidecar first, so the pipeline sees metadata when the audio's BlobCreated event fires.
    store.put_json(s.container_recordings, f"{call_id}.json", metadata)
    pipeline.register_upload(blob_name, metadata)
    store.put_bytes(s.container_recordings, blob_name, data, audio.content_type)
    get_dispatcher().recording_uploaded(blob_name)
    note = f" The file is actually {audio_format.describe(detected)}, so it was stored as {blob_name}." if renamed else ""
    messages.success(request, f"Uploaded {audio.name} as {call_id}. Processing has started.{note}")
    return redirect("portal:pipeline")


# --------------------------------------------------------------------------- settings

CONFIG_TABS = [
    (rules.RUBRIC_FILE, "QA rubric", "Scorecard criteria, weights and the pass threshold."),
    (rules.COMPLIANCE_FILE, "Compliance checks", "Mandatory disclosures and prohibited behaviours."),
    (rules.REASONS_FILE, "Call reasons", "The taxonomy every call is classified into."),
]


@permission_required("edit_settings")
def settings_view(request):
    names = [t[0] for t in CONFIG_TABS]
    current = request.GET.get("file") or request.POST.get("file") or names[0]
    if current not in names:
        raise Http404
    text, source = rules.read_config_text(current)
    error = None

    if request.method == "POST":
        if request.POST.get("action") == "reset":
            text = rules.default_config_text(current)
        else:
            text = request.POST.get("content", "")
        try:
            rules.save_config_text(current, text)
            messages.success(request, "Saved. New calls will be scored with this configuration.")
            return redirect(f"{reverse('portal:settings')}?file={current}")
        except rules.ConfigError as exc:
            error = str(exc)
            source = "unsaved"

    s = get_settings()
    return render(request, "portal/settings.html", {
        "tabs": CONFIG_TABS, "current": current, "content": text, "source": source, "error": error,
        "backends": {
            "Mode": s.mode, "Storage": s.storage_backend, "Call store": s.store_backend,
            "Speech": s.speech_backend, "Language": s.language_backend, "LLM": s.llm_backend,
            "LLM deployment": s.openai_deployment if s.llm_backend == "azure" else "-",
            "Events": s.events_backend, "Sign-in": settings.AUTH_MODE,
            "Power BI": "configured" if powerbi.is_configured() else "not configured",
        },
    })


# --------------------------------------------------------------------------- errors

def forbidden(request, exception=None):
    return render(request, "portal/error.html", {"code": 403, "title": "Access denied",
                                                 "message": "You do not have permission to view this page."}, status=403)


def not_found(request, exception=None):
    return render(request, "portal/error.html", {"code": 404, "title": "Not found",
                                                 "message": "The page or call you are looking for does not exist."}, status=404)
