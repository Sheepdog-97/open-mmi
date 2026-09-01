"""Local owner controls for Trust Transition Lineage v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from .accepted_state import (
    AcceptedTrustStateError,
    DEFAULT_ACCEPTED_STATE_PATH,
    TRANSITION_EXPANSION,
    TRANSITION_GENERATION_REGRESSION,
    accepted_state_digest,
    compare_trust_manifests,
    read_accepted_state,
)
from .lineage import (
    ACK_RECONCILE,
    DECISION_RECONCILE,
    DEFAULT_TRANSITION_LINEAGE_DIR,
    SOURCE_RECONCILE,
    TransitionLineageError,
    _record_lineage_baseline,
    _record_state_transition,
    lineage_summary,
    read_transition_lineage,
)
from .transition_gate import (
    DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    TransitionGateError,
    read_transition_authorization,
    transition_authorization_digest,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise TransitionLineageError("transition lineage operations require root")


def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TransitionLineageError(
            "transition lineage changes require an interactive local terminal"
        )


def _accepted_state():
    state = read_accepted_state(DEFAULT_ACCEPTED_STATE_PATH)
    if state is None:
        raise TransitionLineageError(
            "Accepted Owner Trust State is not established; establish it before lineage"
        )
    return state


def _status_payload() -> dict:
    accepted = _accepted_state()
    records = read_transition_lineage(DEFAULT_TRANSITION_LINEAGE_DIR)
    payload = lineage_summary(records)
    payload["current_accepted_state_digest"] = accepted_state_digest(accepted)
    payload["current_accepted_manifest_digest"] = accepted["manifest_digest"]
    if records:
        head = records[-1]
        anchored = (
            head["accepted_state_after_digest"] == accepted_state_digest(accepted)
            and head["accepted_manifest_after_digest"] == accepted["manifest_digest"]
            and head["manifest_after"] == accepted["manifest"]
        )
        payload["anchors_current_accepted_state"] = anchored
    else:
        payload["anchors_current_accepted_state"] = False
    return payload


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    _require_root()
    payload = _status_payload()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["established"] and payload["anchors_current_accepted_state"] else 3


def _cmd_bootstrap(args: argparse.Namespace) -> int:
    del args
    _require_root()
    accepted = _accepted_state()
    if read_transition_lineage(DEFAULT_TRANSITION_LINEAGE_DIR):
        raise TransitionLineageError("transition lineage is already established")
    digest = accepted_state_digest(accepted)
    print("Current Accepted Owner Trust State will become the Lineage v1 baseline:")
    print(
        json.dumps(
            {
                "accepted_state_digest": digest,
                "manifest_digest": accepted["manifest_digest"],
                "policy_generation": accepted["manifest"]["policy_generation"],
                "history_before_baseline": "unverified",
            },
            indent=2,
            sort_keys=True,
        )
    )
    print(
        "This does not retroactively prove transitions before Lineage v1; it establishes the local chain from this exact state forward."
    )
    _require_local_tty()
    expected = f"ESTABLISH LINEAGE {digest.split(':', 1)[1][:12]}"
    value = input(f"Type {expected} to establish this exact lineage baseline: ")
    if value != expected:
        raise TransitionLineageError(f"confirmation must be exactly {expected}")
    record = _record_lineage_baseline(accepted, DEFAULT_TRANSITION_LINEAGE_DIR)
    print(json.dumps(lineage_summary([record]), indent=2, sort_keys=True))
    return 0


def _matching_expansion_authorization(head: dict, accepted: dict) -> str | None:
    authorization = read_transition_authorization(DEFAULT_TRANSITION_AUTHORIZATION_PATH)
    if authorization is None:
        return None
    if (
        authorization["accepted_state_digest"] != head["accepted_state_after_digest"]
        or authorization["accepted_manifest_digest"] != head["accepted_manifest_after_digest"]
        or authorization["candidate_manifest_digest"] != accepted["manifest_digest"]
        or authorization["candidate_policy_generation"]
        != accepted["manifest"]["policy_generation"]
    ):
        return None
    return transition_authorization_digest(authorization)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    del args
    _require_root()
    accepted = _accepted_state()
    records = read_transition_lineage(DEFAULT_TRANSITION_LINEAGE_DIR)
    if not records:
        raise TransitionLineageError("transition lineage is not established; use bootstrap")
    head = records[-1]
    current_digest = accepted_state_digest(accepted)
    if (
        head["accepted_state_after_digest"] == current_digest
        and head["accepted_manifest_after_digest"] == accepted["manifest_digest"]
        and head["manifest_after"] == accepted["manifest"]
    ):
        print(json.dumps(_status_payload(), indent=2, sort_keys=True))
        return 0

    comparison = compare_trust_manifests(head["accepted_state_after"]["manifest"], accepted["manifest"])
    if comparison["relation"] == TRANSITION_GENERATION_REGRESSION:
        raise TransitionLineageError("current accepted state regresses the lineage head generation")
    authorization_digest = None
    if comparison["relation"] == TRANSITION_EXPANSION:
        authorization_digest = _matching_expansion_authorization(head, accepted)
        if authorization_digest is None:
            raise TransitionLineageError(
                "current accepted state expands the lineage head without matching transition-authorization evidence"
            )

    print("Accepted Owner Trust State is ahead of the lineage head:")
    print(json.dumps(comparison, indent=2, sort_keys=True))
    print(
        "Reconciliation appends evidence only; it does not change the current accepted authority."
    )
    _require_local_tty()
    expected = f"RECONCILE LINEAGE {current_digest.split(':', 1)[1][:12]}"
    value = input(f"Type {expected} to append reconciliation evidence: ")
    if value != expected:
        raise TransitionLineageError(f"confirmation must be exactly {expected}")
    _record_state_transition(
        head["accepted_state_after"],
        accepted,
        source=SOURCE_RECONCILE,
        decision=DECISION_RECONCILE,
        acknowledgement_required=True,
        acknowledgement_method=ACK_RECONCILE,
        authorization_digest=authorization_digest,
        path=DEFAULT_TRANSITION_LINEAGE_DIR,
    )
    print(json.dumps(_status_payload(), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-lineage",
        description="Local owner controls for Open MMI Trust Transition Lineage v1",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser("status", help="show lineage head and accepted-state anchoring")
    status.set_defaults(handler=_cmd_status)
    bootstrap = subparsers.add_parser(
        "bootstrap", help="establish a locally confirmed lineage baseline from current accepted state"
    )
    bootstrap.set_defaults(handler=_cmd_bootstrap)
    reconcile = subparsers.add_parser(
        "reconcile-current",
        help="append evidence when accepted state advanced but its lineage append did not complete",
    )
    reconcile.set_defaults(handler=_cmd_reconcile)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except (TransitionLineageError, AcceptedTrustStateError, TransitionGateError) as exc:
        print(f"open-mmi-trust-lineage: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
