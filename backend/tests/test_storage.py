"""File storage (D5): one interface, write-once files, safe keys."""

from pathlib import Path

import pytest

from mind_dump.config import load_settings
from mind_dump.storage import LocalStorage, create_storage

KEY = "raw/7/20261103T090000000000Z-e5f61234.html.gz"


def test_put_then_get_returns_the_same_bytes(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    storage.put(KEY, b"page")

    assert storage.get(KEY) == b"page"
    assert (tmp_path / KEY).read_bytes() == b"page"


def test_putting_the_same_bytes_again_is_a_no_op(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put(KEY, b"page")

    storage.put(KEY, b"page")  # a retried job

    assert storage.get(KEY) == b"page"


def test_a_landed_file_is_never_overwritten(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put(KEY, b"page")

    with pytest.raises(FileExistsError):
        storage.put(KEY, b"different")
    assert storage.get(KEY) == b"page"


@pytest.mark.parametrize("key", ["", "/etc/passwd", "../outside", "raw/../../x", "raw//x", "a\\b"])
def test_keys_cannot_escape_the_root(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError, match="Bad storage key"):
        LocalStorage(tmp_path).put(key, b"x")


def test_delete_prefix_removes_one_items_files_only(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put("raw/7/a.html.gz", b"a")
    storage.put("raw/7/b.html.gz", b"b")
    storage.put("raw/8/c.html.gz", b"c")

    storage.delete_prefix("raw/7")

    assert not (tmp_path / "raw/7").exists()
    assert storage.get("raw/8/c.html.gz") == b"c"


def test_azure_backend_is_not_built_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("STORAGE_BACKEND", "azure")

    with pytest.raises(RuntimeError, match="P6"):
        create_storage(load_settings())
