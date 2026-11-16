// Usher — small helpers shared by every screen.
import { h, icon } from "./dom.js";
// ---- small helpers ------------------------------------------------------------------------
// Scores: IMDb (or TMDb when IMDb has none), then Rotten Tomatoes and Metacritic. `short` gives just the first.
export function scoreLine(rt, short = false) {
  if (!rt) return "";
  const parts = [];
  if (rt.imdb) parts.push(`IMDb ${rt.imdb.toFixed(1)}`);
  else if (rt.tmdb) parts.push(`TMDb ${rt.tmdb.toFixed(1)}`);
  if (rt.rt != null) parts.push(`Rotten Tomatoes ${rt.rt}%`);
  if (rt.mc != null) parts.push(`Metacritic ${rt.mc}`);
  return short ? (parts[0] || "") : parts.join(" · ");
}

// A search box's clear (×) button: shown only when there's text; clearing keeps the keyboard up.
export function clearButton(input, onClear) {
  const btn = h("button", { type: "button", class: "clear", "aria-label": "Clear", hidden: !input.value,
    onclick: (e) => { e.preventDefault(); input.value = ""; btn.hidden = true; onClear(); input.focus(); } }, icon("close"));
  input.addEventListener("input", () => { btn.hidden = !input.value; });
  return btn;
}

export function fill(el, ...children) { el.replaceChildren(...children.flat(Infinity).filter((k) => k !== null && k !== undefined && k !== false)); }

// Up to two capital letters from a display name: the first letters of its first and last words. Empty when the name has none.
export function initials(name) {
  const words = String(name ?? "").split(/\s+/).map((w) => Array.from(w).find((c) => /[\p{L}\p{N}]/u.test(c))).filter(Boolean);
  const picked = words.length > 1 ? [words[0], words.at(-1)] : words;
  return picked.join("").toLocaleUpperCase();
}

export const fmt = {
  eta(sec) {
    if (sec === null || sec === undefined) return "";
    if (sec < 60) return "under a minute";
    if (sec < 3600) return `about ${Math.round(sec / 60)} min`;
    const hrs = Math.floor(sec / 3600), min = Math.round((sec % 3600) / 60);
    return hrs >= 24 ? `about ${Math.round(hrs / 24)} days` : `about ${hrs} hr${min >= 10 ? ` ${min} min` : ""}`;
  },
  gb(bytes) {
    if (!bytes) return "—";
    return bytes >= 1e12 ? `${(bytes / 1e12).toFixed(1)} TB` : bytes >= 1e9 ? `${(bytes / 1e9).toFixed(1)} GB`
      : `${Math.max(1, Math.round(bytes / 1e6))} MB`;
  },
  dayLabel(date) {
    const d = new Date(date); const today = new Date(); today.setHours(0, 0, 0, 0);
    const that = new Date(d); that.setHours(0, 0, 0, 0);
    const diff = Math.round((that - today) / 86400000);
    if (diff === 0) return "Today";
    if (diff === 1) return "Tomorrow";
    if (diff === -1) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  },
  // "This week", "Next week", then "Week of 23 November" (weeks start on Monday). `date` is a YYYY-MM-DD day.
  weekLabel(date) {
    const monday = (d) => { const m = new Date(d); m.setHours(0, 0, 0, 0); m.setDate(m.getDate() - ((m.getDay() + 6) % 7)); return m; };
    const that = monday(new Date(`${date}T12:00:00`)), now = monday(new Date());
    const weeks = Math.round((that - now) / (7 * 86400000));
    if (weeks <= 0) return "This week";
    if (weeks === 1) return "Next week";
    return `Week of ${that.toLocaleDateString(undefined, { day: "numeric", month: "long" })}`;
  },
  shortDate(date) { return new Date(`${date}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" }); },
  time(date) { return new Date(date).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); },
  ago(date) {
    if (!date) return "";
    const days = Math.floor((Date.now() - new Date(date)) / 86400000);
    return days <= 0 ? "today" : days === 1 ? "yesterday" : `${days} days ago`;
  },
  added(date) { return date ? new Date(date).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : ""; },
};

export const prefs = {
  get(k, d) { try { return localStorage.getItem(`usher.${k}`) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(`usher.${k}`, v); } catch { /* private window */ } },
};
