"""Runtime settings, read from environment variables.

``CC_MODE`` sets the default for every backend:

* ``local`` (default): files on disk, SQLite call store, mock AI services.
* ``azure``: Blob Storage, Cosmos DB, Azure AI Speech / Language / OpenAI.

Each backend can be overridden on its own, e.g. run locally but call the real
Azure OpenAI with ``LLM_BACKEND=azure``. That is how keys get swapped in
one service at a time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    value = os.environ.get(name, default).strip()
    # An unresolved App Service Key Vault reference arrives as the literal reference string.
    return "" if value.startswith("@Microsoft.KeyVault(") else value


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def find_repo_dir(name: str) -> Path | None:
    """Walk up from this package looking for a sibling folder (``Config``, ``Data``).

    Works both in the repo (Code/core/callcenter -> repo root) and in a deployed
    app package where the folder is copied next to the package.
    """
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        candidate = parent / name
        if candidate.is_dir():
            return candidate
    return None


@dataclass(frozen=True)
class Settings:
    mode: str = "local"

    # Storage
    storage_backend: str = "local"
    local_data_dir: Path = field(default_factory=lambda: Path(".localdata"))
    storage_connection_string: str = ""
    storage_account_url: str = ""
    queue_account_url: str = ""
    container_recordings: str = "recordings"
    container_transcripts: str = "transcripts"
    container_insights: str = "insights"
    container_config: str = "config"
    queue_recordings: str = "recordings-events"
    queue_transcripts: str = "transcripts-events"

    # Call store
    store_backend: str = "local"
    cosmos_endpoint: str = ""
    cosmos_key: str = ""
    cosmos_database: str = "callcenter"
    cosmos_container: str = "calls"

    # Speech
    speech_backend: str = "mock"
    speech_endpoint: str = ""
    speech_key: str = ""
    speech_locale: str = "en-US"
    speech_api_version: str = "2024-11-15"
    agent_channel: int = 0

    # Language
    language_backend: str = "mock"
    language_endpoint: str = ""
    language_key: str = ""
    pii_ignore_categories: tuple[str, ...] = ("Organization", "DateTime", "Quantity", "PersonType", "URL")

    # LLM
    llm_backend: str = "mock"
    openai_endpoint: str = ""
    openai_key: str = ""
    openai_deployment: str = "gpt-4.1-mini"
    openai_api_version: str = "2024-10-21"

    # Events (downstream CRM / real-time phase)
    events_backend: str = "none"
    eventhub_namespace: str = ""
    eventhub_name: str = "call-insights"

    # Managed identity (user-assigned); empty = system-assigned / developer login
    azure_client_id: str = ""

    config_dir: Path | None = None
    data_dir: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.mode == "local"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    mode = _env("CC_MODE", "local").lower()
    azure = mode == "azure"

    config_dir = _env("CC_CONFIG_DIR")
    data_dir = _env("CC_SAMPLE_DATA_DIR")
    ignore = _env("PII_IGNORE_CATEGORIES")

    return Settings(
        mode=mode,
        storage_backend=_env("STORAGE_BACKEND", "azure" if azure else "local").lower(),
        local_data_dir=Path(_env("LOCAL_DATA_DIR", str(_default_local_data_dir()))),
        storage_connection_string=_env("AZURE_STORAGE_CONNECTION_STRING"),
        storage_account_url=_env("AZURE_STORAGE_ACCOUNT_URL"),
        queue_account_url=_env("AZURE_STORAGE_QUEUE_URL"),
        container_recordings=_env("CONTAINER_RECORDINGS", "recordings"),
        container_transcripts=_env("CONTAINER_TRANSCRIPTS", "transcripts"),
        container_insights=_env("CONTAINER_INSIGHTS", "insights"),
        container_config=_env("CONTAINER_CONFIG", "config"),
        queue_recordings=_env("QUEUE_RECORDINGS", "recordings-events"),
        queue_transcripts=_env("QUEUE_TRANSCRIPTS", "transcripts-events"),
        store_backend=_env("STORE_BACKEND", "cosmos" if azure else "local").lower(),
        cosmos_endpoint=_env("COSMOS_ENDPOINT"),
        cosmos_key=_env("COSMOS_KEY"),
        cosmos_database=_env("COSMOS_DATABASE", "callcenter"),
        cosmos_container=_env("COSMOS_CONTAINER", "calls"),
        speech_backend=_env("SPEECH_BACKEND", "azure" if azure else "mock").lower(),
        speech_endpoint=_env("SPEECH_ENDPOINT").rstrip("/"),
        speech_key=_env("SPEECH_KEY"),
        speech_locale=_env("SPEECH_LOCALE", "en-US"),
        speech_api_version=_env("SPEECH_API_VERSION", "2024-11-15"),
        agent_channel=int(_env("AGENT_CHANNEL", "0") or 0),
        language_backend=_env("LANGUAGE_BACKEND", "azure" if azure else "mock").lower(),
        language_endpoint=_env("LANGUAGE_ENDPOINT").rstrip("/"),
        language_key=_env("LANGUAGE_KEY"),
        pii_ignore_categories=tuple(c.strip() for c in ignore.split(",") if c.strip())
        if ignore
        else Settings.pii_ignore_categories,
        llm_backend=_env("LLM_BACKEND", "azure" if azure else "mock").lower(),
        openai_endpoint=_env("AZURE_OPENAI_ENDPOINT").rstrip("/"),
        openai_key=_env("AZURE_OPENAI_KEY"),
        openai_deployment=_env("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini"),
        openai_api_version=_env("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        events_backend=_env("EVENTS_BACKEND", "none").lower(),
        eventhub_namespace=_env("EVENTHUB_NAMESPACE"),
        eventhub_name=_env("EVENTHUB_NAME", "call-insights"),
        azure_client_id=_env("AZURE_CLIENT_ID"),
        config_dir=Path(config_dir) if config_dir else find_repo_dir("Config"),
        data_dir=Path(data_dir) if data_dir else find_repo_dir("Data"),
    )


def _default_local_data_dir() -> Path:
    root = find_repo_dir("Code")
    # repo/Code exists -> keep local data at repo/.localdata
    return (root.parent / ".localdata") if root else Path(".localdata")


def reset_settings_cache() -> None:
    """Used by tests after changing environment variables."""
    get_settings.cache_clear()
    from . import factory

    factory.reset()
