# Project progress and context

Read this first when continuing work in a new session. Keep it updated after each piece of work.
Last updated: 2026-10-21 (demo deployment in progress; code upload blocked by Cloud Shell token issue).

## What this is

Call Center Analysis: Azure post-call analytics built from the TTNS Technical Solution Document
("Call center Analysis", Tata Tele Business Services). Recordings are transcribed (Azure AI Speech),
PII is redacted and sentiment scored (Azure AI Language), then Azure OpenAI produces summary, call
reason, root cause, resolution, emotion, a 20-parameter QA evaluation, coaching and compliance checks.
Results go to Cosmos DB (app/CRM) and Parquet (Power BI). A Django web app shows them, with Microsoft
Entra ID sign-in.

Repository: https://github.com/TTNS-Deployments/Call-Center-Analysis (**public**), branch `main`.
User works in a GitHub Codespace (`/workspaces/Call-Center-Analysis`) and deploys from **Azure Cloud Shell**.

## Decisions agreed with the user

- Local mode first with mock AI services; real Azure services swapped in per service.
- English only for now (customer's real calls look like Hinglish outbound B2B sales: future work).
- Django for backend AND frontend (server-rendered templates + htmx + Chart.js), full web app.
- ARM templates (not Bicep) with one SKU parameters file per environment + Deploy to Azure button.
- Entra ID sign-in with app roles Admin / Supervisor / QA / Agent.
- App Service (not Container Apps) for the web app; Docker is only for local dev.
- **GPT-5 family only** (user: GPT-4 deprecated). `gpt-5-mini` 2025-08-07, GlobalStandard, API version
  2025-04-01-preview, `AZURE_OPENAI_REASONING_EFFORT=low` (reasoning models get no temperature).
- **QA = the customer's 20-parameter evaluation** (from their "Parameters for call center analysis" PDF):
  Fully met (5) / Partial (3) / Not met (0, assumed) / N/A (excluded); QA % = weighted credit over applicable
  parameters; "Incorrect / Falsifying information" weight 2 (assumed); per-parameter recommendation; prioritised
  coaching (High/Medium/Low); agent page shows the parameter table with verdicts (Strength >= 80, Inconsistent >= 50).
- Client PDF in TTBS format: `Call-Center-Analysis-Artifacts.pdf` in the repo root (official TTBS logo; Microsoft
  partner badges are drawn stand-ins).

## Where things are

| Path | What |
|---|---|
| `Code/core/callcenter` | Pipeline (`pipeline.py`), clients (`speech.py`, `language.py`, `llm.py`), `metrics.py` (wpm, dead air, talk-overs), storage, Cosmos/SQLite store, Parquet, analytics, CLI (`python -m callcenter seed/upload/process/worker/retry/status/reset`) |
| `Code/core/callcenter/prompts/insights_v2.md` | Current LLM prompt |
| `Code/functions` | Azure Functions (Flex Consumption): queue triggers `transcribe`, `enrich` + poison handlers |
| `Code/webapp` | Django app (`accounts/` auth+roles, `portal/` views/API/Power BI embed, templates, static incl. `img/ttbs-logo.png`) |
| `Config/*.yaml` | 20-parameter QA rubric, compliance checks, call reasons (incl. `sales_opportunity`) |
| `Deployment/ARM/azuredeploy.json` | ARM template (47 entries; ~13 real resources for demo). Edit the JSON directly |
| `Deployment/ARM/parameters/{demo,dev,prod}.parameters.json` | SKU files per environment |
| `Deployment/environments/demo.env` | Demo deployment variables: RG `Vaibhav.Chaudhary`, `centralindia`, SETUP_ENTRA=1, SEED=1 |
| `Deployment/scripts/` | `deploy.sh` (infra + code + Entra + seed; reads `environments/<env>.env`), `setup_entra.sh`, `package.sh` (tar, no rsync) |
| `Data/demo_calls/` | 4 demo recordings (Piper TTS) + JSON metadata + `scripts.yaml` + `generate_audio.py` + `README.md` test checklist |
| `Analytics/` | Power BI model as text (Power Query, DAX incl. 20-parameter measures). No .pbix yet |
| `Documents/` | architecture, technical spec, deployment guide, configuration, local development |
| `artifact-build/` (git-ignored) | PDF source + `build.py`. Rebuild: `.venv/bin/python artifact-build/build.py`, copy PDF to repo root |
| `brand/` (git-ignored) | `ttbs-logo.png` (official), `ms-partner-*.svg` (stand-ins) |

Tests: `make test` (core 36, webapp 8). CI on GitHub is green.

## Demo environment settings (final)

- Region: everything `centralindia`, **except Azure OpenAI in `southindia`** (`openAiLocation`), because
  Central India offers no GPT-5 models. Verified in the user's subscription: only quota is
  `gpt-5-mini` 2025-08-07 GlobalStandard = 500K TPM; all other GPT-5 GlobalStandard quotas are 0;
  `gpt-5.1-mini` does not exist.
- SKUs: web B1; Functions Flex Consumption (max 40, the Flex minimum; 2 GB); Speech S0; Language S;
  OpenAI capacity 20K TPM; Cosmos serverless; Event Hubs off; storage LRS; logs 30 days / 1 GB/day;
  `entraDefaultRole=Supervisor`.
- Demo calls: DEMO-0001 stereo model service call; DEMO-0002 compliance breaches (CVV/OTP/full card);
  DEMO-0003 angry repeat complaint, escalated; DEMO-0004 3-min outbound B2B sales call (all 20 params, 12 s hold).

## Azure environment facts (from the user)

- Subscription "Microsoft Azure Sponsorship"; user now has **Contributor on the subscription** and Owner on RG
  `Vaibhav.Chaudhary` (centralindia). User object ID `f0cd8504-4ace-43a5-b486-80367227c3a6`.
- All 10 resource providers registered (Web, CognitiveServices, DocumentDB, EventGrid, Storage, KeyVault,
  OperationalInsights, Insights, EventHub, AlertsManagement).
- **Device-code sign-in is blocked by Conditional Access**, so this Codespace cannot sign in to Azure
  (az CLI 2.91 installed here, signed out). Everything runs in **Azure Cloud Shell** (Bash, storage mounted,
  repo cloned at `~/Call-Center-Analysis`, Python 3.12.15, make, zip available).
- Cloud Shell token broker problems: "Timeout waiting for token from portal" for audiences
  `https://appservice.azure.com` (every time) and apparently Microsoft Graph (`az ad signed-in-user show` failed);
  `https://management...` works (with an occasional timeout).

## Deployment status (2026-10-21)

1. `Deployment/scripts/deploy.sh -e demo` run in Cloud Shell twice.
2. **Infrastructure deployed successfully** (deployment `callcenter-demo`). Names use suffix `gqausd`,
   e.g. Function app `ccai-demo-gqausd-func`, web app `ccai-demo-gqausd-web`.
3. Second run (after commit b2150e4) resolved the user's object ID from the ARM token, so the deployer's
   Storage / Key Vault / AI / Cosmos data roles are now assigned.
4. **Blocked:** code upload `az functionapp deployment source config-zip` fails with
   "Timeout waiting for token from portal. Audience: https://appservice.azure.com". Not yet done after it:
   web app code upload, `setup_entra.sh` (needs Graph: likely same token problem), demo-call upload (SEED=1).
5. Last thing asked of the user (no answer yet): run in Cloud Shell
   `for aud in https://management.azure.com https://appservice.azure.com https://graph.microsoft.com https://storage.azure.com https://vault.azure.net; do printf "%-32s " "$aud"; az account get-access-token --resource "$aud" -o none 2>/dev/null && echo OK || echo FAILED; done`
   and try `az login --scope "https://appservice.azure.com/.default"`; if that works, run
   `Deployment/scripts/deploy.sh -e demo --skip-infra`.

### Next steps if the App Service / Graph tokens keep failing (plan, not yet implemented)

- **Code upload via ARM only** (management audience): package the zips (pre-installing Python deps with
  `pip install --target` in Cloud Shell, Python 3.12 / linux x64, if remote build is unavailable), upload them to the
  deployment storage using the account key fetched via ARM (`az storage account keys list`, shared key is enabled)
  and a SAS URL, then deploy with an ARM template containing `Microsoft.Web/sites/extensions` named `onedeploy`
  (`packageUri`, type zip) for the Function app and the web app. Verify the onedeploy properties before relying on them.
- **Entra app registration in the portal** instead of `setup_entra.sh`: create app "Call Center Analysis (demo)",
  web redirect URI `https://<web app>/auth/callback`, ID tokens enabled, app roles Admin/Supervisor/QA/Agent
  (values exactly), client secret -> Key Vault secret `entra-client-secret` (portal), then
  `az webapp config appsettings set -g Vaibhav.Chaudhary -n ccai-demo-gqausd-web --settings ENTRA_CLIENT_ID=<appId>`,
  put the client ID in `demo.parameters.json` (`entraClientId`), assign the user the Admin role (Enterprise apps).
- **Demo calls**: `python -m callcenter upload` needs a storage-audience token; if that fails, upload the 4 WAV +
  JSON files from `Data/demo_calls` to the `recordings` container (JSON first) in the portal, or with
  `az storage blob upload --auth-mode key` using the account key.
- After success: set `SETUP_ENTRA=0` in `demo.env`; follow `Data/demo_calls/README.md` checklist.

## Open items / ideas

- Power BI report not built (option: generate a PBIP project). Embedding needs Fabric/Embedded capacity (not in TSD estimate).
- Official Microsoft Solutions Partner badge images not yet provided.
- Each infra deployment regenerates the CRM API key and Django secret (newGuid defaults); Entra secret valid 1 year.
- Hinglish / multilingual support, CRM data for "Remarks Capturing" and "Tagging" parameters.
- Assumptions to confirm with the user: "Not met" = 0 points; "Incorrect / Falsifying information" weight 2.
- Repo is public while the PDF footer says "strictly confidential" (user chose to publish, incl. demo.env with RG name).

## Working preferences

- User wants changes pushed to GitHub when asked ("push it"); deployment happens from Cloud Shell, so fixes must be pushed.
- Give copy-paste-ready commands for Cloud Shell (no placeholders to edit where avoidable).
- When the conversation context gets near full, update this file so work can continue in a new session.
