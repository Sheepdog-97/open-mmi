#!/usr/bin/env python3
"""Validate and fingerprint the checked Open MMI Trust Manifest."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from open_mmi_trust import DEFAULT_MANIFEST_PATH, load_manifest, manifest_digest


def main() -> None:
    manifest = load_manifest(DEFAULT_MANIFEST_PATH)
    print(
        "Verified Open MMI Trust Manifest "
        f"generation {manifest['policy_generation']}: {manifest_digest(manifest)}"
    )


if __name__ == "__main__":
    main()
