/* Starring an event: the logged-in user's own list of events to keep (ADR 0007). */

import { openLogin } from "./account.js";
import * as api from "./api.js";
import { $, esc } from "./dom.js";
import { val } from "./facts.js";
import { changed, state } from "./state.js";

/* Phosphor Icons 2.1.1, regular and fill weights (MIT) */
const ICON = `<svg class="off" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"><path d="M239.18,97.26A16.38,16.38,0,0,0,224.92,86l-59-4.76L143.14,26.15a16.36,16.36,0,0,0-30.27,0L90.11,81.23,31.08,86a16.46,16.46,0,0,0-9.37,28.86l45,38.83L53,211.75a16.38,16.38,0,0,0,24.5,17.82L128,198.49l50.53,31.08A16.4,16.4,0,0,0,203,211.75l-13.76-58.07,45-38.83A16.43,16.43,0,0,0,239.18,97.26Zm-15.34,5.47-48.7,42a8,8,0,0,0-2.56,7.91l14.88,62.8a.37.37,0,0,1-.17.48c-.18.14-.23.11-.38,0l-54.72-33.65a8,8,0,0,0-8.38,0L69.09,215.94c-.15.09-.19.12-.38,0a.37.37,0,0,1-.17-.48l14.88-62.8a8,8,0,0,0-2.56-7.91l-48.7-42c-.12-.1-.23-.19-.13-.5s.18-.27.33-.29l63.92-5.16A8,8,0,0,0,103,91.86l24.62-59.61c.08-.17.11-.25.35-.25s.27.08.35.25L153,91.86a8,8,0,0,0,6.75,4.92l63.92,5.16c.15,0,.24,0,.33.29S224,102.63,223.84,102.73Z"/></svg><svg class="on" viewBox="0 0 256 256" fill="currentColor" aria-hidden="true"><path d="M234.29,114.85l-45,38.83L203,211.75a16.4,16.4,0,0,1-24.5,17.82L128,198.49,77.47,229.57A16.4,16.4,0,0,1,53,211.75l13.76-58.07-45-38.83A16.46,16.46,0,0,1,31.08,86l59-4.76,22.76-55.08a16.36,16.36,0,0,1,30.27,0l22.75,55.08,59,4.76a16.46,16.46,0,0,1,9.37,28.86Z"/></svg>`;

/* A toggle; logged out, it opens the login. `labelled` shows the word beside the icon. */
export function starButton(ev, { labelled = false } = {}) {
  const on = Boolean(state.user && ev.starred);
  const name = labelled ? "" : ` aria-label="Star ${esc(val(ev, "title") || "this event")}" title="${on ? "Unstar" : "Star"}"`;
  return `<button type="button" class="${labelled ? "btn " : ""}star" data-star="${esc(ev.key)}" aria-pressed="${on}"${name}>${ICON}${labelled ? "<span>Star</span>" : ""}</button>`;
}

const pending = new Set();
const find = (key) => state.events.find((e) => e.key === key);

/* The views re-render on every change; this finds the new button in the same place and
   gives it back the focus the old one lost. */
function settle(scope, key, { focus, pop }) {
  const button = scope?.querySelector(`[data-star="${CSS.escape(key)}"]`);
  if (!button) return;
  if (focus && document.activeElement === document.body) button.focus({ preventScroll: true });
  if (pop) button.classList.add("pop");
}

/* Shows the change at once and takes it back if the radar refuses it. One call per event
   at a time, so answers cannot arrive out of order. */
export async function toggleStar(button) {
  if (!state.user) return openLogin("star", button);
  const key = button.dataset.star;
  const ev = find(key);
  if (!ev || pending.has(key)) return;
  const want = !ev.starred;
  const scope = button.closest("[role=tabpanel], .drawer");
  const focused = document.activeElement === button;
  const show = (starred, pop) => {
    const current = find(key);
    if (current) current.starred = starred;
    changed();
    settle(scope, key, { focus: focused, pop });
  };
  pending.add(key);
  show(want, want);
  try {
    await api.setStar(key, want);
  } catch (error) {
    show(!want, false);
    toast(`Could not ${want ? "star" : "unstar"} the event: ${error.message}`);
  } finally {
    pending.delete(key);
  }
}

let toastTimer;
function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    box.hidden = true;
  }, 6000);
}
