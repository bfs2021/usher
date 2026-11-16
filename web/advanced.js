// Usher — Settings → Advanced: what only the person running the server needs (the last update result). Closed until opened.
import { SignedOut, api } from "./data.js";
import { h } from "./dom.js";
import { fill, fmt } from "./util.js";

export const Advanced = (Base) => class extends Base {
  advancedSection() {
    const label = h("span", { class: "count" }, this.advancedOpen ? "Hide" : "Show");
    const body = h("div", {});
    const section = h("details", { class: "section", open: !!this.advancedOpen },
      h("summary", { class: "section-head" }, h("h2", {}, "Advanced"), label), body);
    section.addEventListener("toggle", () => {
      this.advancedOpen = section.open;
      label.textContent = section.open ? "Hide" : "Show";
      if (section.open) this.fillAdvanced(body);
    });
    if (this.advancedOpen) this.fillAdvanced(body);
    return section;
  }

  async fillAdvanced(body) {
    if (!body.firstChild) body.append(h("p", { class: "footnote" }, "Checking…"));
    let data;
    try { data = await api("/api/updates"); }
    catch (e) { if (e instanceof SignedOut) return this.signIn(); fill(body, h("p", { class: "footnote" }, e.message)); return; }
    const last = data.last;
    fill(body, last ? h("div", { class: "list" },
      h("div", { class: "row" }, h("span", { class: `dot${last.result === "built" ? "" : " warn"}`, "aria-hidden": "true" }),
        h("div", { class: "text" }, h("div", { class: "title" }, "Last update"),
          h("div", { class: "sub wrap" }, `${last.result === "built" ? `Updated to ${last.version}` : "Failed, previous version restored"} · ${fmt.ago(last.at * 1000)}`)),
        h("span", {}))) : h("p", { class: "footnote" }, "No update has run yet."));
  }
};
