"""Strict parsing and workspace-scoped transaction queries."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, cast

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Category, Transaction

Direction = Literal["all", "expense", "income"]
Subscription = Literal["all", "yes", "no"]
ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FilterValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Invalid transaction filters")
        self.field_errors = field_errors


@dataclass(frozen=True)
class TransactionFilters:
    start_date: date | None = None
    end_date: date | None = None
    category_id: int | None = None
    direction: Direction = "all"
    subscription: Subscription = "all"
    query: str = ""
    merchant: str = ""
    spending_only: bool = False
    review_needed: bool = False
    page: int = 1
    page_size: int = 50


@dataclass(frozen=True)
class TransactionPage:
    items: tuple[Transaction, ...]
    total_items: int
    page: int
    page_size: int
    total_pages: int


def _parse_date(value: str, field: str, errors: dict[str, str]) -> date | None:
    if not value:
        return None
    if not ISO_DATE_PATTERN.fullmatch(value):
        errors[field] = "Use a date in YYYY-MM-DD format."
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors[field] = "Use a valid calendar date."
        return None


def _positive_integer(
    value: str, field: str, errors: dict[str, str], *, optional: bool = False
) -> int | None:
    if optional and not value:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        errors[field] = "Use a positive whole number."
        return None
    if parsed <= 0:
        errors[field] = "Use a positive whole number."
        return None
    return parsed


def parse_filters(params: Mapping[str, str]) -> TransactionFilters:
    """Parse bounded URL filters and aggregate independent field errors."""
    errors: dict[str, str] = {}
    start_date = _parse_date(params.get("start_date", "").strip(), "start_date", errors)
    end_date = _parse_date(params.get("end_date", "").strip(), "end_date", errors)
    if start_date is not None and end_date is not None and end_date < start_date:
        errors["end_date"] = "End date must be on or after start date."

    direction_value = params.get("direction", "all").strip() or "all"
    if direction_value not in {"all", "expense", "income"}:
        errors["direction"] = "Choose all, expense, or income."

    subscription_value = params.get("subscription", "all").strip() or "all"
    if subscription_value not in {"all", "yes", "no"}:
        errors["subscription"] = "Choose all, yes, or no."

    category_id = _positive_integer(
        params.get("category_id", "").strip(), "category_id", errors, optional=True
    )
    page = _positive_integer(params.get("page", "1").strip(), "page", errors)
    query = params.get("q", "").strip()
    if len(query) > 100:
        errors["q"] = "Search must be 100 characters or fewer."
    merchant = params.get("merchant", "").strip()
    if len(merchant) > 512:
        errors["merchant"] = "Merchant must be 512 characters or fewer."
    spending_value = params.get("spending", "").strip()
    if spending_value not in {"", "only"}:
        errors["spending"] = "Choose categorized spending only."
    review_value = params.get("review", "").strip()
    if review_value not in {"", "needed"}:
        errors["review"] = "Choose transactions needing review."
    if spending_value and review_value:
        errors["review"] = "Choose spending or review-needed transactions, not both."

    if errors:
        raise FilterValidationError(errors)
    assert page is not None
    return TransactionFilters(
        start_date=start_date,
        end_date=end_date,
        category_id=category_id,
        direction=cast(Direction, direction_value),
        subscription=cast(Subscription, subscription_value),
        query=query,
        merchant=merchant,
        spending_only=spending_value == "only",
        review_needed=review_value == "needed",
        page=page,
    )


def _category_is_available(session: Session, workspace_id: int, category_id: int) -> bool:
    return (
        session.scalar(
            select(Category.id).where(
                Category.id == category_id,
                or_(Category.workspace_id.is_(None), Category.workspace_id == workspace_id),
            )
        )
        is not None
    )


def list_transactions(
    session: Session, workspace_id: int, filters: TransactionFilters
) -> TransactionPage:
    """Return one deterministic page that can never cross a workspace boundary."""
    if filters.category_id is not None and not _category_is_available(
        session, workspace_id, filters.category_id
    ):
        raise FilterValidationError({"category_id": "Choose an available category."})

    predicates = [Transaction.workspace_id == workspace_id]
    accessible_category = or_(
        Category.workspace_id.is_(None), Category.workspace_id == workspace_id
    )
    categorized_spending = Transaction.category.has(
        and_(
            Category.kind == "expense",
            func.lower(func.trim(Category.name)) != "uncategorized",
            accessible_category,
        )
    )
    accessible_transfer = Transaction.category.has(
        and_(Category.kind == "transfer", accessible_category)
    )
    if filters.start_date is not None:
        predicates.append(
            Transaction.date >= datetime.combine(filters.start_date, time.min, tzinfo=UTC)
        )
    if filters.end_date is not None:
        predicates.append(
            Transaction.date
            < datetime.combine(filters.end_date + timedelta(days=1), time.min, tzinfo=UTC)
        )
    if filters.category_id is not None:
        predicates.append(Transaction.category_id == filters.category_id)
    if filters.direction == "expense":
        predicates.append(Transaction.amount_cents < 0)
    elif filters.direction == "income":
        predicates.append(Transaction.amount_cents > 0)
    if filters.subscription == "yes":
        predicates.append(Transaction.is_subscription.is_(True))
    elif filters.subscription == "no":
        predicates.append(Transaction.is_subscription.is_(False))
    if filters.query:
        lowered = filters.query.casefold()
        predicates.append(
            or_(
                func.lower(Transaction.description).contains(lowered, autoescape=True),
                func.lower(Transaction.normalized_merchant).contains(lowered, autoescape=True),
            )
        )
    if filters.merchant:
        merchant_identity = func.trim(
            func.coalesce(
                func.nullif(func.trim(Transaction.normalized_merchant), ""),
                func.nullif(func.trim(Transaction.description), ""),
                literal("Merchant not available"),
            )
        )
        predicates.append(merchant_identity == filters.merchant)
    if filters.spending_only:
        predicates.extend((Transaction.amount_cents < 0, categorized_spending))
    elif filters.review_needed:
        predicates.extend(
            (
                Transaction.amount_cents < 0,
                ~categorized_spending,
                ~accessible_transfer,
            )
        )

    total_items = session.scalar(select(func.count()).select_from(Transaction).where(*predicates))
    assert total_items is not None
    items = tuple(
        session.scalars(
            select(Transaction)
            .options(
                joinedload(Transaction.category),
                joinedload(Transaction.merchant_rule),
            )
            .where(*predicates)
            .order_by(Transaction.date.desc(), Transaction.id.desc())
            .limit(filters.page_size)
            .offset((filters.page - 1) * filters.page_size)
        )
    )
    total_pages = (total_items + filters.page_size - 1) // filters.page_size if total_items else 0
    return TransactionPage(
        items=items,
        total_items=total_items,
        page=filters.page,
        page_size=filters.page_size,
        total_pages=total_pages,
    )
