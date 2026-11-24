"""POST /items/upload (P2, D34): one file is stored once, limits hold, delete leaves nothing.

Test files are built in code: a few real header bytes plus padding is all the API checks.
"""

import hashlib
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import DictRow

from mind_dump.uploads import MAX_UPLOAD_BYTES

PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
MB = 1024 * 1024


def upload(
    client: TestClient, data: bytes, filename: str | None = "report.pdf", **headers: str
) -> httpx2.Response:
    if filename is not None:
        headers.setdefault("Content-Disposition", f'attachment; filename="{filename}"')
    return client.post("/items/upload", content=data, headers=headers)


def stored_files(storage_dir: Path) -> list[Path]:
    return sorted(p for p in storage_dir.rglob("*") if p.is_file())


# ---------- happy paths ----------


def test_upload_pdf_returns_201_with_location_and_file_fields(
    client: TestClient, storage_dir: Path
) -> None:
    response = upload(client, PDF, "report.pdf")

    assert response.status_code == 201, response.text
    item = response.json()
    sha = hashlib.sha256(PDF).hexdigest()
    assert response.headers["location"] == f"/items/{item['id']}"
    assert item["type"] == "file"
    assert item["title"] == "report.pdf"
    assert item["filename"] == "report.pdf"
    assert item["sha256"] == sha
    assert item["media_type"] == "application/pdf"
    assert item["size_bytes"] == len(PDF)
    assert stored_files(storage_dir) == [storage_dir / "files" / sha]
    assert (storage_dir / "files" / sha).read_bytes() == PDF


def test_png_and_jpeg_are_accepted(client: TestClient) -> None:
    assert upload(client, PNG, "a.png").json()["media_type"] == "image/png"
    assert upload(client, JPEG, "b.jpg").json()["media_type"] == "image/jpeg"


def test_list_and_get_show_file_items(client: TestClient) -> None:
    saved = upload(client, PDF).json()

    listed = client.get("/items").json()["items"]
    detail = client.get(f"/items/{saved['id']}").json()

    assert listed == [saved]
    assert detail["sha256"] == saved["sha256"]
    assert detail["content"] is None  # text extraction comes with the worker (D7b, P3)


def test_no_filename_header_means_no_filename_or_title(client: TestClient) -> None:
    item = upload(client, PDF, filename=None).json()

    assert item["filename"] is None
    assert item["title"] is None


# ---------- duplicates: the bytes decide (P2 proof) ----------


def test_same_file_twice_is_one_item_and_one_stored_file(
    client: TestClient, storage_dir: Path
) -> None:
    first = upload(client, PDF)

    second = upload(client, PDF)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.headers["location"] == f"/items/{first.json()['id']}"
    assert len(client.get("/items").json()["items"]) == 1
    assert len(stored_files(storage_dir)) == 1


def test_same_bytes_under_another_name_is_still_a_duplicate(client: TestClient) -> None:
    first = upload(client, PDF, "report.pdf")

    renamed = upload(client, PDF, "final-v2.pdf")

    assert renamed.status_code == 409
    assert renamed.headers["location"] == f"/items/{first.json()['id']}"


def test_different_bytes_under_the_same_name_are_two_items(
    client: TestClient, storage_dir: Path
) -> None:
    assert upload(client, PDF, "report.pdf").status_code == 201
    assert upload(client, PDF + b"% edited\n", "report.pdf").status_code == 201

    assert len(client.get("/items").json()["items"]) == 2
    assert len(stored_files(storage_dir)) == 2


def test_two_simultaneous_uploads_of_one_file_only_one_wins(
    client: TestClient, storage_dir: Path
) -> None:
    """The unique index on sha256 settles the race, as it does for links (D31)."""
    start = threading.Barrier(8)

    def post() -> int:
        start.wait()  # release all threads at the same instant
        return upload(client, PDF).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = sorted(pool.map(lambda _: post(), range(8)))

    assert codes == [201] + [409] * 7
    assert len(client.get("/items").json()["items"]) == 1
    assert len(stored_files(storage_dir)) == 1


