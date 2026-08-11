import hashlib
import re
from io import BytesIO
from pathlib import Path

import pytest

from app.payslips.storage import PayslipStorageError, PayslipUploadStore


def test_save_uses_an_opaque_workspace_key_and_hashes_source(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path, max_bytes=100)

    saved = store.save(7, ".pdf", BytesIO(b"%PDF-synthetic"))

    assert re.fullmatch(r"7/[0-9a-f]{32}\.pdf", saved.storage_key)
    assert saved.checksum == hashlib.sha256(b"%PDF-synthetic").hexdigest()
    assert saved.size_bytes == 14
    assert store.read(saved.storage_key) == b"%PDF-synthetic"


def test_jpeg_suffix_is_stored_canonically_as_jpg(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path)

    saved = store.save(3, ".JPEG", BytesIO(b"jpeg"))

    assert saved.storage_key.endswith(".jpg")


def test_oversize_upload_removes_partial_source(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path, max_bytes=4)

    with pytest.raises(PayslipStorageError, match="at most 4 bytes") as error:
        store.save(7, ".png", BytesIO(b"12345"))

    assert error.value.code == "file_too_large"
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.parametrize("suffix", [".gif", ".txt", "pdf", ""])
def test_save_rejects_unsupported_suffixes_before_writing(tmp_path: Path, suffix: str) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(PayslipStorageError, match="PDF, PNG, or JPEG") as error:
        store.save(7, suffix, BytesIO(b"content"))

    assert error.value.code == "unsupported_file_type"
    assert list(tmp_path.rglob("*.*")) == []


def test_save_rejects_invalid_workspace_before_writing(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(PayslipStorageError, match="workspace is invalid"):
        store.save(0, ".pdf", BytesIO(b"content"))

    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.parametrize(
    "storage_key",
    ["../secret.pdf", "/absolute.pdf", "1/../../secret.pdf", "1/not-opaque.pdf"],
)
def test_read_rejects_paths_outside_private_key_space(tmp_path: Path, storage_key: str) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(PayslipStorageError, match="private file key is invalid"):
        store.read(storage_key)


def test_missing_file_has_a_safe_error(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path)

    with pytest.raises(PayslipStorageError, match="private payslip file is missing") as error:
        store.read("1/0123456789abcdef0123456789abcdef.pdf")

    assert error.value.code == "source_missing"


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = PayslipUploadStore(tmp_path)
    saved = store.save(7, ".png", BytesIO(b"content"))

    store.delete(saved.storage_key)
    store.delete(saved.storage_key)

    with pytest.raises(PayslipStorageError, match="missing"):
        store.read(saved.storage_key)
