"""Local owner CLI for Open MMI Telemetry Guard."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .guard import (
    DEFAULT_AUTHORIZATION_PATH,
    TelemetryGuardError,
    _create_authorization,
    collection_decision,
    load_scope_file,
    read_authorization,
    _revoke_authorization,
    scope_digest,
)



def _require_root() -> None:
    if os.geteuid() != 0:
        raise TelemetryGuardError("open-mmi-telemetry owner operations require root")

def _require_local_tty() -> None:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise TelemetryGuardError(
            "telemetry authorization changes require an interactive local terminal"
        )


def _read_vin() -> str:
    return getpass.getpass("Vehicle VIN (used only for a local fingerprint; not stored): ")


def _scope_summary(scope: dict[str, Any]) -> dict[str, Any]:
    return {
        "purpose": scope["purpose"],
        "signals": scope["signals"],
        "retention": scope["retention"],
        "destination": scope["destination"],
        "scope_digest": scope_digest(scope),
    }


def _confirm_scope(scope: dict[str, Any]) -> None:
    suffix = scope_digest(scope).split(":", 1)[1][:12]
    expected = f"AUTHORIZE {suffix}"
    value = input(f"Type {expected} to bind this exact scope to this vehicle: ")
    if value != expected:
        raise TelemetryGuardError(f"confirmation must be exactly {expected}")


def _confirm_revoke() -> None:
    value = input("Type REVOKE to remove telemetry authorization: ")
    if value != "REVOKE":
        raise TelemetryGuardError("confirmation must be exactly REVOKE")


def _redacted_status(authorization: dict[str, Any] | None) -> dict[str, Any]:
    if authorization is None:
        return {"authorized": False}
    return {
        "authorized": True,
        "authorized_at": authorization["authorized_at"],
        "scope": authorization["scope"],
        "scope_digest": authorization["scope_digest"],
        "vin_binding": {
            "algorithm": authorization["vin_binding"]["algorithm"],
            "iterations": authorization["vin_binding"]["iterations"],
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-telemetry",
        description="Local owner control for Open MMI Telemetry Guard",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show current local telemetry authorization")
    status.set_defaults(handler=_cmd_status)

    authorize_parser = subparsers.add_parser(
        "authorize",
        help="authorize one exact local-only session telemetry scope for one VIN",
    )
    authorize_parser.add_argument("--scope", type=Path, required=True)
    authorize_parser.set_defaults(handler=_cmd_authorize)

    check = subparsers.add_parser("check", help="check an exact VIN and scope without collecting")
    check.add_argument("--scope", type=Path, required=True)
    check.set_defaults(handler=_cmd_check)

    revoke_parser = subparsers.add_parser("revoke", help="remove local telemetry authorization")
    revoke_parser.set_defaults(handler=_cmd_revoke)
    return parser


def _cmd_status(args: argparse.Namespace) -> int:
    _require_root()
    print(json.dumps(_redacted_status(read_authorization(DEFAULT_AUTHORIZATION_PATH)), indent=2, sort_keys=True))
    return 0


def _cmd_authorize(args: argparse.Namespace) -> int:
    _require_root()
    scope = load_scope_file(args.scope)
    print("Proposed telemetry authorization:")
    print(json.dumps(_scope_summary(scope), indent=2, sort_keys=True))
    print("Raw VIN will not be stored. This authorization is local-only and session-scoped.")
    _require_local_tty()
    vin = _read_vin()
    _confirm_scope(scope)
    authorization = _create_authorization(vin, scope, DEFAULT_AUTHORIZATION_PATH)
    print(
        json.dumps(
            {
                "authorized": True,
                "authorized_at": authorization["authorized_at"],
                "scope_digest": authorization["scope_digest"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    _require_root()
    scope = load_scope_file(args.scope)
    _require_local_tty()
    vin = _read_vin()
    decision = collection_decision(vin, scope, DEFAULT_AUTHORIZATION_PATH)
    print(
        json.dumps(
            {
                "allowed": decision.allowed,
                "reason": decision.reason,
                "scope_digest": decision.scope_digest,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if decision.allowed else 3


def _cmd_revoke(args: argparse.Namespace) -> int:
    _require_root()
    _require_local_tty()
    _confirm_revoke()
    removed = _revoke_authorization(DEFAULT_AUTHORIZATION_PATH)
    print(json.dumps({"authorized": False, "revoked": removed}, sort_keys=True))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except TelemetryGuardError as exc:
        print(f"open-mmi-telemetry: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
