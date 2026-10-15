#!/usr/bin/env bash
# One-command deployment: infrastructure (ARM) + application code (+ optional Entra ID setup).
#
#   Deployment/scripts/deploy.sh -g <resource-group> -e <dev|prod> [options]
#
# Options
#   -g, --resource-group   resource group (created if missing); default RESOURCE_GROUP from the env file
#   -e, --environment      parameters file to use: Deployment/ARM/parameters/<env>.parameters.json [required]
#                          Deployment/environments/<env>.env (optional) supplies defaults for -g/-l/-s and flags
#   -l, --location         region for the resource group (default: centralindia)
#   -s, --subscription     subscription ID or name (default: current az account)
#       --setup-entra      create the Entra ID app registration and roles (first deployment)
#       --no-setup-entra   skip it even if the env file sets SETUP_ENTRA=1
#       --seed             upload the 3 demo recordings (Data/demo_calls) after deployment (needs `make setup`)
#       --skip-infra       only deploy code
#       --skip-code        only deploy infrastructure
#
# Prerequisites: Azure CLI (az login), rsync, zip, python3. The signed-in identity needs
# Owner (or Contributor + User Access Administrator) on the resource group.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS="$ROOT/Deployment/scripts"
RG="" ENV="" LOCATION="centralindia" LOCATION_SET=0 SUBSCRIPTION="" SETUP_ENTRA=0 NO_SETUP_ENTRA=0 SEED=0 SKIP_INFRA=0 SKIP_CODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--resource-group) RG="$2"; shift 2 ;;
    -e|--environment) ENV="$2"; shift 2 ;;
    -l|--location) LOCATION="$2"; LOCATION_SET=1; shift 2 ;;
    -s|--subscription) SUBSCRIPTION="$2"; shift 2 ;;
    --setup-entra) SETUP_ENTRA=1; shift ;;
    --no-setup-entra) NO_SETUP_ENTRA=1; shift ;;
    --seed) SEED=1; shift ;;
    --skip-infra) SKIP_INFRA=1; shift ;;
    --skip-code) SKIP_CODE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option $1" >&2; exit 1 ;;
  esac
done
[[ -n "$ENV" ]] || { echo "Usage: $0 -e <environment> [-g <resource-group>]  (see --help)" >&2; exit 1; }

# Optional per-environment variables (Deployment/environments/<env>.env). Command-line options win.
ENV_FILE="$ROOT/Deployment/environments/$ENV.env"
if [[ -f "$ENV_FILE" ]]; then
  echo "Using variables from $(basename "$ENV_FILE")"
  CLI_LOCATION="$LOCATION" CLI_SETUP_ENTRA="$SETUP_ENTRA" CLI_SEED="$SEED"
  # shellcheck disable=SC1090
  source "$ENV_FILE"   # may set RESOURCE_GROUP, LOCATION, AZURE_SUBSCRIPTION, SETUP_ENTRA, SEED
  [[ -z "$RG" ]] && RG="${RESOURCE_GROUP:-}"
  [[ "$LOCATION_SET" -eq 1 || -z "${LOCATION:-}" ]] && LOCATION="$CLI_LOCATION"
  [[ -z "$SUBSCRIPTION" ]] && SUBSCRIPTION="${AZURE_SUBSCRIPTION:-}"
  [[ "$CLI_SETUP_ENTRA" == "1" ]] && SETUP_ENTRA=1
  [[ "$CLI_SEED" == "1" ]] && SEED=1
fi
[[ "$NO_SETUP_ENTRA" -eq 1 ]] && SETUP_ENTRA=0
[[ -n "$RG" ]] || { echo "No resource group: pass -g or set RESOURCE_GROUP in $ENV_FILE" >&2; exit 1; }

PARAMS="$ROOT/Deployment/ARM/parameters/$ENV.parameters.json"
[[ -f "$PARAMS" ]] || { echo "Parameters file not found: $PARAMS" >&2; exit 1; }

[[ -n "$SUBSCRIPTION" ]] && az account set --subscription "$SUBSCRIPTION"
echo "Subscription: $(az account show --query name -o tsv)"

