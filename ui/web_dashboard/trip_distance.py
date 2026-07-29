"""Persistent high-resolution trip distance accumulator.

The vehicle odometer is authoritative but only exposes whole kilometres on the
confirmed SEAT 1P mapping. The dashboard therefore integrates the decoded road
speed between status samples and periodically checkpoints that fractional
movement here. The odometer remains a recovery anchor after a long dashboard
or browser interruption; it is never rescaled or replaced.
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

API_VERSION = 1
MAX_ODOMETER_KM = 10_000_000.0
MAX_TOTAL_KM = 100_000_000.0
MAX_ELAPSED_SECONDS = 600.0
MAX_SPEED_KMH = 350.0
MAX_DELTA_SLACK_KM = 0.02
RECOVERY_GAP = timedelta(minutes=2)
MAX_RECOVERY_ODOMETER_ADVANCE_KM = 1_000.0


@dataclass(frozen=True)
class DistanceState:
    total_km: float = 0.0
    updated_at: Optional[str] = None
    odometer_km: Optional[float] = None


def default_path() -> Path:
    override = os.getenv("OPEN_MMI_TRIP_DISTANCE_FILE", "").strip()
    return Path(override) if override else config_dir() / "trip-distance.json"


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


def _validate_state(payload: Mapping[str, Any]) -> DistanceState:
    unknown = sorted(set(payload) - {"total_km", "updated_at", "odometer_km"})
    if unknown:
        raise ConfigurationError(f"unsupported trip distance field: {unknown[0]}")

    total = _finite_number(payload.get("total_km", 0), "total_km")
    if not 0.0 <= total <= MAX_TOTAL_KM:
        raise ConfigurationError("total_km is outside the supported range")

    updated_at = payload.get("updated_at")
    odometer = payload.get("odometer_km")
    if updated_at in (None, "") and odometer in (None, ""):
        return DistanceState(total_km=total)
    if updated_at in (None, "") or odometer in (None, ""):
        raise ConfigurationError("trip distance timestamp and odometer must be stored together")
    return DistanceState(
        total_km=total,
        updated_at=_timestamp(updated_at, "updated_at"),
        odometer_km=_odometer(odometer),
    )


def _default_document() -> dict[str, Any]:
    return {"api_version": API_VERSION, "distance": DistanceState().__dict__}


def read_document(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _default_document()
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot read trip distance state: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigurationError("trip distance state must be an object")
    if payload.get("api_version") != API_VERSION:
        raise ConfigurationError("unsupported trip distance state version")
    unknown = sorted(set(payload) - {"api_version", "distance"})
    if unknown:
        raise ConfigurationError(f"unsupported trip distance configuration field: {unknown[0]}")
    if not isinstance(payload.get("distance"), dict):
        raise ConfigurationError("trip distance payload must be an object")
    state = _validate_state(payload["distance"])
    return {"api_version": API_VERSION, "distance": state.__dict__}


def _write_document(document: Mapping[str, Any], path: Optional[Path] = None) -> None:
    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked trip distance state: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.parent.chmod(0o700)
    except OSError as exc:
        raise ConfigurationError(f"cannot prepare trip distance directory: {exc}") from exc

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
        raise ConfigurationError(f"cannot write trip distance state: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


def status_payload(path: Optional[Path] = None) -> dict[str, Any]:
    target = path or default_path()
    document = read_document(target)
    return {
        "ok": True,
        "api_version": API_VERSION,
        "path": str(target),
        **document["distance"],
    }


def observe(
    payload: Mapping[str, Any],
    path: Optional[Path] = None,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    if set(payload) != {"distance_delta_km", "elapsed_seconds", "odometer_km"}:
        raise ConfigurationError("trip distance observation requires delta, elapsed time and odometer")

    delta = _finite_number(payload.get("distance_delta_km"), "distance_delta_km")
    elapsed = _finite_number(payload.get("elapsed_seconds"), "elapsed_seconds")
    odometer = _odometer(payload.get("odometer_km"))
    if not 0.0 <= elapsed <= MAX_ELAPSED_SECONDS:
        raise ConfigurationError("elapsed_seconds is outside the supported range")
    if delta < 0.0:
        raise ConfigurationError("distance_delta_km cannot be negative")
    if elapsed == 0.0 and delta != 0.0:
        raise ConfigurationError("distance_delta_km must be zero when elapsed_seconds is zero")
    plausible_delta = (MAX_SPEED_KMH * elapsed / 3600.0) + MAX_DELTA_SLACK_KM
    if delta > plausible_delta:
        raise ConfigurationError("distance_delta_km exceeds the supported speed envelope")

    target = path or default_path()
    if target.is_symlink():
        raise ConfigurationError(f"refusing to replace symlinked trip distance state: {target}")
    document = read_document(target)
    state = document["distance"]
    observed_at = _normalise_now(now)
    accepted_delta = delta

    previous_at = state.get("updated_at")
    previous_odometer = state.get("odometer_km")
    if previous_at and previous_odometer is not None:
        last_update = datetime.fromisoformat(previous_at)
        inactive_for = observed_at - last_update
        odometer_advance = odometer - float(previous_odometer)
        if (
            (elapsed == 0.0 or inactive_for >= RECOVERY_GAP)
            and 0.0 <= odometer_advance <= MAX_RECOVERY_ODOMETER_ADVANCE_KM
        ):
            # After a long interruption the browser may only know the fractional
            # distance since it restarted. Recover at least the confirmed whole-
            # kilometre movement without affecting normal continuous samples.
            accepted_delta = max(accepted_delta, odometer_advance)

    next_total = float(state.get("total_km", 0.0)) + accepted_delta
    if next_total > MAX_TOTAL_KM:
        raise ConfigurationError("trip distance total is outside the supported range")

    document["distance"] = DistanceState(
        total_km=next_total,
        updated_at=observed_at.isoformat(),
        odometer_km=odometer,
    ).__dict__
    _write_document(document, target)
    result = status_payload(target)
    result["accepted_delta_km"] = accepted_delta
    return result
