# D34: File uploads

Status: Approved, 2026-11-22
Problem: the same PDF or image uploaded twice must be found as a duplicate, stored once, and checked for size and type, because uploads are the biggest and least trusted input the API takes.
Without a tool: match on filename or size. Two different files called `report.pdf` merge into one (a silent loss), the same file renamed is stored twice, and a renamed `.exe` passes as a PDF.
Options: recognise by name, by size, or by content hash; on a duplicate, return the existing item (200) or reject (409); upload as a multipart form (new dependency `python-multipart`) or as a raw body.
Chosen:
- Duplicate recognition: the SHA-256 of the bytes, not the name or size. A partial unique index on `items (sha256) WHERE type = 'file'` enforces it.
- Duplicate upload: 409 with `Location: /items/<existing id>`, like links (D31).
- Storage key: `files/<sha256>`. The hash is the key.
- Upload format: the raw request body (`curl --data-binary @x.pdf`), with the filename in a `Content-Disposition` header.
- Limits: 25 MB; PDF, PNG and JPEG only. The type is decided from the file's first bytes (magic numbers), never from the extension or the client's `Content-Type`. Too big is 413; the wrong type is 415.
- Delete: rows first, then the file.
Why (San):
- Names and sizes lie: one name can hide two files, one file can have two names. Same bytes means the same file.
- One rule across the API; the panel already shows "Already saved".
- With the hash as the key, a second copy can't exist, even by mistake.
- A multipart form needs `python-multipart`, a new dependency. A raw body needs nothing new.
- Limits are security: a client can claim anything.
- Rows first, then the file: a failure can only leave an unused file, never a row pointing at nothing.
Cost: a raw body carries one file per request and no form fields, so the filename travels in a header. The type check knows only three formats; anything else is a 415 until it is added on purpose.
We will switch if: a browser form must upload directly (then decide on `python-multipart`), or users need more file types.
