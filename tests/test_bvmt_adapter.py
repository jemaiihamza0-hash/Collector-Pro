"""BVMT adapter: only the official bulletins published on the editions page."""

from app.adapters import html_links, seeds

PAGE = "https://tunis-stockexchange.com/editions-statistique"
# Link shapes copied from the real page (10/11/2026); no document content.
HTML = """<html><head><title>Editions</title></head><body>
<a href="/sites/default/files/2026-11/Bull20261106.pdf">Bulletin 06/11</a>
<a href="/sites/default/files/2026-11/Bull20261105.pdf">Bulletin 05/11</a>
<a href="/publications-societes">Publications</a>
<a href="/sites/default/files/2026-11/rapport-annuel.pdf">Rapport</a>
<a href="https://tunis-stockexchange.com/sites/default/files/2026-11/Bull20261104.pdf?x=1">Q</a>
<a href="https://example.com/sites/default/files/2026-11/Bull20261103.pdf">Ailleurs</a>
</body></html>"""


def test_bvmt_source_is_seeded_with_its_editions_page_only():
    assert seeds("https://tunis-stockexchange.com/") == [
        "https://tunis-stockexchange.com/",
        PAGE,
    ]
    assert not any("sitemap" in url or "rss" in url for url in seeds(PAGE))


def test_only_published_official_bulletins_are_followed():
    _, links = html_links(HTML, PAGE)
    assert links == [
        "https://tunis-stockexchange.com/sites/default/files/2026-11/Bull20261106.pdf",
        "https://tunis-stockexchange.com/sites/default/files/2026-11/Bull20261105.pdf",
    ]


def test_other_sites_keep_generic_discovery():
    _, links = html_links(HTML, "https://example.com/page")
    assert "https://example.com/publications-societes" in links
    assert "https://example.com/sitemap.xml" in seeds("https://example.com/")
