"""Every text and control colour pair in web/app.css meets WCAG 2.2 AA in light, dark and Increase Contrast.

Text needs 4.5:1; controls and other meaningful graphics need 3:1. A failure names the pair and its ratio. Fix a short
pair with a darker text variant (a lighter one in dark), never a fainter tint. Separators and the tertiary label are
decorative (chevrons, grabbers, hairlines) and carry no meaning on their own, so they are not checked. The accent fills
labelled buttons and markers, so what must be readable is the label on it (`on-accent`), which is checked.
"""

import re
import unittest
from pathlib import Path

CSS = (Path(__file__).resolve().parent.parent / "web" / "app.css").read_text(encoding="utf-8")

TEXT = 4.5
CONTROL = 3.0

# (colour, background layers from the outside in: the last is opaque, earlier ones are translucent fills on top of it)
TEXT_PAIRS = (
    [("label", (b,)) for b in ("bg", "bg-elevated")]
    + [("label", ("fill-strong", b)) for b in ("bg", "bg-elevated")]  # initials on the account button
    + [("label-2", (f, b)) for b in ("bg", "bg-elevated") for f in (None, "fill")]
    + [("accent-text", (f, b)) for b in ("bg", "bg-elevated") for f in (None, "fill", "accent-tint")]
    + [("green", (f, b)) for b in ("bg", "bg-elevated") for f in (None, "green-tint")]
    + [("orange", (f, b)) for b in ("bg", "bg-elevated") for f in (None, "orange-tint")]
    + [("red", (f, b)) for b in ("bg", "bg-elevated") for f in (None, "fill", "red-tint")]
    + [("on-accent", ("accent",)), ("on-green", ("green",)), ("on-red", ("red",))]
)
CONTROL_PAIRS = [(c, (f, b)) for c in ("green", "orange", "red", "blue", "purple") for b in ("bg", "bg-elevated") for f in (None, "fill-strong")]


def block(opening):
    """The declarations of the :root rule that follows `opening` in the stylesheet."""
    start = CSS.index(opening)
    root = CSS.index(":root", start)
    return CSS[CSS.index("{", root) + 1 : CSS.index("}", root)]


def declarations(text):
    return dict(re.findall(r"--([\w-]+):\s*([^;]+);", text))


def palettes():
    light = declarations(block(":root {"))
    dark = {**light, **declarations(block("@media (prefers-color-scheme: dark)"))}
    more = declarations(block("@media (prefers-contrast: more)"))
    return {"light": light, "dark": dark, "light, Increase Contrast": {**light, **more}, "dark, Increase Contrast": {**dark, **more}}


def parse(value, tokens):
    """(r, g, b, alpha) of a token, following var() references."""
    value = value.strip()
    ref = re.fullmatch(r"var\(--([\w-]+)\)", value)
    if ref:
        return parse(tokens[ref.group(1)], tokens)
    if value.startswith("#"):
        return (*(int(value[i : i + 2], 16) for i in (1, 3, 5)), 1.0)
    rgba = re.fullmatch(r"rgba?\(([^)]*)\)", value)
    if rgba:
        parts = [float(x) for x in rgba.group(1).split(",")]
        return (*parts[:3], parts[3] if len(parts) > 3 else 1.0)
    raise ValueError(f"unsupported colour {value!r}")


def over(top, below):
    alpha = top[3]
    return (*(top[i] * alpha + below[i] * (1 - alpha) for i in range(3)), 1.0)


def flatten(layers, tokens):
    result = parse(tokens[layers[-1]], tokens)
    for name in reversed(layers[:-1]):
        if name:
            result = over(parse(tokens[name], tokens), result)
    return result


def luminance(rgb):
    def channel(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


class ContrastTests(unittest.TestCase):
    def check(self, pairs, minimum):
        failures = []
        for mode, tokens in palettes().items():
            for colour, layers in pairs:
                background = flatten(layers, tokens)
                # Text drawn with a translucent colour is blended onto its background first.
                foreground = over(parse(tokens[colour], tokens), background)
                got = ratio(foreground, background)
                if got < minimum:
                    names = " on ".join(n for n in layers if n)
                    failures.append(f"{colour} on {names} ({mode}): {got:.2f}:1, needs {minimum}:1")
        self.assertEqual(failures, [], "\n" + "\n".join(failures))

    def test_text_meets_aa(self):
        self.check(TEXT_PAIRS, TEXT)

    def test_controls_meet_aa(self):
        self.check(CONTROL_PAIRS, CONTROL)

    def test_every_palette_token_is_declared_for_dark_or_shared(self):
        tokens = palettes()
        for colour, layers in TEXT_PAIRS + CONTROL_PAIRS:
            for name in (colour, *[n for n in layers if n]):
                self.assertIn(name, tokens["dark"], f"--{name} is not declared in app.css")

    def test_separator_is_apples_light_value(self):
        self.assertEqual(palettes()["light"]["separator"], "rgba(60, 60, 67, 0.29)")


if __name__ == "__main__":
    unittest.main()
