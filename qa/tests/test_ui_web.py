"""UI / web app: server-rendered pages and behaviour via Django's test client (test IDs U-xx)."""
from __future__ import annotations

import re

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings

from callcenter import pipeline, samples
from callcenter.factory import get_call_store, get_dispatcher

from conftest import DEMO_IDS, call_doc, login

PERSONAS = ["dev-admin", "dev-supervisor", "dev-qa"] + [f"dev-A100{i}" for i in range(1, 7)]
PAGES = ["/", "/calls/", "/calls/DEMO-0001/", "/agents/", "/agents/A1001/", "/agents/me/", "/insights/",
         "/pipeline/", "/settings/", "/settings/?file=compliance_checks.yaml", "/settings/?file=call_reasons.yaml"]


def row_ids(html: str) -> list[str]:
    """Call IDs in table order (each row links to its call twice)."""
    return list(dict.fromkeys(re.findall(r'class="cell-title" href="/calls/([^/"]+)/"', html)))


def fresh(user=None):
    c = Client(raise_request_exception=False)
    return login(c, user) if user else c


def wait_pool():
    """Wait for the LocalDispatcher background thread(s), then recreate the pool for later tests."""
    d = get_dispatcher()
    d.pool.shutdown(wait=True)
    from concurrent.futures import ThreadPoolExecutor
    d.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


# --------------------------------------------------------------------------- authentication

def test_U01_login_page_lists_demo_personas(seeded):
    r = fresh().get("/auth/login")
    html = r.content.decode()
    found = re.findall(r'name="user" value="([^"]+)"', html)
    print(r.status_code, found)
    assert r.status_code == 200 and found == PERSONAS


@pytest.mark.parametrize("user", PERSONAS)
def test_U02_login_each_persona(seeded, user):
    c = Client()
    r = c.post("/auth/login", {"user": user})
    assert r.status_code == 302 and r["Location"] == "/"
    page = c.get("/")
    assert page.status_code == 200 and b"Sign out" in page.content


def test_U03_invalid_login_rejected_generic(seeded):
    c = fresh()
    r = c.post("/auth/login", {"user": "dev-root"})
    print("dev mode unknown persona ->", r.status_code, r["Content-Type"], r.content[:60])
    assert r.status_code in (400, 401, 403) and b"Traceback" not in r.content
    assert c.get("/").status_code == 302      # still not signed in
    with override_settings(AUTH_MODE="password", DEMO_PASSWORD="right-pass"):
        from django.core.cache import cache
        cache.clear()
        r = fresh().post("/auth/login", {"user": "dev-admin", "password": "wrong"})
        msg = re.findall(r'class="msg error">([^<]+)<', r.content.decode())
        print("password mode wrong password ->", r.status_code, msg)
        assert r.status_code == 403 and msg == ["Wrong password."]
        r = fresh().post("/auth/login", {"user": "dev-nobody", "password": "wrong"})
        print("password mode ignores the user field (single admin) ->", r.status_code)
        assert r.status_code == 403
        cache.clear()


def test_U04_open_redirect_after_login_blocked(seeded):
    for nxt in ["https://evil.example/", "//evil.example/x", "/calls/"]:
        r = Client().post(f"/auth/login?next={nxt}", {"user": "dev-admin"})
        print(nxt, "->", r["Location"])
        assert r["Location"] == ("/calls/" if nxt == "/calls/" else "/")


def test_U05_logout(seeded):
    c = fresh("dev-admin")
    assert c.get("/auth/logout").status_code == 405       # GET not allowed (CSRF-safe logout)
    r = c.post("/auth/logout")
    assert r.status_code == 302 and r["Location"].endswith("/auth/login")
    r = c.get("/calls/")
    print("after logout /calls/ ->", r.status_code, r["Location"])
    assert r.status_code == 302 and "/auth/login?next=/calls/" in r["Location"]


