"""Power BI 'app owns data' embedding: a service principal generates a view-only embed token.

Requirements (see Analytics/README.md): the report is published to a workspace on a
Power BI Embedded / Fabric capacity, the service principal is a member of that workspace,
and the tenant setting "Service principals can use Fabric APIs" is enabled.
"""

from __future__ import annotations

import time

import requests
from django.conf import settings

API = "https://api.powerbi.com/v1.0/myorg"
SCOPE = "https://analysis.windows.net/powerbi/api/.default"

_cache: dict = {}


def is_configured() -> bool:
    return all([settings.POWERBI_WORKSPACE_ID, settings.POWERBI_REPORT_ID, settings.POWERBI_CLIENT_ID,
                settings.POWERBI_CLIENT_SECRET, settings.POWERBI_TENANT_ID])


def _aad_token() -> str:
    import msal

    app = msal.ConfidentialClientApplication(
        settings.POWERBI_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{settings.POWERBI_TENANT_ID}",
        client_credential=settings.POWERBI_CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(scopes=[SCOPE])
    if "access_token" not in result:
        raise RuntimeError(f"Power BI auth failed: {result.get('error_description', result.get('error'))}")
    return result["access_token"]


def embed_config() -> dict:
    """Return {"embedUrl", "reportId", "token", "expiry"}; cached until shortly before expiry."""
    cached = _cache.get("config")
    if cached and cached["expires_epoch"] - 300 > time.time():
        return cached
    headers = {"Authorization": f"Bearer {_aad_token()}"}
    base = f"{API}/groups/{settings.POWERBI_WORKSPACE_ID}/reports/{settings.POWERBI_REPORT_ID}"
    report = requests.get(base, headers=headers, timeout=30)
    report.raise_for_status()
    token = requests.post(f"{base}/GenerateToken", headers=headers, json={"accessLevel": "View"}, timeout=30)
    token.raise_for_status()
    body = token.json()
    from datetime import datetime

    expiry = datetime.fromisoformat(body["expiration"].replace("Z", "+00:00"))
    config = {
        "embedUrl": report.json()["embedUrl"],
        "reportId": settings.POWERBI_REPORT_ID,
        "token": body["token"],
        "expiry": body["expiration"],
        "expires_epoch": expiry.timestamp(),
    }
    _cache["config"] = config
    return config
