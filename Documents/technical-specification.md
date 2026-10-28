# Technical Specification

This is the *Technical Spec for each step and process of the application architecture* deliverable (TSD §10.1b). The code references are relative to the repository root.

## 1. Recording ingestion

**Contract with the telephony server**

| Item | Value |
|---|---|
| Destination | Storage account `<prefix><env><suffix>st`, container `recordings`, any folder path |
| Audio | `.wav` (preferred: PCM 8/16 kHz, mono or stereo), `.mp3`, `.ogg`, `.opus`, `.flac`, `.m4a`, `.webm`, `.wma`, `.aac`; up to 2 hours per file |
| Call ID | The file name without extension, which must be unique, e.g. `recordings/2026/10/01/CALL-123456.wav` gives the ID `CALL-123456` |
| Metadata (optional) | A JSON file with the same base name, **uploaded before the audio**: `recordings/2026/10/01/CALL-123456.json` |
| Authentication | A managed identity or service principal with *Storage Blob Data Contributor* on the container, or a SAS token scoped to `recordings` with write-only permission |

The metadata file has this shape. All fields are optional:

```json
{
  "agent_id": "A1001",
  "agent_name": "Priya Sharma",
  "agent_email": "priya.sharma@contoso.com",
  "team": "Team Alpha",
  "customer_id": "CUST-00042",
  "direction": "inbound",
  "started_at": "2026-10-01T10:15:00+05:30",
  "channels": 2,
  "agent_channel": 0,
  "first_speaker": "agent"
}
```

- `agent_email` is matched to the signed-in user's email, so that agents see their own calls.
- `started_at` is normalised to UTC. If it is missing, the processing time is used.
- With `channels: 2`, speakers are assigned by channel (`agent_channel`, default 0). Otherwise diarization is used and `first_speaker` decides who the first voice is.

## 2. Transcription (`callcenter/speech.py`, Function `transcribe`)

- **API:** `POST {SPEECH_ENDPOINT}/speechtotext/transcriptions:transcribe?api-version=2024-11-15` (fast transcription), sent as multipart audio plus a definition. The definition contains:
  - `locales`: the call's `locale` from the metadata (`en-US` or `hi-IN`; `auto` sends both so Speech identifies the language), else `SPEECH_LOCALE` (default `en-US`)
  - for stereo audio, `channels: [0,1]`
  - otherwise `diarization: {enabled: true, maxSpeakers: 2}`
- **Auth:** a managed identity bearer token. `SPEECH_KEY` is used instead if it is set.
- **Output:** `transcripts/<call_id>.json` contains `phrases[{speaker, offset_ms, duration_ms, text, confidence, locale}]`, `locale` (the language spoken longest), `duration_ms`, `metadata` and `source`.
- **Mock:** in local mode the transcript comes from the sample script in the metadata, or is generated deterministically from the file name.

## 3. PII redaction and sentiment (`callcenter/language.py`)

- **PII:** Azure AI Language `recognize_pii_entities`, in batches of 5 utterances, language `en`. Entities with confidence ≥ 0.5 are redacted.
  - These categories are ignored because they carry no personal data and are useful context: `Organization`, `Quantity`, `PersonType`, `URL`. The list is set by `PII_IGNORE_CATEGORIES`.
  - `DateTime` entities are redacted only when they contain a year. This catches dates of birth while keeping durations like "three days".
  - Adjacent entities of the same category are merged, so "[NAME] [NAME]" becomes "[NAME]".
- **Placeholders:**

  | Placeholder | Covers |
  |---|---|
  | `[NAME]` | Person |
  | `[PHONE]` | PhoneNumber |
  | `[EMAIL]` | Email |
  | `[CARD]` | CreditCardNumber |
  | `[ACCOUNT]` | bank account numbers |
  | `[ADDRESS]` | Address |
  | `[DATE]` | DateTime with a year |
  | `[PAN]` | Indian PAN |
  | `[AADHAAR]` | Aadhaar |

