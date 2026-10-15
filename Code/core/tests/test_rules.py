import pytest

from callcenter import rules


def test_bundled_config_is_valid():
    loaded = rules.load_rules()
    assert len(loaded.criteria) == 20
    assert loaded.ratings["partial"]["credit"] == 0.5
    assert loaded.verdict(100) == "Strength" and loaded.verdict(50) == "Inconsistent" and loaded.verdict(None) == "Not applicable"
    assert {c["kind"] for c in loaded.checks} == {"required", "prohibited"}


@pytest.mark.parametrize("text, message", [
    ("criteria: []", "non-empty"),
    ("criteria:\n  - {id: a, name: A}", "missing: description"),
    ("criteria:\n  - {id: a, name: A, description: x, type: other}", "objective or subjective"),
    ("criteria:\n  - {id: a, name: A, description: x, weight: 0}", "weight must be > 0"),
    ("criteria:\n  - {id: a, name: A, description: x, source: phone}", "source must be"),
    ("criteria:\n  - {id: a, name: A, description: x}\n  - {id: a, name: B, description: y}", "Duplicate"),
    ("ratings: {great: {score: 5, credit: 1}}\ncriteria:\n  - {id: a, name: A, description: x}", "unknown rating"),
    ("criteria: [", "Invalid YAML"),
])
def test_rubric_validation(text, message):
    with pytest.raises(rules.ConfigError, match=message):
        rules.parse_and_validate(rules.RUBRIC_FILE, text)


def test_runtime_override_takes_precedence():
    text = "criteria:\n  - {id: only, name: Only, description: x}\n"
    rules.save_config_text(rules.RUBRIC_FILE, text)
    assert [c["id"] for c in rules.load_rules().criteria] == ["only"]
    assert rules.read_config_text(rules.RUBRIC_FILE)[1] == "storage"
