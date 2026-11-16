// The page's building blocks: line icons and h(), the one way elements are made. Split from app.js to keep it
// within its size limit; nothing here knows about Usher's data.

const ICONS = {
  home: '<path d="M4 11.5 12 5l8 6.5V20a1 1 0 0 1-1 1h-4.5v-6h-5v6H5a1 1 0 0 1-1-1z"/>',
  downloads: '<path d="M12 4v11M7.5 10.5 12 15l4.5-4.5"/><path d="M5 19h14"/>',
  upcoming: '<rect x="4" y="5.5" width="16" height="15" rx="2.5"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/><path d="M8 14h2M11 14h2M14 14h2M8 17h2M11 17h2"/>',
  library: '<rect x="4" y="4" width="7" height="7" rx="1.6"/><rect x="13" y="4" width="7" height="7" rx="1.6"/><rect x="4" y="13" width="7" height="7" rx="1.6"/><rect x="13" y="13" width="7" height="7" rx="1.6"/>',
  status: '<path d="M3 12h4l2.5-6 5 12 2.5-6h4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6"/><path d="m15 15 5 5"/>',
  close: '<path d="m6 6 12 12M18 6 6 18"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  warning: '<path d="M12 4 3 19.5h18z"/><path d="M12 10v4.5M12 17.2v.1"/>',
  popcorn: '<path d="M6 9h12l-1.5 11h-9z"/><path d="M8.5 9a2.5 2.5 0 1 1 2-4 2.5 2.5 0 0 1 4 0 2.5 2.5 0 1 1 1.5 4"/><path d="M10 9.5 10.6 20M14 9.5 13.4 20"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  clock: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>',
  more: '<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>',
  wifi: '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.5 16a5 5 0 0 1 7 0"/><circle cx="12" cy="19.2" r="1"/>',
  like: '<path d="M7.5 10.5V20H5a1.5 1.5 0 0 1-1.5-1.5V12A1.5 1.5 0 0 1 5 10.5z"/><path d="M7.5 10.5 11 3.8a1.9 1.9 0 0 1 3.4 1.5L13.5 9h5.1a2 2 0 0 1 2 2.4l-1.3 6.9a2 2 0 0 1-2 1.7H7.5z"/>',
  dislike: '<g transform="rotate(180 12 12)"><path d="M7.5 10.5V20H5a1.5 1.5 0 0 1-1.5-1.5V12A1.5 1.5 0 0 1 5 10.5z"/><path d="M7.5 10.5 11 3.8a1.9 1.9 0 0 1 3.4 1.5L13.5 9h5.1a2 2 0 0 1 2 2.4l-1.3 6.9a2 2 0 0 1-2 1.7H7.5z"/></g>',
  person: '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5c.8-4 3.8-6 7.5-6s6.7 2 7.5 6"/>',
};

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML = ICONS[name]; // constant strings defined above, never data
  return svg;
}

// h("div", {class: "row", onclick: fn, style: {"--p": "40%"}}, child, "text") — text is always text, never HTML.
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") for (const [p, val] of Object.entries(v)) el.style.setProperty(p, val);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
