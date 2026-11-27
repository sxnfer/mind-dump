-- 012: GIF and WebP files can be saved too (D42), as well as PDF, PNG and JPEG.
-- Never edit this file after it has run anywhere. Add a new numbered file instead (D4).
--
-- These are the four image formats Claude reads. The Mac app converts every other image
-- it can open (HEIC, TIFF and the rest) to JPEG before it uploads it, so the database
-- still holds only what the worker can describe.

-- 006 wrote the check inline, so Postgres named it items_media_type_check.
ALTER TABLE items DROP CONSTRAINT items_media_type_check;
ALTER TABLE items ADD CONSTRAINT items_media_type_check
    CHECK (media_type IN ('application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp'));
