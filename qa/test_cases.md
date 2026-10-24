# Test cases (local mode)

Scope: what exists in this repository and runs with `CC_MODE=local`. Layers: Backend (Code/core, functions, API), UI (Django pages and server behaviour), Front end (static assets, templates, browser). Automated tests live in `qa/tests/` and are named after these IDs (`test_B05_...`).

| ID | Layer | Priority | Area | Steps | Expected result |
|---|---|---|---|---|---|
| B-01 | Backend | Critical | Existing tests (core) | make test (first half): .venv/bin/python -m pytest Code/core -q | All existing core tests pass. |
| B-02 | Backend | Critical | Existing tests (web app) | make test (second half): cd Code/webapp && manage.py test | All existing Django tests pass. |
| B-03 | Backend | High | Sample data / make seed | LOCAL_DATA_DIR=<tmp> make seed | 40 synthetic calls generated and analysed, none FAILED, exit 0. |
| B-04 | Backend | High | Transcription output | Seed 30 sample + 4 demo calls; read every transcripts/<id>.json. | Each has source, locale, duration_ms, call_id, audio_blob, transcribed_at and phrases; each phrase has speaker agent\|customer, increasing offset_ms, duration_ms>0, non-empty text, confidence 0-1; no mock_script in metadata. |
| B-05 | Backend | Critical | PII redaction (sample calls) | For 30 seeded calls extract raw names, phones, emails, card/account numbers, DOB, address from the raw script; search the call store transcript, summary/reason/root cause, QA/compliance/coaching, insights JSON, all 4 Parquet tables and the CRM event payload. | No raw PII value appears in any redacted output (placeholders [NAME], [PHONE], [EMAIL], [CARD], [ACCOUNT], [DATE], [ADDRESS] instead). |
| B-06 | Backend | Critical | PII redaction (spoken demo calls) | python -m callcenter upload (4 demo calls, local mode); check the redacted transcript/summary for the spoken PII in Data/demo_calls/scripts.yaml. | Spoken names, phone, email, DOB, card, CVV, OTP are absent from the stored transcript (README: OTP/CVV 'may' be masked). |
| B-07 | Backend | High | PII patterns (mock Language) | MockLanguageClient.analyze on: email, +91 phone, dashed and plain card, account number, PAN, 'My name is Zoya Khan', Flat ... PIN address. | Each value replaced by its placeholder and absent from the output. |
| B-08 | Backend | High | PII not in logs (pipeline) | Process 3 PII-heavy calls with root logging at DEBUG; scan captured records. | No raw PII in log records. |
| B-09 | Backend | High | Sentiment | Check sentiment block of all calls; compare DEMO-0001 (positive) vs DEMO-0003 (angry). | overall/customer in positive\|neutral\|negative, scores in [-1,1], per-utterance labels valid; DEMO-0001 positive, DEMO-0003 negative. |
| B-10 | Backend | High | Emotion | Check emotion {start,end,dominant} for all calls; DEMO-0001 vs DEMO-0003. | Values from the allowed emotion list; DEMO-0001 ends satisfied, DEMO-0003 starts angry/frustrated. |
| B-11 | Backend | High | QA criteria | Check qa block of all calls; DEMO-0001 (model call) vs DEMO-0002 (poor call). | 20 criteria in rubric order, valid ratings, percent 0-100 or None, counts sum to 20; DEMO-0001 passes, DEMO-0002 fails with low_qa flag. |
| B-12 | Backend | High | Compliance | Check compliance block of all calls; DEMO-0001 vs DEMO-0002. | All checks in config order with pass\|fail\|not_applicable; failed_count consistent; DEMO-0001 passes; DEMO-0002 fails recording disclosure and OTP/CVV prohibition, flag compliance_breach. |
| B-13 | Backend | High | Summary, call reason, root cause | Check summary, call_reason (+name), root_cause, resolution of all calls and expected reasons for the 4 demo calls. | Non-empty summary/root cause, reason from call_reasons.yaml, valid resolution; demo reasons plan_change / card_services / technical_issue / sales_opportunity; DEMO-0003 escalated and repeat contact. |
| B-14 | Backend | Critical | End to end | Upload one call + sidecar; pipeline.process_recording; spy on status changes; read store, insights JSON, Parquet, list. | Status transcribing -> transcribed -> enriching -> completed; stage done, no error; transcript, insights JSON, 4 Parquet tables written; call listed and retrievable. |
| B-15 | Backend | Medium | Empty file (mock mode) | Upload a 0-byte .wav and process. | Documented local behaviour: mock speech ignores audio and returns a sample conversation; no crash. |
| B-16 | Backend | Medium | Silent audio (empty transcript) | Inject a speech client that returns 0 phrases (what a silent recording yields); process. | A clear state: failed with a reason, or completed but recognisable as no speech, not scored as agent failures. |
| B-17 | Backend | Medium | Unsupported file | transcribe_recording('x.txt' / 'x.json'); web upload of notes.txt (U-22). | Ignored (None) and no call record; web upload refused with a message. |
| B-18 | Backend | High | Corrupt audio (Speech error) | Azure fast-transcription client with requests.post mocked to HTTP 400 InvalidAudioFormat (no network); process a garbage .wav. | Call status failed, stage transcribe, error mentions the Speech API error. |
| B-19 | Backend | Medium | Very long transcript | Inject 2,000 phrases (~3.3 h); process; time it. Render detail page (U-28). | Completes in < 60 s with all utterances stored; page renders. |
| B-20 | Backend | Medium | Missing metadata | Upload audio with no sidecar JSON; process. | Completes; started_at defaulted; agent fields empty, no crash. |
| B-21 | Backend | Medium | Missing fields in LLM output | Insights client returns {}; process. | normalize() fills defaults; call completes (reason general_enquiry, resolution unresolved). |
| B-22 | Backend | High | Duplicate call | Process the same blob twice (duplicate BlobCreated); web upload with an existing call ID (U-22). | Second run skipped, processed_at unchanged, one record; web refuses duplicate ID. |
| B-23 | Backend | High | Stage failure: transcribe + retry | Speech client raises ConnectionError; then pipeline.retry. | failed/transcribe with the error text; retry completes and clears error. |
| B-24 | Backend | High | Stage failure: enrich + retry | Language client raises TimeoutError; then retry, counting speech calls. | failed/enrich; retry resumes at enrich without re-transcribing. |
| B-25 | Backend | Medium | Corrupt intermediate transcript | Overwrite transcripts/<id>.json with invalid JSON; run enrich. | failed/enrich with a parse error, no crash of the caller beyond the raised error. |
| B-26 | Backend | Medium | CLI status / retry | python -m callcenter status; mark a call failed; status; retry <id>; retry NO-SUCH-CALL. | Counts per status; failed call listed; retry completes; unknown ID exits non-zero. |
| B-27 | Backend | Medium | Functions queue handlers | Load Code/functions/function_app.py; call transcribe() with a base64 Event Grid message, enrich(), an other-container event, transcribe_poison(). | Stages run; foreign container ignored; poison marks the call failed 'after all retries'; bad message raises ValueError. |
| B-28 | Backend | Critical | Config: local defaults | Fresh interpreter with no backend variables: print settings; manage.py check. | mode local, storage/store local, speech/language/llm mock, events none; Django check clean. |
| B-29 | Backend | High | Config: invalid web settings | Start Django with AUTH_MODE=bogus; CC_MODE=azure + AUTH_MODE=dev; CC_MODE=azure without DJANGO_SECRET_KEY. | Startup refused with a message naming the problem. |
| B-30 | Backend | High | Config: invalid pipeline backends | STORAGE/STORE/LLM_BACKEND=foo; LLM_BACKEND=azure and SPEECH_BACKEND=azure without endpoints. | Clear error naming the backend / missing variable. |
| B-31 | Backend | Low | Config: non-numeric numbers | AGENT_CHANNEL=left; MAX_UPLOAD_MB=lots. | Error names the variable. |
| B-32 | Backend | Low | Config: unknown CC_MODE | CC_MODE=prodution (typo). | Rejected with a clear error. |
| B-33 | Backend | Medium | Config: invalid rules override | Write invalid qa_rubric.yaml directly to the config container (bypassing the UI); process a call; remove it; retry. | Call failed with a ConfigError naming the problem; retry after fixing completes. |
| B-34 | Backend | Medium | Config: rules validation | save_config_text with bad compliance kind, reason without description, list instead of mapping. | ConfigError with a specific message; nothing saved. |
| B-35 | Backend | Critical | CRM API: valid key | GET /api/calls?range=all and /api/calls/DEMO-0001 with X-API-Key: local-test-key. | 200 JSON; list has count + items with call_id, status, agent_id, started_at, summary, call_reason, root_cause, resolution, sentiment, emotion, qa, compliance, flag_list (no transcript/history); detail adds transcript, no history. |
| B-36 | Backend | High | CRM API: filters | agent=A1004&limit=2; customer=CUST-20457; flag=compliance_breach. | Only matching calls; limit applied. |
| B-37 | Backend | Critical | CRM API: missing / wrong key | No key, wrong key, empty key, key as Bearer token, key in query string - on list and detail. | 401/403 JSON, no data. |
| B-38 | Backend | High | CRM API: unknown call ID | GET /api/calls/NO-SUCH-CALL with valid key. | 404 JSON. |
| B-39 | Backend | Medium | CRM API: malformed input | limit=abc, limit=-5, limit=1e3, from=not-a-date, from=2026-13-45&to=x, range=999, status=%00, agent=' OR 1=1 --. | Clean 4xx JSON error or the parameter ignored; never 500. |
| B-40 | Backend | Critical | CRM API: no PII in responses | For 30 sample calls compare raw script PII with /api/calls (all) and /api/calls/<id> responses. | No raw PII in list or detail JSON. |
| B-41 | Backend | High | CRM API: session + role scoping | Sign in as agent A1001 and call the API without a key. | Only own calls listed; other agents' call -> 404. |
| B-42 | Backend | Medium | Event feed payload | events.event_payload(DEMO-0002); NullPublisher.publish; EVENTS_BACKEND default. | type call.insights.completed with ids, summary, reason, qa_percent, sentiment, compliance_passed, flags; no transcript/PII; local publisher is a no-op. |
| B-43 | Backend | High | No hardcoded secrets | Regex scan of every tracked file for storage keys, key/secret/password/token literals, sk- keys, private keys, connection-string passwords. | No real secrets (documented public emulator keys allowed). |
| B-44 | Backend | High | .env git-ignored | git check-ignore .env; git ls-files; git log --all -- .env local.settings.json. | .env ignored, not tracked, never committed. |
| B-45 | Backend | Medium | Log statements don't print text | Static scan of logging calls in Code/ for transcript/phrase/text/doc arguments. | No log call interpolates call text. |
| B-46 | Backend | Medium | Docker local stack | docker compose -p qa-cc-smoke up --build -d; run --rm seed; GET /healthz, sign in, /calls/, API; down -v. | Image builds (collectstatic), web + Azurite start, 40 calls seeded through the real Blob SDK, pages 200. |
| U-01 | UI | High | Login page | GET /auth/login. | 200; demo personas Admin, Supervisor, QA and 6 agents listed. |
| U-02 | UI | Critical | Login each demo user | POST /auth/login user=<persona> for all 9. | 302 to /; overview 200 with Sign out. |
| U-03 | UI | High | Invalid login | Dev mode: unknown persona. Password mode (DEMO_PASSWORD set): wrong password; unknown user. | Rejected with a generic message; not signed in; no stack trace. |
| U-04 | UI | High | Login ?next= open redirect | Login with next=https://evil.example/, //evil.example/x, /calls/. | External targets replaced by /; local path kept. |
| U-05 | UI | High | Logout | GET /auth/logout; POST /auth/logout; then GET /calls/. | GET 405; POST 302 to login; session gone. |
| U-06 | UI | Critical | Protected pages need sign-in | Anonymous GET of every route incl. audio and Power BI token. | 302 to /auth/login?next=... |
| U-07 | UI | High | Protected POSTs need sign-in | Anonymous POST upload, retry, settings; GET /healthz. | 302 to login; healthz public 'ok'. |
| U-08 | UI | Critical | Role-based access matrix | Admin, Supervisor, QA, Agent(A1001): GET 12 routes (own/other call and audio, agents, own/other agent, me, insights, pipeline, settings) and POST upload/retry/settings. | Matrix per roles.py: Supervisor no Settings; QA no Pipeline/Settings/upload/retry; Agent only own calls/scorecard, 403 elsewhere. |
| U-09 | UI | Medium | Navigation per role | Read sidebar items per role. | Admin all 6; Supervisor no Settings; QA no Pipeline/Settings; Agent Overview, Calls, My scorecard. |
| U-10 | UI | Critical | Agent sees only own calls | Agent A1001: /calls/?range=all; owner of every listed call. | Only A1001 calls. |
| U-11 | UI | Critical | Pages load without errors | Each persona type: 18 URLs incl. range presets, team filter, builtin insights, 4 demo call pages. | No 5xx, no template errors. |
| U-12 | UI | High | Call list content | /calls/?range=all: header and DEMO-0003 row. | Columns incl. call, agent, summary, sentiment, resolution, QA; row shows reason, flags. |
| U-13 | UI | Critical | Call detail content | QA user opens /calls/DEMO-0003/. | Transcript (with redaction chips), sentiment, emotion start/end, QA score, all compliance checks, summary, reason, root cause. |
| U-14 | UI | High | Search / filter / sort | q (case-insensitive), agent, team, reason, sentiment, resolution, flag, qa_min/qa_max, sort=qa / -qa. | Only matching rows; sort order correct. |
| U-15 | UI | Medium | Empty state | /calls/?q=zzz; all list pages for an empty date range; agent with no calls. | 'No calls match these filters.'; pages 200. |
| U-16 | UI | Medium | Pagination | /calls/?range=all page 1/2; page=999, abc, -1. | 25 per page, no overlap, Next/Previous; bad page numbers handled. |
| U-17 | UI | Medium | Bad parameters | /calls/? qa_min=abc, qa_max=<script>, sort=;drop, range=bogus, from=2026-99-99, page=1e9, team=%00. | Friendly handling (ignored or message), never 500. |
| U-18 | UI | High | Invalid IDs (DEBUG=False) | /calls/NO-SUCH-CALL/, its /audio, /settings/?file=../../.env, /nope/, GET on retry. | Friendly 404 page ('Not found', Back to overview) / 405; no stack trace. |
| U-19 | UI | High | No debug leak with DEBUG=False | Trigger the 500 from U-17 with DEBUG=False. | Generic error page, no traceback/exception/paths. |
| U-20 | UI | Low | Debug pages with local defaults | Default local settings (DJANGO_DEBUG unset): /calls/NO-SUCH-CALL/ and the U-17 500. | No debug details. |
| U-21 | UI | High | Azure-mode secure defaults | CC_MODE=azure (+ secret, entra): read DEBUG, cookie flags, SSL redirect, HSTS. | DEBUG False; secure cookies; SSL redirect; HSTS. |
| U-22 | UI | High | Upload form | Supervisor: no file; .txt; existing call ID; size over MAX_UPLOAD_MB; valid upload with call_id 'QA-UP/../../x'. | Specific error messages; valid upload sanitised and processed to completed. |
| U-23 | UI | High | Retry from UI | Mark DEMO-0004 failed; Supervisor sees it on Pipeline; POST retry; unknown ID. | Failure shown with reason; retry completes; unknown -> 404. |
| U-24 | UI | Medium | Retry next= open redirect | Supervisor POST /calls/DEMO-0003/retry next=https://evil.example/phish. | Redirect stays on site. |
| U-25 | UI | Medium | Settings edit | Admin: save invalid YAML, valid YAML, reset. | 'Not saved' + reason; 'Saved.'; reset OK. |
| U-26 | UI | Medium | htmx partials | HX-Request on /calls/ and /pipeline/. | Fragments without <html>. |
| U-27 | UI | Medium | Audio streaming | Full GET, Range 100-199, suffix -10, unsatisfiable range. | 200 audio/*; 206 exact bytes; 416. |
| U-28 | UI | Medium | Edge-case calls render | No-metadata call, 2,000-utterance call, failed call: list, detail, pipeline, overview, agents. | All 200; long page fast. |
| F-01 | Front end | Medium | Templates: no missing variables | (a) string_if_invalid on pages with complete calls; (b) all pages incl. failed/no-metadata calls: visible text has no literal None or template syntax. | No unresolved variables; no 'None' shown. |
| F-02 | Front end | High | Static files | Collect every /static/ URL from 9 pages; fetch on runserver (DEBUG) and gunicorn DEBUG=false after collectstatic (whitenoise manifest). | All 200 with right content types; prod uses hashed names + long cache; missing file 404. |
| F-03 | Front end | High | Browser console / network | Chromium headless: Admin visits 10 pages (networkidle), Agent 4; record console errors, page errors, failed requests, HTTP >= 400. | None. |
| F-04 | Front end | Medium | Charts render | Overview, Insights, Agents, Agent pages: count canvases vs Chart.js instances. | Every canvas has a chart; Chart.js and htmx loaded. |
| F-05 | Front end | Medium | Live search (htmx) | Type DEMO-0002 in the search box. | Table updates via htmx to 1 row. |
| F-06 | Front end | Medium | Audio player | Open DEMO-0001; wait for audio metadata. | Duration ~88 s. |
| F-07 | Front end | High | Browser sign-in / sign-out | Click QA persona; click Sign out; open /calls/. | Name shown; signed out; redirected to login. |
| F-08 | Front end | High | CSRF | Every POST form on 10 pages has csrfmiddlewaretoken; POST /auth/login without token. | All forms have token; tokenless POST 403. |
| F-09 | Front end | Medium | Required fields / error messages | Pipeline: submit upload without file. Settings: save 'categories: []'. | Browser blocks (input :invalid); settings shows 'Not saved' + reason. |
| F-10 | Front end | Medium | Accessibility basics | 9 pages: title, html lang, viewport, img alt, input labels, button names, h1. | All present; titles distinct. |
| F-11 | Front end | Medium | Mobile width (375 px) | Chromium 375x812: scrollWidth - clientWidth on 9 app pages + login. | No horizontal scroll. |
| F-12 | Front end | High | Security headers / cookies | Sign in on runserver and on gunicorn DEBUG=false; inspect headers and Set-Cookie. | X-Frame-Options DENY, nosniff, Referrer-Policy, session cookie HttpOnly + SameSite=Lax, csrftoken SameSite=Lax. |
| F-13 | Front end | Low | Content-Security-Policy | Check CSP header. | A CSP is sent. |
| F-14 | Front end | Critical | Output escaping (XSS) | Create a call whose transcript, agent name and team contain <img onerror> and <script>; search with payloads; open detail, list, agent page, overview in Chromium. | Payloads shown as text; no dialogs; no injected elements. |
| F-15 | Front end | Low | Third-party scripts | Inspect external <script> tags. | Pinned versions with Subresource Integrity (or self-hosted). |
| F-16 | Front end | High | Live server logs free of PII | After the browser tests, scan runserver + gunicorn logs for raw PII. | None. |

## Out of scope

| Item | Reason |
|---|---|
| Real Azure AI Speech, Language and OpenAI | Needs keys and costs money; brief says mocks only. So real transcription of silent/corrupt audio, real PII accuracy and LLM scoring against Data/demo_calls/README.md expectations are unverified. |
| Cosmos DB call store, Blob Storage/ADLS on Azure, Key Vault references | Azure resources. SQLite and folder storage tested; Azurite path covered by B-46. |
| Event Hubs publishing (EVENTS_BACKEND=eventhub) | Azure only. Payload builder tested (B-42). |
| Microsoft Entra ID sign-in (AUTH_MODE=entra), app roles from tokens | Needs an app registration and credentials. |
| Power BI embedding / report (Analytics/) | Needs Power BI Embedded/Fabric capacity; no .pbix in repo. |
| ARM deployment, Deploy to Azure button, deploy.sh, deploy_code_arm.sh, setup_entra.sh, GitHub Deploy workflow | Real deployment. ARM/parameter consistency is covered by existing tests (B-01). |
| Private endpoints, VPN, Microsoft Defender, App Service networking, HTTPS/HSTS on the real host | Azure portal / deployment checks. |
| QueueDispatcher (Azure retry via Storage queues) | Needs Azure Storage queues; local retry path tested. |

Also not run (in scope but not executed): see *Not Tested* in `TEST_REPORT.md`.
