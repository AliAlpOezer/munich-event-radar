"""pretix (pretix.eu): `/<organiser>/<event>/`, beside pretix's own paths."""

import re

from .base import Platform, host, segments


class Pretix(Platform):
    name = "pretix"
    hosts = ("pretix.eu",)
    event_url = re.compile(r"^https?://pretix\.eu/([^/?#]+)/([^/?#]+)")
    not_event_prefixes = ("locale/", "control/", "api/", "static/", "about/")

    def organiser_source(self, url: str) -> str | None:
        path = segments(url)
        if host(url) == "pretix.eu" and len(path) == 1:
            return f"https://pretix.eu/{path[0]}/"
        return None
