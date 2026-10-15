# Deployment

| File | Purpose |
|---|---|
| `ARM/azuredeploy.json` | The ARM template for every Azure resource, role assignment and app setting |
| `ARM/parameters/demo.parameters.json`, `dev.parameters.json`, `prod.parameters.json` | **The SKU files.** Pick the tier and size of every service per environment. Copy one to add an environment (`test`, `uat`). |
| `ARM/createUiDefinition.json` | Portal form used by the *Deploy to Azure* button (a dropdown per SKU) |
| `environments/<env>.env` | Optional deployment variables per environment (subscription, resource group, region, setup flags) read by `deploy.sh` |
| `scripts/deploy.sh` | Infrastructure + code in one command (`-e <env> [-g <rg>] [--setup-entra] [--seed]`) |
| `scripts/setup_entra.sh` | Entra ID app registration, app roles, client secret in Key Vault |
| `scripts/package.sh` | Builds `build/webapp.zip` and `build/functions.zip` |

Step-by-step instructions are in [Documents/deployment-guide.md](../Documents/deployment-guide.md).

## Demo environment

For a demo or proof of concept, use the `demo` files. They pick the cheapest settings that still run the whole pipeline:

| File | Contents |
|---|---|
| `ARM/parameters/demo.parameters.json` | SKUs: web app B1; Functions Flex Consumption, max 5 instances; Speech S0 and Language S (pay per use, no fixed cost); Azure OpenAI `gpt-4.1-mini` GlobalStandard, 20K TPM; Cosmos DB serverless; Event Hubs off; LRS storage; recordings deleted after 30 days, raw transcripts after 7; logs capped at 1 GB/day; users without a role get **Supervisor** (sees all demo calls) |
| `environments/demo.env` | Deployment variables: subscription, resource group (`rg-callcenter-demo`), region (`centralindia`), whether to set up Entra ID sign-in and whether to seed sample calls |

```bash
az login
Deployment/scripts/deploy.sh -e demo        # everything comes from the two demo files
```

After the first run, set `SETUP_ENTRA=0` in `demo.env` (each setup run adds a new client secret). Remove the demo with `az group delete -n rg-callcenter-demo`.

## SKU and sizing parameters

The values below are the defaults in the two parameter files. "Options" are the values the template accepts.

| Parameter | dev | prod | Options / notes |
|---|---|---|---|
| `location` | centralindia | centralindia | Any region with the services below |
| **Storage** | | | |
| `storageSku` | Standard_LRS | Standard_LRS | `Standard_LRS`, `Standard_ZRS`, `Standard_GRS`, `Standard_RAGRS`, `Standard_GZRS` |
| `recordingCoolAfterDays` | 30 | 30 | Move recordings to the Cool tier |
| `recordingDeleteAfterDays` | 90 | 365 | Delete recordings (must be greater than the Cool days) |
| `rawTranscriptDeleteAfterDays` | 30 | 90 | Delete raw, un-redacted transcripts |
| **AI services** | | | |
| `speechSku` | S0 | S0 | `S0`, `F0` (free: 5 audio h/month, one per subscription) |
| `languageSku` | S | S | `S`, `F0` (free: 5k records/month, one per subscription) |
| `openAiLocation` | centralindia | centralindia | Region of the Azure OpenAI resource; must offer the model |
| `openAiModelName` / `openAiModelVersion` | gpt-4.1-mini / 2025-04-14 | same | Any chat model that supports JSON-schema output |
| `openAiDeploymentSku` | GlobalStandard | GlobalStandard | `Standard` (in-region processing), `DataZoneStandard`, `GlobalStandard` |
| `openAiCapacity` | 30 | 100 | Thousands of tokens per minute (TPM). About 4k tokens per 5-minute call. |
| **Database** | | | |
| `cosmosCapacityMode` | Serverless | Serverless | `Serverless`, `Provisioned` |
| `cosmosThroughput` | 400 | 400 | RU/s, used only when Provisioned |
| `cosmosEnableFreeTier` | false | false | Provisioned only, one per subscription |
| **Compute** | | | |
| `webAppSku` | B1 | S1 | `B1`–`B3`, `S1`–`S3`, `P0v3`–`P3v3` |
| `webAppInstanceCount` | 1 | 1 | 1–10 |
| `functionPlanSku` | FC1 | FC1 | Flex Consumption (serverless) |
| `functionMaximumInstances` | 10 | 40 | Scale-out cap. Also limits parallel calls to Speech, Language and OpenAI. |
| `functionInstanceMemoryMB` | 2048 | 2048 | `512`, `2048`, `4096` |
| **Messaging** | | | |
| `deployEventHubs` | false | true | CRM event feed and real-time phase |
| `eventHubSku` / `eventHubCapacity` | Basic / 1 | Basic / 1 | `Basic`, `Standard` / throughput units |
| **Operations** | | | |
| `logAnalyticsRetentionDays` | 30 | 30 | 30–730 |
| `logAnalyticsDailyQuotaGb` | 1 | -1 | Daily ingestion cap (-1 = none) |
| `keyVaultSku` | standard | standard | `standard`, `premium` |
| **Application** | | | |
| `entraClientId` | (set by `setup_entra.sh`) | | App registration client ID |
| `entraDefaultRole` | Agent | Agent | Role for users without an assignment; `""` = deny |
| `powerBiWorkspaceId` / `powerBiReportId` | "" | "" | Enables the embedded report |
| `displayTimeZone` | Asia/Kolkata | Asia/Kolkata | |
| `tags` | owner, project | | Added to every resource |

The secrets `djangoSecretKey`, `crmApiKey` and `entraClientSecret` are **not** stored in the parameter files. The first two are generated on deployment, and the Entra secret is created by `setup_entra.sh`. All three are written to Key Vault.

## Mapping to the TSD estimate

| TSD line item | Template resource | Parameter(s) |
|---|---|---|
| Storage account, GPv2 with hierarchical namespace, LRS, Hot | Storage account (ADLS Gen2) | `storageSku` |
| App Service S1 Linux | Web app plan | `webAppSku=S1` (prod) |
| Azure Functions Consumption | Function app, Flex Consumption | `functionMaximumInstances`, `functionInstanceMemoryMB` |
| Azure Speech | Speech resource | `speechSku` |
| Azure OpenAI, GPT-3.5-Turbo, East US | Azure OpenAI + deployment (current model, in region by default) | `openAiModelName`, `openAiLocation`, `openAiDeploymentSku`, `openAiCapacity` |
| Azure Language | Language resource | `languageSku` |
| Event Hubs Basic, 1 TU | Event Hubs namespace + hub | `deployEventHubs`, `eventHubSku`, `eventHubCapacity` |
| Azure Monitor / Log Analytics | Log Analytics + Application Insights | `logAnalyticsRetentionDays`, `logAnalyticsDailyQuotaGb` |
| Cosmos DB for NoSQL | Cosmos DB account, database and container | `cosmosCapacityMode`, `cosmosThroughput` |
| Defender for Cloud | Subscription-level plan, enabled outside this template | see the deployment guide |
| Key Vault | Key Vault | `keyVaultSku` |

Power BI licensing or capacity (Pro/PPU licences, or a Fabric/Embedded capacity for embedding in the web app) is **not** in the TSD estimate and is not deployed by this template. See [Analytics/README.md](../Analytics/README.md).
