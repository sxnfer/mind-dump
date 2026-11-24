"""Rules for uploaded files (D34). Pure functions: bytes and header text in, answers out.

Uploads are the biggest and least trusted input the API takes. A client can claim any
name, size or type, so each rule here is decided from what the server can see itself.
"""

import re
import unicodedata
from email.message import Message

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB; one more byte is a 413

# The first bytes of each allowed format ("magic numbers"). The extension and the
# client's Content-Type are claims; these bytes are what the file actually is.
_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
ALLOWED_MEDIA_TYPES = tuple(media_type for _, media_type in _SIGNATURES)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_MAX_FILENAME = 200  # the same limit as a title, because the filename is the default title


def sniff_media_type(first_bytes: bytes) -> str | None:
    """The file's real type from its first bytes, or None if it is not PDF, PNG or JPEG."""
    for signature, media_type in _SIGNATURES:
        if first_bytes.startswith(signature):
            return media_type
    return None


def file_key(sha256: str) -> str:
    """Where a file's bytes live in storage. The hash is the key, so one file has one copy."""
    if not _SHA256.fullmatch(sha256):
        raise ValueError(f"Not a SHA-256 hex digest: {sha256!r}")
    return f"files/{sha256}"


def safe_filename(content_disposition: str | None) -> str | None:
    """The filename from `Content-Disposition: attachment; filename="report.pdf"`, made safe.

    Keeps only the last path part (`../../etc/x.pdf` becomes `x.pdf`), removes control
    characters and trims to 200 characters. Missing or empty gives None.
    The standard library parses the header, including the `filename*=UTF-8''...` form.
    """
    if not content_disposition:
        return None
    header = Message()
    header["Content-Disposition"] = content_disposition
    name = header.get_filename()
    if not name:
        return None
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(ch for ch in name if not unicodedata.category(ch).startswith("C"))
    name = name.strip()[:_MAX_FILENAME].strip()
    return name if name not in ("", ".", "..") else None
