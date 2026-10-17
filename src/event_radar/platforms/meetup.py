"""Meetup: event pages under a group, and each group's iCal feed."""

import re

from .base import Platform, host, segments


class Meetup(Platform):
    name = "meetup"
    hosts = ("meetup.com",)
    event_url = re.compile(r"^https?://(?:www\.)?meetup\.com/[^/]+/events/(\d+)")
    feed_uid = re.compile(r"^event_(\d+)@meetup\.com$")

    def organiser_source(self, url: str) -> str | None:
        path = segments(url)
        if host(url) == "meetup.com" and 1 <= len(path) <= 2 and path[1:] in ([], ["events"]):
            return f"https://www.meetup.com/{path[0]}/events/ical/"
        return None
