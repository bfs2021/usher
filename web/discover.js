// Usher — Discover and its sheets.
import { SignedOut, api, details, lists, store } from "./data.js";
import { titleCard } from "./rows.js";
import { whenSeen } from "./prefetch.js";
import { h, icon } from "./dom.js";
import { fill, fmt, scoreLine } from "./util.js";
import { filters } from "./filters.js";

const itemUrl = (c) => `/api/discover/item?kind=${c.kind}&id=${c.tmdb}`;

export const Discover = (Base) => class extends Base {
  // ---- Discover (TMDb; streaming data by JustWatch) ---------------------------------------------
  showDiscover(el) {
    const intro = h("div", { class: "empty" }, icon("search"), h("h3", {}, "Find something to watch"),
      h("p", {}, "Search for a movie or TV show, then tap Get it. You can follow it on Home until it's ready."));
    const d = lists.show("discover");     // drawn again by listLoaded when it arrives, or if it can't be had
    if (!d && !lists.tried("discover")) return fill(el, h("p", { class: "footnote" }, "Loading what's popular…"));
    if (!d?.enabled || !d.rows.length) return fill(el, intro);
    const keys = ["kind", "score", "when", "stream"];
    const services = [...new Set(d.rows.flatMap((row) => row.items.flatMap((c) => c.streaming || [])))].sort();
    const rows = d.rows.map((row) => ({ ...row, items: row.items.filter((c) => filters.match(c, keys)) })).filter((row) => row.items.length);
    fill(el, [this.filterBar(keys, services, () => this.showResults()),
      filters.active(keys) && !rows.length ? h("p", { class: "empty" }, "Nothing here matches these filters.") : null,
      ...rows.map((row) => h("section", { class: "section", "aria-label": row.name },
      h("div", { class: "section-head" }, h("h2", {}, row.name)),
      h("div", { class: "shelf", role: "list" }, row.items.map((c) => {
        const card = titleCard({ art: c.art, title: c.title, onclick: () => this.discoverSheet(c),
          overlay: c.have ? h("span", { class: "played", "aria-label": "In your library" }, icon("check"))
            : c.asked ? h("span", { class: "played asked", "aria-label": "Already asked for" }, icon("clock")) : null,
          subtitle: [c.year, c.kind === "movie" ? "Movie" : "TV"].filter(Boolean).join(" · ") });
        whenSeen(card, () => details.ahead(itemUrl(c)));   // so its sheet opens with where it's streaming at once
        return card;
      })))),
      h("p", { class: "footnote attribution" }, "Discover uses the TMDB API but is not endorsed or certified by TMDB. Streaming availability by JustWatch.")]);
  }

  async discoverSheet(c) {
    const loading = this.openSheet(() => [this.head(c.art, c.title, [c.year, c.kind === "movie" ? "Movie" : "TV show"].filter(Boolean).join(" · "), null),
      h("p", { class: "footnote" }, "Checking where it's streaming…")]);
    try {
      const r = await details.load(itemUrl(c));
      if (this.sheet !== loading) return;
      this.getSheet({ ...r, art: r.art || c.art });
    } catch (e) { if (e instanceof SignedOut) return this.signIn(); this.toast(e.message); loading.dialog.close(); }
  }

  // A row of menus; a menu that's filtering is highlighted. `services` lists streaming services in the results.
  filterBar(keys, services, redraw) {
    const f = filters.state;
    const opts = {
      kind: [["", "All types"], ["movie", "Movies"], ["show", "TV shows"]],
      score: [["", "Any score"], ["6", "Score 6+"], ["7", "Score 7+"], ["8", "Score 8+"]],
      when: [["", "Any year"], ["2", "Last 2 years"], ["5", "Last 5 years"], ["10", "Last 10 years"], ["old", "Older"]],
      stream: [["", "Streaming or not"], ["any", "On a service"], ["none", "Not streaming"],
        ...[...new Set([...services, ...(f.stream?.startsWith("svc:") ? [f.stream.slice(4)] : [])])].map((n) => [`svc:${n}`, `On ${n}`])],
      sort: [["", "Best match"], ["score", "Highest score"], ["new", "Newest"]],
    };
    const label = { kind: "Type", score: "Score", when: "Released", stream: "Streaming", sort: "Sort by" };
    return h("div", { class: "filters", role: "group", "aria-label": "Filters" }, keys.map((k) =>
      h("select", { class: `sort filter${f[k] ? " on" : ""}`, "aria-label": label[k],
        onchange: (e) => { if (e.target.value) f[k] = e.target.value; else delete f[k]; filters.save(); redraw(); } },
      opts[k].map(([v, text]) => h("option", { value: v, selected: (f[k] || "") === v }, text)))));
  }

  filterCount(shown, total, redraw) {
    return h("div", { class: "filter-count" }, h("span", {}, `Showing ${shown} of ${total}`),
      h("button", { class: "link", onclick: () => { filters.clear(); redraw(); } }, "Clear filters"));
  }

  providersBlock(r) {
    if (!r.providers) return null;
    if (!r.providers.length) {
      return h("p", { class: "footnote" }, r.rentable ? "Not on a streaming service here; only to rent or buy." : "Not streaming anywhere here right now.");
    }
    return h("div", { class: "providers" }, h("div", { class: "footnote" }, "Included with"),
      h("div", { class: "provider-list" }, r.providers.map((p) => h("span", { class: "provider" },
        p.logo ? h("img", { src: p.logo, alt: "", width: 28, height: 28, onerror: (e) => e.target.remove() }) : null, p.name))),
      h("div", { class: "footnote" }, "You may already be able to watch it without downloading."));
  }

  resultStatus(r) {
    if (!r.inLibrary) return null;
    if (r.kind === "movie") {
      if (r.hasFile) return ["In your library", "ready"];
      if (r.monitored) return [r.by ? `${r.by} asked for this` : "Looking for it", "looking"];
      return null; // known but never downloaded: can still be asked for
    }
    if (r.have) return [`In your library · ${r.have} of ${r.total} episodes`, "ready"];
    return [r.by ? `${r.by} asked for this` : "Asked for", "looking"];
  }

  // Short labels for a search result: In library or Requested, then where it's streaming.
  badges(r) {
    const out = [];
    const status = this.resultStatus(r);
    if (status) out.push(h("span", { class: `pill small ${status[1]}` }, status[1] === "ready" ? "In library" : "Requested"));
    const on = r.streaming || [];
    if (on.length) out.push(h("span", { class: "pill small streaming" }, on.length > 2 ? `On ${on[0]} +${on.length - 1}` : `On ${on.join(" and ")}`));
    return out;
  }

  getSheet(r) {
    const choices = [
      ["latest", "Latest season", "The newest season, plus new episodes as they air."],
      ["start", "From the start", "Every episode, from season 1. Uses the most space."],
      ["new", "New episodes only", "Nothing now; just episodes that air from today."],
    ];
    let choice = "latest";
    const status = this.resultStatus(r);
    const blocked = (status && !(r.kind === "movie" && !r.hasFile && !r.monitored)) || r.noTvdb;
    const sub = [r.year, r.kind === "movie" ? "Movie" : "TV show", r.kind === "show" && r.seasons ? `${r.seasons} season${r.seasons === 1 ? "" : "s"}` : null,
      r.kind === "movie" && r.runtime ? `${Math.floor(r.runtime / 60)} hr ${r.runtime % 60} min` : null].filter(Boolean).join(" · ");
    const sheet = this.openSheet(() => {
      const low = store.snap?.status?.storage;
      const btn = h("button", { class: "btn", onclick: async () => {
        btn.disabled = true; btn.textContent = "Asking…";
        try {
          const res = await api("/api/get", { method: "POST", body: { kind: r.kind, ext: r.ext, choice } });
          lists.drop("library");
          if (this.searchResults) r.inLibrary = true, r.libId = res.id, r.monitored = true, r.by = store.me.name;
          this.showResults();
          this.pending = { kind: r.kind, id: res.id, title: r.title, year: r.year, art: r.art, by: store.me.name, asked: new Date().toISOString(),
            step: 0, label: "Searching…", detail: "Checking the search sites for a good copy.", steps: ["Asked", "Found", "Downloading", "Getting it ready", "Ready to watch"] };
          if (res.already) { sheet.dialog.close(); this.toast(`${r.title} is already in your library.`); return; }
          this.journey(r.kind, res.id);
        } catch (e) { this.toast(e.message); btn.disabled = false; btn.textContent = "Get it"; }
      } }, "Get it");
      return [
        this.head(r.art, r.title, sub, status && blocked ? [status[0], status[1]] : null),
        r.overview ? h("p", { class: "overview" }, r.overview) : null,
        scoreLine(r.ratings) ? h("p", { class: "scores" }, scoreLine(r.ratings)) : null,
        this.opinionButtons(r),
        this.providersBlock(r),
        !blocked && r.kind === "show" ? h("fieldset", { class: "choices" }, h("legend", {}, "What to get"),
          h("div", { class: "list" }, choices.map(([value, label, hint]) => h("label", { class: "row" },
            h("input", { type: "radio", name: "choice", value, checked: value === choice, onchange: () => { choice = value; } }),
            h("div", { class: "text" }, h("div", { class: "title" }, label), h("div", { class: "sub wrap" }, hint)),
            h("span", { class: "tick", "aria-hidden": "true" }, icon("check")))))) : null,
        !blocked && low?.low ? h("div", { class: "banner" }, `Storage is getting full: ${fmt.gb(low.free)} left. It will still try, but it may stop at 25 GB.`) : null,
        blocked && r.kind === "show" && status?.[1] === "ready" && Number.isInteger(r.libId) ? this.seasonsBlock(r.libId) : null,
        r.noTvdb ? h("p", { class: "footnote" }, "This show can't be added automatically: the TV listings service doesn't know it yet.")
          : blocked ? h("p", { class: "footnote" }, status?.[1] === "looking" ? "It's already been asked for. Follow it on Home."
            : r.kind === "show" ? "It's already in your library. New episodes arrive by themselves." : "It's already in your library.") : h("div", { class: "actions" }, btn),
      ];
    });
  }
};
