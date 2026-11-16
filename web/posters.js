// Usher — the one place artwork <img> elements are made.
import { h } from "./dom.js";
import { constrained, whenIdle } from "./prefetch.js";
// ---- posters: the one place artwork <img> elements are made ----
// The tile shows the title on the fill colour until the picture has arrived *and decoded whole*; only then is
// the decoded picture swapped in as a fresh element, so WebKit never shows (or keeps) a half-drawn poster. A
// picture that fails, or decodes to nothing, is tried once more with a fresh address, then `fallback`, then the
// title stays. Pictures already shown are remembered, so a redraw of Home reuses them instead of reloading.
// Each is asked for at the width its tile is drawn (the service worker keeps it on the device, sw.js).
const art = { ok: new Map(), bad: new Map(), loading: new Map(), shown: new Map(), seen: null, waiting: new Set(), ahead: false };
const BAD_FOR = 10 * 60 * 1000; // the server's own "try again later" for a missing picture
const STEPS = [120, 160, 240, 320, 480, 640, 960]; // the widths the server makes (usher/artwork.py)
const AHEAD = 12; // pictures started ahead per idle moment

// The address at the width the tile is drawn, in device pixels, up to twice its size: beyond that the difference
// can't be seen on a poster, and it would cost over twice the data.
function sized(url, el) {
  if (!url.startsWith("/")) return url;
  const need = (el.getBoundingClientRect().width || 150) * Math.min(window.devicePixelRatio || 1, 2);
  const w = STEPS.find((step) => step >= need) || STEPS.at(-1);
  return `${url}${url.includes("?") ? "&" : "?"}w=${w}`;
}

// One picture, off the page: resolves with the <img> once it has loaded and decoded to a real size.
function decodedPicture(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = "async";
    img.alt = "";
    img.onload = () => (img.decode ? img.decode() : Promise.resolve())
      .then(() => (img.naturalWidth > 0 && img.naturalHeight > 0 ? resolve(img) : reject(new Error("empty"))), reject);
    img.onerror = reject;
    img.src = src;
  });
}

function loadPicture(url) {
  if (art.ok.has(url)) return decodedPicture(art.ok.get(url));
  if ((art.bad.get(url) || 0) > Date.now()) return Promise.reject(new Error("no picture"));
  if (art.loading.has(url)) return art.loading.get(url);
  const once = decodedPicture;
  const p = once(url)
    .catch(() => once(url + (url.includes("?") ? "&" : "?") + "r=1"))
    .then((img) => { art.ok.set(url, img.currentSrc || img.src); return img; },
      (e) => { art.bad.set(url, Date.now() + BAD_FOR); throw e; })
    .finally(() => art.loading.delete(url));
  art.loading.set(url, p);
  return p;
}

function begin(el) {
  art.waiting.delete(el);
  art.seen?.unobserve(el);
  const start = el._start;
  el._start = null;
  start?.();
}

function whenNear(el, start) {
  // Load what is on screen or about to be (what loading="lazy" did), rows that scroll sideways included...
  if (!("IntersectionObserver" in window)) return start();
  art.seen = art.seen || new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) begin(e.target);
  }, { rootMargin: "400px" });
  el._start = start;
  art.seen.observe(el);
  if (constrained()) return;
  art.waiting.add(el);
  loadAhead();
}

// ...and, while the device is idle, the rest of the rows on screen and the next screenful, so they are there when
// scrolled to. A tile no longer on the page is let go.
function loadAhead() {
  if (art.ahead) return;
  art.ahead = true;
  whenIdle(() => {
    art.ahead = false;
    const reach = innerHeight * 2;
    let started = 0;
    for (const el of [...art.waiting]) {
      if (!el.isConnected) { art.waiting.delete(el); continue; }
      const r = el.getBoundingClientRect();
      if (r.bottom < 0 || r.top > reach) continue;
      begin(el);
      if (++started >= AHEAD) break;
    }
    if (started >= AHEAD) loadAhead(); // more to start at the next idle moment
  });
}

export function poster(src, title, cls = "poster", fallback = null) {
  const stand = h("div", { class: `${cls} poster-fallback${title ? " titled" : ""}`, "aria-hidden": "true" }, title || "?");
  const urls = [src, fallback].filter((u, i, all) => u && all.indexOf(u) === i);
  if (!urls.length) return stand; // no picture anywhere yet (common for shows not out yet): the title, not a blank
  // Already shown once: the browser holds it decoded, so show it straight away (no placeholder flash on redraws).
  const known = art.shown.get(`${cls} ${urls[0]}`);
  if (art.ok.has(known)) return h("img", { class: cls, src: art.ok.get(known), alt: "" });
  const next = (i) => {
    if (i >= urls.length) return; // nothing left: the title stays
    const want = sized(urls[i], stand);
    loadPicture(want).then((img) => {
      if (i === 0) art.shown.set(`${cls} ${urls[0]}`, want);
      // The decoded picture goes in as a new element (a copy if another tile took this one), never a half-drawn one.
      const el = img.isConnected ? img.cloneNode() : img;
      el.className = `${cls} poster-in`;
      if (stand.isConnected) stand.replaceWith(el);
    }, () => next(i + 1));
  };
  whenNear(stand, () => next(0));
  return stand;
}

// replaceChildren() turns null into the text "null"; drop empties first.
