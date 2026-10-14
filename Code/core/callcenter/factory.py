"""Builds the configured implementation of each dependency (cached per process)."""

from __future__ import annotations

from functools import lru_cache

from .config import get_settings


def _unknown(kind: str, value: str):
    return ValueError(f"Unknown {kind} backend '{value}'")


@lru_cache(maxsize=1)
def get_blob_store():
    from . import storage

    s = get_settings()
    if s.storage_backend == "local":
        return storage.LocalBlobStore(s.local_data_dir / "storage")
    if s.storage_backend == "azure":
        return storage.AzureBlobStore(s)
    raise _unknown("storage", s.storage_backend)


@lru_cache(maxsize=1)
def get_call_store():
    from . import store

    s = get_settings()
    if s.store_backend == "local":
        return store.SqliteCallStore(s.local_data_dir / "callstore.sqlite3")
    if s.store_backend == "cosmos":
        return store.CosmosCallStore(s)
    raise _unknown("store", s.store_backend)


@lru_cache(maxsize=1)
def get_speech_client():
    from . import speech

    s = get_settings()
    if s.speech_backend == "mock":
        return speech.MockSpeechClient(s)
    if s.speech_backend == "azure":
        return speech.AzureFastTranscriptionClient(s)
    raise _unknown("speech", s.speech_backend)


@lru_cache(maxsize=1)
def get_language_client():
    from . import language

    s = get_settings()
    if s.language_backend == "mock":
        return language.MockLanguageClient(s)
    if s.language_backend == "azure":
        return language.AzureLanguageClient(s)
    raise _unknown("language", s.language_backend)


@lru_cache(maxsize=1)
def get_insights_client():
    from . import llm

    s = get_settings()
    if s.llm_backend == "mock":
        return llm.MockInsightsClient(s)
    if s.llm_backend == "azure":
        return llm.AzureOpenAIInsightsClient(s)
    raise _unknown("llm", s.llm_backend)


@lru_cache(maxsize=1)
def get_event_publisher():
    from . import events

    s = get_settings()
    if s.events_backend == "none":
        return events.NullPublisher()
    if s.events_backend == "eventhub":
        return events.EventHubPublisher(s)
    raise _unknown("events", s.events_backend)


@lru_cache(maxsize=1)
def get_dispatcher():
    from . import dispatch

    s = get_settings()
    # Locally the web app runs the pipeline itself; in Azure, Functions do it via queues.
    return dispatch.QueueDispatcher(s) if s.mode == "azure" else dispatch.LocalDispatcher()


def reset() -> None:
    for fn in (get_blob_store, get_call_store, get_speech_client, get_language_client,
               get_insights_client, get_event_publisher, get_dispatcher):
        fn.cache_clear()
    from .samples import load_scenarios

    load_scenarios.cache_clear()
