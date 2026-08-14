"""LangGraph orchestration for privacy-bounded category suggestions."""

from typing import Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from app.categorization.ai_types import (
    CategorySuggestion,
    ClassifierResult,
    DescriptionClassifier,
)
from app.categorization.sanitization import sanitize_transaction_description


class CategorizationGraphState(TypedDict, total=False):
    raw_description: str
    allowed_categories: tuple[str, ...]
    sanitized_description: str
    classifier_result: ClassifierResult | None
    suggestion: CategorySuggestion | None


class CompiledCategorizationGraph(Protocol):
    def invoke(self, values: CategorizationGraphState) -> CategorizationGraphState: ...


def build_categorization_graph(
    classifier: DescriptionClassifier,
) -> CompiledCategorizationGraph:
    """Compile the sanitizer, classifier, and allowlist validator graph."""

    def sanitize_description(state: CategorizationGraphState) -> CategorizationGraphState:
        return {
            "sanitized_description": sanitize_transaction_description(
                state.get("raw_description", "")
            )
        }

    def classify(state: CategorizationGraphState) -> CategorizationGraphState:
        description = state.get("sanitized_description", "")
        if not description:
            return {"classifier_result": None}
        try:
            result = classifier.classify(description, state.get("allowed_categories", ()))
        except Exception:
            result = None
        return {"classifier_result": result}

    def validate(state: CategorizationGraphState) -> CategorizationGraphState:
        result = state.get("classifier_result")
        allowed = state.get("allowed_categories", ())
        if (
            result is None
            or result.abstain is not False
            or result.category_name is None
            or result.category_name not in allowed
            or not isinstance(result.is_subscription, bool)
        ):
            return {"suggestion": None}
        return {
            "suggestion": CategorySuggestion(
                category_name=result.category_name,
                is_subscription=result.is_subscription,
            )
        }

    graph = StateGraph(CategorizationGraphState)
    graph.add_node("sanitize_description", sanitize_description)
    graph.add_node("classify", classify)
    graph.add_node("validate", validate)
    graph.add_edge(START, "sanitize_description")
    graph.add_edge("sanitize_description", "classify")
    graph.add_edge("classify", "validate")
    graph.add_edge("validate", END)
    return graph.compile()


def suggest_category(
    graph: CompiledCategorizationGraph,
    description: str,
    allowed_categories: tuple[str, ...],
) -> CategorySuggestion | None:
    """Return one validated suggestion from sanitized description text only."""
    unique_categories = tuple(dict.fromkeys(allowed_categories))
    result = graph.invoke(
        {
            "raw_description": description,
            "allowed_categories": unique_categories,
        }
    )
    suggestion = result.get("suggestion")
    return suggestion if isinstance(suggestion, CategorySuggestion) else None
