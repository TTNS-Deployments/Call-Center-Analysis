"""Curated Parquet output in the data lake, read by Power BI.

Layout (one file per call per table, partitioned by date)::

    insights/curated/calls/YYYY/MM/DD/<call_id>.parquet
    insights/curated/qa_scores/YYYY/MM/DD/<call_id>.parquet
    insights/curated/compliance/YYYY/MM/DD/<call_id>.parquet
    insights/curated/utterances/YYYY/MM/DD/<call_id>.parquet

Re-processing a call overwrites its files, so the output stays idempotent.
"""

from __future__ import annotations

import io
from datetime import date, datetime

import pyarrow as pa
import pyarrow.parquet as pq

CALLS_SCHEMA = pa.schema([
    ("call_id", pa.string()), ("call_date", pa.date32()), ("started_at", pa.timestamp("s", tz="UTC")),
    ("agent_id", pa.string()), ("agent_name", pa.string()), ("team", pa.string()), ("customer_id", pa.string()),
    ("direction", pa.string()), ("duration_sec", pa.int32()),
    ("call_reason", pa.string()), ("call_reason_name", pa.string()), ("call_reason_detail", pa.string()),
    ("root_cause", pa.string()), ("resolution", pa.string()), ("summary", pa.string()),
    ("sentiment_label", pa.string()), ("sentiment_score", pa.float32()),
    ("customer_sentiment_score", pa.float32()), ("agent_sentiment_score", pa.float32()),
    ("emotion_start", pa.string()), ("emotion_end", pa.string()), ("emotion_dominant", pa.string()),
    ("qa_score", pa.int32()), ("qa_max", pa.int32()), ("qa_percent", pa.float32()), ("qa_passed", pa.bool_()),
    ("compliance_passed", pa.bool_()), ("compliance_failed_count", pa.int32()), ("pii_entities", pa.int32()),
    ("flag_unhappy", pa.bool_()), ("flag_repeat_contact", pa.bool_()), ("flag_unresolved", pa.bool_()),
    ("flag_compliance_breach", pa.bool_()), ("flag_low_qa", pa.bool_()),
    ("processed_at", pa.timestamp("s", tz="UTC")), ("prompt_version", pa.string()), ("model", pa.string()),
])

QA_SCHEMA = pa.schema([
    ("call_id", pa.string()), ("call_date", pa.date32()), ("agent_id", pa.string()), ("team", pa.string()),
    ("criterion_id", pa.string()), ("criterion_name", pa.string()), ("category", pa.string()), ("type", pa.string()),
    ("score", pa.int32()), ("max_points", pa.int32()), ("percent", pa.float32()), ("evidence", pa.string()),
])

COMPLIANCE_SCHEMA = pa.schema([
    ("call_id", pa.string()), ("call_date", pa.date32()), ("agent_id", pa.string()), ("team", pa.string()),
    ("check_id", pa.string()), ("check_name", pa.string()), ("kind", pa.string()), ("status", pa.string()),
    ("evidence", pa.string()),
])

UTTERANCE_SCHEMA = pa.schema([
    ("call_id", pa.string()), ("call_date", pa.date32()), ("seq", pa.int32()), ("speaker", pa.string()),
    ("start_ms", pa.int64()), ("end_ms", pa.int64()), ("text", pa.string()), ("sentiment", pa.string()),
    ("sentiment_score", pa.float32()),
])


def _ts(value: str | None):
    return datetime.fromisoformat(value) if value else None


def _to_bytes(rows: list[dict], schema: pa.Schema) -> bytes:
    table = pa.Table.from_pylist(rows, schema=schema)
    buf = io.BytesIO()
    pq.write_table(table, buf, compression="snappy")
    return buf.getvalue()


def call_rows(doc: dict) -> dict[str, list[dict]]:
    call_date = date.fromisoformat(doc["call_date"])
    sentiment, emotion, qa, comp, flags = (doc.get(k) or {} for k in ("sentiment", "emotion", "qa", "compliance", "flags"))
    base = {"call_id": doc["call_id"], "call_date": call_date}
    calls = [{
        **base,
        "started_at": _ts(doc.get("started_at")),
        "agent_id": doc.get("agent_id"), "agent_name": doc.get("agent_name"), "team": doc.get("team"),
        "customer_id": doc.get("customer_id"), "direction": doc.get("direction"),
        "duration_sec": doc.get("duration_sec"),
        "call_reason": doc.get("call_reason"), "call_reason_name": doc.get("call_reason_name"),
        "call_reason_detail": doc.get("call_reason_detail"), "root_cause": doc.get("root_cause"),
        "resolution": doc.get("resolution"), "summary": doc.get("summary"),
        "sentiment_label": sentiment.get("overall"), "sentiment_score": sentiment.get("score"),
        "customer_sentiment_score": sentiment.get("customer_score"), "agent_sentiment_score": sentiment.get("agent_score"),
        "emotion_start": emotion.get("start"), "emotion_end": emotion.get("end"), "emotion_dominant": emotion.get("dominant"),
        "qa_score": qa.get("score"), "qa_max": qa.get("max"), "qa_percent": qa.get("percent"), "qa_passed": qa.get("passed"),
        "compliance_passed": comp.get("passed"), "compliance_failed_count": comp.get("failed_count"),
        "pii_entities": (doc.get("pii") or {}).get("total", 0),
        "flag_unhappy": flags.get("unhappy_customer", False), "flag_repeat_contact": flags.get("repeat_contact", False),
        "flag_unresolved": flags.get("unresolved", False), "flag_compliance_breach": flags.get("compliance_breach", False),
        "flag_low_qa": flags.get("low_qa", False),
        "processed_at": _ts(doc.get("processed_at")), "prompt_version": doc.get("prompt_version"), "model": doc.get("model"),
    }]
    agent_cols = {"agent_id": doc.get("agent_id"), "team": doc.get("team")}
    qa_rows = [{
        **base, **agent_cols, "criterion_id": c["id"], "criterion_name": c["name"], "category": c.get("category"),
        "type": c.get("type"), "score": c["score"], "max_points": c["max_points"],
        "percent": round(100 * c["score"] / c["max_points"], 1) if c["max_points"] else 0.0, "evidence": c.get("evidence"),
    } for c in qa.get("criteria", [])]
    comp_rows = [{
        **base, **agent_cols, "check_id": c["id"], "check_name": c["name"], "kind": c["kind"], "status": c["status"],
        "evidence": c.get("evidence"),
    } for c in comp.get("checks", [])]
    utt_rows = [{
        **base, "seq": i, "speaker": u["speaker"], "start_ms": u["start_ms"], "end_ms": u["end_ms"], "text": u["text"],
        "sentiment": u.get("sentiment"), "sentiment_score": u.get("score"),
    } for i, u in enumerate(doc.get("transcript", []))]
    return {"calls": calls, "qa_scores": qa_rows, "compliance": comp_rows, "utterances": utt_rows}


SCHEMAS = {"calls": CALLS_SCHEMA, "qa_scores": QA_SCHEMA, "compliance": COMPLIANCE_SCHEMA, "utterances": UTTERANCE_SCHEMA}


def write_call(blob_store, container: str, doc: dict) -> list[str]:
    d = date.fromisoformat(doc["call_date"])
    written = []
    for table, rows in call_rows(doc).items():
        name = f"curated/{table}/{d:%Y/%m/%d}/{doc['call_id']}.parquet"
        blob_store.put_bytes(container, name, _to_bytes(rows, SCHEMAS[table]), "application/octet-stream")
        written.append(name)
    return written
