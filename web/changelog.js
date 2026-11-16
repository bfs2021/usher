// Usher — Settings → About → Changelog: a pushed screen (back button, as in iOS Settings) listing every version, newest first.
import { SignedOut, api } from "./data.js";
import { h, icon } from "./dom.js";
import { fill } from "./util.js";

// One heading per version, then its changes under their kind (Added, Fixed…), in the changelog's own words.
export function versionBlocks(versions) {
  return versions.map((v) => {
    const kinds = [...new Set(v.changes.map((c) => c.kind))];
    return h("section", { class: "section changelog-version" }, h("div", { class: "section-head" }, h("h2", {}, v.version)),
      h("div", { class: "list" }, kinds.map((kind) => h("div", { class: "changelog-group" },
        kind ? h("h3", { class: "changelog-kind" }, kind) : null,
        v.changes.filter((c) => c.kind === kind).map((c) => h("p", { class: "changelog-change" }, c.text))))));
  });
}

export const Changelog = (Base) => class extends Base {
  openChangelog() {
    this.pushScreen("changelog");
  }

  // What render() draws while the Changelog is open: the title, the back button (named for the screen below) and the body.
  changelogView(backLabel) {
    const body = h("div", { class: "content" }, h("p", { class: "empty" }, "Checking…"));
    this.loadChangelog(body);
    const back = h("button", { class: "nav-back", onclick: () => this.popScreen() }, icon("chevron"), backLabel);
    return { title: "Changelog", back, body };
  }

  async loadChangelog(body) {
    try {
      const { versions } = await api("/api/changelog");
      fill(body, this.offlineBanner(), versions.length ? versionBlocks(versions) : h("p", { class: "empty" }, "No changes are listed."));
    } catch (e) {
      if (e instanceof SignedOut) return this.signIn();
      fill(body, h("p", { class: "footnote" }, e.message));
    }
  }
};
