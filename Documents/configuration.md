# Configuration Reference

## Where each setting comes from

All runtime settings are environment variables:

| Environment | Source |
|---|---|
| Azure | App Service / Function app settings, set by the ARM template |
| Local | `.env` or your shell |

**Business rules** (the QA rubric, compliance checks and call reasons) are YAML files in `Config/`, editable at runtime from the web app (*Settings*). **Infrastructure sizes** are in `Deployment/ARM/parameters/<env>.parameters.json`.

## Pipeline (`callcenter` package: Functions, web app, CLI)

| Variable | Default (local / azure) | Description |
|---|---|---|
| `CC_MODE` | `local` | `local` or `azure`. Sets the default for every backend below. |
| `STORAGE_BACKEND` | `local` / `azure` | `local` = folder under `LOCAL_DATA_DIR/storage`; `azure` = Blob Storage (or Azurite). |
| `AZURE_STORAGE_ACCOUNT_URL` | – | `https://<account>.blob.core.windows.net/`, used with a managed identity or `az login`. |
| `AZURE_STORAGE_CONNECTION_STRING` | – | Alternative to the account URL (keys, Azurite: `UseDevelopmentStorage=true`). |
| `AZURE_STORAGE_QUEUE_URL` | derived | Queue endpoint, used to send retry messages. |
| `CONTAINER_RECORDINGS` / `_TRANSCRIPTS` / `_INSIGHTS` / `_CONFIG` | `recordings` / `transcripts` / `insights` / `config` | Container names. |
| `STORE_BACKEND` | `local` / `cosmos` | `local` = SQLite file; `cosmos` = Cosmos DB. |
| `COSMOS_ENDPOINT`, `COSMOS_KEY` | – | Without a key, the identity is used (data-plane RBAC). |
| `COSMOS_DATABASE`, `COSMOS_CONTAINER` | `callcenter`, `calls` | |
| `SPEECH_BACKEND` | `mock` / `azure` | |
| `SPEECH_ENDPOINT`, `SPEECH_KEY` | – | `https://<name>.cognitiveservices.azure.com`. |
| `SPEECH_LOCALE` | `en-US` | Recognition language. |
| `SPEECH_API_VERSION` | `2024-11-15` | Fast transcription API version. |
| `AGENT_CHANNEL` | `0` | Agent channel for stereo recordings (can be overridden per call in the metadata). |
| `LANGUAGE_BACKEND` | `mock` / `azure` | |
| `LANGUAGE_ENDPOINT`, `LANGUAGE_KEY` | – | |
| `PII_IGNORE_CATEGORIES` | `Organization,DateTime,Quantity,PersonType,URL` | PII categories that are not redacted. DateTime values that contain a year are always redacted. |
| `LLM_BACKEND` | `mock` / `azure` | |
| `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY` | – | |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-5-mini` | Deployment name. |
| `AZURE_OPENAI_API_VERSION` | `2025-04-01-preview` | Must support the model and `json_schema` structured output. |
| `AZURE_OPENAI_REASONING_EFFORT` | `low` | GPT-5 / o-series models: `minimal`, `low`, `medium` or `high` (sent instead of `temperature`). `none` = non-reasoning model, sends `temperature=0`. |
| `EVENTS_BACKEND` | `none` | `eventhub` publishes a `call.insights.completed` event per call. |
| `EVENTHUB_NAMESPACE`, `EVENTHUB_NAME` | –, `call-insights` | Fully qualified namespace (`<ns>.servicebus.windows.net`). |
| `AZURE_CLIENT_ID` | – | Client ID of a user-assigned managed identity, if one is used. |
| `LOCAL_DATA_DIR` | `<repo>/.localdata` | Local mode data folder. |
| `CC_CONFIG_DIR`, `CC_SAMPLE_DATA_DIR` | `<repo>/Config`, `<repo>/Data` | Override where the default rules and sample scripts are read from. |

## Functions only

| Variable | Description |
|---|---|
| `AzureWebJobsStorage__accountName` | Functions host storage (identity-based). |
| `PipelineStorage__queueServiceUri` | Queue endpoint for the triggers (identity-based connection `PipelineStorage`). |

Queue names are fixed in `function_app.py` (`recordings-events`, `transcripts-events` and their `-poison` queues) and match the ARM template.

## Web app only

| Variable | Default | Description |
|---|---|---|
| `DJANGO_SECRET_KEY` | dev key in local mode | **Required in Azure** (Key Vault reference). |
| `DJANGO_DEBUG` | `true` local / `false` azure | |
| `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` | localhost | The App Service host name is added automatically. |
| `AUTH_MODE` | `dev` local / `entra` azure | `dev` is refused unless `CC_MODE=local`. |
| `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID`, `ENTRA_CLIENT_SECRET` | – | App registration for sign-in. |
| `ENTRA_AUTHORITY` | `https://login.microsoftonline.com/<tenant>` | Override for sovereign clouds. |
| `ENTRA_DEFAULT_ROLE` | `Agent` | Role for users without an app role. Empty = deny access. |
| `CRM_API_KEYS` | – | Comma-separated keys accepted in the `X-API-Key` header. |
| `POWERBI_WORKSPACE_ID`, `POWERBI_REPORT_ID` | – | Enables the embedded report on the Insights page. |
| `POWERBI_TENANT_ID`, `POWERBI_CLIENT_ID`, `POWERBI_CLIENT_SECRET` | the `ENTRA_*` values | Service principal for embed tokens. |
| `DISPLAY_TIME_ZONE` | `Asia/Kolkata` | Time zone used to display dates. |
| `MAX_UPLOAD_MB` | `200` | Upload limit for the test upload form. |
| `LOG_LEVEL` | `INFO` | |

Key Vault references that cannot be resolved (for example a secret that has not been created yet) are treated as empty values, never as literal strings.

## Business rules (`Config/*.yaml`)

| File | Contents |
|---|---|
| `qa_rubric.yaml` | `pass_threshold`, `ratings` (score and credit for met / partial / not_met), `verdicts`, plus `criteria[]` with `id`, `name`, `category`, `type` (objective/subjective), `weight`, `source` (transcript / audio_metrics / crm) and `description`. The description is the rule the LLM rates against, including when the parameter is N/A. |
| `compliance_checks.yaml` | `checks[]` with `id`, `name`, `kind` (required/prohibited), `applies_when` (optional), `description` and `example_phrases`. |
| `call_reasons.yaml` | `categories[]` with `id`, `name` and `description`. Align these with the CRM disposition codes. |

Keys starting with `mock_` are only used by the local mock scorer.

Do not rename the `id` values after go-live: reports and Parquet history depend on them.
