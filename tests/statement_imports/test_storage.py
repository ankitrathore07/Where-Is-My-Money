from io import BytesIO
from pathlib import Path

import pytest

from app.statement_imports.storage import StatementStorageError, StatementUploadStore


@pytest.mark.parametrize(
    ("suffix", "canonical"),
    [(".csv", ".csv"), (".pdf", ".pdf"), (".png", ".png"), (".jpg", ".jpg"), (".jpeg", ".jpg")],
)
def test_store_uses_opaque_workspace_key_and_hashes_source(
    tmp_path: Path, suffix: str, canonical: str
) -> None:
    store = StatementUploadStore(tmp_path)
    stored = store.save(7, suffix, BytesIO(b"synthetic statement"))
    assert stored.storage_key.startswith("7/")
    assert stored.storage_key.endswith(canonical)
    assert "synthetic" not in stored.storage_key
    assert stored.checksum == "a5891355d67e8622dfabcd9322ee700af5453aafc8bc7eebec02dcbfdba731bc"
    assert store.read(stored.storage_key) == b"synthetic statement"


def test_store_removes_partial_oversize_source(tmp_path: Path) -> None:
    store = StatementUploadStore(tmp_path, max_bytes=4)
    with pytest.raises(StatementStorageError) as error:
        store.save(1, ".csv", BytesIO(b"12345"))
    assert error.value.code == "file_too_large"
    assert list(tmp_path.rglob("*.*")) == []


@pytest.mark.parametrize("key", ["../secret.csv", "/absolute.pdf", "1/../../secret.png"])
def test_store_rejects_paths_outside_private_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(StatementStorageError) as error:
        StatementUploadStore(tmp_path).read(key)
    assert error.value.code == "invalid_storage_key"


def test_delete_is_idempotent_and_missing_read_is_safe(tmp_path: Path) -> None:
    store = StatementUploadStore(tmp_path)
    stored = store.save(1, ".csv", BytesIO(b"data"))
    store.delete(stored.storage_key)
    store.delete(stored.storage_key)
    with pytest.raises(StatementStorageError) as error:
        store.read(stored.storage_key)
    assert error.value.code == "source_missing"
