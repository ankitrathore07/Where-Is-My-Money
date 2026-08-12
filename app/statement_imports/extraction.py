from typing import Protocol

from app.payslips.extraction import DocumentExtractionError, ExtractedText


class LocalDocumentExtractor(Protocol):
    def extract(self, data: bytes, suffix: str) -> ExtractedText: ...


class StatementDocumentExtractor:
    def __init__(self, extractor: LocalDocumentExtractor) -> None:
        self.extractor = extractor

    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        if suffix.casefold() not in {".pdf", ".png", ".jpg", ".jpeg"}:
            raise DocumentExtractionError(
                "unsupported_file_type", "Choose a PDF, PNG, or JPEG statement."
            )
        try:
            return self.extractor.extract(data, suffix)
        except DocumentExtractionError as exc:
            message = exc.message.replace("Payslip", "Statement").replace("payslip", "statement")
            raise DocumentExtractionError(exc.code, message) from exc
