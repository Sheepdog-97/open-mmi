"""Resolve and verify the active physical SocketCAN runtime."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


DEFAULT_STATUS_PATH = Path("/run/open-mmi/canbusd-status-unconfigured.json")
PHYSICAL_CAN_INTERFACE_RE = re.compile(r"^can[0-9]{1,3}$")


@dataclass(frozen=True)
class CanHealth:
    present: bool
    up: bool
    state: str

    @property
    def healthy(self) -> bool:
        return self.present and self.up and self.state not in {
            "BUS-OFF",
            "STOPPED",
            "DISCONNECTED",
            "UNKNOWN",
        }


def active_interface(status_path: Path) -> Optional[str]:
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None

    runtime = payload.get("runtime")
    if not isinstance(runtime, Mapping) or runtime.get("state") != "ready":
        return None
    interface = runtime.get("interface")
    if not isinstance(interface, str) or not PHYSICAL_CAN_INTERFACE_RE.fullmatch(interface):
        return None
    return interface


def can_health(
    interface: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> CanHealth:
    if not PHYSICAL_CAN_INTERFACE_RE.fullmatch(interface):
        return CanHealth(False, False, "UNKNOWN")
    try:
        result = runner(
            ["ip", "-details", "-json", "link", "show", "dev", interface],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return CanHealth(False, False, "UNKNOWN")
    if result.returncode != 0:
        return CanHealth(False, False, "DISCONNECTED")

    try:
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise ValueError("unexpected ip link response")
        row = rows[0]
        flags = set(row.get("flags") or [])
        info = ((row.get("linkinfo") or {}).get("info_data") or {})
        state = str(info.get("state") or "UNKNOWN").upper()
    except (ValueError, AttributeError, TypeError):
        return CanHealth(True, False, "UNKNOWN")

    return CanHealth(True, "UP" in flags, state)
