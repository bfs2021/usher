// Usher — search filters, kept per device.
import { prefs } from "./util.js";
// ---- search filters: kept per device ---------------------------------------------------------
const THIS_YEAR = new Date().getFullYear();
export const filters = {
  state: (() => { try { return JSON.parse(prefs.get("search.filters", "{}")) || {}; } catch { return {}; } })(),
  save() { prefs.set("search.filters", JSON.stringify(this.state)); },
  active(keys) { return keys.some((k) => this.state[k]); },
  clear() { this.state = {}; this.save(); },
  score: (r) => r.ratings?.imdb ?? r.ratings?.tmdb ?? null,
  match(r, keys) {
    const f = this.state;
    if (keys.includes("kind") && f.kind && r.kind !== f.kind) return false;
    if (keys.includes("score") && f.score && !((this.score(r) ?? -1) >= Number(f.score))) return false;
    if (keys.includes("when") && f.when) {
      const y = r.year;
      if (!y) return false;
      if (f.when === "old" ? y > THIS_YEAR - 10 : y < THIS_YEAR - Number(f.when) + 1) return false;
    }
    if (keys.includes("stream") && f.stream) {
      if (!Array.isArray(r.streaming)) return false;          // TMDb didn't answer in time: unknown
      if (f.stream === "none") return !r.streaming.length;
      if (f.stream === "any") return r.streaming.length > 0;
      return r.streaming.includes(f.stream.slice(4));
    }
    return true;
  },
  sort(list) {
    const by = this.state.sort;
    if (by === "score") return [...list].sort((a, b) => (this.score(b) ?? -1) - (this.score(a) ?? -1));
    if (by === "new") return [...list].sort((a, b) => (b.year || 0) - (a.year || 0));
    return list;
  },
};
