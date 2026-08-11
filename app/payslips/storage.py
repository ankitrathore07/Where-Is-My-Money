import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
STORAGE_KEY_PATTERN = re.compile(r"^[1-9]\d*/[0-9a-f]{32}\.(?:pdf|png|jpg)$")
ALLOWED_SUFFIXES = {".pdf": ".pdf", ".png": ".png", ".jpg": ".jpg", ".jpeg": ".jpg"}


@dataclass(frozen=True)
class StoredPayslipUpload:
    storage_key: str
    checksum: str
    size_bytes: int


class PayslipStorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PayslipUploadStore:
    """Store opaque payslip sources below one configured private root."""

    def __init__(self, root: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def _resolve(self, storage_key: str) -> Path:
        key_path = Path(storage_key)
        target = (self.root / key_path).resolve()
        if (
            key_path.is_absolute()
            or not STORAGE_KEY_PATTERN.fullmatch(storage_key.replace("\\", "/"))
            or not target.is_relative_to(self.root)
        ):
            raise PayslipStorageError("invalid_storage_key", "The private file key is invalid.")
        return target

    def save(self, workspace_id: int, suffix: str, stream: BinaryIO) -> StoredPayslipUpload:
        """Stream one source to an opaque path while hashing and enforcing size."""
        if workspace_id <= 0:
            raise PayslipStorageError("invalid_workspace", "The workspace is invalid.")
        canonical_suffix = ALLOWED_SUFFIXES.get(suffix.casefold())
        if canonical_suffix is None:
            raise PayslipStorageError(
                "unsupported_file_type", "Choose a PDF, PNG, or JPEG payslip."
            )

        storage_key = f"{workspace_id}/{uuid.uuid4().hex}{canonical_suffix}"
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        completed = False
        try:
            with target.open("xb") as destination:
                while chunk := stream.read(CHUNK_SIZE):
                    if not isinstance(chunk, bytes):
                        raise PayslipStorageError(
                            "invalid_stream", "The uploaded payslip could not be read."
                        )
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise PayslipStorageError(
                            "file_too_large",
                            f"Payslip files may be at most {self.max_bytes} bytes.",
                        )
                    digest.update(chunk)
                    destination.write(chunk)
            completed = True
            return StoredPayslipUpload(storage_key, digest.hexdigest(), size)
        finally:
            if not completed:
                target.unlink(missing_ok=True)

    def read(self, storage_key: str) -> bytes:
        """Read one generated private source or return a safe missing error."""
        target = self._resolve(storage_key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise PayslipStorageError(
                "source_missing", "The private payslip file is missing."
            ) from exc

    def delete(self, storage_key: str) -> None:
        """Idempotently delete one generated private source."""
        self._resolve(storage_key).unlink(missing_ok=True)
