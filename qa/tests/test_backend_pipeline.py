"""Backend: pipeline stages, PII, scoring, E2E, edge and failure cases (test IDs B-xx)."""
from __future__ import annotations

import io
import json
import logging
import time

import pyarrow.parquet as pq
import pytest

from callcenter import events, pipeline, samples, speech
from callcenter.config import Settings, get_settings
from callcenter.factory import get_blob_store, get_call_store
from callcenter.llm import EMOTIONS, RESOLUTIONS, MockInsightsClient
from callcenter.rules import RATINGS, load_rules

from conftest import DEMO_IDS, REPO, SAMPLE_COUNT, call_doc, dumps, leaks, raw_pii_from_script

S = get_settings


def blobs():
    return get_blob_store()


def put_audio(call_id: str, meta: dict | None = None, data: bytes | None = None, ext: str = ".wav") -> str:
    s = S()
    if meta is not None:
        blobs().put_json(s.container_recordings, f"{call_id}.json", meta)
    blobs().put_bytes(s.container_recordings, f"{call_id}{ext}", data if data is not None else samples.make_wav(2000))
    return f"{call_id}{ext}"


class FakeSpeech:
    def __init__(self, phrases):
        self.phrases = phrases

    def transcribe(self, audio, filename, metadata):
        dur = (self.phrases[-1]["offset_ms"] + self.phrases[-1]["duration_ms"]) if self.phrases else 0
        return {"source": "fake", "locale": "en-US", "duration_ms": dur, "phrases": self.phrases}


def phrases_from(lines):
    return samples.time_script([{"speaker": s, "text": t} for s, t in lines])


# --------------------------------------------------------------------------- B-03 seed via make

def test_B03_make_seed_populates_store(tmp_path):
    import subprocess

    env = {**__import__("os").environ, "LOCAL_DATA_DIR": str(tmp_path / "seed")}
    r = subprocess.run(["make", "seed"], cwd=str(REPO), env=env,
                       capture_output=True, text=True, timeout=600)
    print(r.stdout[-800:], r.stderr[-400:])
    assert r.returncode == 0
    assert r.stdout.count("CALL-") >= 40 and "FAILED" not in r.stdout


# --------------------------------------------------------------------------- B-04 transcription output

def test_B04_transcript_well_formed(seeded):
    s = S()
    names = blobs().list(s.container_transcripts)
    assert len(names) >= SAMPLE_COUNT + 4
    for name in names:
        t = blobs().get_json(s.container_transcripts, name)
        assert {"source", "locale", "duration_ms", "phrases", "call_id", "audio_blob", "transcribed_at"} <= set(t), name
        assert t["phrases"], name
        prev = -1
        for p in t["phrases"]:
            assert p["speaker"] in ("agent", "customer")
            assert isinstance(p["offset_ms"], int) and p["offset_ms"] > prev
            assert p["duration_ms"] > 0 and p["text"].strip()
            assert 0 <= p["confidence"] <= 1
            prev = p["offset_ms"]
        assert t["duration_ms"] >= t["phrases"][-1]["offset_ms"]
        assert "mock_script" not in t["metadata"]
        assert {p["speaker"] for p in t["phrases"]} == {"agent", "customer"}, name


# --------------------------------------------------------------------------- B-05..B-08 PII

def _all_outputs(call_id: str) -> dict[str, str]:
    """Every redacted output for one call, as text."""
    s = S()
    doc = call_doc(call_id)
    out = {
        "call_store.transcript": " ".join(u["text"] for u in doc["transcript"]),
        "call_store.summary+reason+root": " ".join(str(doc.get(k) or "") for k in
                                                  ("summary", "call_reason_detail", "root_cause")),
        "call_store.qa/compliance/coaching": dumps([doc["qa"], doc["compliance"], doc["coaching"]]),
        "insights_json": blobs().get_bytes(s.container_insights, f"json/{call_id}.json").decode(),
        "event_payload": dumps(events.event_payload(doc)),
    }
    for name in blobs().list(s.container_insights, "curated/"):
        if name.endswith(f"/{call_id}.parquet"):
            table = pq.read_table(io.BytesIO(blobs().get_bytes(s.container_insights, name)))
            out[f"parquet:{name.split('/')[1]}"] = dumps(table.to_pylist())
    return out