@pytest.mark.parametrize("url", PAGES + ["/calls/DEMO-0001/audio", "/insights/powerbi-token"])
def test_U06_unauthenticated_redirects_to_login(seeded, url):
    r = fresh().get(url)
    assert r.status_code == 302 and r["Location"].startswith("/auth/login?next=")


def test_U07_unauthenticated_post_endpoints_redirect(seeded):
    c = Client(enforce_csrf_checks=False)
    for url in ["/pipeline/upload", "/calls/DEMO-0001/retry", "/settings/"]:
        r = c.post(url)
        assert r.status_code == 302 and "/auth/login" in r["Location"], url
    assert fresh().get("/healthz").content == b"ok"


# --------------------------------------------------------------------------- roles

EXPECTED = {   # url -> {persona: status}
    "/": dict(admin=200, supervisor=200, qa=200, agent=200),
    "/calls/": dict(admin=200, supervisor=200, qa=200, agent=200),
    "/calls/DEMO-0001/": dict(admin=200, supervisor=200, qa=200, agent=200),   # agent A1001 owns DEMO-0001
    "/calls/DEMO-0002/": dict(admin=200, supervisor=200, qa=200, agent=403),
    "/calls/DEMO-0002/audio": dict(admin=200, supervisor=200, qa=200, agent=403),
    "/agents/": dict(admin=200, supervisor=200, qa=200, agent=403),
    "/agents/A1001/": dict(admin=200, supervisor=200, qa=200, agent=200),
    "/agents/A1004/": dict(admin=200, supervisor=200, qa=200, agent=403),
    "/agents/me/": dict(admin=302, supervisor=302, qa=302, agent=200),
    "/insights/": dict(admin=200, supervisor=200, qa=200, agent=403),
    "/pipeline/": dict(admin=200, supervisor=200, qa=403, agent=403),
    "/settings/": dict(admin=200, supervisor=403, qa=403, agent=403),
}
ROLE_USER = {"admin": "dev-admin", "supervisor": "dev-supervisor", "qa": "dev-qa", "agent": "dev-A1001"}


@pytest.mark.parametrize("role", list(ROLE_USER))
def test_U08_role_access_matrix(seeded, role):
    c = fresh(ROLE_USER[role])
    wrong = []
    for url, expected in EXPECTED.items():
        got = c.get(url).status_code
        if got != expected[role]:
            wrong.append((url, expected[role], got))
    # state-changing endpoints
    up = c.post("/pipeline/upload").status_code
    rt = c.post("/calls/DEMO-0001/retry").status_code
    st = c.post("/settings/", {"file": "qa_rubric.yaml", "content": "x"}).status_code
    if role in ("qa", "agent"):
        wrong += [(u, 403, s) for u, s in (("upload", up), ("retry", rt)) if s != 403]
    if role != "admin":
        wrong += [("settings POST", 403, st)] if st != 403 else []
    print(role, "mismatches:", wrong)
    assert not wrong
    if role == "admin":  # the retry above re-queued DEMO-0001; let it finish
        wait_pool()


def test_U09_navigation_per_role(seeded):
    nav = {}
    for role, user in ROLE_USER.items():
        html = fresh(user).get("/").content.decode()
        block = html.split('<nav class="nav">')[1].split("</nav>")[0]
        nav[role] = [re.sub(r"\s+", " ", t).strip() for t in re.findall(r"</svg>\s*([^<]+?)\s*</a>", block)]
    print(nav)
    assert nav["admin"] == ["Overview", "Calls", "Agents", "Insights", "Pipeline", "Settings"]
    assert nav["supervisor"] == ["Overview", "Calls", "Agents", "Insights", "Pipeline"]
    assert nav["qa"] == ["Overview", "Calls", "Agents", "Insights"]
    assert nav["agent"] == ["Overview", "Calls", "My scorecard"]


