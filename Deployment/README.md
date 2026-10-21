# Deployment

| File | Purpose |
|---|---|
| `ARM/azuredeploy.json` | The ARM template for every Azure resource, role assignment and app setting |
| `ARM/parameters/demo.parameters.json`, `dev.parameters.json`, `prod.parameters.json` | **The SKU files.** Pick the tier and size of every service per environment. Copy one to add an environment (`test`, `uat`). |
| `ARM/createUiDefinition.json` | Portal form used by the *Deploy to Azure* button (a dropdown per SKU) |
| `environments/<env>.env` | Optional deployment variables per environment (subscription, resource group, region, setup flags) read by `deploy.sh` |
| `scripts/deploy.sh` | Infrastructure + code in one command (`-e <env> [-g <rg>] [--setup-entra] [--seed]`) |
| `scripts/setup_entra.sh` | Entra ID app registration, app roles, client secret in Key Vault |
| `scripts/package.sh` | Builds `build/webapp.zip` and `build/functions.zip` (`--vendor`: all Python libraries included) |
| `scripts/deploy_code_arm.sh` | Code + demo calls through the management API only (`deploy.sh --code-via-arm`), for Cloud Shell / Conditional Access |
| `ARM/code-onedeploy.json` | Small template that deploys a zip to the Function app (`onedeploy`), used by `deploy_code_arm.sh` |

Step-by-step instructions are in [Documents/deployment-guide.md](../Documents/deployment-guide.md).

## Demo environment

For a demo or proof of concept, use the `demo` files. They pick the cheapest settings that still run the whole pipeline:

| File | Contents |
|---|---|
| `ARM/parameters/demo.parameters.json` | SKUs: web app B1; Functions Flex Consumption (max 40 instances, the lowest Flex allows; billed per execution); Speech S0 and Language S (pay per use, no fixed cost); Azure OpenAI `gpt-5-mini` GlobalStandard, 20K TPM, reasoning effort low; Cosmos DB serverless; Event Hubs off; LRS storage; recordings deleted after 30 days, raw transcripts after 7; logs capped at 1 GB/day; users without a role get **Supervisor** (sees all demo calls) |
| `environments/demo.env` | Deployment variables: subscription, resource group (`Vaibhav.Chaudhary`), region (`centralindia`), Entra setup off (app registered in the portal), seed sample calls, `CODE_VIA_ARM=1` (management-API-only code deployment) |

```bash
az login
Deployment/scripts/deploy.sh -e demo        # everything comes from the two demo files
```

Sign-in: register the app in the portal and pass its ID and secret with `--param` (see [the deployment guide](../Documents/deployment-guide.md#when-only-the-management-sign-in-works-cloud-shell-conditional-access)). If your tenant allows Microsoft Graph and App Service tokens, you can instead set `SETUP_ENTRA=1` and `CODE_VIA_ARM=0`. Remove the demo by deleting the resources in the resource group.

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
| `openAiLocation` | southindia | southindia | Region of the Azure OpenAI resource; must offer the model and have quota. Central India offers no GPT-5 models (checked October 2026) |
| `openAiModelName` / `openAiModelVersion` | gpt-5-mini / 2025-08-07 | same | A GPT-5 family chat model offered in the region (check with `az cognitiveservices model list -l <region>`) |
| `openAiApiVersion` | 2025-04-01-preview | same | API version the Functions use; must support the model |
| `openAiReasoningEffort` | low | low | `minimal`, `low`, `medium`, `high`; `none` for a non-reasoning model |
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
| `functionMaximumInstances` | 40 | 40 | Scale-out cap (Flex Consumption: 40-1000). Billed per execution, not per instance. |
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
