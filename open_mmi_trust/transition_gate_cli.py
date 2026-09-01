"""Local owner acknowledgement surface for Trust Transition Gate v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from ui import update_coordinator

from .accepted_state import DEFAULT_ACCEPTED_STATE_PATH
from .transition_gate import (
    DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    TRANSITION_EXPANSION,
    TransitionGateError,
    _authorize_prepared_expansion,
    evaluate_prepared_candidate,
    transition_authorization_digest,
)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise TransitionGateError("trust transition acknowledgement requires root")


def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TransitionGateError(
            "trust transition acknowledgement requires an interactive local terminal"
        )


def _prepared_transition():
    state = update_coordinator.read_state(update_coordinator.DEFAULT_STATE_FILE)
    if state["state"] != "prepared" or state["stage"] != "prepared":
        raise TransitionGateError("no prepared update candidate is available")
    try:
        stage = update_coordinator.trusted_prepared_stage(
            state, update_coordinator.DEFAULT_STAGING_ROOT
        )
    except update_coordinator.CoordinatorError as exc:
        raise TransitionGateError(str(exc)) from exc
    return stage, state, evaluate_prepared_candidate(
        stage,
        transaction_id=state["transaction_id"],
        candidate_commit=state["candidate_commit"],
        accepted_state_path=DEFAULT_ACCEPTED_STATE_PATH,
        authorization_path=DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-transition",
        description="Local owner control for Open MMI Trust Transition Gate v1",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    status = subparsers.add_parser(
        "status", help="show trust comparison for the exact prepared candidate"
    )
    status.set_defaults(handler=_cmd_status)
    acknowledge = subparsers.add_parser(
        "acknowledge",
        help="acknowledge one exact prepared trust-boundary expansion",
    )
    acknowledge.set_defaults(handler=_cmd_acknowledge)
    return parser


def _cmd_status(args: argparse.Namespace) -> int:
    del args
    _require_root()
    _, _, transition = _prepared_transition()
    print(json.dumps(transition.summary(), indent=2, sort_keys=True))
    return 0 if transition.allowed else 3


def _cmd_acknowledge(args: argparse.Namespace) -> int:
    del args
    _require_root()
    stage, state, transition = _prepared_transition()
    if transition.relation != TRANSITION_EXPANSION:
        print(json.dumps(transition.summary(), indent=2, sort_keys=True))
        if transition.allowed:
            print("Prepared candidate does not require owner expansion acknowledgement.")
            return 0
        raise TransitionGateError("prepared candidate cannot be acknowledged")
    if transition.acknowledged:
        print(json.dumps(transition.summary(), indent=2, sort_keys=True))
        return 0

    print("Prepared trust-boundary expansion:")
    print(json.dumps(transition.summary(), indent=2, sort_keys=True))
    print(
        "Candidate code has not been executed. This acknowledgement is bound to the exact "
        "prepared transaction, commit, candidate manifest, and current accepted state."
    )
    _require_local_tty()
    manifest_suffix = transition.candidate_manifest_digest.split(":", 1)[1][:12]
    commit_suffix = transition.candidate_commit[:12]
    expected = f"AUTHORIZE TRANSITION {manifest_suffix} {commit_suffix}"
    value = input(f"Type {expected} to authorize this exact trust-boundary expansion: ")
    if value != expected:
        raise TransitionGateError(f"confirmation must be exactly {expected}")

    authorization = _authorize_prepared_expansion(
        stage,
        transaction_id=state["transaction_id"],
        candidate_commit=state["candidate_commit"],
        expected_candidate_manifest_digest=transition.candidate_manifest_digest,
        expected_accepted_state_digest=transition.accepted_state_digest,
        accepted_state_path=DEFAULT_ACCEPTED_STATE_PATH,
        authorization_path=DEFAULT_TRANSITION_AUTHORIZATION_PATH,
    )
    _, _, verified = _prepared_transition()
    print(
        json.dumps(
            {
                "authorized": True,
                "authorization_digest": transition_authorization_digest(authorization),
                "transition": verified.summary(),
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
    except (TransitionGateError, update_coordinator.CoordinatorError) as exc:
        print(f"open-mmi-trust-transition: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
