from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup
from defusedxml import ElementTree
from .cmf import cmf_seeds, year_filter_links


def seeds(url):
    p = urlsplit(url)
    origin = f"{p.scheme}://{p.netloc}"
    result = [
        url,
        origin + "/sitemap.xml",
        origin + "/sitemap_index.xml",
        origin + "/rss.xml",
    ]
    if p.hostname.removeprefix("www.") == "cmf.tn":
        result.extend(cmf_seeds(origin))
    return list(dict.fromkeys(result))


def html_links(body, url):
    soup = BeautifulSoup(body, "html.parser")
    base = soup.find("base", href=True)
    base_url = urljoin(url, base["href"]) if base else url
    links = [
        urljoin(base_url, tag.get("href") or tag.get("src") or tag.get("data"))
        for tag in soup.select(
            'a[href],iframe[src],embed[src],object[data],link[rel="next"],link[rel="alternate"]'
        )
        if tag.get("href") or tag.get("src") or tag.get("data")
    ]
    if urlsplit(url).hostname.removeprefix("www.") == "cmf.tn":
        links.extend(year_filter_links(soup, url))
    title = soup.title.get_text(" ", strip=True) if soup.title else url
    # Le plan CMF fournit les rubriques ; leurs liens de pagination sont suivis.
    return title, list(dict.fromkeys(links))


def xml_links(body, url):
    root = ElementTree.fromstring(body)
    links = []
    for tag in root.iter():
        name = tag.tag.rsplit("}", 1)[-1].lower()
        value = (tag.text or "").strip()
        if name == "loc" and value:
            links.append(urljoin(url, value))
        elif name in ("link", "enclosure"):
            href = tag.get("href") or tag.get("url") or value
            if href:
                links.append(urljoin(url, href))
    return list(dict.fromkeys(links))
