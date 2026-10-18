/* The side drawer. It shows one thing at a time: an event, an intake, the form to suggest
   an organisation or add an event, or the account; `open` says which, and each view checks
   it before updating the drawer. */

import { $ } from "./dom.js";
import { state } from "./state.js";

export const open = { mode: null, id: null };
let opener = null;

export const isOpen = (mode, id) => open.mode === mode && (id === undefined || open.id === id);

/* badge: { cls, text } for an event's tier, or null. */
export function setHeading(title, badge = null) {
  const tier = $("drawer-tier");
  tier.hidden = !badge;
  if (badge) {
    tier.className = `tier-badge ${badge.cls}`;
    tier.textContent = badge.text;
  }
  $("drawer-title").textContent = title;
}

export function showDrawer({ mode, id = null, title, badge = null, hash, from = null }) {
  open.mode = mode;
  open.id = id;
  if (from) opener = from;
  setHeading(title, badge);
  fillTools("");
  history.replaceState(null, "", hash);
  $("drawer").hidden = false;
  $("scrim").hidden = false;
  $("drawer-close").focus();
}

export const fillDrawer = (html) => {
  $("drawer-body").innerHTML = html;
};

/* Buttons beside Close that belong to what is open, such as an event's star. */
export const fillTools = (html) => {
  $("drawer-tools").innerHTML = html;
};

export function closeDrawer() {
  open.mode = null;
  open.id = null;
  history.replaceState(null, "", state.tabHash);
  $("drawer").hidden = true;
  $("scrim").hidden = true;
  if (opener && document.body.contains(opener)) opener.focus();
  opener = null;
}
