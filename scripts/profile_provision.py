#!/usr/bin/env python3
"""Apply profile-driven open-mmi CAN provisioning.

This helper is intentionally called by scripts/manage.sh, not by canbusd.

canbusd remains a passive SocketCAN consumer. This script is an explicit management
operation that reads the selected vehicle profile and writes the local Linux plumbing
needed for that profile.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from canbusd import profile_catalogue


DEFAULT_BUS = "comfort"
DEFAULT_INTERFACE = "can0"
UDEV_RULE_PATH = Path("/etc/udev/rules.d/80-canbus.rules")


@dataclass(frozen=True)
class CanBusProvision:
    name: str
    interface: str
    bitrate: Optional[int]
    provisioning: str
    capture_point: Optional[str]
    bring_up: bool
    source: str


@dataclass(frozen=True)
class ProfileProvisionPlan:
    vehicle: str
    bindings: str
    profile_path: Path
    bindings_path: Path
    default_bus: str
    active_interface: str
    buses: list[CanBusProvision]


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        value = value.strip()
        return int(value, 16) if value.lower().startswith("0x") else int(value)

    return int(value)


def _optional_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}

    return bool(value)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")

    return data


def resolve_source_vehicle(
    repo_root: Path,
    install_dir: Path,
    vehicle: str,
    *,
    development_checkout: bool = False,
) -> dict[str, Any]:
    roots = (
        (repo_root, install_dir)
        if development_checkout
        else (install_dir, repo_root)
    )
    for root in roots:
        try:
            resolved = profile_catalogue.resolve_profile(root, vehicle)
        except profile_catalogue.VehicleProfileCatalogueError:
            continue
        if Path(resolved["path"]).is_file():
            return resolved
    raise FileNotFoundError(f"Vehicle profile not found: {vehicle}")


def source_vehicle_path(
    repo_root: Path,
    install_dir: Path,
    vehicle: str,
    *,
    development_checkout: bool = False,
) -> Path:
    return Path(
        resolve_source_vehicle(
            repo_root,
            install_dir,
            vehicle,
            development_checkout=development_checkout,
        )["path"]
    )


def source_bindings_path(
    repo_root: Path,
    install_dir: Path,
    bindings: str,
    *,
    development_checkout: bool = False,
) -> Path:
    install_path = install_dir / "bindings" / f"{bindings}.json"
    repo_path = repo_root / "bindings" / f"{bindings}.json"

    candidates = (
        (repo_path, install_path)
        if development_checkout
        else (install_path, repo_path)
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate

    raise FileNotFoundError(f"Bindings file not found: {bindings}")


def copy_if_missing(src: Path, dst: Path) -> bool:
    if dst.exists():
        return False

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


def chown_tree(path: Path, user: str) -> None:
    if not user:
        return

    try:
        info = pwd.getpwnam(user)
    except KeyError:
        return

    if not path.exists():
        return

    for item in [path, *path.rglob("*")]:
        try:
            os.chown(item, info.pw_uid, info.pw_gid)
        except PermissionError:
            pass


def build_plan(
    profile: Mapping[str, Any],
    profile_path: Path,
    bindings_path: Path,
    vehicle: str,
    bindings: str,
) -> ProfileProvisionPlan:
    default_bus = str(profile.get("default_bus") or DEFAULT_BUS)

    raw_buses = profile.get("can_buses") or {}
    if not isinstance(raw_buses, Mapping):
        raise ValueError("profile can_buses must be an object when present")

    if default_bus not in raw_buses:
        raw_buses = {
            **dict(raw_buses),
            default_bus: {},
        }

    buses: list[CanBusProvision] = []
    for name, raw_meta in raw_buses.items():
        if not isinstance(raw_meta, Mapping):
            raise ValueError(f"can_buses.{name} must be an object")

        interface = str(
            raw_meta.get("interface")
            or raw_meta.get("tested_interface")
            or DEFAULT_INTERFACE
        )
        bitrate = _optional_int(raw_meta.get("bitrate"))
        provisioning = str(raw_meta.get("provisioning") or "manual")

        physical_can = (
            interface.startswith("can")
            and interface[3:].isdigit()
            and 1 <= len(interface[3:]) <= 3
        )
        if physical_can and (
            provisioning != "udev"
            or bitrate is None
            or bitrate <= 0
        ):
            raise ValueError(
                "physical CAN interfaces require bitrate and "
                "udev listen-only provisioning"
            )

        buses.append(
            CanBusProvision(
                name=str(name),
                interface=interface,
                bitrate=bitrate,
                provisioning=provisioning,
                capture_point=raw_meta.get("capture_point"),
                bring_up=_optional_bool(raw_meta.get("bring_up"), False),
                source="profile",
            )
        )

    active_bus = next((bus for bus in buses if bus.name == default_bus), buses[0])

    return ProfileProvisionPlan(
        vehicle=vehicle,
        bindings=bindings,
        profile_path=profile_path,
        bindings_path=bindings_path,
        default_bus=default_bus,
        active_interface=active_bus.interface,
        buses=buses,
    )


def render_systemd_dropin(plan: ProfileProvisionPlan) -> str:
    return f"""# Generated by open-mmi profile provisioning.
#
# Source profile:
#   {plan.profile_path}
#
# Edit with:
#   sudo ./scripts/manage.sh config apply-profile {plan.vehicle} {plan.bindings}
#
# Advanced manual override:
#   sudo ./scripts/manage.sh config edit-can

