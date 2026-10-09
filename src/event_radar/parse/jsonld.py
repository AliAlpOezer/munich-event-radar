import json

from selectolax.lexbor import LexborHTMLParser

from ..model import Evidence, ParsedEvent
from .html import image_url

_AVAILABILITY = {
    "SoldOut": "sold_out",
    "InStock": "open",
    "LimitedAvailability": "open",
    "PreOrder": "not_open",
    "PreSale": "not_open",
    "Discontinued": "closed",
}
_ATTENDANCE = {"Offline": "in_person", "Online": "online", "Mixed": "mixed"}


def _items(tree: LexborHTMLParser):
    for script in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.text(strip=True))
        except ValueError:
            continue
        for item in data if isinstance(data, list) else data.get("@graph", [data]):
            if isinstance(item, dict):
                yield item


def _is_event(item: dict) -> bool:
    kind = item.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k.endswith("Event") for k in kinds)


def _tail(uri: object) -> str:
    return str(uri).rsplit("/", 1)[-1].replace("EventAttendanceMode", "")


def _location(location: object) -> dict[str, object]:
    place = location[0] if isinstance(location, list) and location else location
    if not isinstance(place, dict):
        return {"venue": place} if place else {}
    address = place.get("address")
    if isinstance(address, dict):
        street = address.get("streetAddress")
        postal = address.get("postalCode")
        city = address.get("addressLocality")
        text = ", ".join(str(p) for p in (street, " ".join(filter(None, (postal, city)))) if p)
        return {"venue": place.get("name"), "address": text or None, "city": city}
    return {"venue": place.get("name"), "address": address}


def _offers(offers: object) -> dict[str, object]:
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if not isinstance(offer, dict):
        return {}
    price = offer.get("price", offer.get("lowPrice"))
    fields = {
        "reg_availability": _AVAILABILITY.get(_tail(offer.get("availability", ""))),
        "reg_opens_at": offer.get("availabilityStarts") or offer.get("validFrom"),
        "reg_closes_at": offer.get("availabilityEnds") or offer.get("validThrough"),
        "reg_url": offer.get("url"),
    }
    if price is not None:
        fields["price"] = "free" if str(price) in {"0", "0.0", "0.00"} else "paid"
    return fields


def events(tree: LexborHTMLParser, base: str = "") -> list[ParsedEvent]:
    found = []
    for item in _items(tree):
        if not _is_event(item):
            continue
        organiser = item.get("organizer")
        values = {
            "title": item.get("name"),
            "starts_at": item.get("startDate"),
            "ends_at": item.get("endDate"),
            "event_url": item.get("url"),
            "image": image_url(item.get("image"), base),
            "attendance": _ATTENDANCE.get(_tail(item.get("eventAttendanceMode", ""))),
            "organiser": organiser.get("name") if isinstance(organiser, dict) else None,
            **_location(item.get("location")),
            **_offers(item.get("offers")),
        }
        if _tail(item.get("eventStatus", "")) == "EventCancelled":
            values["reg_availability"] = "closed"
        found.append(ParsedEvent({k: Evidence(v) for k, v in values.items() if v}))
    return found