- **Sentiment:** `analyze_sentiment` on the **redacted** text, in batches of 10. The label and confidence scores are kept for each utterance, and the utterance score is `positive − negative`, a value between −1 and 1.
- **Call-level sentiment:**
  - Customer score: the mean of the customer utterance scores. The label is positive above 0.15, negative below −0.15, and neutral in between.
  - The agent score and an end-of-call customer score (the last 3 utterances) are also stored.

## 4. LLM insights (`callcenter/llm.py`, prompt `callcenter/prompts/insights_v1.md`)

- **Request:** `chat.completions` with `response_format = json_schema (strict)`. GPT-5 models get `reasoning_effort` (default `low`, setting `AZURE_OPENAI_REASONING_EFFORT`) and no `temperature`, which they do not accept; a non-reasoning model (`none`) gets `temperature=0`. The schema is built from the current configuration, so the IDs of criteria, checks and reasons are enums.
- **System prompt:** the rules, then the reason taxonomy, the QA criteria with `max_points` and type, the compliance checks with kind, `applies_when` and approved wording, and the resolution definitions.
- **User message:** the call ID, the direction and the transcript as `[mm:ss] AGENT|CUSTOMER: text`.
- **Output fields:**
  - text fields: `summary`, `call_reason_detail`, `root_cause`
  - enums: `call_reason`, `resolution` (`resolved`, `follow_up`, `escalated` or `unresolved`), and `customer_emotion` `{start, end, dominant}` drawn from 9 emotions
  - `repeat_contact` (boolean)
  - `qa[{id, score, evidence, rationale}]` and `compliance[{id, status: pass|fail|not_applicable, evidence}]`
  - lists: `topics`, `follow_up_actions`, `coaching_tips`
- **Validation (`normalize`):** scores are clamped to 0..`max_points`. Missing criteria or checks are added, with a score of 0 or a status of fail/n/a. Unknown enum values fall back to safe defaults.
- **Versioning:** each document records `prompt_version`, `model` and `token_usage`. To change the prompt, add a new `insights_vN.md` (current: `insights_v3`, which handles Hindi calls) and update `PROMPT_VERSION`, so results stay traceable.

## 5. QA scoring and compliance

- **Configuration:** `Config/qa_rubric.yaml`, `Config/compliance_checks.yaml` and `Config/call_reasons.yaml` are validated on save. The web app's Settings page saves an override to the `config` container, and the override takes precedence over the repository defaults.
- **Ratings:** every parameter is rated *Fully met* (score 5, 100% credit), *Partial* (3, 50%), *Not met* (0, 0%) or *N/A* (the situation did not arise; excluded). The scale is set under `ratings:` in the rubric.
- **QA %:** weighted average credit over the applicable parameters only. A call passes when the result is ≥ `pass_threshold` (default 75); a call where every parameter is N/A has no QA %.
- **Measured evidence:** before the LLM call, the pipeline measures agent words per minute, talk share, silences of 10 s or more and talk-overs from the transcript timings (`callcenter/metrics.py`). Parameters with `source: audio_metrics` are judged with these values; `source: crm` parameters are judged only from what is said on the call.
- **Agent view:** per parameter across calls: counts of Fully met / Partial / Not met / N/A, % met, average score, a verdict (Strength ≥ 80%, Inconsistent ≥ 50%, otherwise Needs improvement; set under `verdicts:`), an example quote and the most frequent recommendation. Coaching recommendations carry a priority (High / Medium / Low).
- **Compliance:** a call passes when no check has the status `fail`. `prohibited` checks fail when the behaviour *is* detected.
- **Flags:**

  | Flag | Raised when |
  |---|---|
  | `unhappy_customer` | Customer score < −0.15, or a negative end emotion |
  | `repeat_contact` | The LLM reports a repeat contact |
  | `unresolved` | The resolution is unresolved or escalated |
  | `compliance_breach` | Any compliance check failed |
  | `low_qa` | The call did not pass QA |

