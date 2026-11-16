// Usher — what Watch does on a title that is already in Jellyfin: one main way in, the other in a menu.
// Swiftfin 1.6 registers swiftfin:// (jellyfin:// only on its main branch): swiftfin://<server>/<user>/item/<item>.
export const OPEN_IN = "open.in"; // per-device preference: "app" (default) or "web"

// `app` is {server, user} or null, `web` the media server's address or null. Returns the main way in, the other
// one for the menu (null when there is only one) and whether the app is the main one (it needs the note).
export function watchLinks(app, web, id, openIn = "app") {
  const viaApp = app ? { href: `swiftfin://${app.server}/${app.user}/item/${id}`, label: "Open in the Jellyfin app", app: true } : null;
  const viaWeb = web ? { href: `${web}/web/#/details?id=${id}`, label: "Open in browser", app: false } : null;
  const [first, second] = openIn === "web" ? [viaWeb, viaApp] : [viaApp, viaWeb];
  return { main: first || second, other: first ? second : null };
}
