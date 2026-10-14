"""Loads and validates the editable YAML configuration (QA rubric, compliance checks, call reasons).

Lookup order for each file:
1. ``config`` storage container (saved from the web app Settings page), then
2. the ``Config/`` folder shipped with the repo / app package.
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml

from .config import get_settings

RUBRIC_FILE = "qa_rubric.yaml"
COMPLIANCE_FILE = "compliance_checks.yaml"
REASONS_FILE = "call_reasons.yaml"
CONFIG_FILES = (RUBRIC_FILE, COMPLIANCE_FILE, REASONS_FILE)


class ConfigError(ValueError):
    pass


@dataclass
class Rules:
    rubric: dict
    compliance: dict
    reasons: dict

    @property
    def criteria(self) -> list[dict]:
        return self.rubric["criteria"]

    @property
    def checks(self) -> list[dict]:
        return self.compliance["checks"]

    @property
    def categories(self) -> list[dict]:
        return self.reasons["categories"]

    @property
    def max_points(self) -> int:
        return sum(int(c["max_points"]) for c in self.criteria)

    @property
    def pass_threshold(self) -> float:
        return float(self.rubric.get("pass_threshold", 75))

    def category_name(self, category_id: str) -> str:
        for c in self.categories:
            if c["id"] == category_id:
                return c["name"]
        return category_id or "Unknown"


def read_config_text(name: str) -> tuple[str, str]:
    """Return (yaml_text, source) where source is 'storage' or 'default'."""
    from .factory import get_blob_store

    settings = get_settings()
    try:
        store = get_blob_store()
        if store.exists(settings.container_config, name):
            return store.get_bytes(settings.container_config, name).decode("utf-8"), "storage"
    except Exception:  # storage unavailable -> fall back to bundled defaults
        pass
    if settings.config_dir and (settings.config_dir / name).is_file():
        return (settings.config_dir / name).read_text(encoding="utf-8"), "default"
    raise ConfigError(f"Configuration file {name} not found")


def default_config_text(name: str) -> str:
    settings = get_settings()
    if settings.config_dir and (settings.config_dir / name).is_file():
        return (settings.config_dir / name).read_text(encoding="utf-8")
    raise ConfigError(f"Default configuration file {name} not found")


def parse_and_validate(name: str, text: str) -> dict:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("Top level must be a mapping")

    if name == RUBRIC_FILE:
        _require_list(data, "criteria", ("id", "name", "max_points", "description"))
        for c in data["criteria"]:
            if c.get("type", "objective") not in ("objective", "subjective"):
                raise ConfigError(f"criteria '{c['id']}': type must be objective or subjective")
            if int(c["max_points"]) <= 0:
                raise ConfigError(f"criteria '{c['id']}': max_points must be > 0")
    elif name == COMPLIANCE_FILE:
        _require_list(data, "checks", ("id", "name", "kind", "description"))
        for c in data["checks"]:
            if c["kind"] not in ("required", "prohibited"):
                raise ConfigError(f"check '{c['id']}': kind must be required or prohibited")
    elif name == REASONS_FILE:
        _require_list(data, "categories", ("id", "name", "description"))
    else:
        raise ConfigError(f"Unknown configuration file {name}")
    return data


def _require_list(data: dict, key: str, fields: tuple[str, ...]) -> None:
    items = data.get(key)
    if not isinstance(items, list) or not items:
        raise ConfigError(f"'{key}' must be a non-empty list")
    seen = set()
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ConfigError(f"{key}[{i}] must be a mapping")
        missing = [f for f in fields if f not in item]
        if missing:
            raise ConfigError(f"{key}[{i}] is missing: {', '.join(missing)}")
        if item["id"] in seen:
            raise ConfigError(f"Duplicate id '{item['id']}' in {key}")
        seen.add(item["id"])


def load_rules() -> Rules:
    loaded = {}
    for name in CONFIG_FILES:
        text, _ = read_config_text(name)
        loaded[name] = parse_and_validate(name, text)
    return Rules(rubric=loaded[RUBRIC_FILE], compliance=loaded[COMPLIANCE_FILE], reasons=loaded[REASONS_FILE])


def save_config_text(name: str, text: str) -> dict:
    """Validate and save a runtime override to the config container."""
    from .factory import get_blob_store

    data = parse_and_validate(name, text)
    get_blob_store().put_bytes(get_settings().container_config, name, text.encode("utf-8"), "application/x-yaml")
    return data
