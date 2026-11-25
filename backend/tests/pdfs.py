"""Small real PDFs for tests, built by hand: no binary files in the repo.

    make_pdf(["page one text", "page two text"])   # one string per page; "\\n" = new line

Each page is one text block in Helvetica, one line per "\\n". An empty string gives a page
with no text at all, like a scanned page. Run as a script to write one to a file (the CI
smoke test uploads it):

    uv run python tests/pdfs.py /tmp/smoke.pdf
"""

import sys
from pathlib import Path


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[str]) -> bytes:
    n = len(pages)
    page_ids = [4 + 2 * i for i in range(n)]
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>"
        ).encode(),
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, text in zip(page_ids, pages, strict=True):
        if text:
            shown = " ".join(f"({_escape(line)}) Tj T*" for line in text.split("\n"))
            stream = f"BT /F1 10 Tf 14 TL 40 760 Td {shown} ET".encode("latin-1")
        else:
            stream = b"0 0 1 rg 40 40 100 100 re f"  # a drawing, no text: a "scan"
        objects[page_id] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>"
        ).encode()
        objects[page_id + 1] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = len(out)
        out += f"{number} 0 obj\n".encode() + objects[number] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for number in range(1, size):
        out += f"{offsets[number]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


if __name__ == "__main__":
    Path(sys.argv[1]).write_bytes(
        make_pdf(["Smoke test PDF, page one.", "Page two says something else."])
    )