# ---------- delete (P2 proof) ----------


def test_deleting_a_file_item_leaves_no_file_behind(client: TestClient, storage_dir: Path) -> None:
    item = upload(client, PDF).json()

    assert client.delete(f"/items/{item['id']}").status_code == 204

    assert client.get(f"/items/{item['id']}").status_code == 404
    assert stored_files(storage_dir) == []


def test_a_deleted_file_can_be_uploaded_again(client: TestClient, storage_dir: Path) -> None:
    item = upload(client, PDF).json()
    client.delete(f"/items/{item['id']}")

    again = upload(client, PDF)

    assert again.status_code == 201
    assert len(stored_files(storage_dir)) == 1


# ---------- size limit: 413 ----------


def test_exactly_the_limit_is_accepted(client: TestClient) -> None:
    data = PDF + b"\x00" * (MAX_UPLOAD_BYTES - len(PDF))

    assert upload(client, data).status_code == 201


def test_one_byte_over_the_limit_is_413(client: TestClient, storage_dir: Path) -> None:
    data = PDF + b"\x00" * (MAX_UPLOAD_BYTES + 1 - len(PDF))

    response = upload(client, data)

    assert response.status_code == 413
    assert "25 MB" in response.json()["detail"]
    assert stored_files(storage_dir) == []


def test_over_the_limit_without_content_length_is_413(
    client: TestClient, storage_dir: Path
) -> None:
    """A chunked body has no Content-Length, so the count while reading must catch it."""

    def chunks() -> Iterator[bytes]:  # 30 MB in 1 MB pieces, no length known up front
        yield PDF
        for _ in range(30):
            yield b"\x00" * MB

    response = client.post(
        "/items/upload",
        content=chunks(),
        headers={"Content-Disposition": 'attachment; filename="big.pdf"'},
    )

    assert response.status_code == 413
    assert stored_files(storage_dir) == []
    assert client.get("/items").json()["items"] == []


# ---------- type: 415, the bytes decide ----------


def test_text_claiming_to_be_a_pdf_is_415(client: TestClient, storage_dir: Path) -> None:
    response = upload(
        client, b"just some text, honest", "evil.pdf", **{"Content-Type": "application/pdf"}
    )

    assert response.status_code == 415
    assert "PDF, PNG and JPEG" in response.json()["detail"]
    assert stored_files(storage_dir) == []


def test_real_pdf_sent_as_octet_stream_is_accepted(client: TestClient) -> None:
    response = upload(client, PDF, "scan", **{"Content-Type": "application/octet-stream"})

    assert response.status_code == 201
    assert response.json()["media_type"] == "application/pdf"


def test_empty_body_is_422(client: TestClient) -> None:
    assert upload(client, b"").status_code == 422


# ---------- filename ----------


def test_a_path_in_the_filename_keeps_only_the_last_part(client: TestClient) -> None:
    item = upload(client, PDF, "../../etc/x.pdf").json()

    assert item["filename"] == "x.pdf"


def test_utf8_filename_is_decoded(client: TestClient) -> None:
    item = upload(
        client,
        PDF,
        None,
        **{"Content-Disposition": "attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf"},
    ).json()

    assert item["filename"] == "résumé.pdf"


# ---------- the database is the second layer ----------


def test_database_refuses_a_second_row_with_the_same_hash(
    conn: psycopg.Connection[DictRow],
) -> None:
    sha = hashlib.sha256(PDF).hexdigest()
    insert = (
        "INSERT INTO items (type, sha256, media_type, size_bytes) "
        "VALUES ('file', %s, 'application/pdf', 10)"
    )
    conn.execute(insert, (sha,))

    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute(insert, (sha,))


def test_database_refuses_a_file_row_without_its_hash(
    conn: psycopg.Connection[DictRow],
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO items (type, title) VALUES ('file', 'no bytes')")
