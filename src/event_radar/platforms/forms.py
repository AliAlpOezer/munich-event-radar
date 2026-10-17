"""Form services that events register with. A form is an event's registration page and
has a key; the services list no events of their own."""

import re

from .base import Platform


class Typeform(Platform):
    name = "typeform"
    hosts = ("typeform.com",)
    event_url = re.compile(r"^https?://form\.typeform\.com/to/([A-Za-z0-9]+)")


class Tally(Platform):
    name = "tally"
    hosts = ("tally.so",)
    event_url = re.compile(r"^https?://tally\.so/r/([A-Za-z0-9]+)")


class GoogleForms(Platform):
    name = "gform"
    hosts = ("forms.gle", "docs.google.com")
    event_url = re.compile(r"^https?://(?:forms\.gle/|docs\.google\.com/forms/d/(?:e/)?)([\w-]+)")