def test_B05_pii_redacted_in_all_outputs_sample_calls(seeded):
    """Raw names, phones, emails, card/account numbers, DOB, address from the sample scripts must not survive."""
    problems = []
    checked = 0
    for call_id, meta, _ in seeded["calls"]:
        pii = raw_pii_from_script(meta["mock_script"])
        for where, text in _all_outputs(call_id).items():
            # agent_name is legitimate metadata in the store/insights/parquet; check agent names only in free text
            free_text = where in ("call_store.transcript", "call_store.summary+reason+root")
            found = leaks(text, pii, include_agent_names=free_text)
            checked += 1
            if found:
                problems.append(f"{call_id} {where}: {found}")
    print(f"checked {checked} outputs; problems={len(problems)}")
    for p in problems[:20]:
        print(p)
    assert not problems


DEMO_PII = {
    "DEMO-0001": ["Rohan", "Das", "9 8 7 6 5, 4 3 2 1 0", "rohan dot das at example dot com",
                  "twelfth of March, nineteen ninety"],
    "DEMO-0002": ["Meera", "Nair", "4 1 1 1, 2 2 2 2, 3 3 3 3, 4 4 4 4", "C V V is 1 2 3", "5 8 2 9 1 4", "Vikram"],
    "DEMO-0003": ["Karan", "Malhotra", "9 1 2 3 4, 5 6 7 8 9", "Rahul"],
    "DEMO-0004": ["Rajesh", "Kumar", "Neha"],
}


@pytest.mark.parametrize("call_id", DEMO_IDS)
def test_B06_pii_redacted_demo_calls(seeded, call_id):
    """Spoken-style PII in the scripted demo calls (Data/demo_calls/scripts.yaml)."""
    outputs = _all_outputs(call_id)
    text = outputs["call_store.transcript"] + " " + outputs["call_store.summary+reason+root"]
    leaked = [v for v in DEMO_PII[call_id] if v in text]
    print(call_id, "pii_summary:", call_doc(call_id)["pii"], "leaked:", leaked)
    for u in call_doc(call_id)["transcript"]:
        if any(v in u["text"] for v in leaked):
            print("   ", u["speaker"], "|", u["text"])
    assert not leaked


@pytest.mark.parametrize("text, must_not_contain, token", [
    ("Email me at sneha.k99@example.com please", ["sneha.k99@example.com"], "[EMAIL]"),
    ("Call me on +91 98765 43210", ["98765", "43210"], "[PHONE]"),
    ("My card is 4111-2222-3333-4444", ["4111", "4444"], "[CARD]"),
    ("Card 4111222233334444 expired", ["4111222233334444"], "[CARD]"),
    ("Account number 1234 5678 9012", ["1234 5678 9012"], "[ACCOUNT]"),
    ("My PAN is ABCDE1234F", ["ABCDE1234F"], "[PAN]"),
    ("My name is Zoya Khan", ["Zoya", "Khan"], "[NAME]"),
    ("I live at Flat 402, Lake View Apartments, Bengaluru 560034", ["Flat 402", "560034"], "[ADDRESS]"),
])
def test_B07_pii_patterns_mock_language(text, must_not_contain, token):
    from callcenter.language import MockLanguageClient

    out = MockLanguageClient(S()).analyze([{"speaker": "customer", "offset_ms": 0, "duration_ms": 1000, "text": text}])
    red = out["phrases"][0]["text"]
    print(repr(text), "->", repr(red))
    assert token in red and not any(v in red for v in must_not_contain)


def test_B08_pii_absent_from_logs(seeded, caplog):
    """Process a PII-heavy call with DEBUG logging; no raw PII may reach the log records."""
    caplog.set_level(logging.DEBUG)
    calls = samples.generate_calls(count=3, days=1, seed=99)
    pii_all = {}
    for call_id, meta, wav in calls:
        cid = f"QA-LOG-{call_id}"
        put_audio(cid, {**meta, "call_id": cid}, wav)
        pipeline.process_recording(f"{cid}.wav")
        for k, v in raw_pii_from_script(meta["mock_script"]).items():
            pii_all.setdefault(k, set()).update(v)
    pii_all.pop("agent_names")
    text = "\n".join(r.getMessage() for r in caplog.records)
    print(f"{len(caplog.records)} log records captured")
    assert not leaks(text, pii_all)


