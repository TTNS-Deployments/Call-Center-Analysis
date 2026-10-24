"""Front end: templates, static files, browser behaviour, forms, accessibility, headers, escaping (test IDs F-xx).

Starts two live servers on the QA data folder:
  * 127.0.0.1:8765  `manage.py runserver` with local defaults (DEBUG on, like `make run`)
  * 127.0.0.1:8766  gunicorn with DJANGO_DEBUG=false + collectstatic (whitenoise manifest), like docker/App Service
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests
from django.test import Client, override_settings

from conftest import DATA_DIR, REPO, WEBAPP, login

DEV = "http://127.0.0.1:8765"
PROD = "http://127.0.0.1:8766"
EVIDENCE = REPO / "qa" / "evidence"
KEY_PAGES = ["/", "/calls/", "/calls/DEMO-0001/", "/agents/", "/agents/A1001/", "/insights/", "/pipeline/", "/settings/"]
XSS = "<img src=x onerror=alert('xss1')><script>alert('xss2')</script>"


def _wait(url, proc, log):
    for _ in range(120):
        if proc.poll() is not None:
            raise RuntimeError(f"server exited: {Path(log).read_text()[-2000:]}")
        try:
            if requests.get(f"{url}/healthz", timeout=1).status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"server did not start: {url}")


@pytest.fixture(scope="session")
def xss_call(seeded):
    """A call whose transcript, summary and metadata contain HTML/JS payloads."""
    from test_backend_pipeline import FakeSpeech, phrases_from, put_audio
    import callcenter.pipeline as p

    orig = p.get_speech_client
    p.get_speech_client = lambda: FakeSpeech(phrases_from([
        ("agent", f"Hello, thank you for calling. {XSS}"),
        ("customer", f"My problem is {XSS} and it is terrible"),
    ]))
    try:
        p.process_recording(put_audio("QA-XSS-0001", {"agent_id": "A1001", "agent_name": f"Eve{XSS}", "team": f"T{XSS}"}))
    finally:
        p.get_speech_client = orig
    return "QA-XSS-0001"


@pytest.fixture(scope="session")
def servers(seeded, xss_call):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    base = {k: v for k, v in os.environ.items() if k != "DJANGO_DEBUG"}
    base.update({"LOCAL_DATA_DIR": str(DATA_DIR), "CC_MODE": "local", "AUTH_MODE": "dev", "CRM_API_KEYS": "local-test-key"})
    procs = []
    dev_log = EVIDENCE / "runserver_dev.log"
    dev = subprocess.Popen([sys.executable, "manage.py", "runserver", "127.0.0.1:8765", "--noreload"], cwd=WEBAPP,
                           env={**base, "DJANGO_SETTINGS_MODULE": "config.settings"},
                           stdout=dev_log.open("w"), stderr=subprocess.STDOUT)
    procs.append(dev)
    static_root = Path(tempfile.mkdtemp(prefix="qa-static-"))
    import atexit, shutil
    atexit.register(shutil.rmtree, static_root, True)
    prod_env = {**base, "DJANGO_SETTINGS_MODULE": "qa_settings", "DJANGO_DEBUG": "false", "QA_STATIC_ROOT": str(static_root),
                "PYTHONPATH": f"{REPO / 'qa' / 'tests'}:{WEBAPP}", "DJANGO_ALLOWED_HOSTS": "127.0.0.1,localhost"}
    cs = subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], cwd=WEBAPP, env=prod_env,
                        capture_output=True, text=True)
    (EVIDENCE / "collectstatic.log").write_text(cs.stdout + cs.stderr)
    prod_log = EVIDENCE / "gunicorn_prod.log"
    prod = subprocess.Popen([sys.executable, "-m", "gunicorn", "--bind=127.0.0.1:8766", "--workers=1", "--threads=4",
                             "config.wsgi"], cwd=WEBAPP, env=prod_env, stdout=prod_log.open("w"), stderr=subprocess.STDOUT)
    procs.append(prod)
    try:
        _wait(DEV, dev, dev_log)
        _wait(PROD, prod, prod_log)
        yield {"collectstatic": cs, "static_root": static_root}
    finally:
        for p in procs:
            p.terminate()
            p.wait(timeout=10)


def session_for(base_url, user="dev-admin") -> requests.Session:
    s = requests.Session()
    page = s.get(f"{base_url}/auth/login")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text).group(1)
    r = s.post(f"{base_url}/auth/login", data={"user": user, "csrfmiddlewaretoken": token},
               headers={"Referer": f"{base_url}/auth/login"}, allow_redirects=False)
    assert r.status_code == 302, r.status_code
    return s


@pytest.fixture(scope="session")
def browser(servers):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def new_page(browser, base=DEV, user="dev-admin", **ctx):
    context = browser.new_context(**ctx)
    page = context.new_page()
    # domcontentloaded: the "load" event also waits for htmx / Chart.js from public CDNs, which stalled
    # for 30 s twice during this QA run (see defect on CDN scripts); the sign-in itself does not need them.
    page.goto(f"{base}/auth/login", wait_until="domcontentloaded")
    page.click(f'button[name="user"][value="{user}"]')
    page.wait_for_url(f"{base}/", wait_until="domcontentloaded")
    return page


# --------------------------------------------------------------------------- templates

def test_F01_templates_no_missing_variables(seeded):
    """(a) Strict: string_if_invalid on pages that only show fully processed calls.
    (b) All pages incl. failed / metadata-less calls: no literal 'None', raw '{{' / '{%' in visible text.
    (string_if_invalid also defeats |default filters, so (a) is limited to complete data - Django caveat.)"""
    import copy
    import html as htmllib
    from django.conf import settings

    templates = copy.deepcopy(settings.TEMPLATES)
    templates[0]["OPTIONS"]["string_if_invalid"] = "@@MISSING:%s@@"
    strict_pages = ["/", "/calls/?q=DEMO", "/calls/DEMO-0001/", "/calls/DEMO-0003/", "/calls/QA-XSS-0001/",
                    "/agents/", "/insights/?view=builtin", "/settings/"]
    found = {}
    with override_settings(TEMPLATES=templates):
        c = login(Client(), "dev-admin")
        for url in strict_pages:
            for name in set(re.findall(r"@@MISSING:([^@]+)@@", c.get(url).content.decode())):
                found.setdefault(name, set()).add(url)
    literal = []
    for user, pages in {"dev-admin": KEY_PAGES + ["/calls/?range=all", "/calls/?range=all&page=2"],
                        "dev-A1001": ["/", "/calls/", "/agents/me/"]}.items():
        c = login(Client(), user)
        for url in pages:
            body = c.get(url).content.decode()
            visible = htmllib.unescape(re.sub(r"<script.*?</script>|<[^>]+>", " ", body, flags=re.S))
            for token in ("None", "{{", "{%", "@@"):
                if re.search(rf"(?<![\w]){re.escape(token)}(?![\w])", visible):
                    literal.append((user, url, token))
    print("strict unresolved variables:", {k: sorted(v) for k, v in found.items()} or "none")
    print("literal None / template syntax in visible text:", literal or "none")
    assert not found and not literal


# --------------------------------------------------------------------------- static files

def _local_assets(html):
    return sorted(set(re.findall(r'(?:href|src)="(/static/[^"]+)"', html)))


@pytest.mark.parametrize("base", [DEV, PROD], ids=["debug-runserver", "prod-whitenoise"])
def test_F02_static_files_served(servers, base):
    s = session_for(base)
    assets = set()
    for url in ["/auth/login"] + KEY_PAGES:
        assets.update(_local_assets(s.get(f"{base}{url}").text))
    results = {}
    for a in sorted(assets):
        r = requests.get(f"{base}{a}")
        results[a] = (r.status_code, r.headers.get("Content-Type", "").split(";")[0], r.headers.get("Cache-Control", ""))
    for a, v in results.items():
        print(base, a, v)
    assert {"css", "js", "png"} <= {a.rsplit(".", 1)[-1] for a in assets}
    assert all(v[0] == 200 for v in results.values())
    assert requests.get(f"{base}/static/does-not-exist.css").status_code == 404
    if base == PROD:
        assert servers["collectstatic"].returncode == 0
        assert all(re.search(r"\.[0-9a-f]{12}\.", a) for a in assets), "manifest (hashed) names expected"
        assert all("max-age" in v[2] for v in results.values())


# --------------------------------------------------------------------------- browser

def test_F03_no_console_errors_or_failed_requests(browser):
    page = new_page(browser)
    problems = []
    page.on("console", lambda m: problems.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    # Media streams are aborted by the browser when the test navigates away (net::ERR_ABORTED): not an app error.
    page.on("requestfailed", lambda r: problems.append(f"requestfailed: {r.url} {r.failure}")
            if not (r.resource_type == "media" and "ERR_ABORTED" in (r.failure or "")) else None)
    page.on("response", lambda r: problems.append(f"HTTP {r.status}: {r.url}") if r.status >= 400 else None)
    visited = []
    for url in KEY_PAGES + ["/calls/DEMO-0004/", "/insights/?view=builtin"]:
        page.goto(f"{DEV}{url}", wait_until="networkidle")
        visited.append(url)
    agent = new_page(browser, user="dev-A1001")
    agent.on("pageerror", lambda e: problems.append(f"agent pageerror: {e}"))
    agent.on("console", lambda m: problems.append(f"agent console.{m.type}: {m.text}") if m.type == "error" else None)
    for url in ["/", "/calls/", "/agents/me/", "/calls/DEMO-0001/"]:
        agent.goto(f"{DEV}{url}", wait_until="networkidle")
    print("visited:", visited, "\nproblems:", problems or "none")
    assert not problems


def test_F04_charts_render(browser):
    page = new_page(browser)
    counts = {}
    for url in ["/", "/insights/?view=builtin", "/agents/", "/agents/A1001/"]:
        page.goto(f"{DEV}{url}", wait_until="networkidle")
        counts[url] = page.evaluate("""() => { const cs=[...document.querySelectorAll('canvas')];
            return {canvases: cs.length, charts: cs.filter(c => window.Chart && Chart.getChart(c)).length,
                    chartjs: !!window.Chart, htmx: !!window.htmx}; }""")
    print(counts)
    assert all(v["chartjs"] and v["htmx"] for v in counts.values())
    assert all(v["canvases"] == v["charts"] for v in counts.values()) and counts["/"]["charts"] >= 1


def test_F05_live_search_htmx(browser):
    page = new_page(browser)
    page.goto(f"{DEV}/calls/?range=all", wait_until="networkidle")
    before = page.locator("a.cell-title").count()
    with page.expect_response(lambda r: "/calls/" in r.url and r.request.headers.get("hx-request") == "true"):
        page.locator('input[name="q"]').press_sequentially("DEMO-0002", delay=30)   # keyup triggers htmx
    page.wait_for_timeout(500)
    after = [t.strip() for t in page.locator("a.cell-title").all_inner_texts()]
    print("rows before", before, "after", after, "url", page.url)
    assert before > 1 and after == ["DEMO-0002"]


def test_F06_audio_player_loads(browser):
    page = new_page(browser)
    page.goto(f"{DEV}/calls/DEMO-0001/", wait_until="networkidle")
    page.wait_for_function("() => { const a=document.querySelector('audio'); return a && a.readyState >= 1; }", timeout=10000)
    duration = page.evaluate("() => document.querySelector('audio').duration")
    print("audio duration (s):", duration)
    assert 80 <= duration <= 95   # DEMO-0001 is 88 s


def test_F07_browser_login_logout(browser):
    page = new_page(browser, user="dev-qa")
    assert "Leela Thomas" in page.inner_text(".userbox")
    page.click('.userbox button[type="submit"]')
    page.wait_for_url(f"{DEV}/auth/login")
    page.goto(f"{DEV}/calls/")
    print("after logout:", page.url)
    assert "/auth/login?next=/calls/" in page.url


# --------------------------------------------------------------------------- forms

def test_F08_csrf_tokens_and_enforcement(seeded):
    c = login(Client(), "dev-admin")
    missing = []
    for url in ["/auth/login"] + KEY_PAGES + ["/calls/DEMO-0004/"]:
        html = c.get(url).content.decode()
        for form in re.findall(r"<form[^>]*method=\"post\"[^>]*>.*?</form>", html, re.S | re.I):
            if "csrfmiddlewaretoken" not in form:
                missing.append(url)
    strict = Client(enforce_csrf_checks=True)
    login_no_token = strict.post("/auth/login", {"user": "dev-admin"}).status_code
    print("forms without CSRF token:", missing or "none", "| POST /auth/login without token ->", login_no_token)
    assert not missing and login_no_token == 403


def test_F09_required_fields_and_error_messages(browser):
    page = new_page(browser)
    page.goto(f"{DEV}/pipeline/")
    required = page.evaluate("() => document.querySelector('input[name=audio]').required")
    page.click('form[action$="/pipeline/upload"] button[type="submit"]')
    still = page.url.endswith("/pipeline/")
    invalid = page.evaluate("() => document.querySelector('input[name=audio]').matches(':invalid')")
    page.goto(f"{DEV}/settings/?file=call_reasons.yaml")
    page.fill("textarea[name=content]", "categories: []")
    page.click('button[value="save"]')
    page.wait_for_load_state()
    err = page.inner_text("body")
    print("audio required:", required, "| blocked submit:", still and invalid, "| settings error shown:",
          "Not saved" in err and "non-empty" in err)
    assert required and still and invalid and "Not saved" in err and "non-empty" in err


# --------------------------------------------------------------------------- accessibility / responsive

def test_F10_titles_alt_labels(browser):
    page = new_page(browser)
    titles, issues = {}, []
    for url in KEY_PAGES + ["/auth/login"]:
        if url == "/auth/login":
            page = browser.new_context().new_page()
        page.goto(f"{DEV}{url}", wait_until="networkidle")
        titles[url] = page.title()
        res = page.evaluate("""() => {
          const out = [];
          if (!document.documentElement.lang) out.push('html has no lang');
          if (!document.querySelector('meta[name=viewport]')) out.push('no viewport meta');
          document.querySelectorAll('img').forEach(i => { if (!i.hasAttribute('alt')) out.push('img without alt: ' + i.src); });
          document.querySelectorAll('input, select, textarea').forEach(el => {
            if (el.type === 'hidden') return;
            const labelled = el.labels && el.labels.length || el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.title;
            if (!labelled) out.push(`unlabelled ${el.tagName.toLowerCase()}[name=${el.name || el.id}]`);
          });
          document.querySelectorAll('button').forEach(b => { if (!b.innerText.trim() && !b.getAttribute('aria-label') && !b.title) out.push('button without name'); });
          if (!document.querySelector('h1')) out.push('no h1');
          return out; }""")
        issues += [f"{url}: {x}" for x in res]
    print("titles:", titles)
    print("issues:", issues or "none")
    assert all(t and "·" in t for t in titles.values()) and len(set(titles.values())) >= 6
    assert not issues


def test_F11_no_horizontal_scroll_on_mobile(browser):
    page = new_page(browser, viewport={"width": 375, "height": 812}, is_mobile=True)
    overflow = {}
    for url in KEY_PAGES + ["/calls/DEMO-0004/"]:
        page.goto(f"{DEV}{url}", wait_until="networkidle")
        overflow[url] = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    login = browser.new_context(viewport={"width": 375, "height": 812}).new_page()
    login.goto(f"{DEV}/auth/login")
    overflow["/auth/login"] = login.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    print("horizontal overflow px at 375 wide:", overflow)
    assert all(v <= 1 for v in overflow.values())


# --------------------------------------------------------------------------- security headers / escaping

@pytest.mark.parametrize("base", [DEV, PROD], ids=["debug-runserver", "prod-gunicorn"])
def test_F12_security_headers_and_cookies(servers, base):
    page = requests.get(f"{base}/auth/login")
    token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text).group(1)
    r = requests.post(f"{base}/auth/login", data={"user": "dev-admin", "csrfmiddlewaretoken": token},
                      cookies=page.cookies, headers={"Referer": f"{base}/auth/login"}, allow_redirects=False)
    h = r.headers
    cookies = r.raw.headers.getlist("Set-Cookie")       # raw list: "expires=Fri, 23 Oct" contains a comma
    sess = next((c for c in cookies if c.startswith("sessionid=")), "")
    csrf = next((c for c in page.raw.headers.getlist("Set-Cookie") if c.startswith("csrftoken=")), "")
    found = {
        "X-Frame-Options": h.get("X-Frame-Options"),
        "X-Content-Type-Options": h.get("X-Content-Type-Options"),
        "Referrer-Policy": h.get("Referrer-Policy"),
        "Cross-Origin-Opener-Policy": h.get("Cross-Origin-Opener-Policy"),
        "Content-Security-Policy": h.get("Content-Security-Policy"),
        "session HttpOnly": "httponly" in sess.lower(),
        "session SameSite": re.search(r"samesite=(\w+)", sess, re.I).group(1) if "samesite" in sess.lower() else None,
        "csrf SameSite": re.search(r"samesite=(\w+)", csrf, re.I).group(1) if "samesite" in csrf.lower() else None,
        "Server header": h.get("Server"),
    }
    print(base, found)
    assert found["X-Frame-Options"] == "DENY" and found["X-Content-Type-Options"] == "nosniff"
    assert found["Referrer-Policy"] and found["session HttpOnly"] and found["session SameSite"] == "Lax"
    assert found["csrf SameSite"] == "Lax"


def test_F13_content_security_policy_present(servers):
    h = session_for(DEV).get(f"{DEV}/").headers
    print("Content-Security-Policy:", h.get("Content-Security-Policy"))
    assert h.get("Content-Security-Policy"), "no CSP header"


def test_F14_output_escaping_no_injection(browser, xss_call):
    c = login(Client(), "dev-admin")
    detail = c.get(f"/calls/{xss_call}/").content.decode()
    listing = c.get("/calls/?q=QA-XSS").content.decode()
    search = c.get("/calls/?q=%3Cscript%3Ealert(3)%3C/script%3E").content.decode()
    raw = [n for n, h in (("detail", detail), ("list", listing), ("search", search))
           if "<script>alert(" in h or "<img src=x onerror" in h]
    page = new_page(browser)
    dialogs = []
    page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    for url in [f"/calls/{xss_call}/", "/calls/?q=QA-XSS", "/calls/?q=%3Cimg%20src%3Dx%20onerror%3Dalert(4)%3E",
                "/agents/A1001/", "/"]:
        page.goto(f"{DEV}{url}", wait_until="networkidle")
    injected = page.evaluate("() => document.querySelectorAll('img[src=x], img[onerror]').length")
    print("raw payload in HTML:", raw or "none", "| alert dialogs:", dialogs or "none", "| injected <img>:", injected,
          "| escaped form present:", "&lt;script&gt;" in detail or "&lt;img" in detail)
    assert not raw and not dialogs and injected == 0


def test_F15_third_party_scripts_have_sri(seeded):
    html = login(Client(), "dev-admin").get("/").content.decode()
    external = re.findall(r'<script[^>]+src="(https?://[^"]+)"[^>]*>', html)
    tags = re.findall(r'<script[^>]+src="https?://[^"]+"[^>]*>', html)
    without = [t for t in tags if "integrity=" not in t]
    print("external scripts:", external, "| without integrity:", len(without))
    assert not without


def test_F16_server_logs_have_no_pii(servers, seeded):
    """After all browser tests: the live server logs must not contain raw PII from the sample calls."""
    from conftest import leaks, raw_pii_from_script
    text = (EVIDENCE / "runserver_dev.log").read_text() + (EVIDENCE / "gunicorn_prod.log").read_text()
    pii = {}
    for _, meta, _ in seeded["calls"]:
        for k, v in raw_pii_from_script(meta["mock_script"]).items():
            pii.setdefault(k, set()).update(v)
    pii.pop("agent_names")
    found = leaks(text, pii)
    print(f"{len(text.splitlines())} log lines scanned; PII found: {found or 'none'}")
    assert not found
