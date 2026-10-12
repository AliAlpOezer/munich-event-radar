import assert from "node:assert/strict";
import { test } from "node:test";

import {
  humanReason, isPast, isUpcoming, organiserNames, placeDay, registration, sortTime, venueText, when,
} from "../../site/js/facts.js";
import { today } from "../../site/js/time.js";

const shift = (days) => {
  const d = new Date(`${today()}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
};
const event = (fields, rest = {}) => ({
  fields: Object.fromEntries(Object.entries(fields).map(([k, value]) => [k, { value }])),
  organiser_ids: [],
  drop_reason: null,
  ...rest,
});

test("an event that started earlier and still runs is listed today, without a time", () => {
  const running = event({ starts_at: `${shift(-1)}T09:00:00`, ends_at: `${shift(1)}T17:00:00` });
  assert.equal(placeDay(running), today());
  assert.equal(sortTime(running), "");
  assert.ok(isUpcoming(running));
  const later = event({ starts_at: `${shift(3)}T18:00:00` });
  assert.equal(placeDay(later), shift(3));
  assert.equal(sortTime(later), "18:00");
});

test("an event is past after its last day, or when the judge says so", () => {
  assert.ok(isPast(event({ starts_at: shift(-2) })));
  assert.ok(!isPast(event({ starts_at: today() })));
  assert.ok(isPast(event({ starts_at: shift(5) }, { drop_reason: "past" })));
  assert.ok(!isUpcoming(event({ starts_at: shift(5) }, { drop_reason: "online_only" })));
});

test("the registration window in a few words", () => {
  const reg = (fields) => registration(event(fields));
  assert.deepEqual(reg({ reg_availability: "sold_out" }), { text: "Sold out", cls: "bad" });
  assert.deepEqual(reg({ reg_closes_at: shift(-1) }), { text: "Registration closed", cls: "bad" });
  assert.equal(reg({ reg_availability: "open", reg_closes_at: shift(3), spots_left: 4 }).cls, "warn");
  assert.match(reg({ reg_availability: "open", reg_closes_at: shift(3), spots_left: 4 }).text, /^Open until .*, 4 left$/);
  assert.deepEqual(reg({ reg_availability: "open", reg_closes_at: shift(30) }).cls, "ok");
  assert.deepEqual(reg({ reg_availability: "open" }), { text: "Open", cls: "ok" });
  assert.deepEqual(reg({ reg_availability: "not_open" }), { text: "Not open yet", cls: "warn" });
  assert.deepEqual(reg({ reg_mode: "approval" }), { text: "Approval needed", cls: "" });
  assert.deepEqual(reg({ reg_url: "https://a.org/r" }), { text: "Registration link", cls: "" });
  assert.deepEqual(reg({}), { text: "No registration info", cls: "" });
});

test("when: one moment, a same-day range, or two days", () => {
  const day = shift(10);
  const from = when(event({ starts_at: `${day}T18:00:00` }));
  assert.equal(when(event({ starts_at: `${day}T18:00:00`, ends_at: `${day}T21:00:00` })), `${from} to 21:00`);
  assert.match(when(event({ starts_at: day, ends_at: shift(11) })), / to /);
});

test("organisers come from the registry, else from the page", () => {
  const orgs = new Map([[1, { name: "CDTM" }], [2, { name: "TUM.ai" }]]);
  assert.equal(organiserNames(event({}, { organiser_ids: [1, 2] }), orgs), "CDTM, TUM.ai");
  assert.equal(organiserNames(event({ organiser: "Someone" }), orgs), "Someone");
});

test("a venue that is only a link points to the event page", () => {
  assert.equal(venueText(event({ venue: "https://maps.example/x" })), "Venue on the event page");
  assert.equal(venueText(event({ address: "Arcisstr. 21" })), "Arcisstr. 21");
});

test("the judge's reasons in words", () => {
  assert.equal(humanReason("price=free_student_route"), "Free route for students");
  assert.equal(humanReason("kind=panel_talk"), "Kind: panel talk");
  assert.equal(humanReason("unlisted=x"), "unlisted=x");
  assert.equal(humanReason("plain"), "plain");
});
