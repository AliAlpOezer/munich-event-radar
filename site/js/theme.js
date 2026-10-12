/* The light/dark toggle. theme-init.js applies the saved choice before the first paint. */

import { $ } from "./dom.js";

export function wireTheme() {
  const toggle = $("theme-toggle");
  const root = document.documentElement;
  const label = () => toggle.setAttribute("aria-label", `Switch to ${root.dataset.theme === "dark" ? "light" : "dark"} theme`);
  label();
  toggle.addEventListener("click", () => {
    const dark = root.dataset.theme !== "dark";
    if (dark) root.dataset.theme = "dark";
    else delete root.dataset.theme;
    label();
    try {
      localStorage.setItem("radar.theme", dark ? "dark" : "light");
    } catch {
      /* storage blocked: the theme resets to light on reload */
    }
  });
}
