// Usher — the Upcoming and Library screens.
import { lists } from "./data.js";
import { titleRow, titleCard } from "./rows.js";
import { h, icon } from "./dom.js";
import { clearButton, fill, fmt, prefs, scoreLine } from "./util.js";

export const UpcomingLibrary = (Base) => class extends Base {
  // ---- Upcoming ----------------------------------------------------------------------------
  upcoming() {
    const view = prefs.get("up.view", "mine");
    const seg = (value, label) => h("button", { "aria-pressed": String(view === value), onclick: () => { prefs.set("up.view", value); this.render(false); } }, label);
    const wrap = h("div", { class: "content" }, this.offlineBanner(),
      h("div", { class: "toolbar" }, h("div", { class: "segmented", role: "group", "aria-label": "Show" }, seg("mine", "Your shows"), seg("all", "All upcoming"))));
    if (view === "all") return this.upcomingAll(wrap);
    const mine = lists.show("upcoming");
    if (!mine) { wrap.append(h("p", { class: "empty" }, "Loading the next two weeks…")); return wrap; }
    const days = new Map();
    for (const it of mine.items) {
      const label = fmt.dayLabel(it.when);
      if (!days.has(label)) days.set(label, []);
      days.get(label).push(it);
    }
    if (!days.size) {
      wrap.append(h("div", { class: "empty" }, icon("upcoming"), h("h3", {}, "Nothing due"),
        h("p", {}, "None of your shows have new episodes in the next two weeks.")));
    }
    for (const [label, items] of days) {
      wrap.append(h("section", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, label)),
        h("div", { class: "list" }, items.map((it) => titleRow({ art: it.art, title: it.title, subtitle: it.subtitle, onclick: () => this.details(it, "upcoming"),
          state: it.here ? "Already here" : null, meta: it.kind === "show" ? fmt.time(it.when) : "" })))));
    }
    wrap.append(h("p", { class: "footnote" }, "Times are when episodes air. They usually arrive here within a few hours."));
    return wrap;
  }

  // Every English-language show premiering or starting a new season in the next two months (TMDb), with Get it.
  upcomingAll(wrap) {
    const d = lists.show("upcomingAll");
    if (!d) { wrap.append(h("p", { class: "empty" }, "Loading upcoming shows…")); return wrap; }
    if (!d.enabled) {
      wrap.append(h("div", { class: "empty" }, icon("upcoming"), h("h3", {}, "Not set up"), h("p", {}, "This needs Usher's TMDb key, the same as Discover.")));
      return wrap;
    }
    const f = (() => { try { return JSON.parse(prefs.get("up.filters", "{}")) || {}; } catch { return {}; } })();
    const services = [...new Set(d.items.flatMap((c) => c.streaming || []))].sort();
    const pick = (key, label, opts) => h("select", { class: `sort filter${f[key] ? " on" : ""}`, "aria-label": label,
      onchange: (e) => { if (e.target.value) f[key] = e.target.value; else delete f[key]; prefs.set("up.filters", JSON.stringify(f)); this.render(false); } },
    opts.map(([v, text]) => h("option", { value: v, selected: (f[key] || "") === v }, text)));
    const shown = d.items.filter((c) => (!f.type || (f.type === "new") === c.new)
      && (!f.stream || (f.stream === "any" ? c.streaming.length > 0 : f.stream === "none" ? !c.streaming.length : c.streaming.includes(f.stream.slice(4)))));
    wrap.append(h("div", { class: "filters", role: "group", "aria-label": "Filters" },
      pick("type", "Type", [["", "New shows and seasons"], ["new", "New shows"], ["season", "New seasons"]]),
      pick("stream", "Streaming", [["", "Streaming or not"], ["any", "On a service"], ["none", "Not streaming"],
        ...[...new Set([...services, ...(f.stream?.startsWith("svc:") ? [f.stream.slice(4)] : [])])].map((n) => [`svc:${n}`, `On ${n}`])])));
    if (!shown.length) {
      wrap.append(h("p", { class: "empty" }, d.items.length ? "Nothing here matches these filters." : "TMDb has no upcoming shows listed just now."));
    }
    const weeks = new Map();
    for (const c of shown) {
      const label = fmt.weekLabel(c.when);
      if (!weeks.has(label)) weeks.set(label, []);
      weeks.get(label).push(c);
    }
    for (const [label, items] of weeks) {
      wrap.append(h("section", { class: "section" },
        h("div", { class: "section-head" }, h("h2", {}, label)),
        h("div", { class: "list" }, items.map((c) => {
          const badges = this.badges({ kind: "show", inLibrary: c.have || c.asked, have: c.have ? 1 : 0, streaming: c.streaming });
          return titleRow({ art: c.art, title: c.title, badges, meta: fmt.shortDate(c.when), onclick: () => this.discoverSheet(c),
            subtitle: [c.new ? "New show" : `Season ${c.season}`, c.network, ...(c.genres || []).slice(0, 2), scoreLine(c.ratings, true)].filter(Boolean).join(" · ") });
        }))));
    }
    wrap.append(h("p", { class: "footnote attribution" }, `English-language shows starting in the next ${Math.round(d.days / 30)} months, from TMDB (not endorsed or certified by TMDB). Streaming availability by JustWatch. Shows you already follow are under Your shows.`));
    return wrap;
  }

  // ---- Library -----------------------------------------------------------------------------
  library() {
    const wrap = h("div", { class: "content" }, this.offlineBanner());
    const lib = lists.show("library");    // a copy over two minutes old is shown while a new one is fetched
    if (!lib) { wrap.append(h("p", { class: "empty" }, "Loading your library…")); return wrap; }

    // Only what's actually here to watch: a movie with its file, a show with at least one episode. Titles still
    // wanted, stopped part-way or removed stay out (they're on Home while Usher is getting them).
    const here = { movies: lib.movies.filter((m) => m.state === "ready"), shows: lib.shows.filter((x) => x.have > 0) };
    const kind = prefs.get("lib.kind", "shows");
    const sort = prefs.get("lib.sort", "added");
    const grid = h("div", { class: "grid", role: "list" });
    const fill = () => {
      const q = (this.libQuery || "").trim().toLowerCase();
      let items = here[kind].filter((it) => !q || it.title.toLowerCase().includes(q));
      items = items.slice().sort(sort === "title" ? (a, b) => a.sort.localeCompare(b.sort) : (a, b) => (b.added || "").localeCompare(a.added || ""));
      grid.replaceChildren(...items.map((it) => titleCard({ art: it.art, title: it.title, onclick: () => this.details(it, "library"),
        subtitle: it.kind === "movie" ? ({ ready: it.year, looking: "Looking for it", not_out: "Not out yet", not_wanted: "Not downloaded" }[it.state] ?? it.year)
          : `${it.have} of ${it.total} episodes` })));
      if (!items.length) grid.replaceChildren(h("p", { class: "empty" }, q ? `Nothing in your ${kind === "movies" ? "movies" : "TV shows"} matches “${this.libQuery}”.` : "Nothing here yet."));
    };
    const seg = (value, label) => h("button", { "aria-pressed": String(kind === value), onclick: () => { prefs.set("lib.kind", value); this.render(false); } }, label);
    const select = h("select", { class: "sort", "aria-label": "Sort by", onchange: (e) => { prefs.set("lib.sort", e.target.value); fill(); } },
      h("option", { value: "added", selected: sort === "added" }, "Recently added"),
      h("option", { value: "title", selected: sort === "title" }, "Title"));
    const input = h("input", { type: "search", placeholder: kind === "movies" ? "Filter movies" : "Filter TV shows", "aria-label": "Filter", value: this.libQuery || "", autocomplete: "off",
      oninput: (e) => { this.libQuery = e.target.value; fill(); } });
    wrap.append(h("div", { class: "toolbar" },
      h("div", { class: "segmented", role: "group", "aria-label": "Show" }, seg("shows", `TV shows · ${here.shows.length}`), seg("movies", `Movies · ${here.movies.length}`)),
      h("label", { class: "search" }, icon("search"), input, clearButton(input, () => { this.libQuery = ""; fill(); })), select), grid);
    fill();
    return wrap;
  }
};
