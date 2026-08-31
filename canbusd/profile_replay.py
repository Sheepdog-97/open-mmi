"""Deterministic maintained vehicle-profile mapping replay.

Fixture files are human-reviewable CAN examples. They prove that one profile
turns exact frames into the canonical events and persistent status paths it
claims, without requiring SocketCAN or vehicle hardware in CI.
"""

from __future__ import annotations

import json
import math
import stat
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from canbusd.can_runtime import item_matches_bus
from canbusd import status_registry
from canbusd.status_rules import StatusRuleState, evaluate_status_rules, parse_status_rules

SCHEMA_VERSION = 1
FIXTURE_ID = "open-mmi.vehicle-mapping-fixtures"
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_CASES = 512


class VehicleProfileReplayError(ValueError):
    """Raised when a mapping fixture cannot be parsed or replayed."""


def _parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    raise ValueError("value is not an integer")


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VehicleProfileReplayError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise VehicleProfileReplayError(f"non-finite JSON number: {value}")


def load_json(path: Path, *, maximum: int = MAX_FIXTURE_BYTES) -> Mapping[str, Any]:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise VehicleProfileReplayError(f"cannot inspect fixture input: {path}") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
        raise VehicleProfileReplayError(f"fixture input must be a bounded regular file: {path}")
    try:
        document = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VehicleProfileReplayError(f"fixture input is not valid UTF-8 JSON: {path}") from exc
    if not isinstance(document, Mapping):
        raise VehicleProfileReplayError(f"fixture input root must be an object: {path}")
    return document


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, Mapping):
            result.update(_flatten(item, path))
        else:
            result[path] = item
    return result


def _frame_data(value: Any) -> bytes:
    if isinstance(value, str):
        compact = value.replace(" ", "").replace("_", "")
        if len(compact) % 2 or len(compact) > 16:
            raise VehicleProfileReplayError("frame data must contain up to eight bytes")
        try:
            return bytes.fromhex(compact)
        except ValueError as exc:
            raise VehicleProfileReplayError("frame data must be hexadecimal") from exc
    if isinstance(value, list) and len(value) <= 8:
        try:
            parsed = [_parse_int(item) for item in value]
        except ValueError as exc:
            raise VehicleProfileReplayError("frame data contains an invalid byte") from exc
        if any(not 0 <= item <= 255 for item in parsed):
            raise VehicleProfileReplayError("frame data contains an invalid byte")
        return bytes(parsed)
    raise VehicleProfileReplayError("frame data must be hexadecimal text or a byte array")


def _events_for_frame(
    profile: Mapping[str, Any],
    bus: str,
    can_id: int,
    data: bytes,
) -> list[dict[str, Any]]:
    default_bus = str(profile.get("default_bus") or "comfort")
    events: list[dict[str, Any]] = []
    rules = profile.get("rules")
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, Mapping) or not item_matches_bus(rule, bus, default_bus):
            continue
        try:
            if _parse_int(rule.get("id")) != can_id:
                continue
        except ValueError:
            continue

        if "matches" in rule:
            raw_matches = rule.get("matches")
            if not isinstance(raw_matches, list) or not raw_matches:
                continue
            matched = True
            for match in raw_matches:
                if not isinstance(match, Mapping):
                    matched = False
                    break
                try:
                    byte_index = _parse_int(match.get("byte"))
                    expected = _parse_int(match.get("value"))
                except ValueError:
                    matched = False
                    break
                if (
                    byte_index < 0
                    or byte_index >= len(data)
                    or data[byte_index] != expected
                ):
                    matched = False
                    break
            if matched:
                events.append(
                    {
                        "event": str(rule.get("event")),
                        "payload": None,
                    }
                )
            continue

        try:
            byte_index = _parse_int(rule.get("byte", 0))
        except ValueError:
            continue
        if byte_index < 0 or byte_index >= len(data):
            continue
        raw = data[byte_index]
        expected = rule.get("value")
        carries_payload = isinstance(expected, str) and expected.lower() == "any"
        if not carries_payload:
            try:
                if raw != _parse_int(expected):
                    continue
            except ValueError:
                continue
        events.append(
            {
                "event": str(rule.get("event")),
                "payload": raw if carries_payload else None,
            }
        )
    return events


