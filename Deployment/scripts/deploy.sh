#!/usr/bin/env bash
# One-command deployment: infrastructure (ARM) + application code (+ optional Entra ID setup).
#
#   Deployment/scripts/deploy.sh -g <resource-group> -e <dev|prod> [options]
#
# Options
#   -g, --resource-group   resource group (created if missing)            [required]
#   -e, --environment      parameters file to use: Deployment/ARM/parameters/<env>.parameters.json [required]
#   -l, --location         region for the resource group (default: centralindia)
#   -s, --subscription     subscription ID or name (default: current az account)
#       --setup-entra      create the Entra ID app registration and roles (first deployment)
#       --seed             upload synthetic sample calls after deployment (demo / smoke test)
#       --skip-infra       only deploy code
#       --skip-code        only deploy infrastructure
#
# Prerequisites: Azure CLI (az login), rsync, zip, python3. The signed-in identity needs
# Owner (or Contributor + User Access Administrator) on the resource group.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCRIPTS="$ROOT/Deployment/scripts"
RG="" ENV="" LOCATION="centralindia" SUBSCRIPTION="" SETUP_ENTRA=0 SEED=0 SKIP_INFRA=0 SKIP_CODE=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--resource-group) RG="$2"; shift 2 ;;
    -e|--environment) ENV="$2"; shift 2 ;;
    -l|--location) LOCATION="$2"; shift 2 ;;
    -s|--subscription) SUBSCRIPTION="$2"; shift 2 ;;
    --setup-entra) SETUP_ENTRA=1; shift ;;
    --seed) SEED=1; shift ;;
    --skip-infra) SKIP_INFRA=1; shift ;;
    --skip-code) SKIP_CODE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "Unknown option $1" >&2; exit 1 ;;
  esac
done
[[ -n "$RG" && -n "$ENV" ]] || { echo "Usage: $0 -g <resource-group> -e <environment>  (see --help)" >&2; exit 1; }

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
  echo "==> Resource group $RG ($LOCATION)"
  az group create -n "$RG" -l "$LOCATION" -o none
  echo "==> Deploying infrastructure with $(basename "$PARAMS") (takes ~10 minutes)"
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
  echo "==> Uploading sample calls (processed by the Functions)"
  (
    cd "$ROOT"
    export CC_MODE=azure AZURE_STORAGE_ACCOUNT_URL="$(out storageBlobEndpoint)" COSMOS_ENDPOINT="$(out cosmosEndpoint)"
    python3 -m callcenter seed --count 20
  )
fi

echo
echo "Done. Web app: $WEB_URL"
[[ $SETUP_ENTRA -eq 1 ]] || echo "Sign-in is not configured yet if this is the first deployment: re-run with --setup-entra --skip-infra --skip-code"
