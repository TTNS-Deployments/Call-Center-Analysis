import pytest

from callcenter import rules


def test_bundled_config_is_valid():
    loaded = rules.load_rules()
    assert loaded.max_points == 100
    assert {c["kind"] for c in loaded.checks} == {"required", "prohibited"}


@pytest.mark.parametrize("text, message", [
    ("criteria: []", "non-empty"),
    ("criteria:\n  - {id: a, name: A, max_points: 5}", "missing: description"),
    ("criteria:\n  - {id: a, name: A, max_points: 5, description: x, type: other}", "objective or subjective"),
    ("criteria:\n  - {id: a, name: A, max_points: 5, description: x}\n  - {id: a, name: B, max_points: 5, description: y}", "Duplicate"),
    ("criteria: [", "Invalid YAML"),
])
def test_rubric_validation(text, message):
    with pytest.raises(rules.ConfigError, match=message):
        rules.parse_and_validate(rules.RUBRIC_FILE, text)


def test_runtime_override_takes_precedence():
    text = "criteria:\n  - {id: only, name: Only, max_points: 10, description: x}\n"
    rules.save_config_text(rules.RUBRIC_FILE, text)
    assert [c["id"] for c in rules.load_rules().criteria] == ["only"]
    assert rules.read_config_text(rules.RUBRIC_FILE)[1] == "storage"
