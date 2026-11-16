// Usher — a small "…" menu: a button that opens a short list of links. Follows docs/interface.md: large touch
// targets, an opaque panel (so Reduce Transparency needs nothing), no motion under Reduce Motion (menu.css).
import { h, icon } from "./dom.js";

// items: [{ label, href, external }]. `label` names the button for VoiceOver ("More ways to open").
export function menu(label, items) {
  let open = false;
  const close = (refocus) => {
    if (!open) return;
    open = false;
    list.hidden = true; button.setAttribute("aria-expanded", "false");
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", keys, true);
    if (refocus) button.focus();
  };
  const outside = (e) => { if (!wrap.isConnected || !wrap.contains(e.target)) close(false); };
  const keys = (e) => {
    if (!wrap.isConnected) return close(false);
    if (e.key === "Escape") { e.preventDefault(); return close(true); }
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    e.preventDefault();
    const links = [...list.children], at = links.indexOf(document.activeElement);
    links[(at + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length].focus();
  };
  const toggle = () => {
    if (open) return close(false);
    open = true;
    list.hidden = false; button.setAttribute("aria-expanded", "true");
    document.addEventListener("pointerdown", outside, true);
    document.addEventListener("keydown", keys, true);
    list.firstElementChild?.focus();
  };
  const list = h("div", { class: "menu-list", role: "menu", "aria-label": label, hidden: true },
    items.map((it) => h("a", { class: "menu-item", role: "menuitem", href: it.href, onclick: () => close(false),
      target: it.external ? "_blank" : null, rel: it.external ? "noopener noreferrer" : null }, it.label)));
  const button = h("button", { type: "button", class: "btn plain menu-btn", "aria-label": label, "aria-haspopup": "menu", "aria-expanded": "false", onclick: toggle }, icon("more"));
  const wrap = h("div", { class: "menu" }, button, list);
  return wrap;
}
