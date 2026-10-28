import json

import pytest

from callcenter import audio, pipeline, samples, speech
from callcenter.config import Settings, get_settings
from callcenter.factory import get_blob_store, get_call_store
from callcenter.language import AzureLanguageClient

MP3_FRAME = b"\xff\xfb\x90\x64" + b"\x00" * 64


@pytest.mark.parametrize("data, expected", [
    (samples.make_wav(500), ".wav"),
    (MP3_FRAME, ".mp3"),
    (b"ID3\x04\x00\x00\x00\x00\x00\x0a" + b"\x00" * 10 + MP3_FRAME, ".mp3"),  # ID3 tag, then frames
    (b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 32, ".m4a"),
    (b"\xff\xf1\x50\x80" + b"\x00" * 32, ".aac"),  # ADTS
    (b"OggS" + b"\x00" * 32, ".ogg"),
    (b"#!AMR\n" + b"\x00" * 32, ".amr"),
    (b"<!DOCTYPE html><html>", None),  # a web page saved as .mp3
    (b"", None),
])
def test_detect_format(data, expected):
    assert audio.detect_format(data) == expected


def test_matches_treats_opus_as_ogg():
    assert audio.matches(".opus", ".ogg") and audio.matches(".mp3", ".mp3")
    assert not audio.matches(".mp3", ".m4a") and not audio.matches(".mp3", None)


def test_requested_locales():
    s = Settings()
    assert speech.requested_locales({}, s) == ["en-US"]
    assert speech.requested_locales({"locale": "hi-IN"}, s) == ["hi-IN"]
    assert speech.requested_locales({"locale": "auto"}, s) == ["en-US", "hi-IN"]
    assert speech.requested_locales({}, Settings(speech_locale="en-IN, hi-IN")) == ["en-IN", "hi-IN"]


class FakeResponse:
    def __init__(self, status, body):
        self.status_code, self._body, self.text = status, body, json.dumps(body)

    def json(self):
        return self._body


def azure_speech(monkeypatch, response):
    sent = {}

    def post(url, headers, files, timeout):
        sent["definition"] = json.loads(files["definition"][1])
        return response

    monkeypatch.setattr(speech.requests, "post", post)
    return speech.AzureFastTranscriptionClient(Settings(speech_endpoint="https://x", speech_key="k")), sent


def test_azure_speech_identifies_hindi(monkeypatch):
    body = {"durationMilliseconds": 4000, "phrases": [
        {"speaker": 1, "offsetMilliseconds": 0, "durationMilliseconds": 1000, "text": "Hello", "locale": "en-US"},
        {"speaker": 2, "offsetMilliseconds": 1000, "durationMilliseconds": 3000, "text": "मेरा बिल गलत है",
         "locale": "hi-IN"},
    ]}
    client, sent = azure_speech(monkeypatch, FakeResponse(200, body))
    result = client.transcribe(MP3_FRAME, "c.mp3", {"locale": "auto"})
    assert sent["definition"]["locales"] == ["en-US", "hi-IN"]
    assert result["locale"] == "hi-IN"  # spoken the longest
    assert [p["locale"] for p in result["phrases"]] == ["en-US", "hi-IN"]


def test_azure_speech_error_is_readable(monkeypatch):
    body = {"error": {"code": "InvalidRequest", "message": "Audio format is not supported.",
                      "innererror": {"code": "InvalidAudioFormat"}}}
    client, _ = azure_speech(monkeypatch, FakeResponse(422, body))
    with pytest.raises(RuntimeError) as err:
        client.transcribe(b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 32, "c.mp3", {})
    message = str(err.value)
    assert "422 InvalidRequest/InvalidAudioFormat: Audio format is not supported." in message
    assert "c.mp3" in message and "M4A" in message


def test_azure_language_sends_each_phrase_in_its_language():
    calls = []

    class Result:
        is_error = False
        entities = []
        sentiment = "neutral"

        class confidence_scores:  # noqa: N801 - mirrors the SDK attribute
            positive, neutral, negative = 0.1, 0.8, 0.1

    class FakeTextAnalytics:
        def recognize_pii_entities(self, documents):
            calls.append(documents)
            return [Result() for _ in documents]

        analyze_sentiment = recognize_pii_entities

    client = AzureLanguageClient.__new__(AzureLanguageClient)
    client.client, client.ignore = FakeTextAnalytics(), set()
    phrases = [{"speaker": "agent", "offset_ms": 0, "duration_ms": 1, "text": "Namaste", "locale": "hi-IN"},
               {"speaker": "customer", "offset_ms": 1, "duration_ms": 1, "text": "Hi"}]
    client.analyze(phrases)
    assert [[d["language"] for d in docs] for docs in calls] == [["hi", "en"], ["hi", "en"]]


def test_hindi_upload_is_recorded_on_the_call():
    s = get_settings()
    blobs = get_blob_store()
    call_id, meta, wav = samples.generate_calls(count=1, seed=5)[0]
    blobs.put_json(s.container_recordings, f"{call_id}.json", {**meta, "locale": "hi-IN"})
    blobs.put_bytes(s.container_recordings, f"{call_id}.wav", wav)
    pipeline.process_recording(f"{call_id}.wav")
    doc = get_call_store().get(call_id)
    assert doc["status"] == "completed" and doc["locale"] == "hi-IN"
