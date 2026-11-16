// Usher — the Search screen.
import { SignedOut, api } from "./data.js";
import { titleRow } from "./rows.js";
import { h, icon } from "./dom.js";
import { clearButton, scoreLine } from "./util.js";
import { filters } from "./filters.js";

export const Search = (Base) => class extends Base {
  // ---- Search --------------------------------------------------------------------------------
  searchScreen() {
    const results = h("div", { class: "results" });
    this.searchResultsEl = results;
    const input = h("input", { type: "search", placeholder: "Movies and TV shows", "aria-label": "Search for a movie or TV show",
      value: this.searchQuery || "", autocomplete: "off", autocorrect: "off", spellcheck: "false", enterkeyhint: "search",
      oninput: (e) => { this.searchQuery = e.target.value; clearTimeout(this.searchTimer); this.searchTimer = setTimeout(() => this.runSearch(), 275); },
      onkeydown: (e) => { if (e.key === "Enter") { clearTimeout(this.searchTimer); this.runSearch(); input.blur(); } } });
    const wrap = h("div", { class: "content" }, this.offlineBanner(),
      h("div", { class: "toolbar" }, h("label", { class: "search big" }, icon("search"), input,
        clearButton(input, () => { this.searchQuery = ""; clearTimeout(this.searchTimer); this.searchSeq = (this.searchSeq || 0) + 1; this.searching = false; this.searchResults = null; this.showResults(); }))), results);
    this.showResults();
    if (matchMedia("(hover: hover)").matches) queueMicrotask(() => input.focus());
    return wrap;
  }

  async runSearch() {
    const q = (this.searchQuery || "").trim();
    if (q.length < 2) { this.searchResults = null; return this.showResults(); }
    const seq = (this.searchSeq = (this.searchSeq || 0) + 1);
    this.searching = true; this.showResults();
    // Each source is asked on its own and shown as it answers: titles already in the library at once, then TV
    // and movies. `c` names this tab, so the server skips lookups for a search that's been replaced.
    this.searchTab ||= Math.random().toString(36).slice(2, 12);
    const rows = new Map(), failed = [];
    const publish = () => {
      const ql = q.toLowerCase(), t = (r) => r.title.toLowerCase();
      const results = [...rows.values()].sort((a, b) => (t(a) !== ql) - (t(b) !== ql)
        || !t(a).startsWith(ql) - !t(b).startsWith(ql) || a.rank - b.rank).slice(0, 24);
      this.searchResults = { q, results, failed: [...failed] };
      this.showResults();
    };
    const parts = { library: null, show: "TV", movie: "movies" };
    const asked = Object.entries(parts).map(([part, name]) =>
      api(`/api/search?q=${encodeURIComponent(q)}&extras=0&part=${part}&c=${this.searchTab}`).then((data) => {
        if (seq !== this.searchSeq || data.superseded) return;
        for (const r of data.results) {   // one row per title: a lookup's row replaces the library's
          const key = `${r.kind}:${r.ext}`, had = rows.get(key);
          if (!had || r.rank < had.rank) rows.set(key, r);
        }
        failed.push(...(data.failed || []));
        publish();
      }).catch((e) => { if (name && !(e instanceof SignedOut)) failed.push(name); throw e; }));
    const done = await Promise.allSettled(asked);
    if (done.some((d) => d.reason instanceof SignedOut)) return this.signIn();
    if (seq !== this.searchSeq) return;
    this.searching = false;
    if (done.every((d) => d.status === "rejected")) {
      this.searchResults = { results: [], error: done[0].reason.message };
      return this.showResults();
    }
    publish();
    // Then streaming badges, TMDb scores and missing posters a moment later, since TMDb is the slow part.
    const data = this.searchResults;
    const ids = [...new Set(data.results.filter((r) => r.tmdb).map((r) => `${r.kind}:${r.tmdb}`))];
    if (!ids.length) return;
    const extra = await api(`/api/search/extras?ids=${encodeURIComponent(ids.join(","))}`).catch(() => null);
    if (seq !== this.searchSeq || !extra) return;
    const by = new Map(extra.items.map((x) => [`${x.kind}:${x.tmdb}`, x]));
    for (const r of data.results) {
      const x = by.get(`${r.kind}:${r.tmdb}`);
      if (!x) continue;
      if (Array.isArray(x.streaming)) Object.assign(r, { streaming: x.streaming, providers: x.providers, rentable: x.rentable });
      if (!r.ratings?.imdb && x.ratings?.tmdb) r.ratings = { ...(r.ratings || {}), tmdb: x.ratings.tmdb };
      if (!r.art && x.art) r.art = x.art;
    }
    this.showResults();
  }

  showResults() {
    const el = this.searchResultsEl;
    if (!el) return;
    const data = this.searchResults;
    const current = data && data.q === (this.searchQuery || "").trim();   // results for what's typed now
    if (this.searching && (!data || (current && !data.results.length))) return el.replaceChildren(h("p", { class: "empty" }, "Searching…"));
    if (!data) return this.showDiscover(el);
    if (data.error) return el.replaceChildren(h("p", { class: "empty" }, data.error));
    if (!data.results.length) return el.replaceChildren(h("p", { class: "empty" }, `Nothing found for “${this.searchQuery.trim()}”. Check the spelling, or try fewer words.`));
    const keys = ["kind", "score", "when", "stream"];
    const shown = filters.sort(data.results.filter((r) => filters.match(r, keys)));
    const services = [...new Set(data.results.flatMap((r) => r.streaming || []))].sort();
    el.replaceChildren(...[
      this.filterBar([...keys, "sort"], services, () => this.showResults()),
      data.failed?.length ? h("div", { class: "banner" }, `Couldn't search ${data.failed.join(" or ")} just now. These results may be incomplete.`) : null,
      filters.active(keys) ? this.filterCount(shown.length, data.results.length, () => this.showResults()) : null,
      shown.length ? null : h("p", { class: "empty" }, "Nothing here matches these filters."),
      h("div", { class: "list", style: { opacity: this.searching && !current ? "0.6" : "1" } }, shown.map((r) => {
        const badges = this.badges(r);
        return titleRow({ art: r.art, title: r.title, badges, onclick: () => this.getSheet(r),
          subtitle: [r.year, r.kind === "movie" ? "Movie" : "TV show", r.kind === "show" ? r.network : null, scoreLine(r.ratings, true)].filter(Boolean).join(" · ") });
      })),
      this.searching && current ? h("p", { class: "footnote" }, "Searching…") : null].filter(Boolean));
  }
};
