# QA Step 1: Discovery (read-only)

Repository: `/workspaces/Call-Center-Analysis`, branch `main`, commit `36bf55f`.
Date: 2026-10-23. Python in `.venv`: 3.14.2 (Django 6.1.2, whitenoise 6.12, pyarrow 26, pytest 9.1.1, Playwright 1.63 + Chromium already installed).

Sources read: `README.md`, `Makefile`, `docker-compose.yml`, `Dockerfile`, `requirements-dev.txt`, `.gitignore`, `.env.example`,
`Documents/configuration.md`, `Documents/local-development.md`, `Documents/architecture.md` / `technical-specification.md` (PII sections),
`SECURITY.md`, `PROJECT_PROGRESS.md`, all of `Code/core/callcenter/*.py`, `Code/functions/function_app.py`,
`Code/webapp/{config,accounts,portal}`, the templates and static files, the existing tests, and `Data/`.
`.env` exists; it is git-ignored and byte-identical to `.env.example` (checked with `cmp`, contents not printed).

## Make targets (`make help`)

| Target | What it does |
|---|---|
| `setup` | Create `.venv` and `pip install -r requirements-dev.txt` |
| `seed` | `python -m callcenter seed --count 40` (40 synthetic calls through the whole pipeline) |
| `run` | `manage.py runserver 0.0.0.0:8000` |
| `worker` | `python -m callcenter worker` (polls the recordings folder) |
| `test` | `pytest Code/core -q` then `manage.py test` in `Code/webapp` |
| `package` | `Deployment/scripts/package.sh` (build zips) |
| `reset` | `python -m callcenter reset` (delete `.localdata`) |
| `docker-up` / `docker-down` | Web + Azurite via docker compose |

Only `make` includes `.env`. CLI: `python -m callcenter seed | upload | process | worker | retry | status | reset`.

## Existing automated tests

| File | Count | Covers |
|---|---|---|
| `Code/core/tests/test_pipeline.py` | 10 | E2E local pipeline + Parquet, non-audio ignored, duplicate skipped, failure + retry, timestamps, Event Grid parsing, QA scoring, speech metrics |
| `Code/core/tests/test_language.py` | 5 | Mock PII redaction (name/phone/card/email/dates/spoken digits), coalescing, sentiment labels |
| `Code/core/tests/test_llm.py` | 4 | LLM output normalisation, schema/prompt coverage, GPT-5 request options (fake client) |
| `Code/core/tests/test_rules.py` | 10 | Bundled YAML valid, rubric validation errors, runtime override |
| `Code/core/tests/test_deployment.py` | 7 | ARM template vs parameter files / UI definition / queue names |
| `Code/webapp/portal/tests.py` | 11 | Login redirect, admin pages render, htmx partial, audio range, agent isolation, settings validation, CRM API key, upload, demo-password lockout |

## Local-mode architecture (what actually runs)

| Concern | Local implementation |
|---|---|
| Storage | `LocalBlobStore`: folders under `LOCAL_DATA_DIR/storage/<container>/` (`recordings`, `transcripts`, `insights`, `config`) |
| Call store | `SqliteCallStore` at `LOCAL_DATA_DIR/callstore.sqlite3` |
| Speech | `MockSpeechClient`: **ignores the audio bytes**; returns the sidecar `mock_script`, the demo script for `DEMO-000x`, or a scenario chosen from the file name |
| Language | `MockLanguageClient`: regex PII + known sample names; lexicon sentiment |
| LLM | `MockInsightsClient`: keyword heuristics, same JSON shape; `normalize()` validates it |
| Events | `NullPublisher` (`EVENTS_BACKEND=none`); `event_payload()` builds the CRM event |
| Orchestration | `LocalDispatcher` thread pool in the web process |
| Auth | `AUTH_MODE=dev`: persona picker, no password. `AUTH_MODE=password` also exists (shared `DEMO_PASSWORD`, 5-failure lockout) |

Pipeline statuses: `uploaded → transcribing → transcribed → enriching → completed`, or `failed` (with `stage` and `error`).
Retry resumes at `enrich` if the transcript exists, else re-runs from `transcribe`.
By design (SECURITY.md, architecture.md), the `transcripts` container holds the **raw** (unredacted) transcript; everything users,
the CRM API, Cosmos/SQLite, Parquet and insights JSON receive is redacted.

## Roles (`Code/webapp/accounts/roles.py`)

| Permission | Admin | Supervisor | QA | Agent |
|---|---|---|---|---|
| view_all_calls / view_insights / view_agents | ✔ | ✔ | ✔ | ✘ (own calls only, by `agent_id` or `agent_email`) |
| manage_pipeline (Pipeline page, upload, retry) | ✔ | ✔ | ✘ | ✘ |
| edit_settings | ✔ | ✘ | ✘ | ✘ |

Dev personas: `dev-admin`, `dev-supervisor`, `dev-qa`, and `dev-A1001` … `dev-A1006` (agents from `scenarios.yaml`).

