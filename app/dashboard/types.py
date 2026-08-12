from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AccountPosition:
    account_id: int
    name: str
    account_type: str
    institution: str | None
    is_liability: bool
    balance_cents: int | None
    as_of_date: date | None


@dataclass(frozen=True)
class PositionSummary:
    assets_cents: int
    liabilities_cents: int
    net_worth_cents: int
    cash_cents: int
    missing_balance_count: int
    accounts: tuple[AccountPosition, ...]


@dataclass(frozen=True)
class AnnualPosition:
    year: int
    assets_cents: int | None
    liabilities_cents: int | None
    net_worth_cents: int | None


@dataclass(frozen=True)
class AnnualCashFlow:
    year: int
    income_cents: int | None
    spending_cents: int | None
    savings_cents: int | None
    savings_rate_basis_points: int | None
    needs_review_count: int


@dataclass(frozen=True)
class DashboardHighlight:
    kind: str
    title: str
    detail: str
    tone: str


@dataclass(frozen=True)
class SpendingPeriod:
    key: str
    label: str
    start_date: date
    end_date: date
    selected_month: str


@dataclass(frozen=True)
class SpendingBreakdown:
    key: str
    label: str
    spending_cents: int
    percentage_basis_points: int
    transaction_count: int
    transactions_url: str


@dataclass(frozen=True)
class SpendingReport:
    period: SpendingPeriod
    total_cents: int
    transaction_count: int
    needs_review_count: int
    categories: tuple[SpendingBreakdown, ...]
    merchants: tuple[SpendingBreakdown, ...]
    all_transactions_url: str
    review_transactions_url: str


@dataclass(frozen=True)
class DashboardReport:
    as_of_date: date | None
    has_transactions: bool
    position: PositionSummary
    net_worth_series: tuple[AnnualPosition, ...]
    cash_flow_series: tuple[AnnualCashFlow, ...]
    highlights: tuple[DashboardHighlight, ...]
