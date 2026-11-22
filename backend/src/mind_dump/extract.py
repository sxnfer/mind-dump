"""Turn a landed web page into article text (D7).

The rules, in order:
1. Take <main>, else <article>, else <body>.
2. Drop page furniture: nav, header, footer, aside, script, style, noscript, form.
3. Keep one line per block (paragraph, heading, list item) and collapse all other
   whitespace, so re-indented HTML gives exactly the same text, and the same hash.

The text hash decides when a page gets a new version (D32), so anything that leaks
through these rules shows up as a fake version. Fix the rules, then rebuild versions
from the raw files.
"""

import hashlib
import re
from email.message import Message

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

DROP = ["nav", "header", "footer", "aside", "script", "style", "noscript", "form"]
# Elements that start a new line in the extracted text.
BLOCKS = [
    "address", "article", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p", "pre", "section",
    "table", "td", "th", "tr", "ul",
]  # fmt: skip
_SPACES = re.compile(r"\s+")


def _charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    message = Message()
    message["content-type"] = content_type
    return message.get_content_charset()


def extract_text(html: bytes, content_type: str | None = None) -> str:
    """Article text, one block per line. Empty when the page has no readable text."""
    soup = BeautifulSoup(html, "html.parser", from_encoding=_charset(content_type))
    root = soup.find("main") or soup.find("article") or soup.body or soup
    if not isinstance(root, Tag):
        return ""
    for tag in root.find_all(DROP):
        tag.decompose()
    for comment in root.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    # A newline inside a paragraph's source is just a space; only blocks start lines.
    for text in root.find_all(string=True):
        if type(text) is NavigableString:
            text.replace_with(_SPACES.sub(" ", text))
    for block in root.find_all(BLOCKS):
        block.insert_before("\n")
        block.append("\n")
    lines = (" ".join(line.split()) for line in root.get_text().split("\n"))
    return "\n".join(line for line in lines if line)


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
