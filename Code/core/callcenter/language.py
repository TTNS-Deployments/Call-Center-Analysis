"""PII detection/redaction and sentiment, per transcript phrase.

Runs BEFORE the LLM so Azure OpenAI only ever sees redacted text.

Output (both implementations)::

    {"source": "...",
     "phrases": [{...timing..., "speaker", "text": "<redacted>", "pii": [{"category", "offset", "length"}],
                  "sentiment": "positive|neutral|negative|mixed",
                  "scores": {"positive": .., "neutral": .., "negative": ..}}],
     "pii_summary": {"total": 3, "categories": {"PhoneNumber": 1, ...}}}
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections import Counter

from .config import Settings

LABELS = {
    "Person": "NAME",
    "PhoneNumber": "PHONE",
    "Email": "EMAIL",
    "CreditCardNumber": "CARD",
    "Address": "ADDRESS",
    "DateTime": "DATE",
    "BankAccountNumber": "ACCOUNT",
    "USBankAccountNumber": "ACCOUNT",
    "InternationalBankingAccountNumber": "ACCOUNT",
    "INPermanentAccount": "PAN",
    "INUniqueIdentificationNumber": "AADHAAR",
    "IPAddress": "IP",
    "Credential": "CREDENTIAL",
}

YEAR = re.compile(r"\b(19|20)\d{2}\b")


def placeholder(category: str) -> str:
    return f"[{LABELS.get(category, category.upper())}]"


def coalesce(text: str, entities: list[dict]) -> list[dict]:
    """Join same-category entities separated only by whitespace ("[NAME] [NAME]" -> "[NAME]")."""
    merged: list[dict] = []
    for e in sorted(entities, key=lambda e: e["offset"]):
        if merged:
            last = merged[-1]
            end = last["offset"] + last["length"]
            if last["category"] == e["category"] and e["offset"] >= end and not text[end:e["offset"]].strip():
                last["length"] = e["offset"] + e["length"] - last["offset"]
                continue
        merged.append(dict(e))
    return merged


def redact(text: str, entities: list[dict]) -> str:
    """Replace entity spans (python string offsets) with placeholders."""
    out = text
    for e in sorted(entities, key=lambda e: e["offset"], reverse=True):
        out = out[: e["offset"]] + placeholder(e["category"]) + out[e["offset"] + e["length"]:]
    return out


def _merge_overlaps(entities: list[dict]) -> list[dict]:
    result: list[dict] = []
    for e in sorted(entities, key=lambda e: (e["offset"], -e["length"])):
        if result and e["offset"] < result[-1]["offset"] + result[-1]["length"]:
            continue
        result.append(e)
    return result


def summarize(phrases: list[dict]) -> dict:
    counts = Counter(e["category"] for p in phrases for e in p.get("pii", []))
    return {"total": sum(counts.values()), "categories": dict(counts)}


class LanguageClient(ABC):
    @abstractmethod
    def analyze(self, phrases: list[dict]) -> dict: ...


# --------------------------------------------------------------------------- mock

_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("CreditCardNumber", re.compile(r"\b(?:\d{4}[\s-]){3}\d{4}\b|\b\d{15,16}\b")),
    ("BankAccountNumber", re.compile(r"\b\d{4}[\s-]\d{4}[\s-]\d{4}\b|\b\d{11,14}\b")),
    ("PhoneNumber", re.compile(r"(?:\+91[\s-]?)?\b[6-9]\d{4}[\s-]?\d{5}\b")),
    ("INPermanentAccount", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("DateTime", re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTHS})\s+(?:19|20)\d{{2}}\b")),
    ("Address", re.compile(r"\b(?:Flat|House|Plot|Apt\.?)\s+\d+[^.]*?\b\d{6}\b")),
]
SPOKEN_DIGITS = re.compile(r"\b\d(?:[ ,\-]{1,3}\d){9,18}\b")
NAME_INTRO = re.compile(
    r"(?i:my name is|this is|I'm|I am|name is)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)|\b([A-Z][a-z]+) speaking\b"
    r"|\b(?:Mister|Mr\.?|Mrs\.?|Ms\.?|Miss|Madam|Dr\.?)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)"
)
# Spoken forms that speech-to-text produces: "rohan dot das at example dot com",
# "the twelfth of March, nineteen ninety".
SPOKEN_EMAIL = re.compile(r"(?i)\b[a-z0-9]+(?:\s(?:dot|underscore)\s[a-z0-9]+)*\sat\s[a-z0-9-]+(?:\sdot\s[a-z]{2,})+\b")
_ORDINALS = ("first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|eleventh|twelfth|thirteenth|"
             "fourteenth|fifteenth|sixteenth|seventeenth|eighteenth|nineteenth|twentieth|thirtieth|"
             r"twenty[\s-](?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth)|thirty[\s-]first|\d{1,2}(?:st|nd|rd|th)?")
_NUMBER_WORDS = ("one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
                 "seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|and|oh")
SPOKEN_DATE = re.compile(
    rf"(?i)\b(?:the\s+)?(?:{_ORDINALS})\s+(?:of\s+)?(?:{_MONTHS}),?\s+"
    rf"(?:(?:19|20)\d{{2}}|(?:nineteen|twenty|two\sthousand)(?:[\s-](?:{_NUMBER_WORDS}))*)\b"
)
# Card CVV / one-time passwords / PINs: short digit runs said right after the code is named or asked for.
CREDENTIAL_WORDS = re.compile(r"(?i)\b(?:c\s?v\s?v|o\s?t\s?p|p\s?i\s?n|one[\s-]time password|security code)\b")
SHORT_DIGITS = re.compile(r"\b\d(?:[ ,\-]{1,3}\d){2,8}\b|\b\d{3,8}\b")
NOT_NAMES = {"Northwind", "Services", "Thank", "Okay", "Sure", "Hello", "Hi", "Yes", "No"}

POSITIVE = ["thank", "great", "good", "excellent", "relief", "happy", "helpful", "easy", "appreciate",
            "perfect", "wonderful", "pleasure", "glad", "works", "sounds good", "welcome"]
NEGATIVE = ["terrible", "unacceptable", "ridiculous", "frustrat", "worried", "annoying", "disappointed",
            "angry", "not working", "failed", "losing", "complaint", "cancel", "no idea", "overcharged",
            "don't believe", "nobody", "nothing has happened", "too expensive", "never", "ombudsman",
            "unable", "extra", "did not make", "fault", "inconvenience"]


class MockLanguageClient(LanguageClient):
    """Regex PII detection + lexicon sentiment. Good enough to exercise the pipeline offline."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.known_names = self._known_names()

    @staticmethod
    def _known_names() -> set[str]:
        try:
            from .samples import load_scenarios

            data = load_scenarios()
        except Exception:
            return set()
        names = set()
        for full in [a["name"] for a in data.get("agents", [])] + data.get("customers", []):
            names.update(full.split(" "))
        return names

    def _pii(self, text: str, credential_context: bool = False) -> list[dict]:
        found = []
        for category, pattern in PII_PATTERNS + [("Email", SPOKEN_EMAIL), ("DateTime", SPOKEN_DATE)]:
            for m in pattern.finditer(text):
                found.append({"category": category, "offset": m.start(), "length": m.end() - m.start()})
        if credential_context or CREDENTIAL_WORDS.search(text):
            for m in SHORT_DIGITS.finditer(text):
                found.append({"category": "Credential", "offset": m.start(), "length": m.end() - m.start()})
        for m in SPOKEN_DIGITS.finditer(text):  # digits read out one by one: "4 1 1 1, 2 2 2 2, ..."
            digits = re.sub(r"\D", "", m.group(0))
            category = ("CreditCardNumber" if len(digits) in (15, 16) else
                        "PhoneNumber" if len(digits) == 10 and digits[0] in "6789" else "BankAccountNumber")
            found.append({"category": category, "offset": m.start(), "length": m.end() - m.start()})
        for m in NAME_INTRO.finditer(text):
            group = next(g for g in (1, 2, 3) if m.group(g))
            name = m.group(group)
            if name.split(" ")[0] not in NOT_NAMES:
                found.append({"category": "Person", "offset": m.start(group), "length": len(name)})
        for name in self.known_names:
            for m in re.finditer(rf"\b{re.escape(name)}\b", text):
                found.append({"category": "Person", "offset": m.start(), "length": len(name)})
        return coalesce(text, _merge_overlaps(found))

    @staticmethod
    def _sentiment(text: str) -> tuple[str, dict]:
        lower = text.lower()
        pos = sum(lower.count(w) for w in POSITIVE)
        neg = sum(lower.count(w) for w in NEGATIVE)
        if pos and neg:
            label = "mixed"
        elif pos:
            label = "positive"
        elif neg:
            label = "negative"
        else:
            label = "neutral"
        total = pos + neg + 1
        scores = {"positive": round(pos / total, 2), "negative": round(neg / total, 2)}
        scores["neutral"] = round(max(0.0, 1 - scores["positive"] - scores["negative"]), 2)
        return label, scores

    def analyze(self, phrases):
        out = []
        asked_for_credential = False
        for p in phrases:
            # "Read me the OTP" -> the customer's next reply "It says 5 8 2 9 1 4" is a credential.
            entities = self._pii(p["text"], credential_context=asked_for_credential and p["speaker"] == "customer")
            if p["speaker"] == "agent":
                asked_for_credential = bool(CREDENTIAL_WORDS.search(p["text"]))
            label, scores = self._sentiment(p["text"])
            out.append({**p, "text": redact(p["text"], entities), "pii": entities,
                        "sentiment": label, "scores": scores})
        return {"source": "mock", "phrases": out, "pii_summary": summarize(out)}


