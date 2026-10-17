"""Eventbrite: event pages end in the event's numeric id, organisers sit under `/o/`."""

import re

from .base import Platform, host, segments


class Eventbrite(Platform):
    name = "eventbrite"
    hosts = ("eventbrite.",)
    event_url = re.compile(r"^https?://(?:www\.)?eventbrite\.[a-z.]+/e/(?:[^/?#]*-)?(\d{9,})")

    def organiser_source(self, url: str) -> str | None:
        path = segments(url)
        if host(url).startswith("eventbrite.") and len(path) == 2 and path[0] == "o":
            return url
        return None
