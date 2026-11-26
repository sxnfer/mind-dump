# D42: Image formats and sizes

Status: Approved by San, 2026-11-27
Problem: only PDF, PNG and JPEG files could be saved. iPhone photos usually arrive as HEIC, and GIF, WebP and TIFF were refused. A large photo (over about 7.5 MB, or over 8,000 pixels on a side) uploaded fine and then failed at description time, because Claude refuses images over 10 MB once base64-encoded or over 8,000 x 8,000 pixels (Vision docs, checked 2026-11-27). Claude reads JPEG, PNG, GIF and WebP only; an animated GIF is read from its first frame.
Without it: a common photo is refused, or saved and then shown as "Couldn't process", with nothing the user can do about it.
Options:
- Unreadable formats (HEIC, TIFF): convert on the Mac before upload, with macOS's own image framework (ImageIO); or convert in the backend, which needs a new image library.
- Formats Claude reads (GIF, WebP): accept them in the backend by their first bytes, as PNG and JPEG are; or convert them too.
- Too-large images: shrink on the Mac before upload; refuse them in the drop target; or keep the original and shrink only the copy sent to Claude, which needs an image library in the backend.
Chosen:
- The backend accepts GIF and WebP by their first bytes, like PNG and JPEG (D34 otherwise unchanged).
- The Mac app converts HEIC and TIFF to JPEG before upload, with ImageIO.
- The Mac app shrinks an image that is over Claude's limits to fit them before upload. The stored file is the converted or shrunk one.
Why: no new dependency anywhere; the backend stays a store of what Claude can read; the Mac already has the image framework. (San chose these from Claude's recommendation; his own reasons are not recorded yet.)
Cost: for a converted or shrunk image the stored bytes are not the original file, so the content hash (D34) is the hash of the converted bytes. The same photo dropped twice is found as a duplicate only if the conversion gives the same bytes both times, so the conversion must be deterministic (same settings, no timestamp written). Metadata such as location may be lost in conversion. The original stays where it was on the Mac.
We will switch if: the original file must be kept (for example to download it again), or a format the Mac cannot decode is needed; then the backend gets an image library, through its own decision.

Not a new dependency: ImageIO and UniformTypeIdentifiers ship with macOS.
