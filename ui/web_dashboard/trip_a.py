"""Persistent Trip A state and parked-time automatic reset support.

Trip A uses the shared high-resolution trip distance accumulator when available,
with the confirmed vehicle odometer retained as a compatibility fallback and
recovery anchor. The host stores the explicit reset point plus a low-frequency
activity heartbeat used to detect a parked interval. Automatic reset is
conservative: it only occurs when the saved activity is old enough and the
odometer has not advanced by more than one kilometre while the dashboard was
inactive.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from ui.configuration import ConfigurationError, config_dir

API_VERSION = 3
MAX_ODOMETER_KM = 10_000_000.0
MAX_DISTANCE_TOTAL_KM = 100_000_000.0
MAX_AUTO_RESET_HOURS = 168
AUTO_RESET_ODOMETER_TOLERANCE_KM = 1.0


@dataclass(frozen=True)
class TripSettings:
    auto_reset_hours: int = 0


@dataclass(frozen=True)
class TripReset:
    reset_at: Optional[str] = None
    odometer_km: Optional[float] = None
    distance_total_km: Optional[float] = None


@dataclass(frozen=True)
class TripActivity:
    last_active_at: Optional[str] = None
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


def _odometer(value: Any) -> float:
    number = _finite_number(value, "odometer_km")
    if not 0.0 <= number <= MAX_ODOMETER_KM:
        raise ConfigurationError("odometer_km is outside the supported range")
    return number


def _distance_total(value: Any) -> float:
    number = _finite_number(value, "distance_total_km")
    if not 0.0 <= number <= MAX_DISTANCE_TOTAL_KM:
        raise ConfigurationError("distance_total_km is outside the supported range")
    return number


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"{label} must include a timezone")
    return parsed.isoformat()


def _normalise_now(value: Optional[datetime]) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def _validate_settings(payload: Mapping[str, Any]) -> TripSettings:
    unknown = sorted(set(payload) - {"auto_reset_hours"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A setting: {unknown[0]}")
    value = _finite_number(payload.get("auto_reset_hours", 0), "auto_reset_hours")
    if not value.is_integer():
        raise ConfigurationError("auto_reset_hours must be a whole number")
    hours = int(value)
    if not 0 <= hours <= MAX_AUTO_RESET_HOURS:
        raise ConfigurationError(f"auto_reset_hours must be between 0 and {MAX_AUTO_RESET_HOURS}")
    return TripSettings(auto_reset_hours=hours)


def _validate_reset(payload: Mapping[str, Any]) -> TripReset:
    unknown = sorted(set(payload) - {"reset_at", "odometer_km", "distance_total_km"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A reset field: {unknown[0]}")
    reset_at = payload.get("reset_at")
    odometer = payload.get("odometer_km")
    distance_total = payload.get("distance_total_km")
    if reset_at in (None, "") and odometer in (None, "") and distance_total in (None, ""):
        return TripReset()
    return TripReset(
        reset_at=_timestamp(reset_at, "reset_at"),
        odometer_km=_odometer(odometer),
        distance_total_km=None if distance_total in (None, "") else _distance_total(distance_total),
    )


def _validate_activity(payload: Mapping[str, Any]) -> TripActivity:
    unknown = sorted(set(payload) - {"last_active_at", "odometer_km"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A activity field: {unknown[0]}")
    last_active_at = payload.get("last_active_at")
    odometer = payload.get("odometer_km")
    if last_active_at in (None, "") and odometer in (None, ""):
        return TripActivity()
    return TripActivity(
        last_active_at=_timestamp(last_active_at, "last_active_at"),
        odometer_km=_odometer(odometer),
    )


def _default_document() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "settings": TripSettings().__dict__,
        "reset": TripReset().__dict__,
        "activity": TripActivity().__dict__,
    }


def _migrate_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(payload) - {"api_version", "reset"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A configuration field: {unknown[0]}")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("Trip A reset must be an object")
    reset = _validate_reset(payload["reset"])
    return {
        "api_version": API_VERSION,
        "settings": TripSettings().__dict__,
        "reset": reset.__dict__,
        "activity": TripActivity().__dict__,
    }


def _migrate_v2(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(payload) - {"api_version", "settings", "reset", "activity"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A configuration field: {unknown[0]}")
    if not isinstance(payload.get("settings"), dict):
        raise ConfigurationError("Trip A settings must be an object")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("Trip A reset must be an object")
    if not isinstance(payload.get("activity"), dict):
        raise ConfigurationError("Trip A activity must be an object")
    return {
        "api_version": API_VERSION,
        "settings": _validate_settings(payload["settings"]).__dict__,
        "reset": _validate_reset(payload["reset"]).__dict__,
        "activity": _validate_activity(payload["activity"]).__dict__,
    }


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
    if payload.get("api_version") == 1:
        return _migrate_v1(payload)
    if payload.get("api_version") == 2:
        return _migrate_v2(payload)
    if payload.get("api_version") != API_VERSION:
        raise ConfigurationError("unsupported Trip A configuration version")
    unknown = sorted(set(payload) - {"api_version", "settings", "reset", "activity"})
    if unknown:
        raise ConfigurationError(f"unsupported Trip A configuration field: {unknown[0]}")
    if not isinstance(payload.get("settings"), dict):
        raise ConfigurationError("Trip A settings must be an object")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("Trip A reset must be an object")
    if not isinstance(payload.get("activity"), dict):
        raise ConfigurationError("Trip A activity must be an object")
    settings = _validate_settings(payload["settings"])
    reset = _validate_reset(payload["reset"])
    activity = _validate_activity(payload["activity"])
    return {
        "api_version": API_VERSION,
        "settings": settings.__dict__,
        "reset": reset.__dict__,
        "activity": activity.__dict__,
    }


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
            "w", encoding="utf-8", dir=target.parent, prefix=target.name + ".", delete=False
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
        "settings": document["settings"],
        "reset": reset,
        "activity": document["activity"],
    }


def update_settings(payload: Mapping[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked Trip A configuration: {target}")
    current = read_document(target)
    current["settings"] = _validate_settings(payload).__dict__
    _write_document(current, target)
    return status_payload(target)


def reset_trip(
    payload: Mapping[str, Any],
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    allowed = {"confirm", "odometer_km", "distance_total_km"}
    if not set(payload).issubset(allowed) or {"confirm", "odometer_km"} - set(payload) or payload.get("confirm") is not True:
        raise ConfigurationError("Trip A reset requires confirmation and an odometer")
    odometer = _odometer(payload.get("odometer_km"))
    distance_total = payload.get("distance_total_km")
    reset_distance = None if distance_total in (None, "") else _distance_total(distance_total)
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked Trip A configuration: {target}")
    current = read_document(target)
    reset_time = _normalise_now(now)
    current["reset"] = TripReset(
        reset_at=reset_time.isoformat(),
        odometer_km=odometer,
        distance_total_km=reset_distance,
    ).__dict__
    current["activity"] = TripActivity(last_active_at=reset_time.isoformat(), odometer_km=odometer).__dict__
    _write_document(current, target)
    return status_payload(target)


def observe_vehicle(
    payload: Mapping[str, Any],
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    allowed = {"odometer_km", "distance_total_km"}
    if not set(payload).issubset(allowed) or "odometer_km" not in payload:
        raise ConfigurationError("Trip A observation requires an odometer")
    odometer = _odometer(payload.get("odometer_km"))
    distance_total = payload.get("distance_total_km")
    observed_distance = None if distance_total in (None, "") else _distance_total(distance_total)
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked Trip A configuration: {target}")
    current = read_document(target)
    observed_at = _normalise_now(now)
    configured = current["reset"]["odometer_km"] is not None
    auto_reset_hours = int(current["settings"]["auto_reset_hours"])
    activity = current["activity"]
    auto_reset = False

    if configured and auto_reset_hours > 0 and activity["last_active_at"] and activity["odometer_km"] is not None:
        last_active = datetime.fromisoformat(activity["last_active_at"])
        inactive_for = observed_at - last_active
        odometer_advance = odometer - float(activity["odometer_km"])
        if (
            inactive_for >= timedelta(hours=auto_reset_hours)
            and -AUTO_RESET_ODOMETER_TOLERANCE_KM <= odometer_advance <= AUTO_RESET_ODOMETER_TOLERANCE_KM
        ):
            current["reset"] = TripReset(
                reset_at=observed_at.isoformat(),
                odometer_km=odometer,
                distance_total_km=observed_distance,
            ).__dict__
            auto_reset = True

    current["activity"] = TripActivity(last_active_at=observed_at.isoformat(), odometer_km=odometer).__dict__
    _write_document(current, target)
    result = status_payload(target)
    result["auto_reset"] = auto_reset
    return result
