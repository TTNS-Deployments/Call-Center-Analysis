# Handoff: demo deployment playbook (from Call-Center-Analysis)

**For a Claude Code session working on another TTNS repo** (the chatbot app). It records what was learned while
deploying the Call Center Analysis demo, so the same approach can be reused without rediscovering it. The source
repo is public: https://github.com/TTNS-Deployments/Call-Center-Analysis (branch `main`). Every file named below can
be read at `https://raw.githubusercontent.com/TTNS-Deployments/Call-Center-Analysis/main/<path>`, or at
`/workspaces/Call-Center-Analysis/<path>` if that clone is in the same Codespace.

## The task for the chatbot repo (as stated by the user)

The chatbot repo has the same structure as Call-Center-Analysis (modelled on `Azure/ai-solution-accelerators-list`):
`Code/`, `Config/`, `Deployment/ARM/azuredeploy.json` + `parameters/<env>.parameters.json`, `Deployment/scripts/`,
`Documents/`, and so on. The user wants to:

1. **Remove the Entra ID sign-in dependency for the demo.** The user has no rights to create app registrations, and
   Microsoft Graph is unreachable from their shell. Replace it with a demo sign-in protected by a shared password,
   switched on by an ARM parameter. Keep Entra ID as the default for non-demo environments. (Section 5)
2. **Create the demo environment files** with the cheapest possible SKUs: `Deployment/ARM/parameters/demo.parameters.json`
   and `Deployment/environments/demo.env`. (Sections 3 and 4)
3. **Deploy the demo** from Azure Cloud Shell using only the management API. (Sections 2 and 6)

First read the chatbot repo yourself: its template, scripts, app framework, auth code and which Azure services it
uses. Do not assume it matches this one in detail. Then adapt the patterns below.

## 1. The user and how they work

- They deploy for Tata Tele Business Services (TTBS). Their GitHub org is `TTNS-Deployments`.
- They want **copy-paste-ready commands** for Cloud Shell, with no placeholders to edit where that can be avoided.
  When a value has to be typed, say exactly where it comes from.
- They push to GitHub when asked ("push it"). Deployment runs from a `git pull` in Cloud Shell, so **every fix must
  be pushed** before they can use it.
- Keep a `PROJECT_PROGRESS.md` in the repo root (context, decisions, status, next steps). Update it after each piece
  of work and whenever the context window gets near full, so a new session can continue from it.
- Models: **GPT-5 family only.** The user considers GPT-4 deprecated for new projects.
- Pace: give a recommendation, not a survey of options. They answer questions quickly, but prefer that you just
  proceed with sensible defaults.

## 2. Azure environment facts (verified, October 2026)

| Fact | Detail |
|---|---|
| Subscription | "Microsoft Azure Sponsorship". The user has **Contributor on the subscription** and **Owner on resource group `Vaibhav.Chaudhary`** (region `centralindia`) |
| User object ID | `f0cd8504-4ace-43a5-b486-80367227c3a6` |
| Where commands run | **Azure Cloud Shell (Bash)** only. The Codespace cannot sign in to Azure: device-code login is blocked by Conditional Access. Cloud Shell has Python 3.12, pip, zip, make and git, but **no rsync** |
| **Cloud Shell tokens** | Only the audience `https://management.azure.com` works. Tokens for `https://appservice.azure.com` (SCM/Kudu: zip deploy, `az webapp deploy`, `az functionapp deployment source config-zip`), `https://graph.microsoft.com` (`az ad ...`), `https://storage.azure.com` (`--auth-mode login`) and `https://vault.azure.net` (`az keyvault secret ...`) all **FAIL** with "Timeout waiting for token from portal". So **everything must go through ARM / management calls** |
| Registered providers | Microsoft.Web, CognitiveServices, DocumentDB, EventGrid, Storage, KeyVault, OperationalInsights, Insights, EventHub, AlertsManagement. If the chatbot needs others (for example `Microsoft.Search` for AI Search, `Microsoft.App`, `Microsoft.ContainerRegistry`), the user can register them: `az provider register -n <ns> --wait` |
| Azure OpenAI | **Central India offers no GPT-5 models**, so the OpenAI resource goes in **`southindia`** (a separate `openAiLocation` parameter). The only quota available is **`gpt-5-mini` version `2025-08-07`, `GlobalStandard`, 500K TPM** in South India. The quota is **shared per subscription and region**, and the call-center demo already uses 20K of it. `gpt-5.1-mini` does not exist. Other GPT-5 GlobalStandard quotas are 0. **Check embedding models** (if the chatbot does RAG) before choosing: `az cognitiveservices model list -l southindia --query "[?contains(model.name,'embedding')].{n:model.name,v:model.version,s:model.skus[].name}" -o table` and `az cognitiveservices usage list -l southindia -o table` |
| gpt-5-mini calls | It is a reasoning model: send `reasoning_effort` (`low` for the demo) and **no `temperature`**. Use API version `2025-04-01-preview`. Strict `json_schema` output works |
| Existing demo | Call-center demo deployment `callcenter-demo` in `Vaibhav.Chaudhary`, resource name suffix `gqausd` (`ccai-demo-gqausd-web`, `ccai-demo-gqausd-func`, storage `ccaidemogqausdst`, B1 App Service plan). Use a **different solution prefix and deployment name** for the chatbot so nothing collides. Do not touch the `ccai-*` resources |

