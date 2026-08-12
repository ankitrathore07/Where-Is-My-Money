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
NO_CONTENT_TYPES: Mapping[str, frozenset[str]] = MappingProxyType({})


class DocumentUploadValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


DOCUMENT_CATEGORIES = (
    DocumentCategory(
        "transaction_statement",
        "Bank or credit-card transaction statement",
        "csv_import",
        CSV_CONTENT_TYPES_BY_SUFFIX,
    ),
    DocumentCategory("payslip", "Payslip", "payslip", PAYSLIP_CONTENT_TYPES),
    DocumentCategory(
        "retirement_401k_statement", "401(k) retirement statement", None, NO_CONTENT_TYPES
    ),
    DocumentCategory(
        "brokerage_statement", "Brokerage or stocks statement", None, NO_CONTENT_TYPES
    ),
    DocumentCategory("mortgage_statement", "Mortgage statement", None, NO_CONTENT_TYPES),
    DocumentCategory("loan_statement", "Loan statement", None, NO_CONTENT_TYPES),
    DocumentCategory("other_account_statement", "Other account statement", None, NO_CONTENT_TYPES),
    DocumentCategory("unlisted", "Category not listed", None, NO_CONTENT_TYPES),
)
_CATEGORY_BY_KEY: Mapping[str, DocumentCategory] = MappingProxyType(
    {category.key: category for category in DOCUMENT_CATEGORIES}
)


def get_document_category(key: str) -> DocumentCategory | None:
    return _CATEGORY_BY_KEY.get(key)


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
        expected = "CSV" if category.processor == "csv_import" else "PDF, PNG, or JPEG"
        raise DocumentUploadValidationError(
            "category_format_mismatch", f"{category.label} files must use {expected}."
        )
    return category


def client_catalog(*, max_csv_bytes: int, max_payslip_bytes: int) -> list[dict[str, object]]:
    limits = {"csv_import": max_csv_bytes, "payslip": max_payslip_bytes}
    return [
        {
            "key": category.key,
            "label": category.label,
            "supported": category.processor is not None,
            "accepted_suffixes": sorted(category.accepted_suffixes),
            "max_bytes": limits.get(category.processor),
        }
        for category in DOCUMENT_CATEGORIES
    ]
