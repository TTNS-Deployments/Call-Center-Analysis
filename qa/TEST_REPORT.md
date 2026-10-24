# Test report: Call Center Analysis (local mode)

**Verdict: Works as expected**

Tested once in local mode across backend, UI and front end (90 test cases, 154 automated checks, plus the app's own 57 tests). The first run found 12 failures (11 defects: 1 Critical, 6 Medium, 5 Low by test); all were fixed in the code and the full suite was run again: every test case passes except the Docker stack check, which stays Blocked because containers cannot reach each other in this Codespace (the image itself builds and collectstatic succeeds). Every pipeline stage works (transcription, PII redaction, sentiment, emotion, QA, compliance, summary, reason, root cause, storage, failure and retry), as do all pages, roles, the CRM API and the security checks. All Critical and High tests pass, so the exit rule is met. Not covered: the real Azure services, Entra ID sign-in and Power BI, which need Azure credentials.

## Environment

| | |
|---|---|
| Mode | CC_MODE=local (mock Speech/Language/OpenAI, folder storage, SQLite), AUTH_MODE=dev, CRM_API_KEYS=local-test-key |
| Branch | main |
| Commit | 36bf55f Progress: chatbot handoff, shared App Service plan, separate OpenAI resource |
| Python | 3.14.2 (.venv) |
| Tools | Django 6.1.2, pytest 9.1.1, Playwright 1.63.0 (Chromium headless), openpyxl 3.1.5 |
| OS | Linux (GitHub Codespace) |
| Date | 2026-10-23 |
| Data | Isolated temp data folder per run (developer .localdata untouched) |

## Totals

| Layer | Pass | Fail | Blocked | Not Tested | Total |
|---|---|---|---|---|---|
| Backend | 45 | 0 | 1 | 0 | 46 |
| UI | 28 | 0 | 0 | 0 | 28 |
| Front end | 16 | 0 | 0 | 0 | 16 |
| **All** | 89 | 0 | 1 | 0 | 90 |

| Priority | Pass | Fail | Blocked | Not Tested | Total |
|---|---|---|---|---|---|
| Critical | 16 | 0 | 0 | 0 | 16 |
| High | 38 | 0 | 0 | 0 | 38 |
| Medium | 30 | 0 | 1 | 0 | 31 |
| Low | 5 | 0 | 0 | 0 | 5 |

Pass rate (Pass / executed): **100.0%** (89/89). Exit rule (all Critical and High pass): **met**.

Automated checks behind the cases: 154 pytest items in `qa/tests` (154 passed, 0 failed) plus the app's own 57 tests (`make test`, all passed). Every remaining failure is a product issue; test-harness problems found during the run were fixed in the tests, not in the app.

## Failed tests

| ID | Priority | Area | Reason | Defect |
|---|---|---|---|---|

## Defects

### D-01 (Medium, Backend): Local mock PII redaction misses spoken-form PII and unknown names

- **Steps to reproduce:** make setup; python -m callcenter upload (local mode); open /calls/DEMO-0001/, DEMO-0002, DEMO-0004 or GET /api/calls/DEMO-0001.
- **Expected vs actual:** Expected: names, email, date of birth, CVV and OTP redacted. Actual: 'rohan dot das at example dot com', 'twelfth of March, nineteen ninety', 'C V V is 1 2 3', OTP '5 8 2 9 1 4', 'Mister Rajesh Kumar' stay in the transcript, UI, API and Parquet.
- **Affected tests:** B-06
- **Suggested cause (read-only):** MockLanguageClient (Code/core/callcenter/language.py) is regex-based: names only from scenarios.yaml or after 'my name is', emails only in a@b.c form, digit runs only >= 10 digits, dates only '12 March 1990'. Production uses Azure AI Language (not tested here). Idea: add spoken-email / 'C V V'/'O T P' digit-run / spelled-date patterns and demo-script names to the mock, or a test that runs the demo scripts through it.

### D-02 (Medium, Backend / UI): Non-numeric query parameters cause HTTP 500

- **Steps to reproduce:** Sign in; open /calls/?qa_min=abc (or qa_max=<script>). Or GET /api/calls?limit=abc (or limit=1e3) with X-API-Key.
- **Expected vs actual:** Expected: parameter ignored or a 400 message. Actual: HTTP 500 (ValueError); with local defaults the full Django traceback page. Also limit=-5 returns count 35 but 30 items.
- **Affected tests:** U-17, B-39
- **Suggested cause (read-only):** analytics.filter_calls float(f['qa_min']) (Code/core/callcenter/analytics.py:86-88) and api.calls int(request.GET['limit']) (Code/webapp/portal/api.py:46) are not guarded. Idea: parse with try/except, clamp limit to 1..1000.

### D-03 (Medium, Backend): Silent / empty-transcript call is scored as a failing agent

- **Steps to reproduce:** Process a recording whose transcription returns no phrases (silent audio).
- **Expected vs actual:** Expected: a clear 'no speech' outcome (failed or not scored). Actual: completed with QA 0%, flags compliance_breach + low_qa + unresolved, summary with an empty quote.
- **Affected tests:** B-16
- **Suggested cause (read-only):** pipeline.enrich_transcript has no guard for zero phrases; llm.normalize (llm.py:123) marks unconditional required checks as 'fail' when absent, and the mock rates every criterion not_met. Idea: short-circuit empty transcripts to a 'no_speech' state.

### D-04 (Medium, Front end): Horizontal scrolling on phone-width screens

- **Steps to reproduce:** Open any signed-in page at 375 px width (Chromium device emulation).
- **Expected vs actual:** Expected: no horizontal scroll. Actual: page is 635 px wide (260 px overflow); login page is fine.
- **Affected tests:** F-11
- **Suggested cause (read-only):** In the <=860 px media query .app uses grid-template-columns: 1fr (app.css:293); 1fr = minmax(auto,1fr) so the column cannot shrink below the sidebar/nav min-content width. Idea: minmax(0,1fr) and min-width:0 on .sidebar/.main.

### D-05 (Low, UI): Retry 'next' parameter allows an off-site redirect

- **Steps to reproduce:** As Supervisor POST /calls/<id>/retry with next=https://evil.example/phish (valid CSRF token).
- **Expected vs actual:** Expected: redirect limited to this site. Actual: 302 Location https://evil.example/phish.
- **Affected tests:** U-24
- **Suggested cause (read-only):** views.call_retry redirects to request.POST['next'] unchecked (Code/webapp/portal/views.py:162). Needs a signed-in Supervisor and CSRF token, so low risk. Idea: reuse url_has_allowed_host_and_scheme as in accounts.views._safe_next.

### D-06 (Low, Front end): No Content-Security-Policy header

- **Steps to reproduce:** Inspect response headers of any page (runserver or gunicorn).
- **Expected vs actual:** Expected: a CSP. Actual: none (other headers fine).
- **Affected tests:** F-13
- **Suggested cause (read-only):** No CSP middleware/setting. Idea: Django 6 ContentSecurityPolicyMiddleware or a header allowing self + the two CDNs (and inline scripts via nonces).

### D-07 (Low, Front end): CDN scripts without Subresource Integrity; pages wait on public CDNs

- **Steps to reproduce:** View source of any page; observe load in a slow network.
- **Expected vs actual:** Expected: integrity attributes or self-hosted copies. Actual: htmx (unpkg) and Chart.js (jsdelivr) have no integrity; twice during this run a page 'load' stalled 30 s waiting for them (server answered in ms).
- **Affected tests:** F-15 (F-03 observation)
- **Suggested cause (read-only):** base.html:50-51. Idea: add integrity + crossorigin, or vendor the files into static/ (also helps private-network deployments).

### D-08 (Low, Front end): 'None' shown as agent name for calls without metadata

- **Steps to reproduce:** Upload a call without agent details that gets flagged; open the Overview.
- **Expected vs actual:** Expected: blank or '–'. Actual: 'None · 23 Oct, 15:30' in the flagged-calls list.
- **Affected tests:** F-01
- **Suggested cause (read-only):** overview.html:86 {{ c.agent_name }} without |default; build_call_document stores agent_name=None. Idea: |default:'–'.

### D-09 (Low, Backend): Misspelt CC_MODE silently runs in local mode

- **Steps to reproduce:** CC_MODE=prodution python -m callcenter status.
- **Expected vs actual:** Expected: startup error. Actual: runs with local storage and mock AI.
- **Affected tests:** B-32
- **Suggested cause (read-only):** config.get_settings (config.py:114) only checks == 'azure'. Idea: reject values other than local/azure.

### D-10 (Low, Backend): Non-numeric AGENT_CHANNEL / MAX_UPLOAD_MB: error does not name the variable

- **Steps to reproduce:** AGENT_CHANNEL=left python -m callcenter status; MAX_UPLOAD_MB=lots manage.py check.
- **Expected vs actual:** Expected: message naming the variable. Actual: traceback ending "ValueError: invalid literal for int() with base 10: 'left'".
- **Affected tests:** B-31
- **Suggested cause (read-only):** config.py:144 and settings.py:116 call int() directly. Idea: helper that raises 'AGENT_CHANNEL must be an integer'.

### D-11 (Low, UI): Local defaults show Django debug pages

- **Steps to reproduce:** make run (DJANGO_DEBUG unset); open /calls/NO-SUCH-CALL/ or trigger D-02.
- **Expected vs actual:** Expected (per QA brief): no debug details. Actual: technical 404 and full traceback pages, because DEBUG defaults to true when CC_MODE=local.
- **Affected tests:** U-20
- **Suggested cause (read-only):** Intentional for developer machines (settings.py: DEBUG = env_bool('DJANGO_DEBUG', IS_LOCAL)); Azure default is False (U-21) and docker-compose sets false. Only a risk if local mode is exposed to others.

## Not tested / out of scope

| Type | Item | Reason |
|---|---|---|
| Out of scope | Real Azure AI Speech, Language and OpenAI | Needs keys and costs money; brief says mocks only. So real transcription of silent/corrupt audio, real PII accuracy and LLM scoring against Data/demo_calls/README.md expectations are unverified. |
| Out of scope | Cosmos DB call store, Blob Storage/ADLS on Azure, Key Vault references | Azure resources. SQLite and folder storage tested; Azurite path covered by B-46. |
| Out of scope | Event Hubs publishing (EVENTS_BACKEND=eventhub) | Azure only. Payload builder tested (B-42). |
| Out of scope | Microsoft Entra ID sign-in (AUTH_MODE=entra), app roles from tokens | Needs an app registration and credentials. |
| Out of scope | Power BI embedding / report (Analytics/) | Needs Power BI Embedded/Fabric capacity; no .pbix in repo. |
| Out of scope | ARM deployment, Deploy to Azure button, deploy.sh, deploy_code_arm.sh, setup_entra.sh, GitHub Deploy workflow | Real deployment. ARM/parameter consistency is covered by existing tests (B-01). |
| Out of scope | Private endpoints, VPN, Microsoft Defender, App Service networking, HTTPS/HSTS on the real host | Azure portal / deployment checks. |
| Out of scope | QueueDispatcher (Azure retry via Storage queues) | Needs Azure Storage queues; local retry path tested. |
| Not Tested | Azure Functions host runtime (func start + Azurite queues) | Needs Functions Core Tools (npm global install). Handler logic run in-process instead (B-27). |
| Not Tested | make package (build/webapp.zip, functions.zip) | Writes into build/ outside qa/; brief restricts new files to qa/. |
| Not Tested | make worker (folder watcher) | Endless polling loop; same pipeline entry point as tested paths. |
| Not Tested | Cross-browser (Firefox/WebKit), screen-reader audit, colour contrast | Only Chromium installed; basic a11y checks done (F-10). |
| Not Tested | Load / concurrency testing | Not requested; single long-transcript performance checked (B-19, U-28). |

## Observations (no defect raised)

- Mock speech ignores audio content (documented), so empty/corrupt audio can only be checked with injected clients (B-15, B-16, B-18).
- Raw (unredacted) transcripts are kept in the `transcripts` container by design (SECURITY.md: private container + lifecycle rule); every user-facing output is redacted.
- Dev-mode sign-in with an unknown persona returns a bare `400 Unknown user` text page (no template).
- `python -m callcenter retry <unknown id>` exits 1 with a KeyError traceback rather than a message.
- The Azurite account key in docker-compose.yml is the public emulator key, not a secret.
- First run: twice a browser page load stalled 30 s waiting for the public CDN scripts (server answered in ms); fixed by D-07.
- Found while fixing: two retries of the same call at once could collide on the local storage temp file (FileNotFoundError); storage.py now uses a unique temp name per write.

## How to re-run

```bash
.venv/bin/python -m pip install openpyxl          # Playwright + Chromium were already in the venv
make test > qa/evidence/make_test.log 2>&1; echo exit=$? >> qa/evidence/make_test.log
.venv/bin/python -m pytest qa/tests -v -rA --junitxml=qa/evidence/qa_junit.xml > qa/evidence/qa_pytest.log 2>&1
.venv/bin/python qa/build_report.py              # regenerates test_cases.md, TEST_REPORT.md, test_report.xlsx
```

Files: `qa/00_discovery.md`, `qa/test_cases.md`, `qa/TEST_REPORT.md`, `qa/test_report.xlsx`, `qa/tests/`, `qa/evidence/`.
