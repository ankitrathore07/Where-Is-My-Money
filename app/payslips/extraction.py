import subprocess
from dataclasses import dataclass
from io import BytesIO
from math import ceil
from typing import Literal, Protocol

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError


class OcrEngine(Protocol):
    def extract_png(self, image_bytes: bytes) -> str:
        """Extract text from one locally held PNG image."""


@dataclass(frozen=True)
class ExtractedText:
    text: str
    method: Literal["embedded_text", "ocr"]


class DocumentExtractionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class TesseractOcrEngine:
    """Call only the locally installed Tesseract executable with fixed arguments."""

    def __init__(self, executable: str = "tesseract") -> None:
        self.executable = executable

    def extract_png(self, image_bytes: bytes) -> str:
        command = [
            self.executable,
            "stdin",
            "stdout",
            "-l",
            "eng",
            "--psm",
            "6",
        ]
        try:
            completed = subprocess.run(
                command,
                input=image_bytes,
                capture_output=True,
                timeout=30,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise DocumentExtractionError(
                "ocr_unavailable",
                "Install the local Tesseract OCR executable to import images or scanned PDFs.",
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise DocumentExtractionError(
                "ocr_timeout", "Local OCR took too long to read this payslip."
            ) from exc
        except OSError as exc:
            raise DocumentExtractionError(
                "ocr_unavailable", "The local Tesseract OCR executable could not be started."
            ) from exc
        if completed.returncode != 0:
            raise DocumentExtractionError("ocr_failed", "Local OCR could not read this image.")
        return completed.stdout.decode("utf-8", errors="replace").strip()


class DocumentExtractor:
    """Extract embedded PDF text before falling back to an injected local OCR engine."""

    def __init__(
        self,
        ocr_engine: OcrEngine,
        max_pdf_pages: int = 10,
        max_rendered_pixels: int = 40_000_000,
    ) -> None:
        self.ocr_engine = ocr_engine
        self.max_pdf_pages = max_pdf_pages
        self.max_rendered_pixels = max_rendered_pixels

    def extract(self, data: bytes, suffix: str) -> ExtractedText:
        normalized_suffix = suffix.casefold()
        if normalized_suffix == ".pdf":
            return self._extract_pdf(data)
        if normalized_suffix in {".png", ".jpg", ".jpeg"}:
            png = self._normalize_image(data, normalized_suffix)
            return self._ocr_images((png,))
        raise DocumentExtractionError(
            "unsupported_file_type", "Choose a PDF, PNG, or JPEG payslip."
        )

    def _extract_pdf(self, data: bytes) -> ExtractedText:
        if not data.startswith(b"%PDF-"):
            raise DocumentExtractionError(
                "file_signature_mismatch",
                "The file contents do not match the selected PDF type.",
            )
        try:
            reader = PdfReader(BytesIO(data), strict=False)
            if reader.is_encrypted:
                raise DocumentExtractionError(
                    "encrypted_pdf", "Encrypted PDF payslips are not supported."
                )
            page_count = len(reader.pages)
            if page_count > self.max_pdf_pages:
                noun = "page" if self.max_pdf_pages == 1 else "pages"
                raise DocumentExtractionError(
                    "too_many_pages",
                    f"Payslip PDFs may contain at most {self.max_pdf_pages} {noun}.",
                )
            text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        except DocumentExtractionError:
            raise
        except (PdfReadError, ValueError, TypeError, OSError) as exc:
            raise DocumentExtractionError("invalid_pdf", "Choose a valid PDF payslip.") from exc
        if len("".join(text.split())) >= 20:
            return ExtractedText(text=text, method="embedded_text")
        return self._ocr_pdf(data, page_count)

    def _ocr_pdf(self, data: bytes, page_count: int) -> ExtractedText:
        if page_count == 0:
            return self._ocr_text(())
        document = None
        extracted_pages: list[str] = []
        try:
            document = pdfium.PdfDocument(data)
            for page_number in range(page_count):
                page = document[page_number]
                try:
                    scale = 200 / 72
                    width, height = page.get_size()
                    rendered_pixels = ceil(width * scale) * ceil(height * scale)
                    if rendered_pixels > self.max_rendered_pixels:
                        raise DocumentExtractionError(
                            "pdf_page_too_large",
                            "A payslip PDF page contains too many pixels to process safely.",
                        )
                    bitmap = page.render(scale=scale)
                    try:
                        source_image = bitmap.to_pil()
                        try:
                            image = source_image.convert("RGB")
                            try:
                                output = BytesIO()
                                image.save(output, format="PNG")
                                png = output.getvalue()
                            finally:
                                image.close()
                        finally:
                            source_image.close()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                extracted_pages.append(self.ocr_engine.extract_png(png))
        except DocumentExtractionError:
            raise
        except Exception as exc:
            raise DocumentExtractionError(
                "pdf_render_failed", "The scanned PDF could not be prepared for local OCR."
            ) from exc
        finally:
            if document is not None:
                document.close()
        return self._ocr_text(tuple(extracted_pages))

    def _normalize_image(self, data: bytes, suffix: str) -> bytes:
        is_png = data.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = data.startswith(b"\xff\xd8\xff")
        expected_signature = is_png if suffix == ".png" else is_jpeg
        if not expected_signature:
            raise DocumentExtractionError(
                "file_signature_mismatch",
                "The file contents do not match the selected image type.",
            )
        try:
            with Image.open(BytesIO(data)) as source:
                if source.width * source.height > self.max_rendered_pixels:
                    raise DocumentExtractionError(
                        "image_too_large", "The payslip image contains too many pixels."
                    )
                source.load()
                image = source.convert("RGB")
                output = BytesIO()
                image.save(output, format="PNG")
                return output.getvalue()
        except DocumentExtractionError:
            raise
        except Image.DecompressionBombError as exc:
            raise DocumentExtractionError(
                "image_too_large", "The payslip image contains too many pixels."
            ) from exc
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise DocumentExtractionError(
                "invalid_image", "Choose a valid PNG or JPEG payslip image."
            ) from exc

    def _ocr_images(self, images: tuple[bytes, ...]) -> ExtractedText:
        return self._ocr_text(tuple(self.ocr_engine.extract_png(image) for image in images))

    @staticmethod
    def _ocr_text(pages: tuple[str, ...]) -> ExtractedText:
        text = "\n".join(pages).strip()
        if len("".join(text.split())) < 20:
            raise DocumentExtractionError(
                "ocr_text_missing",
                "Local OCR could not read enough text; choose a clearer image or PDF.",
            )
        return ExtractedText(text=text, method="ocr")
