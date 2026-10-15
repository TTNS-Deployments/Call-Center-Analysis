"""Synthesises the demo call recordings from scripts.yaml with Piper TTS (offline, open source).

Only needed to regenerate the audio; the WAV and JSON files are already in this folder.

    python -m venv .tts && .tts/bin/pip install piper-tts numpy pyyaml
    (cd /path/to/voices && .tts/bin/python -m piper.download_voices en_US-amy-medium en_US-ryan-medium en_GB-alan-medium)
    .tts/bin/python Data/demo_calls/generate_audio.py --voices /path/to/voices

Output per call: <id>.wav (16 kHz, 16-bit; stereo when channels: 2 with the agent on channel 0)
and <id>.json (metadata sidecar, uploaded before the audio).
"""

from __future__ import annotations

import argparse
import json
import wave
from pathlib import Path

import numpy as np
import yaml
from piper import PiperVoice

HERE = Path(__file__).resolve().parent
RATE = 16000
GAP_S = 0.45          # silence between turns
LEAD_S = 0.6          # silence at the start


def synth(voice: PiperVoice, text: str) -> np.ndarray:
    chunks, src_rate = [], None
    for chunk in voice.synthesize(text):
        src_rate = chunk.sample_rate
        chunks.append(np.frombuffer(chunk.audio_int16_bytes, dtype=np.int16))
    audio = np.concatenate(chunks).astype(np.float32)
    if src_rate != RATE:  # linear resample to 16 kHz
        n = int(len(audio) * RATE / src_rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    return audio


def build_call(call: dict, voices_dir: Path) -> tuple[np.ndarray, int]:
    voices = {role: PiperVoice.load(str(voices_dir / f"{name}.onnx")) for role, name in call["voices"].items()}
    stereo = int(call.get("channels", 1)) == 2
    tracks = {"agent": [], "customer": []}
    pos = int(LEAD_S * RATE)
    segments = []
    for speaker, text in call["lines"]:
        audio = synth(voices[speaker], text)
        segments.append((speaker, pos, audio))
        pos += len(audio) + int(GAP_S * RATE)
    total = pos + int(0.5 * RATE)
    out = np.zeros((total, 2 if stereo else 1), dtype=np.float32)
    for speaker, start, audio in segments:
        channel = (0 if speaker == "agent" else 1) if stereo else 0
        out[start:start + len(audio), channel] += audio
    peak = np.abs(out).max() or 1.0
    out = np.clip(out * min(1.0, 29000 / peak), -32768, 32767).astype(np.int16)
    return out, out.shape[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--voices", type=Path, required=True, help="folder with the downloaded .onnx voice files")
    args = parser.parse_args()

    calls = yaml.safe_load((HERE / "scripts.yaml").read_text(encoding="utf-8"))["calls"]
    for call in calls:
        audio, channels = build_call(call, args.voices)
        with wave.open(str(HERE / f"{call['id']}.wav"), "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(audio.tobytes())
        meta = {**call["metadata"], "channels": channels}
        if channels == 2:
            meta["agent_channel"] = 0
        (HERE / f"{call['id']}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        print(f"{call['id']}: {len(audio) / RATE:.1f}s, {channels} channel(s)")


if __name__ == "__main__":
    main()
