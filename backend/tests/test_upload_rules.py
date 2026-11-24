"""The pure rules behind uploads (D34): magic bytes, the storage key, safe filenames."""

import pytest

from mind_dump.uploads import file_key, safe_filename, sniff_media_type

SHA = "a" * 64


@pytest.mark.parametrize(
    ("first_bytes", "expected"),
    [
        (b"%PDF-1.7\n...", "application/pdf"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "image/png"),
        (b"\xff\xd8\xff\xe0\x00\x10JFIF", "image/jpeg"),
        (b"\xff\xd8\xff\xe1\x00\x10Exif", "image/jpeg"),
    ],
)
def test_known_formats_are_recognised_by_their_first_bytes(
    first_bytes: bytes, expected: str
) -> None:
    assert sniff_media_type(first_bytes) == expected


@pytest.mark.parametrize(
    "first_bytes",
    [
        b"",
        b"hello",
        b"MZ\x90\x00",  # a Windows .exe, whatever it is named
        b"GIF89a",  # a real image, but not an allowed one
        b"%PDF",  # too short to be sure
        b"\x89PNG",  # half a PNG signature
        b" %PDF-1.4",  # the signature must be at the very start
    ],
)
def test_anything_else_is_not_recognised(first_bytes: bytes) -> None:
    assert sniff_media_type(first_bytes) is None


def test_the_hash_is_the_storage_key() -> None:
    assert file_key(SHA) == f"files/{SHA}"


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, "../" + "a" * 61, "g" * 64])
def test_file_key_refuses_anything_but_a_sha256(bad: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        file_key(bad)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ('attachment; filename="report.pdf"', "report.pdf"),
        ("attachment; filename=report.pdf", "report.pdf"),
        ('attachment; filename="../../etc/x.pdf"', "x.pdf"),
        ('attachment; filename="C:\\\\Users\\\\san\\\\scan.png"', "scan.png"),
        ("attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf", "résumé.pdf"),
        ('attachment; filename="bad\x07name.pdf"', "badname.pdf"),
        ('attachment; filename="  spaced.pdf  "', "spaced.pdf"),
        ('attachment; filename=""', None),
        ('attachment; filename="../"', None),
        ('attachment; filename=".."', None),
        ("attachment", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_filename(header: str | None, expected: str | None) -> None:
    assert safe_filename(header) == expected


def test_long_filenames_are_trimmed_to_the_title_limit() -> None:
    name = safe_filename(f'attachment; filename="{"x" * 300}.pdf"')

    assert name is not None
    assert len(name) == 200
