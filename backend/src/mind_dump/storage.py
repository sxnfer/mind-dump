"""File storage behind one small interface (D5).

The rest of the code only ever calls put, get, delete and delete_prefix with a key such as
`raw/7/20261103T090000000000Z-e5f61234.html.gz` or `files/<sha256>` (D34).
It never sees folders or containers.
STORAGE_BACKEND picks the adapter: `local` now, `azure` (Blob, managed identity) in P6.
"""

import os
import secrets
import shutil
from pathlib import Path
from typing import Protocol

from mind_dump.config import Settings


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def delete_prefix(self, prefix: str) -> None: ...


class LocalStorage:
    """Files under one root folder. In Docker Compose that folder is the `rawdata` volume,
    because a container's own disk is wiped when the container is replaced."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        # Keys are relative paths made of plain parts: no "/x", no "..", no "a//b".
        parts = key.split("/")
        if not key or "\\" in key or any(part in ("", ".", "..") for part in parts):
            raise ValueError(f"Bad storage key: {key!r}")
        return self.root.joinpath(*parts)

    def put(self, key: str, data: bytes) -> None:
        """Write once. Landed files are never overwritten (D28).

        The same bytes again is fine (a retried job); different bytes is a bug.
        """
        path = self._path(key)
        if path.exists():
            if path.read_bytes() == data:
                return
            raise FileExistsError(f"{key} already holds different bytes; files are never replaced")
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary name, then rename: a crash never leaves half a file under `key`.
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        """Delete one file, e.g. `files/<sha256>`. Already gone is fine: deleting twice is safe."""
        self._path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> None:
        """Delete every file under `prefix/`, e.g. `raw/7` when item 7 is deleted."""
        shutil.rmtree(self._path(prefix.rstrip("/")), ignore_errors=True)


def create_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "local":
        return LocalStorage(settings.storage_dir)
    if settings.storage_backend == "azure":
        raise RuntimeError("STORAGE_BACKEND=azure arrives in P6 (D5). Use 'local' for now.")
    raise RuntimeError(
        f"Unknown STORAGE_BACKEND {settings.storage_backend!r}: use 'local' (or 'azure' from P6)"
    )
