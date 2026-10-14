# ADR 0007: Starring events

**Status:** DECIDED, built on branch `star-events`
**Date:** 2026-10-14
**Inputs:** Alp, 2026-10-14: "We want to add some "starring" or "liking" option to events
like we are having on twitter. That must be personal, also we need to keep the favorite
events of course individualisticly." ADR 0005 (the database is the API: views to read,
definer functions to write, RLS on every table).

## What this is for

A logged-in person stars an event with one click on its row or in its drawer, and finds
their starred events again with a "Starred" filter, on any device they log in from. Nobody
else sees whose stars they are or how many an event has.

## Decisions

| # | Decision | Rejected, and why |
|---|---|---|
| 1 | A star is a row in its own table `radar.star (user_id, event_key, starred_at)`, keyed by (user, event); unstarring deletes the row. Rows go with the user or the event (`ON DELETE CASCADE`) | A `starred` column on `user_state`: a star is independent of the status, so a star alone would need a placeholder status `none`, and a merge keeps the survivor's own status row, which would drop a star set on the losing event. Stars in the browser's local storage: lost on another device or when storage is cleared, which is not "keeping" them |
| 2 | Stars are private. RLS lets a user read only their own rows; `api.events` gains `starred`, true only for the caller's own stars. No counts | A public count per event, as Twitter shows likes: Alp asked for personal stars, and with few users a count of 1 can say who is going where. It would also need a definer aggregate beside the invoker views |
| 3 | Writes go through `api.set_star(event_key, starred)`, `SECURITY DEFINER`, checking `auth.uid()`, with the wanted state as an argument, so a repeat is a no-op | `toggle_star(event_key)`: a retried or doubled request flips the star back. A table grant with an RLS `WITH CHECK`: ADR 0005 D4 keeps no direct write path from the page |
| 4 | No per-user cap: the key allows one star per user and event, so the board bounds the rows | A cap like the 5 open suggestions (ADR 0005 D8): those cost LLM calls, stars cost one small row each |
| 5 | A merge moves each user's star to the surviving event unless they starred it already, the rule statuses follow (`store/events.py`) | - |
| 6 | The page changes the star at once, sends one call per event at a time so answers cannot arrive out of order, and takes the change back with a message if the call fails | Waiting for the answer before showing the star: a click that seems to do nothing for a round trip |
| 7 | "Starred" is a filter on the timeline, shown only to a logged-in user. Turning it on turns on every tier, as following an organiser's events does, and past starred events show with "Past" | A separate "Starred" tab: a second list beside the timeline that would repeat its grouping by day, and a fourth item in a top bar that already wraps on phones |
| 8 | A star (bookmark), not a heart | A heart reads as applause; here the point is keeping an event to come back to |

## Deploying

The migration only adds a table, a view column and a function, so the page before this
change keeps working on the migrated database. The page after it needs the migration: until
then its stars stay empty and starring fails with a message. The hosted database migrates
whenever a job opens the store, so a job runs on this code before the page is published.

## Open

- Reminders for starred events (registration opening or closing soon) would fit the
  harvester's existing schedule; nothing reads stars outside the page yet.
