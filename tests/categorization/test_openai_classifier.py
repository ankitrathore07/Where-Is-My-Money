import json
from types import SimpleNamespace

from app.categorization.ai_types import ClassifierResult
from app.categorization.openai_classifier import OpenAIDescriptionClassifier


class FakeResponses:
    def __init__(self, output_parsed: object) -> None:
        self.output_parsed = output_parsed
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return SimpleNamespace(output_parsed=self.output_parsed)


class FakeOpenAIClient:
    def __init__(self, output_parsed: object) -> None:
        self.responses = FakeResponses(output_parsed)


def test_openai_adapter_uses_structured_response_and_minimal_user_payload() -> None:
    client = FakeOpenAIClient(
        SimpleNamespace(category_name="Transfers", is_subscription=False, abstain=False)
    )
    classifier = OpenAIDescriptionClassifier(
        api_key="synthetic-key",
        model="gpt-5.4-nano",
        timeout_seconds=8.0,
        client=client,
    )

    result = classifier.classify(
        "BEST BUY AUTO PYMT",
        ("Housing", "Transfers", "Uncategorized"),
    )

    assert result == ClassifierResult("Transfers", False, False)
    call = client.responses.calls[0]
    assert call["model"] == "gpt-5.4-nano"
    assert "text_format" in call
    messages = call["input"]
    assert isinstance(messages, list)
    user_payload = json.loads(messages[1]["content"])
    assert user_payload == {
        "description": "BEST BUY AUTO PYMT",
        "allowed_categories": ["Housing", "Transfers", "Uncategorized"],
    }
    serialized = json.dumps(user_payload).casefold()
    assert all(
        forbidden not in serialized
        for forbidden in ("amount", "date", "account", "workspace", "filename", "balance")
    )


def test_openai_adapter_treats_missing_parsed_output_as_abstention() -> None:
    client = FakeOpenAIClient(None)
    classifier = OpenAIDescriptionClassifier(
        api_key="synthetic-key",
        model="gpt-5.4-nano",
        timeout_seconds=8.0,
        client=client,
    )

    assert classifier.classify("UNKNOWN", ("Shopping",)) is None

