"""Command-line interface for the read-only Open MMI Trust Inspector."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Mapping, Sequence

from .inspector import FAIL, PASS, UNVERIFIED, inspect_system


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="open-mmi-trust-inspect",
        description="Read-only inspection of the installed Open MMI trust contract",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit the complete machine-readable inspection report",
    )
    parser.add_argument(
        "--require-pass",
        action="store_true",
        help="return non-zero when any evidence remains UNVERIFIED",
    )
    return parser


def _render_capability(capability_id: str, capability: Mapping[str, Any]) -> str:
    suffix = ""
    purposes = capability.get("purposes")
    if purposes:
        suffix = " purposes=" + ",".join(str(item) for item in purposes)
    return (
        f"  {capability_id}: policy={capability['policy']} "
        f"assurance={capability['assurance']}{suffix}"
    )


def render_text(report: Mapping[str, Any]) -> str:
    lines = ["Open MMI Trust Inspector v1", f"Overall: {report['status']}"]
    manifest = report["manifest"]
    if manifest.get("available"):
        lines.append(
            "Manifest: "
            f"generation {manifest['policy_generation']} {manifest['digest']}"
        )
        lines.append("Capabilities:")
        for capability_id in sorted(manifest["capabilities"]):
            lines.append(_render_capability(capability_id, manifest["capabilities"][capability_id]))
    else:
        lines.append(f"Manifest: unavailable ({manifest.get('error', 'unknown error')})")

    accepted_check = next(
        (check for check in report["checks"] if check.get("id") == "owner.accepted-release-state"),
        None,
    )
    if accepted_check is not None:
        evidence = accepted_check.get("evidence", {})
        if evidence.get("established") is True:
            lines.append(
                "Accepted owner trust: ESTABLISHED "
                f"generation={evidence['accepted_generation']} "
                f"manifest={evidence['accepted_manifest_digest']} "
                f"current={evidence.get('current_relation', 'unverified')}"
            )
        elif evidence.get("established") is False:
            lines.append("Accepted owner trust: NOT ESTABLISHED")
        else:
            lines.append(f"Accepted owner trust: {accepted_check['status']}")

    telemetry = report["telemetry_authorization"]
    if telemetry.get("authorized") is True:
        lines.append(
            "Telemetry authorization: AUTHORIZED "
            f"scope={telemetry['scope_digest']} purpose={telemetry['scope']['purpose']}"
        )
    elif telemetry.get("authorized") is False:
        lines.append("Telemetry authorization: NOT AUTHORIZED")
    else:
        lines.append(f"Telemetry authorization: {str(telemetry.get('state', 'unknown')).upper()}")

    lines.append("Checks:")
    for check in report["checks"]:
        lines.append(f"  {check['status']:<10} {check['id']}: {check['summary']}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = inspect_system()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))

    if report["status"] == FAIL:
        return 3
    if args.require_pass and report["status"] == UNVERIFIED:
        return 4
    if report["status"] in {PASS, UNVERIFIED}:
        return 0
    print("open-mmi-trust-inspect: invalid inspection status", file=sys.stderr)
    return 5


if __name__ == "__main__":
    raise SystemExit(main())
