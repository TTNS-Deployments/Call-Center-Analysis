# Azure Deployment Architecture

This document is the *Azure Deployment Architecture Document* deliverable (TSD §10.1a). It covers:

- the components and why each was chosen
- how data flows through the solution
- security
- scaling and resilience
- the path to Phase 2 (real time)

![Architecture](../imgs/architecture.svg)

## 1. Components

| Component | Azure resource (name pattern `<prefix>-<env>-<suffix>-*`) | Purpose | SKU parameter |
|---|---|---|---|
| Data lake | Storage account, StorageV2 with hierarchical namespace (ADLS Gen2) | Containers: `recordings`, `transcripts` (raw), `insights` (JSON + Parquet), `config` (runtime rubric overrides), `app-package` (Functions deployment) | `storageSku`, lifecycle days |
| Eventing | Event Grid system topic on the storage account, plus 2 Storage queues | Sends `BlobCreated` for `recordings/` to `recordings-events` and for `transcripts/` to `transcripts-events`. Queues give back-pressure, retries and poison handling. | – |
| Pipeline compute | Function app on the **Flex Consumption** plan (Python 3.12) | `transcribe` and `enrich` queue triggers, plus poison-queue handlers | `functionMaximumInstances`, `functionInstanceMemoryMB` |
| Speech to text | Azure AI Speech (`SpeechServices`) | Fast transcription API: synchronous, diarization or per-channel speakers | `speechSku` |
| PII and sentiment | Azure AI Language (`TextAnalytics`) | PII entity recognition (redaction) and sentiment for each utterance | `languageSku` |
| LLM insights | Azure OpenAI and a model deployment | Summary, reason, root cause, resolution, emotion, QA scorecard, compliance, as JSON-schema output | `openAiModelName/Version`, `openAiDeploymentSku`, `openAiCapacity`, `openAiLocation` |
| Operational store | Cosmos DB for NoSQL (`callcenter/calls`, partition `/id`) | One document per call: status, metadata, insights, redacted transcript. Read by the web app and the CRM API. | `cosmosCapacityMode`, `cosmosThroughput` |
| Analytics store | Parquet under `insights/curated/` | Tables `calls`, `qa_scores`, `compliance` and `utterances`, read by Power BI | – |
| Downstream feed | Event Hubs namespace + `call-insights` hub (optional) | One event per analysed call, for the CRM and the real-time phase | `deployEventHubs`, `eventHubSku`, `eventHubCapacity` |
| Web app | App Service (Linux, Python 3.12) running Django + gunicorn | UI, Entra ID sign-in, CRM REST API, Power BI embed tokens | `webAppSku`, `webAppInstanceCount` |
| Secrets | Key Vault (RBAC mode) | Django secret key, Entra client secret, CRM API key | `keyVaultSku` |
| Monitoring | Log Analytics + Application Insights | Logs, traces and failures for both apps | `logAnalyticsRetentionDays`, `logAnalyticsDailyQuotaGb` |
| Identity | Entra ID app registration and enterprise app | User sign-in (OIDC) and app roles: Admin, Supervisor, QA, Agent | `entraClientId` |

**Differences from the TSD estimate.** The TSD estimate listed GPT-3.5-Turbo in East US. This template defaults to a current model (`gpt-4.1-mini`) in the solution region, for two reasons:

- GPT-3.5-Turbo is retired.
- Processing PII-derived text outside India conflicts with the data-residency expectations in the TSD.

Both are parameters. Azure Functions run on Flex Consumption because the Linux Consumption plan is being retired.

## 2. Data flow

1. **Ingest.** The telephony server writes `recordings/<call_id>.json` (metadata, optional) and then `recordings/<call_id>.wav`. The file name is the call ID.
2. **Transcribe** (Function `transcribe`). The audio goes to the Speech fast transcription API, and the result goes to `transcripts/<call_id>.json`. The call status moves from `transcribing` to `transcribed`. Speakers are mapped to *agent* and *customer*:
   - **Stereo recordings:** by channel.
   - **Mono recordings:** by diarization. The first speaker is assumed to be the agent; this is configurable per call.
