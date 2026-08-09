import hashlib
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
STORAGE_KEY_PATTERN = re.compile(r"^[1-9]\d*/[0-9a-f]{32}\.csv$")


@dataclass(frozen=True)
class StoredUpload:
    storage_key: str
    checksum: str
    size_bytes: int


class UploadStorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LocalUploadStore:
    """Store opaque workspace files below one configured private root."""

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
            raise UploadStorageError("invalid_storage_key", "The private file key is invalid.")
        return target

    def save(self, workspace_id: int, stream: BinaryIO) -> StoredUpload:
        """Stream one upload to an opaque path while hashing and enforcing size."""
        if workspace_id <= 0:
            raise UploadStorageError("invalid_workspace", "The workspace is invalid.")
        storage_key = f"{workspace_id}/{uuid.uuid4().hex}.csv"
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        completed = False
        try:
            with target.open("xb") as destination:
                while chunk := stream.read(CHUNK_SIZE):
                    if not isinstance(chunk, bytes):
                        raise UploadStorageError(
                            "invalid_stream", "The uploaded file could not be read."
                        )
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadStorageError(
                            "file_too_large",
                            f"CSV files may be at most {self.max_bytes} bytes.",
                        )
                    digest.update(chunk)
                    destination.write(chunk)
            completed = True
            return StoredUpload(storage_key, digest.hexdigest(), size)
        finally:
            if not completed:
                target.unlink(missing_ok=True)

    def read(self, storage_key: str) -> bytes:
        """Read one generated private object or return a safe missing error."""
        target = self._resolve(storage_key)
        try:
            return target.read_bytes()
        except FileNotFoundError as exc:
            raise UploadStorageError(
                "source_missing", "The private source file is missing."
            ) from exc

    def delete(self, storage_key: str) -> None:
        """Idempotently delete one generated private object."""
        self._resolve(storage_key).unlink(missing_ok=True)
