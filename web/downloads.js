// Usher — the Downloads screen: everything from asked to ready to watch, with live progress.
import { store } from "./data.js";
import { titleRow } from "./rows.js";
import { h, icon } from "./dom.js";
import { fmt, prefs } from "./util.js";

const SEEN = "downloads.seen";

export const Downloads = (Base) => class extends Base {
  // Requests made in Usher come first, each with its whole journey; then downloads nobody asked for here (new episodes
  // of followed shows). A title someone asked for is listed once, as the request.
  downloadItems() {
    const s = store.snap || {};
    const requests = s.requests || [];
    const asked = new Set(requests.filter((r) => r.step < 4).map((r) => `${r.kind}:${r.id}`));
    return { requests, downloading: (s.downloading || []).filter((d) => !asked.has(`${d.kind}:${d.id}`)) };
  }

  // What is worth a look, one key per thing and what has happened to it: a request or download that is on its way,
  // one that finished, one with a problem. Moving along (progress, the next step) keeps the same key, so it is not news.
  downloadsNews() {
    const { requests, downloading } = this.downloadItems();
    return [
      ...requests.map((r) => `req:${r.rid}:${r.problem ? "problem" : r.step >= 4 ? "done" : "on its way"}`),
      ...downloading.map((d) => `dl:${d.key}:${d.state === "problem" ? "problem" : "on its way"}`),
    ];
  }

  // What the tab's badge counts: what is new since this device last had the screen open, like Home's. Titles are
  // often left downloading for days, so counting everything on its way would keep the badge up for good.
  downloadsUnseen() {
    this.downloadsSeen ??= new Set((() => { try { return JSON.parse(prefs.get(SEEN, "[]")); } catch { return []; } })());
    return this.downloadsNews().filter((key) => !this.downloadsSeen.has(key)).length;
  }

  // Called while the screen is open. Keeps only what is still listed, so the saved set doesn't grow. Nothing is
  // forgotten before the first list arrives.
  markDownloadsSeen() {
    if (!store.snap || store.snap.loading) return;
    this.downloadsSeen = new Set(this.downloadsNews());
    prefs.set(SEEN, JSON.stringify([...this.downloadsSeen]));
    this.updateBadges();
  }

  // What the screen looks like, minus the numbers that move. If this is unchanged, only the numbers are updated.
  downloadsShape() {
    const { requests, downloading } = this.downloadItems();
    return JSON.stringify([
      requests.map((r) => [r.rid, r.step, r.problem, r.done, r.canRetry]),
      downloading.map((d) => [d.key, d.state, d.reason]),
      store.online,
    ]);
  }

  liveDownloads() {
    this.markDownloadsSeen();
    const shape = this.downloadsShape();
    if (shape !== this.lastDownloadsShape || !this.main.querySelector(".content")) {
      this.lastDownloadsShape = shape;
      return this.render(false);
    }
    const { requests, downloading } = this.downloadItems();
    for (const r of requests) {
      const el = this.main.querySelector(`[data-key="req-${r.rid}"]`);
      if (!el) continue;
      el.querySelector(".state").textContent = this.requestLine(r);
      const now = el.querySelector(".dots i.now");
      if (now && r.progress !== null && r.progress !== undefined) now.style.setProperty("--p", `${Math.floor(r.progress * 100)}%`);
    }
    for (const d of downloading) {
      const el = this.main.querySelector(`[data-key="dl-${d.key}"]`);
      if (!el) continue;
      el.querySelector(".state").textContent = this.downloadLine(d)[0];
      el.querySelector(".progress > i")?.style.setProperty("--p", `${Math.floor(d.progress * 100)}%`);
    }
  }

  downloads() {
    this.lastDownloadsShape = this.downloadsShape();
    const wrap = h("div", { class: "content" }, this.offlineBanner());
    this.markDownloadsSeen();
    const s = store.snap;
    if (!s || s.loading) return wrap.appendChild(h("p", { class: "empty" }, "Checking what's downloading…")), wrap;

    const problems = (s.downloading || []).filter((d) => d.state === "problem");
    const noRoom = problems.filter((d) => d.reason === "space");
    if (noRoom.length) {
      wrap.append(h("div", { class: "banner", role: "status" },
        `The server is out of room, so ${noRoom.length === 1 ? `${noRoom[0].title} is` : `${noRoom.length} downloads are`} waiting to go into the library. ` +
        "They finish by themselves once space is freed."));
    }
    const others = problems.filter((d) => d.reason !== "space");
    if (others.length) {
      wrap.append(h("div", { class: "banner", role: "status" },
        others.length === 1 ? `${others[0].title} has a download problem. Tap it to see what happened.` : `${others.length} downloads have a problem. Tap one to see what happened.`));
    }

    const { requests, downloading } = this.downloadItems();
    if (!requests.length && !downloading.length) {
      wrap.append(h("div", { class: "empty" }, icon("downloads"), h("h3", {}, "Nothing downloading"),
        h("p", {}, "What you ask for shows up here, from asking to ready to watch."),
        h("p", {}, h("button", { class: "btn", style: { "margin-top": "16px", width: "auto" }, onclick: () => this.go("/search") }, "Find something to watch"))));
      return wrap;
    }
    wrap.append(h("div", { class: "list", "aria-label": "Downloads" },
      [...requests.map((r) => this.requestRow(r)), ...downloading.map((d) => this.downloadRow(d))]));
    return wrap;
  }

  requestLine(r) {
    if (r.wrongTitle) return `${r.label} · may be the wrong ${r.kind === "movie" ? "movie" : "show"}`;
    return r.label + (r.eta !== null && r.eta !== undefined && r.step === 2 ? ` · ${fmt.eta(r.eta)} left` : "");
  }

  // One request, from "Asked" to "Ready to watch", as a row on Home.
  requestRow(r) {
    const complete = r.step === 4 && !r.problem;
    const cls = complete ? "ok" : r.problem && r.canRetry !== false ? "problem" : r.problem || r.wrongTitle ? "stuck" : "";
    const pct = r.progress !== null && r.progress !== undefined ? Math.floor(r.progress * 100) : null;
    return h("div", { class: "swipe", "data-key": `req-${r.rid}` },
      titleRow({ art: r.art, title: r.title, subtitle: `${r.by} asked ${fmt.ago(r.asked)}`,
        onclick: () => this.journey(r.kind, r.id, r.rid), label: `${r.title}: ${r.label}`,
        middle: h("div", { class: `dots${complete ? " complete" : ""}`, "aria-hidden": "true" }, r.steps.map((_, i) => h("i", {
          class: complete || i < r.step ? "done" : i === r.step ? (r.problem && r.canRetry !== false ? "now bad" : "now") : "",
          style: !complete && i === r.step && pct !== null ? { "--p": `${pct}%` } : null }))),
        state: this.requestLine(r), stateClass: cls }));
  }

  downloadLine(d) {
    const pct = Math.floor(d.progress * 100);
    return {
      downloading: d.wrongTitle ? [`${pct}% · may be the wrong ${d.kind === "movie" ? "movie" : "show"}`, "stuck"]
        : d.racing ? [`${pct}% · trying a better copy`, ""] : d.stalled ? [`${pct}% · waiting for someone to share it`, ""]
        : [`${pct}%${d.eta !== null && d.eta !== undefined ? ` · ${fmt.eta(d.eta)} left` : ""}`, ""],
      preparing: ["Getting it ready", ""],
      waiting: ["Waiting to start", ""],
      problem: [d.message, d.canRetry === false ? "stuck" : "problem"],
    }[d.state];
  }

  downloadRow(d) {
    const pct = Math.floor(d.progress * 100);
    const line = this.downloadLine(d);
    const bar = d.state === "downloading" || d.state === "preparing"
      ? h("div", { class: "progress", role: "progressbar", "aria-valuenow": pct, "aria-valuemin": 0, "aria-valuemax": 100 }, h("i", { style: { "--p": `${pct}%` } }))
      : null;
    return titleRow({ art: d.art, title: d.title, subtitle: d.subtitle, key: `dl-${d.key}`, onclick: () => this.details(d, "downloading"),
      middle: bar, state: line[0], stateClass: line[1] });
  }
};
