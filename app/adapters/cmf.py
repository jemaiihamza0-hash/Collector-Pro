"""Points d'entrée CMF vérifiés ; pas de génération de noms de PDF supposés."""


def cmf_seeds(origin):
    return [
        origin + "/?q=sitemap",
        origin + "/?q=bulletin-officiel",
        origin + "/?q=consultation-des-tats-financier-des-soci-t-s-faisant-ape",
    ]


def year_filter_links(soup, page_url):
    """Développe les filtres annuels GET exposés, sans combinaisons mois/jour."""
    import re
    from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

    links = []
    for form in soup.find_all("form"):
        if form.get("method", "get").lower() != "get":
            continue
        target = urlsplit(urljoin(page_url, form.get("action") or page_url))
        defaults = dict(parse_qsl(urlsplit(page_url).query, keep_blank_values=True))
        defaults.update(parse_qsl(target.query, keep_blank_values=True))
        for element in form.select("input[name]"):
            name = element["name"]
            if (
                element.get("type") == "hidden"
                and "token" not in name
                and "build_id" not in name
            ):
                defaults[name] = element.get("value", "")
        for select in form.select("select[name]"):
            chosen = select.find("option", selected=True) or select.find("option")
            if chosen:
                defaults[select["name"]] = chosen.get("value", "")
        for select in form.select("select[name]"):
            values = [o.get("value", "") for o in select.find_all("option")]
            years = [v for v in values if re.fullmatch(r"(19|20)\d{2}", v)]
            if not years:
                continue
            for year in years:
                query = {**defaults, select["name"]: year}
                query.pop("page", None)
                links.append(
                    urlunsplit(
                        (
                            target.scheme,
                            target.netloc,
                            target.path,
                            urlencode(query),
                            "",
                        )
                    )
                )
    return links
