// Usher — the Home screen.
import { api, store } from "./data.js";
import { titleRow, titleCard } from "./rows.js";
import { h, icon } from "./dom.js";
import { fmt, prefs } from "./util.js";
import { menu } from "./menu.js";
import { OPEN_IN, watchLinks } from "./watch.js";

export const Home = (Base) => class extends Base {
  // ---- Home --------------------------------------------------------------------------------
  // "Unread" like Mail: an item is new until you open it. Only things added after you started using Usher count.
  unread(card) {
    if (!card?.added || !store.me) return false;
    const added = Date.parse(card.added) / 1000;
    return added > Math.max(store.me.baseline || 0, store.me.opened?.[card.jf] || 0);
  }

  // Every new item once (an item can sit in more than one Latest row), newest first.
  unreadItems() {
    const seen = new Set();
    const items = [];
    for (const row of store.snap?.home || []) {
      if (!row.id.startsWith("latest-")) continue;
      for (const c of row.items) {
        if (seen.has(c.jf)) continue;
        seen.add(c.jf);
        if (this.unread(c)) items.push(c);
      }
    }
    return items.sort((a, b) => Date.parse(b.added) - Date.parse(a.added));
  }

  unreadCount() {
    return this.unreadItems().length;
  }

  // Clears every new-item dot, the New row and the app icon badge at once.
  async markAllSeen() {
    try {
      const r = await api("/api/opened/all", { method: "POST" });
      store.me.baseline = r.baseline;
      store.me.opened = {};
      this.updateBadges();
      this.lastHomeShape = null;
      if (this.current === "/") this.render(false);
    } catch (e) { this.handle(e); }
  }

  markOpened(card) {
    if (!card?.jf || !this.unread(card)) return;
    store.me.opened = { ...(store.me.opened || {}), [card.jf]: Math.floor(Date.now() / 1000) };
    for (const dot of document.querySelectorAll(`[data-jf="${card.jf}"] .unread`)) dot.remove();
    this.updateNewRow();
    this.updateBadges();
    api("/api/opened", { method: "POST", body: { jf: card.jf } }).catch(() => {});
  }

  // The New row after one item is opened: it leaves the row in place, so the shelves don't jump back to the start.
  updateNewRow() {
    const row = this.main?.querySelector(".new-row");
    if (!row) return;
    const items = this.unreadItems();
    const ids = new Set(items.map((c) => c.jf));
    for (const card of row.querySelectorAll("[data-jf]")) if (!ids.has(card.dataset.jf)) card.remove();
    if (!items.length) return row.remove();
    row.querySelector(".count").textContent = items.length;
  }

  // What Home looks like, minus the numbers that move. If this is unchanged, only the numbers are updated.
  homeShape() {
    const s = store.snap || {};
    return JSON.stringify([
      (s.looking || []).map((d) => [d.kind, d.id, d.stuck]),
      (s.home || []).map((row) => [row.id, row.items.map((c) => [c.jf, c.played, c.added, Math.round((c.progress || 0) * 20)])]),
      store.online,
    ]);
  }

  liveHome() {
    const shape = this.homeShape();
    if (shape !== this.lastHomeShape || !this.main.querySelector(".content")) {
      this.lastHomeShape = shape;
      return this.render(false);
    }
  }

  home() {
    const s = store.snap;
    this.lastHomeShape = this.homeShape();
    const wrap = h("div", { class: "content" }, this.offlineBanner());
    if (!s || s.loading) return wrap.appendChild(h("p", { class: "empty" }, "Checking what's new…")), wrap;

    // What is on its way is in Downloads; a title someone asked for is shown there with its whole journey.
    const asked = new Set((s.requests || []).filter((r) => r.step < 4).map((r) => `${r.kind}:${r.id}`));
    const looking = s.looking.filter((d) => !asked.has(`${d.kind}:${d.id}`));

    // New: only what is unread, newest first, above everything else. Not there when nothing is new.
    const fresh = this.unreadItems();
    if (fresh.length) {
      wrap.append(h("section", { class: "section new-row", "aria-label": "New" },
        h("div", { class: "section-head" }, h("h2", {}, "New"),
          h("span", { class: "head-end" }, h("span", { class: "count" }, fresh.length),
            h("button", { class: "link", onclick: () => this.markAllSeen() }, "Mark all as read"))),
        h("div", { class: "shelf", role: "list" }, fresh.map((c) => this.jfCard(c, false)))));
    }

    // The same rows as Jellyfin's own home screen, for the shared viewing account.
    for (const row of s.home || []) {
      const unread = row.items.filter((c) => this.unread(c)).length;
      wrap.append(h("section", { class: "section", "aria-label": row.name },
        h("div", { class: "section-head" }, h("h2", {}, row.name), unread ? h("span", { class: "count new" }, `${unread} new`) : null),
        h("div", { class: `shelf${row.wide ? " wide" : ""}`, role: "list" }, row.items.map((c) => this.jfCard(c, row.wide)))));
    }

    if (looking.length) {
      const shown = looking.slice(0, 12);
      wrap.append(h("section", { class: "section", "aria-labelledby": "h-look" },
        h("div", { class: "section-head" }, h("h2", { id: "h-look" }, "Looking for"), h("span", { class: "count" }, looking.length)),
        h("div", { class: "list" }, shown.map((it) => titleRow({ art: it.art, title: it.title, subtitle: it.subtitle, onclick: () => this.details(it, "looking"),
          state: it.stuck ? `Still looking · since ${fmt.ago(it.since)}` : "Looking for it" }))),
        looking.length > shown.length ? h("p", { class: "footnote" }, `And ${looking.length - shown.length} more. They're all in Library.`) : null));
    }

    if (!(s.home || []).length && !looking.length) {
      wrap.append(h("div", { class: "empty" }, icon("popcorn"), h("h3", {}, "All caught up"),
        h("p", {}, "Nothing new right now."),
        h("p", {}, h("button", { class: "btn", style: { "margin-top": "16px", width: "auto" }, onclick: () => this.go("/search") }, "Find something to watch"))));
    }
    return wrap;
  }

  jfCard(c, wide) {
    const overlay = [
      c.progress ? h("div", { class: "card-progress" }, h("i", { style: { "--p": `${Math.round(c.progress * 100)}%` } })) : null,
      c.played ? h("span", { class: "played", "aria-hidden": "true" }, icon("check"))
        : this.unread(c) ? h("span", { class: `indicator unread${c.unplayed ? "" : " dot"}`, "aria-hidden": "true" }, c.unplayed ? (c.unplayed > 99 ? "99+" : String(c.unplayed)) : null)
          : null];
    return titleCard({ art: c.art, title: c.title, subtitle: c.subtitle, fallback: c.fallback, overlay, attrs: { "data-jf": c.jf },
      onclick: () => this.jfDetails(c), label: `${this.unread(c) ? "New: " : ""}${c.title}, ${c.subtitle}` });
  }

  jfDetails(c) {
    this.markOpened(c);
    const state = c.played ? ["Watched", "ready"] : c.progress ? [`${Math.round(c.progress * 100)}% watched`, "looking"] : null;
    const watch = watchLinks(store.me.jellyfinApp, store.me.jellyfinWeb, c.jf, prefs.get(OPEN_IN, "app"));
    const sheet = this.openSheet(() => [
      this.head(c.fallback || c.art, c.title, c.subtitle, state),
      c.overview ? h("p", { class: "overview" }, c.overview) : null,
      h("p", { class: "footnote" }, "It's in Jellyfin, ready to watch."),
      watch.main ? h("div", { class: "actions" },
        h("div", { class: "watch-row" },
          h("a", { class: "btn", href: watch.main.href, target: watch.main.app ? null : "_blank", rel: watch.main.app ? null : "noopener noreferrer" }, "Watch"),
          watch.other ? menu("More ways to open", [{ ...watch.other, external: !watch.other.app }]) : null),
        watch.main.app ? h("p", { class: "footnote" }, "Opens in Swiftfin. The app needs this server added and signed in as the shared account.") : null) : null,
    ]);
    if (c.key) sheet.title = { kind: c.key.split(":")[0], key: c.key, name: c.title };
  }
};