def _presence_rules(profile: Mapping[str, Any], bus: str) -> list[dict[str, Any]]:
    default_bus = str(profile.get("default_bus") or "comfort")
    raw = profile.get("presence")
    result: list[dict[str, Any]] = []
    for rule in raw if isinstance(raw, list) else []:
        if isinstance(rule, Mapping) and item_matches_bus(rule, bus, default_bus):
            result.append(dict(rule))
    return result


def _expected_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise VehicleProfileReplayError("expect.events must be an array")
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"event", "payload"}:
            raise VehicleProfileReplayError(
                "each expected event must contain exactly event and payload"
            )
        event = item["event"]
        if not isinstance(event, str) or not event:
            raise VehicleProfileReplayError("expected event identifier is invalid")
        payload = item["payload"]
        if isinstance(payload, float) and not math.isfinite(payload):
            raise VehicleProfileReplayError("expected event payload is not finite")
        result.append({"event": event, "payload": payload})
    return result


def replay_case(profile: Mapping[str, Any], case: Mapping[str, Any]) -> dict[str, Any]:
    name = case.get("name")
    bus = case.get("bus", profile.get("default_bus", "comfort"))
    frames = case.get("frames")
    expected = case.get("expect")
    if not isinstance(name, str) or not name:
        raise VehicleProfileReplayError("fixture case name is invalid")
    if not isinstance(bus, str) or not bus:
        raise VehicleProfileReplayError(f"fixture case {name!r} bus is invalid")
    if not isinstance(frames, list) or not frames:
        raise VehicleProfileReplayError(f"fixture case {name!r} requires frames")
    if not isinstance(expected, Mapping) or set(expected) != {"events", "statuses"}:
        raise VehicleProfileReplayError(
            f"fixture case {name!r} expect must contain events and statuses"
        )
    expected_events = _expected_events(expected["events"])
    expected_statuses = expected["statuses"]
    if not isinstance(expected_statuses, Mapping):
        raise VehicleProfileReplayError("expect.statuses must be an object")
    expected_statuses = dict(expected_statuses)

    default_bus = str(profile.get("default_bus") or "comfort")
    raw_status = profile.get("status")
    selected_status = [
        dict(rule)
        for rule in (raw_status if isinstance(raw_status, list) else [])
        if isinstance(rule, Mapping) and item_matches_bus(rule, bus, default_bus)
    ]
    grouped_status = parse_status_rules(selected_status)
    status_state = StatusRuleState()
    presence = _presence_rules(profile, bus)
    last_seen: dict[int, float] = {}
    present: dict[int, bool] = {}
    elapsed_ms = 0.0
    actual_events: list[dict[str, Any]] = []
    actual_statuses: dict[str, Any] = {}

    def check_absence() -> None:
        for rule in presence:
            cid = _parse_int(rule["id"])
            if not present.get(cid):
                continue
            timeout = float(_parse_int(rule.get("timeout_ms", 1000)))
            if elapsed_ms - last_seen.get(cid, elapsed_ms) <= timeout:
                continue
            present[cid] = False
            event = rule.get("on_absent")
            if isinstance(event, str):
                actual_events.append({"event": event, "payload": None})
            actual_statuses[str(rule.get("status_path") or "vehicle.present")] = False

    for frame in frames:
        if not isinstance(frame, Mapping):
            raise VehicleProfileReplayError(f"fixture case {name!r} frame is invalid")
        advance = frame.get("advance_ms", 0)
        if isinstance(advance, bool) or not isinstance(advance, (int, float)) or advance < 0:
            raise VehicleProfileReplayError("frame advance_ms must be non-negative")
        elapsed_ms += float(advance)
        check_absence()
        if "id" not in frame:
            if set(frame) != {"advance_ms"}:
                raise VehicleProfileReplayError("timer frame may contain only advance_ms")
            continue
        if set(frame) - {"id", "data", "advance_ms"}:
            raise VehicleProfileReplayError("frame contains unsupported fields")
        can_id = _parse_int(frame["id"])
        data = _frame_data(frame.get("data", ""))
        actual_events.extend(_events_for_frame(profile, bus, can_id, data))
        update = evaluate_status_rules(
            grouped_status.get(can_id, []), data, len(data), state=status_state
        )
        actual_statuses.update(_flatten(update))
        for rule in presence:
            if _parse_int(rule["id"]) != can_id:
                continue
            last_seen[can_id] = elapsed_ms
            if not present.get(can_id):
                present[can_id] = True
                event = rule.get("on_present")
                if isinstance(event, str):
                    actual_events.append({"event": event, "payload": None})
                actual_statuses[str(rule.get("status_path") or "vehicle.present")] = True
    check_absence()

    valid = actual_events == expected_events and actual_statuses == expected_statuses
    return {
        "name": name,
        "valid": valid,
        "actual": {"events": actual_events, "statuses": actual_statuses},
        "expected": {"events": expected_events, "statuses": expected_statuses},
    }


