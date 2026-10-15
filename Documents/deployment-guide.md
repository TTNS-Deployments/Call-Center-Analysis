# Deployment Guide

## Prerequisites

| Requirement | Notes |
|---|---|
| Azure subscription | **Owner** on the target resource group (or Contributor + User Access Administrator), because the template creates role assignments. |
| Entra ID permissions | Ability to create app registrations (the default for members), and Cloud Application Administrator or similar to assign app roles to others. |
| Azure OpenAI | The subscription must have access and quota for the chosen model and deployment type in `openAiLocation`. Check *Azure AI Foundry → Quotas*. |
| Tools | Azure CLI 2.60+ (`az login`), bash, `rsync`, `zip` and `python3` (Linux, macOS, WSL or Cloud Shell). |
| Resource providers | `Microsoft.Web`, `Microsoft.CognitiveServices`, `Microsoft.DocumentDB`, `Microsoft.EventGrid`, `Microsoft.EventHub`, `Microsoft.KeyVault`, `Microsoft.OperationalInsights`, `Microsoft.Insights`, `Microsoft.Storage`. Register them with `az provider register -n <ns>`. |

## 1. Choose SKUs

Copy or edit `Deployment/ARM/parameters/<env>.parameters.json`. Every tier and size is set there; [Deployment/README.md](../Deployment/README.md) explains each parameter and its options. The values you are most likely to change are:

- `location` and `openAiLocation`. Central India is the default; confirm that the model is available there.
- `openAiModelName`, `openAiModelVersion`, `openAiDeploymentSku` and `openAiCapacity`.
- `webAppSku`, `functionMaximumInstances` and `cosmosCapacityMode`.
- The retention days for recordings and raw transcripts.

## 2a. Deploy with the script (recommended)

```bash
az login
az account set --subscription "<subscription>"
Deployment/scripts/deploy.sh -g rg-callcenter-dev -e dev -l centralindia --setup-entra
```

The script:

1. Creates the resource group.
2. Deploys `azuredeploy.json` with your parameters file. It also grants **you** data access, which it needs for seeding and Key Vault.
3. Packages the code and deploys it to the Function app and the web app. Both use remote build, so dependencies are installed by Azure.
4. With `--setup-entra`, runs `setup_entra.sh`. This creates the app registration with the redirect URI and four app roles, stores a client secret in Key Vault, sets `ENTRA_CLIENT_ID` on the web app, writes it into your parameters file, and assigns you **Admin**.

Re-running is safe. Use `--skip-infra` to deploy code only, `--skip-code` to deploy infrastructure only, and `--seed` to upload the 3 demo recordings in `Data/demo_calls` (see its README for the test checklist). `--seed` runs the `callcenter` package on your machine, so run `make setup` first (the script uses `.venv` automatically).

## 2b. Deploy with the button

The **Deploy to Azure** button in the README opens a portal form, built from `createUiDefinition.json`, with a dropdown for every SKU. It deploys the **infrastructure only**. Afterwards:

```bash
Deployment/scripts/deploy.sh -g <rg> -e <env> --skip-infra --setup-entra
```

If you created the app registration yourself, enter its client ID and secret in the form's *Sign-in* tab instead of running `--setup-entra`. Use the redirect URI `https://<web app>/auth/callback` and create app roles with the values `Admin`, `Supervisor`, `QA` and `Agent`.

> The button needs the template URLs to be publicly readable. For a private repository, publish the two JSON files as a Template Spec or to a storage account with a SAS token, and update the button URL.

## 2c. Deploy with GitHub Actions

`.github/workflows/deploy.yml` (manual run) deploys any environment through OIDC, with no stored passwords. Set it up once:

1. Create an app registration or user-assigned identity for GitHub. Add a federated credential for `repo:<org>/<repo>:environment:<env>`.
2. Grant it **Owner** on the resource group.
3. Create a GitHub environment for each target (`dev`, `prod`) with these variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID` and `AZURE_RESOURCE_GROUP`.
4. Run *Actions → Deploy → Run workflow*. Run `setup_entra.sh` once from your machine; GitHub's identity normally cannot create app registrations.

## 3. After deployment

1. **Sign in** at the web app URL. If you see "Sign-in is not configured", run `setup_entra.sh`.
2. **Assign roles:** *Entra admin center → Enterprise applications → Call Center Analysis (<env>) → Users and groups → Add assignment*. Assigning groups (for example *QA Team → QA*) is the easiest approach. Users without a role get `entraDefaultRole` (Agent by default), so they only see calls where `agent_email` matches their sign-in email.
3. **Smoke test:** go to Pipeline → Upload and upload any English call recording. It should move from *transcribing* to *completed* within about a minute.
4. **Connect the telephony server:** give it write access to the `recordings` container (see [the telephony contract](technical-specification.md#1-recording-ingestion)).
5. **Replace the sample rubric:** go to Settings and edit the QA rubric, compliance checks and call reasons to match the customer's approved scorecard and scripts.
6. **CRM:** read the API key from Key Vault (`crm-api-keys`) and call `GET https://<web app>/api/calls` with the header `X-API-Key: <key>`. For push delivery, give the CRM integration *Azure Event Hubs Data Receiver* on the `call-insights` hub.
7. **Power BI:** follow [Analytics/README.md](../Analytics/README.md), then set `powerBiWorkspaceId` and `powerBiReportId` in the parameters file and redeploy (or set the matching app settings).
8. **Defender for Cloud:** this is a subscription-level setting and is not part of the resource group template. Enable the plans the TSD lists (App Service, Storage, Cosmos DB, AI services, Key Vault) in *Defender for Cloud → Environment settings*.

## Moving from local mode to Azure

Locally, everything runs with mocks. Each mock has an Azure counterpart that is selected by environment variables, so you can connect services one at a time and test them before a full deployment. For example, run locally but use the deployed OpenAI:

```bash
LLM_BACKEND=azure AZURE_OPENAI_ENDPOINT=https://<name>.openai.azure.com AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini \
python -m callcenter process path/to/call.wav
```

Without `AZURE_OPENAI_KEY`, your `az login` identity is used. `deploy.sh` grants it *Cognitive Services OpenAI User*.

## Troubleshooting

| Symptom | Check |
|---|---|
| Deployment fails on the OpenAI deployment | The model, version or deployment type isn't available in `openAiLocation`, or there is no quota. Change the parameters or request quota. |
| Calls stay in *uploaded* or *transcribing* | Function app → Log stream / Application Insights. Check that the Event Grid subscriptions are *Succeeded* and that the `recordings-events` queue receives messages. |
| `403` from Speech, Language or OpenAI in the logs | Role assignments can take up to 10 minutes to propagate after the first deployment. Retry from the Pipeline page. |
| Calls *failed* at `transcribe` with HTTP 400 | Unsupported audio format or an empty file. Convert the recording to WAV/PCM. |
| Web app shows "Application Error" | App Service → Log stream. A Key Vault reference may not have resolved yet. Restart the app after the role assignments propagate. |
| Sign-in loop or `AADSTS50011` | The redirect URI must be exactly `https://<web app>/auth/callback`. Re-run `setup_entra.sh`. |
| A user signs in but sees no calls | Agents only see calls whose metadata `agent_email` equals their sign-in email. Give supervisors the Supervisor or QA role. |
