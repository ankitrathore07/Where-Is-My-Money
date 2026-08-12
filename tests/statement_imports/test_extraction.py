import pytest

from app.payslips.extraction import DocumentExtractionError, ExtractedText
from app.statement_imports.extraction import StatementDocumentExtractor


class StubDocumentExtractor:
    def __init__(self, result: ExtractedText | Exception) -> None:
        self.result = result

    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_statement_adapter_returns_local_document_text() -> None:
    adapter = StatementDocumentExtractor(
        StubDocumentExtractor(ExtractedText("Account statement text", "embedded_text"))
    )
    assert adapter.extract(b"%PDF-source", ".pdf") == ExtractedText(
        "Account statement text", "embedded_text"
    )


def test_statement_adapter_preserves_safety_code_and_translates_payslip_copy() -> None:
    adapter = StatementDocumentExtractor(
        StubDocumentExtractor(
            DocumentExtractionError("too_many_pages", "Payslip PDFs may contain at most 10 pages.")
        )
    )
    with pytest.raises(DocumentExtractionError) as error:
        adapter.extract(b"%PDF-source", ".pdf")
    assert error.value.code == "too_many_pages"
    assert error.value.message == "Statement PDFs may contain at most 10 pages."


def test_statement_adapter_rejects_csv_before_document_extractor() -> None:
    adapter = StatementDocumentExtractor(
        StubDocumentExtractor(ExtractedText("should not be returned", "embedded_text"))
    )
    with pytest.raises(DocumentExtractionError) as error:
        adapter.extract(b"csv", ".csv")
    assert error.value.code == "unsupported_file_type"
