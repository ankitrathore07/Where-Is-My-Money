import pytest

import app.documents.catalog as catalog
from app.documents.catalog import (
    ALLOWED_QUEUE_SUFFIXES,
    DOCUMENT_CATEGORIES,
    MAX_QUEUE_FILES,
    DocumentUploadValidationError,
    client_catalog,
    validate_processable_upload,
)

EXPECTED_KEYS = (
    "transaction_statement",
    "payslip",
    "retirement_401k_statement",
    "brokerage_statement",
    "mortgage_statement",
    "loan_statement",
    "other_account_statement",
    "unlisted",
)


def test_catalog_exposes_stable_manual_categories_and_only_two_processors() -> None:
    assert tuple(category.key for category in DOCUMENT_CATEGORIES) == EXPECTED_KEYS
    assert {category.key for category in DOCUMENT_CATEGORIES if category.processor} == {
        "transaction_statement",
        "payslip",
    }
    assert ALLOWED_QUEUE_SUFFIXES == frozenset({".csv", ".pdf", ".png", ".jpg", ".jpeg"})
    assert MAX_QUEUE_FILES == 10


@pytest.mark.parametrize(
    "key,filename,content_type,processor",
    [
        ("transaction_statement", "checking.csv", "text/csv", "csv_import"),
        ("transaction_statement", "checking.csv", "application/octet-stream", "csv_import"),
        ("payslip", "pay.pdf", "application/pdf", "payslip"),
        ("payslip", "pay.jpeg", "image/jpeg", "payslip"),
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
        ("brokerage_statement", "account.pdf", "application/pdf", "processor_unavailable"),
        ("unlisted", "account.pdf", "application/pdf", "processor_unavailable"),
        ("transaction_statement", "checking.pdf", "application/pdf", "category_format_mismatch"),
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
    payload = client_catalog(max_csv_bytes=5_000_000, max_payslip_bytes=10_000_000)
    assert payload[0] == {
        "key": "transaction_statement",
        "label": "Bank or credit-card transaction statement",
        "supported": True,
        "accepted_suffixes": [".csv"],
        "max_bytes": 5_000_000,
    }
    assert payload[2]["supported"] is False
    assert payload[2]["accepted_suffixes"] == []
    assert "suggestion" not in payload[0]
    assert "confidence" not in payload[0]


def test_category_content_type_mappings_are_immutable() -> None:
    transaction_statement = DOCUMENT_CATEGORIES[0]

    with pytest.raises(TypeError):
        transaction_statement.content_types_by_suffix[".pdf"] = frozenset({"application/pdf"})


def test_category_lookup_mapping_is_immutable() -> None:
    with pytest.raises(TypeError):
        catalog._CATEGORY_BY_KEY["unlisted"] = DOCUMENT_CATEGORIES[0]