# --------------------------------------------------------------------------- B-09..B-13 scoring

def test_B09_sentiment_structure_and_direction(seeded):
    for cid in [c for c, _, _ in seeded["calls"]] + DEMO_IDS:
        sent = call_doc(cid)["sentiment"]
        assert sent["overall"] in ("positive", "neutral", "negative") and sent["customer"] in ("positive", "neutral", "negative")
        for k in ("score", "customer_score", "agent_score", "customer_end_score"):
            assert -1 <= sent[k] <= 1
        for u in call_doc(cid)["transcript"]:
            assert u["sentiment"] in ("positive", "neutral", "negative", "mixed") and -1 <= u["score"] <= 1
    good, bad = call_doc("DEMO-0001")["sentiment"], call_doc("DEMO-0003")["sentiment"]
    print("DEMO-0001", good, "\nDEMO-0003", bad)
    assert good["customer"] == "positive" and bad["customer"] == "negative"
    assert good["customer_score"] > bad["customer_score"]


def test_B10_emotion_structure_and_direction(seeded):
    for cid in [c for c, _, _ in seeded["calls"]] + DEMO_IDS:
        e = call_doc(cid)["emotion"]
        assert set(e) == {"start", "end", "dominant"} and all(v in EMOTIONS for v in e.values())
    e1, e3 = call_doc("DEMO-0001")["emotion"], call_doc("DEMO-0003")["emotion"]
    print("DEMO-0001", e1, "DEMO-0003", e3)
    assert e1["end"] in ("satisfied", "happy", "calm")
    assert e3["start"] in ("angry", "frustrated")


def test_B11_qa_structure_and_direction(seeded):
    r = load_rules()
    for cid in [c for c, _, _ in seeded["calls"]] + DEMO_IDS:
        qa = call_doc(cid)["qa"]
        assert [c["id"] for c in qa["criteria"]] == [c["id"] for c in r.criteria]
        assert all(c["rating"] in RATINGS for c in qa["criteria"])
        assert qa["percent"] is None or 0 <= qa["percent"] <= 100
        assert sum(qa["counts"].values()) == 20
    q1, q2 = call_doc("DEMO-0001")["qa"], call_doc("DEMO-0002")["qa"]
    print("DEMO-0001", q1["percent"], q1["passed"], q1["counts"], "| DEMO-0002", q2["percent"], q2["passed"], q2["counts"])
    assert q1["passed"] and not q2["passed"] and q1["percent"] > q2["percent"]
    assert "low_qa" in call_doc("DEMO-0002")["flag_list"]


def test_B12_compliance_structure_and_direction(seeded):
    r = load_rules()
    for cid in [c for c, _, _ in seeded["calls"]] + DEMO_IDS:
        comp = call_doc(cid)["compliance"]
        assert [c["id"] for c in comp["checks"]] == [c["id"] for c in r.checks]
        assert all(c["status"] in ("pass", "fail", "not_applicable") for c in comp["checks"])
        assert comp["failed_count"] == sum(c["status"] == "fail" for c in comp["checks"])
        assert comp["passed"] == (comp["failed_count"] == 0)
    c1 = {c["id"]: c["status"] for c in call_doc("DEMO-0001")["compliance"]["checks"]}
    c2 = {c["id"]: c["status"] for c in call_doc("DEMO-0002")["compliance"]["checks"]}
    print("DEMO-0001", c1, "\nDEMO-0002", c2)
    assert call_doc("DEMO-0001")["compliance"]["passed"]
    assert c2["no_sensitive_credentials"] == "fail" and c2["recording_disclosure"] == "fail"
    assert "compliance_breach" in call_doc("DEMO-0002")["flag_list"]


