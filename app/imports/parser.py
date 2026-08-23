import csv
import io

from app.imports.types import CsvDocument, CsvSourceRow

MAX_COLUMNS = 50
MAX_ROWS = 1_000
MAX_FIELD_CHARS = 2_000
ALLOWED_DELIMITERS = (",", ";", "\t")


class CsvValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _decode(data: bytes) -> str:
    if not data:
        raise CsvValidationError("empty_file", "Choose a CSV file that contains headers.")
    if b"\x00" in data:
        raise CsvValidationError("nul_byte", "The file contains unsupported binary data.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvValidationError(
            "invalid_encoding", "Save the CSV as UTF-8 and try again."
        ) from exc
    if not text.strip():
        raise CsvValidationError("empty_file", "Choose a CSV file that contains headers.")
    return text


def _dialect(text: str) -> csv.Dialect:
    try:
        return csv.Sniffer().sniff(text[:8192], delimiters="".join(ALLOWED_DELIMITERS))
    except csv.Error as exc:
        header_line = next((line for line in text.splitlines() if line.strip()), "")
        try:
            return csv.Sniffer().sniff(header_line, delimiters="".join(ALLOWED_DELIMITERS))
        except csv.Error:
            raise CsvValidationError(
                "unsupported_delimiter",
                "Use a comma, semicolon, or tab-separated CSV file.",
            ) from exc


def _validate_field_lengths(values: list[str]) -> None:
    if any(len(value) > MAX_FIELD_CHARS for value in values):
        raise CsvValidationError(
            "field_too_long",
            f"CSV fields may contain at most {MAX_FIELD_CHARS} characters.",
        )


def parse_csv_bytes(data: bytes) -> CsvDocument:
    """Decode and parse one bounded, rectangular UTF-8 CSV document."""
    text = _decode(data)
    dialect = _dialect(text)
    reader = csv.reader(io.StringIO(text, newline=""), dialect=dialect, strict=True)

    try:
        header_values = next((row for row in reader if any(value.strip() for value in row)), None)
        if header_values is None:
            raise CsvValidationError("empty_file", "Choose a CSV file that contains headers.")
        _validate_field_lengths(header_values)
        headers = tuple(value.strip() for value in header_values)
        if len(headers) > MAX_COLUMNS:
            raise CsvValidationError(
                "too_many_columns", f"CSV files may contain at most {MAX_COLUMNS} columns."
            )
        if any(not header for header in headers):
            raise CsvValidationError("blank_header", "Every CSV column needs a header.")
        if len(set(headers)) != len(headers):
            raise CsvValidationError("duplicate_header", "CSV headers must be unique.")

        rows: list[CsvSourceRow] = []
        for raw_row in reader:
            source_line = reader.line_num
            if not raw_row or not any(value.strip() for value in raw_row):
                continue
            _validate_field_lengths(raw_row)
            if len(raw_row) > len(headers):
                surplus_values = raw_row[len(headers) :]
                if any(value.strip() for value in surplus_values):
                    raise CsvValidationError(
                        "wide_row", f"Row {source_line} has more fields than the header."
                    )
                raw_row = raw_row[: len(headers)]
            if len(raw_row) < len(headers):
                raise CsvValidationError(
                    "short_row", f"Row {source_line} has fewer fields than the header."
                )
            rows.append(CsvSourceRow(source_line, dict(zip(headers, raw_row, strict=True))))
            if len(rows) > MAX_ROWS:
                raise CsvValidationError(
                    "too_many_rows", f"CSV files may contain at most {MAX_ROWS} data rows."
                )
    except CsvValidationError:
        raise
    except csv.Error as exc:
        raise CsvValidationError("invalid_csv", "The CSV structure could not be read.") from exc

    return CsvDocument(headers=headers, rows=tuple(rows), delimiter=dialect.delimiter)
