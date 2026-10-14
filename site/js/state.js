/* What the page knows. Loaders write it and call changed(); views read it and render. */

import { TIERS } from "./facts.js";
import { restore } from "./storage.js";

export const DEFAULT_FILTERS = { q: "", org: "", tiers: TIERS, past: false, dropped: false, starred: false };

export const state = {
  events: [],
  organisers: [],
  orgById: new Map(),
  board: null, // board_status(): when the board was harvested, and its version
  run: null, // the latest run, shown to admins only
  user: null, // the Supabase user, or null when logged out
  admin: false,
  intakes: [],
  tabHash: "#timeline",
  filters: restore("radar.filters", DEFAULT_FILTERS),
  orgFilters: restore("radar.orgFilters", { q: "", kind: "", sort: "upcoming" }),
};

const listeners = [];
export const onChange = (listener) => listeners.push(listener);
export const changed = () => listeners.forEach((listener) => listener());
