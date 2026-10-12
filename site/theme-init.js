/* Light unless this browser chose dark; runs before the first paint so the page never flashes. */
try {
  if (localStorage.getItem("radar.theme") === "dark") document.documentElement.dataset.theme = "dark";
} catch {
  /* storage blocked: light */
}
