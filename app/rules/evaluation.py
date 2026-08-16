"""Pure, fail-closed evaluation of typed workspace rule conditions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date

from app.categorization.normalization import merchant_key
from app.imports.providers.registry import PROVIDER_PDF_PROFILES, PROVIDER_PROFILES
from app.rules.types import (
    AllCondition,
    AnyCondition,
    ConditionNode,
    NotCondition,
    PredicateCondition,
    RuleContext,
)
from app.rules.validation import MAX_PREDICATES, MAX_TREE_DEPTH

_TEXT_OPERATORS = frozenset({"exact", "contains", "starts_with", "ends_with"})
_AMOUNT_OPERATORS = frozenset(
    {"equal", "greater_than", "greater_or_equal", "less_than", "less_or_equal"}
)
_DATE_OPERATORS = frozenset({"on", "before", "after"})
_DIRECTIONS = frozenset({"income", "expense", "zero"})
_PROVIDER_KEYS = frozenset(
    {"generic_csv", *(profile.key for profile in (*PROVIDER_PROFILES, *PROVIDER_PDF_PROFILES))}
)


@dataclass(frozen=True)
class ConditionResult:
    """The safe evaluation outcome for a condition and its inspected children."""

    matched: bool
    explanation: str
    children: tuple[ConditionResult, ...] = ()


@dataclass(frozen=True)
class CompiledWorkspaceRule:
    """A validated rule and its condition tree, ready for in-memory evaluation."""

    id: int
    name: str
    normalized_merchant: str | None
    category_id: int
    is_subscription: bool
    billing_period_months: int | None
    tag_ids: tuple[int, ...]
    condition: ConditionNode


@dataclass(frozen=True)
class RuleCompilationDiagnostic:
    """A value-free reason that a persisted rule was excluded from evaluation."""

    rule_id: int
    reason: str


@dataclass(frozen=True)
class WorkspaceRuleMatch:
    """The first deterministic workspace-rule match and its safe explanation tree."""

    rule: CompiledWorkspaceRule
    result: ConditionResult

    @property
    def explanation(self) -> str:
        return self.result.explanation


@dataclass(frozen=True)
class CompiledWorkspaceRuleSet:
    """An immutable, workspace-scoped rule snapshot evaluated without database access."""

    workspace_id: int
    rules: tuple[CompiledWorkspaceRule, ...]
    diagnostics: tuple[RuleCompilationDiagnostic, ...] = ()

    def match(self, context: RuleContext) -> WorkspaceRuleMatch | None:
        for compiled in self.rules:
            result = evaluate_condition(compiled.condition, context)
            if result.matched:
                return WorkspaceRuleMatch(compiled, result)
        return None


def evaluate_condition(node: ConditionNode, context: RuleContext) -> ConditionResult:
    """Evaluate a validated condition tree without exposing context values in results."""
    if not _is_valid_tree(node, depth=1, predicate_count=[0]):
        return _invalid_result()
    return _evaluate_node(node, context)


def _evaluate_node(node: ConditionNode, context: RuleContext) -> ConditionResult:
    if isinstance(node, PredicateCondition):
        return _evaluate_predicate(node, context)
    if isinstance(node, AllCondition):
        return _evaluate_all(node, context)
    if isinstance(node, AnyCondition):
        return _evaluate_any(node, context)
    if isinstance(node, NotCondition):
        child = _evaluate_node(node.child, context)
        return ConditionResult(
            not child.matched,
            f"not: {'match' if not child.matched else 'no match'}",
            (child,),
        )
    return _invalid_result()


def _evaluate_all(node: AllCondition, context: RuleContext) -> ConditionResult:
    if not node.children:
        return _invalid_result()
    children: list[ConditionResult] = []
    for child_node in node.children:
        child = _evaluate_node(child_node, context)
        children.append(child)
        if not child.matched:
            return ConditionResult(False, "all: no match", tuple(children))
    return ConditionResult(True, "all: match", tuple(children))


def _evaluate_any(node: AnyCondition, context: RuleContext) -> ConditionResult:
    if not node.children:
        return _invalid_result()
    children: list[ConditionResult] = []
    for child_node in node.children:
        child = _evaluate_node(child_node, context)
        children.append(child)
        if child.matched:
            return ConditionResult(True, "any: match", tuple(children))
    return ConditionResult(False, "any: no match", tuple(children))


def _evaluate_predicate(node: PredicateCondition, context: RuleContext) -> ConditionResult:
    if not _is_valid_predicate(node):
        return _invalid_result()
    matched = False
    if node.field == "description":
        matched = _compare_text(context.description, node.operator, node.value)
    elif node.field == "merchant_key":
        matched = _compare_merchant_key(context.merchant_key, node.operator, node.value)
    elif node.field == "amount_cents":
        matched = _compare_amount(context.amount_cents, node.operator, node.value)
    elif node.field == "transaction_date":
        matched = _compare_date(context.transaction_date, node.operator, node.value)
    elif node.field == "direction":
        matched = _compare_identity(
            context.direction, node.operator, node.value, {"income", "expense", "zero"}
        )
    elif node.field == "account_id":
        matched = _compare_account_id(context.account_id, node.operator, node.value)
    elif node.field == "provider_key":
        matched = _compare_provider_key(context.provider_key, node.operator, node.value)
    return ConditionResult(matched, f"predicate: {'match' if matched else 'no match'}")


def _compare_text(actual: str, operator: object, expected: object) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    normalized_actual = _normalize_text(actual)
    normalized_expected = _normalize_text(expected)
    return _compare_normalized_text(normalized_actual, operator, normalized_expected)


def _compare_merchant_key(actual: str, operator: object, expected: object) -> bool:
    if not isinstance(actual, str) or not isinstance(expected, str):
        return False
    return _compare_normalized_text(
        merchant_key(actual).casefold(), operator, merchant_key(expected).casefold()
    )


def _compare_normalized_text(actual: str, operator: object, expected: str) -> bool:
    if operator == "exact":
        return actual == expected
    if operator == "contains":
        return expected in actual
    if operator == "starts_with":
        return actual.startswith(expected)
    if operator == "ends_with":
        return actual.endswith(expected)
    return False


def _compare_amount(actual: int, operator: object, expected: object) -> bool:
    if not _is_int(actual) or not _is_int(expected):
        return False
    if operator == "equal":
        return actual == expected
    if operator == "greater_than":
        return actual > expected
    if operator == "greater_or_equal":
        return actual >= expected
    if operator == "less_than":
        return actual < expected
    if operator == "less_or_equal":
        return actual <= expected
    return False


def _compare_date(actual: date, operator: object, expected: object) -> bool:
    if not isinstance(actual, date) or not isinstance(expected, str):
        return False
    try:
        expected_date = date.fromisoformat(expected)
    except ValueError:
        return False
    if expected_date.isoformat() != expected:
        return False
    if operator == "on":
        return actual == expected_date
    if operator == "before":
        return actual < expected_date
    if operator == "after":
        return actual > expected_date
    return False


def _compare_identity(
    actual: object, operator: object, expected: object, allowed_values: set[str]
) -> bool:
    return (
        operator == "equal"
        and isinstance(expected, str)
        and expected in allowed_values
        and actual == expected
    )


def _compare_account_id(actual: int | None, operator: object, expected: object) -> bool:
    return (
        operator == "equal"
        and _is_int(actual)
        and _is_int(expected)
        and actual > 0
        and expected > 0
        and actual == expected
    )


def _compare_provider_key(actual: str | None, operator: object, expected: object) -> bool:
    return (
        operator == "equal"
        and isinstance(actual, str)
        and isinstance(expected, str)
        and actual == expected
    )


def _normalize_text(value: str) -> str:
    return _normalized_text(value).casefold()


def _normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _is_valid_predicate(node: PredicateCondition) -> bool:
    if not isinstance(node.field, str) or not isinstance(node.operator, str):
        return False
    if node.field == "description":
        return (
            node.operator in _TEXT_OPERATORS
            and isinstance(node.value, str)
            and bool(_normalized_text(node.value))
            and len(_normalized_text(node.value)) <= 255
        )
    if node.field == "merchant_key":
        return (
            node.operator in _TEXT_OPERATORS
            and isinstance(node.value, str)
            and bool(merchant_key(node.value))
        )
    if node.field == "amount_cents":
        return node.operator in _AMOUNT_OPERATORS and _is_int(node.value)
    if node.field == "transaction_date":
        return node.operator in _DATE_OPERATORS and _is_iso_date(node.value)
    if node.field == "direction":
        return (
            node.operator == "equal" and isinstance(node.value, str) and node.value in _DIRECTIONS
        )
    if node.field == "account_id":
        return node.operator == "equal" and _is_int(node.value) and node.value > 0
    if node.field == "provider_key":
        return (
            node.operator == "equal"
            and isinstance(node.value, str)
            and node.value in _PROVIDER_KEYS
        )
    return False


def _is_valid_tree(node: object, *, depth: int, predicate_count: list[int]) -> bool:
    if depth > MAX_TREE_DEPTH:
        return False
    if isinstance(node, PredicateCondition):
        predicate_count[0] += 1
        return predicate_count[0] <= MAX_PREDICATES and _is_valid_predicate(node)
    if isinstance(node, (AllCondition, AnyCondition)):
        return (
            isinstance(node.children, tuple)
            and bool(node.children)
            and all(
                _is_valid_tree(child, depth=depth + 1, predicate_count=predicate_count)
                for child in node.children
            )
        )
    if isinstance(node, NotCondition):
        return _is_valid_tree(node.child, depth=depth + 1, predicate_count=predicate_count)
    return False


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _invalid_result() -> ConditionResult:
    return ConditionResult(False, "invalid condition")
