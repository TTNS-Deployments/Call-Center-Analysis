"""Azure Functions entry points (Python v2 programming model).

Flow (all wiring is created by the ARM template):

    recordings/<file>  --BlobCreated--> Event Grid --> queue "recordings-events"  --> transcribe()
    transcripts/<id>   --BlobCreated--> Event Grid --> queue "transcripts-events" --> enrich()

A message that keeps failing is retried (host.json maxDequeueCount) and then moved to the
"-poison" queue; the poison handlers mark the call as failed so it shows up on the
Pipeline page, where it can be retried.
"""

import logging

import azure.functions as func

from callcenter import pipeline

app = func.FunctionApp()

# Queue names match the ARM template (Deployment/ARM/azuredeploy.json).
CONNECTION = "PipelineStorage"


def _blob_from(msg: func.QueueMessage, expected_container: str) -> str | None:
    container, blob = pipeline.parse_queue_message(msg.get_body())
    if container != expected_container:
        logging.warning("Ignoring event for container %s", container)
        return None
    return blob


@app.queue_trigger(arg_name="msg", queue_name="recordings-events", connection=CONNECTION)
def transcribe(msg: func.QueueMessage) -> None:
    blob = _blob_from(msg, pipeline.get_settings().container_recordings)
    if not blob or not pipeline.is_audio(blob):
        return  # sidecar metadata JSON or unrelated file
    logging.info("Transcribing %s (dequeue %s)", blob, msg.dequeue_count)
    pipeline.transcribe_recording(blob)


@app.queue_trigger(arg_name="msg", queue_name="transcripts-events", connection=CONNECTION)
def enrich(msg: func.QueueMessage) -> None:
    blob = _blob_from(msg, pipeline.get_settings().container_transcripts)
    if not blob or not blob.endswith(".json"):
        return
    logging.info("Enriching %s (dequeue %s)", blob, msg.dequeue_count)
    pipeline.enrich_transcript(blob)


def _poison(msg: func.QueueMessage, stage: str) -> None:
    try:
        _, blob = pipeline.parse_queue_message(msg.get_body())
    except ValueError:
        logging.error("Unparseable poison message: %s", msg.get_body()[:500])
        return
    if stage == "transcribe" and not pipeline.is_audio(blob):
        return
    call_id = pipeline.call_id_for(blob)
    logging.error("Giving up on %s at stage %s", call_id, stage)
    pipeline.mark_failed(call_id, stage, RuntimeError("Processing failed after all retries"), final=True)


@app.queue_trigger(arg_name="msg", queue_name="recordings-events-poison", connection=CONNECTION)
def transcribe_poison(msg: func.QueueMessage) -> None:
    _poison(msg, "transcribe")


@app.queue_trigger(arg_name="msg", queue_name="transcripts-events-poison", connection=CONNECTION)
def enrich_poison(msg: func.QueueMessage) -> None:
    _poison(msg, "enrich")
