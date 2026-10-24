from callcenter.config import get_settings
from callcenter.language import MockLanguageClient, coalesce, redact


def analyze(text, speaker="customer"):
    client = MockLanguageClient(get_settings())
    return client.analyze([{"speaker": speaker, "offset_ms": 0, "duration_ms": 1000, "text": text}])["phrases"][0]


def test_redacts_common_pii():
    p = analyze("I'm Rohan Das, my number is 98765 43210, card 4111 2222 3333 4444, mail rohan.das12@example.com")
    assert "Rohan" not in p["text"] and "98765" not in p["text"] and "4111" not in p["text"] and "@" not in p["text"]
    assert "[NAME]" in p["text"] and "[PHONE]" in p["text"] and "[CARD]" in p["text"] and "[EMAIL]" in p["text"]
    assert {e["category"] for e in p["pii"]} >= {"Person", "PhoneNumber", "CreditCardNumber", "Email"}


def test_full_dates_redacted_but_not_durations():
    p = analyze("My date of birth is 12 March 1990 and it has been three days.")
    assert "1990" not in p["text"] and "three days" in p["text"]


def test_adjacent_entities_coalesce():
    text = "This is Rohan Das"
    entities = coalesce(text, [{"category": "Person", "offset": 8, "length": 5}, {"category": "Person", "offset": 14, "length": 3}])
    assert redact(text, entities) == "This is [NAME]"


def test_sentiment_labels():
    assert analyze("This is terrible, I'm really disappointed")["sentiment"] == "negative"
    assert analyze("Great, thank you so much")["sentiment"] == "positive"
    assert analyze("My account number please")["sentiment"] == "neutral"


def test_digits_read_out_one_by_one_are_redacted():
    p = analyze("It's 4 1 1 1, 2 2 2 2, 3 3 3 3, 4 4 4 4 and my number is 9 8 7 6 5, 4 3 2 1 0.")
    assert "[CARD]" in p["text"] and "[PHONE]" in p["text"] and "4 1 1 1" not in p["text"]


def test_spoken_forms_are_redacted():
    p = analyze("It's rohan dot das at example dot com, born the twelfth of March, nineteen ninety.")
    assert "rohan" not in p["text"] and "nineteen ninety" not in p["text"]
    assert "[EMAIL]" in p["text"] and "[DATE]" in p["text"]
    assert analyze("Hello, am I speaking with Mister Rajesh Kumar?")["text"] == "Hello, am I speaking with Mister [NAME]?"


def test_cvv_and_otp_digits_are_redacted():
    client = MockLanguageClient(get_settings())
    phrases = [
        {"speaker": "customer", "offset_ms": 0, "duration_ms": 1, "text": "The C V V is 1 2 3."},
        {"speaker": "agent", "offset_ms": 1, "duration_ms": 1, "text": "And read me the O T P you just received."},
        {"speaker": "customer", "offset_ms": 2, "duration_ms": 1, "text": "It says 5 8 2 9 1 4. Is that safe?"},
        {"speaker": "agent", "offset_ms": 3, "duration_ms": 1, "text": "Your reference is C R N 7 7 3 1 0 4 2."},
    ]
    out = [p["text"] for p in client.analyze(phrases)["phrases"]]
    assert out[0] == "The C V V is [CREDENTIAL]." and out[2] == "It says [CREDENTIAL]. Is that safe?"
    assert "7 7 3 1 0 4 2" in out[3]  # reference numbers outside a credential context are kept
