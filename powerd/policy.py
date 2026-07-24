"""Power-policy schema, validation, and atomic persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


DEFAULT_POLICY_PATH = Path("/etc/open-mmi/power-policy.json")
DEFAULT_SILENCE_SECONDS = 60.0
DEFAULT_RESUME_GUARD_SECONDS = 30.0


class PowerPolicyError(RuntimeError):
    """The configured host power policy is invalid or unavailable."""


@dataclass(frozen=True)
class PowerPolicy:
    schema_version: int = 1
    enabled: bool = False
    trigger: str = "can_bus_silence"
    silence_seconds: float = DEFAULT_SILENCE_SECONDS
    require_remote_wake: bool = True
    resume_guard_seconds: float = DEFAULT_RESUME_GUARD_SECONDS


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PowerPolicyError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _strict_bool(value: Any, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise PowerPolicyError(f"{field} must be a boolean")
    return value


def _strict_number(value: Any, field: str, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PowerPolicyError(f"{field} must be a number")
    return float(value)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise PowerPolicyError(f"Could not read {path}: {exc}") from exc

    try:
        value = json.loads(text, object_pairs_hook=_unique_object)
    except (ValueError, PowerPolicyError) as exc:
        raise PowerPolicyError(f"Could not parse {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PowerPolicyError(f"{path} must contain a JSON object")
    return value


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> PowerPolicy:
    raw = _read_json(path)
    allowed = {
        "schema_version",
        "enabled",
        "trigger",
        "silence_seconds",
        "require_remote_wake",
        "resume_guard_seconds",
    }
    unsupported = set(raw) - allowed
    if unsupported:
        raise PowerPolicyError(
            f"Unsupported power-policy fields: {sorted(unsupported)}"
        )

    if raw and raw.get("schema_version") != 1:
        raise PowerPolicyError("power policy schema_version must be 1")

    trigger = raw.get("trigger", "can_bus_silence")
    if not isinstance(trigger, str) or trigger != "can_bus_silence":
        raise PowerPolicyError(f"Unsupported power trigger: {trigger}")

    silence = _strict_number(
        raw.get("silence_seconds"),
        "silence_seconds",
        DEFAULT_SILENCE_SECONDS,
    )
    guard = _strict_number(
        raw.get("resume_guard_seconds"),
        "resume_guard_seconds",
        DEFAULT_RESUME_GUARD_SECONDS,
    )
    if not 10.0 <= silence <= 86400.0:
        raise PowerPolicyError("silence_seconds must be between 10 and 86400")
    if not 0.0 <= guard <= 3600.0:
        raise PowerPolicyError("resume_guard_seconds must be between 0 and 3600")

    return PowerPolicy(
        enabled=_strict_bool(raw.get("enabled"), "enabled", False),
        trigger=trigger,
        silence_seconds=silence,
        require_remote_wake=_strict_bool(
            raw.get("require_remote_wake"),
            "require_remote_wake",
            True,
        ),
        resume_guard_seconds=guard,
    )


def policy_payload(policy: PowerPolicy) -> dict[str, Any]:
    payload = asdict(policy)
    for field in ("silence_seconds", "resume_guard_seconds"):
        value = payload[field]
        if isinstance(value, float) and value.is_integer():
            payload[field] = int(value)
    return payload


def write_policy(path: Path, policy: PowerPolicy) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            json.dump(policy_payload(policy), temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
        temporary_name = None
        directory_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def update_policy(
    path: Path,
    *,
    enabled: bool,
    silence_seconds: Optional[float] = None,
) -> PowerPolicy:
    current = load_policy(path)
    updated = replace(
        current,
        enabled=enabled,
        silence_seconds=(
            current.silence_seconds
            if silence_seconds is None
            else float(silence_seconds)
        ),
    )
    # Re-validate the complete result through the public schema path.
    if not 10.0 <= updated.silence_seconds <= 86400.0:
        raise PowerPolicyError("silence_seconds must be between 10 and 86400")
    write_policy(path, updated)
    return updated


def suspend_allowed(
    *,
    policy: PowerPolicy,
    healthy_can: bool,
    wake_ready: bool,
    transaction_busy: bool,
    observed_frame: bool,
    silent_for: float,
    awake_for: float,
) -> bool:
    return (
        policy.enabled
        and healthy_can
        and (wake_ready or not policy.require_remote_wake)
        and not transaction_busy
        and observed_frame
        and silent_for >= policy.silence_seconds
        and awake_for >= policy.resume_guard_seconds
    )
