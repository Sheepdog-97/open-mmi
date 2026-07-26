#!/usr/bin/env python3
"""Verify that dashboard CSS modules preserve the original cascade byte-for-byte."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ui" / "web_dashboard" / "static"
INDEX = STATIC / "index.html"
LEGACY_STYLESHEET = STATIC / "styles.css"
CSS_MODULES = (
    "styles-core.css",
    "styles-media-layout.css",
    "styles-shell.css",
    "styles-media-sources.css",
    "styles-diagnostics.css",
    "styles-media-final.css",
)
EXPECTED_COMBINED_SHA256 = "587e96c673ec2abaab7c99fb6f1de262cf5bc0939e6d1cd5d3809ed28ba15409"


def combined_css() -> bytes:
    return b"".join((STATIC / name).read_bytes() for name in CSS_MODULES)


def expected_legacy_manifest() -> str:
    imports = "".join(f'@import url("/{name}");\n' for name in CSS_MODULES)
    return (
        "/* Compatibility stylesheet. The dashboard loads the modules directly in index.html. */\n"
        + imports
    )


def verify() -> None:
    missing = [name for name in CSS_MODULES if not (STATIC / name).is_file()]
    if missing:
        raise SystemExit("Missing CSS modules: " + ", ".join(missing))

    digest = hashlib.sha256(combined_css()).hexdigest()
    if digest != EXPECTED_COMBINED_SHA256:
        raise SystemExit(
            "Dashboard CSS module order/content changed: "
            f"expected {EXPECTED_COMBINED_SHA256}, got {digest}"
        )

    manifest = LEGACY_STYLESHEET.read_text(encoding="utf-8")
    if manifest != expected_legacy_manifest():
        raise SystemExit("styles.css compatibility manifest is out of sync")

    html = INDEX.read_text(encoding="utf-8")
    positions = []
    for name in CSS_MODULES:
        marker = f'<link rel="stylesheet" href="/{name}">'
        if html.count(marker) != 1:
            raise SystemExit(f"Expected one stylesheet link for {name}")
        positions.append(html.index(marker))
    if positions != sorted(positions):
        raise SystemExit("Dashboard CSS modules are not loaded in cascade order")
    if '<link rel="stylesheet" href="/styles.css">' in html:
        raise SystemExit("index.html must load CSS modules directly, not styles.css")

    bootstrap = html.index("bootstrap@5.3.8/dist/css/bootstrap.min.css")
    if positions[-1] > bootstrap:
        raise SystemExit("Dashboard CSS must remain before Bootstrap to preserve the current cascade")

    print(
        f"Verified {len(CSS_MODULES)} CSS modules: "
        f"{len(combined_css())} bytes, sha256={digest}"
    )


def main() -> None:
    verify()


if __name__ == "__main__":
    main()
