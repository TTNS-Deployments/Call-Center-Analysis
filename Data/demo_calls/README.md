# Demo calls and test checklist

Four English call recordings for testing a deployment end to end. Together they exercise every feature, and
all 20 QA parameters, except real-time support (not built yet). All names and numbers in them are fictitious.

| Call | Recording | What it is designed to show |
|---|---|---|
| `DEMO-0001` | 88 s, **stereo** (agent left, customer right) | A model call: greeting, recording notice, identity check, fee disclosed, resolved, positive customer. Personal data: name, phone, date of birth, email. |
| `DEMO-0002` | 67 s, mono | A poor call: no recording notice, agent asks for the **full card number, CVV and OTP**, no empathy, no closing. Anxious customer, card fraud. |
| `DEMO-0003` | 72 s, mono | A difficult call: angry **repeat** complainant, cannot be fixed, **escalated** with a complaint reference. |
| `DEMO-0004` | 3 min, **stereo**, **outbound sales** | A strong B2B sales call: right-party check, introduction with permission, company profiling, all four of **BANT**, a price **objection** against a competitor quote, a **12-second hold** with permission asked and thanks given, **PM-meeting benefits**, a concrete **appointment** slot, details noted back, the lead tagged, and a summarised close. |

The audio was synthesised from [`scripts.yaml`](scripts.yaml) (the exact words spoken) with [`generate_audio.py`](generate_audio.py).
Each `.json` file holds the call details (agent, team, customer, channels) that the telephony system would send.

## 1. Upload the calls

**When deploying:** `Deployment/scripts/deploy.sh -e demo` uploads them automatically (`SEED=1` in
`Deployment/environments/demo.env`). Run `make setup` first on the machine you deploy from.

**Any time later** (after `make setup` and `az login`):

```bash
. .venv/bin/activate
export CC_MODE=azure
export AZURE_STORAGE_ACCOUNT_URL=$(az deployment group show -g rg-callcenter-demo -n callcenter-demo \
    --query properties.outputs.storageBlobEndpoint.value -o tsv)
python -m callcenter upload                      # all four calls
python -m callcenter upload --agent-email colleague@yourcompany.com   # DEMO-0001 assigned to that agent (first upload only)
```

**Or by hand:** web app → **Pipeline** → upload a `.wav` and type the details from its `.json` file
(tick *Stereo recording* for `DEMO-0001`).

**Rehearse locally without Azure:** `python -m callcenter upload` in local mode processes them with the simulated
services (the transcript is taken from `scripts.yaml`; scores are rough approximations of the real ones).

## 2. Expected results with the real Azure services

Language-model scores vary a little between runs; these are the outcomes the scripts are written to produce.

| | DEMO-0001 | DEMO-0002 | DEMO-0003 | DEMO-0004 |
|---|---|---|---|---|
| Call reason | Plan change or upgrade | Card services | Technical or service issue | Sales / new business |
| Outcome | Resolved | Resolved (card blocked, dispute raised) | Escalated | Follow-up (meeting booked) or resolved |
| Customer emotion | neutral → satisfied | anxious → relieved/satisfied | frustrated/angry → disappointed | neutral → satisfied |
| QA | High, passes | Low, fails (no notice, no verification, no empathy, no closing) | Medium–high (good empathy, but not resolved) | High, passes; nearly every parameter Fully met |
| Sales parameters (BANT, objection, PM meeting, appointment, company profiling, RPC) | N/A | N/A | N/A | **Rated** (Fully met) |
| Hold protocol / dead air | N/A | N/A | N/A | Fully met; *Measured from the recording* shows 1 silence of about 13 s |
| Recording notice | pass | **fail** | pass | pass |
| Identity verified first | pass | **fail** | pass | not applicable |
| No OTP / CVV / full card request | pass | **fail** | pass | pass |
| Fees disclosed | pass (799 per month stated) | not applicable | not applicable | pass (1,950 per user per month, charged annually) |
| Escalation offered | not applicable | not applicable | pass | not applicable |
| Flags | none | Compliance breach, Low QA score | Unhappy customer, Repeat contact, Unresolved | none |
| Redacted personal data | name, phone, date of birth, email | name, card number (OTP and CVV may also be masked) | name, phone | names |

## 3. Feature checklist

Sign in to the web app (you have the **Admin** role after `setup_entra.sh`). Tick each item.