def replay_fixture(
    profile: Mapping[str, Any],
    fixture: Mapping[str, Any],
    *,
    expected_profile_id: Optional[str] = None,
) -> dict[str, Any]:
    if fixture.get("schema_version") != SCHEMA_VERSION or isinstance(
        fixture.get("schema_version"), bool
    ):
        raise VehicleProfileReplayError("fixture schema_version must be integer 1")
    if fixture.get("fixture_id") != FIXTURE_ID:
        raise VehicleProfileReplayError("fixture_id is unsupported")
    profile_id = fixture.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise VehicleProfileReplayError("fixture profile_id is invalid")
    if expected_profile_id is not None and profile_id != expected_profile_id:
        raise VehicleProfileReplayError("fixture profile_id does not match the profile")
    cases = fixture.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise VehicleProfileReplayError("fixture cases must be a non-empty bounded array")
    reports = [replay_case(profile, case) for case in cases if isinstance(case, Mapping)]
    if len(reports) != len(cases):
        raise VehicleProfileReplayError("fixture cases must contain objects")

    expected_events = {
        item["event"]
        for report in reports
        for item in report["expected"]["events"]
    }
    expected_statuses = {
        path
        for report in reports
        for path in report["expected"]["statuses"]
    }
    profile_events: set[str] = set()
    for rule in profile.get("rules", []) if isinstance(profile.get("rules"), list) else []:
        if isinstance(rule, Mapping) and isinstance(rule.get("event"), str):
            profile_events.add(rule["event"])
    for rule in profile.get("presence", []) if isinstance(profile.get("presence"), list) else []:
        if not isinstance(rule, Mapping):
            continue
        for key in ("on_present", "on_absent"):
            if isinstance(rule.get(key), str):
                profile_events.add(rule[key])
    profile_outputs = status_registry.profile_outputs(profile)
    profile_statuses = {
        str(item["path"])
        for item in profile_outputs
        if item.get("role") != "alias" and isinstance(item.get("path"), str)
    }
    allowed_statuses = {
        str(item["path"])
        for item in profile_outputs
        if isinstance(item.get("path"), str)
    }
    missing_events = sorted(profile_events - expected_events)
    missing_statuses = sorted(profile_statuses - expected_statuses)
    unexpected_events = sorted(expected_events - profile_events)
    unexpected_statuses = sorted(expected_statuses - allowed_statuses)
    valid = (
        all(report["valid"] for report in reports)
        and not missing_events
        and not missing_statuses
        and not unexpected_events
        and not unexpected_statuses
    )
    return {
        "valid": valid,
        "profile_id": profile_id,
        "case_count": len(reports),
        "coverage": {
            "events": len(expected_events & profile_events),
            "event_total": len(profile_events),
            "statuses": len(expected_statuses & profile_statuses),
            "status_total": len(profile_statuses),
            "missing_events": missing_events,
            "missing_statuses": missing_statuses,
            "unexpected_events": unexpected_events,
            "unexpected_statuses": unexpected_statuses,
        },
        "cases": reports,
    }


def replay_files(profile_path: Path, fixture_path: Path) -> dict[str, Any]:
    profile = load_json(profile_path, maximum=1024 * 1024)
    fixture = load_json(fixture_path)
    metadata = profile.get("metadata")
    expected_id = metadata.get("id") if isinstance(metadata, Mapping) else None
    return replay_fixture(profile, fixture, expected_profile_id=expected_id)
