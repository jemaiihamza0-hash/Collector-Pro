"""Politique de port de test seulement ; jamais importée par app/."""

from urllib.parse import urlsplit, urlunsplit
from app.urls import normalize as original


def fixture_normalizer(port):
    def normalize(url):
        p = urlsplit(url)
        if p.hostname == "127.0.0.1" and p.port == port:
            clean = original(
                urlunsplit((p.scheme, p.hostname, p.path, p.query, p.fragment))
            )
            c = urlsplit(clean)
            return urlunsplit((c.scheme, p.netloc, c.path, c.query, c.fragment))
        return original(url)

    return normalize
