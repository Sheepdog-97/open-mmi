#!/usr/bin/env python3
"""Vendor the exact reviewed Bootstrap stylesheet used by Open MMI.

This is a maintainer tool, not a runtime downloader. Normal dashboard rendering must
never depend on this network request. The downloaded bytes are accepted only when
they match Bootstrap's published SHA-384 Subresource Integrity value for v5.3.8.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "ui" / "web_dashboard" / "static" / "vendor" / "bootstrap-5.3.8.min.css"
SOURCE_URL = "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css"
EXPECTED_SHA384_BASE64 = "sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"
MAX_BYTES = 400_000


def sri_sha384(data: bytes) -> str:
    return base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")


def verify_bytes(data: bytes) -> None:
    if not data:
        raise ValueError("Bootstrap stylesheet is empty")
    if len(data) > MAX_BYTES:
        raise ValueError(f"Bootstrap stylesheet exceeds {MAX_BYTES} bytes")
    digest = sri_sha384(data)
    if digest != EXPECTED_SHA384_BASE64:
        raise ValueError(
            "Bootstrap stylesheet SHA-384 does not match the reviewed v5.3.8 asset: "
            f"expected {EXPECTED_SHA384_BASE64}, got {digest}"
        )
    if b"Bootstrap  v5.3.8" not in data[:512] and b"Bootstrap v5.3.8" not in data[:512]:
        raise ValueError("Bootstrap stylesheet does not identify itself as v5.3.8")


def verify_existing(path: Path = DESTINATION) -> None:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"Vendored Bootstrap stylesheet is missing or unreadable: {path}: {exc}") from exc
    try:
        verify_bytes(data)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Verified vendored Bootstrap 5.3.8: sha384-{EXPECTED_SHA384_BASE64}")


def download() -> bytes:
    request = Request(
        SOURCE_URL,
        headers={"User-Agent": "Open-MMI-maintainer-vendor/1"},
        method="GET",
    )
    with urlopen(request, timeout=20) as response:  # nosec B310 - fixed HTTPS URL above
        final_url = response.geturl()
        if not final_url.startswith("https://cdn.jsdelivr.net/"):
            raise ValueError(f"Unexpected Bootstrap download redirect: {final_url}")
        data = response.read(MAX_BYTES + 1)
    verify_bytes(data)
    return data


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, 0o644)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed local asset without performing any network access",
    )
    args = parser.parse_args()

    if args.check:
        verify_existing()
        return

    data = download()
    atomic_write(DESTINATION, data)
    verify_existing()
    print(f"Vendored {SOURCE_URL} -> {DESTINATION.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