def test_U10_agent_sees_only_own_calls_in_lists(seeded):
    html = fresh("dev-A1001").get("/calls/?range=all").content.decode()
    ids = set(row_ids(html))
    owners = {cid: (call_doc(cid) or {}).get("agent_id") for cid in ids}
    print("agent A1001 list:", owners)
    assert ids and all(a == "A1001" for a in owners.values())


# --------------------------------------------------------------------------- pages

@pytest.mark.parametrize("user", ["dev-admin", "dev-supervisor", "dev-qa", "dev-A1001"])
def test_U11_pages_load_without_errors(seeded, user):
    c = fresh(user)
    bad = []
    for url in PAGES + ["/?range=7", "/?range=90", "/?range=all", "/?team=Team%20Alpha", "/calls/?range=all",
                        "/insights/?view=builtin", "/agents/A1001/?range=all"] + [f"/calls/{i}/" for i in DEMO_IDS]:
        r = c.get(url)
        if r.status_code >= 500 or b"TemplateSyntaxError" in r.content or b"VariableDoesNotExist" in r.content:
            bad.append((url, r.status_code))
    print(user, "server/template errors:", bad)
    assert not bad


def test_U12_call_list_columns(seeded):
    html = fresh("dev-admin").get("/calls/?range=all").content.decode()
    head = re.sub(r"<[^>]+>", " ", html.split("<thead>")[1].split("</thead>")[0])
    cols = [w for w in re.split(r"\s{2,}", head) if w.strip()]
    print("columns:", cols)
    for col in ("Call", "Agent", "Summary", "Sentiment", "Resolution", "QA"):
        assert any(col.lower() in c.lower() for c in cols), col
    d = call_doc("DEMO-0003")
    row = html.split('href="/calls/DEMO-0003/"', 2)[2].split("</tr>")[0]
    shown = {"reason": d["call_reason_name"] in row, "sentiment": d["sentiment"]["customer"].capitalize() in row,
             "resolution": d["resolution"].capitalize() in row, "qa": f"{d['qa']['percent']:.0f}%" in row,
             "flags": "Repeat contact" in row}
    print("DEMO-0003 row shows:", shown)
    assert all(shown.values())


def test_U13_call_detail_shows_all_results(seeded):
    d = call_doc("DEMO-0003")
    html = fresh("dev-qa").get("/calls/DEMO-0003/").content.decode()
    import html as htmllib
    text = htmllib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)))
    checks = {
        "transcript utterance": d["transcript"][2]["text"].split("[")[0][:40] in text,
        "sentiment": d["sentiment"]["customer"].lower() in text.lower(),
        "emotion start/end": d["emotion"]["start"] in text.lower() and d["emotion"]["end"] in text.lower(),
        "QA score": f"{d['qa']['percent']:.0f}" in text,
        "compliance checks": all(ch["name"] in text for ch in d["compliance"]["checks"]),
        "summary": d["summary"][:30] in text,
        "call reason": d["call_reason_name"] in text,
        "root cause": d["root_cause"] in text,
        "redaction chip": 'class="pii"' in html,
    }
    print(checks)
    assert all(checks.values())


def test_U14_search_filter_sort(seeded):
    c = fresh("dev-admin")

    def ids(q):
        return row_ids(c.get(f"/calls/?range=all&{q}").content.decode())

    assert ids("q=DEMO-0002") == ["DEMO-0002"]
    assert ids("q=demo-0002") == ["DEMO-0002"]                       # case-insensitive
    assert set(ids("agent=A1004")) and all(call_doc(i)["agent_id"] == "A1004" for i in ids("agent=A1004"))
    assert all(call_doc(i)["team"] == "Team Beta" for i in ids("team=Team%20Beta"))
    assert all(call_doc(i)["call_reason"] == "card_services" for i in ids("reason=card_services"))
    assert all(call_doc(i)["sentiment"]["customer"] == "negative" for i in ids("sentiment=negative"))
    assert all(call_doc(i)["resolution"] == "escalated" for i in ids("resolution=escalated"))
    assert all("compliance_breach" in call_doc(i)["flag_list"] for i in ids("flag=compliance_breach"))
    def qa(i):
        return (call_doc(i).get("qa") or {}).get("percent")       # failed calls have no QA

    rng = [qa(i) for i in ids("qa_min=50&qa_max=80")]
    assert rng and all(p is not None and 50 <= p <= 80 for p in rng)
    asc = [qa(i) or -1 for i in ids("sort=qa")]
    desc = [qa(i) or -1 for i in ids("sort=-qa")]
    print("qa asc first 5:", asc[:5], "desc first 5:", desc[:5])
    assert asc == sorted(asc) and desc == sorted(desc, reverse=True)


