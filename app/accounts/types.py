from dataclasses import dataclass


@dataclass(frozen=True)
class AccountTypeOption:
    value: str
    label: str
    default_is_liability: bool | None


ACCOUNT_TYPE_OPTIONS = (
    AccountTypeOption("checking", "Checking", False),
    AccountTypeOption("savings", "Savings", False),
    AccountTypeOption("credit_card", "Credit card", True),
    AccountTypeOption("investment_401k", "401(k)", False),
    AccountTypeOption("investment_brokerage", "Brokerage", False),
    AccountTypeOption("mortgage", "Mortgage", True),
    AccountTypeOption("auto_loan", "Auto loan", True),
    AccountTypeOption("student_loan", "Student loan", True),
    AccountTypeOption("other", "Other", None),
)


@dataclass(frozen=True)
class AccountInput:
    name: str
    account_type: str
    institution: str
    is_liability: bool


@dataclass(frozen=True)
class ManualBalanceInput:
    amount: str
    as_of_date: str
