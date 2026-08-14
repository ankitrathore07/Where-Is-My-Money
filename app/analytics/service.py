"""Exact aggregate calculations safe to expose through future LLM tools."""

import calendar
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.analytics.types import CashFlowSummary, HouseholdSpendingSummary
from app.db.models import Category, Transaction


def _bounds(start_date: date, end_date: date):
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    predicates = [Transaction.date >= datetime.combine(start_date, time.min, tzinfo=UTC)]
    if end_date == date.max:
        predicates.append(Transaction.date <= datetime.combine(end_date, time.max, tzinfo=UTC))
    else:
        predicates.append(
            Transaction.date < datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
        )
    return predicates


def _rate(savings_cents: int, income_cents: int) -> int | None:
    if not income_cents:
        return None
    return int(
        (Decimal(savings_cents) / Decimal(income_cents) * Decimal(10_000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def summarize_cash_flow(
    session: Session,
    workspace_id: int,
    start_date: date,
    end_date: date,
) -> CashFlowSummary:
    """Return income, spending, and savings for one inclusive custom range."""
    rows = session.execute(
        select(Transaction, Category)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(Transaction.workspace_id == workspace_id, *_bounds(start_date, end_date))
    )
    income_cents = 0
    spending_cents = 0
    needs_review_count = 0
    for transaction, category in rows:
        accessible = category is not None and (
            category.workspace_id is None or category.workspace_id == workspace_id
        )
        kind = category.kind if accessible else None
        if kind == "transfer":
            continue
        if kind == "income" and transaction.amount_cents > 0:
            income_cents += transaction.amount_cents
        elif kind == "expense" and transaction.amount_cents < 0:
            spending_cents -= transaction.amount_cents
        else:
            needs_review_count += 1
    savings_cents = income_cents - spending_cents
    return CashFlowSummary(
        start_date,
        end_date,
        income_cents,
        spending_cents,
        savings_cents,
        _rate(savings_cents, income_cents),
        needs_review_count,
    )


def summarize_recent_cash_flow(
    session: Session,
    workspace_id: int,
    end_date: date,
    months: int,
) -> CashFlowSummary:
    """Return a rolling 1-120 month savings summary ending on ``end_date``."""
    if months < 1 or months > 120:
        raise ValueError("months must be between 1 and 120")
    month_index = (end_date.year - 1) * 12 + end_date.month - 1 - months
    if month_index < 0:
        shifted = date.min
    else:
        year, zero_based_month = divmod(month_index, 12)
        year += 1
        month = zero_based_month + 1
        shifted = date(
            year,
            month,
            min(end_date.day, calendar.monthrange(year, month)[1]),
        )
    start_date = shifted + timedelta(days=1) if shifted > date.min else date.min
    return summarize_cash_flow(session, workspace_id, start_date, end_date)


def _months_inclusive(start_date: date, end_date: date) -> int:
    return (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1


def summarize_household_spending(
    session: Session,
    workspace_id: int,
    start_date: date,
    end_date: date,
) -> HouseholdSpendingSummary:
    """Normalize tagged household, Housing, and Utilities expenses to a monthly amount."""
    rows = session.execute(
        select(Transaction, Category)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .options(selectinload(Transaction.tags))
        .where(Transaction.workspace_id == workspace_id, *_bounds(start_date, end_date))
        .order_by(Transaction.date, Transaction.id)
    )
    window_months = _months_inclusive(start_date, end_date)
    uncadenced: dict[str, int] = defaultdict(int)
    cadenced: dict[str, list[Decimal]] = defaultdict(list)
    total_paid_cents = 0
    transaction_count = 0
    needs_review_count = 0
    for transaction, category in rows:
        if transaction.amount_cents >= 0:
            continue
        accessible_tag_names = {
            tag.name_key
            for tag in transaction.tags
            if tag.workspace_id is None or tag.workspace_id == workspace_id
        }
        tagged_household = "household expenditure" in accessible_tag_names
        accessible_category = category is not None and (
            category.workspace_id is None or category.workspace_id == workspace_id
        )
        category_needs_review = (
            not accessible_category
            or category.kind != "expense"
            or category.name_key == "uncategorized"
        )
        if category_needs_review:
            if tagged_household:
                needs_review_count += 1
            continue
        household = category.name_key in {"housing", "utilities"} or tagged_household
        if not household:
            continue
        amount = -transaction.amount_cents
        total_paid_cents += amount
        transaction_count += 1
        merchant = (
            (transaction.normalized_merchant or "").strip().casefold()
            or transaction.description.strip().casefold()
            or f"transaction:{transaction.id}"
        )
        if transaction.billing_period_months:
            cadenced[merchant].append(Decimal(amount) / Decimal(transaction.billing_period_months))
        else:
            uncadenced[merchant] += amount

    normalized = sum(
        (Decimal(amount) / Decimal(window_months) for amount in uncadenced.values()),
        Decimal(0),
    )
    normalized += sum(
        (sum(values, Decimal(0)) / Decimal(len(values)) for values in cadenced.values()),
        Decimal(0),
    )
    normalized_monthly_cents = int(normalized.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return HouseholdSpendingSummary(
        start_date,
        end_date,
        total_paid_cents,
        normalized_monthly_cents,
        transaction_count,
        needs_review_count,
    )
