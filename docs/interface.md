# Interface rules

How the app should look and behave. Applies to everything under `web/`.

## iOS first, lightweight first

- Every interface element takes its lead from iOS and Apple's Human Interface Guidelines: navigation, sheets, lists, buttons, motion, typography and system colours. The Home Screen app should feel like a native Apple app. When a choice comes up, pick what an iOS app would do.
- Lightweight comes first. iOS is the inspiration, never at the cost of load time, scroll smoothness, battery or compatibility on older supported devices (older iPhones, iPads and Macs included). Where an iOS-style effect isn't feasible on the web without that cost, use the most compatible and performant alternative.

## Colour

- The default palette is Apple's system colours by role: backgrounds, labels, separators, fills and the system colours. The amber accent is the only custom colour.
- Values are fixed tokens in `web/app.css`, never WebKit's system colour keywords: other browsers ignore them and they change with the OS.
- Each colour is declared once for light and once for dark, not with `light-dark()`, which needs Safari 17.5 and would drop older iPhones, iPads and Macs. Increase Contrast has its own block.
- Text needs 4.5:1 against its background in light, dark and Increase Contrast; controls and meaningful graphics need 3:1. `tests/test_contrast.py` checks every pair and names the one that fails. A pair that falls short gets a darker text variant (a lighter one in dark), as green does, never a fainter tint.
- The light separator is Apple's 29% (`rgba(60, 60, 67, 0.29)`). Separators and the tertiary label are decorative and stay below those ratios, so never use them for text or for anything that carries meaning.

## Settings people have turned on

- Respect Reduce Motion (`prefers-reduced-motion`), Reduce Transparency (`prefers-reduced-transparency`) and Increase Contrast (`prefers-contrast`): no essential meaning in motion, blur or faint colour. `web/app.css` turns every animation, transition and blur off for Reduce Motion and Reduce Transparency in one block each, which `tests/test_reduce_motion.py` checks. Safari doesn't report Reduce Transparency to web pages, so the Reduce Transparency block also covers Increase Contrast, which Safari does report: either setting gives solid bars. Motion started from JavaScript is not covered by that test; check the setting there.
- Bars, the sidebar and toasts are translucent with a plain `backdrop-filter` blur, and solid where the browser has no blur. Not Liquid Glass: its refraction needs SVG filters in `backdrop-filter`, which Safari doesn't apply, and stacked filters cost scroll smoothness and battery on older devices. Sheets stay solid.
- Respect Dynamic Type: sizes in `rem`/`em`, layouts that survive larger text.
- Keep content clear of notches and the Home indicator with `env(safe-area-inset-*)`. Size the app shell with `100dvh`, not `100vh`.

## Check before you finish

- iPhone SE, a standard iPhone, iPad and desktop widths, each in light and dark.
- A change to a screen shown in README.md redoes its pictures: `python3 tools/screenshots.py` (see [architecture.md](architecture.md)).

## Words and behaviour

- Plain language (see [CONTRIBUTING.md](../CONTRIBUTING.md)): someone who has never heard of Sonarr must be able to use it without help.
- No manual steps or switches a normal streaming app wouldn't show. Sorting, filing and similar choices happen by themselves, from the settings in `usher.toml`.
