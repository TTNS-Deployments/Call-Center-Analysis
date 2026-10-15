"""Synthetic sample calls for local mode (see Data/sample_calls/scenarios.yaml)."""

from __future__ import annotations

import hashlib
import io
import random
import wave
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import yaml

from .config import get_settings

WORDS_PER_SECOND = 2.7


@lru_cache(maxsize=1)
def load_scenarios() -> dict:
    data_dir = get_settings().data_dir
    path = data_dir / "sample_calls" / "scenarios.yaml" if data_dir else None
    if not path or not path.is_file():
        raise FileNotFoundError("Data/sample_calls/scenarios.yaml not found (set CC_SAMPLE_DATA_DIR)")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _digits(rng: random.Random, n: int) -> str:
    return "".join(str(rng.randint(0, 9)) for _ in range(n))


def random_context(rng: random.Random, agent: dict, customer: str, company: str) -> dict:
    card = f"4{_digits(rng, 3)} {_digits(rng, 4)} {_digits(rng, 4)} {_digits(rng, 4)}"
    first, last = customer.split(" ", 1)
    streets = ["Lake View Apartments, Koramangala, Bengaluru 560034", "Green Park Society, Baner, Pune 411045",
               "Sea Breeze Towers, Andheri West, Mumbai 400053", "Palm Grove, Gachibowli, Hyderabad 500032"]
    months = ["January", "March", "May", "July", "September", "November"]
    return {
        "company": company,
        "agent": agent["name"].split(" ")[0],
        "customer": customer,
        "phone": f"9{_digits(rng, 4)} {_digits(rng, 5)}",
        "card": card,
        "card4": card[-4:],
        "email": f"{first.lower()}.{last.lower()}{rng.randint(10, 99)}@example.com",
        "account": f"{_digits(rng, 4)} {_digits(rng, 4)} {_digits(rng, 4)}",
        "ref": f"CRN{_digits(rng, 7)}",
        "dob": f"{rng.randint(1, 28)} {rng.choice(months)} {rng.randint(1965, 2001)}",
        "amount": str(rng.choice([249, 499, 750, 1200, 1850, 2400])),
        "address": f"Flat {rng.randint(101, 1204)}, {rng.choice(streets)}",
    }


def build_script(scenario: dict, ctx: dict, quality: float, rng: random.Random) -> list[dict]:
    """Render a scenario. Lower quality drops optional lines and may add a compliance breach."""
    lines = []
    allow_bad = quality < 0.62 and rng.random() < 0.45
    for line in scenario["lines"]:
        speaker, text = line[0], line[1]
        tag = line[2] if len(line) > 2 else ""
        if tag.startswith("opt:") and rng.random() > min(0.97, quality + 0.08):
            continue
        if tag.startswith("bad:") and not allow_bad:
            continue
        lines.append({"speaker": speaker, "text": text.format(**ctx)})
    return lines


def time_script(script: list[dict]) -> list[dict]:
    """Assign realistic offsets/durations to scripted lines (deterministic)."""
    phrases, offset = [], 600
    for i, line in enumerate(script):
        words = max(1, len(line["text"].split()))
        duration = int(max(1100, words / WORDS_PER_SECOND * 1000))
        phrases.append({
            "speaker": line["speaker"],
            "offset_ms": offset,
            "duration_ms": duration,
            "text": line["text"],
            "confidence": round(0.88 + (hash_int(line["text"]) % 10) / 100, 3),
        })
        offset += duration + 350 + (hash_int(f"{i}{line['text']}") % 900)
    return phrases


def hash_int(text: str) -> int:
    return int(hashlib.sha1(text.encode()).hexdigest()[:8], 16)


def make_wav(duration_ms: int, phrases: list[dict] | None = None) -> bytes:
    """Placeholder audio (8 kHz, 8-bit mono) of the right length so the UI player and seeking work.

    Speech segments get a faint tone (lower for the agent, higher for the customer) so you can
    hear turn-taking; silence elsewhere.
    """
    import math

    rate = 8000
    total = int(rate * duration_ms / 1000)
    frames = bytearray([128]) * total
    tones = {
        f: bytes(128 + int(6 * math.sin(2 * math.pi * f * i / rate)) for i in range(rate)) for f in (180, 260)
    }
    for p in phrases or []:
        tone = tones[180 if p["speaker"] == "agent" else 260]
        start = int(rate * p["offset_ms"] / 1000)
        end = min(total, start + int(rate * p["duration_ms"] / 1000))
        length = end - start
        if length > 0:
            frames[start:end] = (tone * (length // rate + 1))[:length]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(1)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _pick_scenario(data: dict, rng: random.Random) -> dict:
    scenarios = data["scenarios"]
    return rng.choices(scenarios, weights=[s.get("weight", 1) for s in scenarios])[0]


def demo_script(call_id: str) -> list[dict] | None:
    """Lines of a scripted demo call (Data/demo_calls/scripts.yaml), if call_id is one of them."""
    data_dir = get_settings().data_dir
    path = data_dir / "demo_calls" / "scripts.yaml" if data_dir else None
    if not path or not path.is_file():
        return None
    for call in yaml.safe_load(path.read_text(encoding="utf-8")).get("calls", []):
        if call["id"] == call_id:
            return [{"speaker": speaker, "text": text} for speaker, text in call["lines"]]
    return None


def script_for_upload(filename: str, metadata: dict) -> list[dict]:
    """Script for a file uploaded without one (mock mode), deterministic per file name."""
    demo = demo_script(filename.rsplit(".", 1)[0])
    if demo:
        return demo
    data = load_scenarios()
    rng = random.Random(hash_int(filename))
    agent = {"name": metadata.get("agent_name") or rng.choice(data["agents"])["name"]}
    ctx = random_context(rng, agent, rng.choice(data["customers"]), data["company"])
    return build_script(_pick_scenario(data, rng), ctx, quality=rng.uniform(0.5, 1.0), rng=rng)


def generate_calls(count: int = 40, days: int = 30, seed: int = 7) -> list[tuple[str, dict, bytes]]:
    """Return [(call_id, metadata, wav_bytes)] spread over the last ``days`` days."""
    data = load_scenarios()
    rng = random.Random(seed)
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    calls = []
    for i in range(count):
        agent = rng.choice(data["agents"])
        customer = rng.choice(data["customers"])
        scenario = _pick_scenario(data, rng)
        quality = max(0.3, min(1.0, rng.gauss(agent.get("skill", 0.8), 0.12)))
        ctx = random_context(rng, agent, customer, data["company"])
        script = build_script(scenario, ctx, quality, rng)
        phrases = time_script(script)
        duration = phrases[-1]["offset_ms"] + phrases[-1]["duration_ms"] + 800

        started = now - timedelta(days=rng.uniform(0, days))
        started = started.replace(hour=rng.randint(9, 19), minute=rng.randint(0, 59))
        call_id = f"CALL-{started:%Y%m%d}-{i + 1:04d}"
        metadata = {
            "call_id": call_id,
            "agent_id": agent["id"],
            "agent_name": agent["name"],
            "agent_email": agent["email"],
            "team": agent["team"],
            "customer_id": f"CUST-{hash_int(customer) % 100000:05d}",
            "direction": "inbound",
            "started_at": started.isoformat(timespec="seconds"),
            "channels": 1,
            "scenario": scenario["id"],
            "mock_script": script,
        }
        calls.append((call_id, metadata, make_wav(duration, phrases)))
    return calls
