from dataclasses import dataclass

import pytest

from app.categorization.ai_graph import build_categorization_graph, suggest_category
from app.categorization.ai_types import CategorySuggestion, ClassifierResult

CATEGORIES = ("Housing", "Shopping", "Transfers", "Uncategorized")


@dataclass
class RecordingClassifier:
    result: ClassifierResult | None

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def classify(
        self, description: str, allowed_categories: tuple[str, ...]
    ) -> ClassifierResult | None:
        self.calls.append((description, allowed_categories))
        return self.result


def test_graph_sends_only_sanitized_description_and_allowlist() -> None:
    classifier = RecordingClassifier(ClassifierResult("Transfers", False, False))
    graph = build_categorization_graph(classifier)

    result = suggest_category(
        graph,
        "ZELLE PAYMENT TO JANE SAMPLE 123456789",
        CATEGORIES,
    )

    assert result == CategorySuggestion("Transfers", False)
    assert classifier.calls == [
        ("ZELLE PAYMENT TO <PAYEE>", CATEGORIES)
    ]


@pytest.mark.parametrize(
    "classifier_result",
    [
        None,
        ClassifierResult(None, False, True),
        ClassifierResult("Not Allowed", False, False),
        ClassifierResult(None, False, False),
        ClassifierResult("Shopping", 1, False),  # type: ignore[arg-type]
    ],
)
def test_graph_rejects_abstentions_and_invalid_results(
    classifier_result: ClassifierResult | None,
) -> None:
    classifier = RecordingClassifier(classifier_result)

    result = suggest_category(
        build_categorization_graph(classifier),
        "UNKNOWN SHOP",
        CATEGORIES,
    )

    assert result is None


def test_empty_sanitized_description_skips_classifier() -> None:
    classifier = RecordingClassifier(ClassifierResult("Shopping", False, False))

    result = suggest_category(
        build_categorization_graph(classifier),
        "\x00\x1f   ",
        CATEGORIES,
    )

    assert result is None
    assert classifier.calls == []


def test_classifier_exception_fails_closed_without_description_in_error() -> None:
    class FailingClassifier:
        def classify(
            self, description: str, allowed_categories: tuple[str, ...]
        ) -> ClassifierResult | None:
            raise TimeoutError("synthetic timeout")

    result = suggest_category(
        build_categorization_graph(FailingClassifier()),
        "PRIVATE UNKNOWN DESCRIPTION",
        CATEGORIES,
    )

    assert result is None


def test_sanitized_description_is_capped_before_classifier() -> None:
    classifier = RecordingClassifier(ClassifierResult("Shopping", False, False))

    suggest_category(
        build_categorization_graph(classifier),
        "MERCHANT " + "X" * 500,
        CATEGORIES,
    )

    assert len(classifier.calls[0][0]) == 160

