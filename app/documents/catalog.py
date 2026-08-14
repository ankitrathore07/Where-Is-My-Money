from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from app.documents.types import DocumentCategory

MAX_QUEUE_FILES = 10
ALLOWED_QUEUE_SUFFIXES = frozenset({".csv", ".pdf", ".png", ".jpg", ".jpeg"})
CSV_CONTENT_TYPES = frozenset(
    {"text/csv", "application/csv", "application/vnd.ms-excel", "application/octet-stream"}
)
CSV_CONTENT_TYPES_BY_SUFFIX: Mapping[str, frozenset[str]] = MappingProxyType(
    {".csv": CSV_CONTENT_TYPES}
)
PAYSLIP_CONTENT_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        ".pdf": frozenset({"application/pdf"}),
        ".png": frozenset({"image/png"}),
        ".jpg": frozenset({"image/jpeg", "image/jpg"}),
        ".jpeg": frozenset({"image/jpeg", "image/jpg"}),
    }
)
STATEMENT_CONTENT_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        ".csv": CSV_CONTENT_TYPES,
        **PAYSLIP_CONTENT_TYPES,
    }
)
TRANSACTION_CONTENT_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {".csv": CSV_CONTENT_TYPES, ".pdf": PAYSLIP_CONTENT_TYPES[".pdf"]}
)
NO_CONTENT_TYPES: Mapping[str, frozenset[str]] = MappingProxyType({})


class DocumentUploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


DOCUMENT_CATEGORIES = (
    DocumentCategory(
        "bank_transaction_statement",
        "Bank transaction statement",
        "transaction_import",
        TRANSACTION_CONTENT_TYPES,
        frozenset({"checking", "savings"}),
    ),
    DocumentCategory(
        "credit_card_transaction_statement",
        "Credit-card transaction statement",
        "transaction_import",
        TRANSACTION_CONTENT_TYPES,
        frozenset({"credit_card"}),
    ),
    DocumentCategory("payslip", "Payslip", "payslip", PAYSLIP_CONTENT_TYPES),
    DocumentCategory(
        "bank_balance_statement",
        "Checking or savings balance statement",
        "statement_balance",
        STATEMENT_CONTENT_TYPES,
    ),
    DocumentCategory(
        "credit_card_balance_statement",
        "Credit-card balance statement",
        "statement_balance",
        STATEMENT_CONTENT_TYPES,
    ),
    DocumentCategory(
        "retirement_401k_statement",
        "401(k) retirement statement",
        "statement_balance",
        STATEMENT_CONTENT_TYPES,
    ),
    DocumentCategory(
        "brokerage_statement",
        "Brokerage or stocks statement",
        "statement_balance",
        STATEMENT_CONTENT_TYPES,
    ),
    DocumentCategory(
        "mortgage_statement", "Mortgage statement", "statement_balance", STATEMENT_CONTENT_TYPES
    ),
    DocumentCategory(
        "loan_statement", "Loan statement", "statement_balance", STATEMENT_CONTENT_TYPES
    ),
    DocumentCategory(
        "other_account_statement",
        "Other account statement",
        "statement_balance",
        STATEMENT_CONTENT_TYPES,
    ),
    DocumentCategory("unlisted", "Category not listed", None, NO_CONTENT_TYPES),
)
_CATEGORY_BY_KEY: Mapping[str, DocumentCategory] = MappingProxyType(
    {category.key: category for category in DOCUMENT_CATEGORIES}
)
_LEGACY_TRANSACTION_CATEGORY = DocumentCategory(
    "transaction_statement",
    "Bank or credit-card transaction statement",
    "transaction_import",
    TRANSACTION_CONTENT_TYPES,
)


def get_document_category(key: str) -> DocumentCategory | None:
    if key == _LEGACY_TRANSACTION_CATEGORY.key:
        return _LEGACY_TRANSACTION_CATEGORY
    return _CATEGORY_BY_KEY.get(key)


def compatible_account_types(category_key: str) -> frozenset[str]:
    """Return account types accepted by an account-linked document category."""
    category = get_document_category(category_key)
    return category.compatible_account_types if category is not None else frozenset()


def validate_processable_upload(
    category_key: str, filename: str, content_type: str | None
) -> DocumentCategory:
    category = get_document_category(category_key)
    if category is None:
        raise DocumentUploadValidationError("unknown_category", "Choose a valid document category.")
    if category.processor is None:
        raise DocumentUploadValidationError(
            "processor_unavailable",
            "This document category is recognized, but processing is not available yet.",
        )
    suffix = Path(filename).suffix.casefold()
    allowed_types = category.content_types_by_suffix.get(suffix)
    normalized_type = (content_type or "").casefold()
    if allowed_types is None or normalized_type not in allowed_types:
        if category.processor == "transaction_import":
            expected = "CSV or PDF"
        elif category.processor == "payslip":
            expected = "PDF, PNG, or JPEG"
        else:
            expected = "CSV, PDF, PNG, or JPEG"
        raise DocumentUploadValidationError(
            "category_format_mismatch", f"{category.label} files must use {expected}."
        )
    return category


def client_catalog(
    *, max_csv_bytes: int, max_payslip_bytes: int, max_statement_bytes: int
) -> list[dict[str, object]]:
    limits = {
        "transaction_import": max(max_csv_bytes, max_statement_bytes),
        "payslip": max_payslip_bytes,
        "statement_balance": max_statement_bytes,
    }
    return [
        {
            "key": category.key,
            "label": category.label,
            "supported": category.processor is not None,
            "accepted_suffixes": sorted(category.accepted_suffixes),
            "max_bytes": limits.get(category.processor),
            "compatible_account_types": sorted(category.compatible_account_types),
        }
        for category in DOCUMENT_CATEGORIES
    ]
