"""The client's source as one string: the app is split into modules by screen, and tests that look for
a piece of code should not care which module it lives in."""

from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def client_source(without=()):
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(WEB.glob("*.js")) if p.name != "sw.js" and p.name not in without)
