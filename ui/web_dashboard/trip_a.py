"""Persistent Trip A reset state for the Open MMI dashboard.

Trip A is calculated from the confirmed vehicle odometer.  The host stores only
an explicit reset timestamp and odometer value; the live distance is always
recomputed in the browser from the current CAN status.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from ui.configuration import ConfigurationError, config_dir

API_VERSION = 1
MAX_ODOMETER_KM = 10_000_000.0


@dataclass(frozen=True)
class TripReset:
    reset_at: Optional[str] = None
    odometer_km: Optional[float] = None


def default_path() -> Path:
    override = os.getenv("OPEN_MMI_TRIP_A_FILE", "").strip()
    return Path(override) if override else config_dir() / "trip-a.json"


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ConfigurationError(f"{label} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise ConfigurationError(f"{label} must be finite")
    return number


def _validate_reset(payload: Mapping[str, Any]) -> TripReset:
    unknown = sorted(set(payload) - {"reset_at", "odometer_km"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A reset field: {unknown[0]}")

    reset_at = payload.get("reset_at")
    odometer = payload.get("odometer_km")
    if reset_at in (None, "") and odometer in (None, ""):
        return TripReset()
    if not isinstance(reset_at, str):
        raise ConfigurationError("reset_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(reset_at)
    except ValueError as exc:
        raise ConfigurationError("reset_at must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError("reset_at must include a timezone")
    number = _finite_number(odometer, "odometer_km")
    if not 0.0 <= number <= MAX_ODOMETER_KM:
        raise ConfigurationError("odometer_km is outside the supported range")
    return TripReset(reset_at=parsed.isoformat(), odometer_km=number)


def _default_document() -> dict[str, Any]:
    return {"api_version": API_VERSION, "reset": TripReset().__dict__}


def read_document(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_document()
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read Trip A configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("Trip A configuration must be an object")
    if payload.get("api_version") != API_VERSION:
        raise ConfigurationError("unsupported Trip A configuration version")
    unknown = sorted(set(payload) - {"api_version", "reset"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A configuration field: {unknown[0]}")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("Trip A reset must be an object")
    reset = _validate_reset(payload["reset"])
    return {"api_version": API_VERSION, "reset": reset.__dict__}


def _write_document(document: Mapping[str, Any], path: Optional[Path] = None) -> None:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked Trip A configuration: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.chmod(0o700)
    except OSError as exc:
        raise ConfigurationError(f"cannot prepare Trip A configuration directory: {exc}") from exc

    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=target.name + ".",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        temporary.replace(target)
        target.chmod(0o600)
    except OSError as exc:
        raise ConfigurationError(f"cannot write Trip A configuration: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def status_payload(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    document = read_document(target)
    reset = document["reset"]
    configured = reset["reset_at"] is not None and reset["odometer_km"] is not None
    return {
        "ok": True,
        "api_version": API_VERSION,
        "path": str(target),
        "configured": configured,
        "reset": reset,
    }


def reset_trip(
    payload: Mapping[str, Any],
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if set(payload) != {"confirm", "odometer_km"} or payload.get("confirm") is not True:
        raise ConfigurationError("Trip A reset requires confirmation and an odometer")
    odometer = _finite_number(payload.get("odometer_km"), "odometer_km")
    if not 0.0 <= odometer <= MAX_ODOMETER_KM:
        raise ConfigurationError("odometer_km is outside the supported range")
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked Trip A configuration: {target}")
    reset_time = now or datetime.now(timezone.utc)
    if reset_time.tzinfo is None:
        reset_time = reset_time.replace(tzinfo=timezone.utc)
    document = {
        "api_version": API_VERSION,
        "reset": TripReset(reset_at=reset_time.isoformat(), odometer_km=odometer).__dict__,
    }
    _write_document(document, target)
    return status_payload(target)