3. **Enrich** (Function `enrich`, triggered by the new transcript blob):
   1. Azure AI Language detects PII in each utterance. The text is redacted with placeholders such as `[PHONE]` and `[CARD]`, and sentiment is scored on the redacted text.
   2. Azure OpenAI receives **only redacted text**, together with the current rubric, compliance checks and reason taxonomy, and returns strict JSON.
   3. The output is validated: scores are clamped, and missing items are filled in.
   4. The call document is written to Cosmos DB, `insights/json/<call_id>.json` and four Parquet files. The status becomes `completed` and an event goes to Event Hubs.
4. **Consume.**
   - The web app reads Cosmos DB. Audio is streamed through the app, so the browser never gets storage access.
   - Power BI reads the Parquet folders.
   - The CRM uses `/api/calls` with an API key, or the Event Hubs feed.

**Failure handling.** Any exception marks the call `failed`, with the stage and the error message, and is re-raised. The queue then redelivers the message (5 attempts, then the `-poison` queue). The poison handler marks the call as failed permanently. Admins and supervisors can retry from the Pipeline page, which puts a message on the right stage's queue.

Duplicate `BlobCreated` deliveries are ignored, because a call that is already past stage 1 is not transcribed again. All outputs are overwritten on re-analysis, so processing is idempotent.

## 3. Security

- **No keys in configuration.** Functions and the web app authenticate to Storage, Cosmos DB (data-plane RBAC), Speech, Language, OpenAI and Event Hubs with their system-assigned managed identities. Key-based auth stays enabled only so that Power BI and local tools can connect; it can be disabled per service.
- **Least privilege.**
  - Function app: Storage Blob Data Owner, Queue Data Contributor and Table Data Contributor (all required by the Functions host on Flex), Cognitive Services User, Cognitive Services OpenAI User, Event Hubs Data Sender, Cosmos DB Data Contributor, and Monitoring Metrics Publisher.
  - Web app: Blob Data Contributor, Queue Data Message Sender, Key Vault Secrets User, and Cosmos DB Data Contributor.
- **PII minimisation.** Raw transcripts are deleted by a lifecycle rule (`rawTranscriptDeleteAfterDays`). Everything users see is redacted. Recordings move to the Cool tier and are deleted after the configured number of days.
- **Users.** Entra ID sign-in uses the auth code flow (MSAL). Sessions are signed, `HttpOnly` and `Secure` cookies, and there is no user database. Roles come from Entra app roles, and Agents are limited to calls whose `agent_email`/`agent_id` matches them.
- **Network.** Public endpoints use HTTPS only and TLS 1.2. Private endpoints and VNet integration are not part of the SOW, but can be added later without code changes.

## 4. Scaling and cost

| Service | How it scales |
|---|---|
| Functions | Scale out per queue length, up to `functionMaximumInstances`. This also caps concurrency against the Speech, Language and OpenAI rate limits. |
| Speech | Billed per audio hour. Fast transcription is synchronous, so each call holds one Function execution for roughly a fraction of the call's length. |
| OpenAI | Throughput is bounded by `openAiCapacity` (thousands of tokens per minute). A typical 5-minute call uses about 3–5k tokens. |
| Cosmos DB | Serverless by default (pay per request); switch to Provisioned for steady high volume. Transcripts and history are excluded from indexing to save RUs. |
| Web app | Scales by plan size and instance count. |

The cost drivers, in order, are usually:

1. Speech (audio hours)
2. The web app plan
3. Power BI licensing or capacity (outside the ARM template)
4. OpenAI tokens

## 5. Environments and DR

Each environment (`dev`, `test`, `uat`, `prod`) is one resource group deployed from its own parameters file. DR outside the primary region is out of scope (TSD §7). If you need it later:

- Use GZRS storage.
- Add a second Cosmos DB region.
- Redeploy the template into the paired region (the template is region-agnostic).

## 6. Phase 2: real-time

Phase 2 adds live transcription and agent assist, and reuses Phase 1 unchanged for the post-call analysis:

- **Audio stream.** The telephony platform forks the live audio (SIPREC/WebSocket) to a streaming service on Container Apps or App Service. That service uses the Speech SDK's continuous recognition.
- **Real-time insights.** Partial transcripts go to Event Hubs (already provisioned). A lightweight consumer runs the PII redaction and an agent-assist prompt (next best action, knowledge lookup), and pushes suggestions to the agent desktop over SignalR or Web PubSub.
- **Post-call.** When the call ends, the full recording lands in `recordings/` as today, so post-call analysis, QA, Power BI and the CRM feed stay exactly the same.
