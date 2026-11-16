// Usher — fetching ahead: what is likely next (the other tabs' lists, artwork in the rows on screen and the next
// screenful, a Discover title's details), only while the device is idle and never on a constrained connection.

// The person asked to save data (Low Data Mode, Data Saver), or the connection is too slow to spare.
export function constrained() {
  const c = typeof navigator === "undefined" ? null : navigator.connection;
  return !!c && (c.saveData === true || /(^|-)2g$/.test(c.effectiveType || ""));
}

// fn runs once the device has nothing better to do; never when data is to be saved. Safari has no requestIdleCallback,
// so there it waits a moment after the screen is drawn.
export function whenIdle(fn) {
  if (constrained()) return;
  if (typeof requestIdleCallback === "function") requestIdleCallback(() => fn(), { timeout: 3000 });
  else setTimeout(fn, 400);
}

// fn runs once, when most of el has been on screen (not just near it), then when the device is idle.
let seen = null;
export function whenSeen(el, fn) {
  if (constrained() || typeof IntersectionObserver !== "function") return;
  seen ??= new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      seen.unobserve(e.target);
      const run = e.target._seen;
      e.target._seen = null;
      whenIdle(run);
    }
  }, { threshold: 0.6 });
  el._seen = fn;
  seen.observe(el);
}
