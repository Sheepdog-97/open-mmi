"""Command-line entry point for the Open MMI power-policy service."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

from powerd.daemon import run
from powerd.policy import (
    DEFAULT_POLICY_PATH,
    PowerPolicyError,
    load_policy,
    policy_payload,
    update_policy,
)
from powerd.runtime import DEFAULT_STATUS_PATH, PHYSICAL_CAN_INTERFACE_RE
from powerd.wake import DEFAULT_SYS_CLASS_NET, enable_remote_wake


def _policy_command(args: argparse.Namespace) -> int:
    try:
        if args.policy_action == "enable":
            policy = update_policy(
                args.policy,
                enabled=True,
                silence_seconds=args.silence_seconds,
            )
        elif args.policy_action == "disable":
            policy = update_policy(args.policy, enabled=False)
        else:
            policy = load_policy(args.policy)
    except PowerPolicyError as exc:
        print(f"open-mmi-powerd: {exc}", file=__import__("sys").stderr)
        return 2

    print(json.dumps(policy_payload(policy), indent=2, sort_keys=True))
    return 0


def _wake_enable_command(args: argparse.Namespace) -> int:
    if not PHYSICAL_CAN_INTERFACE_RE.fullmatch(args.interface):
        print(
            f"open-mmi-powerd: invalid physical CAN interface: {args.interface}",
            file=__import__("sys").stderr,
        )
        return 2

    if enable_remote_wake(args.interface, args.sys_class_net):
        return 0

    print(
        f"open-mmi-powerd: remote wake is unavailable for {args.interface}",
        file=__import__("sys").stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run the persistent policy daemon")
    run_parser.add_argument(
        "--policy",
        type=Path,
        default=Path(
            os.getenv("OPEN_MMI_POWER_POLICY", str(DEFAULT_POLICY_PATH))
        ),
    )
    run_parser.add_argument(
        "--status",
        type=Path,
        default=Path(os.getenv("OPEN_MMI_STATUS_PATH", str(DEFAULT_STATUS_PATH))),
    )

    wake_parser = subparsers.add_parser(
        "wake-enable",
        help="enable verified USB and PCI wake controls for a CAN interface",
    )
    wake_parser.add_argument("--interface", required=True)
    wake_parser.add_argument(
        "--sys-class-net",
        type=Path,
        default=DEFAULT_SYS_CLASS_NET,
        help=argparse.SUPPRESS,
    )

    policy_parser = subparsers.add_parser("policy", help="inspect or update policy")
    policy_parser.add_argument(
        "policy_action",
        choices=("show", "enable", "disable"),
    )
    policy_parser.add_argument(
        "--policy",
        type=Path,
        default=Path(
            os.getenv("OPEN_MMI_POWER_POLICY", str(DEFAULT_POLICY_PATH))
        ),
    )
    policy_parser.add_argument(
        "--silence-seconds",
        type=float,
        default=60.0,
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "policy":
        return _policy_command(args)
    if args.command == "wake-enable":
        return _wake_enable_command(args)

    logging.basicConfig(
        level=getattr(
            logging,
            os.getenv("OPEN_MMI_LOG_LEVEL", "INFO").upper(),
            logging.INFO,
        ),
        format="[%(name)s] %(levelname)s: %(message)s",
    )
    run(policy_path=args.policy, status_path=args.status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
