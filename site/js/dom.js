/* Small DOM helpers every view shares. */

export const $ = (id) => document.getElementById(id);

const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
export const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ENTITIES[c]);

export const skeleton = (rows) => `<div class="skeleton">${"<div></div>".repeat(rows)}</div>`;

export function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
