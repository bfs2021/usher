"""Reduce Motion and Reduce Transparency reach every animation, transition and blur in web/*.css, and bars go solid
with Reduce Transparency, with Increase Contrast and where the browser has no blur.

The rule as the stylesheets keep it today: app.css, linked first on every page (tests/test_stylesheets.py), turns
motion and blur off for every element in one `prefers-reduced-motion: reduce` block and one
`prefers-reduced-transparency: reduce` block, with `!important`. So a stylesheet may animate, transition or blur
freely, as long as app.css keeps those blocks and no other declaration of these properties uses `!important`, which
would win over them. Motion started from JavaScript checks the setting itself (web/shell.js, web/sheetdrag.js).
"""

import re
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
EVERY = "*, *::before, *::after"
VIEW_TRANSITIONS = "::view-transition-group(*), ::view-transition-old(*), ::view-transition-new(*) { animation: none !important; }"
SETTINGS = {
    "Reduce Motion": ("prefers-reduced-motion: reduce", ("animation: none !important", "transition: none !important")),
    "Reduce Transparency": (
        "prefers-reduced-transparency: reduce",
        ("-webkit-backdrop-filter: none !important", "backdrop-filter: none !important"),
    ),
}
DECLARATION = re.compile(r"(?:^|[{;])[ \t]*((?:-webkit-)?(animation|transition|backdrop-filter)[\w-]*)[ \t]*:[ \t]*([^;}]*)", re.M)


def without_comments(css):
    """The stylesheet with its comments blanked out, keeping line numbers."""
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), css, flags=re.S)


def media_blocks(css, query):
    """(start, end, inside) of each @media block whose condition names `query`."""
    blocks = []
    for m in re.finditer(r"@media[^{]*\(" + re.escape(query) + r"\)[^{]*\{", css):
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        blocks.append((m.start(), i, css[m.end() : i - 1]))
    return blocks


def problems(sheets):
    """Messages for each animation, transition or blur in `sheets` ({file name: css}) that a setting can't turn off."""
    sheets = {name: without_comments(css) for name, css in sheets.items()}
    app = sheets.get("app.css", "")
    resets, found = [], []
    for setting, (query, needed) in SETTINGS.items():
        blocks = media_blocks(app, query)
        resets += [(start, end) for start, end, _ in blocks]
        transitions = setting == "Reduce Motion" and any("::view-transition" in css for css in sheets.values())
        if not any(EVERY in inside and all(n in inside for n in needed) for _, _, inside in blocks):
            found.append((setting, f"app.css has no @media ({query}) block setting {' and '.join(needed)} on {EVERY}"))
        elif transitions and not any(VIEW_TRANSITIONS in inside for _, _, inside in blocks):
            # The view transition pseudo-elements don't match *, so they need their own line.
            found.append((setting, f"app.css's @media ({query}) block doesn't end view transitions: {VIEW_TRANSITIONS}"))
    used = set()
    for name, css in sorted(sheets.items()):
        for m in DECLARATION.finditer(css):
            if name == "app.css" and any(start <= m.start() < end for start, end in resets):
                continue
            prop, kind, value = m.group(1), m.group(2), m.group(3)
            setting = "Reduce Transparency" if kind == "backdrop-filter" else "Reduce Motion"
            used.add(setting)
            if "!important" in value and value.split("!")[0].strip() != "none":
                line = css.count("\n", 0, m.start(1)) + 1
                found.append((setting, f"{name}:{line}: {prop} with !important wins over {setting}"))
    return [message for setting, message in found if setting in used]


def stylesheets():
    return {path.name: path.read_text(encoding="utf-8") for path in sorted(WEB.glob("*.css"))}


class StylesheetTests(unittest.TestCase):
    def test_every_animation_transition_and_blur_can_be_turned_off(self):
        self.assertEqual(problems(stylesheets()), [])

    def test_the_stylesheets_do_animate_and_blur(self):
        # Otherwise the rule above would pass by checking nothing.
        text = "".join(stylesheets().values())
        for prop in ("animation:", "transition:", "backdrop-filter:"):
            self.assertIn(prop, text)


def supports_blocks(css, condition):
    """The inside of each @supports block whose condition contains `condition`."""
    blocks = []
    for m in re.finditer(r"@supports([^{]*)\{", css):
        if condition not in m.group(1):
            continue
        depth, i = 1, m.end()
        while depth and i < len(css):
            depth += {"{": 1, "}": -1}.get(css[i], 0)
            i += 1
        blocks.append(css[m.end() : i - 1])
    return blocks


SOLID_BAR = re.compile(r":root\s*\{[^}]*--bar:\s*var\(--bg-elevated\)")


