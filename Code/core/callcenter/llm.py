"""Call insights from the redacted transcript: summary, reason, root cause, resolution,
emotion, QA scorecard and compliance checks.

Output (both implementations, validated by ``normalize``)::

    {"summary", "call_reason", "call_reason_detail", "root_cause", "resolution",
     "customer_emotion": {"start", "end", "dominant"}, "repeat_contact": bool,
     "qa": [{"id", "score", "evidence", "rationale"}],
     "compliance": [{"id", "status": "pass|fail|not_applicable", "evidence"}],
     "topics": [...], "follow_up_actions": [...], "coaching_tips": [...]}
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from importlib import resources

from .config import Settings
from .rules import Rules

PROMPT_VERSION = "insights_v1"
EMOTIONS = ["calm", "satisfied", "happy", "neutral", "confused", "anxious", "frustrated", "angry", "disappointed"]
NEGATIVE_EMOTIONS = {"anxious", "frustrated", "angry", "disappointed", "confused"}
RESOLUTIONS = ["resolved", "follow_up", "escalated", "unresolved"]


def format_transcript(phrases: list[dict]) -> str:
    lines = []
    for p in phrases:
        secs = p["offset_ms"] // 1000
        lines.append(f"[{secs // 60:02d}:{secs % 60:02d}] {p['speaker'].upper()}: {p['text']}")
    return "\n".join(lines)


def build_system_prompt(rules: Rules) -> str:
    template = resources.files("callcenter.prompts").joinpath(f"{PROMPT_VERSION}.md").read_text(encoding="utf-8")
    reasons = "\n".join(f"- {c['id']}: {c['name']}. {c['description']}" for c in rules.categories)
    criteria = "\n".join(
        f"- {c['id']} ({c.get('type', 'objective')}, max_points={c['max_points']}): {c['name']}. "
        f"{' '.join(str(c['description']).split())}"
        for c in rules.criteria
    )
    checks = []
    for c in rules.checks:
        line = f"- {c['id']} ({c['kind']}): {c['name']}. {' '.join(str(c['description']).split())}"
        if c.get("applies_when"):
            line += f" Applies when: {c['applies_when']}"
        if c.get("example_phrases"):
            line += " Approved wording examples: " + "; ".join(f'"{e}"' for e in c["example_phrases"])
        checks.append(line)
    return (
        template.replace("{emotions}", ", ".join(EMOTIONS))
        .replace("{reasons}", reasons)
        .replace("{criteria}", criteria)
        .replace("{checks}", "\n".join(checks))
    )


def output_schema(rules: Rules) -> dict:
    def strict_object(props: dict) -> dict:
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    emotion = {"type": "string", "enum": EMOTIONS}
    text_list = {"type": "array", "items": {"type": "string"}}
    return strict_object({
        "summary": {"type": "string"},
        "call_reason": {"type": "string", "enum": [c["id"] for c in rules.categories]},
        "call_reason_detail": {"type": "string"},
        "root_cause": {"type": "string"},
        "resolution": {"type": "string", "enum": RESOLUTIONS},
        "customer_emotion": strict_object({"start": emotion, "end": emotion, "dominant": emotion}),
        "repeat_contact": {"type": "boolean"},
        "qa": {"type": "array", "items": strict_object({
            "id": {"type": "string", "enum": [c["id"] for c in rules.criteria]},
            "score": {"type": "integer"},
            "evidence": {"type": "string"},
            "rationale": {"type": "string"},
        })},
        "compliance": {"type": "array", "items": strict_object({
            "id": {"type": "string", "enum": [c["id"] for c in rules.checks]},
            "status": {"type": "string", "enum": ["pass", "fail", "not_applicable"]},
            "evidence": {"type": "string"},
        })},
        "topics": text_list,
        "follow_up_actions": text_list,
        "coaching_tips": text_list,
    })


def normalize(raw: dict, rules: Rules) -> dict:
    """Clamp and complete model output so downstream code can rely on its shape."""
    reason_ids = {c["id"] for c in rules.categories}
    qa_by_id = {q.get("id"): q for q in raw.get("qa", []) if isinstance(q, dict)}
    comp_by_id = {c.get("id"): c for c in raw.get("compliance", []) if isinstance(c, dict)}

    qa = []
    for c in rules.criteria:
        q = qa_by_id.get(c["id"], {})
        score = q.get("score", 0)
        try:
            score = int(round(float(score)))
        except (TypeError, ValueError):
            score = 0
        qa.append({
            "id": c["id"],
            "score": max(0, min(int(c["max_points"]), score)),
            "evidence": str(q.get("evidence", ""))[:400],
            "rationale": str(q.get("rationale", "Not evaluated by the model" if not q else ""))[:600],
        })

    compliance = []
    for c in rules.checks:
        item = comp_by_id.get(c["id"], {})
        status = item.get("status")
        if status not in ("pass", "fail", "not_applicable"):
            status = "fail" if c["kind"] == "required" and not c.get("applies_when") else "not_applicable"
        compliance.append({"id": c["id"], "status": status, "evidence": str(item.get("evidence", ""))[:400]})

    emotion = raw.get("customer_emotion") or {}
    fix = lambda e: e if e in EMOTIONS else "neutral"  # noqa: E731
    as_list = lambda v: [str(x) for x in v][:10] if isinstance(v, list) else []  # noqa: E731
    return {
        "summary": str(raw.get("summary", "")).strip(),
        "call_reason": raw.get("call_reason") if raw.get("call_reason") in reason_ids else rules.categories[-1]["id"],
        "call_reason_detail": str(raw.get("call_reason_detail", "")).strip(),
        "root_cause": str(raw.get("root_cause", "")).strip(),
        "resolution": raw.get("resolution") if raw.get("resolution") in RESOLUTIONS else "unresolved",
        "customer_emotion": {k: fix(emotion.get(k)) for k in ("start", "end", "dominant")},
        "repeat_contact": bool(raw.get("repeat_contact", False)),
        "qa": qa,
        "compliance": compliance,
        "topics": as_list(raw.get("topics")),
        "follow_up_actions": as_list(raw.get("follow_up_actions")),
        "coaching_tips": as_list(raw.get("coaching_tips")),
    }


class InsightsClient(ABC):
    source = "unknown"

    @abstractmethod
    def _generate(self, phrases: list[dict], metadata: dict, rules: Rules) -> dict: ...

    def generate(self, phrases: list[dict], metadata: dict, rules: Rules) -> dict:
        result = normalize(self._generate(phrases, metadata, rules), rules)
        result["source"] = self.source
        result["prompt_version"] = PROMPT_VERSION
        return result


# --------------------------------------------------------------------------- azure

class AzureOpenAIInsightsClient(InsightsClient):
    source = "azure-openai"

    def __init__(self, settings: Settings):
        from openai import AzureOpenAI

        if not settings.openai_endpoint:
            raise RuntimeError("Set AZURE_OPENAI_ENDPOINT")
        kwargs = {"azure_endpoint": settings.openai_endpoint, "api_version": settings.openai_api_version, "max_retries": 4}
        if settings.openai_key:
            kwargs["api_key"] = settings.openai_key
        else:
            from azure.identity import get_bearer_token_provider

            from .azure_auth import COGNITIVE_SCOPE, get_credential

            kwargs["azure_ad_token_provider"] = get_bearer_token_provider(get_credential(), COGNITIVE_SCOPE)
        self.client = AzureOpenAI(**kwargs)
        self.deployment = settings.openai_deployment
        self.reasoning_effort = settings.openai_reasoning_effort

    def _model_options(self) -> dict:
        if self.reasoning_effort and self.reasoning_effort != "none":
            return {"reasoning_effort": self.reasoning_effort}  # GPT-5 / o-series: no temperature
        return {"temperature": 0}

    def _generate(self, phrases, metadata, rules):
        user = (
            f"Call ID: {metadata.get('call_id', '')}\nDirection: {metadata.get('direction', 'inbound')}\n\n"
            f"Transcript:\n{format_transcript(phrases)}"
        )
        response = self.client.chat.completions.create(
            model=self.deployment,
            **self._model_options(),
            messages=[{"role": "system", "content": build_system_prompt(rules)}, {"role": "user", "content": user}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "call_insights", "strict": True, "schema": output_schema(rules)},
            },
        )
        content = response.choices[0].message.content or "{}"
        result = json.loads(content)
        usage = getattr(response, "usage", None)
        if usage:
            result["_usage"] = {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens}
        return result

    def generate(self, phrases, metadata, rules):
        raw = self._generate(phrases, metadata, rules)
        usage = raw.pop("_usage", None)
        result = normalize(raw, rules)
        result.update(source=self.source, prompt_version=PROMPT_VERSION, model=self.deployment)
        if usage:
            result["token_usage"] = usage
        return result


# --------------------------------------------------------------------------- mock

ROOT_CAUSES = [
    ("roaming add-on", "Incorrect add-on activated on the account"),
    ("local exchange", "Network fault at the local exchange"),
    ("bank gateway", "Payment gateway delay"),
    ("did not make", "Suspected fraudulent card transaction"),
    ("moved", "Customer relocation"),
    ("data runs out", "Current plan does not match usage"),
    ("never watch", "Plan cost does not match perceived value"),
    ("pending approval", "Refund approval backlog"),
]


class MockInsightsClient(InsightsClient):
    """Keyword heuristics that mimic the LLM output shape. Local mode only."""

    source = "mock"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    @staticmethod
    def _has(text: str, keywords) -> str | None:
        lower = f" {text.lower()} "
        for k in keywords or []:
            if k.lower() in lower:
                return k
        return None

    def _generate(self, phrases, metadata, rules):
        agent = [p for p in phrases if p["speaker"] == "agent"]
        customer = [p for p in phrases if p["speaker"] == "customer"]
        cust_text = " ".join(p["text"] for p in customer).lower()
        agent_text = " ".join(p["text"] for p in agent).lower()
        all_text = f"{agent_text} {cust_text}"

        # Reason: keyword hits in the first customer turns weigh most.
        opening = " ".join(p["text"] for p in customer[:2]).lower()
        best, best_score = rules.categories[-1]["id"], 0
        for cat in rules.categories:
            score = sum(3 * opening.count(k) + cust_text.count(k) for k in cat.get("mock_keywords", []))
            if score > best_score:
                best, best_score = cat["id"], score

        # Resolution
        closing_customer = [p for p in customer if len(p["text"].split()) > 4][-1:] or customer[-1:]
        happy_end = bool(closing_customer) and closing_customer[0].get("sentiment") in ("positive", "mixed")
        if "escalat" in agent_text:
            resolution = "escalated"
        elif any(k in agent_text for k in ("ticket number", "within 48", "will be resolved")):
            resolution = "follow_up"
        elif happy_end or any(k in agent_text for k in ("done,", "i have updated", "has been upgraded", "i have changed", "blocked the card", "i have raised a refund")):
            resolution = "resolved"
        else:
            resolution = "unresolved"

        # Emotion
        def emotion_of(p: dict | None) -> str:
            if not p:
                return "neutral"
            t = p["text"].lower()
            if any(k in t for k in ("ridiculous", "unacceptable", "ombudsman", "terrible")):
                return "angry"
            if any(k in t for k in ("worried", "did not make")):
                return "anxious"
            if "disappointed" in t or "don't believe" in t:
                return "disappointed"
            if p.get("sentiment") == "negative":
                return "frustrated"
            if p.get("sentiment") in ("positive", "mixed"):
                return "satisfied"
            return "neutral"

        start, end = emotion_of(customer[0] if customer else None), emotion_of(closing_customer[0] if closing_customer else None)
        emotions = [emotion_of(p) for p in customer]
        dominant = max(set(emotions), key=emotions.count) if emotions else "neutral"

        # QA
        qa = []
        for c in rules.criteria:
            window = c.get("mock_window", "any")
            pool = agent[:2] if window == "opening" else agent[-3:] if window == "closing" else agent
            hits = [p for p in pool if self._has(p["text"], c.get("mock_keywords"))]
            max_points = int(c["max_points"])
            if c.get("type") == "subjective":
                fraction = 1.0 if len(hits) >= 2 else 0.7 if hits else 0.3
            else:
                fraction = 1.0 if hits else 0.0
            qa.append({
                "id": c["id"],
                "score": round(max_points * fraction),
                "evidence": hits[0]["text"][:160] if hits else "",
                "rationale": "Expected behaviour observed." if hits else "Expected behaviour not found in the transcript.",
            })

        # Compliance
        compliance = []
        for c in rules.checks:
            applies = not c.get("mock_applies_keywords") or self._has(all_text, c["mock_applies_keywords"])
            hits = [p for p in agent if self._has(p["text"], c.get("mock_keywords"))]
            if not applies:
                status = "not_applicable"
            elif c["kind"] == "prohibited":
                status = "fail" if hits else "pass"
            else:
                status = "pass" if hits else "fail"
            evidence = hits[0]["text"][:160] if hits and status != "not_applicable" else ""
            compliance.append({"id": c["id"], "status": status, "evidence": evidence})

        issue = customer[0]["text"] if customer else ""
        reason_name = rules.category_name(best)
        outcome = {
            "resolved": "The agent resolved the issue on the call.",
            "follow_up": "The agent raised a ticket and gave a timeline for follow-up.",
            "escalated": "The agent escalated the issue for further action.",
            "unresolved": "The issue was not resolved on the call.",
        }[resolution]
        root = next((cause for key, cause in ROOT_CAUSES if key in all_text), "Not determined")
        failed_qa = [c["name"] for c, q in zip(rules.criteria, qa) if q["score"] < int(c["max_points"]) * 0.7]

        return {
            "summary": f"The customer called regarding {reason_name.lower()}: \"{issue[:180]}\" {outcome}",
            "call_reason": best,
            "call_reason_detail": issue[:200],
            "root_cause": root,
            "resolution": resolution,
            "customer_emotion": {"start": start, "end": end, "dominant": dominant},
            "repeat_contact": any(k in cust_text for k in ("third time", "called twice", "already called", "last time", "called before")),
            "qa": qa,
            "compliance": compliance,
            "topics": [reason_name, resolution.replace("_", " ")],
            "follow_up_actions": [] if resolution == "resolved" else ["Track the reference number until closure"],
            "coaching_tips": [f"Improve: {name}" for name in failed_qa],
        }
