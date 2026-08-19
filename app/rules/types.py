"""Immutable typed values used to describe workspace rule conditions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

type ConditionField = Literal[
    "description",
    "merchant_key",
    "amount_cents",
    "transaction_date",
    "direction",
    "account_id",
    "provider_key",
]
type TextOperator = Literal["exact", "contains", "starts_with", "ends_with"]
type AmountOperator = Literal[
    "equal", "greater_than", "greater_or_equal", "less_than", "less_or_equal"
]
type DateOperator = Literal["on", "before", "after"]
type EqualityOperator = Literal["equal"]
type ConditionOperator = TextOperator | AmountOperator | DateOperator | EqualityOperator
type ConditionValue = str | int
type Direction = Literal["income", "expense", "zero"]


@dataclass(frozen=True)
class RuleContext:
    description: str
    merchant_key: str
    amount_cents: int
    transaction_date: date
    direction: Direction
    account_id: int | None
    provider_key: str | None


@dataclass(frozen=True)
class PredicateCondition:
    field: ConditionField
    operator: ConditionOperator
    value: ConditionValue


@dataclass(frozen=True)
class AllCondition:
    children: tuple[ConditionNode, ...]


@dataclass(frozen=True)
class AnyCondition:
    children: tuple[ConditionNode, ...]


@dataclass(frozen=True)
class NotCondition:
    child: ConditionNode


type ConditionNode = PredicateCondition | AllCondition | AnyCondition | NotCondition
