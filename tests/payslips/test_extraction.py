import subprocess
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.payslips.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    TesseractOcrEngine,
)
from tests.payslips.pdf_helpers import make_text_pdf

FIXTURE = Path(__file__).parents[1] / "fixtures" / "payslips" / "synthetic_paystub_text.txt"


class UnexpectedOcr:
    def extract_png(self, image_bytes: bytes) -> str:
        raise AssertionError("embedded PDF text must not invoke OCR")


class RecordingOcr:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[bytes] = []

    def extract_png(self, image_bytes: bytes) -> str:
        self.calls.append(image_bytes)
        return self.result


def test_pdf_embedded_text_is_used_without_ocr() -> None:
    source_text = FIXTURE.read_text(encoding="utf-8")
    document = make_text_pdf(source_text.splitlines())

    extracted = DocumentExtractor(UnexpectedOcr()).extract(document, ".pdf")

    assert extracted.method == "embedded_text"
    assert "Employer: Northstar Bicycle Works" in extracted.text
    assert "Net Pay: $3,700.00" in extracted.text


def test_malformed_pdf_returns_safe_validation_error() -> None:
    with pytest.raises(DocumentExtractionError, match="valid PDF") as error:
        DocumentExtractor(UnexpectedOcr()).extract(b"%PDF-this-is-broken", ".pdf")

    assert error.value.code == "invalid_pdf"


def test_encrypted_pdf_is_rejected_without_reading_private_content() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.encrypt("synthetic-password")
    output = BytesIO()
    writer.write(output)

    with pytest.raises(DocumentExtractionError, match="(?i)encrypted") as error:
        DocumentExtractor(UnexpectedOcr()).extract(output.getvalue(), ".pdf")

    assert error.value.code == "encrypted_pdf"


def test_pdf_page_limit_is_enforced_before_rendering_or_ocr() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)

    with pytest.raises(DocumentExtractionError, match="at most 1 page") as error:
        DocumentExtractor(UnexpectedOcr(), max_pdf_pages=1).extract(output.getvalue(), ".pdf")

    assert error.value.code == "too_many_pages"


def test_oversized_pdf_page_is_rejected_before_rendering() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=10_000, height=10_000)
    output = BytesIO()
    writer.write(output)

    with pytest.raises(DocumentExtractionError, match="too many pixels") as error:
        DocumentExtractor(UnexpectedOcr(), max_rendered_pixels=1_000_000).extract(
            output.getvalue(), ".pdf"
        )

    assert error.value.code == "pdf_page_too_large"


def test_non_pdf_bytes_are_rejected_before_parser() -> None:
    with pytest.raises(DocumentExtractionError, match="do not match") as error:
        DocumentExtractor(UnexpectedOcr()).extract(b"not a pdf", ".pdf")

    assert error.value.code == "file_signature_mismatch"


