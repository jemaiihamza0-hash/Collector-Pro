import ipaddress
import socket
import threading
from urllib.parse import urljoin, urlsplit, urlunsplit, quote, parse_qsl, urlencode
from urllib.robotparser import RobotFileParser
import httpx
from .config import settings


class PolicyError(ValueError):
    pass


def normalize(url):
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        raise PolicyError("URL HTTP(S) complète requise, sans identifiants.")
    if p.port not in (None, 80, 443):
        raise PolicyError("Ports 80 et 443 uniquement.")
    if len(url) > 4096:
        raise PolicyError("URL trop longue.")
    query = [
        (k, v)
        for k, v in parse_qsl(p.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in ("fbclid", "gclid")
    ]
    netloc = p.hostname.encode("idna").decode().lower()
    if ":" in netloc:
        netloc = "[" + netloc + "]"
    if p.port:
        netloc += ":" + str(p.port)
    return urlunsplit(
        (
            p.scheme,
            netloc,
            quote(p.path or "/", safe="/%:@!$&'()*+,;=-._~"),
            urlencode(query),
            "",
        )
    )


def public_address(url):
    host = urlsplit(normalize(url)).hostname
    addresses = socket.getaddrinfo(host, None)
    if not addresses or any(
        not ipaddress.ip_address(a[4][0]).is_global for a in addresses
    ):
        raise PolicyError("Adresse locale ou privée refusée.")


def allowed_hosts(url, extra):
    host = urlsplit(normalize(url)).hostname
    hosts = {host}
    if host.startswith("www."):
        hosts.add(host[4:])
    else:
        hosts.add("www." + host)
    for value in extra:
        hosts.add(
            urlsplit(
                normalize(value if "://" in value else "https://" + value)
            ).hostname
        )
    return sorted(hosts)


class Policy:
    def __init__(self, hosts, respect):
        self.hosts, self.respect = set(hosts), respect
        self.cache = {}
        self.lock = threading.RLock()

    def scope(self, url):
        url = normalize(url)
        if urlsplit(url).hostname not in self.hosts:
            raise PolicyError("Hôte hors du périmètre autorisé.")
        public_address(url)
        return url

    def robots(self, url):
        p = urlsplit(url)
        origin = urlunsplit((p.scheme, p.netloc, "", "", ""))
        with self.lock:
            if origin in self.cache:
                return self.cache[origin]
            rule = RobotFileParser()
            target = origin + "/robots.txt"
            try:
                with httpx.Client(timeout=15, trust_env=False) as client:
                    for _ in range(6):
                        self.scope(target)
                        with client.stream(
                            "GET", target, headers={"User-Agent": settings.user_agent}
                        ) as response:
                            if response.status_code in (301, 302, 303, 307, 308):
                                target = urljoin(target, response.headers["location"])
                                continue
                            if response.status_code in (404, 410):
                                rule.parse([])
                                break
                            if response.status_code != 200:
                                raise PolicyError(
                                    "robots.txt inaccessible : politique non vérifiable."
                                )
                            body = bytearray()
                            for chunk in response.iter_bytes():
                                body.extend(chunk)
                                if len(body) > 512 * 1024:
                                    raise PolicyError("robots.txt trop volumineux.")
                            rule.parse(
                                body.decode("utf-8", errors="replace").splitlines()
                            )
                            break
                    else:
                        raise PolicyError("Trop de redirections robots.txt.")
            except Exception:
                self.cache[origin] = None
                raise
            self.cache[origin] = rule
            return rule

    def check(self, url):
        self.scope(url)
        if self.respect:
            rule = self.robots(url)
            if rule is None or not rule.can_fetch(settings.user_agent, url):
                raise PolicyError(
                    "URL exclue par robots.txt ou politique indisponible."
                )
            return rule.crawl_delay(settings.user_agent) or 0
        return 0
