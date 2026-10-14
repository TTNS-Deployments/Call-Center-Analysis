"""Call store: one JSON document per call (status, metadata, insights, redacted transcript).

* Local mode: SQLite file under the local data folder.
* Azure: Cosmos DB for NoSQL, container partitioned on ``/id``.

The web app and CRM API read from here. Parquet in the data lake is the
analytics copy that Power BI reads.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings

# Fields left out of list/summary queries to keep them small.
HEAVY_FIELDS = ("transcript", "history")

SUMMARY_FIELDS = (
    "id", "call_id", "status", "stage", "attempts", "error", "audio_blob", "agent_id", "agent_name",
    "agent_email", "team", "customer_id", "direction", "started_at", "call_date", "duration_sec",
    "summary", "call_reason", "call_reason_name", "call_reason_detail", "root_cause", "resolution", "sentiment", "emotion",
    "qa", "compliance", "flags", "flag_list", "pii", "topics", "sources", "transcript_blob", "created_at", "updated_at", "processed_at",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def to_summary(doc: dict) -> dict:
    return {k: v for k, v in doc.items() if k not in HEAVY_FIELDS}


class CallStore(ABC):
    @abstractmethod
    def get(self, call_id: str) -> dict | None: ...

    @abstractmethod
    def upsert(self, doc: dict) -> dict: ...

    @abstractmethod
    def list_summaries(self, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
        """Summaries (no transcript) with ``started_at`` in [date_from, date_to], newest first."""

    @abstractmethod
    def delete(self, call_id: str) -> None: ...

    def patch(self, call_id: str, **fields) -> dict:
        doc = self.get(call_id) or {"id": call_id, "call_id": call_id, "created_at": utcnow()}
        doc.update(fields)
        return self.upsert(doc)

    def add_history(self, call_id: str, event: str, **fields) -> dict:
        doc = self.get(call_id) or {"id": call_id, "call_id": call_id, "created_at": utcnow()}
        doc.update(fields)
        doc.setdefault("history", []).append({"at": utcnow(), "event": event})
        doc["history"] = doc["history"][-50:]
        return self.upsert(doc)


class SqliteCallStore(CallStore):
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS calls ("
                " id TEXT PRIMARY KEY, started_at TEXT, doc TEXT NOT NULL)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_calls_started ON calls(started_at)")

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, call_id):
        with self._connect() as conn:
            row = conn.execute("SELECT doc FROM calls WHERE id = ?", (call_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def upsert(self, doc):
        doc = dict(doc)
        doc.setdefault("id", doc.get("call_id"))
        doc["updated_at"] = utcnow()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO calls(id, started_at, doc) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET started_at = excluded.started_at, doc = excluded.doc",
                (doc["id"], doc.get("started_at") or "", json.dumps(doc, default=str)),
            )
        return doc

    def list_summaries(self, date_from=None, date_to=None):
        sql, params = "SELECT doc FROM calls WHERE 1=1", []
        if date_from:
            sql += " AND started_at >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND started_at <= ?"
            params.append(date_to)
        sql += " ORDER BY started_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [to_summary(json.loads(r[0])) for r in rows]

    def delete(self, call_id):
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM calls WHERE id = ?", (call_id,))


class CosmosCallStore(CallStore):
    def __init__(self, settings: Settings):
        from azure.cosmos import CosmosClient, PartitionKey

        if not settings.cosmos_endpoint:
            raise RuntimeError("Set COSMOS_ENDPOINT")
        if settings.cosmos_key:
            client = CosmosClient(settings.cosmos_endpoint, credential=settings.cosmos_key)
        else:
            from .azure_auth import get_credential

            client = CosmosClient(settings.cosmos_endpoint, credential=get_credential())
        database = client.get_database_client(settings.cosmos_database)
        if settings.cosmos_key:
            # Data-plane RBAC (managed identity) cannot create resources; the ARM template does that.
            database = client.create_database_if_not_exists(settings.cosmos_database)
            database.create_container_if_not_exists(settings.cosmos_container, PartitionKey(path="/id"))
        self.container = database.get_container_client(settings.cosmos_container)

    def get(self, call_id):
        from azure.cosmos.exceptions import CosmosResourceNotFoundError

        try:
            doc = self.container.read_item(call_id, partition_key=call_id)
        except CosmosResourceNotFoundError:
            return None
        return {k: v for k, v in doc.items() if not k.startswith("_")}

    def upsert(self, doc):
        doc = dict(doc)
        doc.setdefault("id", doc.get("call_id"))
        doc["updated_at"] = utcnow()
        self.container.upsert_item(doc)
        return doc

    def list_summaries(self, date_from=None, date_to=None):
        fields = ", ".join(f"c.{f}" for f in SUMMARY_FIELDS)
        query, params = f"SELECT {fields} FROM c WHERE 1=1", []
        if date_from:
            query += " AND c.started_at >= @from"
            params.append({"name": "@from", "value": date_from})
        if date_to:
            query += " AND c.started_at <= @to"
            params.append({"name": "@to", "value": date_to})
        query += " ORDER BY c.started_at DESC"
        items = self.container.query_items(query, parameters=params, enable_cross_partition_query=True)
        return [{k: v for k, v in item.items() if v is not None} for item in items]

    def delete(self, call_id):
        self.container.delete_item(call_id, partition_key=call_id)
