"""Local owner CLI for Accepted Owner Trust State v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Mapping, Sequence

from .accepted_state import (
    DEFAULT_ACCEPTED_STATE_PATH,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    TRANSITION_NARROWER,
    AcceptedTrustStateError,
    _record_accepted_manifest,
    accepted_state_digest,
    compare_trust_manifests,
    read_accepted_state,
)
from .manifest import DEFAULT_MANIFEST_PATH, ManifestError, load_manifest, manifest_digest
from .lineage import (
    ACK_ACCEPTED_STATE,
    DECISION_LOCAL_OWNER,
    DEFAULT_TRANSITION_LINEAGE_DIR,
    SOURCE_ACCEPTED_STATE_CLI,
    TransitionLineageError,
    _record_lineage_baseline,
    _record_state_transition,
    require_lineage_current,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise AcceptedTrustStateError("accepted owner trust state operations require root")


def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise AcceptedTrustStateError(
            "accepted owner trust state changes require an interactive local terminal"
        )


def _manifest_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "policy_generation": manifest["policy_generation"],
        "manifest_digest": "sha256:" + manifest_digest(manifest),
        "capabilities": manifest["capabilities"],
    }


def _status_payload(state: dict[str, Any] | None, current: Mapping[str, Any]) -> dict[str, Any]:
    if state is None:
        return {
            "established": False,
            "state": "not-established",
            "current_manifest": _manifest_summary(current),
        }
    comparison = compare_trust_manifests(state["manifest"], current)
    return {
        "established": True,
        "state": "established",
        "accepted_at": state["accepted_at"],
        "accepted_state_digest": accepted_state_digest(state),
        "accepted_manifest": _manifest_summary(state["manifest"]),
        "current_manifest": _manifest_summary(current),
        "current_vs_accepted": comparison,
    }


def _confirm_current(manifest: Mapping[str, Any], *, action: str) -> None:
    suffix = manifest_digest(manifest)[:12]
    if action == "narrow":
        expected = f"NARROW {suffix}"
        prompt = "Type {value} to surrender the previously accepted authority: "
    elif action == "bootstrap":
        expected = f"ACCEPT CURRENT {suffix}"
        prompt = "Type {value} to bootstrap this exact installed trust boundary: "
    elif action == "refresh":
        expected = f"ACCEPT CURRENT {suffix}"
        prompt = "Type {value} to refresh accepted state to this equivalent installed boundary: "
    else:
        raise AcceptedTrustStateError("invalid accepted-state confirmation action")
    value = input(prompt.format(value=expected))
    if value != expected:
        raise AcceptedTrustStateError(f"confirmation must be exactly {expected}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-state",
        description="Local owner control for Open MMI Accepted Owner Trust State v1",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show accepted owner trust state and current relation")
    status.set_defaults(handler=_cmd_status)

    accept = subparsers.add_parser(
        "accept-current",
        help="bootstrap or narrow accepted state to the exact currently installed manifest",
    )
    accept.set_defaults(handler=_cmd_accept_current)
    return parser


def _load_current_manifest() -> dict[str, Any]:
    try:
        return load_manifest(DEFAULT_MANIFEST_PATH)
    except ManifestError as exc:
        raise AcceptedTrustStateError(f"installed Trust Manifest is invalid: {exc}") from exc


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    _require_root()
    current = _load_current_manifest()
    state = read_accepted_state(DEFAULT_ACCEPTED_STATE_PATH)
    print(json.dumps(_status_payload(state, current), indent=2, sort_keys=True))
    return 0


def _cmd_accept_current(args: argparse.Namespace) -> int:
    del args
    _require_root()
    current = _load_current_manifest()
    existing = read_accepted_state(DEFAULT_ACCEPTED_STATE_PATH)

    if existing is None:
        print("Current installed trust boundary to bootstrap:")
        print(json.dumps(_manifest_summary(current), indent=2, sort_keys=True))
        print("This records owner acceptance; it does not prove release provenance or file integrity.")
        _require_local_tty()
        _confirm_current(current, action="bootstrap")
    else:
        comparison = compare_trust_manifests(existing["manifest"], current)
        if comparison["accepted_manifest_digest"] == comparison["candidate_manifest_digest"]:
            print(json.dumps(_status_payload(existing, current), indent=2, sort_keys=True))
            return 0
        if comparison["relation"] == TRANSITION_GENERATION_REGRESSION:
            raise AcceptedTrustStateError(
                "current manifest generation regresses accepted state; v1 refuses downgrade acceptance"
            )
        if comparison["relation"] == TRANSITION_EXPANSION:
            raise AcceptedTrustStateError(
                "current installed boundary exceeds accepted owner trust; v1 cannot broaden state after installation. Expansion must be acknowledged by the old-trusted-side transition gate before installation."
            )
        try:
            require_lineage_current(existing, DEFAULT_TRANSITION_LINEAGE_DIR)
        except TransitionLineageError as exc:
            raise AcceptedTrustStateError(
                f"transition lineage must anchor accepted state before changing it: {exc}"
            ) from exc

        print("Current installed boundary does not exceed accepted owner trust:")
        print(json.dumps(comparison, indent=2, sort_keys=True))
        _require_local_tty()
        _confirm_current(
            current,
            action=(
                "narrow"
                if comparison["relation"] == TRANSITION_NARROWER
                else "refresh"
            ),
        )

    state = _record_accepted_manifest(current, DEFAULT_ACCEPTED_STATE_PATH)
    try:
        if existing is None:
            _record_lineage_baseline(state, DEFAULT_TRANSITION_LINEAGE_DIR)
        else:
            _record_state_transition(
                existing,
                state,
                source=SOURCE_ACCEPTED_STATE_CLI,
                decision=DECISION_LOCAL_OWNER,
                acknowledgement_required=True,
                acknowledgement_method=ACK_ACCEPTED_STATE,
                path=DEFAULT_TRANSITION_LINEAGE_DIR,
            )
    except TransitionLineageError as exc:
        raise AcceptedTrustStateError(
            f"accepted state changed but transition lineage could not be finalized: {exc}"
        ) from exc
    print(
        json.dumps(
            {
                "established": True,
                "accepted_at": state["accepted_at"],
                "accepted_manifest_digest": state["manifest_digest"],
                "accepted_state_digest": accepted_state_digest(state),
                "policy_generation": state["manifest"]["policy_generation"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except AcceptedTrustStateError as exc:
        print(f"open-mmi-trust-state: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