def test_U15_empty_state(seeded):
    c = fresh("dev-admin")
    r = c.get("/calls/?q=zzz-no-such-call")
    assert r.status_code == 200 and b"No calls match these filters." in r.content
    bad = []
    for url in ["/", "/calls/", "/agents/", "/insights/?view=builtin", "/agents/A1001/"]:
        r = c.get(f"{url}?from=2001-01-01&to=2001-01-02")
        if r.status_code != 200:
            bad.append((url, r.status_code))
    a = fresh("dev-A1006").get("/agents/me/?from=2001-01-01&to=2001-01-02")
    print("empty-range errors:", bad, "| agent me empty:", a.status_code)
    assert not bad and a.status_code == 200


def test_U16_pagination(seeded):
    c = fresh("dev-admin")
    p1 = c.get("/calls/?range=all").content.decode()
    total = int(re.search(r"(\d+) calls?", p1).group(1))
    rows1 = row_ids(p1)
    p2 = c.get("/calls/?range=all&page=2").content.decode()
    rows2 = row_ids(p2)
    print("total", total, "page1", len(rows1), "page2", len(rows2), "| 'page 1 of' shown:", "page 1 of" in p1)
    assert total > 25 and len(rows1) == 25 and rows2 and not set(rows1) & set(rows2)
    assert "Next" in p1 and "Previous" in p2
    for bad in ("page=999", "page=abc", "page=-1"):
        assert c.get(f"/calls/?range=all&{bad}").status_code == 200, bad


@pytest.mark.parametrize("query", ["qa_min=abc", "qa_max=%3Cscript%3E", "sort=;drop", "range=bogus",
                                   "from=2026-99-99", "page=1e9", "team=%00"])
def test_U17_bad_parameters_friendly(seeded, query):
    r = fresh("dev-admin").get(f"/calls/?{query}")
    print(query, "->", r.status_code)
    assert r.status_code < 500, f"/calls/?{query} returned {r.status_code}"


@pytest.mark.parametrize("url, expected", [
    ("/calls/NO-SUCH-CALL/", 404), ("/calls/NO-SUCH-CALL/audio", 404), ("/settings/?file=../../.env", 404),
    ("/nope/", 404), ("/calls/DEMO-0001/retry", 405),
])
def test_U18_invalid_ids_friendly_error(seeded, url, expected):
    with override_settings(DEBUG=False):
        r = fresh("dev-admin").get(url)
    body = r.content.decode(errors="ignore")
    print(url, "->", r.status_code, re.findall(r"<h1>([^<]+)</h1>", body))
    assert r.status_code == expected and "Traceback" not in body
    if expected == 404:
        assert "Not found" in body and "Back to overview" in body


def test_U19_debug_false_hides_details_on_500(seeded):
    with override_settings(DEBUG=False):
        r = fresh("dev-admin").get("/calls/?qa_min=abc")
    body = r.content.decode(errors="ignore")
    print("DEBUG=False 500 body:", body[:120].replace("\n", " "))
    assert "Traceback" not in body and "ValueError" not in body and "settings.py" not in body


