/* Which events the timeline shows for its filters. */

import { isDropped, isPast, searchText } from "./facts.js";

/* The filters are remembered across logins and a logged-out page has no stars, so
   `starred` narrows only while someone is logged in. */
export function visibleEvents(events, filters, { orgById, loggedIn }) {
  const q = filters.q.trim().toLowerCase();
  return events.filter((ev) => {
    if (isDropped(ev)) {
      if (!filters.dropped) return false;
    } else if (isPast(ev)) {
      if (!filters.past) return false;
    } else if (!filters.tiers.includes(ev.tier)) {
      return false;
    }
    if (filters.starred && loggedIn && !ev.starred) return false;
    if (filters.org && !ev.organiser_ids.includes(Number(filters.org))) return false;
    return !q || searchText(ev, orgById).includes(q);
  });
}
