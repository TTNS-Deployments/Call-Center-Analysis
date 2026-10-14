#!/usr/bin/env bash
# Creates (or updates) the Microsoft Entra ID app registration used for web app sign-in.
#
#   Deployment/scripts/setup_entra.sh -g <resource-group> -e <environment>
#
# What it does
#   1. App registration "Call Center Analysis (<env>)" with redirect URI https://<web app>/auth/callback
#      and app roles Admin, Supervisor, QA, Agent (fixed IDs, so re-running is safe)
#   2. Service principal (enterprise app) so users and groups can be assigned to roles
#   3. New client secret -> Key Vault secret "entra-client-secret" (the web app reads it from there)
#   4. ENTRA_CLIENT_ID app setting on the web app + entraClientId in the parameters file,
#      so later ARM deployments keep it
#   5. Assigns YOU the Admin role
#
# Assign other users/groups to roles in: Entra admin center -> Enterprise applications ->
# "Call Center Analysis (<env>)" -> Users and groups.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RG="" ENV=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -g|--resource-group) RG="$2"; shift 2 ;;
    -e|--environment) ENV="$2"; shift 2 ;;
    *) echo "Unknown option $1" >&2; exit 1 ;;
  esac
done
[[ -n "$RG" && -n "$ENV" ]] || { echo "Usage: $0 -g <resource-group> -e <environment>" >&2; exit 1; }

DEPLOYMENT="callcenter-$ENV"
out() { az deployment group show -g "$RG" -n "$DEPLOYMENT" --query "properties.outputs.$1.value" -o tsv; }
WEB_APP="$(out webAppName)"
REDIRECT="$(out entraRedirectUri)"
KEY_VAULT="$(out keyVaultName)"
NAME="Call Center Analysis ($ENV)"

ROLES_FILE="$(mktemp)"
trap 'rm -f "$ROLES_FILE"' EXIT
cat > "$ROLES_FILE" <<'JSON'
[
  {"id": "6a1c1f3e-2b8f-4c47-9a54-1f0b7b2d9a01", "value": "Admin", "displayName": "Admin",
   "description": "Full access including settings and pipeline management", "allowedMemberTypes": ["User"], "isEnabled": true},
  {"id": "6a1c1f3e-2b8f-4c47-9a54-1f0b7b2d9a02", "value": "Supervisor", "displayName": "Supervisor",
   "description": "All calls, agents, insights and pipeline", "allowedMemberTypes": ["User"], "isEnabled": true},
  {"id": "6a1c1f3e-2b8f-4c47-9a54-1f0b7b2d9a03", "value": "QA", "displayName": "QA analyst",
   "description": "All calls, agents and insights", "allowedMemberTypes": ["User"], "isEnabled": true},
  {"id": "6a1c1f3e-2b8f-4c47-9a54-1f0b7b2d9a04", "value": "Agent", "displayName": "Agent",
   "description": "Own calls and own scorecard", "allowedMemberTypes": ["User"], "isEnabled": true}
]
JSON
ADMIN_ROLE_ID="6a1c1f3e-2b8f-4c47-9a54-1f0b7b2d9a01"

APP_ID="$(az ad app list --display-name "$NAME" --query '[0].appId' -o tsv)"
if [[ -z "$APP_ID" ]]; then
  echo "==> Creating app registration '$NAME'"
  APP_ID="$(az ad app create --display-name "$NAME" --sign-in-audience AzureADMyOrg \
    --web-redirect-uris "$REDIRECT" --enable-id-token-issuance true --app-roles @"$ROLES_FILE" --query appId -o tsv)"
else
  echo "==> Updating app registration '$NAME' ($APP_ID)"
  az ad app update --id "$APP_ID" --web-redirect-uris "$REDIRECT" --enable-id-token-issuance true --app-roles @"$ROLES_FILE"
fi

SP_ID="$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)"
[[ -n "$SP_ID" ]] || SP_ID="$(az ad sp create --id "$APP_ID" --query id -o tsv)"

echo "==> Creating client secret and storing it in Key Vault $KEY_VAULT"
SECRET="$(az ad app credential reset --id "$APP_ID" --display-name "webapp-$(date +%Y%m%d)" --years 1 --append --query password -o tsv)"
az keyvault secret set --vault-name "$KEY_VAULT" --name entra-client-secret --value "$SECRET" -o none

echo "==> Configuring web app $WEB_APP"
az webapp config appsettings set -g "$RG" -n "$WEB_APP" --settings ENTRA_CLIENT_ID="$APP_ID" -o none

PARAMS="$ROOT/Deployment/ARM/parameters/$ENV.parameters.json"
if [[ -f "$PARAMS" ]]; then
  python3 - "$PARAMS" "$APP_ID" <<'PY'
import json, sys
path, app_id = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["parameters"].setdefault("entraClientId", {})["value"] = app_id
json.dump(data, open(path, "w"), indent=2)
open(path, "a").write("\n")
PY
  echo "    entraClientId saved to $(basename "$PARAMS") (commit this change)"
fi

USER_ID="$(az ad signed-in-user show --query id -o tsv 2>/dev/null || true)"
if [[ -n "$USER_ID" ]]; then
  echo "==> Assigning you the Admin role"
  az rest --method post --url "https://graph.microsoft.com/v1.0/servicePrincipals/$SP_ID/appRoleAssignedTo" \
    --body "{\"principalId\": \"$USER_ID\", \"resourceId\": \"$SP_ID\", \"appRoleId\": \"$ADMIN_ROLE_ID\"}" -o none 2>/dev/null \
    || echo "    (already assigned)"
fi

az webapp restart -g "$RG" -n "$WEB_APP" -o none
echo "Entra ID sign-in configured. Client ID: $APP_ID"
