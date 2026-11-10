"""Point d'entrée BVMT vérifié : la page des éditions liste les bulletins officiels.

Vérifié le 10/11/2026 : https://tunis-stockexchange.com/editions-statistique
publie les 17 derniers bulletins quotidiens sous la forme
/sites/default/files/AAAA-MM/BullAAAAMMJJ.pdf. Aucun nom de PDF n'est deviné :
seuls les liens réellement publiés sur cette page sont suivis, et rien d'autre
du site (navigation, plan du site, flux) n'est collecté pour cette source.
"""

import re
from urllib.parse import urlsplit

BVMT_HOSTS = {"tunis-stockexchange.com", "bvmt.com.tn"}
EDITIONS_PATH = "/editions-statistique"
BULLETIN_PATH = re.compile(r"^/sites/default/files/\d{4}-\d{2}/Bull\d{8}\.pdf$")


def is_bvmt(url):
    return (urlsplit(url).hostname or "").removeprefix("www.") in BVMT_HOSTS


def bvmt_seeds(origin):
    return [origin + EDITIONS_PATH]


def bulletin_links(links):
    """Garde uniquement les bulletins officiels publiés sur le site BVMT."""
    return [
        link
        for link in links
        if is_bvmt(link)
        and not urlsplit(link).query
        and BULLETIN_PATH.fullmatch(urlsplit(link).path)
    ]
