"""Shared QA fixtures.

Everything runs in local mode (mock Speech / Language / OpenAI, folder storage, SQLite) against a
data folder created for this run, so the developer's own .localdata is never touched.

    .venv/bin/python -m pytest qa/tests -v
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
WEBAPP = REPO / "Code" / "webapp"
DATA_DIR = Path(os.environ.get("QA_DATA_DIR") or tempfile.mkdtemp(prefix="qa-callcenter-"))
if not os.environ.get("QA_DATA_DIR") and not os.environ.get("QA_KEEP_DATA"):
    import atexit
    import shutil
    atexit.register(shutil.rmtree, DATA_DIR, True)   # set QA_KEEP_DATA=1 to inspect the data afterwards

# Environment for the in-process app: must be set before Django / callcenter settings load.
os.environ.update({
    "CC_MODE": "local",
    "AUTH_MODE": "dev",
    "CRM_API_KEYS": "local-test-key",
    "LOCAL_DATA_DIR": str(DATA_DIR),
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "DJANGO_ALLOWED_HOSTS": "testserver,localhost,127.0.0.1",
})
for name in ("STORAGE_BACKEND", "STORE_BACKEND", "SPEECH_BACKEND", "LANGUAGE_BACKEND", "LLM_BACKEND",
             "EVENTS_BACKEND", "DJANGO_DEBUG"):
    os.environ.pop(name, None)
sys.path.insert(0, str(WEBAPP))

import django  # noqa: E402

django.setup()

from callcenter import cli, pipeline, samples  # noqa: E402
from callcenter.config import get_settings, reset_settings_cache  # noqa: E402
from callcenter.factory import get_blob_store, get_call_store  # noqa: E402

reset_settings_cache()

SAMPLE_COUNT = 30
DEMO_IDS = ["DEMO-0001", "DEMO-0002", "DEMO-0003", "DEMO-0004"]


def _scenario_names() -> tuple[set[str], set[str]]:
    data = samples.load_scenarios()
    customers = {part for full in data["customers"] for part in full.split()}
    agents = {part for a in data["agents"] for part in a["name"].split()}
    return customers, agents


CUSTOMER_NAME_PARTS, AGENT_NAME_PARTS = _scenario_names()

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
DIGIT_RUN = re.compile(r"\d(?:[\d \-]{7,})\d")          # phone / card / account numbers as written
DOB = re.compile(r"\b\d{1,2} (?:January|March|May|July|September|November) (?:19|20)\d{2}\b")
ADDRESS = re.compile(r"Flat \d+, [^.?!]*\d{6}")


def raw_pii_from_script(script: list[dict]) -> dict[str, set[str]]:
    """PII values present in the raw (pre-redaction) sample script."""
    text = " ".join(line["text"] for line in script)
    words = set(re.findall(r"[A-Z][a-z]+", text))
    return {
        "customer_names": words & CUSTOMER_NAME_PARTS,
        "agent_names": words & AGENT_NAME_PARTS,
        "emails": set(EMAIL.findall(text)),
        "numbers": {m.strip() for m in DIGIT_RUN.findall(text) if len(re.sub(r"\D", "", m)) >= 10},
        "dates_of_birth": set(DOB.findall(text)),
        "addresses": set(ADDRESS.findall(text)),
    }


def leaks(text: str, pii: dict[str, set[str]], include_agent_names: bool = True) -> list[str]:
    found = []
    for kind, values in pii.items():
        if kind == "agent_names" and not include_agent_names:
            continue
        for v in values:
            pattern = rf"\b{re.escape(v)}\b" if kind.endswith("names") else re.escape(v)
            if re.search(pattern, text):
                found.append(f"{kind}:{v}")
    return found


@pytest.fixture(scope="session")
def seeded():
    """30 synthetic calls (like `make seed`) + the 4 demo calls (like `python -m callcenter upload`)."""
    s = get_settings()
    blobs = get_blob_store()
    calls = samples.generate_calls(count=SAMPLE_COUNT, days=20, seed=7)
    for call_id, meta, wav in calls:
        blobs.put_json(s.container_recordings, f"{call_id}.json", meta)
        blobs.put_bytes(s.container_recordings, f"{call_id}.wav", wav, "audio/wav")
        pipeline.process_recording(f"{call_id}.wav")
    assert cli.main(["upload"]) == 0
    return {"calls": calls, "data_dir": DATA_DIR}


@pytest.fixture
def client():
    from django.test import Client

    return Client(raise_request_exception=False)


def login(c, user_id: str):
    r = c.post("/auth/login", {"user": user_id})
    assert r.status_code == 302, f"login {user_id}: {r.status_code}"
    return c


@pytest.fixture
def as_role(client):
    def _as(user_id):
        return login(client, user_id)
    return _as


def call_doc(call_id: str) -> dict:
    return get_call_store().get(call_id)


def dumps(obj) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
