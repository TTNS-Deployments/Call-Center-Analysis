"""Publishes a 'call insights ready' event for downstream systems (CRM, real-time phase).

Failures are logged, never raised: a CRM feed problem must not fail call processing.
"""

from __future__ import annotations

import json
import logging

from .config import Settings

log = logging.getLogger(__name__)

EVENT_FIELDS = (
    "call_id", "agent_id", "team", "customer_id", "started_at", "duration_sec", "summary", "call_reason",
    "root_cause", "resolution", "flag_list", "processed_at",
)


def event_payload(doc: dict) -> dict:
    payload = {k: doc.get(k) for k in EVENT_FIELDS}
    payload["type"] = "call.insights.completed"
    payload["qa_percent"] = (doc.get("qa") or {}).get("percent")
    payload["sentiment"] = (doc.get("sentiment") or {}).get("overall")
    payload["compliance_passed"] = (doc.get("compliance") or {}).get("passed")
    return payload


class NullPublisher:
    def publish(self, doc: dict) -> None:
        return None


class EventHubPublisher:
    def __init__(self, settings: Settings):
        from azure.eventhub import EventHubProducerClient

        from .azure_auth import get_credential

        self.producer = EventHubProducerClient(
            fully_qualified_namespace=settings.eventhub_namespace,
            eventhub_name=settings.eventhub_name,
            credential=get_credential(),
        )

    def publish(self, doc: dict) -> None:
        from azure.eventhub import EventData

        try:
            batch = self.producer.create_batch(partition_key=doc.get("call_id"))
            batch.add(EventData(json.dumps(event_payload(doc), default=str)))
            self.producer.send_batch(batch)
        except Exception:  # noqa: BLE001
            log.exception("Failed to publish call event for %s", doc.get("call_id"))
