import io

import pyarrow.parquet as pq
import pytest

from callcenter import analytics, pipeline, samples
from callcenter.config import get_settings
from callcenter.factory import get_blob_store, get_call_store


def upload_samples(n=6):
    s = get_settings()
    blobs = get_blob_store()
    calls = samples.generate_calls(count=n, days=10, seed=3)
    for call_id, meta, wav in calls:
        blobs.put_json(s.container_recordings, f"{call_id}.json", meta)
        blobs.put_bytes(s.container_recordings, f"{call_id}.wav", wav)
    return calls


def test_end_to_end_local_pipeline():
    calls = upload_samples()
    s = get_settings()
    for call_id, meta, _ in calls:
        doc = pipeline.process_recording(f"{call_id}.wav")
        assert doc["status"] == "completed"
        assert doc["agent_id"] == meta["agent_id"]
        assert 0 <= doc["qa"]["percent"] <= 100
        assert doc["transcript"] and all("text" in u for u in doc["transcript"])
        # no raw PII from the script survives in the stored transcript
        stored = " ".join(u["text"] for u in doc["transcript"])
        assert meta["agent_name"].split()[0] not in stored
        # parquet outputs are readable
        names = get_blob_store().list(s.container_insights, "curated/calls/")
        assert any(call_id in n for n in names)

    table = pq.read_table(io.BytesIO(get_blob_store().get_bytes(
        s.container_insights, next(n for n in get_blob_store().list(s.container_insights, "curated/qa_scores/")))))
    assert {"criterion_id", "score", "max_points"} <= set(table.column_names)

    summaries = get_call_store().list_summaries()
    assert len(summaries) == len(calls) and "transcript" not in summaries[0]
    overview = analytics.overview(summaries)
    assert overview["kpis"]["completed"] == len(calls)


def test_non_audio_is_ignored_and_duplicates_skipped():
    upload_samples(1)
    assert pipeline.transcribe_recording("CALL-x.json") is None
    blob = next(n for n in get_blob_store().list(get_settings().container_recordings) if n.endswith(".wav"))
    pipeline.process_recording(blob)
    assert pipeline.transcribe_recording(blob) is None  # duplicate BlobCreated event
    assert pipeline.process_recording(blob, force=True)["status"] == "completed"


def test_failure_is_recorded_and_retry_recovers(monkeypatch):
    upload_samples(1)
    blob = next(n for n in get_blob_store().list(get_settings().container_recordings) if n.endswith(".wav"))
    from callcenter import factory

    class Broken:
        def generate(self, *a, **k):
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(factory, "get_insights_client", lambda: Broken())
    monkeypatch.setattr(pipeline, "get_insights_client", lambda: Broken())
    with pytest.raises(RuntimeError):
        pipeline.process_recording(blob)
    doc = get_call_store().get(pipeline.call_id_for(blob))
    assert doc["status"] == "failed" and doc["stage"] == "enrich" and "model unavailable" in doc["error"]

    monkeypatch.undo()
    assert pipeline.retry(doc["call_id"])["status"] == "completed"


@pytest.mark.parametrize("value, expected", [
    ("2026-10-01T10:15:00+05:30", "2026-10-01T04:45:00+00:00"),
    ("2026-10-01T04:45:00Z", "2026-10-01T04:45:00+00:00"),
    ("2026-10-01T04:45:00", "2026-10-01T04:45:00+00:00"),
])
def test_timestamps_normalized_to_utc(value, expected):
    assert pipeline.normalize_timestamp(value) == expected


def test_parse_event_grid_message():
    msg = '{"subject": "/blobServices/default/containers/recordings/blobs/2026/10/CALL-1.wav", "data": {}}'
    assert pipeline.parse_queue_message(msg) == ("recordings", "2026/10/CALL-1.wav")