def test_B13_summary_reason_root_cause(seeded):
    r = load_rules()
    reason_ids = {c["id"] for c in r.categories}
    for cid in [c for c, _, _ in seeded["calls"]] + DEMO_IDS:
        d = call_doc(cid)
        assert d["summary"].strip() and d["call_reason"] in reason_ids and d["call_reason_name"]
        assert d["root_cause"].strip() and d["resolution"] in RESOLUTIONS
    expected = {"DEMO-0001": ("plan_change", "resolved"), "DEMO-0002": ("card_services", "resolved"),
                "DEMO-0003": ("technical_issue", "escalated"), "DEMO-0004": ("sales_opportunity", None)}
    for cid, (reason, resolution) in expected.items():
        d = call_doc(cid)
        print(cid, d["call_reason"], d["resolution"], "| root:", d["root_cause"], "| flags:", d["flag_list"])
    got = {cid: (call_doc(cid)["call_reason"], call_doc(cid)["resolution"]) for cid in expected}
    assert all(got[c][0] == expected[c][0] for c in expected)
    assert got["DEMO-0003"][1] == "escalated" and got["DEMO-0001"][1] == "resolved"
    assert call_doc("DEMO-0003")["flags"]["repeat_contact"]


# --------------------------------------------------------------------------- B-14 end to end

def test_B14_end_to_end_one_call(seeded):
    s = S()
    calls = samples.generate_calls(count=1, days=1, seed=1234)
    _, meta, wav = calls[0]
    cid = "QA-E2E-0001"
    blob = put_audio(cid, {**meta, "call_id": cid}, wav)
    statuses = []
    store = get_call_store()
    original = store.add_history

    def spy(call_id, event, **fields):
        if call_id == cid and "status" in fields:
            statuses.append(fields["status"])
        return original(call_id, event, **fields)

    store.add_history = spy
    try:
        doc = pipeline.process_recording(blob)
    finally:
        store.add_history = original
    print("status transitions:", statuses, "->", doc["status"])
    assert statuses == ["transcribing", "transcribed", "enriching"] and doc["status"] == "completed"
    stored = store.get(cid)
    assert stored["status"] == "completed" and stored["stage"] == "done" and stored["error"] is None
    assert blobs().exists(s.container_transcripts, f"{cid}.json")
    assert blobs().exists(s.container_insights, f"json/{cid}.json")
    parquet = [n for n in blobs().list(s.container_insights, "curated/") if n.endswith(f"/{cid}.parquet")]
    assert sorted(n.split("/")[1] for n in parquet) == ["calls", "compliance", "qa_scores", "utterances"]
    assert any(c["call_id"] == cid for c in store.list_summaries())


# --------------------------------------------------------------------------- B-15..B-21 edge / negative

def test_B15_empty_or_silent_audio_mock_mode(seeded):
    """Documented mock behaviour: any audio (even 0 bytes) is replaced by a sample conversation."""
    doc = pipeline.process_recording(put_audio("QA-EMPTY-0001", {}, b""))
    print("0-byte file ->", doc["status"], len(doc["transcript"]), "utterances (mock script)")
    assert doc["status"] == "completed"


def test_B16_silent_audio_empty_transcript(seeded, monkeypatch):
    """Speech returns no phrases (what a silent recording produces). Expect a clear state, no crash."""
    monkeypatch.setattr(pipeline, "get_speech_client", lambda: FakeSpeech([]))
    blob = put_audio("QA-SILENT-0001", {"agent_id": "A1001"})
    try:
        doc = pipeline.process_recording(blob)
        print("result:", doc["status"], "qa:", doc["qa"]["percent"], "summary:", repr(doc["summary"][:120]),
              "reason:", doc["call_reason"], "flags:", doc["flag_list"])
        outcome = "completed"
    except Exception as exc:  # noqa: BLE001
        print("raised:", type(exc).__name__, exc)
        outcome = "raised"
    stored = call_doc("QA-SILENT-0001")
    print("stored status:", stored["status"], "error:", stored.get("error"))
    assert stored["status"] in ("completed", "failed")
    if stored["status"] == "failed":
        assert stored["error"]
    else:
        # A call with no speech must be identifiable as such, not scored as agent failures.
        assert outcome == "completed"
        assert not {"compliance_breach", "low_qa"} & set(stored["flag_list"]), \
            f"silent call flagged as {stored['flag_list']} with QA {stored['qa']['percent']}"


def test_B17_unsupported_file_ignored():
    assert pipeline.transcribe_recording("QA-NOTAUDIO.txt") is None
    assert pipeline.transcribe_recording("QA-NOTAUDIO.json") is None
    assert call_doc("QA-NOTAUDIO") is None


