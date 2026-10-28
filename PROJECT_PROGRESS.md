# Project progress and context

Read this first when continuing work in a new session. Keep it updated after each piece of work.
Last updated: 2026-10-28 (Hindi call language; upload checks the file's real audio format).

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
| `Code/core/callcenter/prompts/insights_v3.md` | Current LLM prompt (v3 adds Hindi handling) |
| `Code/functions` | Azure Functions (Flex Consumption): queue triggers `transcribe`, `enrich` + poison handlers |
| `Code/webapp` | Django app (`accounts/` auth+roles, `portal/` views/API/Power BI embed, templates, static incl. `img/ttbs-logo.png`) |
| `Config/*.yaml` | 20-parameter QA rubric, compliance checks, call reasons (incl. `sales_opportunity`) |
| `Deployment/ARM/azuredeploy.json` | ARM template (47 entries; ~13 real resources for demo). Edit the JSON directly |
| `Deployment/ARM/parameters/{demo,dev,prod}.parameters.json` | SKU files per environment |
| `Deployment/environments/demo.env` | Demo deployment variables: RG `Vaibhav.Chaudhary`, `centralindia`, SETUP_ENTRA=0, SEED=1, CODE_VIA_ARM=1 |
| `Deployment/scripts/` | `deploy.sh` (infra + code + Entra + seed; reads `environments/<env>.env`; `--code-via-arm`, `--param k=v`), `deploy_code_arm.sh` (management-API-only code + seed), `setup_entra.sh`, `package.sh` (tar; `--vendor` bundles deps + collectstatic) |
| `Deployment/ARM/code-onedeploy.json` | `Microsoft.Web/sites/extensions` `onedeploy` (packageUri, remoteBuild false) for the Function app |
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
5. Token test in Cloud Shell: only `https://management.azure.com` works; appservice, graph, storage, vault FAILED.
6. **Implemented (pushed) the management-only path:** `deploy.sh --code-via-arm` (on via `CODE_VIA_ARM=1` in demo.env)
   -> `deploy_code_arm.sh`: `package.sh --vendor` (tested in Codespace: webapp.zip 96 MB with staticfiles,
   functions.zip 84 MB with `.python_packages`), upload with storage account key to `code-packages`, 1-year read SAS,
   Function app via ARM `code-onedeploy.json`, web app via `WEBSITE_RUN_FROM_PACKAGE` + `PYTHONPATH=/home/site/wwwroot/.packages`
   + startup `python -m gunicorn ... config.wsgi`, demo calls (JSON then WAV) with the account key. Not yet verified on Azure.
7. Entra: portal registration steps are in `Documents/deployment-guide.md` ("When only the management sign-in works");
   client ID/secret passed with `deploy.sh -e demo --param entraClientId=... --param entraClientSecret='...'`.

### Next steps

- User runs in Cloud Shell: `cd ~/Call-Center-Analysis && git pull && Deployment/scripts/deploy.sh -e demo --skip-infra`
  (code + seed). Then register the Entra app in the portal and run `deploy.sh -e demo --param entraClientId=... --param entraClientSecret='...'`
  (full infra + code again; set `--no-seed`-like behaviour by passing nothing: SEED=1 re-uploads demo calls, harmless).
- If onedeploy on Flex fails: alternative is uploading the zip into the Function app's deployment storage container.
- If the web app fails to start: check Log stream; likely PYTHONPATH / startup command.
- After success: put entraClientId into demo.parameters.json; follow `Data/demo_calls/README.md` checklist.

### Demo sign-in without Entra (added after the user said they lack app-registration rights)

- New `AUTH_MODE=password` (`Code/webapp/accounts/views.py`): demo personas (Admin/Supervisor/QA + one per agent found
  in Cosmos) and a shared `DEMO_PASSWORD`. Wrong password 5x locks the client IP for 15 minutes (per-worker LocMem cache).
- ARM: params `authMode` (entra|password, default entra) and `demoPassword` (Key Vault secret `demo-password`,
  created only when non-empty); `demo.parameters.json` sets `authMode=password`.
- User runs: `Deployment/scripts/deploy.sh -e demo --param demoPassword='<password>'` (infra + code + seed).

### Demo is live (2026-10-21)

- `deploy.sh -e demo --param demoPassword=...` succeeded: code on both apps, 4 demo calls uploaded, password sign-in works
  (user signed in as Admin). Web app: https://ccai-demo-gqausd-web.azurewebsites.net
- `Documents/user-guide.md` written (pages, roles, scoring, demo walkthrough script, customer FAQ, troubleshooting).
- Next: user goes through `Data/demo_calls/README.md` checklist; fix whatever the real services get wrong.

### Chatbot app (separate repo, separate Claude Code session)

- `DEMO_DEPLOYMENT_HANDOFF.md` (repo root) is the playbook for the user's chatbot repo (same structure): remove Entra
  sign-in (demo password), demo SKUs + demo.env, management-API-only deployment from Cloud Shell.
- Decisions: the chatbot web app shares this demo's **B1 plan `ccai-demo-gqausd-web-plan`** (separate web app); the
  chatbot gets **its own Azure OpenAI resource in southindia** with `gpt-5-mini` (shared 500K TPM quota; this demo uses 20K).
  Do not change the plan's SKU here without considering the chatbot.

### Hindi and upload format check (2026-10-28)

- Calls can be English (`en-US`), Hindi (`hi-IN`) or `auto` (Speech language identification between the two):
  upload form Language field / sidecar `"locale"` / `SPEECH_LOCALE` default. Each phrase carries its `locale`;
  Azure Language PII and sentiment run per phrase in `hi`/`en`; prompt `insights_v3` writes output in English and
  quotes evidence as spoken. The call document has `locale`, shown on the call page.
- Demo issue (2026-10-27): an "MP3" upload failed with a Speech API "unsupported" error. Root cause not confirmed
  (no Azure log access from the Codespace). Uploads now check the real format from the bytes (`callcenter/audio.py`):
  non-audio is rejected with a clear message, a mislabelled file (e.g. M4A named .mp3) is stored under its real
  extension, and Speech errors now show Azure's code/message plus the detected format. `.amr` is accepted.

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