[Service]
Environment="OPEN_MMI_VEHICLE={plan.vehicle}"
Environment="OPEN_MMI_BINDINGS={plan.bindings}"
Environment="OPEN_MMI_VEHICLE_CONFIG={plan.profile_path}"
Environment="OPEN_MMI_BINDINGS_FILE={plan.bindings_path}"
Environment="OPEN_MMI_CAN_BUS={plan.default_bus}"
Environment="OPEN_MMI_CAN_INTERFACE={plan.active_interface}"
"""


def render_udev_rules(plan: ProfileProvisionPlan) -> str:
    lines = [
        "# Generated by open-mmi profile provisioning.",
        "#",
        f"# Vehicle profile: {plan.vehicle}",
        f"# Source profile: {plan.profile_path}",
        "#",
        "# CAN interfaces are provisioned here because the daemon is a passive",
        "# SocketCAN consumer. canbusd does not configure bitrate or bring links up.",
        "",
    ]

    emitted_interfaces: set[str] = set()

    for bus in plan.buses:
        if bus.provisioning != "udev":
            lines.extend(
                [
                    f"# CAN bus {bus.name}: provisioning={bus.provisioning}; no udev rule generated.",
                    "",
                ]
            )
            continue

        if not bus.bitrate:
            lines.extend(
                [
                    f"# CAN bus {bus.name}: no bitrate metadata; no udev rule generated.",
                    "",
                ]
            )
            continue

        if bus.interface in emitted_interfaces:
            lines.extend(
                [
                    f"# CAN bus {bus.name}: interface {bus.interface} already has a generated rule.",
                    "",
                ]
            )
            continue

        emitted_interfaces.add(bus.interface)

        if bus.capture_point:
            lines.append(f"# CAN bus {bus.name}: {bus.capture_point}")
        else:
            lines.append(f"# CAN bus {bus.name}")

        lines.append(
            f'SUBSYSTEM=="net", KERNEL=="{bus.interface}", ACTION=="add", '
            f'RUN+="/sbin/ip link set {bus.interface} down", '
            f'RUN+="/sbin/ip link set {bus.interface} type can bitrate {bus.bitrate} listen-only on", '
            f'RUN+="/sbin/ip link set {bus.interface} up"'
        )
        lines.append("")

    lines.extend(
        [
            "# Local UI/input support used by open-mmi actions.",
            'KERNEL=="uinput", MODE="0666"',
            'SUBSYSTEM=="backlight", KERNEL=="intel_backlight", GROUP="video", MODE="0664"',
            "",
        ]
    )

    return "\n".join(lines)


def apply_plan(
    plan: ProfileProvisionPlan,
    systemd_user_dir: Path,
    real_user: str,
    udev_rule_path: Path = UDEV_RULE_PATH,
) -> None:
    override_dir = systemd_user_dir / "canbusd.service.d"
    override_dir.mkdir(parents=True, exist_ok=True)
    override_file = override_dir / "10-can-runtime.conf"
    override_file.write_text(render_systemd_dropin(plan), encoding="utf-8")

    udev_rule_path.parent.mkdir(parents=True, exist_ok=True)
    udev_rule_path.write_text(render_udev_rules(plan), encoding="utf-8")

    chown_tree(systemd_user_dir, real_user)


def print_summary(plan: ProfileProvisionPlan, systemd_user_dir: Path) -> None:
    print(f"Selected vehicle profile: {plan.vehicle}")
    print(f"Bindings: {plan.bindings}")
    print()
    print(f"Profile file: {plan.profile_path}")
    print(f"Bindings file: {plan.bindings_path}")
    print()
    print("Daemon runtime:")
    print(f"  OPEN_MMI_VEHICLE={plan.vehicle}")
    print(f"  OPEN_MMI_BINDINGS={plan.bindings}")
    print(f"  OPEN_MMI_CAN_BUS={plan.default_bus}")
    print(f"  OPEN_MMI_CAN_INTERFACE={plan.active_interface}")
    print()
    print("CAN buses:")
    for bus in plan.buses:
        bitrate = bus.bitrate if bus.bitrate is not None else "not configured"
        print(
            f"  {bus.name}: interface={bus.interface} bitrate={bitrate} "
            f"provisioning={bus.provisioning}"
        )
    print()
    print(f"Systemd drop-in: {systemd_user_dir / 'canbusd.service.d' / '10-can-runtime.conf'}")
    print(f"udev rules: {UDEV_RULE_PATH}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--install-dir", type=Path, required=True)
    parser.add_argument("--user-config-dir", type=Path, required=True)
    parser.add_argument("--systemd-user-dir", type=Path, required=True)
    parser.add_argument("--vehicle", default="seat-leon-1p-pq35")
    parser.add_argument("--bindings", default="default")
    parser.add_argument("--real-user", default="")
    parser.add_argument(
        "--development-checkout",
        action="store_true",
        help="prefer maintained files from --repo-root instead of the installed tree",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    resolved_vehicle = resolve_source_vehicle(
        args.repo_root,
        args.install_dir,
        args.vehicle,
        development_checkout=args.development_checkout,
    )
    src_profile = Path(resolved_vehicle["path"])
    canonical_vehicle = str(resolved_vehicle["id"])
    src_bindings = source_bindings_path(
        args.repo_root,
        args.install_dir,
        args.bindings,
        development_checkout=args.development_checkout,
    )

    if args.dry_run:
        profile = load_json(src_profile)
        plan = build_plan(profile, src_profile, src_bindings, canonical_vehicle, args.bindings)

        print_summary(plan, args.systemd_user_dir)
        print(f"Using source profile: {src_profile}")
        print(f"Using source bindings: {src_bindings}")

        return 0

    profile = load_json(src_profile)
    plan = build_plan(profile, src_profile, src_bindings, canonical_vehicle, args.bindings)

    apply_plan(plan, args.systemd_user_dir, args.real_user)
    print_summary(plan, args.systemd_user_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