def test_B18_corrupt_audio_azure_speech_error(seeded, monkeypatch):
    """Azure Speech rejects a corrupt file (HTTP 400, mocked - no network). Call must end in failed with a reason."""
    client = speech.AzureFastTranscriptionClient(Settings(speech_endpoint="https://speech.invalid", speech_key="dummy"))

    class Resp:
        status_code = 400
        text = '{"code":"InvalidAudioFormat","message":"The audio is corrupt or unsupported."}'

    monkeypatch.setattr(speech.requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(pipeline, "get_speech_client", lambda: client)
    blob = put_audio("QA-CORRUPT-0001", {}, b"\x00\x01garbage-not-audio")
    with pytest.raises(RuntimeError):
        pipeline.process_recording(blob)
    d = call_doc("QA-CORRUPT-0001")
    print(d["status"], d["stage"], d["error"])
    assert d["status"] == "failed" and d["stage"] == "transcribe" and "Speech API error 400" in d["error"]


def test_B19_very_long_transcript(seeded, monkeypatch):
    lines = []
    for i in range(1000):
        lines.append(("agent", f"Thank you for waiting, I am checking item {i} on your account for you now."))
        lines.append(("customer", f"Okay, item {i} looks fine to me, please continue with the next one."))
    monkeypatch.setattr(pipeline, "get_speech_client", lambda: FakeSpeech(phrases_from(lines)))
    t0 = time.perf_counter()
    doc = pipeline.process_recording(put_audio("QA-LONG-0001", {"agent_id": "A1002"}))
    elapsed = time.perf_counter() - t0
    print(f"2000 phrases, duration {doc['duration_sec']}s, processed in {elapsed:.1f}s, status {doc['status']}")
    assert doc["status"] == "completed" and len(doc["transcript"]) == 2000 and elapsed < 60


def test_B20_missing_metadata(seeded):
    doc = pipeline.process_recording(put_audio("QA-NOMETA-0001"))   # no sidecar JSON at all
    print("status", doc["status"], "agent_id", doc.get("agent_id"), "started_at", doc["started_at"])
    assert doc["status"] == "completed" and doc["started_at"] and doc.get("agent_id") is None


def test_B21_llm_output_missing_fields(seeded, monkeypatch):
    class Empty(MockInsightsClient):
        def _generate(self, phrases, metadata, rules):
            return {}

    monkeypatch.setattr(pipeline, "get_insights_client", lambda: Empty())
    doc = pipeline.process_recording(put_audio("QA-LLMEMPTY-0001", {"agent_id": "A1003"}))
    print(doc["status"], doc["call_reason"], doc["resolution"], doc["qa"]["percent"], doc["compliance"]["failed_count"])
    assert doc["status"] == "completed" and doc["call_reason"] == "general_enquiry" and doc["resolution"] == "unresolved"


def test_B22_duplicate_call_skipped(seeded):
    blob = put_audio("QA-DUP-0001", {"agent_id": "A1001"})
    first = pipeline.process_recording(blob)
    processed_at = call_doc("QA-DUP-0001")["processed_at"]
    assert pipeline.process_recording(blob) is None      # duplicate BlobCreated
    assert call_doc("QA-DUP-0001")["processed_at"] == processed_at and first["status"] == "completed"
    assert len([c for c in get_call_store().list_summaries() if c["call_id"] == "QA-DUP-0001"]) == 1


def test_B23_transcribe_failure_then_retry(seeded, monkeypatch):
    class Boom:
        def transcribe(self, *a):
            raise ConnectionError("speech service unreachable")

    monkeypatch.setattr(pipeline, "get_speech_client", lambda: Boom())
    blob = put_audio("QA-FAIL-T-0001", {"agent_id": "A1001"})
    with pytest.raises(ConnectionError):
        pipeline.process_recording(blob)
    d = call_doc("QA-FAIL-T-0001")
    print("after failure:", d["status"], d["stage"], d["error"])
    assert d["status"] == "failed" and d["stage"] == "transcribe" and "unreachable" in d["error"]
    monkeypatch.undo()
    out = pipeline.retry("QA-FAIL-T-0001")
    print("after retry:", out["status"])
    assert out["status"] == "completed" and call_doc("QA-FAIL-T-0001")["error"] is None


def test_B24_enrich_failure_then_retry_from_enrich(seeded, monkeypatch):
    class Boom:
        def analyze(self, phrases):
            raise TimeoutError("language service timed out")

    blob = put_audio("QA-FAIL-E-0001", {"agent_id": "A1002"})
    monkeypatch.setattr(pipeline, "get_language_client", lambda: Boom())
    with pytest.raises(TimeoutError):
        pipeline.process_recording(blob)
    d = call_doc("QA-FAIL-E-0001")
    print("after failure:", d["status"], d["stage"], d["error"])
    assert d["status"] == "failed" and d["stage"] == "enrich"
    monkeypatch.undo()
    calls = {"n": 0}
    real = pipeline.get_speech_client

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(pipeline, "get_speech_client", counting)
    out = pipeline.retry("QA-FAIL-E-0001")
    print("after retry:", out["status"], "speech re-run:", calls["n"])
    assert out["status"] == "completed" and calls["n"] == 0


def test_B25_corrupt_transcript_json(seeded):
    s = S()
    blob = put_audio("QA-BADJSON-0001", {"agent_id": "A1001"})
    pipeline.transcribe_recording(blob)
    blobs().put_bytes(s.container_transcripts, "QA-BADJSON-0001.json", b"{not json")
    with pytest.raises(Exception):
        pipeline.enrich_transcript("QA-BADJSON-0001.json")
    d = call_doc("QA-BADJSON-0001")
    print(d["status"], d["stage"], d["error"])
    assert d["status"] == "failed" and d["stage"] == "enrich" and d["error"]


def test_B26_cli_status_and_retry(seeded):
    import subprocess
    import sys

    from conftest import REPO

    env = {**__import__("os").environ}
    r = subprocess.run([sys.executable, "-m", "callcenter", "status"], cwd=REPO, env=env, capture_output=True, text=True)
    print(r.stdout.splitlines()[0])
    assert r.returncode == 0 and "calls:" in r.stdout
    pipeline.mark_failed("QA-NOMETA-0001", "enrich", RuntimeError("simulated"))
    r = subprocess.run([sys.executable, "-m", "callcenter", "status"], cwd=REPO, env=env, capture_output=True, text=True)
    assert "FAILED QA-NOMETA-0001" in r.stdout
    r = subprocess.run([sys.executable, "-m", "callcenter", "retry", "QA-NOMETA-0001"], cwd=REPO, env=env,
                       capture_output=True, text=True)
    print(r.stdout.strip(), r.returncode)
    assert r.returncode == 0 and "completed" in r.stdout
    r = subprocess.run([sys.executable, "-m", "callcenter", "retry", "NO-SUCH-CALL"], cwd=REPO, env=env,
                       capture_output=True, text=True)
    print("unknown id ->", r.returncode, r.stderr.strip().splitlines()[-1] if r.stderr else "")
    assert r.returncode != 0


# --------------------------------------------------------------------------- B-27 functions handlers

def test_B27_functions_queue_handlers(seeded):
    import base64
    import importlib.util

    from conftest import REPO

    spec = importlib.util.spec_from_file_location("function_app", REPO / "Code" / "functions" / "function_app.py")
    fa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fa)

    class Msg:
        def __init__(self, body, dequeue_count=1):
            self.body, self.dequeue_count = body, dequeue_count

        def get_body(self):
            return self.body

    cid = "QA-FUNC-0001"
    put_audio(cid, {"agent_id": "A1004"})
    event = json.dumps({"subject": f"/blobServices/default/containers/recordings/blobs/{cid}.wav"}).encode()
    fa.transcribe(Msg(base64.b64encode(event)))       # base64 delivery
    assert call_doc(cid)["status"] == "transcribed"
    fa.enrich(Msg(json.dumps({"subject": f"/blobServices/default/containers/transcripts/blobs/{cid}.json"}).encode()))
    assert call_doc(cid)["status"] == "completed"
    fa.transcribe(Msg(json.dumps({"subject": "/blobServices/default/containers/other/blobs/x.wav"}).encode()))  # ignored
    fa.transcribe_poison(Msg(event))
    d = call_doc(cid)
    print("poison ->", d["status"], d["error"])
    assert d["status"] == "failed" and "retries" in d["error"]
    with pytest.raises(ValueError):
        pipeline.parse_queue_message('{"foo": 1}')
