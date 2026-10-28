"""Speech to text.

Output (both implementations)::

    {"source": "...", "locale": "en-US", "duration_ms": 123000,
     "phrases": [{"speaker": "agent"|"customer", "offset_ms": 0, "duration_ms": 0,
                  "text": "...", "confidence": 0.93, "locale": "en-US"}]}

The language comes from the recording's metadata (``"locale": "hi-IN"``, or ``"auto"`` to let
Speech identify it among ``LANGUAGES``), falling back to ``SPEECH_LOCALE``.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

import requests

from .config import Settings

# Call languages the analysis supports end to end (Speech, PII, sentiment, LLM).
LANGUAGES = {"en-US": "English", "hi-IN": "Hindi"}
AUTO = "auto"


def requested_locales(metadata: dict, settings: Settings) -> list[str]:
    """Candidate locales for a recording. More than one = Speech language identification."""
    value = str(metadata.get("locale") or settings.speech_locale or "en-US").strip()
    if value.lower() == AUTO:
        return list(LANGUAGES)
    return [loc.strip() for loc in value.split(",") if loc.strip()]


def language_name(locale: str | None) -> str:
    return LANGUAGES.get(locale or "", locale or "")


def _main_locale(phrases: list[dict], fallback: str) -> str:
    """The locale spoken the longest."""
    totals: dict[str, int] = {}
    for p in phrases:
        loc = p.get("locale") or fallback
        totals[loc] = totals.get(loc, 0) + int(p.get("duration_ms", 0))
    return max(totals, key=totals.get) if totals else fallback


class SpeechClient(ABC):
    @abstractmethod
    def transcribe(self, audio: bytes, filename: str, metadata: dict) -> dict: ...


class MockSpeechClient(SpeechClient):
    """Returns a scripted conversation instead of real recognition.

    Uses ``metadata["mock_script"]`` when the sample generator put one there; for any
    other uploaded file it builds a plausible conversation from the sample scenarios,
    picked deterministically from the file name.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio, filename, metadata):
        from . import samples

        script = metadata.get("mock_script")
        if not script:
            script = samples.script_for_upload(filename, metadata)
        phrases = samples.time_script(script)
        duration = phrases[-1]["offset_ms"] + phrases[-1]["duration_ms"] + 800 if phrases else 0
        # The sample scripts are English, so "auto" detects English.
        locale = requested_locales(metadata, self.settings)[0]
        phrases = [{**p, "locale": locale} for p in phrases]
        return {"source": "mock", "locale": locale, "duration_ms": duration, "phrases": phrases}


class AzureFastTranscriptionClient(SpeechClient):
    """Azure AI Speech fast transcription REST API (synchronous, files up to 2 hours)."""

    def __init__(self, settings: Settings):
        if not settings.speech_endpoint:
            raise RuntimeError("Set SPEECH_ENDPOINT (e.g. https://<name>.cognitiveservices.azure.com)")
        self.settings = settings

    def _headers(self) -> dict:
        if self.settings.speech_key:
            return {"Ocp-Apim-Subscription-Key": self.settings.speech_key}
        from .azure_auth import bearer_token

        return {"Authorization": f"Bearer {bearer_token()}"}

    def transcribe(self, audio, filename, metadata):
        stereo = int(metadata.get("channels") or 1) == 2
        locales = requested_locales(metadata, self.settings)
        definition: dict = {"locales": locales, "profanityFilterMode": "None"}
        if stereo:
            definition["channels"] = [0, 1]
        else:
            definition["diarization"] = {"enabled": True, "maxSpeakers": 2}

        url = (
            f"{self.settings.speech_endpoint}/speechtotext/transcriptions:transcribe"
            f"?api-version={self.settings.speech_api_version}"
        )
        response = requests.post(
            url,
            headers=self._headers(),
            files={
                "audio": (filename, audio, "application/octet-stream"),
                "definition": (None, json.dumps(definition), "application/json"),
            },
            timeout=600,
        )
        if response.status_code >= 400:
            raise RuntimeError(api_error(response, filename, audio))
        body = response.json()
        phrases = self._map_speakers(body.get("phrases", []), stereo, metadata, locales[0])
        return {
            "source": "azure-fast-transcription",
            "locale": _main_locale(phrases, locales[0]),
            "duration_ms": body.get("durationMilliseconds", 0),
            "phrases": phrases,
        }

    def _map_speakers(self, phrases: list[dict], stereo: bool, metadata: dict, locale: str) -> list[dict]:
        phrases = sorted(phrases, key=lambda p: p.get("offsetMilliseconds", 0))
        if stereo:
            agent_channel = int(metadata.get("agent_channel", self.settings.agent_channel))
            is_agent = lambda p: p.get("channel", 0) == agent_channel  # noqa: E731
        else:
            # Diarization labels speakers 1..n. Assume whoever speaks first is the agent
            # (agents open the call) unless the telephony metadata says otherwise.
            first = phrases[0].get("speaker") if phrases else None
            first_is_agent = metadata.get("first_speaker", "agent") == "agent"
            is_agent = lambda p: (p.get("speaker") == first) == first_is_agent  # noqa: E731

        return [
            {
                "speaker": "agent" if is_agent(p) else "customer",
                "offset_ms": int(p.get("offsetMilliseconds", 0)),
                "duration_ms": int(p.get("durationMilliseconds", 0)),
                "text": p.get("text", "").strip(),
                "confidence": round(float(p.get("confidence", 0.0)), 3),
                "locale": p.get("locale") or locale,
            }
            for p in phrases
            if p.get("text", "").strip()
        ]


def api_error(response: requests.Response, filename: str, audio: bytes) -> str:
    """A readable message from an Azure Speech error response, naming the file's real format."""
    from . import audio as audio_format

    try:
        body = json.loads(response.text)
        error = (body.get("error") or body) if isinstance(body, dict) else {}
    except ValueError:
        error = {}
    code = error.get("code") or ""
    inner = (error.get("innererror") or {}).get("code") or ""
    message = error.get("message") or response.text[:500]
    detected = audio_format.detect_format(audio[:65536])
    if len(audio) < audio_format.MIN_AUDIO_BYTES:
        message += (f" The file is only {len(audio):,} bytes, too small to contain a recording: it is probably empty "
                    "or did not download/export completely.")
    return (f"Speech API error {response.status_code}{f' {code}' if code else ''}{f'/{inner}' if inner else ''}: "
            f"{message} (file {filename}, {len(audio):,} bytes, content looks like {audio_format.describe(detected)})")