def test_U20_default_local_debug_pages(seeded):
    """Local defaults (DJANGO_DEBUG unset): what does an error page reveal?"""
    from django.conf import settings
    r404 = fresh("dev-admin").get("/calls/NO-SUCH-CALL/")
    r500 = fresh("dev-admin").get("/calls/?qa_min=abc")
    leak404 = b"Django tried these URL patterns" in r404.content or b"Raised by:" in r404.content
    leak500 = b"Traceback" in r500.content
    print("DEBUG =", settings.DEBUG, "| 404 shows debug info:", leak404, "| 500 shows traceback:", leak500)
    assert not (leak404 or leak500)


def test_U21_azure_mode_defaults_debug_off():
    from test_backend_config_api_security import run_py
    r = run_py("import django; django.setup(); from django.conf import settings as s; "
               "print(s.DEBUG, s.SESSION_COOKIE_SECURE, s.CSRF_COOKIE_SECURE, s.SECURE_SSL_REDIRECT, s.SECURE_HSTS_SECONDS)",
               {"CC_MODE": "azure", "DJANGO_SECRET_KEY": "qa-only", "AUTH_MODE": "entra"})
    print("azure defaults DEBUG, cookie secure, csrf secure, ssl redirect, hsts:", r.stdout.strip())
    assert r.stdout.split() == ["False", "True", "True", "True", "3600"]


# --------------------------------------------------------------------------- upload / retry / settings

def _upload(c, **fields):
    data = {"audio": SimpleUploadedFile(fields.pop("filename", "t.wav"), fields.pop("content", samples.make_wav(1500)),
                                        content_type="audio/wav"), **fields}
    r = c.post("/pipeline/upload", data, follow=True)
    return r, re.findall(r'class="msg (?:error|success)">([^<]+)<', r.content.decode())


def test_U22_upload_validation_and_success(seeded):
    c = fresh("dev-supervisor")
    r = c.post("/pipeline/upload", {}, follow=True)
    m0 = re.findall(r'class="msg error">([^<]+)<', r.content.decode())
    _, m1 = _upload(c, filename="notes.txt", content=b"hello")
    _, m2 = _upload(c, call_id="DEMO-0001")
    with override_settings(MAX_UPLOAD_MB=0):
        _, m3 = _upload(c, call_id="QA-BIG")
    _, m4 = _upload(c, call_id="QA-UP/../../x", agent_id="A1003", agent_name="Ananya Iyer")
    wait_pool()
    print("no file:", m0, "| .txt:", m1, "| duplicate:", m2, "| too big:", m3, "| ok:", m4)
    assert m0 == ["Choose an audio file to upload."]
    assert m1 == ["Unsupported file type .txt."]
    assert m2 == ["A call with ID DEMO-0001 already exists."]
    assert m3 == ["File is larger than 0 MB."]
    assert m4 and "Processing has started" in m4[0]
    new_id = re.search(r"as (\S+)\. Processing", m4[0]).group(1)
    assert "/" not in new_id and call_doc(new_id)["status"] == "completed"


def test_U23_retry_from_ui(seeded):
    pipeline.mark_failed("DEMO-0004", "enrich", RuntimeError("simulated outage"))
    c = fresh("dev-supervisor")
    pipe = c.get("/pipeline/").content.decode()
    assert "DEMO-0004" in pipe and "simulated outage" in pipe
    r = c.post("/calls/DEMO-0004/retry", {"next": "/pipeline/"})
    wait_pool()
    print("retry ->", r.status_code, r["Location"], "| status now:", call_doc("DEMO-0004")["status"])
    assert r.status_code == 302 and call_doc("DEMO-0004")["status"] == "completed"
    assert c.post("/calls/NO-SUCH/retry").status_code == 404


def test_U24_retry_next_param_open_redirect(seeded):
    c = fresh("dev-supervisor")
    r = c.post("/calls/DEMO-0003/retry", {"next": "https://evil.example/phish"})
    wait_pool()
    print("retry next=https://evil.example ->", r.status_code, r["Location"])
    assert not r["Location"].startswith(("http://", "https://", "//"))


