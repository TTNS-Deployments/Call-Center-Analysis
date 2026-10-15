"""Speech measurements derived from transcript timings.

Used as evidence for the audio-based QA parameters (rate of speech, hold protocol / dead air,
interruptions / overlapping), so the language model does not have to guess them from text.
"""

from __future__ import annotations

DEAD_AIR_SECONDS = 10.0


def speech_metrics(phrases: list[dict]) -> dict:
    phrases = sorted(phrases, key=lambda p: p["offset_ms"])
    words = {"agent": 0, "customer": 0}
    talk_ms = {"agent": 0, "customer": 0}
    for p in phrases:
        who = p["speaker"] if p["speaker"] in words else "customer"
        words[who] += len(p["text"].split())
        talk_ms[who] += p["duration_ms"]

    silences, overlaps, agent_interruptions = [], 0, 0
    for prev, nxt in zip(phrases, phrases[1:]):
        prev_end = prev["offset_ms"] + prev["duration_ms"]
        gap = (nxt["offset_ms"] - prev_end) / 1000
        if gap >= DEAD_AIR_SECONDS:
            silences.append(round(gap, 1))
        if nxt["offset_ms"] < prev_end and nxt["speaker"] != prev["speaker"]:
            overlaps += 1
            if nxt["speaker"] == "agent":
                agent_interruptions += 1

    def wpm(who: str) -> int | None:
        return round(words[who] / (talk_ms[who] / 60000)) if talk_ms[who] >= 5000 else None

    total_talk = talk_ms["agent"] + talk_ms["customer"]
    end = max((p["offset_ms"] + p["duration_ms"] for p in phrases), default=0)
    return {
        "duration_s": round(end / 1000),
        "agent_wpm": wpm("agent"),
        "customer_wpm": wpm("customer"),
        "agent_talk_pct": round(100 * talk_ms["agent"] / total_talk) if total_talk else None,
        "dead_air_count": len(silences),
        "longest_silence_s": max(silences, default=0.0),
        "overlaps": overlaps,
        "agent_interruptions": agent_interruptions,
    }


def describe(metrics: dict) -> str:
    """Plain-text block for the LLM prompt."""
    m = metrics or {}
    return (
        f"- Agent speaking rate: {m.get('agent_wpm') or 'unknown'} words per minute\n"
        f"- Agent share of talk time: {m.get('agent_talk_pct') if m.get('agent_talk_pct') is not None else 'unknown'}%\n"
        f"- Silences of {int(DEAD_AIR_SECONDS)} seconds or more (dead air): {m.get('dead_air_count', 0)}"
        f" (longest {m.get('longest_silence_s', 0)} s)\n"
        f"- Times the speakers talked over each other: {m.get('overlaps', 0)}"
        f" (agent started over the customer {m.get('agent_interruptions', 0)} times)"
    )
