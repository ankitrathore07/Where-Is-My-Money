from dataclasses import dataclass
from datetime import date
from typing import Literal

DateFormat = Literal["iso", "mdy", "dmy"]
AmountMode = Literal["single", "split"]
AmountSign = Literal["as_is", "invert"]


@dataclass(frozen=True)
class CsvSourceRow:
    row_number: int
    values: dict[str, str]


@dataclass(frozen=True)
class CsvDocument:
    headers: tuple[str, ...]
    rows: tuple[CsvSourceRow, ...]
    delimiter: str


@dataclass(frozen=True)
class ColumnMapping:
    date_column: str
    description_column: str
    amount_mode: AmountMode
    amount_column: str | None
    debit_column: str | None
    credit_column: str | None
    date_format: DateFormat
    amount_sign: AmountSign

    def to_json(self) -> dict[str, str | None]:
        return {
            "date_column": self.date_column,
            "description_column": self.description_column,
            "amount_mode": self.amount_mode,
            "amount_column": self.amount_column,
            "debit_column": self.debit_column,
            "credit_column": self.credit_column,
            "date_format": self.date_format,
            "amount_sign": self.amount_sign,
        }


@dataclass(frozen=True)
class NormalizedTransaction:
    row_number: int
    transaction_date: date
    description: str
    normalized_merchant: str
    amount_cents: int


@dataclass(frozen=True)
class FingerprintedTransaction:
    transaction: NormalizedTransaction
    occurrence: int
    fingerprint: str


@dataclass(frozen=True)
class ReviewRow:
    row_number: int
    date_value: str
    description_value: str
    amount_value: str
    normalized: NormalizedTransaction | None
    fingerprint: str | None
    duplicate: bool
    included: bool
    field_errors: dict[str, str]
    normalized_merchant: str | None = None
    category_id: int | None = None
    category_name: str | None = None
    is_subscription: bool | None = None
    categorization_source: str | None = None
    tag_ids: tuple[int, ...] = ()
    billing_period_months: int | None = None


@dataclass(frozen=True)
class ImportReview:
    rows: tuple[ReviewRow, ...]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int


@dataclass(frozen=True)
class RowEdit:
    row_number: int
    include: bool
    date_value: str
    description_value: str
    amount_value: str
    normalized_merchant: str | None = None
    category_id: int | None = None
    is_subscription: bool | None = None
    categorization_source: str | None = None
    tag_ids: tuple[int, ...] | None = None
    billing_period_months: int | None = None
    billing_period_submitted: bool = False
    original_normalized_merchant: str | None = None
    original_category_id: int | None = None
    original_is_subscription: bool | None = None
    original_categorization_source: str | None = None
    original_tag_ids: tuple[int, ...] | None = None
    original_billing_period_months: int | None = None
