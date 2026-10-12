import assert from "node:assert/strict";
import { test } from "node:test";

import { daysBetween, formatMoment, munich, relative, today } from "../../site/js/time.js";

test("a moment with an offset is shown in Munich time, summer and winter", () => {
  assert.deepEqual(munich("2026-07-01T16:30:00Z"), { day: "2026-07-01", time: "18:30" });
  assert.deepEqual(munich("2026-12-01T23:30:00+00:00"), { day: "2026-12-02", time: "00:30" });
});

test("a date stays all-day and a timestamp without offset is taken as written", () => {
  assert.deepEqual(munich("2026-10-20"), { day: "2026-10-20", time: null });
  assert.deepEqual(munich("2026-10-20T18:00:00"), { day: "2026-10-20", time: "18:00" });
  assert.equal(munich("next Tuesday"), null);
  assert.equal(munich(null), null);
});

test("days are counted across a clock change", () => {
  assert.equal(daysBetween("2026-10-24", "2026-10-26"), 2);
  assert.equal(daysBetween("2026-03-30", "2026-03-28"), -2);
});

test("a moment reads as weekday, day, month and time, with the year only when not this one", () => {
  const year = Number(today().slice(0, 4));
  assert.equal(formatMoment(`${year}-07-01T16:30:00Z`), `${weekday(`${year}-07-01`)} 1 Jul, 18:30`);
  assert.match(formatMoment(`${year + 1}-07-01`), new RegExp(`${year + 1}$`));
  assert.equal(formatMoment("someday"), "someday");
});

test("how long ago, in words", () => {
  const ago = (minutes) => new Date(Date.now() - minutes * 60000).toISOString();
  assert.equal(relative(null), "never");
  assert.equal(relative(ago(0)), "just now");
  assert.equal(relative(ago(5)), "5 min ago");
  assert.equal(relative(ago(180)), "3 h ago");
  assert.equal(relative(ago(3 * 24 * 60)), "3 days ago");
});

function weekday(day) {
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short" }).format(new Date(`${day}T12:00:00Z`));
}
