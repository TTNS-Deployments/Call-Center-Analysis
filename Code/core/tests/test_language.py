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