## 3. Demo SKU choices (lowest that still runs everything)

Make a `demo.parameters.json` next to the repo's existing parameter files, add `demo` to the `environmentName`
allowed values, and add a "demo" option to `createUiDefinition.json` if the repo has one. Choices used for the
call-center demo, plus likely chatbot services:

| Service | Demo choice | Notes |
|---|---|---|
| App Service plan (web app) | **No new plan: reuse the call-center B1 plan** (user's decision, see section 3a) | Billing is per plan, not per web app |
| Azure Functions | **Flex Consumption FC1**, `maximumInstanceCount` **40**, 2048 MB | **40 is the minimum Flex accepts**; lower values fail validation. Billed per execution |
| Azure OpenAI | `gpt-5-mini` 2025-08-07 GlobalStandard, **capacity 20** (20K TPM), in `southindia` | Pay per token |
| Embeddings (if RAG) | Smallest available embedding model in South India, about 10–20K TPM | Check availability and quota first (section 2) |
| Azure AI Search (if used) | **Free** tier if the subscription has no free search service yet (only one per subscription); otherwise **Basic** | Free has no semantic ranker and no private endpoints, and is limited to 50 MB / 3 indexes |
| Cosmos DB | **Serverless** | No minimum RU cost |
| Storage | Standard_LRS, short lifecycle rules | |
| Speech / Language | S0 / S (pay per use) | F0 is one per subscription |
| Event Hubs | Off (`deploy...=false`) | |
| Log Analytics | 30 days retention, 1 GB/day cap | |
| Key Vault | standard | |

## 3a. Share the existing App Service plan (user's decision)

The user wants the chatbot's web app on the **same App Service plan** as the call-center demo, so they pay for one
B1 instead of two.

- Existing plan: **`ccai-demo-gqausd-web-plan`**, Linux (`reserved: true`), **B1**, `centralindia`, resource group
  `Vaibhav.Chaudhary`. Resource ID:
  `az appservice plan show -g Vaibhav.Chaudhary -n ccai-demo-gqausd-web-plan --query id -o tsv`.
- In the chatbot template, add a parameter such as `existingAppServicePlanId` (string, default `""`). When it is
  set, **skip creating the plan** (`"condition": "[empty(parameters('existingAppServicePlanId'))]"` on the
  serverfarm) and use it as the web app's `serverFarmId`. Remove the plan from the web app's `dependsOn` in that
  case, or use a variable that resolves to either ID. Keep creating a plan for other environments. Put the ID in
  `demo.parameters.json`.
- It must be a **separate web app** (its own name, settings, managed identity, startup command and logs) on the
  same plan. Do not merge the two apps into one web app.
- The web app must be **Linux** (`kind: app,linux`, `linuxFxVersion` such as `PYTHON|3.12` or `NODE|20-lts`) and in
  the **same region** (`centralindia`) as the plan. The resource group is the same too.
- **Never modify the shared plan** from the chatbot template, neither its SKU nor its instance count. It belongs to
  the call-center deployment `callcenter-demo`, and its next infra run would set the plan back anyway.
- B1 has 1 vCPU and 1.75 GB RAM shared by both apps. Keep the chatbot light: about 2 gunicorn workers, no large
  in-process models. Both apps start slowly after a restart.
- The Function app (if the chatbot has one) is on its own Flex Consumption plan anyway. Flex plans are billed per
  execution, so a separate one costs nothing when idle.

## 4. `demo.env` and `deploy.sh` patterns

`Deployment/environments/demo.env` (read with `source` by `deploy.sh -e demo`; command-line options override it):

```bash
AZURE_SUBSCRIPTION=""                 # empty = current az account
RESOURCE_GROUP="Vaibhav.Chaudhary"    # existing RG the user owns; deploy.sh reuses it, never recreates it
LOCATION="centralindia"
SETUP_ENTRA=0                         # Graph is unreachable from Cloud Shell
SEED=1                                # upload demo/sample data, if the app has any
CODE_VIA_ARM=1                        # management-API-only code deployment (section 6)
```

Things `deploy.sh` needed (see `Deployment/scripts/deploy.sh` in the source repo):

- Read `environments/<env>.env`, with CLI flags winning.
- Reuse an existing resource group (`az group exists`) instead of creating one.
- Warn about unregistered resource providers.
- **Deployer object ID without Graph.** `az ad signed-in-user show` fails, so decode `oid` from the ARM token:
  ```bash
  oid_from_token() {
    az account get-access-token --query accessToken -o tsv 2>/dev/null | python3 -c '
  import base64, json, sys
  part = sys.stdin.read().strip().split(".")[1]
  part += "=" * (-len(part) % 4)
  print(json.loads(base64.urlsafe_b64decode(part)).get("oid", ""))' 2>/dev/null || true
  }
  ```
- `--param name=value` passthrough (repeatable) into `az deployment group create`, used for secrets such as
  `demoPassword`: `${EXTRA_PARAMS[@]+--parameters "${EXTRA_PARAMS[@]}"}`.
- `--code-via-arm` / `CODE_VIA_ARM=1` sends the code step to `deploy_code_arm.sh` and skips the Graph-based Entra setup.
- The final message should match the auth mode (no "run --setup-entra" hint for password mode).

## 5. Replacing Entra ID with a demo password (pattern used in the call-center web app)

What was built (Django). Adapt it to the chatbot's framework:

- An `AUTH_MODE` setting with values `entra` (default), `password` (demo) and `dev` (local only). A
  `password` mode with an empty `DEMO_PASSWORD` **refuses sign-in** and shows "not configured"; it never fails open.
- The login page in password mode: a password field plus a list of demo users, one per role. The selected user is
  stored in the session exactly as an Entra sign-in would be, so the rest of the app is unchanged.
- The password is compared with `hmac.compare_digest`. After 5 wrong passwords per client IP, sign-in is blocked
  for 15 minutes (Django cache; the IP is taken from `X-Forwarded-For` behind App Service).
- **ARM:** parameter `authMode` (`allowedValues: ["entra","password"]`, default `entra`) → app setting `AUTH_MODE`.
  Parameter `demoPassword` (securestring, default `""`) → Key Vault secret `demo-password`, created **only when the
  parameter is not empty** (`"condition": "[not(empty(parameters('demoPassword')))]"`), so redeploys keep it. The
  app setting is `DEMO_PASSWORD = @Microsoft.KeyVault(VaultName=<kv>;SecretName=demo-password)`. The web app's
  managed identity needs *Key Vault Secrets User*.
- An unresolved Key Vault reference arrives as the literal string `@Microsoft.KeyVault(...)`. The settings helper
  treats it as empty.
- `demo.parameters.json` sets `"authMode": {"value": "password"}`. The user sets the password once with
  `deploy.sh -e demo --param demoPassword='...'`.
- Tests: wrong password → 403, then 429 after 5 attempts; right password → signed in; empty password configured →
  refused. Source: `Code/webapp/accounts/views.py`, `Code/webapp/config/settings.py`,
  `Code/webapp/templates/accounts/login.html`, `DemoPasswordTests` in `Code/webapp/portal/tests.py`.

If the chatbot also uses Entra ID for **service-to-service** calls (managed identity to OpenAI or Search), keep
that: managed identities and RBAC role assignments are created by ARM and need no Graph access. Only the
**user sign-in** (app registration, MSAL) has to be replaced.

## 6. Deploying code with management calls only

Kudu/SCM zip deployment is impossible from Cloud Shell (no `appservice` token), so code goes in like this. Source:
`Deployment/scripts/deploy_code_arm.sh`, `Deployment/scripts/package.sh`, `Deployment/ARM/code-onedeploy.json`.

1. **Package with dependencies included** (`package.sh --vendor`), because no remote build happens:
   ```bash
   PIP_TARGET_FLAGS=(--quiet --disable-pip-version-check --only-binary=:all: --implementation cp --python-version 3.12
     --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --platform manylinux_2_28_x86_64 --upgrade)
   # Function app:  pip install "${PIP_TARGET_FLAGS[@]}" --target <stage>/.python_packages/lib/site-packages -r requirements.txt
   # Web app:       pip install "${PIP_TARGET_FLAGS[@]}" --target <stage>/.packages -r requirements.txt
   #                + build static assets INTO the package (Django: collectstatic), because run-from-package is read-only
   ```
   Use `tar -C src -cf - . | tar -C dst -xf -` instead of rsync. pip prints an `ERROR ... ansible-core requires
   packaging` dependency warning in Cloud Shell; it is harmless.
2. **Upload with the storage account key**, which the management API returns:
   `KEY=$(az storage account keys list -g $RG -n $STORAGE --query "[0].value" -o tsv)`, then
   `az storage container create/blob upload --account-key "$KEY"` into a `code-packages` container, and a read-only SAS
   valid for one year: `az storage blob generate-sas ... --permissions r --expiry <+365d> --https-only --full-uri`.
   Shared-key access must be enabled on the storage account (`allowSharedKeyAccess: true`).
3. **Function app (Flex Consumption):** deploy with an ARM deployment of a `onedeploy` extension. Verified working:
   ```json
   { "type": "Microsoft.Web/sites/extensions", "apiVersion": "2024-04-01",
     "name": "[format('{0}/onedeploy', parameters('siteName'))]",
     "properties": { "packageUri": "[parameters('packageUri')]", "remoteBuild": false } }
   ```
   Run it as `az deployment group create -g $RG -n code-functions-$STAMP --template-file code-onedeploy.json --parameters siteName=... packageUri=...`.
4. **Web app (Linux App Service):** run it from the package (verified working):
   ```bash
   az webapp config appsettings set -g $RG -n $WEB --settings WEBSITE_RUN_FROM_PACKAGE="<SAS URL>" \
     SCM_DO_BUILD_DURING_DEPLOYMENT=false PYTHONPATH=/home/site/wwwroot/.packages
   az webapp config set -g $RG -n $WEB --startup-file "python -m gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 2 --threads 4 config.wsgi"
   az webapp restart -g $RG -n $WEB
   ```
   Use `python -m gunicorn`, because the gunicorn script is not on PATH when the packages live in `.packages`. For
   FastAPI or Flask, change the startup command (for example
   `python -m gunicorn -k uvicorn.workers.UvicornWorker app.main:app`). For Streamlit, use `python -m streamlit run ...`
   with `--server.port 8000 --server.address 0.0.0.0`.
5. **An infrastructure deployment resets the web app's app settings**, which removes `WEBSITE_RUN_FROM_PACKAGE`. So
   `deploy.sh` always runs the code step after the infra step. Never run the infra step alone on a live demo.
6. **Seed data** (documents for a RAG index, sample files): upload with the account key in the same way. If seeding
   needs an index build (AI Search), check that it can run inside the app or a Function, triggered by the upload;
   Cloud Shell cannot call the data planes with an AAD token.

## 7. Other pitfalls already hit

- Validate ARM JSON before pushing. `az bicep decompile` or a compile check catches errors, and
  `python -c "import json; json.load(open(...))"` at least checks the syntax.
- The "Deploy to Azure" button showed **47 resources** for the call-center template. That is normal: it counts every
  template entry, including role assignments, child resources and conditional ones. Only about 13 real resources
  are deployed for the demo.
- Each infrastructure deployment regenerates `newGuid()` defaults (for example a Django secret key), which signs
  users out. That is fine for a demo.
- A first page load after a restart takes about 30 seconds on B1.
- Report outcomes honestly. When something has only been tested locally and not on Azure, say so.

## 8. Suggested order for the chatbot

1. Read the chatbot repo: template parameters and resources, scripts, app framework, auth flow, model names, tests.
2. Create `PROJECT_PROGRESS.md` there with the findings.
3. Switch models to GPT-5 if needed: `gpt-5-mini` in `southindia`, reasoning effort, no temperature, API version
   `2025-04-01-preview`. Check embeddings.
4. Add password demo sign-in (section 5) with tests.
5. Add `demo.parameters.json` (section 3, with `existingAppServicePlanId` for the shared plan from section 3a) and `demo.env` (section 4).
6. Add `--code-via-arm` and `--param` to `deploy.sh`, plus `deploy_code_arm.sh`, `package.sh --vendor` and
   `code-onedeploy.json` (section 6). Test the packaging locally (`package.sh --vendor` works in the Codespace too).
7. Update the docs (Deployment README, deployment guide), push, and give the user one Cloud Shell command:
   `cd ~ && git clone https://github.com/TTNS-Deployments/<repo>.git; cd ~/<repo> && git pull && Deployment/scripts/deploy.sh -e demo --param demoPassword='<password>'`
8. Help them verify: sign in, try each feature, and read the Log stream on errors.
