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
    assert {"criterion_id", "rating", "score", "credit", "applicable"} <= set(table.column_names)

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


def test_qa_scoring_excludes_not_applicable_and_weights():
    from callcenter.rules import load_rules

    r = load_rules()
    ids = [c["id"] for c in r.criteria]
    ratings = [{"id": i, "rating": "not_applicable", "evidence": "", "rationale": "", "recommendation": ""} for i in ids]
    ratings[0]["rating"] = "met"                                        # weight 1, credit 1
    ratings[1]["rating"] = "partial"                                    # weight 1, credit 0.5
    acc = ids.index("accurate_information")
    ratings[acc]["rating"] = "not_met"                                  # weight 2, credit 0
    qa = pipeline.score_qa(ratings, r)
    assert qa["applicable"] == 3 and qa["counts"]["not_applicable"] == 17
    assert qa["percent"] == round(100 * (1 + 0.5 + 0) / (1 + 1 + 2), 1)  # 37.5
    assert qa["avg_score"] == round((5 + 3 + 0) / 3, 2)
    assert not qa["passed"]

    all_na = pipeline.score_qa([{**x, "rating": "not_applicable"} for x in ratings], r)
    assert all_na["percent"] is None and not all_na["passed"]


def test_speech_metrics_detect_dead_air_and_overlaps():
    from callcenter.metrics import speech_metrics

    phrases = [
        {"speaker": "agent", "offset_ms": 0, "duration_ms": 6000, "text": "one two three four five six seven eight nine ten eleven twelve"},
        {"speaker": "customer", "offset_ms": 5000, "duration_ms": 3000, "text": "hello there"},        # overlaps agent
        {"speaker": "agent", "offset_ms": 20000, "duration_ms": 4000, "text": "sorry for the wait thank you"},  # 12 s silence
    ]
    m = speech_metrics(phrases)
    assert m["dead_air_count"] == 1 and m["longest_silence_s"] == 12.0
    assert m["overlaps"] == 1 and m["agent_interruptions"] == 0
    assert m["agent_wpm"] == round(18 / (10000 / 60000))  # 12 + 6 agent words over 10 s


def test_silent_recording_is_not_scored(monkeypatch):
    s = get_settings()
    get_blob_store().put_bytes(s.container_recordings, "SILENT-1.wav", samples.make_wav(1000))

    class Silent:
        def transcribe(self, audio, filename, metadata):
            return {"source": "test", "locale": "en-US", "duration_ms": 1000, "phrases": []}

    class MustNotBeCalled:
        def generate(self, *a, **k):
            raise AssertionError("no LLM call for a silent recording")

    monkeypatch.setattr(pipeline, "get_speech_client", lambda: Silent())
    monkeypatch.setattr(pipeline, "get_insights_client", lambda: MustNotBeCalled())
    doc = pipeline.process_recording("SILENT-1.wav")
    assert doc["status"] == "completed" and doc["flag_list"] == ["no_speech"]
    assert doc["qa"]["percent"] is None and doc["compliance"]["passed"]


def test_non_numeric_qa_filters_are_ignored():
    items = [{"qa": {"percent": 40}}, {"qa": {"percent": 90}}]
    assert analytics.filter_calls(items, {"qa_min": "abc", "qa_max": "<script>"}) == items
    assert analytics.filter_calls(items, {"qa_min": "50"}) == items[1:]


@pytest.mark.parametrize("env, message", [
    ({"CC_MODE": "prodution"}, "Unknown CC_MODE"),
    ({"AGENT_CHANNEL": "left"}, "AGENT_CHANNEL must be a whole number"),
])
def test_invalid_settings_are_reported_clearly(monkeypatch, env, message):
    from callcenter.config import reset_settings_cache

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    with pytest.raises(ValueError, match=message):
        get_settings()
