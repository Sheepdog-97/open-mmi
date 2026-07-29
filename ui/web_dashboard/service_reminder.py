"""Persistent inspection service reminder configuration.

The reminder is intentionally independent from the vehicle cluster. It stores
an Open MMI reset point, user-selected distance/time intervals and a persistent
notification acknowledgement. The browser combines this host state with the
confirmed live odometer.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from ui.configuration import ConfigurationError, config_dir

API_VERSION = 2
DEFAULT_DISTANCE_INTERVAL_KM = 16093.44
DEFAULT_TIME_INTERVAL_MONTHS = 12
DEFAULT_WARNING_DISTANCE_KM = 1609.344
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


@dataclass(frozen=True)
class ReminderAcknowledgement:
    acknowledged_at: Optional[str] = None
    level: Optional[str] = None
    reset_date: Optional[str] = None
    reset_odometer_km: Optional[float] = None
    due_date: Optional[str] = None
    due_odometer_km: Optional[float] = None


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


def _validate_odometer(value: Any, label: str = "odometer_km") -> float:
    number = _finite_number(value, label)
    if not 0.0 <= number <= MAX_ODOMETER_KM:
        raise ConfigurationError(f"{label} is outside the supported range")
    return number


def _validate_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"{label} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigurationError(f"{label} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ConfigurationError(f"{label} must include a timezone")
    return parsed.isoformat()


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
    distance = _finite_number(payload.get("distance_interval_km", DEFAULT_DISTANCE_INTERVAL_KM), "distance_interval_km")
    months = _integer(payload.get("time_interval_months", DEFAULT_TIME_INTERVAL_MONTHS), "time_interval_months")
    warning_distance = _finite_number(payload.get("warning_distance_km", DEFAULT_WARNING_DISTANCE_KM), "warning_distance_km")
    warning_days = _integer(payload.get("warning_days", DEFAULT_WARNING_DAYS), "warning_days")
    if not 100.0 <= distance <= 200_000.0:
        raise ConfigurationError("distance_interval_km must be between 100 and 200000")
    if not 1 <= months <= 120:
        raise ConfigurationError("time_interval_months must be between 1 and 120")
    if not 0.0 <= warning_distance <= distance:
        raise ConfigurationError("warning_distance_km must be between 0 and the distance interval")
    if not 0 <= warning_days <= 3650:
        raise ConfigurationError("warning_days must be between 0 and 3650")
    return ReminderSettings(enabled, distance, months, warning_distance, warning_days)


def _validate_reset(payload: Mapping[str, Any]) -> ReminderReset:
    unknown = sorted(set(payload) - {"reset_date", "odometer_km"})
    if unknown:
        raise ConfigurationError(f"unsupported service reminder reset field: {unknown[0]}")
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
    return ReminderReset(reset_date=parsed.isoformat(), odometer_km=_validate_odometer(odometer))


def _validate_acknowledgement(payload: Mapping[str, Any]) -> ReminderAcknowledgement:
    allowed = {
        "acknowledged_at", "level", "reset_date", "reset_odometer_km", "due_date", "due_odometer_km"
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ConfigurationError(f"unsupported service reminder acknowledgement field: {unknown[0]}")
    if all(payload.get(key) in (None, "") for key in allowed):
        return ReminderAcknowledgement()
    level = payload.get("level")
    if level not in {"soon", "due"}:
        raise ConfigurationError("acknowledgement level must be soon or due")
    reset_date = payload.get("reset_date")
    due_date = payload.get("due_date")
    try:
        reset_date = date.fromisoformat(str(reset_date)).isoformat()
        due_date = date.fromisoformat(str(due_date)).isoformat()
    except ValueError as exc:
        raise ConfigurationError("acknowledgement dates must be ISO dates") from exc
    return ReminderAcknowledgement(
        acknowledged_at=_validate_timestamp(payload.get("acknowledged_at"), "acknowledged_at"),
        level=level,
        reset_date=reset_date,
        reset_odometer_km=_validate_odometer(payload.get("reset_odometer_km"), "reset_odometer_km"),
        due_date=due_date,
        due_odometer_km=_validate_odometer(payload.get("due_odometer_km"), "due_odometer_km"),
    )


def _default_document() -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "settings": ReminderSettings().__dict__,
        "reset": ReminderReset().__dict__,
        "acknowledgement": ReminderAcknowledgement().__dict__,
    }


def _migrate_v1(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(payload) - {"api_version", "settings", "reset"})
    if unknown:
        raise ConfigurationError(f"unsupported service reminder configuration field: {unknown[0]}")
    if not isinstance(payload.get("settings"), dict):
        raise ConfigurationError("service reminder settings must be an object")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("service reminder reset must be an object")
    return {
        "api_version": API_VERSION,
        "settings": _validate_settings(payload["settings"]).__dict__,
        "reset": _validate_reset(payload["reset"]).__dict__,
        "acknowledgement": ReminderAcknowledgement().__dict__,
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
    if payload.get("api_version") == 1:
        return _migrate_v1(payload)
    if payload.get("api_version") != API_VERSION:
        raise ConfigurationError("unsupported service reminder configuration version")
    unknown = sorted(set(payload) - {"api_version", "settings", "reset", "acknowledgement"})
    if unknown:
        raise ConfigurationError(f"unsupported service reminder configuration field: {unknown[0]}")
    if not isinstance(payload.get("settings"), dict):
        raise ConfigurationError("service reminder settings must be an object")
    if not isinstance(payload.get("reset"), dict):
        raise ConfigurationError("service reminder reset must be an object")
    if not isinstance(payload.get("acknowledgement"), dict):
        raise ConfigurationError("service reminder acknowledgement must be an object")
    return {
        "api_version": API_VERSION,
        "settings": _validate_settings(payload["settings"]).__dict__,
        "reset": _validate_reset(payload["reset"]).__dict__,
        "acknowledgement": _validate_acknowledgement(payload["acknowledgement"]).__dict__,
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


def _next_due(document: Mapping[str, Any]) -> dict[str, Any]:
    settings = document["settings"]
    reset = document["reset"]
    configured = reset["reset_date"] is not None and reset["odometer_km"] is not None
    if not configured:
        return {"date": None, "odometer_km": None}
    return {
        "date": add_calendar_months(date.fromisoformat(reset["reset_date"]), int(settings["time_interval_months"])).isoformat(),
        "odometer_km": float(reset["odometer_km"]) + float(settings["distance_interval_km"]),
    }


def status_payload(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    document = read_document(target)
    reset = document["reset"]
    configured = reset["reset_date"] is not None and reset["odometer_km"] is not None
    return {
        "ok": True,
        "api_version": API_VERSION,
        "path": str(target),
        "configured": configured,
        "settings": document["settings"],
        "reset": reset,
        "next_due": _next_due(document),
        "acknowledgement": document["acknowledgement"],
    }


def update_settings(payload: Mapping[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    current = read_document(target)
    current["settings"] = _validate_settings(payload).__dict__
    current["acknowledgement"] = ReminderAcknowledgement().__dict__
    _write_document(current, target)
    return status_payload(target)


def reset_interval(
    payload: Mapping[str, Any], path: Optional[Path] = None, *, today: Optional[date] = None
) -> dict[str, Any]:
    if set(payload) != {"confirm", "odometer_km"} or payload.get("confirm") is not True:
        raise ConfigurationError("Service reminder reset requires confirmation and an odometer")
    odometer = _validate_odometer(payload.get("odometer_km"))
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    current = read_document(target)
    current["reset"] = ReminderReset(reset_date=(today or date.today()).isoformat(), odometer_km=odometer).__dict__
    current["acknowledgement"] = ReminderAcknowledgement().__dict__
    _write_document(current, target)
    return status_payload(target)


def acknowledge(
    payload: Mapping[str, Any], path: Optional[Path] = None, *, now: Optional[datetime] = None
) -> dict[str, Any]:
    if set(payload) != {"confirm", "level"} or payload.get("confirm") is not True:
        raise ConfigurationError("Service reminder acknowledgement requires confirmation and a level")
    level = payload.get("level")
    if level not in {"soon", "due"}:
        raise ConfigurationError("acknowledgement level must be soon or due")
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked service reminder: {target}")
    current = read_document(target)
    next_due = _next_due(current)
    reset = current["reset"]
    if reset["reset_date"] is None or next_due["date"] is None:
        raise ConfigurationError("inspection interval must be reset before acknowledgement")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    current["acknowledgement"] = ReminderAcknowledgement(
        acknowledged_at=timestamp.isoformat(),
        level=level,
        reset_date=reset["reset_date"],
        reset_odometer_km=float(reset["odometer_km"]),
        due_date=next_due["date"],
        due_odometer_km=float(next_due["odometer_km"]),
    ).__dict__
    _write_document(current, target)
    return status_payload(target)
