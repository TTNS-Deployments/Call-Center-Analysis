import pytest

from callcenter.config import reset_settings_cache


@pytest.fixture(autouse=True)
def local_mode(tmp_path, monkeypatch):
    """Every test runs in local mode against a fresh, empty data folder."""
    for name in ("CC_MODE", "STORAGE_BACKEND", "STORE_BACKEND", "SPEECH_BACKEND", "LANGUAGE_BACKEND", "LLM_BACKEND"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    yield
    reset_settings_cache()
