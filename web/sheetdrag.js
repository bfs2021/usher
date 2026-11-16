// Usher — dragging a sheet down to close it, as on iOS: by the grabber (touch or mouse) or by the sheet itself while its content is scrolled to the top.
const CLOSE_FRACTION = 0.25; // dragged past a quarter of the sheet's height closes it
const CLOSE_SPEED = 0.5; // px per ms: a quick flick closes it too
const SLOP = 8; // px before a touch in the content counts as a drag rather than a tap or scroll
const SETTLE_MS = 250;

const reducedMotion = () => globalThis.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;

// `scroller` is the sheet's scrolling content: a touch there only drags the sheet when the content is at the top, so scrolling inside the sheet still works.
export function dragToClose(dialog, scroller) {
  let startY = 0;
  let startTime = 0;
  let offset = 0;
  let speed = 0;
  let lastY = 0;
  let lastTime = 0;
  let dragging = false;
  let tracking = false;

  const place = (px, animate) => {
    dialog.style.transition = animate ? `transform ${SETTLE_MS}ms cubic-bezier(0.2, 0.8, 0.2, 1)` : "none";
    dialog.style.transform = px ? `translateY(${px}px)` : "";
  };
  const begin = (y) => { startY = lastY = y; startTime = lastTime = performance.now(); offset = 0; speed = 0; tracking = true; dragging = false; };
  const move = (y) => {
    const now = performance.now();
    if (now > lastTime) speed = (y - lastY) / (now - lastTime);
    lastY = y; lastTime = now;
    offset = Math.max(0, y - startY);
    place(offset, false);
  };
  const finish = () => {
    tracking = false;
    if (!dragging) return;
    dragging = false;
    const height = dialog.getBoundingClientRect().height || 1;
    if (offset > height * CLOSE_FRACTION || speed > CLOSE_SPEED) {
      if (reducedMotion()) return dialog.close();
      place(height, true);
      // transitionend can be skipped (hidden tab, interrupted), so the timer closes it either way.
      setTimeout(() => dialog.close(), SETTLE_MS);
      return;
    }
    place(0, !reducedMotion());
  };

  // Touch: anywhere on the sheet, so long as the content is at the top and the finger moves down.
  dialog.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1) { tracking = false; return; }
    begin(e.touches[0].clientY);
  }, { passive: true });
  dialog.addEventListener("touchmove", (e) => {
    if (!tracking) return;
    const y = e.touches[0].clientY;
    if (!dragging) {
      if (y - startY < -SLOP || scroller.scrollTop > 0) { tracking = false; return; } // scrolling the content, not the sheet
      if (y - startY < SLOP) return;
      dragging = true;
    }
    e.preventDefault();
    move(y);
  }, { passive: false });
  dialog.addEventListener("touchend", finish);
  dialog.addEventListener("touchcancel", () => { offset = 0; finish(); });

  // Mouse: by the grabber only, since text in the sheet has to stay selectable.
  scroller.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse" || e.button !== 0 || !e.target.closest?.(".grabber")) return;
    begin(e.clientY);
    dragging = true;
    const onMove = (ev) => move(ev.clientY);
    const onUp = () => { removeEventListener("pointermove", onMove); removeEventListener("pointerup", onUp); finish(); };
    addEventListener("pointermove", onMove);
    addEventListener("pointerup", onUp);
    e.preventDefault();
  });
}
