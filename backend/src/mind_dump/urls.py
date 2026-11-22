"""The business key for links (D31): one normalised form per page.

Pydantic's HttpUrl has already lowercased the scheme and host and dropped a default
port. Here we drop what can never change the page the server sends back:
- the #fragment (the browser never sends it to the server)
- a named list of tracking parameters (they only feed the site's analytics)

Everything else stays exactly as typed, including the trailing slash and every other
parameter, because a false merge silently loses a page (D31).
"""

from urllib.parse import unquote_plus, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid"})
TRACKING_PREFIXES = ("utm_",)


def _is_tracking(name: str) -> bool:
    name = unquote_plus(name).lower()
    return name in TRACKING_PARAMS or name.startswith(TRACKING_PREFIXES)


def normalise_url(url: str) -> str:
    parts = urlsplit(url)
    # Work on the raw "a=1&b=2" text, so kept parameters are byte-for-byte unchanged
    # (decoding and re-encoding could turn %20 into +, and that would be a new key).
    kept = [
        pair for pair in parts.query.split("&") if pair and not _is_tracking(pair.partition("=")[0])
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), ""))
