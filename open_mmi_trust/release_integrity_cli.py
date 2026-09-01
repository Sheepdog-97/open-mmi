"""Local owner CLI for Installed Release/File Integrity v1."""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any, Sequence

from .accepted_state import (
    DEFAULT_ACCEPTED_STATE_PATH,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    accepted_state_digest,
    compare_trust_manifests,
)
from .lineage import DEFAULT_TRANSITION_LINEAGE_DIR
from .release_integrity import (
    DEFAULT_INTEGRITY_STATE_PATH,
    DEFAULT_SOURCE_DESCRIPTOR,
    ReleaseIntegrityError,
    _git,
    _record_integrity_state,
    current_trust_anchors,
    default_install_root,
    default_package_root,
    expected_release_from_git,
    integrity_state_digest,
    read_integrity_state,
    validate_integrity_state,
    verify_installed_runtime,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise ReleaseIntegrityError("installed release integrity operations require root")


def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ReleaseIntegrityError(
            "installed release integrity bootstrap requires an interactive local terminal"
        )


def _source_from_descriptor(path: Path = DEFAULT_SOURCE_DESCRIPTOR) -> tuple[Path, str]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseIntegrityError("managed update source descriptor is unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise ReleaseIntegrityError("managed update source descriptor is untrusted")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseIntegrityError("managed update source descriptor is invalid") from exc
    expected = {
        "schema_version", "channel", "repository_path", "branch", "upstream",
        "installed_commit", "installed_version",
    }
    if not isinstance(payload, dict) or set(payload) != expected or payload.get("schema_version") != 1:
        raise ReleaseIntegrityError("managed update source descriptor schema is invalid")
    repository = Path(str(payload.get("repository_path") or ""))
    commit = str(payload.get("installed_commit") or "").lower()
    if not repository.is_absolute():
        raise ReleaseIntegrityError("managed update repository path is invalid")
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ReleaseIntegrityError("managed installed commit is invalid")
    return repository, commit


def _bootstrap_source(install_root: Path) -> tuple[Path, str]:
    if (install_root / ".git").is_dir():
        dirty = _git(install_root, "status", "--porcelain", text=True)
        if dirty.returncode != 0 or str(dirty.stdout).strip():
            raise ReleaseIntegrityError(
                "editable Open MMI checkout is not clean; commit the exact runtime bytes before integrity bootstrap"
            )
        head = _git(install_root, "rev-parse", "HEAD", text=True)
        commit = str(head.stdout).strip().lower() if head.returncode == 0 else ""
        if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
            raise ReleaseIntegrityError("editable Open MMI checkout HEAD is invalid")
        return install_root, commit
    return _source_from_descriptor()


def _proposed_state(
    *,
    repository: Path,
    commit: str,
    install_root: Path,
    accepted_state_path: Path,
    lineage_path: Path,
    package_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    accepted, lineage_head = current_trust_anchors(accepted_state_path, lineage_path)
    expected = expected_release_from_git(repository, commit)
    comparison = compare_trust_manifests(accepted["manifest"], expected["trust_manifest"])
    if comparison["relation"] in {TRANSITION_EXPANSION, TRANSITION_GENERATION_REGRESSION}:
        raise ReleaseIntegrityError(
            "active release Trust Manifest exceeds or regresses Accepted Owner Trust State"
        )
    payload = {
        "schema_version": 1,
        "state_id": "org.open-mmi.installed-release-integrity",
        "recorded_at": "2000-01-01T00:00:00+00:00",
        "record_source": "baseline-existing-state",
        "candidate_commit": expected["candidate_commit"],
        "trust_manifest": expected["trust_manifest"],
        "trust_manifest_digest": expected["trust_manifest_digest"],
        "inventory": expected["inventory"],
        "inventory_digest": expected["inventory_digest"],
        "accepted_state_digest_at_recording": accepted_state_digest(accepted),
        "lineage_head_record_digest_at_recording": lineage_head,
    }
    proposed = validate_integrity_state(payload)
    verification = verify_installed_runtime(proposed, install_root, package_root)
    if not verification["matches"]:
        raise ReleaseIntegrityError(
            "active Open MMI runtime does not match the exact committed runtime inventory"
        )
    return proposed, verification


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    _require_root()
    install_root = default_install_root()
    package_root = default_package_root()
    state = read_integrity_state(DEFAULT_INTEGRITY_STATE_PATH)
    if state is None:
        print(json.dumps({
            "established": False,
            "state": "not-established",
            "install_root": str(install_root),
        }, indent=2, sort_keys=True))
        return 3
    verification = verify_installed_runtime(state, install_root, package_root)
    print(json.dumps({
        "established": True,
        "state": "established" if verification["matches"] else "mismatch",
        "recorded_at": state["recorded_at"],
        "record_source": state["record_source"],
        "candidate_commit": state["candidate_commit"],
        "trust_manifest_digest": state["trust_manifest_digest"],
        "inventory_digest": state["inventory_digest"],
        "integrity_state_digest": integrity_state_digest(state),
        "verification": verification,
        "provenance": "separate-release-provenance-state",
    }, indent=2, sort_keys=True))
    return 0 if verification["matches"] else 4


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    del args
    _require_root()
    if read_integrity_state(DEFAULT_INTEGRITY_STATE_PATH) is not None:
        raise ReleaseIntegrityError("Installed Release/File Integrity is already established")
    install_root = default_install_root()
    package_root = default_package_root()
    repository, commit = _bootstrap_source(install_root)
    proposed, verification = _proposed_state(
        repository=repository,
        commit=commit,
        install_root=install_root,
        accepted_state_path=DEFAULT_ACCEPTED_STATE_PATH,
        lineage_path=DEFAULT_TRANSITION_LINEAGE_DIR,
        package_root=package_root,
    )
    print("Current installed runtime will become the File Integrity v1 baseline:")
    print(json.dumps({
        "candidate_commit": proposed["candidate_commit"],
        "trust_manifest_digest": proposed["trust_manifest_digest"],
        "inventory_digest": proposed["inventory_digest"],
        "files": verification["files_expected"],
        "history_before_baseline": "unverified",
        "release_signer_provenance": "not-established-by-integrity-bootstrap",
    }, indent=2, sort_keys=True))
    print(
        "This proves byte identity to this exact committed Git tree from this point forward; "
        "it does not establish an independent release-signing identity."
    )
    _require_local_tty()
    suffix = proposed["inventory_digest"].split(":", 1)[1][:12]
    expected = f"ESTABLISH INTEGRITY {suffix}"
    value = input(f"Type {expected} to establish this exact installed runtime baseline: ")
    if value != expected:
        raise ReleaseIntegrityError(f"confirmation must be exactly {expected}")

    accepted, lineage_head = current_trust_anchors(
        DEFAULT_ACCEPTED_STATE_PATH, DEFAULT_TRANSITION_LINEAGE_DIR
    )
    # Re-derive and re-verify after confirmation so the prompt cannot authorize
    # bytes that changed while the owner was reviewing the summary.
    repository_now, commit_now = _bootstrap_source(install_root)
    if repository_now != repository or commit_now != commit:
        raise ReleaseIntegrityError("installed release identity changed during bootstrap confirmation")
    current = expected_release_from_git(repository_now, commit_now)
    if current["inventory_digest"] != proposed["inventory_digest"]:
        raise ReleaseIntegrityError("installed release inventory changed during bootstrap confirmation")
    state = _record_integrity_state(
        candidate_commit=current["candidate_commit"],
        trust_manifest=current["trust_manifest"],
        inventory=current["inventory"],
        accepted_state=accepted,
        lineage_head_record_digest=lineage_head,
        record_source="baseline-existing-state",
        path=DEFAULT_INTEGRITY_STATE_PATH,
    )
    final_verification = verify_installed_runtime(state, install_root, package_root)
    if not final_verification["matches"]:
        raise ReleaseIntegrityError("installed runtime changed while integrity baseline was being recorded")
    print(json.dumps({
        "established": True,
        "recorded_at": state["recorded_at"],
        "candidate_commit": state["candidate_commit"],
        "inventory_digest": state["inventory_digest"],
        "integrity_state_digest": integrity_state_digest(state),
        "files_verified": final_verification["files_expected"],
        "release_signer_provenance": "not-established-by-integrity-bootstrap",
    }, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-integrity",
        description="Local owner control for Open MMI Installed Release/File Integrity v1",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="verify active runtime against recorded integrity")
    status.set_defaults(handler=_cmd_status)
    bootstrap = subparsers.add_parser(
        "bootstrap", help="establish integrity for the exact clean installed/committed runtime"
    )
    bootstrap.set_defaults(handler=_cmd_bootstrap)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except ReleaseIntegrityError as exc:
        print(f"open-mmi-trust-integrity: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
