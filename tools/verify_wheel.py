#!/usr/bin/env python3
"""Fail when an Open MMI wheel omits application modules or runtime assets."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile


REQUIRED_PATHS = {
    "actions/__init__.py",
    "bindings/default.json",
    "canbusd/core.py",
    "powerd/cli.py",
    "powerd/daemon.py",
    "powerd/inhibitors.py",
    "powerd/policy.py",
    "powerd/runtime.py",
    "powerd/wake.py",
    "canbusd/action_registry.py",
    "canbusd/data/vehicle-actions.v1.json",
    "ui/dashboard/status_cli.py",
    "ui/update_policy.py",
    "ui/update_readiness.py",
    "ui/update_coordinator.py",
    "ui/update_installer.py",
    "canbusd/event_registry.py",
    "canbusd/data/vehicle-events.v1.json",
    "canbusd/status_registry.py",
    "canbusd/profile_catalogue.py",
    "canbusd/profile_replay.py",
    "canbusd/data/vehicle-statuses.v1.json",
    "canbusd/data/vehicle-profile.v1.schema.json",
    "canbusd/data/vehicle-qualification.v1.schema.json",
    "ui/vehicle_profile_conformance.py",
    "ui/vehicle_profile_qualification.py",
    "ui/vehicle_capture_analysis.py",
    "ui/vehicle_profile_scaffold.py",
    "ui/vehicle_config_coordinator.py",
    "ui/web_dashboard/bluetooth.py",
    "ui/web_dashboard/jellyfin.py",
    "ui/web_dashboard/radio.py",
    "ui/web_dashboard/runtime_diagnostics.py",
    "ui/web_dashboard/update_status.py",
    "ui/web_dashboard/server.py",
    "ui/web_dashboard/versioning.py",
    "ui/web_dashboard/usb.py",
    "ui/web_dashboard/static/api.js",
    "ui/web_dashboard/static/dashboard-connection.js",
    "ui/web_dashboard/static/frontend-version.js",
    "ui/web_dashboard/static/runtime-diagnostics.js",
    "ui/web_dashboard/static/app.js",
    "ui/web_dashboard/static/preferences.js",
    "ui/web_dashboard/static/status.js",
    "ui/web_dashboard/static/navigation.js",
    "ui/web_dashboard/static/overlays.js",
    "ui/web_dashboard/static/vehicle.js",
    "ui/web_dashboard/static/media.js",
    "ui/web_dashboard/static/jellyfin-reconnection.js",
    "ui/web_dashboard/static/media-jellyfin.js",
    "ui/web_dashboard/static/media-radio.js",
    "ui/web_dashboard/static/media-usb.js",
    "ui/web_dashboard/static/media-bluetooth.js",
    "ui/web_dashboard/static/system-settings.js",
    "ui/web_dashboard/static/styles-system-settings.css",
    "ui/web_dashboard/static/styles-runtime-hardening.css",
    "ui/web_dashboard/static/index.html",
    "ui/web_dashboard/static/styles.css",
    "ui/web_dashboard/static/styles-core.css",
    "ui/web_dashboard/static/styles-media-layout.css",
    "ui/web_dashboard/static/styles-shell.css",
    "ui/web_dashboard/static/styles-media-sources.css",
    "ui/web_dashboard/static/styles-diagnostics.css",
    "ui/web_dashboard/static/styles-media-final.css",
    "vehicles/catalogue.v1.json",
    "vehicles/_template/config.template.json",
    "vehicles/_template/fixtures/README.md",
    "vehicles/_template/evidence/README.md",
    "vehicles/_template/evidence/qualification.v1.json",
    "vehicles/_template/notes/README.md",
    "vehicles/seat/leon/1p-pq35/config.json",
    "vehicles/seat/leon/1p-pq35/fixtures/mappings.v1.json",
    "vehicles/seat/leon/1p-pq35/evidence/qualification.v1.json",
    "vehicles/seat/leon/1p-pq35/README.md",
}


def verify(wheel_path: Path) -> None:
    if not wheel_path.is_file():
        raise SystemExit(f"Wheel does not exist: {wheel_path}")

    with ZipFile(wheel_path) as archive:
        members = set(archive.namelist())

    missing = sorted(REQUIRED_PATHS - members)
    if missing:
        formatted = "\n".join(f"  - {path}" for path in missing)
        raise SystemExit(f"Wheel is missing required files:\n{formatted}")

    metadata = [name for name in members if name.endswith(".dist-info/METADATA")]
    if len(metadata) != 1:
        raise SystemExit(f"Expected exactly one METADATA file, found {len(metadata)}")

    print(f"Verified {wheel_path}: {len(members)} files, all required assets present")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    verify(args.wheel)


if __name__ == "__main__":
    main()
