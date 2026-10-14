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
