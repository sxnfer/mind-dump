-- 003: the same link can be saved only once.
-- A partial unique index: it covers rows where type = 'link' only, so notes are unaffected.
-- The database enforces this, not the API, so two saves arriving at the same moment
-- cannot both win (a check-then-insert in Python would let that race through).
-- "Same" means the same URL after the API's normalising (lowercase scheme and host).

CREATE UNIQUE INDEX items_link_url_key ON items (url) WHERE type = 'link';
