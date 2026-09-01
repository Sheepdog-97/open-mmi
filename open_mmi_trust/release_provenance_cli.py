"""Local owner CLI for Release Provenance / Pinned Signer Root v1."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Sequence

from . import release_integrity
from .release_provenance import (
    DEFAULT_PROVENANCE_ROOT_PATH,
    MAX_PUBLIC_KEY_BYTES,
    ReleaseProvenanceError,
    _write_provenance_root,
    build_provenance_root,
    provenance_root_digest,
    read_provenance_root,
    verification_repository_for_integrity,
    verify_commit_provenance,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ReleaseProvenanceError("release provenance operations require root")


def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ReleaseProvenanceError(
            "release signer bootstrap requires an interactive local terminal"
        )


def _read_public_key_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseProvenanceError("release signer public key file cannot be opened safely") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > MAX_PUBLIC_KEY_BYTES
        ):
            raise ReleaseProvenanceError("release signer public key file is not a safe regular file")
        chunks: list[bytes] = []
        remaining = MAX_PUBLIC_KEY_BYTES + 1
        while remaining:
            data = os.read(descriptor, min(65536, remaining))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        value = b"".join(chunks)
    finally:
        os.close(descriptor)
    if not value or len(value) > MAX_PUBLIC_KEY_BYTES:
        raise ReleaseProvenanceError("release signer public key file is empty or too large")
    return value


def _verification_repository(install_root: Path, expected_commit: str) -> Path:
    return verification_repository_for_integrity(install_root, expected_commit)


def _current_integrity() -> tuple[dict, Path, Path, Path]:
    install_root = release_integrity.default_install_root()
    package_root = release_integrity.default_package_root()
    state = release_integrity.require_current_integrity(
        release_integrity.DEFAULT_INTEGRITY_STATE_PATH,
        install_root,
        package_root,
    )
    repository = _verification_repository(install_root, state["candidate_commit"])
    return state, install_root, package_root, repository


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    _require_root()
    root = read_provenance_root(DEFAULT_PROVENANCE_ROOT_PATH)
    if root is None:
        print(json.dumps({
            "established": False,
            "state": "not-established",
        }, indent=2, sort_keys=True))
        return 3

    result = {
        "established": True,
        "state": "established",
        "established_at": root["established_at"],
        "root_source": root["root_source"],
        "algorithm": root["algorithm"],
        "primary_fingerprint": root["primary_fingerprint"],
        "signing_fingerprints": root["signing_fingerprints"],
        "public_key_sha256": root["public_key_sha256"],
        "baseline_commit": root["baseline_commit"],
        "baseline_integrity_state_digest": root["baseline_integrity_state_digest"],
        "history_before_baseline": root["history_before_baseline"],
        "provenance_root_digest": provenance_root_digest(root),
    }
    try:
        integrity, _install_root, _package_root, repository = _current_integrity()
        verification = verify_commit_provenance(repository, integrity["candidate_commit"], root)
    except (release_integrity.ReleaseIntegrityError, ReleaseProvenanceError) as exc:
        result.update({
            "state": "verification-failed",
            "current_release_verified": False,
            "error": str(exc),
        })
        print(json.dumps(result, indent=2, sort_keys=True))
        return 4

    result.update({
        "current_release_verified": True,
        "current_commit": integrity["candidate_commit"],
        "current_integrity_state_digest": release_integrity.integrity_state_digest(integrity),
        "current_signature": verification,
    })
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    _require_root()
    if read_provenance_root(DEFAULT_PROVENANCE_ROOT_PATH) is not None:
        raise ReleaseProvenanceError("release signer root is already established")
    _require_local_tty()

    integrity, install_root, package_root, repository = _current_integrity()
    integrity_digest = release_integrity.integrity_state_digest(integrity)
    key_path = Path(args.key_file)
    key_bytes = _read_public_key_file(key_path)
    proposed = build_provenance_root(
        key_bytes=key_bytes,
        baseline_commit=integrity["candidate_commit"],
        baseline_integrity_state_digest=integrity_digest,
        established_at="2000-01-01T00:00:00+00:00",
    )
    verification = verify_commit_provenance(repository, integrity["candidate_commit"], proposed)

    print("Current accepted release signer will become the Provenance v1 trust root:")
    print(json.dumps({
        "baseline_commit": integrity["candidate_commit"],
        "baseline_integrity_state_digest": integrity_digest,
        "primary_fingerprint": proposed["primary_fingerprint"],
        "signing_fingerprints": proposed["signing_fingerprints"],
        "public_key_sha256": proposed["public_key_sha256"],
        "current_signature": verification,
        "history_before_baseline": "unverified",
        "network_key_discovery": "prohibited",
        "signer_rotation": "not-supported-in-v1",
    }, indent=2, sort_keys=True))
    print(
        "Verify the full primary fingerprint through an independent owner/auditor channel before confirming. "
        "GitHub's verified badge, repository ancestry, and this prompt are not independent signer roots."
    )
    expected = f"PIN RELEASE SIGNER {proposed['primary_fingerprint']}"
    value = input(f"Type {expected} to pin this exact release signer: ")
    if value != expected:
        raise ReleaseProvenanceError(f"confirmation must be exactly {expected}")

    # Re-read all mutable bootstrap inputs after owner confirmation.  The prompt
    # cannot authorize a key, commit, or runtime that changed while it was reviewed.
    current, install_root_now, package_root_now, repository_now = _current_integrity()
    if install_root_now != install_root or package_root_now != package_root:
        raise ReleaseProvenanceError("active Open MMI runtime roots changed during signer confirmation")
    current_integrity_digest = release_integrity.integrity_state_digest(current)
    if (
        current["candidate_commit"] != integrity["candidate_commit"]
        or current_integrity_digest != integrity_digest
        or repository_now != repository
    ):
        raise ReleaseProvenanceError("installed release identity changed during signer confirmation")
    key_bytes_now = _read_public_key_file(key_path)
    final_root = build_provenance_root(
        key_bytes=key_bytes_now,
        baseline_commit=current["candidate_commit"],
        baseline_integrity_state_digest=current_integrity_digest,
    )
    for field in ("primary_fingerprint", "signing_fingerprints", "public_key_sha256"):
        if final_root[field] != proposed[field]:
            raise ReleaseProvenanceError("release signer public key changed during confirmation")
    final_verification = verify_commit_provenance(repository_now, current["candidate_commit"], final_root)
    stored = _write_provenance_root(final_root, DEFAULT_PROVENANCE_ROOT_PATH)

    print(json.dumps({
        "established": True,
        "established_at": stored["established_at"],
        "primary_fingerprint": stored["primary_fingerprint"],
        "signing_fingerprints": stored["signing_fingerprints"],
        "public_key_sha256": stored["public_key_sha256"],
        "baseline_commit": stored["baseline_commit"],
        "baseline_integrity_state_digest": stored["baseline_integrity_state_digest"],
        "provenance_root_digest": provenance_root_digest(stored),
        "current_signature": final_verification,
        "history_before_baseline": stored["history_before_baseline"],
    }, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-provenance",
        description="Local owner control for Open MMI Release Provenance / Pinned Signer Root v1",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="verify the current integrity-bound commit with the pinned signer")
    status.set_defaults(handler=_cmd_status)
    bootstrap = subparsers.add_parser(
        "bootstrap",
        help="pin one externally reviewed OpenPGP public key as the release signer root",
    )
    bootstrap.add_argument(
        "--key-file",
        required=True,
        metavar="PATH",
        help="local regular file containing exactly one public OpenPGP key",
    )
    bootstrap.set_defaults(handler=_cmd_bootstrap)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (ReleaseProvenanceError, release_integrity.ReleaseIntegrityError) as exc:
        print(f"open-mmi-trust-provenance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
