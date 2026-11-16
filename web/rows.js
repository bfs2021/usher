// Usher — the one place list rows and shelf cards are made, so every screen looks and reads the same.
import { h, icon } from "./dom.js";
import { poster } from "./posters.js";

const joined = (...parts) => parts.filter(Boolean).join(", ");

// A list row: small poster, title, subtitle, then (optionally) `middle` (a progress bar), a state line and
// badges. It ends in a chevron, a `meta` text on the right (even an empty one), or your own `trailing` element.
// With `onclick` the row is a button that screen readers read as title, subtitle and state, unless `label` says otherwise.
export function titleRow({ art, title, subtitle, state, stateClass = "", middle, badges, meta, trailing, onclick, label, key }) {
  const text = h("div", { class: "text" },
    h("div", { class: "title" }, title),
    subtitle ? h("div", { class: "sub" }, subtitle) : null,
    middle,
    state ? h("div", { class: `state${stateClass ? ` ${stateClass}` : ""}` }, state) : null,
    badges?.length ? h("div", { class: "badges" }, badges) : null);
  const end = trailing ?? (meta === undefined ? h("span", { class: "chev" }, icon("chevron")) : h("span", { class: "meta" }, meta));
  const props = { class: "row has-thumb", "data-key": key };
  if (!onclick) return h("div", props, poster(art, title, "thumb"), text, end);
  return h("button", { ...props, onclick, "aria-label": label ?? joined(title, subtitle, typeof state === "string" ? state : null) },
    poster(art, title, "thumb"), text, end);
}

// A shelf card: a poster (with `overlay` marks on it), the title and a subtitle underneath.
export function titleCard({ art, title, subtitle, overlay, fallback, onclick, label, attrs = {} }) {
  return h("button", { class: "card", role: "listitem", ...attrs, onclick, "aria-label": label },
    h("div", { class: "art" }, poster(art, title, "poster", fallback), overlay),
    h("div", { class: "title" }, title),
    h("div", { class: "sub" }, subtitle));
}
