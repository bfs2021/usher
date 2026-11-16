// Usher — start-up guard. A classic script, so it runs even when the app's modules fail to load. If the logo is still
// all there is after a few seconds, or a module can't be fetched, it says so and offers a reload with the saved app
// files dropped. It doesn't import anything: it must work when the rest of the app doesn't.
(() => {
  const WAIT = 10000;
  const KEEP = ["usher-art", "usher-data"];
  let shown = false;

  async function reload() {
    // Offline, dropping the saved app files would leave nothing to open: reload as it is. Artwork and saved lists stay.
    try {
      if (navigator.onLine !== false) {
        const reg = await navigator.serviceWorker?.getRegistration();
        if (reg) await Promise.race([reg.update(), new Promise((r) => setTimeout(r, 4000))]);
        if (window.caches) await Promise.all((await caches.keys()).filter((k) => !KEEP.includes(k)).map((k) => caches.delete(k)));
      }
    } catch { /* reload anyway */ }
    location.reload();
  }

  function explain() {
    const launch = document.querySelector(".launch");
    if (shown || !launch) return;
    shown = true;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "launch-reload";
    button.textContent = "Reload";
    button.addEventListener("click", () => { button.disabled = true; reload(); });
    const title = document.createElement("h3");
    title.textContent = "Usher didn't start";
    const text = document.createElement("p");
    text.textContent = "Part of the app didn't load, which can happen just after an update. Reloading fetches it again.";
    const box = document.createElement("div");
    box.className = "launch-failed";
    box.setAttribute("role", "alert");
    box.append(title, text, button);
    launch.removeAttribute("aria-hidden");
    launch.replaceChildren(box);
  }

  // A module that can't be fetched fires `error` on its script element (it doesn't bubble, so listen while it falls); one
  // that loads but doesn't fit the others (an old copy with a new one) is an uncaught error on the window itself.
  addEventListener("error", (e) => { if (!e.target?.tagName || e.target.tagName === "SCRIPT") explain(); }, true);
  setTimeout(explain, WAIT);
})();
