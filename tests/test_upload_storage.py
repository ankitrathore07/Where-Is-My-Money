import hashlib
from io import BytesIO
from pathlib import Path

import pytest

from app.imports.storage import LocalUploadStore, UploadStorageError


def test_save_uses_an_opaque_workspace_key(tmp_path: Path) -> None:
    source = b"a,b\n1,2\n"
    store = LocalUploadStore(tmp_path, max_bytes=20)

    saved = store.save(42, BytesIO(source))

    assert saved.storage_key.startswith("42/")
    assert saved.storage_key.endswith(".csv")
    assert saved.size_bytes == len(source)
    assert saved.checksum == hashlib.sha256(source).hexdigest()
    assert store.read(saved.storage_key) == source


def test_oversize_upload_removes_partial_file(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path, max_bytes=5)

    with pytest.raises(UploadStorageError) as error:
        store.save(1, BytesIO(b"123456"))

    assert error.value.code == "file_too_large"
    assert list(tmp_path.rglob("*.csv")) == []


def test_pdf_uses_opaque_key_and_its_own_size_limit(tmp_path: Path) -> None:
    source = b"%PDF-synthetic"
    store = LocalUploadStore(tmp_path, max_bytes=5, max_pdf_bytes=len(source))

    saved = store.save(42, BytesIO(source), ".PDF")

    assert saved.storage_key.startswith("42/")
    assert saved.storage_key.endswith(".pdf")
    assert saved.size_bytes == len(source)
    assert saved.checksum == hashlib.sha256(source).hexdigest()
    assert store.read(saved.storage_key) == source


def test_oversize_pdf_removes_partial_file(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path, max_pdf_bytes=5)

    with pytest.raises(UploadStorageError) as error:
        store.save(1, BytesIO(b"123456"), ".pdf")

    assert error.value.code == "file_too_large"
    assert list(tmp_path.rglob("*.pdf")) == []


def test_store_rejects_unsupported_suffix_before_writing(tmp_path: Path) -> None:
    with pytest.raises(UploadStorageError) as error:
        LocalUploadStore(tmp_path).save(1, BytesIO(b"private"), ".png")

    assert error.value.code == "unsupported_file_type"
    assert list(tmp_path.rglob("*.*")) == []


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = LocalUploadStore(tmp_path)
    saved = store.save(1, BytesIO(b"a,b\n1,2\n"))

    store.delete(saved.storage_key)
    store.delete(saved.storage_key)

    assert not (tmp_path / saved.storage_key).exists()


@pytest.mark.parametrize(
    "key",
    ["../secret.csv", "/absolute.csv", "1/../../secret.csv", "1/not-opaque.pdf"],
)
def test_read_rejects_paths_outside_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(UploadStorageError) as error:
        LocalUploadStore(tmp_path).read(key)

    assert error.value.code == "invalid_storage_key"


def test_missing_file_has_a_safe_error(tmp_path: Path) -> None:
    with pytest.raises(UploadStorageError) as error:
        LocalUploadStore(tmp_path).read("1/0123456789abcdef0123456789abcdef.csv")

    assert error.value.code == "source_missing"
