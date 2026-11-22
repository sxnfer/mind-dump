"""HTML to article text (D7). Every rule here decides when a page gets a new version."""

from mind_dump.extract import extract_text, text_sha256


def html(body: str) -> bytes:
    return f"<!doctype html><html><head><title>T</title></head><body>{body}</body></html>".encode()


def test_takes_main_and_drops_furniture_around_it() -> None:
    page = html(
        "<header><nav>Home Projects</nav></header>"
        "<main><h1>Project 1234</h1><p>Credits issued: 120,000</p></main>"
        "<aside>We use cookies</aside><footer>© 2026</footer>"
    )

    assert extract_text(page) == "Project 1234\nCredits issued: 120,000"


def test_falls_back_to_article_then_body() -> None:
    assert extract_text(html("<nav>x</nav><article><p>From article</p></article>")) == (
        "From article"
    )
    assert extract_text(html("<nav>Menu</nav><p>From body</p><footer>f</footer>")) == "From body"


def test_drops_scripts_styles_forms_and_comments_inside_main() -> None:
    page = html(
        "<main><p>Kept</p><script>track()</script><style>p{}</style><noscript>Enable JS</noscript>"
        "<form>Subscribe</form><!-- build 42 --></main>"
    )

    assert extract_text(page) == "Kept"


def test_reformatted_html_gives_the_same_text_and_hash() -> None:
    compact = html("<main><p>Status: Registered</p><p>Credits <b>issued</b>: 120,000</p></main>")
    reindented = html(
        """
        <main>
            <p>Status:
               Registered</p>
            <p>
              Credits <b>issued</b>:   120,000
            </p>
        </main>
        """
    )

    assert extract_text(compact) == "Status: Registered\nCredits issued: 120,000"
    assert text_sha256(extract_text(reindented)) == text_sha256(extract_text(compact))


def test_uses_the_charset_from_the_content_type() -> None:
    page = "<main><p>Café</p></main>".encode("latin-1")

    assert extract_text(page, "text/html; charset=ISO-8859-1") == "Café"


def test_a_page_with_no_readable_text_is_empty() -> None:
    assert extract_text(html("<div id='app'></div><script>render()</script>")) == ""
