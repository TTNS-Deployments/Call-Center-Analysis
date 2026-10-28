"""The call processing pipeline.

Stage 1 ``transcribe_recording``: recordings/<file>  -> Speech -> transcripts/<call_id>.json
Stage 2 ``enrich_transcript``:    transcripts/<call_id>.json -> Language (PII redaction, sentiment)
                                  -> OpenAI (summary, reason, QA, compliance)
                                  -> insights/json + Parquet + call store + event

In Azure each stage is a queue-triggered Function fed by Event Grid (BlobCreated), so
each stage is retried on its own. Locally the stages are chained in-process.

Telephony integration contract
------------------------------
Upload the audio to the ``recordings`` container. Optionally upload a sidecar JSON with the
same base name *before* the audio (``<call_id>.json``) carrying metadata::

    {"agent_id": "A1001", "agent_name": "...", "agent_email": "...", "team": "...",
     "customer_id": "...", "direction": "inbound", "started_at": "2026-10-01T10:15:00+00:00",
     "channels": 2, "agent_channel": 0, "locale": "hi-IN"}

``locale`` is the call language: "en-US", "hi-IN", or "auto" to let Speech identify it.

The file name (without extension) becomes the call ID.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import PurePosixPath

from . import parquet
from .config import get_settings
from .factory import (
    get_blob_store,
    get_call_store,
    get_event_publisher,
    get_insights_client,
    get_language_client,
    get_speech_client,
)
from .metrics import speech_metrics
from .rules import Rules, load_rules
from .store import utcnow

log = logging.getLogger(__name__)

AUDIO_EXTENSIONS = {".wav", ".mp3", ".ogg", ".opus", ".flac", ".m4a", ".webm", ".wma", ".aac", ".amr"}
METADATA_FIELDS = ("agent_id", "agent_name", "agent_email", "team", "customer_id", "direction", "started_at")

STATUS_UPLOADED = "uploaded"
STATUS_TRANSCRIBING = "transcribing"
STATUS_TRANSCRIBED = "transcribed"
STATUS_ENRICHING = "enriching"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
IN_PROGRESS = {STATUS_UPLOADED, STATUS_TRANSCRIBING, STATUS_TRANSCRIBED, STATUS_ENRICHING}
DONE_OR_PAST_STAGE_1 = {STATUS_TRANSCRIBED, STATUS_ENRICHING, STATUS_COMPLETED}

POSITIVE_THRESHOLD = 0.15


def is_audio(blob_name: str) -> bool:
    return PurePosixPath(blob_name).suffix.lower() in AUDIO_EXTENSIONS


def call_id_for(blob_name: str) -> str:
    return PurePosixPath(blob_name).stem


def sidecar_name(blob_name: str) -> str:
    path = PurePosixPath(blob_name)
    return str(path.with_suffix(".json"))


def read_metadata(blob_name: str) -> dict:
    s = get_settings()
    return get_blob_store().get_json_or_none(s.container_recordings, sidecar_name(blob_name)) or {}


def mark_failed(call_id: str, stage: str, exc: BaseException, final: bool = False) -> None:
    message = f"{type(exc).__name__}: {exc}"[:1000]
    note = " (retries exhausted)" if final else ""
    try:
        get_call_store().add_history(call_id, f"{stage} failed{note}: {message}",
                                     status=STATUS_FAILED, stage=stage, error=message)
    except Exception:  # noqa: BLE001 - never mask the original error
        log.exception("Could not record failure for %s", call_id)


def register_upload(blob_name: str, metadata: dict | None = None) -> dict:
    """Record a newly uploaded recording as queued (used by the web app upload)."""
    call_id = call_id_for(blob_name)
    meta = {k: v for k, v in (metadata or {}).items() if k in METADATA_FIELDS}
    meta.setdefault("started_at", utcnow())
    fields = {**_meta_fields(meta), "status": STATUS_UPLOADED, "stage": "transcribe", "error": None,
              "attempts": 0, "audio_blob": blob_name}
    return get_call_store().add_history(call_id, "Recording uploaded", **fields)


def normalize_timestamp(value: str | None) -> str:
    """ISO-8601 in UTC ("2026-10-01T04:45:00+00:00") so string comparison sorts and filters correctly."""
    if not value:
        return utcnow()
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        log.warning("Unparseable started_at %r, using current time", value)
        return utcnow()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _meta_fields(meta: dict) -> dict:
    out = {k: meta[k] for k in METADATA_FIELDS if meta.get(k)}
    if "started_at" in out:
        out["started_at"] = normalize_timestamp(out["started_at"])
        out["call_date"] = out["started_at"][:10]
    return out


# --------------------------------------------------------------------------- stage 1

def transcribe_recording(blob_name: str, chain: bool = False) -> dict | None:
    """Stage 1. Returns the transcript, or None when the blob is not an audio file."""
    if not is_audio(blob_name):
        return None
    s = get_settings()
    blobs, store = get_blob_store(), get_call_store()
    call_id = call_id_for(blob_name)
    meta = read_metadata(blob_name)

    existing = store.get(call_id) or {}
    if existing.get("audio_blob") == blob_name and existing.get("status") in DONE_OR_PAST_STAGE_1:
        # Duplicate BlobCreated delivery. Retries/re-analysis reset the status to "uploaded" first.
        log.info("Skipping %s: already %s", blob_name, existing["status"])
        return None
    started_at = meta.get("started_at") or existing.get("started_at") or utcnow()
    fields = {**_meta_fields({**meta, "started_at": started_at}), "status": STATUS_TRANSCRIBING,
              "stage": "transcribe", "error": None, "audio_blob": blob_name,
              "attempts": int(existing.get("attempts") or 0) + 1}
    store.add_history(call_id, "Transcription started", **fields)
    try:
        audio = blobs.get_bytes(s.container_recordings, blob_name)
        transcript = get_speech_client().transcribe(audio, PurePosixPath(blob_name).name, meta)
        transcript.update({
            "call_id": call_id,
            "audio_blob": blob_name,
            "metadata": {k: v for k, v in meta.items() if k != "mock_script"},
            "transcribed_at": utcnow(),
        })
        transcript_blob = f"{call_id}.json"
        blobs.put_json(s.container_transcripts, transcript_blob, transcript)
        store.add_history(
            call_id, f"Transcribed ({len(transcript['phrases'])} phrases)", status=STATUS_TRANSCRIBED,
            stage="enrich", transcript_blob=transcript_blob, duration_sec=round(transcript["duration_ms"] / 1000),
            attempts=0,
        )
    except Exception as exc:
        mark_failed(call_id, "transcribe", exc)
        raise

    if chain:
        enrich_transcript(transcript_blob)
    return transcript


# --------------------------------------------------------------------------- stage 2

def enrich_transcript(transcript_blob: str) -> dict:
    """Stage 2. Returns the final call document."""
    s = get_settings()
    blobs, store = get_blob_store(), get_call_store()
    call_id = call_id_for(transcript_blob)
    existing = store.get(call_id) or {}
    store.add_history(call_id, "Analysis started", status=STATUS_ENRICHING, stage="enrich", error=None,
                      attempts=int(existing.get("attempts") or 0) + 1)
    try:
        transcript = blobs.get_json(s.container_transcripts, transcript_blob)
        rules = load_rules()
        measured = speech_metrics(transcript["phrases"])
        if transcript["phrases"]:
            language = get_language_client().analyze(transcript["phrases"])
            metadata = {**transcript.get("metadata", {}), "call_id": call_id, "speech_metrics": measured,
                        "locale": transcript.get("locale")}
            insights = get_insights_client().generate(language["phrases"], metadata, rules)
        else:  # silent or empty recording: nothing to score, and no reason to call the AI services
            language = {"source": "none", "phrases": [], "pii_summary": {"total": 0, "categories": {}}}
            insights = no_speech_insights(rules)

        doc = build_call_document(store.get(call_id) or {}, transcript, language, insights, rules)
        doc["speech_metrics"] = measured
        blobs.put_json(s.container_insights, f"json/{call_id}.json", doc)
        parquet.write_call(blobs, s.container_insights, doc)
        doc = store.upsert(doc)
    except Exception as exc:
        mark_failed(call_id, "enrich", exc)
        raise

    get_event_publisher().publish(doc)
    return doc


NO_SPEECH_SUMMARY = "No speech was detected in the recording, so the call was not scored."


def no_speech_insights(rules: Rules) -> dict:
    """Insights for a recording without speech: every QA parameter and compliance check is N/A."""
    return {
        "summary": NO_SPEECH_SUMMARY,
        "call_reason": rules.categories[-1]["id"],
        "call_reason_detail": "",
        "root_cause": "No speech detected",
        "resolution": "unresolved",
        "customer_emotion": {"start": "neutral", "end": "neutral", "dominant": "neutral"},
        "repeat_contact": False,
        "qa": [{"id": c["id"], "rating": "not_applicable", "evidence": "", "rationale": "No speech in the recording.",
                "recommendation": ""} for c in rules.criteria],
        "compliance": [{"id": c["id"], "status": "not_applicable", "evidence": ""} for c in rules.checks],
        "topics": [],
        "follow_up_actions": ["Check the recording: no speech was detected"],
        "coaching": [],
        "source": "none",
        "no_speech": True,
    }


def score_qa(ratings: list[dict], rules: Rules) -> dict:
    """Per-parameter ratings -> call QA summary. N/A parameters are left out of the score."""
    scale = rules.ratings
    criteria = {c["id"]: c for c in rules.criteria}
    items, counts = [], {r: 0 for r in ("met", "partial", "not_met", "not_applicable")}
    for q in ratings:
        c = criteria[q["id"]]
        rating = q["rating"]
        counts[rating] += 1
        spec = scale.get(rating)
        items.append({
            "id": q["id"], "name": c["name"], "category": c.get("category", ""), "type": c.get("type", "objective"),
            "source": c.get("source", "transcript"), "weight": float(c.get("weight", 1)), "rating": rating,
            "rating_label": spec["label"] if spec else "N/A",
            "score": spec["score"] if spec else None,
            "credit": float(spec["credit"]) if spec else None,
            "evidence": q["evidence"], "rationale": q["rationale"], "recommendation": q.get("recommendation", ""),
        })
    scored = [i for i in items if i["credit"] is not None]
    total_weight = sum(i["weight"] for i in scored)
    percent = round(100 * sum(i["weight"] * i["credit"] for i in scored) / total_weight, 1) if total_weight else None
    avg_score = round(sum(i["score"] for i in scored) / len(scored), 2) if scored else None
    return {
        "percent": percent,
        "avg_score": avg_score,
        "max_score": max(spec["score"] for spec in scale.values()),
        "passed": percent is not None and percent >= rules.pass_threshold,
        "counts": counts,
        "applicable": len(scored),
        "criteria": items,
    }


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _label(score: float) -> str:
    if score > POSITIVE_THRESHOLD:
        return "positive"
    if score < -POSITIVE_THRESHOLD:
        return "negative"
    return "neutral"


def build_call_document(existing: dict, transcript: dict, language: dict, insights: dict, rules: Rules) -> dict:
    call_id = transcript["call_id"]
    meta = transcript.get("metadata", {})

    utterances = []
    for p in language["phrases"]:
        scores = p.get("scores") or {}
        utterances.append({
            "speaker": p["speaker"],
            "start_ms": p["offset_ms"],
            "end_ms": p["offset_ms"] + p["duration_ms"],
            "text": p["text"],
            "sentiment": p.get("sentiment", "neutral"),
            "score": round(scores.get("positive", 0) - scores.get("negative", 0), 3),
        })
    customer_scores = [u["score"] for u in utterances if u["speaker"] == "customer"]
    agent_scores = [u["score"] for u in utterances if u["speaker"] == "agent"]
    customer_score = _avg(customer_scores)
    overall = _avg([u["score"] for u in utterances])

    qa = score_qa(insights["qa"], rules)
    qa_percent = qa["percent"]

    checks = {c["id"]: c for c in rules.checks}
    comp_items = [{
        "id": c["id"], "name": checks[c["id"]]["name"], "kind": checks[c["id"]]["kind"],
        "status": c["status"], "evidence": c["evidence"],
    } for c in insights["compliance"]]
    failed_checks = [c for c in comp_items if c["status"] == "fail"]

    emotion = insights["customer_emotion"]
    from .llm import NEGATIVE_EMOTIONS

    no_speech = bool(insights.get("no_speech"))
    flags = {
        "unhappy_customer": customer_score < -POSITIVE_THRESHOLD or emotion["end"] in NEGATIVE_EMOTIONS,
        "repeat_contact": insights["repeat_contact"],
        "unresolved": not no_speech and insights["resolution"] in ("unresolved", "escalated"),
        "compliance_breach": bool(failed_checks),
        "low_qa": qa_percent is not None and qa_percent < rules.pass_threshold,
        "no_speech": no_speech,
    }

    started_at = normalize_timestamp(meta.get("started_at") or existing.get("started_at"))
    doc = dict(existing)
    doc.update({
        "id": call_id,
        "call_id": call_id,
        "status": STATUS_COMPLETED,
        "stage": "done",
        "error": None,
        "attempts": 0,
        "audio_blob": transcript.get("audio_blob") or existing.get("audio_blob"),
        "transcript_blob": f"{call_id}.json",
        "insights_blob": f"json/{call_id}.json",
        **{k: meta.get(k) or existing.get(k) for k in METADATA_FIELDS},
        "started_at": started_at,
        "call_date": started_at[:10],
        "duration_sec": round(transcript.get("duration_ms", 0) / 1000),
        "locale": transcript.get("locale") or "en-US",
        "summary": insights["summary"],
        "call_reason": insights["call_reason"],
        "call_reason_name": rules.category_name(insights["call_reason"]),
        "call_reason_detail": insights["call_reason_detail"],
        "root_cause": insights["root_cause"],
        "resolution": insights["resolution"],
        "sentiment": {
            "overall": _label(overall),
            "score": overall,
            "customer": _label(customer_score),
            "customer_score": customer_score,
            "agent_score": _avg(agent_scores),
            "customer_end_score": _avg(customer_scores[-3:]),
        },
        "emotion": emotion,
        "qa": qa,
        "compliance": {"passed": not failed_checks, "failed_count": len(failed_checks), "checks": comp_items},
        "flags": flags,
        "flag_list": [k for k, v in flags.items() if v],
        "pii": language.get("pii_summary", {"total": 0, "categories": {}}),
        "topics": insights["topics"],
        "follow_up_actions": insights["follow_up_actions"],
        "coaching": insights.get("coaching", []),
        "transcript": utterances,
        "processed_at": utcnow(),
        "prompt_version": insights.get("prompt_version"),
        "model": insights.get("model", insights.get("source")),
        "token_usage": insights.get("token_usage"),
        "sources": {"speech": transcript.get("source"), "language": language.get("source"), "llm": insights.get("source")},
    })
    doc.setdefault("history", []).append({"at": utcnow(), "event": "Analysis completed"})
    doc["history"] = doc["history"][-50:]
    return doc


# --------------------------------------------------------------------------- entry points

def process_recording(blob_name: str, force: bool = False) -> dict | None:
    """Run both stages in-process (local mode, CLI, tests)."""
    if force:
        get_call_store().patch(call_id_for(blob_name), status=STATUS_UPLOADED, attempts=0)
    if transcribe_recording(blob_name) is None:
        return None
    return enrich_transcript(f"{call_id_for(blob_name)}.json")


def retry(call_id: str) -> dict | None:
    """Re-run a call from the stage that failed (in-process)."""
    doc = get_call_store().get(call_id)
    if not doc:
        raise KeyError(call_id)
    s = get_settings()
    if doc.get("stage") == "enrich" and get_blob_store().exists(s.container_transcripts, f"{call_id}.json"):
        return enrich_transcript(f"{call_id}.json")
    return process_recording(doc["audio_blob"], force=True)


def parse_queue_message(body: str | bytes | dict) -> tuple[str, str]:
    """Return (container, blob_name) from an Event Grid BlobCreated message or a retry message.

    Retry messages (sent by the web app) look like ``{"call_id": "...", "container": "...", "blob": "..."}``.
    """
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    if isinstance(body, str):
        text = body.strip()
        if not text.startswith(("{", "[")):  # base64-encoded delivery
            text = base64.b64decode(text).decode("utf-8")
        body = json.loads(text)
    if isinstance(body, list):  # Event Grid can batch; queue delivery sends one event per message
        body = body[0]
    if "blob" in body and "container" in body:
        return body["container"], body["blob"]
    subject = body.get("subject", "")  # /blobServices/default/containers/<c>/blobs/<path>
    marker = "/containers/"
    if marker not in subject or "/blobs/" not in subject:
        raise ValueError(f"Unrecognised queue message: {str(body)[:200]}")
    container, blob = subject.split(marker, 1)[1].split("/blobs/", 1)
    return container, blob