**Pipeline and processing**
- [ ] **Pipeline** page: the four calls move from *uploaded / transcribing* to *completed*; the page refreshes every 5 seconds.
- [ ] **Stereo vs mono:** in `DEMO-0001` the AGENT/CUSTOMER labels follow the channels; in `DEMO-0002` and `DEMO-0003` they come from speaker separation (first voice = agent).
- [ ] **Failure and retry:** upload any non-audio file renamed to `broken.wav`. It fails at the *transcribe* stage, is retried automatically, then shows as *failed* with the error. Click **Retry** (it fails again, as expected), then delete it from the `recordings` container.

**Call detail** (open each call from **Calls**)
- [ ] Audio plays; clicking a transcript line or a point on the sentiment strip jumps to it; *Follow audio* highlights the current line.
- [ ] Personal data appears as grey labels (`NAME`, `PHONE`, `CARD`, `DATE`, `EMAIL`); the count shows above the transcript.
- [ ] Summary, reason, root cause, outcome, emotion start → end and sentiment scores match the table above.
- [ ] QA scorecard: each of the 20 parameters shows *Fully met / Partial / Not met / N/A* and a score out of 5; the QA % ignores N/A.
      Open *Why this rating* for the quote, the reasoning and the recommendation.
- [ ] `DEMO-0004`: BANT, objection handling, PM meeting, appointment and RPC are rated (not N/A); *Measured from the recording*
      shows one silence of about 13 s and *Hold Protocol / Dead Air* is rated on it.
- [ ] Compliance checklist: passes, failures and *not applicable* as in the table, with quotes as evidence.
- [ ] Coaching recommendations with a priority (High / Medium / Low) appear on `DEMO-0002` and `DEMO-0003`.

**Lists and dashboards**
- [ ] **Overview:** KPI tiles, trend chart, top reasons, *Needs attention* lists `DEMO-0002` and `DEMO-0003`, root causes, agent leaderboard.
- [ ] **Calls:** search for `fraud` or a call ID; filter by sentiment *Negative*, flag *Compliance breach*, QA range 0–60; sort by QA; date buttons 7d/30d/90d/All.
- [ ] **Agents:** four agents with QA, compliance and coaching focus. Open one for the **20-parameter evaluation** (avg score,
      Fully / Partial / Not met / N/A counts, % met against the team average, verdict, example quote, recommendation),
      the coaching recommendations and the QA trend.
- [ ] **Insights:** built-in charts (criteria, compliance by check, sentiment, outcome, emotion, reasons, root causes).

**Settings and re-analysis**
- [ ] **Settings → QA rubric:** change `pass_threshold: 75` to `90` → *Validate and save*. Open `DEMO-0003` → **Re-analyse** → it now shows *Below threshold* (if its score is under 90). Then *Reset to default* and re-analyse again.
- [ ] Validation: replace the rubric text with `criteria: []` → *Validate and save* → it is refused with an explanation.

**Access control**
- [ ] Sign out. A colleague with **no** role signs in → gets the demo default role (**Supervisor**): sees all calls and the Pipeline page, but no Settings.
- [ ] Agent view: assign a colleague the **Agent** role (Entra admin center → Enterprise applications → *Call Center Analysis (demo)* → Users and groups),
      run `python -m callcenter upload Data/demo_calls/DEMO-0001.wav --agent-email <their email>` (this updates the call details),
      then open `DEMO-0001` in the web app and click **Re-analyse** so the new agent email is applied. Have them sign in:
      they see only that call and **My scorecard**, and get *Access denied* on Agents, Insights, Pipeline and Settings.

**Integrations**
- [ ] **CRM API:**
  ```bash
  KEY=$(az keyvault secret show --vault-name <key-vault> -n crm-api-keys --query value -o tsv)
  curl -s -H "X-API-Key: $KEY" "https://<web app>/api/calls?flag=compliance_breach"
  curl -s -H "X-API-Key: $KEY" "https://<web app>/api/calls/DEMO-0002"
  curl -s -o /dev/null -w "%{http_code}\n" "https://<web app>/api/calls"     # 401 without a key
  ```
- [ ] **Data lake for Power BI:** in the storage account, `insights/curated/` has `calls`, `qa_scores`, `compliance` and `utterances` Parquet files for each call.

## What cannot be tested in the demo

| Feature | Why | How to test later |
|---|---|---|
| Real-time transcription and agent assist | Not built yet (phase 2) | – |
| Power BI report embedded in Insights | Report not built; embedding needs a Fabric/Embedded capacity | Build the report (see `Analytics/README.md`), set `powerBiWorkspaceId` / `powerBiReportId` |
| CRM event feed (Event Hubs) | Switched off in the demo settings to save cost | Set `deployEventHubs` to `true` and redeploy |
| Live telephony integration | No telephony system connected | Point the telephony server at the `recordings` container |
