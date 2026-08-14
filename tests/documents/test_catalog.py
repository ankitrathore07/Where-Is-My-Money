import pytest

import app.documents.catalog as catalog
from app.documents.catalog import (
    ALLOWED_QUEUE_SUFFIXES,
    DOCUMENT_CATEGORIES,
    MAX_QUEUE_FILES,
    DocumentUploadValidationError,
    client_catalog,
    compatible_account_types,
    validate_processable_upload,
)

EXPECTED_KEYS = (
    "bank_transaction_statement",
    "credit_card_transaction_statement",
    "payslip",
    "bank_balance_statement",
    "credit_card_balance_statement",
    "retirement_401k_statement",
    "brokerage_statement",
    "mortgage_statement",
    "loan_statement",
    "other_account_statement",
    "unlisted",
)


def test_catalog_exposes_stable_manual_categories_and_supported_processors() -> None:
    assert tuple(category.key for category in DOCUMENT_CATEGORIES) == EXPECTED_KEYS
    assert {category.key for category in DOCUMENT_CATEGORIES if category.processor} == {
        "bank_transaction_statement",
        "credit_card_transaction_statement",
        "payslip",
        "bank_balance_statement",
        "credit_card_balance_statement",
        "retirement_401k_statement",
        "brokerage_statement",
        "mortgage_statement",
        "loan_statement",
        "other_account_statement",
    }
    assert ALLOWED_QUEUE_SUFFIXES == frozenset({".csv", ".pdf", ".png", ".jpg", ".jpeg"})
    assert MAX_QUEUE_FILES == 10


def test_transaction_categories_have_distinct_account_compatibility() -> None:
    assert compatible_account_types("bank_transaction_statement") == frozenset(
        {"checking", "savings"}
    )
    assert compatible_account_types("credit_card_transaction_statement") == frozenset(
        {"credit_card"}
    )
    assert compatible_account_types("payslip") == frozenset()


@pytest.mark.parametrize(
    "key,filename,content_type,processor",
    [
        ("bank_transaction_statement", "checking.csv", "text/csv", "transaction_import"),
        (
            "bank_transaction_statement",
            "checking.csv",
            "application/octet-stream",
            "transaction_import",
        ),
        ("bank_transaction_statement", "checking.pdf", "application/pdf", "transaction_import"),
        (
            "credit_card_transaction_statement",
            "card.csv",
            "text/csv",
            "transaction_import",
        ),
        ("transaction_statement", "legacy.csv", "text/csv", "transaction_import"),
        ("payslip", "pay.pdf", "application/pdf", "payslip"),
        ("payslip", "pay.jpeg", "image/jpeg", "payslip"),
        ("retirement_401k_statement", "plan.csv", "text/csv", "statement_balance"),
        ("brokerage_statement", "brokerage.pdf", "application/pdf", "statement_balance"),
        ("mortgage_statement", "mortgage.png", "image/png", "statement_balance"),
        ("loan_statement", "loan.jpg", "image/jpeg", "statement_balance"),
        ("other_account_statement", "other.jpeg", "image/jpeg", "statement_balance"),
    ],
)
def test_processable_metadata_returns_the_selected_category(
    key: str, filename: str, content_type: str, processor: str
) -> None:
    category = validate_processable_upload(key, filename, content_type)
    assert category.key == key
    assert category.processor == processor


@pytest.mark.parametrize(
    "key,filename,content_type,code",
    [
        ("missing", "checking.csv", "text/csv", "unknown_category"),
        ("unlisted", "account.pdf", "application/pdf", "processor_unavailable"),
        (
            "bank_transaction_statement",
            "checking.png",
            "image/png",
            "category_format_mismatch",
        ),
        ("payslip", "pay.pdf", "text/plain", "category_format_mismatch"),
    ],
)
def test_invalid_metadata_has_a_safe_stable_error(
    key: str, filename: str, content_type: str, code: str
) -> None:
    with pytest.raises(DocumentUploadValidationError) as error:
        validate_processable_upload(key, filename, content_type)
    assert error.value.code == code
    assert error.value.message


def test_client_catalog_contains_no_classifier_or_server_only_objects() -> None:
    payload = client_catalog(
        max_csv_bytes=5_000_000,
        max_payslip_bytes=10_000_000,
        max_statement_bytes=12_000_000,
    )
    assert payload[0] == {
        "key": "bank_transaction_statement",
        "label": "Bank transaction statement",
        "supported": True,
        "accepted_suffixes": [".csv", ".pdf"],
        "max_bytes": 12_000_000,
        "compatible_account_types": ["checking", "savings"],
    }
    assert payload[1]["compatible_account_types"] == ["credit_card"]
    assert payload[3]["supported"] is True
    assert payload[3]["accepted_suffixes"] == [".csv", ".jpeg", ".jpg", ".pdf", ".png"]
    assert payload[3]["max_bytes"] == 12_000_000
    assert "suggestion" not in payload[0]
    assert "confidence" not in payload[0]


def test_category_content_type_mappings_are_immutable() -> None:
    transaction_statement = DOCUMENT_CATEGORIES[0]

    with pytest.raises(TypeError):
        transaction_statement.content_types_by_suffix[".pdf"] = frozenset({"application/pdf"})


def test_category_lookup_mapping_is_immutable() -> None:
    with pytest.raises(TypeError):
        catalog._CATEGORY_BY_KEY["unlisted"] = DOCUMENT_CATEGORIES[0]
