"""Backend: configuration, CRM API / event feed, code-level security (test IDs B-28 .. B-45)."""
from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest

from callcenter import events, pipeline, rules
from callcenter.config import get_settings
from callcenter.factory import get_blob_store

from conftest import DEMO_IDS, REPO, WEBAPP, call_doc, dumps, leaks, login, raw_pii_from_script

KEY = {"HTTP_X_API_KEY": "local-test-key"}


def run_py(code: str, env_overrides: dict, cwd=WEBAPP) -> subprocess.CompletedProcess:
    """Run a snippet in a fresh interpreter with a clean local-mode environment plus overrides."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CC_", "AUTH_", "DJANGO_", "STORAGE_", "STORE_", "SPEECH_", "LANGUAGE_", "LLM_",
                                "AGENT_", "MAX_UPLOAD"))}
    env.update({"PYTHONPATH": str(WEBAPP), "DJANGO_SETTINGS_MODULE": "config.settings"})
    env.update(env_overrides)
    return subprocess.run([sys.executable, "-c", code], cwd=str(cwd), env=env, capture_output=True, text=True,
                          timeout=120)


def last_line(r):
    lines = (r.stderr or r.stdout).strip().splitlines()
    return lines[-1] if lines else ""


# --------------------------------------------------------------------------- config

def test_B28_local_defaults_and_django_check(tmp_path):
    r = run_py("from callcenter.config import get_settings as g; s=g(); "
               "print(s.mode, s.storage_backend, s.store_backend, s.speech_backend, s.language_backend, s.llm_backend, s.events_backend)",
               {"LOCAL_DATA_DIR": str(tmp_path)})
    print(r.stdout.strip())
    assert r.stdout.split() == ["local", "local", "local", "mock", "mock", "mock", "none"]
    r = subprocess.run([sys.executable, "manage.py", "check"], cwd=WEBAPP, capture_output=True, text=True,
                       env={**{k: v for k, v in os.environ.items() if not k.startswith(("CC_", "AUTH_"))},
                            "LOCAL_DATA_DIR": str(tmp_path)})
    print(r.stdout.strip(), r.returncode)
    assert r.returncode == 0 and "no issues" in r.stdout


STARTUP = "import django; django.setup(); print('started')"


@pytest.mark.parametrize("env, expect", [
    ({"AUTH_MODE": "bogus"}, "Unknown AUTH_MODE"),
    ({"CC_MODE": "azure", "AUTH_MODE": "dev", "DJANGO_SECRET_KEY": "x"}, "AUTH_MODE=dev is only allowed"),
    ({"CC_MODE": "azure", "AUTH_MODE": "entra"}, "DJANGO_SECRET_KEY must be set"),
], ids=["auth-mode-bogus", "dev-auth-in-azure", "no-secret-in-azure"])
def test_B29_invalid_webapp_config_clear_error(env, expect):
    r = run_py(STARTUP, env)
    print(env, "->", r.returncode, last_line(r))
    assert r.returncode != 0 and expect in last_line(r)


@pytest.mark.parametrize("env, expect", [
    ({"STORAGE_BACKEND": "foo"}, "Unknown storage backend 'foo'"),
    ({"STORE_BACKEND": "foo"}, "Unknown store backend 'foo'"),
    ({"LLM_BACKEND": "foo"}, "Unknown llm backend 'foo'"),
    ({"LLM_BACKEND": "azure"}, "Set AZURE_OPENAI_ENDPOINT"),
    ({"SPEECH_BACKEND": "azure"}, "Set SPEECH_ENDPOINT"),
], ids=["storage", "store", "llm", "llm-azure-no-endpoint", "speech-azure-no-endpoint"])
def test_B30_invalid_pipeline_config_clear_error(env, expect, tmp_path):
    code = ("from callcenter import factory as f; f.get_blob_store(); f.get_call_store(); f.get_speech_client(); "
            "f.get_language_client(); f.get_insights_client(); print('ok')")
    r = run_py(code, {**env, "LOCAL_DATA_DIR": str(tmp_path)})
    print(env, "->", r.returncode, last_line(r))
    assert r.returncode != 0 and expect in last_line(r)


@pytest.mark.parametrize("env, code", [
    ({"AGENT_CHANNEL": "left"}, "from callcenter.config import get_settings; get_settings(); print('ok')"),
    ({"MAX_UPLOAD_MB": "lots"}, STARTUP),
], ids=["AGENT_CHANNEL", "MAX_UPLOAD_MB"])
def test_B31_non_numeric_setting_error_names_variable(env, code, tmp_path):
    r = run_py(code, {**env, "LOCAL_DATA_DIR": str(tmp_path)})
    name = next(iter(env))
    print(env, "->", r.returncode, last_line(r))
    assert r.returncode != 0 and name in (r.stderr + r.stdout).split("Traceback")[-1].splitlines()[-1]


def test_B32_unknown_cc_mode_rejected(tmp_path):
    r = run_py("from callcenter.config import get_settings; s=get_settings(); print('mode', s.mode, s.storage_backend, s.llm_backend)",
               {"CC_MODE": "prodution", "LOCAL_DATA_DIR": str(tmp_path)})
    print("CC_MODE=prodution ->", r.returncode, (r.stdout or last_line(r)).strip())
    assert r.returncode != 0, "a misspelt CC_MODE is silently treated as local mode"


def test_B33_invalid_rules_override_marks_call_failed(seeded):
    s = get_settings()
    blobs = get_blob_store()
    try:
        blobs.put_bytes(s.container_config, rules.RUBRIC_FILE, b"criteria: []\n")   # bypasses the Settings page validation
        from callcenter import samples
        blobs.put_bytes(s.container_recordings, "QA-BADRULES-0001.wav", samples.make_wav(1000))
        with pytest.raises(rules.ConfigError):
            pipeline.process_recording("QA-BADRULES-0001.wav")
        d = call_doc("QA-BADRULES-0001")
        print(d["status"], d["stage"], d["error"])
        assert d["status"] == "failed" and "criteria" in d["error"]
    finally:
        os.remove(s.local_data_dir / "storage" / s.container_config / rules.RUBRIC_FILE)
    assert pipeline.retry("QA-BADRULES-0001")["status"] == "completed"


@pytest.mark.parametrize("name, text, message", [
    (rules.COMPLIANCE_FILE, "checks:\n  - {id: a, name: A, kind: maybe, description: x}", "required or prohibited"),
    (rules.REASONS_FILE, "categories:\n  - {id: a, name: A}", "missing: description"),
    (rules.RUBRIC_FILE, "- just\n- a list", "mapping"),
])
def test_B34_rules_validation_messages(name, text, message):
    with pytest.raises(rules.ConfigError, match=message):
        rules.save_config_text(name, text)


# --------------------------------------------------------------------------- CRM API

SUMMARY_FIELDS = {"call_id", "status", "agent_id", "started_at", "summary", "call_reason", "root_cause", "resolution",
                  "sentiment", "emotion", "qa", "compliance", "flag_list"}


def test_B35_api_valid_key_list_and_detail(seeded, client):
    r = client.get("/api/calls?range=all", **KEY)
    print(r.status_code, r["Content-Type"], "count", r.json()["count"])
    assert r.status_code == 200 and r["Content-Type"].startswith("application/json")
    body = r.json()
    assert body["count"] >= 34 and SUMMARY_FIELDS <= set(body["items"][0])
    assert "transcript" not in body["items"][0] and "history" not in body["items"][0]
    d = client.get("/api/calls/DEMO-0001", **KEY)
    assert d.status_code == 200
    doc = d.json()
    assert SUMMARY_FIELDS <= set(doc) and doc["transcript"] and "history" not in doc


def test_B36_api_filters(seeded, client):
    r = client.get("/api/calls?range=all&agent=A1004&limit=2", **KEY).json()
    assert r["items"] and all(c["agent_id"] == "A1004" for c in r["items"]) and len(r["items"]) <= 2
    r = client.get("/api/calls?range=all&customer=CUST-20457", **KEY).json()
    assert [c["call_id"] for c in r["items"]] == ["DEMO-0002"]
    r = client.get("/api/calls?range=all&flag=compliance_breach", **KEY).json()
    assert all("compliance_breach" in c["flag_list"] for c in r["items"]) and r["count"] >= 1
    print("filters OK; compliance_breach count", r["count"])


@pytest.mark.parametrize("headers", [{}, {"HTTP_X_API_KEY": "wrong-key"}, {"HTTP_X_API_KEY": ""},
                                     {"HTTP_AUTHORIZATION": "Bearer local-test-key"}],
                         ids=["missing", "wrong", "empty", "bearer-instead-of-header"])
def test_B37_api_rejects_missing_or_wrong_key(seeded, client, headers):
    for url in ("/api/calls", "/api/calls/DEMO-0001", "/api/calls?api_key=local-test-key"):
        r = client.get(url, **headers)
        assert r.status_code in (401, 403) and r.json() == {"error": "unauthorized"}, (url, r.status_code)
    print(headers, "-> 401")


def test_B38_api_unknown_call_404(seeded, client):
    r = client.get("/api/calls/NO-SUCH-CALL", **KEY)
    print(r.status_code, r.content[:80])
    assert r.status_code == 404 and r.json() == {"error": "not found"}


@pytest.mark.parametrize("query", ["limit=abc", "limit=-5", "limit=1e3", "from=not-a-date", "from=2026-13-45&to=x",
                                   "range=999", "status=%00", "agent=' OR 1=1 --"])
def test_B39_api_malformed_input_clean_4xx_or_ignored(seeded, client, query):
    r = client.get(f"/api/calls?{query}", **KEY)
    ctype = r.get("Content-Type", "")
    print(query, "->", r.status_code, ctype, r.content[:120])
    assert r.status_code < 500, f"server error for ?{query}"
    assert ctype.startswith("application/json")


def test_B40_api_responses_contain_no_pii(seeded, client):
    problems = []
    listing = client.get("/api/calls?range=all&limit=1000", **KEY).content.decode()
    for call_id, meta, _ in seeded["calls"]:
        pii = raw_pii_from_script(meta["mock_script"])
        detail = client.get(f"/api/calls/{call_id}", **KEY).json()
        free = " ".join(u["text"] for u in detail["transcript"]) + detail["summary"] + detail["call_reason_detail"]
        problems += [f"{call_id} detail: {x}" for x in leaks(free, pii)]
        problems += [f"{call_id} detail-json: {x}" for x in leaks(dumps(detail), pii, include_agent_names=False)]
        problems += [f"{call_id} list: {x}" for x in leaks(listing, pii, include_agent_names=False)]
    print(len(seeded["calls"]), "calls checked; problems:", problems[:10])
    assert not problems


def test_B41_api_session_respects_roles(seeded, client):
    login(client, "dev-A1001")
    r = client.get("/api/calls?range=all").json()
    assert r["count"] >= 1 and all(c["agent_id"] == "A1001" for c in r["items"])
    assert client.get("/api/calls/DEMO-0002").status_code == 404           # A1004's call
    assert client.get("/api/calls/DEMO-0001").status_code == 200           # own call
    print("agent A1001 sees", r["count"], "calls via API, all own")


def test_B42_event_payload(seeded):
    doc = call_doc("DEMO-0002")
    p = events.event_payload(doc)
    print(sorted(p))
    assert p["type"] == "call.insights.completed" and p["call_id"] == "DEMO-0002"
    assert {"qa_percent", "sentiment", "compliance_passed", "flag_list", "summary", "call_reason"} <= set(p)
    assert "transcript" not in p and "Meera" not in dumps(p) and "4 1 1 1" not in dumps(p)
    assert events.NullPublisher().publish(doc) is None and get_settings().events_backend == "none"


# --------------------------------------------------------------------------- code-level security

SECRET_PATTERNS = {
    "azure_storage_key": r"AccountKey=[A-Za-z0-9+/=]{40,}",
    "generic_key_assignment": r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-/+=]{16,}['\"]",
    "openai_style": r"sk-[A-Za-z0-9]{20,}",
    "private_key": r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    "conn_string_password": r"(?i)password=[^;\s'\"]{6,}",
}


def test_B43_no_hardcoded_secrets():
    files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True).stdout.split()
    hits = []
    for f in files:
        if f.endswith((".png", ".pdf", ".wav", ".svg", ".jpg")):
            continue
        try:
            text = (REPO / f).read_text(encoding="utf-8", errors="ignore")
        except (IsADirectoryError, FileNotFoundError):
            continue
        for kind, pattern in SECRET_PATTERNS.items():
            for m in re.finditer(pattern, text):
                hits.append((f, kind, m.group(0)[:24] + "…"))
    # The Azurite emulator account key is a public, documented constant (not a secret).
    azurite = "Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw=="
    real = [h for h in hits if not (h[1] == "azure_storage_key" and azurite[:20] in (REPO / h[0]).read_text())]
    for h in hits:
        print("hit:", h)
    assert not real


def test_B44_env_file_ignored_and_never_committed():
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO).returncode == 0
    tracked = subprocess.run(["git", "ls-files", ".env", "local.settings.json"], cwd=REPO, capture_output=True,
                             text=True).stdout.strip()
    history = subprocess.run(["git", "log", "--all", "--oneline", "--", ".env", "Code/functions/local.settings.json"],
                             cwd=REPO, capture_output=True, text=True).stdout.strip()
    print("ignored:", ignored, "| tracked:", repr(tracked), "| commits touching .env:", repr(history))
    assert ignored and not tracked and not history


def test_B45_no_transcript_text_in_log_statements():
    """Static check: no logging call in the app code interpolates transcript/phrase text."""
    offenders = []
    for path in list((REPO / "Code").rglob("*.py")):
        if "tests" in path.parts or ".venv" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\blog(ging)?\.(debug|info|warning|error|exception)\(", line) and \
                    re.search(r"\b(text|phrases|transcript|utterance|doc\b)",
                              re.sub(r"doc\.get\([\"']call_id[\"']\)", "", line.split("(", 1)[1])):
                offenders.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
    print("\n".join(offenders) or "no offending log statements")
    assert not offenders