## Web routes (`config/urls.py`, `accounts/urls.py`, `portal/urls.py`)

| Route | View | Access |
|---|---|---|
| `GET/POST /auth/login` | persona sign-in (`?next=` checked by `url_has_allowed_host_and_scheme`) | public |
| `/auth/entra`, `/auth/callback` | Entra ID (redirects to login when not in entra mode) | public |
| `POST /auth/logout` | flush session | POST only |
| `/healthz` | `ok` | public |
| `/` | Overview (`?range=7\|30\|90\|all`, `?from&to`, `?team`) | signed in |
| `/calls/` | list, filters `q, agent, team, reason, sentiment, resolution, status, flag, qa_min, qa_max`, `sort`, `page` (25/page), htmx partial | signed in (agents: own) |
| `/calls/<id>/` | detail: transcript, sentiment, emotion, summary, reason, root cause, QA, compliance, coaching, metrics | can_view_call |
| `/calls/<id>/audio` | audio with HTTP Range | can_view_call |
| `POST /calls/<id>/retry` | re-run pipeline (`next` param) | manage_pipeline |
| `/agents/`, `/agents/<id>/`, `/agents/me/` | leaderboard / agent scorecard | view_agents; agent: own id or `me` |
| `/insights/`, `/insights/powerbi-token` | built-in charts / Power BI token | view_insights |
| `/pipeline/`, `POST /pipeline/upload` | status, failures, test upload | manage_pipeline |
| `/settings/?file=` | edit `qa_rubric.yaml`, `compliance_checks.yaml`, `call_reasons.yaml` | edit_settings |
| custom 403 / 404 | `portal/error.html` (no custom 500 handler) | |

## CRM API (`portal/api.py`)

| Endpoint | Behaviour in code |
|---|---|
| `GET /api/calls` | `X-API-Key` in `CRM_API_KEYS` (or a session). Filters `from,to,range,agent,team,reason,status,flag,customer,limit` (≤1000). Returns `{"count","items"}` (summaries, no transcript/history) |
| `GET /api/calls/<id>` | Full document incl. redacted transcript, `history` removed. Unknown/not-visible → 404 JSON |
| Unauthorised | `401 {"error":"unauthorized"}` |
| Event feed | `EVENTS_BACKEND=eventhub` → `call.insights.completed` to Event Hubs (Azure only). Local: `NullPublisher`; payload built by `events.event_payload()` |

## Front end

Templates: `base.html`, `accounts/login.html`, `portal/{overview,calls,call_detail,agents,agent_detail,insights,insights_powerbi,pipeline,settings,error}.html`, partials `calls_table`, `pipeline_tables`, `range`.
Static: `css/app.css`, `js/app.js`, `img/ttbs-logo.png`; whitenoise middleware (finders when DEBUG, `CompressedManifestStaticFilesStorage` otherwise, needs `collectstatic` into `Code/webapp/staticfiles`).
External scripts from CDNs: htmx 2.0.4 (unpkg), Chart.js 4.4.1 (jsdelivr), powerbi-client (Power BI page only).
Escaping: Django autoescape; transcript via `pii` filter (escape, then wrap `[TOKEN]` in a span); chart data via `json_script`. No `|safe` in templates.
Sessions: signed cookies, `HttpOnly`, `SameSite=Lax`, 8 h; `Secure`, HSTS, nosniff only when not local.

## Sample data (`Data/`)

| Path | Contents |
|---|---|
| `Data/sample_calls/scenarios.yaml` | Company, 6 agents (A1001–A1006), 10 customers, 8 scenarios (billing dispute, card fraud, address change, plan upgrade, network escalation, payment failed, cancellation, repeat complaint) with optional/bad lines |
| `Data/demo_calls/DEMO-000{1..4}.wav/.json` + `scripts.yaml` | 4 scripted calls with expected results in `Data/demo_calls/README.md`: 0001 model call (positive, resolved, passes); 0002 compliance breaches (full card, CVV, OTP); 0003 angry repeat complaint, escalated; 0004 outbound B2B sales |
| `Config/qa_rubric.yaml`, `compliance_checks.yaml`, `call_reasons.yaml` | 20 QA parameters, compliance checks, reason taxonomy |

## Points that shape the test design

- Mock speech never looks at the audio, so "empty / silent / corrupt audio" can only be exercised by injecting a fake speech client (the documented behaviour in mock mode is that any upload becomes a sample conversation).
- Mock Language is regex-based; spoken PII in the demo scripts (spelled-out emails, CVV/OTP digits, dates in words, names not in `scenarios.yaml`) is a realistic test of it.
- `DEBUG` defaults to **true** in local mode, so error-page leakage must be tested with both defaults and `DJANGO_DEBUG=false`.
- Not available locally: Azure Speech/Language/OpenAI, Cosmos, Event Hubs, Entra ID, Power BI, Functions host, ARM deployment.
