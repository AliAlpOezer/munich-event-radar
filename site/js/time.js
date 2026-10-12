/* Dates: every moment is shown in Munich time. Date-only values stay all-day. */

const TZ = "Europe/Berlin";
const partsFormat = new Intl.DateTimeFormat("en-GB", {
  timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

/* { day: "YYYY-MM-DD", time: "HH:MM" | null } in Munich, or null when unreadable. A
   timestamp without an offset is already local and is taken as written. */
export function munich(value) {
  if (!value) return null;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return { day: text, time: null };
  if (!/([zZ]|[+-]\d{2}:?\d{2})$/.test(text)) {
    const m = text.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/);
    return m ? { day: m[1], time: m[2] } : null;
  }
  const moment = new Date(text);
  if (Number.isNaN(moment.getTime())) return null;
  const p = Object.fromEntries(partsFormat.formatToParts(moment).map((x) => [x.type, x.value]));
  return { day: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` };
}

export const today = () => munich(new Date().toISOString()).day;
const noon = (day) => new Date(`${day}T12:00:00Z`);
export const formatDay = (day, options) => new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...options }).format(noon(day));
export const daysBetween = (a, b) => Math.round((noon(b) - noon(a)) / 86400000);

export function formatMoment(value) {
  const m = munich(value);
  if (!m) return value ? String(value) : "";
  const options = { weekday: "short", day: "numeric", month: "short" };
  if (m.day.slice(0, 4) !== today().slice(0, 4)) options.year = "numeric";
  const day = formatDay(m.day, options);
  return m.time ? `${day}, ${m.time}` : day;
}

export function relative(iso) {
  if (!iso) return "never";
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 36 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}
