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