def test_U25_settings_edit_validate_reset(seeded):
    c = fresh("dev-admin")
    r = c.post("/settings/", {"file": "call_reasons.yaml", "content": "categories: []"})
    assert r.status_code == 200 and b"Not saved" in r.content and b"non-empty" in r.content
    original = c.get("/settings/?file=call_reasons.yaml").content
    from callcenter import rules
    good = rules.default_config_text("call_reasons.yaml")
    r = c.post("/settings/", {"file": "call_reasons.yaml", "content": good}, follow=True)
    assert b"Saved." in r.content
    r = c.post("/settings/", {"file": "call_reasons.yaml", "action": "reset"}, follow=True)
    assert b"Saved." in r.content and r.status_code == 200 and original
    print("settings invalid -> 'Not saved', valid -> 'Saved.', reset OK")


def test_U26_htmx_partials(seeded):
    c = fresh("dev-admin")
    r = c.get("/calls/?q=DEMO", HTTP_HX_REQUEST="true")
    p = c.get("/pipeline/", HTTP_HX_REQUEST="true")
    assert r.status_code == 200 and b"<html" not in r.content and b"DEMO-0001" in r.content
    assert p.status_code == 200 and b"<html" not in p.content


def test_U27_audio_streaming(seeded):
    c = fresh("dev-admin")
    full = c.get("/calls/DEMO-0001/audio")
    size = int(full["Content-Length"])
    body = b"".join(full.streaming_content)
    part = c.get("/calls/DEMO-0001/audio", HTTP_RANGE="bytes=100-199")
    tail = c.get("/calls/DEMO-0001/audio", HTTP_RANGE="bytes=-10")
    bad = c.get("/calls/DEMO-0001/audio", HTTP_RANGE=f"bytes={size + 10}-")
    print("full", full.status_code, full["Content-Type"], size, "| range", part.status_code, part["Content-Range"],
          "| suffix", tail.status_code, tail["Content-Range"], "| unsatisfiable", bad.status_code)
    assert full.status_code == 200 and full["Content-Type"].startswith("audio/")
    assert len(body) == size and body[:4] == b"RIFF"
    assert part.status_code == 206 and b"".join(part.streaming_content) == body[100:200]
    assert tail.status_code == 206 and b"".join(tail.streaming_content) == body[-10:]
    assert bad.status_code == 416


def test_U28_edge_calls_render(seeded):
    """Calls with no metadata / very long transcript / failed status render in list and detail."""
    from test_backend_pipeline import FakeSpeech, phrases_from, put_audio
    import time
    import callcenter.pipeline as p

    if not call_doc("QA-NOMETA-UI"):
        p.process_recording(put_audio("QA-NOMETA-UI"))
    lines = [pair for i in range(1000) for pair in (("agent", f"Line {i} from the agent, checking the account."),
                                                    ("customer", f"Reply {i}, okay."))]
    orig = p.get_speech_client
    p.get_speech_client = lambda: FakeSpeech(phrases_from(lines))
    try:
        if not call_doc("QA-LONG-UI"):
            p.process_recording(put_audio("QA-LONG-UI", {"agent_id": "A1002"}))
    finally:
        p.get_speech_client = orig
    p.mark_failed("QA-NOMETA-UI", "transcribe", RuntimeError("boom"))
    c = fresh("dev-admin")
    t0 = time.perf_counter()
    long_page = c.get("/calls/QA-LONG-UI/")
    elapsed = time.perf_counter() - t0
    statuses = {u: c.get(u).status_code for u in ["/calls/?range=all", "/calls/QA-NOMETA-UI/", "/pipeline/", "/",
                                                    "/agents/?range=all"]}
    print("long transcript page:", long_page.status_code, f"{len(long_page.content) // 1024} KB in {elapsed:.2f}s", statuses)
    assert long_page.status_code == 200 and elapsed < 10 and all(s == 200 for s in statuses.values())
    p.retry("QA-NOMETA-UI")
