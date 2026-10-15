from callcenter import llm, rules


def test_normalize_clamps_and_completes():
    r = rules.load_rules()
    raw = {"call_reason": "nonsense", "resolution": "maybe", "qa": [{"id": "greeting", "score": 999}],
           "compliance": [], "customer_emotion": {"start": "furious"}}
    out = llm.normalize(raw, r)
    assert out["call_reason"] == r.categories[-1]["id"]
    assert out["resolution"] == "unresolved"
    assert [q["id"] for q in out["qa"]] == [c["id"] for c in r.criteria]
    assert out["qa"][0]["score"] == 10  # clamped to max_points
    assert out["customer_emotion"]["start"] == "neutral"
    assert len(out["compliance"]) == len(r.checks)


def test_schema_and_prompt_cover_rules():
    r = rules.load_rules()
    schema = llm.output_schema(r)
    assert schema["properties"]["qa"]["items"]["properties"]["id"]["enum"] == [c["id"] for c in r.criteria]
    prompt = llm.build_system_prompt(r)
    for c in r.criteria + r.checks + r.categories:
        assert c["id"] in prompt
    for placeholder in ("{emotions}", "{reasons}", "{criteria}", "{checks}"):
        assert placeholder not in prompt


def _fake_azure_client(monkeypatch, effort):
    import json as _json

    from callcenter.config import reset_settings_cache, get_settings

    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_KEY", "test-key")
    monkeypatch.setenv("AZURE_OPENAI_REASONING_EFFORT", effort)
    reset_settings_cache()
    client = llm.AzureOpenAIInsightsClient(get_settings())
    sent = {}

    class Completions:
        def create(self, **kwargs):
            sent.update(kwargs)
            msg = type("M", (), {"content": _json.dumps({"call_reason": "billing"})})
            return type("R", (), {"choices": [type("C", (), {"message": msg})], "usage": None})

    client.client = type("Client", (), {"chat": type("Chat", (), {"completions": Completions()})})
    return client, sent


def test_gpt5_request_uses_reasoning_effort_not_temperature(monkeypatch):
    client, sent = _fake_azure_client(monkeypatch, "low")
    client.generate([{"speaker": "agent", "offset_ms": 0, "duration_ms": 1, "text": "Hello"}], {}, rules.load_rules())
    assert sent["reasoning_effort"] == "low" and "temperature" not in sent
    assert sent["model"] == "gpt-5-mini"
    assert sent["response_format"]["type"] == "json_schema"


def test_classic_model_request_uses_temperature(monkeypatch):
    client, sent = _fake_azure_client(monkeypatch, "none")
    client.generate([{"speaker": "agent", "offset_ms": 0, "duration_ms": 1, "text": "Hello"}], {}, rules.load_rules())
    assert sent["temperature"] == 0 and "reasoning_effort" not in sent
