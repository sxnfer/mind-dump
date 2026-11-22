# D7: Content extraction (web pages)

Status: Approved, 2026-11-22 (HTML only; PDFs and images are decided in P2 and M2)
Problem: a landed page (D28) is HTML, full of menus, footers, cookie banners and scripts. Search, chunking and the version history (choice 4) need only the article text.
Without a tool: hand-written rules on Python's `html.parser`. That means the most code, and more page furniture leaking into the text. Because a new version starts when the text hash changes, every leaked "© 2026" or "updated 3 min ago" creates a fake version.
Options: the standard library `html.parser`; BeautifulSoup with simple rules; trafilatura, a main-content detector tuned on many sites.
Chosen: BeautifulSoup (`beautifulsoup4`), running on the built-in `html.parser`, so it adds one package and no `lxml`.
- Take `<main>`, else `<article>`, else `<body>`.
- Always drop `nav`, `header`, `footer`, `aside`, `script`, `style`, `noscript`, `form`.
- Collapse whitespace before hashing, so reformatted HTML does not start a version.
- Extraction reads only the landed raw file (D28), never the network.
Why (San): I've used it before (registry-reader) and can explain every rule. In the demo I'll be asked why a page got a new version, so I need an extractor whose behaviour I can explain line by line, not a detector I have to trust.
Cost: one runtime dependency. Sites without `<main>` or `<article>` may leak furniture into the text, which means fake versions. The fix is to improve the rules and rebuild versions from the raw files (choice 4), so a bad rule is never permanent.
We will switch if: fake versions from leaked furniture show up on more than a few saved sites, even after improving the rules. Then trial trafilatura on the same raw files and compare versions per page.
