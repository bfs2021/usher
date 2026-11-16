// Usher — Settings → About → Updates: whether the server is keeping up with new versions. Read-only, shown to every signed-in account.
import { SignedOut, api } from "./data.js";
import { h } from "./dom.js";
import { fill } from "./util.js";

// How the server's state reads. Each line says the state in words, since the dot's colour alone must not carry it.
export function updatesText(view, nowMs = Date.now()) {
  const newest = view.check?.newest;
  switch (view.state) {
    case "up_to_date": return { label: "Up to date", detail: `Checked ${minutesAgo(view.check.at, nowMs)}`, dot: "" };
    case "waiting": return { label: "Update waiting", detail: `Version ${newest} installs by itself within 15 minutes`, dot: " off" };
    case "held": return { label: "Held", detail: `Version ${newest} needs a step on the server first, so it won't install by itself`, dot: " warn" };
    case "rolled_back": return { label: "Last update rolled back", detail: `Version ${newest} didn't work, so the previous version is still running`, dot: " warn" };
    default: return { label: "Can't tell", detail: "The server hasn't reported in over an hour, so it may have stopped checking", dot: " off" };
  }
}

function minutesAgo(seconds, nowMs) {
  const minutes = Math.max(0, Math.floor((nowMs / 1000 - seconds) / 60));
  if (minutes < 1) return "just now";
  return minutes === 1 ? "1 minute ago" : `${minutes} minutes ago`;
}

export const Updates = (Base) => class extends Base {
  // The last answer is kept for the next time Settings draws, so the row doesn't flash "Checking…" on every redraw.
  updatesRow() {
    const row = h("div", { class: "row" });
    this.fillUpdates(row, this.updatesView);
    this.refreshUpdates(row);
    return row;
  }

  fillUpdates(row, view, error) {
    const text = view ? updatesText(view) : null;
    fill(row, h("span", { class: `dot${text ? text.dot : " off"}`, "aria-hidden": "true" }),
      h("div", { class: "text" }, h("div", { class: "title" }, "Updates"),
        h("div", { class: "sub wrap" }, error ?? (text ? `${text.label} · ${text.detail}` : "Checking…"))),
      h("span", {}));
  }

  async refreshUpdates(row) {
    try { this.updatesView = await api("/api/updates"); }
    catch (e) {
      if (e instanceof SignedOut) return this.signIn();
      return this.fillUpdates(row, null, e.message);
    }
    this.fillUpdates(row, this.updatesView);
  }
};
