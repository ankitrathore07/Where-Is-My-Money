"""Contracts at the optional description-only classifier boundary."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CategorySuggestion:
    category_name: str
    is_subscription: bool


@dataclass(frozen=True)
class ClassifierResult:
    category_name: str | None
    is_subscription: bool
    abstain: bool


class DescriptionClassifier(Protocol):
    def classify(
        self,
        description: str,
        allowed_categories: tuple[str, ...],
    ) -> ClassifierResult | None: ...

