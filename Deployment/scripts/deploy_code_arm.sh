#!/usr/bin/env bash
# Deploys the application code (and optionally the demo calls) using ONLY the Azure management API.
#
#   Deployment/scripts/deploy_code_arm.sh -g <resource-group> -e <environment> [--seed]
#
# For environments where tokens for App Service (SCM), Storage, Key Vault or Graph cannot be obtained
# (e.g. Azure Cloud Shell under Conditional Access). It:
#   1. builds both packages with all Python dependencies included (package.sh --vendor; needs python3 + pip)
#   2. uploads them to the storage account using the account key (read via the management API)
#   3. Function app: deploys the zip with an ARM "onedeploy" extension (Deployment/ARM/code-onedeploy.json)
#   4. web app: runs from the package (WEBSITE_RUN_FROM_PACKAGE = read-only SAS URL, valid 1 year)
#   5. --seed: uploads Data/demo_calls (metadata JSON first, then audio) with the account key
# Run it again after every infrastructure deployment (the template resets the web app settings).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RG="" ENV="" SEED=0 SEED_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--resource-group) RG="$2"; shift 2 ;;
    -e|--environment) ENV="$2"; shift 2 ;;
    --seed) SEED=1; shift ;;
    --seed-only) SEED=1; SEED_ONLY=1; shift ;;   # only upload the demo calls
    *) echo "Unknown option $1" >&2; exit 1 ;;
  esac
done
[[ -n "$RG" && -n "$ENV" ]] || { echo "Usage: $0 -g <resource-group> -e <environment> [--seed]" >&2; exit 1; }

DEPLOYMENT="callcenter-$ENV"
out() { az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.$1.value" -o tsv; }
FUNC_APP="$(out functionAppName)"
WEB_APP="$(out webAppName)"
STORAGE="$(out storageAccountName)"
CONTAINER="code-packages"
STAMP="$(date -u +%Y%m%d%H%M%S)"

KEY="$(az storage account keys list -g "$RG" -n "$STORAGE" --query "[0].value" -o tsv)"

if [[ $SEED_ONLY -eq 0 ]]; then
echo "==> Packaging with dependencies included (takes a few minutes)"
"$ROOT/Deployment/scripts/package.sh" --vendor

echo "==> Uploading packages to $STORAGE/$CONTAINER (account key)"
az storage container create --account-name "$STORAGE" --account-key "$KEY" -n "$CONTAINER" -o none
EXPIRY="$(date -u -d '+365 days' '+%Y-%m-%dT%H:%MZ')"
sas_url() {
  az storage blob generate-sas --account-name "$STORAGE" --account-key "$KEY" -c "$CONTAINER" -n "$1" \
    --permissions r --expiry "$EXPIRY" --https-only --full-uri -o tsv
}
for name in functions webapp; do
  az storage blob upload --account-name "$STORAGE" --account-key "$KEY" -c "$CONTAINER" \
    -n "$name-$STAMP.zip" -f "$ROOT/build/$name.zip" --overwrite -o none
done
FUNC_URL="$(sas_url "functions-$STAMP.zip")"
WEB_URL_PKG="$(sas_url "webapp-$STAMP.zip")"

echo "==> Deploying Function app $FUNC_APP (ARM onedeploy)"
az deployment group create -g "$RG" -n "code-functions-$STAMP" \
  --template-file "$ROOT/Deployment/ARM/code-onedeploy.json" \
  --parameters siteName="$FUNC_APP" packageUri="$FUNC_URL" -o none

echo "==> Configuring web app $WEB_APP to run from the package"
az webapp config appsettings set -g "$RG" -n "$WEB_APP" -o none --settings \
  WEBSITE_RUN_FROM_PACKAGE="$WEB_URL_PKG" SCM_DO_BUILD_DURING_DEPLOYMENT=false \
  PYTHONPATH=/home/site/wwwroot/.packages
az webapp config set -g "$RG" -n "$WEB_APP" -o none \
  --startup-file "python -m gunicorn --bind=0.0.0.0:8000 --timeout 600 --workers 2 --threads 4 config.wsgi"
az webapp restart -g "$RG" -n "$WEB_APP" -o none
fi

if [[ $SEED -eq 1 ]]; then
  echo "==> Uploading the demo calls to the recordings container (account key)"
  for json in "$ROOT"/Data/demo_calls/DEMO-*.json; do   # metadata first, so it is there when the audio event fires
    az storage blob upload --account-name "$STORAGE" --account-key "$KEY" -c recordings \
      -n "$(basename "$json")" -f "$json" --overwrite -o none
  done
  for wav in "$ROOT"/Data/demo_calls/DEMO-*.wav; do
    az storage blob upload --account-name "$STORAGE" --account-key "$KEY" -c recordings \
      -n "$(basename "$wav")" -f "$wav" --overwrite -o none
    echo "    uploaded $(basename "$wav")"
  done
fi

echo "Code deployed. Web app: https://$(az webapp show -g "$RG" -n "$WEB_APP" --query defaultHostName -o tsv)"