# --------------------------------------------------------------------------- azure

class AzureLanguageClient(LanguageClient):
    """Azure AI Language: PII entity recognition + sentiment analysis (text API)."""

    PII_BATCH = 5
    SENTIMENT_BATCH = 10

    def __init__(self, settings: Settings):
        from azure.ai.textanalytics import TextAnalyticsClient

        if not settings.language_endpoint:
            raise RuntimeError("Set LANGUAGE_ENDPOINT (e.g. https://<name>.cognitiveservices.azure.com)")
        if settings.language_key:
            from azure.core.credentials import AzureKeyCredential

            credential = AzureKeyCredential(settings.language_key)
        else:
            from .azure_auth import get_credential

            credential = get_credential()
        self.client = TextAnalyticsClient(settings.language_endpoint, credential)
        self.ignore = set(settings.pii_ignore_categories)

    @staticmethod
    def _batches(items, size):
        for i in range(0, len(items), size):
            yield i, items[i: i + size]

    def _keep(self, entity) -> bool:
        if entity.category == "DateTime":
            # Durations like "three days" are useful context; full dates (e.g. date of birth) are PII.
            return bool(YEAR.search(entity.text))
        return entity.category not in self.ignore and entity.confidence_score >= 0.5

    def analyze(self, phrases):
        texts = [p["text"] for p in phrases]
        entities: list[list[dict]] = [[] for _ in texts]
        for start, batch in self._batches(texts, self.PII_BATCH):
            results = self.client.recognize_pii_entities(batch, language="en")
            for j, res in enumerate(results):
                if res.is_error:
                    raise RuntimeError(f"PII recognition failed: {res.error}")
                entities[start + j] = coalesce(batch[j], _merge_overlaps([
                    {"category": e.category, "offset": e.offset, "length": e.length}
                    for e in res.entities if self._keep(e)
                ]))

        redacted = [redact(t, e) for t, e in zip(texts, entities)]
        sentiments: list[tuple[str, dict]] = [("neutral", {})] * len(texts)
        for start, batch in self._batches(redacted, self.SENTIMENT_BATCH):
            results = self.client.analyze_sentiment(batch, language="en")
            for j, res in enumerate(results):
                if res.is_error:
                    raise RuntimeError(f"Sentiment analysis failed: {res.error}")
                cs = res.confidence_scores
                sentiments[start + j] = (res.sentiment, {
                    "positive": round(cs.positive, 2), "neutral": round(cs.neutral, 2), "negative": round(cs.negative, 2),
                })

        out = [
            {**p, "text": redacted[i], "pii": entities[i], "sentiment": sentiments[i][0], "scores": sentiments[i][1]}
            for i, p in enumerate(phrases)
        ]
        return {"source": "azure-language", "phrases": out, "pii_summary": summarize(out)}
