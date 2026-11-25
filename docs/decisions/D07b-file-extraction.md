# D7b: Text from PDFs and images

Status: Approved, 2026-11-25 (decided by Claude under San's delegation; San to review)
Problem: an uploaded PDF or image is only bytes (D34). Search, citations and answers need its text. A PDF holds text on pages; an image holds none we can read directly.
Without a tool: files stay unsearchable. A saved methodology PDF or a photo of a whiteboard can be listed, but never found by what it says.
Options: for PDFs, pypdf, PyMuPDF, or pdfplumber. For images, an OCR engine (Tesseract) or a vision model.
Chosen:
- PDFs: `pypdf`, text read page by page. The pages are joined with a blank line, and each page's place in the text is kept (`file_texts.page_starts`, `page_ends`).
- Chunks never cross a page (D36, rule 6), so each chunk has one page number for its citation.
- Images (PNG, JPEG): a vision model (Claude, D8) writes a description, including any text visible in the image. That description is the item's text. It is stored and shown as a description (`GET /items/{id}/text` says `kind: description`), because it is the model's words, not the source.
- The text is derived data in `file_texts`: it can be rebuilt from the stored file. It is written in the same transaction as its chunks (D30). A re-run reuses it; `from_raw=true` rebuilds it.
- An encrypted PDF that does not open with an empty password, a damaged PDF, and a PDF with no text layer (a scan) are permanent errors with a clear reason (D35). So is an image with no `ANTHROPIC_API_KEY`, an image over the API's limits (10 MB as base64, 8000 x 8000 pixels), or one Claude declines to describe.
Why:
- pypdf: BSD licence, pure Python, no system libraries in the slim image, and it gives text per page, which citations need.
- Not PyMuPDF: it is AGPL (or a paid licence), which does not fit an MIT project that others download and run (D17).
- Not pdfplumber: it pulls in pdfminer.six and image libraries for table features we do not use yet.
- Images: there is no text to extract. An OCR engine (Tesseract) is a system package that reads text but cannot describe a photo or a diagram. The vision model is already approved under D8.
Cost: pypdf is weaker on complex layouts and tables. A scanned PDF has no text layer, so its item ends `error`. Each image costs one model call, a few pence, and needs the owner's key.
We will switch if: real documents (carbon registry methodology PDFs) lose tables or columns that answers need; then compare pdfplumber or a vision model on those pages. If image volume makes the cost matter: OCR first, and a model only when OCR finds little text.