class SolidBarTests(unittest.TestCase):
    """Bars go solid wherever their blur would be missing or unwanted."""

    def setUp(self):
        self.app = without_comments(stylesheets()["app.css"])

    def test_increase_contrast_makes_the_bars_solid_without_blur(self):
        # Safari reports Increase Contrast but not Reduce Transparency, so this is how an iPhone gets solid bars.
        blocks = [inside for _, _, inside in media_blocks(self.app, "prefers-contrast: more") if EVERY in inside]
        self.assertTrue(blocks, "app.css has no Increase Contrast block that turns the blur off")
        for needed in SETTINGS["Reduce Transparency"][1]:
            self.assertIn(needed, blocks[0])
        self.assertRegex(blocks[0], SOLID_BAR)

    def test_reduce_transparency_makes_the_bars_solid(self):
        _, _, inside = media_blocks(self.app, "prefers-reduced-transparency: reduce")[0]
        self.assertRegex(inside, SOLID_BAR)

    def test_a_browser_without_blur_gets_solid_bars(self):
        blocks = supports_blocks(self.app, "not")
        self.assertTrue(any(SOLID_BAR.search(b) for b in blocks), "no @supports not block makes the bars solid")


class RuleTests(unittest.TestCase):
    """The rule itself: it fails on each broken case and passes the good ones."""

    def setUp(self):
        self.sheets = stylesheets()
        self.app = self.sheets["app.css"]

    def without_block(self, query):
        start, end, _ = media_blocks(without_comments(self.app), query)[0]
        return without_comments(self.app)[:start] + without_comments(self.app)[end:]

    def test_blur_without_a_reduce_transparency_block_fails(self):
        found = problems({**self.sheets, "app.css": self.without_block("prefers-reduced-transparency: reduce")})
        self.assertEqual(len(found), 1)
        self.assertIn("prefers-reduced-transparency: reduce", found[0])

    def test_motion_without_a_reduce_motion_block_fails(self):
        found = problems({**self.sheets, "app.css": self.without_block("prefers-reduced-motion: reduce")})
        self.assertEqual(len(found), 1)
        self.assertIn("prefers-reduced-motion: reduce", found[0])

    def test_a_reset_that_leaves_transitions_running_fails(self):
        app = self.app.replace("transition: none !important; ", "")
        self.assertEqual(len(problems({**self.sheets, "app.css": app})), 1)

    def test_a_reset_that_only_some_elements_get_fails(self):
        app = self.app.replace("*, *::before, *::after { -webkit-backdrop-filter", ".tabbar { -webkit-backdrop-filter")
        self.assertEqual(len(problems({**self.sheets, "app.css": app})), 1)

    def test_view_transitions_need_their_own_reset(self):
        app = self.app.replace(VIEW_TRANSITIONS, "")
        found = problems({**self.sheets, "app.css": app})
        self.assertEqual(len(found), 1)
        self.assertIn("view transitions", found[0])

    def test_important_motion_or_blur_elsewhere_fails_and_names_the_line(self):
        for css in (
            ".spin {\n  animation: spin 1s linear infinite !important;\n}\n",
            ".fade {\n  transition-duration: 2s !important;\n}\n",
            ".glass {\n  backdrop-filter: blur(8px) !important;\n}\n",
        ):
            found = problems({**self.sheets, "extra.css": css})
            self.assertEqual(len(found), 1, css)
            self.assertIn("extra.css:2:", found[0])
        self.assertEqual(problems({**self.sheets, "extra.css": ".still { animation: none !important; }"}), [])

    def test_a_stylesheet_may_animate_and_blur_when_the_resets_exist(self):
        css = "/* a comment: animation: x !important; */\n.x { animation: pop 0.2s; transition: opacity 0.3s; backdrop-filter: blur(4px); }\n"
        self.assertEqual(problems({**self.sheets, "extra.css": css}), [])

    def test_nothing_to_turn_off_needs_no_reset(self):
        self.assertEqual(problems({"app.css": "body { color: black; }", "plain.css": ".a { opacity: 0.5; }"}), [])
        found = problems({"app.css": "body { color: black; }", "glass.css": ".a { backdrop-filter: blur(2px); }"})
        self.assertEqual(len(found), 1)
        self.assertIn("prefers-reduced-transparency", found[0])

    def test_selectors_and_keyframe_names_are_not_declarations(self):
        css = ".animation:hover { color: red; }\n::view-transition-old(root) { opacity: 1; }\n@keyframes transition-in { from { opacity: 0; } }\n"
        self.assertEqual([m.group(1) for m in DECLARATION.finditer(css)], [])


if __name__ == "__main__":
    unittest.main()