def test_scanned_pdf_pages_are_rendered_and_sent_to_local_ocr() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    ocr = RecordingOcr(FIXTURE.read_text(encoding="utf-8"))

    extracted = DocumentExtractor(ocr).extract(output.getvalue(), ".pdf")

    assert extracted.method == "ocr"
    assert "Gross Pay: $5,000.00" in extracted.text
    assert len(ocr.calls) == 1
    assert ocr.calls[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_multi_page_pdf_is_ocrd_before_the_next_page_is_rendered() -> None:
    events: list[str] = []

    class SequentialOcr:
        def extract_png(self, image_bytes: bytes) -> str:
            assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            events.append("ocr")
            return FIXTURE.read_text(encoding="utf-8")

    class FakeBitmap:
        def to_pil(self) -> Image.Image:
            return Image.new("RGB", (20, 20), "white")

        def close(self) -> None:
            events.append("close-bitmap")

    class FakePage:
        def __init__(self, page_number: int) -> None:
            self.page_number = page_number

        def get_size(self) -> tuple[int, int]:
            return (20, 20)

        def render(self, *, scale: float) -> FakeBitmap:
            assert scale == 200 / 72
            if self.page_number == 1:
                assert "ocr" in events
            events.append(f"render-{self.page_number}")
            return FakeBitmap()

        def close(self) -> None:
            events.append(f"close-page-{self.page_number}")

    class FakeDocument:
        def __getitem__(self, page_number: int) -> FakePage:
            return FakePage(page_number)

        def close(self) -> None:
            events.append("close-document")

    extractor = DocumentExtractor(SequentialOcr())
    with patch("app.payslips.extraction.pdfium.PdfDocument", return_value=FakeDocument()):
        extracted = extractor._ocr_pdf(b"synthetic", page_count=2)

    assert extracted.method == "ocr"
    assert events.index("ocr") < events.index("render-1")
    assert events.count("ocr") == 2


@pytest.mark.parametrize("suffix, image_format", [(".png", "PNG"), (".jpg", "JPEG")])
def test_image_payslip_is_normalized_to_png_and_sent_to_local_ocr(
    suffix: str, image_format: str
) -> None:
    source = BytesIO()
    Image.new("RGB", (120, 60), "white").save(source, format=image_format)
    ocr = RecordingOcr(FIXTURE.read_text(encoding="utf-8"))

    extracted = DocumentExtractor(ocr).extract(source.getvalue(), suffix)

    assert extracted.method == "ocr"
    assert "Net Pay: $3,700.00" in extracted.text
    assert len(ocr.calls) == 1
    assert ocr.calls[0].startswith(b"\x89PNG\r\n\x1a\n")


def test_image_signature_must_match_selected_type() -> None:
    with pytest.raises(DocumentExtractionError, match="do not match") as error:
        DocumentExtractor(RecordingOcr("text")).extract(b"not an image", ".png")

    assert error.value.code == "file_signature_mismatch"


def test_oversized_image_is_rejected_before_pixel_data_is_loaded() -> None:
    source = BytesIO()
    Image.new("RGB", (20, 20), "white").save(source, format="PNG")

    with pytest.raises(DocumentExtractionError, match="too many pixels") as error:
        DocumentExtractor(UnexpectedOcr(), max_rendered_pixels=399).extract(
            source.getvalue(), ".png"
        )

    assert error.value.code == "image_too_large"


def test_pillow_decompression_bomb_error_becomes_safe_validation_error() -> None:
    with (
        patch(
            "app.payslips.extraction.Image.open",
            side_effect=Image.DecompressionBombError("synthetic private decoder detail"),
        ),
        pytest.raises(DocumentExtractionError, match="too many pixels") as error,
    ):
        DocumentExtractor(UnexpectedOcr()).extract(b"\x89PNG\r\n\x1a\nrest", ".png")

    assert error.value.code == "image_too_large"
    assert "synthetic private decoder detail" not in str(error.value)


def test_empty_ocr_result_requires_a_clearer_source() -> None:
    source = BytesIO()
    Image.new("RGB", (20, 20), "white").save(source, format="PNG")

    with pytest.raises(DocumentExtractionError, match="could not read enough text") as error:
        DocumentExtractor(RecordingOcr("  \n ")).extract(source.getvalue(), ".png")

    assert error.value.code == "ocr_text_missing"


def test_tesseract_uses_fixed_local_command_and_standard_input() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout=b"synthetic extracted text", stderr=b""
    )
    with patch("app.payslips.extraction.subprocess.run", return_value=completed) as run:
        text = TesseractOcrEngine().extract_png(b"synthetic-png")

    assert text == "synthetic extracted text"
    run.assert_called_once_with(
        ["tesseract", "stdin", "stdout", "-l", "eng", "--psm", "6"],
        input=b"synthetic-png",
        capture_output=True,
        timeout=30,
        check=False,
        shell=False,
    )


def test_missing_tesseract_explains_the_local_requirement() -> None:
    with (
        patch("app.payslips.extraction.subprocess.run", side_effect=FileNotFoundError),
        pytest.raises(DocumentExtractionError, match="Install the local Tesseract") as error,
    ):
        TesseractOcrEngine().extract_png(b"synthetic-png")

    assert error.value.code == "ocr_unavailable"


def test_tesseract_timeout_has_a_safe_error() -> None:
    timeout = subprocess.TimeoutExpired(cmd="tesseract", timeout=30)
    with (
        patch("app.payslips.extraction.subprocess.run", side_effect=timeout),
        pytest.raises(DocumentExtractionError, match="took too long") as error,
    ):
        TesseractOcrEngine().extract_png(b"synthetic-png")

    assert error.value.code == "ocr_timeout"


def test_tesseract_nonzero_exit_does_not_expose_native_error_text() -> None:
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout=b"", stderr=b"private native diagnostic"
    )
    with (
        patch("app.payslips.extraction.subprocess.run", return_value=completed),
        pytest.raises(DocumentExtractionError, match="could not read this image") as error,
    ):
        TesseractOcrEngine().extract_png(b"synthetic-png")

    assert error.value.code == "ocr_failed"
    assert "private native diagnostic" not in str(error.value)
