from dataclasses import dataclass
from datetime import date

SUPPORTED_STATEMENT_CATEGORIES = (
    "investment_401k",
    "brokerage",
    "mortgage",
    "loan",
    "other",
)

_COMPATIBLE_ACCOUNT_TYPES = {
    "investment_401k": frozenset({"investment_401k"}),
    "brokerage": frozenset({"investment_brokerage"}),
    "mortgage": frozenset({"mortgage"}),
    "loan": frozenset({"auto_loan", "student_loan"}),
    "other": frozenset({"other"}),
}


class StatementFormatError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StatementReviewValidationError(ValueError):
    def __init__(self, field_errors: dict[str, str]) -> None:
        super().__init__("Correct the highlighted statement fields before confirming.")
        self.field_errors = field_errors


@dataclass(frozen=True)
class StatementCandidate:
    account_name: str
    institution: str | None
    account_last_four: str | None
    balance_cents: int
    as_of_date: date
    extraction_method: str

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "account_name": self.account_name,
            "institution": self.institution,
            "account_last_four": self.account_last_four,
            "balance_cents": self.balance_cents,
            "as_of_date": self.as_of_date.isoformat(),
            "extraction_method": self.extraction_method,
        }


@dataclass(frozen=True)
class StatementReviewValues:
    account_id: int
    account_name: str
    institution: str | None
    account_last_four: str | None
    balance_cents: int
    as_of_date: date

    def to_json(self) -> dict[str, str | int | None]:
        return {
            "account_id": self.account_id,
            "account_name": self.account_name,
            "institution": self.institution,
            "account_last_four": self.account_last_four,
            "balance_cents": self.balance_cents,
            "as_of_date": self.as_of_date.isoformat(),
        }


def compatible_account_types(category: str) -> frozenset[str]:
    try:
        return _COMPATIBLE_ACCOUNT_TYPES[category]
    except KeyError:
        raise StatementFormatError(
            "unsupported_category", "This account statement category is not supported."
        ) from None