# Identity running the deployment gets data access (Storage, Key Vault, AI) for setup and seeding.
USER_TYPE="$(az account show --query user.type -o tsv)"
if [[ "$USER_TYPE" == "servicePrincipal" ]]; then
  DEPLOYER_ID="$(az ad sp show --id "$(az account show --query user.name -o tsv)" --query id -o tsv 2>/dev/null || true)"
  DEPLOYER_TYPE="ServicePrincipal"
else
  DEPLOYER_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
  DEPLOYER_TYPE="User"
fi
[[ -n "$DEPLOYER_ID" ]] || echo "Note: could not resolve the deploying identity; skipping its data-access role assignments."

DEPLOYMENT="callcenter-$ENV"
if [[ $SKIP_INFRA -eq 0 ]]; then
  # Reuse an existing resource group (e.g. one you were given Owner on); create it only if missing.
  if [[ "$(az group exists -n "$RG")" == "true" ]]; then
    echo "==> Using existing resource group $RG ($(az group show -n "$RG" --query location -o tsv))"
  else
    echo "==> Creating resource group $RG ($LOCATION)"
    az group create -n "$RG" -l "$LOCATION" -o none
  fi

  # Resource providers must be registered at subscription level (needs subscription rights).
  MISSING=""
  for ns in Microsoft.Web Microsoft.CognitiveServices Microsoft.DocumentDB Microsoft.EventGrid Microsoft.EventHub \
            Microsoft.KeyVault Microsoft.OperationalInsights Microsoft.Insights Microsoft.Storage; do
    state="$(az provider show -n "$ns" --query registrationState -o tsv 2>/dev/null || echo unknown)"
    [[ "$state" == "Registered" ]] || MISSING="$MISSING $ns($state)"
  done
  if [[ -n "$MISSING" ]]; then
    echo "WARNING: these resource providers are not confirmed as registered:$MISSING" >&2
    echo "         Ask a subscription Owner/Contributor to run: az provider register -n <namespace>" >&2
  fi

  echo "==> Deploying infrastructure with $(basename "$PARAMS")"
  az deployment group create -g "$RG" -n "$DEPLOYMENT" \
    --template-file "$ROOT/Deployment/ARM/azuredeploy.json" \
    --parameters @"$PARAMS" \
    --parameters deployerPrincipalId="$DEPLOYER_ID" deployerPrincipalType="$DEPLOYER_TYPE" \
    -o none
fi

out() { az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.$1.value" -o tsv; }
WEB_APP="$(out webAppName)"
FUNC_APP="$(out functionAppName)"
WEB_URL="$(out webAppUrl)"

if [[ $SKIP_CODE -eq 0 ]]; then
  echo "==> Packaging"
  "$SCRIPTS/package.sh"
  echo "==> Deploying Function app $FUNC_APP (remote build)"
  az functionapp deployment source config-zip -g "$RG" -n "$FUNC_APP" --src "$ROOT/build/functions.zip" --build-remote true -o none
  echo "==> Deploying web app $WEB_APP (remote build)"
  az webapp deploy -g "$RG" -n "$WEB_APP" --src-path "$ROOT/build/webapp.zip" --type zip -o none
fi

if [[ $SETUP_ENTRA -eq 1 ]]; then
  "$SCRIPTS/setup_entra.sh" -g "$RG" -e "$ENV"
fi

if [[ $SEED -eq 1 ]]; then
  echo "==> Uploading the 3 demo calls from Data/demo_calls (processed by the Functions)"
  (
    cd "$ROOT"
    PY="python3"
    [[ -x "$ROOT/.venv/bin/python" ]] && PY="$ROOT/.venv/bin/python"
    if ! "$PY" -c "import callcenter, azure.storage.blob" 2>/dev/null; then
      echo "Cannot seed: the callcenter package is not installed. Run 'make setup' and re-run with --skip-infra --skip-code --seed." >&2
      exit 1
    fi
    export CC_MODE=azure AZURE_STORAGE_ACCOUNT_URL="$(out storageBlobEndpoint)" COSMOS_ENDPOINT="$(out cosmosEndpoint)"
    "$PY" -m callcenter upload Data/demo_calls
  )
fi

echo
echo "Done. Web app: $WEB_URL"
[[ $SETUP_ENTRA -eq 1 ]] || echo "Sign-in is not configured yet if this is the first deployment: re-run with --setup-entra --skip-infra --skip-code"
