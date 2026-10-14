"""How the web app hands work to the pipeline.

* LocalDispatcher: runs the pipeline in a background thread inside the web app process.
* QueueDispatcher (Azure): uploads are picked up automatically by Event Grid -> queue -> Function,
  so only retries need an explicit message on the stage's queue.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor

from .config import Settings

log = logging.getLogger(__name__)


class LocalDispatcher:
    def __init__(self, workers: int = 2):
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pipeline")

    def _run(self, fn, *args):
        def task():
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - failure is already recorded on the call
                log.exception("Pipeline task failed")

        return self.pool.submit(task)

    def recording_uploaded(self, blob_name: str):
        from .pipeline import process_recording

        return self._run(process_recording, blob_name)

    def retry(self, call_id: str):
        from .pipeline import retry

        return self._run(retry, call_id)


class QueueDispatcher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _queue(self, name: str):
        from azure.storage.queue import QueueClient

        # Plain-text JSON: the Functions host is configured with messageEncoding "none" (host.json).
        s = self.settings
        if s.storage_connection_string:
            return QueueClient.from_connection_string(s.storage_connection_string, name)
        from .azure_auth import get_credential

        url = s.queue_account_url or s.storage_account_url.replace(".blob.", ".queue.")
        return QueueClient(url, name, credential=get_credential())

    def recording_uploaded(self, blob_name: str):
        return None  # Event Grid BlobCreated -> recordings-events queue -> transcribe Function

    def retry(self, call_id: str):
        from .factory import get_blob_store, get_call_store

        s = self.settings
        doc = get_call_store().get(call_id)
        if not doc:
            raise KeyError(call_id)
        transcript = f"{call_id}.json"
        if doc.get("stage") == "enrich" and get_blob_store().exists(s.container_transcripts, transcript):
            queue, message = s.queue_transcripts, {"call_id": call_id, "container": s.container_transcripts, "blob": transcript}
        else:
            queue, message = s.queue_recordings, {"call_id": call_id, "container": s.container_recordings, "blob": doc["audio_blob"]}
        get_call_store().add_history(call_id, "Retry requested", status="uploaded", error=None, attempts=0)
        self._queue(queue).send_message(json.dumps(message))