- **UAT calibration:** the success criteria target 80–90% agreement with human reviewers. To measure it:
  1. Export a sample of calls from `/api/calls/<id>`.
  2. Have QA staff score the same calls.
  3. Compare the results per criterion.
  4. Tighten the criterion `description` text and re-analyse.

## 6. Storage outputs

**Cosmos DB document** (`callcenter/pipeline.py::build_call_document`). The main fields are:

```
id, call_id, status, stage, attempts, error, audio_blob, transcript_blob, insights_blob,
agent_id, agent_name, agent_email, team, customer_id, direction, started_at (UTC ISO), call_date, duration_sec,
summary, call_reason, call_reason_name, call_reason_detail, root_cause, resolution,
sentiment{overall, score, customer, customer_score, agent_score, customer_end_score},
emotion{start, end, dominant},
qa{score, max, percent, passed, criteria[{id, name, category, type, score, max_points, evidence, rationale}]},
compliance{passed, failed_count, checks[{id, name, kind, status, evidence}]},
flags{...}, flag_list[], pii{total, categories{}}, topics[], follow_up_actions[], coaching_tips[],
transcript[{speaker, start_ms, end_ms, text (redacted), sentiment, score}],
history[{at, event}], processed_at, prompt_version, model, token_usage, sources{speech, language, llm}
```

**Parquet** (`callcenter/parquet.py`). Each table is written to `insights/curated/<table>/YYYY/MM/DD/<call_id>.parquet`, one file per call per table, and is overwritten on re-analysis.

| Table | Grain | Key columns |
|---|---|---|
| `calls` | one row per call | call_id, call_date, agent, team, reason, resolution, sentiment, emotion, QA, compliance, flags |
| `qa_scores` | one row per criterion | call_id, criterion_id, name, category, type, score, max_points, percent |
| `compliance` | one row per check | call_id, check_id, name, kind, status |
| `utterances` | one row per utterance | call_id, seq, speaker, start_ms, end_ms, text (redacted), sentiment |

**Event Hubs** (`callcenter/events.py`). The event `call.insights.completed` carries the call ID, agent, team, customer, time, duration, summary, reason, root cause, resolution, flags, QA %, sentiment and compliance pass. The transcript is not included.

## 7. Web app (`Code/webapp`)

- **Stack:** Django, server-rendered templates, htmx (live filters and pipeline refresh) and Chart.js. There is no SPA build step. Gunicorn runs on App Service.
- **Auth** (`accounts/`):
  - `AUTH_MODE=entra` uses the MSAL auth code flow, takes roles from the `roles` claim, and assigns `ENTRA_DEFAULT_ROLE` when a user has no role.
  - `AUTH_MODE=dev` uses demo users and only works when `CC_MODE=local`.
- **Pages:** Overview, Calls, Call detail (audio streamed with HTTP Range support), Agents, My scorecard, Insights (Power BI or built-in), Pipeline (status, retry, upload) and Settings (rubric editor).
- **CRM API:** `GET /api/calls` accepts `from`, `to`, `agent`, `team`, `reason`, `status`, `flag`, `customer` and `limit`. `GET /api/calls/<id>` returns the full document without its history. Authenticate with the `X-API-Key` header (Key Vault secret `crm-api-keys`, comma-separated for rotation).
- **Power BI:** the "app owns data" model. The service principal gets a View embed token, which the browser refreshes before it expires. See `Analytics/README.md`.

## 8. Testing

- `Code/core/tests`: redaction, sentiment, rule validation, LLM output normalisation, the end-to-end pipeline (mock services, Parquet output), failure and retry, timestamp handling, and consistency between the ARM template, the parameter files and the UI definition.
- `Code/webapp/portal/tests.py`: sign-in redirects, every page for every role, Agent data isolation, htmx partials, audio Range requests, settings validation, the CRM API key, and upload to processing.
- Load and security testing (TSD §8, "Testing of applications"):
  - Upload N sample calls with `python -m callcenter seed --count N` against the deployed environment, and watch the Function scale-out and failure rate.
  - Run an OWASP ZAP baseline scan against the web app URL.
