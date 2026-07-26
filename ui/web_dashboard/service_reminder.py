"""Persistent Open MMI inspection reminder configuration.

The reminder is deliberately independent of the vehicle cluster.  It records the
host date and confirmed odometer when the driver resets the interval, then exposes
fixed distance/time deadlines to the local dashboard.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from ui.configuration import ConfigurationError, config_dir

API_VERSION = 1
DEFAULT_DISTANCE_INTERVAL_KM = 16093.44  # 10,000 miles
DEFAULT_TIME_INTERVAL_MONTHS = 12
DEFAULT_WARNING_DISTANCE_KM = 1609.344  # 1,000 miles
DEFAULT_WARNING_DAYS = 30
MAX_ODOMETER_KM = 10_000_000.0


@dataclass(frozen=True)
class ReminderSettings:
    enabled: bool = True
    distance_interval_km: float = DEFAULT_DISTANCE_INTERVAL_KM
    time_interval_months: int = DEFAULT_TIME_INTERVAL_MONTHS
    warning_distance_km: float = DEFAULT_WARNING_DISTANCE_KM
    warning_days: int = DEFAULT_WARNING_DAYS


@dataclass(frozen=True)
class ReminderReset:
    reset_date: Optional[str] = None
    odometer_km: Optional[float] = None


def default_path() -> Path:
    override = os.getenv("OPEN_MMI_SERVICE_REMINDER_FILE", "").strip()
    return Path(override) if override else config_dir() / "service-reminder.json"


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


def _integer(value: Any, label: str) -> int:
    number = _finite_number(value, label)
    if not number.is_integer():
        raise ConfigurationError(f"{label} must be a whole number")
    return int(number)


def _validate_settings(payload: Mapping[str, Any]) -> ReminderSettings:
    allowed = {
        "enabled",
        "distance_interval_km",
        "time_interval_months",
        "warning_distance_km",
        "warning_days",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigurationError(f"Unsupported service reminder field: {unknown[0]}")

    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigurationError("enabled must be true or false")

    distance = _finite_number(
        payload.get("distance_interval_km", DEFAULT_DISTANCE_INTERVAL_KM),
        "distance_interval_km",
    )
    months = _integer(
        payload.get("time_interval_months", DEFAULT_TIME_INTERVAL_MONTHS),
        "time_interval_months",
    )
    warning_distance = _finite_number(
        payload.get("warning_distance_km", DEFAULT_WARNING_DISTANCE_KM),
        "warning_distance_km",
    )
    warning_days = _integer(payload.get("warning_days", DEFAULT_WARNING_DAYS), "warning_days")

    if not 100.0 <= distance <= 200_000.0:
        raise ConfigurationError("distance_interval_km must be between 100 and 200000")
    if not 1 <= months <= 120:
        raise ConfigurationError("time_interval_months must be between 1 and 120")
    if not 0.0 <= warning_distance <= distance:
        raise ConfigurationError("warning_distance_km must be between 0 and the distance interval")
    if not 0 <= warning_days <= 3650:
        raise ConfigurationError("warning_days must be between 0 and 3650")

    return ReminderSettings(
        enabled=enabled,
        distance_interval_km=distance,
        time_interval_months=months,
        warning_distance_km=warning_distance,
        warning_days=warning_days,
    )


def _validate_reset(payload: Mapping[str, Any]) -> ReminderReset:
    reset_date = payload.get("reset_date")
    odometer = payload.get("odometer_km")
    if reset_date in (None, "") and odometer in (None, ""):
        return ReminderReset()
    if not isinstance(reset_date, str):
        raise ConfigurationError("reset_date must be an ISO date")
    try:
        parsed = date.fromisoformat(reset_date)
    except ValueError as exc:
        raise ConfigurationError("reset_date must be an ISO date") from exc
    number = _finite_number(odometer, "odometer_km")
    if not 0.0 <= number <= MAX_ODOMETER_KM:
        raise ConfigurationError("odometer_km is outside the supported range")
    return ReminderReset(reset_date=parsed.isoformat(), odometer_km=number)


def _default_document() -> dict[str, Any]:
    settings = ReminderSettings()
    return {
        "api_version": API_VERSION,
        "settings": settings.__dict__,
        "reset": ReminderReset().__dict__,
    }


def read_document(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_document()
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read service reminder configuration: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("service reminder configuration must be an object")
    if payload.get("api_version") != API_VERSION:
        raise ConfigurationError("unsupported service reminder configuration version")
    unknown = sorted(set(payload) - {"api_version", "settings", "reset"})
    if unknown:
        raise ConfigurationError(f"unsupported service reminder configuration field: {unknown[0]}")
    if not isinstance(payload.get("settings"), dict):
        raise ConfigurationError("service reminder settings must be an object")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("service reminder reset must be an object")
    settings = _validate_settings(payload["settings"])
    reset = _validate_reset(payload["reset"])
    return {
        "api_version": API_VERSION,
        "settings": settings.__dict__,
        "reset": reset.__dict__,
    }


def _write_document(document: Mapping[str, Any], path: Optional[Path] = None) -> None:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.chmod(0o700)
    except OSError as exc:
        raise ConfigurationError(f"cannot prepare service reminder directory: {exc}") from exc

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
        raise ConfigurationError(f"cannot write service reminder configuration: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def status_payload(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    document = read_document(target)
    settings = document["settings"]
    reset = document["reset"]
    configured = reset["reset_date"] is not None and reset["odometer_km"] is not None
    next_due_date = None
    next_due_odometer_km = None
    if configured:
        next_due_date = add_calendar_months(
            date.fromisoformat(reset["reset_date"]),
            int(settings["time_interval_months"]),
        ).isoformat()
        next_due_odometer_km = float(reset["odometer_km"]) + float(settings["distance_interval_km"])
    return {
        "ok": True,
        "api_version": API_VERSION,
        "path": str(target),
        "configured": configured,
        "settings": settings,
        "reset": reset,
        "next_due": {
            "date": next_due_date,
            "odometer_km": next_due_odometer_km,
        },
    }


def update_settings(payload: Mapping[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    current = read_document(target)
    settings = _validate_settings(payload)
    document = {
        "api_version": API_VERSION,
        "settings": settings.__dict__,
        "reset": current["reset"],
    }
    _write_document(document, target)
    return status_payload(target)


def reset_interval(
    payload: Mapping[str, Any],
    path: Optional[Path] = None,
    *,
    today: Optional[date] = None,
) -> dict[str, Any]:
    if set(payload) != {"confirm", "odometer_km"} or payload.get("confirm") is not True:
        raise ConfigurationError("Service reminder reset requires confirmation and an odometer")
    odometer = _finite_number(payload.get("odometer_km"), "odometer_km")
    if not 0.0 <= odometer <= MAX_ODOMETER_KM:
        raise ConfigurationError("odometer_km is outside the supported range")
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    current = read_document(target)
    reset = ReminderReset(
        reset_date=(today or date.today()).isoformat(),
        odometer_km=odometer,
    )
    document = {
        "api_version": API_VERSION,
        "settings": current["settings"],
        "reset": reset.__dict__,
    }
    _write_document(document, target)
    return status_payload(target)
