"""Read-only vehicle setup discovery, validation and runtime status.

The browser, CLI and the later privileged configuration coordinator share this
module.  Every catalogue lookup resolves a source class and identifier beneath a
fixed root; callers never supply a filesystem path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from canbusd.can_runtime import resolve_can_runtime
from canbusd import action_registry as vehicle_actions
from canbusd import event_registry as vehicle_events
from canbusd import profile_catalogue as maintained_profiles
from canbusd import status_registry as vehicle_statuses
from ui import vehicle_configuration


API_VERSION = 1
DEFAULT_BUS = "comfort"
DEFAULT_INTERFACE = "can0"
MAX_PROFILE_BYTES = 1024 * 1024
MAX_BINDINGS_BYTES = 256 * 1024
MAX_STATUS_BYTES = 2 * 1024 * 1024
MAX_CAN_ID = 0x1FFFFFFF
MAX_EVENT_LENGTH = 128
MAX_STATUS_PATH_LENGTH = 192

IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
PYTHON_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
INTERFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")
STATUS_TYPES = {
    "raw",
    "percent",
    "scaled",
    "bool",
    "enum",
    "bitfield",
    "signed_magnitude",
    "steering_angle",
    "u16le",
    "u24le",
    "u32le",
    "uint_le",
}
PROVISIONING_MODES = {"manual", "udev"}
RUNTIME_ENVIRONMENT_KEYS = {
    "OPEN_MMI_VEHICLE",
    "OPEN_MMI_BINDINGS",
    "OPEN_MMI_VEHICLE_CONFIG",
    "OPEN_MMI_BINDINGS_FILE",
    "OPEN_MMI_CAN_BUS",
    "OPEN_MMI_CAN_INTERFACE",
}


class VehicleSetupError(RuntimeError):
    """A fail-closed vehicle setup inspection error."""


@dataclass(frozen=True)
class CatalogueRoots:
    """Trusted roots for maintained and user-owned catalogue content."""

    maintained: Path
    custom: Path
    development_mode: bool = False


def default_roots(environment: Optional[Mapping[str, str]] = None) -> CatalogueRoots:
    env = dict(os.environ if environment is None else environment)
    development_root = str(
        env.get("OPEN_MMI_VEHICLE_SETUP_DEVELOPMENT_ROOT") or ""
    ).strip()
    installed_root = str(env.get("OPEN_MMI_INSTALL_DIR") or "/opt/open-mmi")
    config_root = str(
        env.get("OPEN_MMI_CONFIG_DIR")
        or Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "open-mmi"
    )
    return CatalogueRoots(
        maintained=Path(development_root or installed_root).expanduser(),
        custom=Path(config_root).expanduser(),
        development_mode=bool(development_root),
    )


def _issue(level: str, code: str, path: str, message: str) -> dict[str, str]:
    return {"level": level, "code": code, "path": path, "message": message}


def _validation(issues: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    ordered = [dict(issue) for issue in issues]
    errors = [issue for issue in ordered if issue["level"] == "error"]
    warnings = [issue for issue in ordered if issue["level"] == "warning"]
    return {"valid": not errors, "errors": errors, "warnings": warnings}


def validate_identifier(value: Any, *, path: str = "id") -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        issues.append(
            _issue(
                "error",
                "invalid-identifier",
                path,
                "must match ^[a-z0-9][a-z0-9_-]{0,63}$",
            )
        )
    return _validation(issues)


def _catalogue_path(
    roots: CatalogueRoots,
    kind: str,
    source: str,
    identifier: str,
) -> tuple[Path, Path]:
    identifier_validation = validate_identifier(identifier)
    if not identifier_validation["valid"]:
        raise VehicleSetupError("Invalid catalogue identifier")
    if source == "maintained":
        root = roots.maintained
    elif source == "custom":
        root = roots.custom
    else:
        raise VehicleSetupError("Catalogue source must be maintained or custom")

    if kind == "profile":
        if source == "maintained":
            try:
                resolved = maintained_profiles.resolve_profile(root, identifier)
            except maintained_profiles.VehicleProfileCatalogueError as exc:
                raise VehicleSetupError(str(exc)) from exc
            return root, resolved["path"]
        return root, root / "vehicles" / identifier / "config.json"
    if kind == "bindings":
        return root, root / "bindings" / f"{identifier}.json"
    raise VehicleSetupError("Catalogue kind must be profile or bindings")


def _reject_symlink_components(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise VehicleSetupError("Catalogue path escapes its fixed root") from exc

    current = root
    components = [root, *(root / Path(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1))]
    for current in components:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise VehicleSetupError("Catalogue path cannot be inspected") from exc
        if stat.S_ISLNK(mode):
            raise VehicleSetupError("Symlinked catalogue paths are not trusted")


def resolve_catalogue_path(
    roots: CatalogueRoots,
    kind: str,
    source: str,
    identifier: str,
) -> Path:
    """Resolve one identity beneath a fixed root without following symlinks."""

    root, target = _catalogue_path(roots, kind, source, identifier)
    _reject_symlink_components(root, target)
    return target


def _parse_int(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    raise ValueError("value is not an integer")


def _bounded_text(value: Any, *, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not any(ord(character) < 32 for character in value)
    )


def _validate_can_id(
    value: Any,
    path: str,
    issues: list[dict[str, str]],
) -> None:
    try:
        parsed = _parse_int(value)
    except (TypeError, ValueError):
        parsed = -1
    if not 0 <= parsed <= MAX_CAN_ID:
        issues.append(
            _issue("error", "invalid-can-id", path, "must be a CAN identifier from 0 to 0x1FFFFFFF")
        )


def _validate_byte(
    value: Any,
    path: str,
    issues: list[dict[str, str]],
) -> None:
    try:
        parsed = _parse_int(value)
    except (TypeError, ValueError):
        parsed = -1
    if not 0 <= parsed <= 7:
        issues.append(_issue("error", "invalid-byte-index", path, "must be an integer from 0 to 7"))


def _validate_bus_reference(
    value: Any,
    path: str,
    declared: set[str],
    issues: list[dict[str, str]],
) -> None:
    values: Sequence[Any]
    if isinstance(value, list):
        values = value
    else:
        values = [value]
    if not values or any(not isinstance(item, str) for item in values):
        issues.append(_issue("error", "invalid-bus-reference", path, "must name one bus or a list of buses"))
        return
    for item in values:
        if item not in declared:
            issues.append(_issue("error", "undeclared-bus", path, f"references undeclared bus {item!r}"))


def _validate_event_reference(
    value: Any,
    *,
    path: str,
    registry: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> Optional[Mapping[str, Any]]:
    if not _bounded_text(value, maximum=MAX_EVENT_LENGTH) or not vehicle_events.EVENT_NAME_RE.fullmatch(value):
        issues.append(
            _issue(
                "error",
                "invalid-event",
                path,
                "must be a canonical Open MMI event identifier",
            )
        )
        return None
    if value in registry["events"]:
        definition = registry["events"][value]
        if definition.get("status") == "deprecated":
            issues.append(
                _issue(
                    "warning",
                    "deprecated-event",
                    path,
                    "canonical event is deprecated and should be migrated",
                )
            )
        return definition
    alias = registry["aliases"].get(value)
    if alias:
        issues.append(
            _issue(
                "error",
                "deprecated-event-alias",
                path,
                f"must use canonical event {alias['event']!r}",
            )
        )
        return None
    issues.append(
        _issue(
            "error",
            "unregistered-event",
            path,
            (
                "is not yet registered; search the shared human vocabulary with "
                "'open-mmi-config vehicle-setup events --search <meaning>' and, "
                "when no existing event fits, propose a universal registry entry "
                "in the same pull request"
            ),
        )
    )
    return None


def _validate_status_reference(
    value: Any,
    *,
    path: str,
    value_type: str,
    registry: Mapping[str, Any],
    issues: list[dict[str, str]],
    enum_values: Optional[Sequence[str]] = None,
    allow_alias: bool = False,
) -> Optional[Mapping[str, Any]]:
    if (
        not _bounded_text(value, maximum=MAX_STATUS_PATH_LENGTH)
        or not vehicle_statuses.STATUS_PATH_RE.fullmatch(value)
    ):
        issues.append(
            _issue(
                "error",
                "invalid-status-path",
                path,
                "must be a lowercase dot-separated canonical status path",
            )
        )
        return None
    state, canonical = vehicle_statuses.status_state(value, registry)
    if state == "alias" and canonical is not None:
        if not allow_alias:
            issues.append(
                _issue(
                    "error",
                    "deprecated-status-alias",
                    path,
                    f"must use canonical status path {canonical!r}",
                )
            )
            return None
        issues.append(
            _issue(
                "warning",
                "deprecated-status-alias",
                path,
                f"compatibility output aliases canonical status path {canonical!r}",
            )
        )
    elif state == "unknown":
        issues.append(
            _issue(
                "error",
                "unregistered-status",
                path,
                (
                    "is not yet registered; search the shared human vocabulary with "
                    "'open-mmi-config vehicle-setup statuses --search <meaning>' and, "
                    "when no existing path fits, propose a universal registry entry "
                    "in the same pull request"
                ),
            )
        )
        return None
    output: dict[str, Any] = {
        "path": value,
        "value_type": value_type,
        "role": "alias" if allow_alias else "primary",
    }
    if enum_values is not None:
        output["enum_values"] = list(enum_values)
    try:
        return vehicle_statuses.require_output(output, registry=registry)
    except vehicle_statuses.VehicleStatusRegistryError as exc:
        code = (
            "status-enum-mismatch"
            if "enum values" in str(exc)
            else "status-type-mismatch"
        )
        issues.append(_issue("error", code, path, str(exc)))
        return None


def _validate_profile_item(
    item: Any,
    path: str,
    kind: str,
    default_bus: str,
    declared_buses: set[str],
    event_registry: Mapping[str, Any],
    status_registry: Mapping[str, Any],
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(item, Mapping):
        issues.append(_issue("error", "invalid-rule", path, "must be an object"))
        return
    if "id" not in item:
        issues.append(_issue("error", "missing-can-id", f"{path}.id", "is required"))
    else:
        _validate_can_id(item["id"], f"{path}.id", issues)

    if "bus" in item:
        _validate_bus_reference(item["bus"], f"{path}.bus", declared_buses, issues)
    elif default_bus not in declared_buses:
        issues.append(_issue("error", "undeclared-default-bus", f"{path}.bus", "implicit default bus is not declared"))

    if kind == "rule":
        _validate_byte(item.get("byte", 0), f"{path}.byte", issues)
        event_definition = _validate_event_reference(
            item.get("event"),
            path=f"{path}.event",
            registry=event_registry,
            issues=issues,
        )
        value = item.get("value")
        carries_payload = isinstance(value, str) and value.lower() == "any"
        if event_definition is not None:
            expects_payload = event_definition["payload"]["type"] != "none"
            if carries_payload and not expects_payload:
                issues.append(
                    _issue(
                        "error",
                        "unexpected-event-payload",
                        f"{path}.value",
                        "'any' forwards a CAN byte but this event declares no payload",
                    )
                )
            elif expects_payload and not carries_payload:
                issues.append(
                    _issue(
                        "error",
                        "missing-event-payload",
                        f"{path}.value",
                        "this event requires the decoded value payload",
                    )
                )
        if not carries_payload:
            try:
                parsed = _parse_int(value)
            except (TypeError, ValueError):
                parsed = -1
            if not 0 <= parsed <= 255:
                issues.append(_issue("error", "invalid-rule-value", f"{path}.value", "must be 0..255 or 'any'"))
        return

    if kind == "presence":
        try:
            timeout = _parse_int(item.get("timeout_ms", 1000))
        except (TypeError, ValueError):
            timeout = 0
        if not 1 <= timeout <= 86_400_000:
            issues.append(_issue("error", "invalid-timeout", f"{path}.timeout_ms", "must be between 1 and 86400000"))
        for key in ("on_present", "on_absent"):
            if key in item and item[key] is not None:
                event_definition = _validate_event_reference(
                    item[key],
                    path=f"{path}.{key}",
                    registry=event_registry,
                    issues=issues,
                )
                if (
                    event_definition is not None
                    and event_definition["payload"]["type"] != "none"
                ):
                    issues.append(
                        _issue(
                            "error",
                            "missing-event-payload",
                            f"{path}.{key}",
                            "presence transitions cannot supply this event payload",
                        )
                    )
        status_path = item.get("status_path", "vehicle.present")
        if not _bounded_text(status_path, maximum=MAX_STATUS_PATH_LENGTH):
            issues.append(_issue("error", "invalid-status-path", f"{path}.status_path", "must be a bounded string"))
        else:
            _validate_status_reference(
                status_path,
                path=f"{path}.status_path",
                value_type="boolean",
                registry=status_registry,
                issues=issues,
            )
        return

    rule_type = item.get("type", "raw")
    if rule_type not in STATUS_TYPES:
        issues.append(_issue("error", "unsupported-status-type", f"{path}.type", "is not a supported status decoder"))
    if not _bounded_text(item.get("path"), maximum=MAX_STATUS_PATH_LENGTH):
        issues.append(_issue("error", "invalid-status-path", f"{path}.path", "must be a non-empty bounded string"))

    if "byte" in item:
        _validate_byte(item["byte"], f"{path}.byte", issues)
    if "start_byte" in item:
        _validate_byte(item["start_byte"], f"{path}.start_byte", issues)
    if "bytes" in item:
        if not isinstance(item["bytes"], list) or not item["bytes"]:
            issues.append(_issue("error", "invalid-byte-list", f"{path}.bytes", "must be a non-empty list"))
        else:
            for index, value in enumerate(item["bytes"]):
                _validate_byte(value, f"{path}.bytes[{index}]", issues)
    if rule_type == "bool" and "true" not in item:
        issues.append(_issue("error", "missing-true-value", f"{path}.true", "is required for bool rules"))
    elif rule_type == "bool":
        for key in ("true", "false", "mask"):
            if key not in item:
                continue
            try:
                parsed = _parse_int(item[key])
            except (TypeError, ValueError):
                parsed = -1
            if not 0 <= parsed <= 255:
                issues.append(_issue("error", "invalid-status-value", f"{path}.{key}", "must be between 0 and 255"))
    if rule_type == "enum":
        values = item.get("values")
        if not isinstance(values, Mapping) or not values:
            issues.append(_issue("error", "invalid-enum-values", f"{path}.values", "must be a non-empty object"))
        else:
            for key in values:
                try:
                    parsed = _parse_int(key)
                except (TypeError, ValueError):
                    parsed = -1
                if not 0 <= parsed <= 255:
                    issues.append(_issue("error", "invalid-enum-key", f"{path}.values.{key}", "must be between 0 and 255"))
    if rule_type == "bitfield":
        fields_present = False
        for key in ("fields", "equals"):
            values = item.get(key)
            if values is None:
                continue
            if not isinstance(values, Mapping):
                issues.append(_issue("error", "invalid-bitfield-values", f"{path}.{key}", "must be an object"))
                continue
            fields_present = fields_present or bool(values)
            for name, value in values.items():
                valid_entry = _bounded_text(name, maximum=64)
                if key == "equals" and isinstance(value, Mapping):
                    if set(value) - {"mask", "value"} or "value" not in value:
                        valid_entry = False
                    else:
                        try:
                            parsed_value = _parse_int(value["value"])
                            parsed_mask = (
                                _parse_int(value["mask"])
                                if "mask" in value
                                else 0xFF
                            )
                        except (TypeError, ValueError):
                            parsed_value = -1
                            parsed_mask = -1
                        valid_entry = (
                            valid_entry
                            and 0 <= parsed_value <= 255
                            and 0 <= parsed_mask <= 255
                        )
                else:
                    try:
                        parsed = _parse_int(value)
                    except (TypeError, ValueError):
                        parsed = -1
                    valid_entry = valid_entry and 0 <= parsed <= 255
                if not valid_entry:
                    issues.append(_issue("error", "invalid-bitfield-entry", f"{path}.{key}.{name}", "must have a bounded name and 0..255 value"))
        if not fields_present:
            issues.append(_issue("error", "empty-bitfield", path, "must declare fields or equals"))
    fixed_lengths = {"u16le": 2, "u24le": 3, "u32le": 4}
    if rule_type in fixed_lengths:
        try:
            start_byte = _parse_int(item.get("start_byte", item.get("byte", 0)))
        except (TypeError, ValueError):
            start_byte = -1
        if start_byte < 0 or start_byte + fixed_lengths[rule_type] > 8:
            issues.append(_issue("error", "status-width-exceeds-frame", path, "selected bytes exceed an eight-byte CAN frame"))
    if rule_type == "uint_le":
        try:
            length = _parse_int(item.get("length", 1))
        except (TypeError, ValueError):
            length = 0
        if not 1 <= length <= 8:
            issues.append(_issue("error", "invalid-integer-length", f"{path}.length", "must be between 1 and 8"))
        else:
            try:
                start_byte = _parse_int(item.get("start_byte", item.get("byte", 0)))
            except (TypeError, ValueError):
                start_byte = -1
            if start_byte < 0 or start_byte + length > 8:
                issues.append(_issue("error", "status-width-exceeds-frame", path, "selected bytes exceed an eight-byte CAN frame"))

    for output in vehicle_statuses.rule_outputs(item):
        _validate_status_reference(
            output["path"],
            path=f"{path}.{output['role']}",
            value_type=output["value_type"],
            registry=status_registry,
            issues=issues,
            enum_values=output.get("enum_values"),
            allow_alias=output["role"] == "alias",
        )


def validate_profile(
    document: Any,
    *,
    event_registry: Optional[Mapping[str, Any]] = None,
    status_registry: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    selected_event_registry = (
        vehicle_events.normalize_registry(event_registry)
        if event_registry is not None
        else vehicle_events.registry_payload()
    )
    selected_status_registry = (
        vehicle_statuses.normalize_registry(status_registry)
        if status_registry is not None
        else vehicle_statuses.registry_payload()
    )
    if not isinstance(document, Mapping):
        return _validation([_issue("error", "invalid-document", "$", "profile must contain a JSON object")])

    default_bus = document.get("default_bus", DEFAULT_BUS)
    if not isinstance(default_bus, str) or not IDENTIFIER_RE.fullmatch(default_bus):
        issues.append(_issue("error", "invalid-default-bus", "default_bus", "must be a valid identifier"))
        default_bus = DEFAULT_BUS

    raw_buses = document.get("can_buses")
    if raw_buses is None:
        raw_buses = {default_bus: {}}
        issues.append(_issue("warning", "legacy-bus-fallback", "can_buses", "missing bus metadata uses the documented single-bus fallback"))
    elif not isinstance(raw_buses, Mapping):
        issues.append(_issue("error", "invalid-can-buses", "can_buses", "must be an object"))
        raw_buses = {}

    declared_buses: set[str] = set()
    for raw_name, metadata in raw_buses.items():
        path = f"can_buses.{raw_name}"
        if not isinstance(raw_name, str) or not IDENTIFIER_RE.fullmatch(raw_name):
            issues.append(_issue("error", "invalid-bus-name", path, "must be a valid identifier"))
            continue
        declared_buses.add(raw_name)
        if not isinstance(metadata, Mapping):
            issues.append(_issue("error", "invalid-bus-metadata", path, "must be an object"))
            continue
        interface = metadata.get("interface", metadata.get("tested_interface", DEFAULT_INTERFACE))
        if not isinstance(interface, str) or not INTERFACE_RE.fullmatch(interface):
            issues.append(_issue("error", "invalid-interface", f"{path}.interface", "must be a valid Linux interface name"))
        if "bitrate" in metadata:
            try:
                bitrate = _parse_int(metadata["bitrate"])
            except (TypeError, ValueError):
                bitrate = 0
            if not 1 <= bitrate <= 10_000_000:
                issues.append(_issue("error", "invalid-bitrate", f"{path}.bitrate", "must be between 1 and 10000000"))
        provisioning = metadata.get("provisioning", "manual")
        if provisioning not in PROVISIONING_MODES:
            issues.append(_issue("error", "invalid-provisioning", f"{path}.provisioning", "must be manual or udev"))
        if "bring_up" in metadata and not isinstance(metadata["bring_up"], bool):
            issues.append(_issue("error", "invalid-bring-up", f"{path}.bring_up", "must be boolean"))

    if default_bus not in declared_buses:
        issues.append(_issue("warning", "default-bus-fallback", "default_bus", "default bus is not declared and will use legacy fallback metadata"))
        declared_buses.add(default_bus)

    for key, kind in (("rules", "rule"), ("presence", "presence"), ("status", "status")):
        items = document.get(key, [])
        if not isinstance(items, list):
            issues.append(_issue("error", "invalid-rule-collection", key, "must be an array"))
            continue
        for index, item in enumerate(items):
            _validate_profile_item(
                item,
                f"{key}[{index}]",
                kind,
                default_bus,
                declared_buses,
                selected_event_registry,
                selected_status_registry,
                issues,
            )

    return _validation(issues)


def _validate_legacy_binding(
    action: Mapping[str, Any],
    *,
    path: str,
    issues: list[dict[str, str]],
) -> None:
    unsupported = sorted(set(action) - {"module", "func", "args"})
    for key in unsupported:
        issues.append(
            _issue(
                "error",
                "unsupported-binding-field",
                f"{path}.{key}",
                "is not supported",
            )
        )
    for key in ("module", "func"):
        if not isinstance(action.get(key), str) or not PYTHON_IDENTIFIER_RE.fullmatch(
            action[key]
        ):
            issues.append(
                _issue(
                    "error",
                    f"invalid-{key}",
                    f"{path}.{key}",
                    "must be a Python identifier",
                )
            )
    args = action.get("args", [])
    if not isinstance(args, list) or len(args) > 16:
        issues.append(
            _issue(
                "error",
                "invalid-arguments",
                f"{path}.args",
                "must be an array of at most 16 values",
            )
        )
    else:
        for index, value in enumerate(args):
            if (
                isinstance(value, (list, dict))
                or (isinstance(value, str) and len(value.encode("utf-8")) > 4096)
                or (isinstance(value, float) and not math.isfinite(value))
            ):
                issues.append(
                    _issue(
                        "error",
                        "invalid-argument",
                        f"{path}.args[{index}]",
                        "must be a bounded JSON scalar",
                    )
                )


def validate_bindings(
    document: Any,
    *,
    event_registry: Optional[Mapping[str, Any]] = None,
    action_registry: Optional[Mapping[str, Any]] = None,
    maintained: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    selected_event_registry = (
        vehicle_events.normalize_registry(event_registry)
        if event_registry is not None
        else vehicle_events.registry_payload()
    )
    selected_action_registry = (
        vehicle_actions.normalize_registry(action_registry)
        if action_registry is not None
        else vehicle_actions.registry_payload()
    )
    if not isinstance(document, Mapping):
        return _validation(
            [_issue("error", "invalid-document", "$", "bindings must contain a JSON object")]
        )

    for event, action in document.items():
        path = f"bindings.{event}"
        event_definition = _validate_event_reference(
            event,
            path=path,
            registry=selected_event_registry,
            issues=issues,
        )
        if not isinstance(action, Mapping):
            issues.append(_issue("error", "invalid-binding", path, "must be an object"))
            continue

        if vehicle_actions.is_legacy_binding(action):
            level = "error" if maintained else "warning"
            issues.append(
                _issue(
                    level,
                    "legacy-action-schema",
                    path,
                    (
                        "maintained bindings must use a canonical action identifier"
                        if maintained
                        else (
                            "module/func bindings remain supported for custom compatibility "
                            "but should migrate to a canonical action identifier"
                        )
                    ),
                )
            )
            _validate_legacy_binding(action, path=path, issues=issues)
            continue

        carries_payload = (
            None
            if event_definition is None
            else event_definition["payload"]["type"] != "none"
        )
        try:
            vehicle_actions.resolve_binding(
                action,
                carries_event_payload=carries_payload,
                registry=selected_action_registry,
                allow_legacy=False,
            )
        except vehicle_actions.VehicleActionRegistryError as exc:
            issues.append(_issue("error", exc.code, path, str(exc)))

    return _validation(issues)


def compatibility_report(profile: Any, bindings: Any) -> dict[str, Any]:
    emitted: list[str] = []
    if isinstance(profile, Mapping):
        for item in profile.get("rules", []) if isinstance(profile.get("rules", []), list) else []:
            if isinstance(item, Mapping) and isinstance(item.get("event"), str):
                emitted.append(item["event"])
        for item in profile.get("presence", []) if isinstance(profile.get("presence", []), list) else []:
            if not isinstance(item, Mapping):
                continue
            for key in ("on_present", "on_absent"):
                if isinstance(item.get(key), str):
                    emitted.append(item[key])
    bound = sorted(str(key) for key in bindings) if isinstance(bindings, Mapping) else []
    emitted_set = set(emitted)
    bound_set = set(bound)
    return {
        "emitted_and_bound": sorted(emitted_set & bound_set),
        "emitted_unbound": sorted(emitted_set - bound_set),
        "bound_unemitted": sorted(bound_set - emitted_set),
        "duplicate_emitted": sorted(key for key, count in Counter(emitted).items() if count > 1),
    }


def _unique_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _read_document(path: Path, maximum_bytes: int) -> tuple[Any, str]:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise VehicleSetupError("Catalogue document is missing") from exc
    except OSError as exc:
        raise VehicleSetupError("Catalogue document cannot be inspected") from exc
    if not stat.S_ISREG(mode):
        raise VehicleSetupError("Catalogue document must be a regular file")
    try:
        size = path.stat().st_size
        if size > maximum_bytes:
            raise VehicleSetupError("Catalogue document exceeds the size limit")
        content = path.read_bytes()
    except OSError as exc:
        raise VehicleSetupError("Catalogue document cannot be read") from exc
    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    try:
        return (
            json.loads(
                content.decode("utf-8"),
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            ),
            revision,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VehicleSetupError("Catalogue document is not valid UTF-8 JSON") from exc


def _display_name(identifier: str) -> str:
    words = re.split(r"[-_]", identifier)
    return " ".join(word.upper() if len(word) <= 2 and any(character.isdigit() for character in word) else word.capitalize() for word in words)


def _invalid_entry(source: str, identifier: str, message: str, code: str = "invalid-entry") -> dict[str, Any]:
    return {
        "source": source,
        "id": identifier,
        "display_name": _display_name(identifier),
        "valid": False,
        "revision": "",
        "validation": _validation([_issue("error", code, "$", message)]),
    }


def _canonical_profile_identity(
    roots: CatalogueRoots,
    source: str,
    identifier: str,
) -> tuple[str, list[str], str]:
    if source != "maintained":
        return identifier, [], "canonical"
    try:
        resolved = maintained_profiles.resolve_profile(roots.maintained, identifier)
    except maintained_profiles.VehicleProfileCatalogueError as exc:
        raise VehicleSetupError(str(exc)) from exc
    return resolved["id"], list(resolved["aliases"]), str(resolved["requested_status"])


def _profile_entry(roots: CatalogueRoots, source: str, identifier: str) -> dict[str, Any]:
    aliases: list[str] = []
    requested_status = "canonical"
    try:
        canonical_id, aliases, requested_status = _canonical_profile_identity(
            roots, source, identifier
        )
        path = resolve_catalogue_path(roots, "profile", source, canonical_id)
        identifier = canonical_id
        document, revision = _read_document(path, MAX_PROFILE_BYTES)
    except VehicleSetupError as exc:
        return _invalid_entry(source, identifier, str(exc))
    validation = validate_profile(document)
    buses: list[dict[str, Any]] = []
    if isinstance(document, Mapping):
        default_bus = document.get("default_bus", DEFAULT_BUS)
        raw_buses = document.get("can_buses")
        if not isinstance(raw_buses, Mapping):
            raw_buses = {default_bus: {}}
        for name in sorted(raw_buses):
            metadata = raw_buses[name]
            metadata = metadata if isinstance(metadata, Mapping) else {}
            bitrate = metadata.get("bitrate")
            try:
                bitrate = _parse_int(bitrate) if bitrate is not None else None
            except (TypeError, ValueError):
                bitrate = None
            buses.append(
                {
                    "name": str(name),
                    "interface": str(metadata.get("interface") or metadata.get("tested_interface") or DEFAULT_INTERFACE),
                    "bitrate": bitrate,
                    "provisioning": str(metadata.get("provisioning") or "manual"),
                }
            )
    else:
        default_bus = DEFAULT_BUS
    metadata = document.get("metadata") if isinstance(document, Mapping) else None
    metadata = metadata if isinstance(metadata, Mapping) else {}
    qualification = metadata.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    display_name = metadata.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        display_name = _display_name(identifier)
    return {
        "source": source,
        "id": identifier,
        "aliases": aliases,
        "requested_status": requested_status,
        "display_name": display_name,
        "manufacturer": str(metadata.get("manufacturer") or ""),
        "model": str(metadata.get("model") or ""),
        "generation": str(metadata.get("generation") or ""),
        "platform": str(metadata.get("platform") or ""),
        "maturity": (
            "custom"
            if source == "custom"
            else str(metadata.get("maturity") or "unspecified")
        ),
        "qualification_level": (
            "unqualified"
            if source == "custom"
            else str(qualification.get("level") or "none")
        ),
        "last_tested": (
            None if source == "custom" else qualification.get("last_tested")
        ),
        "valid": validation["valid"],
        "revision": revision,
        "default_bus": default_bus,
        "buses": buses,
        "event_count": len(document.get("rules", [])) if isinstance(document, Mapping) and isinstance(document.get("rules", []), list) else 0,
        "presence_rule_count": len(document.get("presence", [])) if isinstance(document, Mapping) and isinstance(document.get("presence", []), list) else 0,
        "status_rule_count": len(document.get("status", [])) if isinstance(document, Mapping) and isinstance(document.get("status", []), list) else 0,
        "validation": validation,
    }


def _bindings_entry(roots: CatalogueRoots, source: str, identifier: str) -> dict[str, Any]:
    try:
        path = resolve_catalogue_path(roots, "bindings", source, identifier)
        document, revision = _read_document(path, MAX_BINDINGS_BYTES)
    except VehicleSetupError as exc:
        return _invalid_entry(source, identifier, str(exc))
    validation = validate_bindings(document, maintained=(source == "maintained"))
    return {
        "source": source,
        "id": identifier,
        "display_name": _display_name(identifier),
        "valid": validation["valid"],
        "revision": revision,
        "binding_count": len(document) if isinstance(document, Mapping) else 0,
        "validation": validation,
    }


def _discover_identifiers(
    root: Path,
    kind: str,
    source: str,
) -> tuple[list[str], list[dict[str, str]]]:
    directory = root / ("vehicles" if kind == "profile" else "bindings")
    if not directory.exists():
        return [], []
    if directory.is_symlink() or not directory.is_dir():
        return [], [_issue("error", "untrusted-catalogue-root", kind, "catalogue directory is not a trusted directory")]
    if kind == "profile" and source == "maintained":
        try:
            entries = maintained_profiles.profile_entries(root)
        except maintained_profiles.VehicleProfileCatalogueError as exc:
            return [], [_issue("error", "invalid-maintained-catalogue", kind, str(exc))]
        return [str(entry["id"]) for entry in entries], []
    try:
        children = sorted(directory.iterdir(), key=lambda item: item.name)
    except OSError:
        return [], [_issue("error", "unreadable-catalogue", kind, "catalogue directory cannot be read")]
    if kind == "profile":
        identifiers = [
            child.name
            for child in children
            if child.name != "__pycache__"
            and not child.name.startswith(".")
            and (
                child.is_symlink()
                or (child.is_dir() and (child / "config.json").exists())
            )
        ]
    else:
        identifiers = [child.name[:-5] for child in children if child.name.endswith(".json")]
    return identifiers, []


def catalogue_payload(roots: Optional[CatalogueRoots] = None) -> dict[str, Any]:
    selected_roots = roots or default_roots()
    profiles: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for source, root in (("maintained", selected_roots.maintained), ("custom", selected_roots.custom)):
        profile_ids, profile_issues = _discover_identifiers(root, "profile", source)
        binding_ids, binding_issues = _discover_identifiers(root, "bindings", source)
        issues.extend(profile_issues)
        issues.extend(binding_issues)
        for identifier in profile_ids:
            if validate_identifier(identifier)["valid"]:
                profiles.append(_profile_entry(selected_roots, source, identifier))
            else:
                profiles.append(_invalid_entry(source, identifier, "Invalid catalogue identifier", "invalid-identifier"))
        for identifier in binding_ids:
            if validate_identifier(identifier)["valid"]:
                bindings.append(_bindings_entry(selected_roots, source, identifier))
            else:
                bindings.append(_invalid_entry(source, identifier, "Invalid catalogue identifier", "invalid-identifier"))
    key = lambda entry: (0 if entry["source"] == "maintained" else 1, entry["id"])
    profiles.sort(key=key)
    bindings.sort(key=key)
    return {
        "development_mode": selected_roots.development_mode,
        "profiles": profiles,
        "bindings": bindings,
        "issues": issues,
    }


def discover_interfaces(sys_class_net: Path = Path("/sys/class/net")) -> list[dict[str, Any]]:
    interfaces: list[dict[str, Any]] = []
    try:
        entries = sorted(sys_class_net.iterdir(), key=lambda item: item.name)
    except (FileNotFoundError, OSError):
        return interfaces
    for entry in entries:
        if not INTERFACE_RE.fullmatch(entry.name):
            continue
        try:
            if (entry / "type").read_text(encoding="utf-8").strip() != "280":
                continue
            operstate = (entry / "operstate").read_text(encoding="utf-8").strip()
            flags_path = entry / "flags"
            flags = int(flags_path.read_text(encoding="utf-8").strip(), 0) if flags_path.exists() else None
            bitrate_path = entry / "can" / "bitrate"
            bitrate = int(bitrate_path.read_text(encoding="utf-8").strip()) if bitrate_path.exists() else None
        except (OSError, ValueError):
            continue
        interfaces.append(
            {
                "name": entry.name,
                "kind": "socketcan",
                "present": True,
                "up": bool(flags & 1) if flags is not None else operstate == "up",
                "operstate": operstate,
                "configured_bitrate": bitrate,
                "last_frame_age_seconds": None,
            }
        )
    return interfaces


def _default_dropin_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "canbusd.service.d" / "10-can-runtime.conf"


def read_runtime_environment(path: Optional[Path] = None) -> dict[str, str]:
    target = path or _default_dropin_path()
    if target.is_symlink():
        raise VehicleSetupError("Runtime drop-in may not be a symlink")
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise VehicleSetupError("Runtime drop-in cannot be read") from exc
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("Environment="):
            continue
        try:
            entries = shlex.split(stripped[len("Environment=") :], posix=True)
        except ValueError:
            continue
        for entry in entries:
            if "=" not in entry:
                continue
            key, value = entry.split("=", 1)
            if key in RUNTIME_ENVIRONMENT_KEYS:
                values[key] = value
    return values


def _identity_from_environment(
    roots: CatalogueRoots,
    kind: str,
    identifier: str,
    explicit_path: str,
) -> dict[str, Any]:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        for source in ("maintained", "custom"):
            try:
                resolved_id = identifier
                if kind == "profile" and source == "maintained":
                    resolved_id, _aliases, _status = _canonical_profile_identity(
                        roots, source, identifier
                    )
                expected = resolve_catalogue_path(roots, kind, source, resolved_id)
            except VehicleSetupError:
                continue
            if candidate == expected:
                return {"source": source, "id": resolved_id}
        return {"source": "external", "id": identifier}
    if kind == "profile":
        try:
            identifier, _aliases, _status = _canonical_profile_identity(
                roots, "maintained", identifier
            )
        except VehicleSetupError:
            pass
    return {"source": "maintained", "id": identifier}


def _find_entry(entries: Iterable[Mapping[str, Any]], identity: Mapping[str, str]) -> Optional[dict[str, Any]]:
    return next(
        (
            dict(entry)
            for entry in entries
            if entry.get("source") == identity.get("source") and entry.get("id") == identity.get("id")
        ),
        None,
    )


def _load_identity_document(
    roots: CatalogueRoots,
    kind: str,
    identity: Mapping[str, str],
) -> Any:
    if identity.get("source") not in {"maintained", "custom"}:
        return {}
    try:
        path = resolve_catalogue_path(roots, kind, identity["source"], identity["id"])
        document, _revision = _read_document(path, MAX_PROFILE_BYTES if kind == "profile" else MAX_BINDINGS_BYTES)
        return document
    except (KeyError, VehicleSetupError):
        return {}


def _default_status_path(environment: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environment is None else environment
    explicit = str(env.get("OPEN_MMI_STATUS_PATH") or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    runtime_dir = str(env.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime_dir:
        return Path(runtime_dir) / "open-mmi" / "status.json"
    return Path("/tmp/open-mmi-status.json")


def _loaded_identity(value: Any) -> Optional[dict[str, str]]:
    if value == {}:
        return {}
    if not isinstance(value, Mapping) or set(value) != {"source", "id", "revision"}:
        return None
    source = value.get("source")
    identifier = value.get("id")
    revision = value.get("revision")
    if not isinstance(source, str) or source not in {"maintained", "custom", "external"}:
        return None
    if not isinstance(identifier, str) or not IDENTIFIER_RE.fullmatch(identifier):
        return None
    if not isinstance(revision, str) or not vehicle_configuration.REVISION_RE.fullmatch(revision):
        return None
    return {"source": source, "id": identifier, "revision": revision}


def read_loaded_runtime(path: Path) -> Optional[dict[str, Any]]:
    """Read bounded daemon evidence from the status wrapper, failing closed."""

    descriptor_fd = -1
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor_fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor_fd)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_STATUS_BYTES:
            return None
        with os.fdopen(descriptor_fd, "rb", closefd=True) as handle:
            descriptor_fd = -1
            content = handle.read(MAX_STATUS_BYTES + 1)
    except OSError:
        return None
    finally:
        if descriptor_fd >= 0:
            os.close(descriptor_fd)
    if len(content) > MAX_STATUS_BYTES:
        return None
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    runtime = payload.get("runtime")
    if not isinstance(runtime, Mapping) or set(runtime) != {
        "api_version",
        "state",
        "errors",
        "vehicle",
        "bindings",
        "active_bus",
        "interface",
    }:
        return None
    if runtime.get("api_version") != API_VERSION:
        return None
    state = runtime.get("state")
    errors = runtime.get("errors")
    active_bus = runtime.get("active_bus")
    interface = runtime.get("interface")
    vehicle = _loaded_identity(runtime.get("vehicle"))
    bindings = _loaded_identity(runtime.get("bindings"))
    if state not in {"ready", "invalid"}:
        return None
    if not isinstance(errors, list) or len(errors) > 16 or any(
        not isinstance(error, str) or not error or len(error) > 128
        for error in errors
    ):
        return None
    if not isinstance(active_bus, str) or not IDENTIFIER_RE.fullmatch(active_bus):
        return None
    if not isinstance(interface, str) or not INTERFACE_RE.fullmatch(interface):
        return None
    if vehicle is None or bindings is None:
        return None
    if state == "ready" and (not vehicle or not bindings or errors):
        return None
    if state == "invalid" and not errors:
        return None
    updated_at = payload.get("updated_at")
    if (
        isinstance(updated_at, bool)
        or not isinstance(updated_at, (int, float))
        or not math.isfinite(float(updated_at))
        or float(updated_at) < 0
    ):
        return None
    return {
        "api_version": API_VERSION,
        "state": state,
        "errors": list(errors),
        "vehicle": vehicle,
        "bindings": bindings,
        "active_bus": active_bus,
        "interface": interface,
        "updated_at": float(updated_at),
    }


def status_payload(
    roots: Optional[CatalogueRoots] = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
    dropin_path: Optional[Path] = None,
    sys_class_net: Path = Path("/sys/class/net"),
    status_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Return the complete read-only setup status used by CLI and dashboard."""

    selected_roots = roots or default_roots(environment)
    if environment is None:
        runtime_environment = read_runtime_environment(dropin_path)
        for key in RUNTIME_ENVIRONMENT_KEYS:
            if key in os.environ:
                runtime_environment[key] = os.environ[key]
    else:
        runtime_environment = {key: str(value) for key, value in environment.items() if key in RUNTIME_ENVIRONMENT_KEYS}

    catalogue = catalogue_payload(selected_roots)
    vehicle_id = runtime_environment.get("OPEN_MMI_VEHICLE", "seat-leon-1p-pq35")
    bindings_id = runtime_environment.get("OPEN_MMI_BINDINGS", "default")
    vehicle = _identity_from_environment(
        selected_roots,
        "profile",
        vehicle_id,
        runtime_environment.get("OPEN_MMI_VEHICLE_CONFIG", ""),
    )
    bindings = _identity_from_environment(
        selected_roots,
        "bindings",
        bindings_id,
        runtime_environment.get("OPEN_MMI_BINDINGS_FILE", ""),
    )
    profile_entry = _find_entry(catalogue["profiles"], vehicle)
    bindings_entry = _find_entry(catalogue["bindings"], bindings)
    profile_document = _load_identity_document(selected_roots, "profile", vehicle)
    bindings_document = _load_identity_document(selected_roots, "bindings", bindings)
    runtime = resolve_can_runtime(profile_document if isinstance(profile_document, Mapping) else {}, runtime_environment)
    active_errors: list[str] = []
    if vehicle["source"] == "external":
        active_errors.append("external-profile-path")
    elif profile_entry is None:
        active_errors.append("profile-not-found")
    elif not profile_entry.get("valid"):
        active_errors.append("profile-invalid")
    if bindings["source"] == "external":
        active_errors.append("external-bindings-path")
    elif bindings_entry is None:
        active_errors.append("bindings-not-found")
    elif not bindings_entry.get("valid"):
        active_errors.append("bindings-invalid")

    revisions = {
        "vehicle": str(profile_entry.get("revision") or "") if profile_entry else "",
        "bindings": str(bindings_entry.get("revision") or "") if bindings_entry else "",
    }
    revision_input = json.dumps(
        {**revisions, "active_bus": runtime.name, "interface": runtime.interface},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    configuration_revision = "sha256:" + hashlib.sha256(revision_input).hexdigest()
    interfaces = discover_interfaces(sys_class_net)
    interface_names = {entry["name"] for entry in interfaces}
    loaded_path = status_path
    if loaded_path is None and environment is None:
        loaded_path = _default_status_path()
    elif loaded_path is None and environment is not None and environment.get("OPEN_MMI_STATUS_PATH"):
        loaded_path = _default_status_path(environment)
    loaded = read_loaded_runtime(loaded_path) if loaded_path is not None else None
    return {
        "api_version": API_VERSION,
        "read_only": True,
        "runtime_mode": "single",
        "catalogue": catalogue,
        "active": {
            "state": "ready" if not active_errors else "invalid",
            "errors": active_errors,
            "vehicle": {**vehicle, "revision": revisions["vehicle"]},
            "bindings": {**bindings, "revision": revisions["bindings"]},
            "active_bus": runtime.name,
            "interface": runtime.interface,
            "interface_present": runtime.interface in interface_names,
            "configuration_revision": configuration_revision,
            "loaded": loaded,
        },
        "compatibility": compatibility_report(profile_document, bindings_document),
        "interfaces": interfaces,
    }


def _request_object(
    value: Any,
    *,
    path: str,
    required: set[str],
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise VehicleSetupError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise VehicleSetupError(f"{path} contains an invalid field name")
    unknown = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if unknown:
        raise VehicleSetupError(
            f"{path} contains unsupported fields: {', '.join(unknown)}"
        )
    if missing:
        raise VehicleSetupError(
            f"{path} is missing required fields: {', '.join(missing)}"
        )
    return value


def _request_identity(value: Any, *, path: str) -> dict[str, str]:
    identity = _request_object(
        value,
        path=path,
        required={"source", "id"},
    )
    source = identity["source"]
    identifier = identity["id"]
    if not isinstance(source, str) or source not in {"maintained", "custom"}:
        raise VehicleSetupError(f"{path}.source must be maintained or custom")
    if not isinstance(identifier, str) or not IDENTIFIER_RE.fullmatch(identifier):
        raise VehicleSetupError(f"{path}.id is invalid")
    return {"source": source, "id": identifier}


def _request_runtime(value: Any) -> tuple[str, str]:
    runtime = _request_object(
        value,
        path="runtime",
        required={"active_bus", "buses"},
    )
    active_bus = runtime["active_bus"]
    if not isinstance(active_bus, str) or not IDENTIFIER_RE.fullmatch(active_bus):
        raise VehicleSetupError("runtime.active_bus is invalid")
    buses = _request_object(
        runtime["buses"],
        path="runtime.buses",
        required={active_bus},
    )
    if len(buses) != 1:
        raise VehicleSetupError("runtime.buses must contain exactly the active bus")
    assignment = _request_object(
        buses[active_bus],
        path=f"runtime.buses.{active_bus}",
        required={"interface"},
    )
    interface = assignment["interface"]
    if not isinstance(interface, str) or not INTERFACE_RE.fullmatch(interface):
        raise VehicleSetupError(
            f"runtime.buses.{active_bus}.interface is invalid"
        )
    return active_bus, interface


def _selected_document(
    roots: CatalogueRoots,
    kind: str,
    identity: Mapping[str, str],
) -> tuple[Mapping[str, Any], str, dict[str, Any]]:
    path = resolve_catalogue_path(
        roots,
        kind,
        identity["source"],
        identity["id"],
    )
    document, revision = _read_document(
        path,
        MAX_PROFILE_BYTES if kind == "profile" else MAX_BINDINGS_BYTES,
    )
    validation = (
        validate_profile(document)
        if kind == "profile"
        else validate_bindings(
            document, maintained=(identity["source"] == "maintained")
        )
    )
    if not validation["valid"] or not isinstance(document, Mapping):
        label = "vehicle profile" if kind == "profile" else "bindings"
        raise VehicleSetupError(f"Selected {label} is invalid")
    return document, revision, validation


def _profile_bus_metadata(
    profile: Mapping[str, Any],
    active_bus: str,
) -> dict[str, Any]:
    default_bus = str(profile.get("default_bus") or DEFAULT_BUS)
    raw_buses = profile.get("can_buses")
    if not isinstance(raw_buses, Mapping):
        raw_buses = {default_bus: {}}
    if active_bus not in raw_buses:
        if active_bus != default_bus:
            raise VehicleSetupError("runtime.active_bus is not declared by the profile")
        metadata: Mapping[str, Any] = {}
    else:
        raw_metadata = raw_buses[active_bus]
        metadata = raw_metadata if isinstance(raw_metadata, Mapping) else {}
    bitrate = metadata.get("bitrate")
    try:
        parsed_bitrate = _parse_int(bitrate) if bitrate is not None else None
    except (TypeError, ValueError):
        parsed_bitrate = None
    return {
        "name": active_bus,
        "profile_interface": str(
            metadata.get("interface")
            or metadata.get("tested_interface")
            or DEFAULT_INTERFACE
        ),
        "bitrate": parsed_bitrate,
        "provisioning": str(metadata.get("provisioning") or "manual"),
        "declared_bus_count": len(raw_buses),
    }


def _preview_warning(code: str, path: str, message: str) -> dict[str, str]:
    return _issue("warning", code, path, message)


def preview_payload(
    request: Mapping[str, Any],
    roots: Optional[CatalogueRoots] = None,
    *,
    current_status: Optional[Mapping[str, Any]] = None,
    sys_class_net: Path = Path("/sys/class/net"),
) -> dict[str, Any]:
    """Return a deterministic, non-mutating configuration plan.

    Caller input contains only allowlisted catalogue identities and one logical
    bus-to-interface assignment.  Paths and generated configuration text are
    resolved later by the privileged coordinator and never cross the browser
    contract.
    """

    payload = _request_object(
        request,
        path="request",
        required={"vehicle", "bindings", "runtime"},
    )
    selected_roots = roots or default_roots()
    vehicle = _request_identity(payload["vehicle"], path="vehicle")
    if vehicle["source"] == "maintained":
        canonical_id, _aliases, _status = _canonical_profile_identity(
            selected_roots, "maintained", vehicle["id"]
        )
        vehicle = {"source": "maintained", "id": canonical_id}
    bindings = _request_identity(payload["bindings"], path="bindings")
    active_bus, interface = _request_runtime(payload["runtime"])

    profile_document, profile_revision, profile_validation = _selected_document(
        selected_roots, "profile", vehicle
    )
    bindings_document, bindings_revision, bindings_validation = _selected_document(
        selected_roots, "bindings", bindings
    )
    bus = _profile_bus_metadata(profile_document, active_bus)
    compatibility = compatibility_report(profile_document, bindings_document)

    target = vehicle_configuration.normalize_selection(
        {
            "vehicle": {**vehicle, "revision": profile_revision},
            "bindings": {**bindings, "revision": bindings_revision},
            "runtime": {
                "mode": "single",
                "active_bus": active_bus,
                "buses": {active_bus: {"interface": interface}},
            },
        }
    )
    status = dict(current_status) if current_status is not None else status_payload(
        selected_roots,
        sys_class_net=sys_class_net,
    )
    current = status.get("active") if isinstance(status.get("active"), Mapping) else {}
    interfaces = status.get("interfaces") if isinstance(status.get("interfaces"), list) else []
    selected_interface = next(
        (
            dict(entry)
            for entry in interfaces
            if isinstance(entry, Mapping) and entry.get("name") == interface
        ),
        None,
    )
    interface_status = {
        "name": interface,
        "present": bool(
            selected_interface and selected_interface.get("present") is True
        ),
        "up": bool(selected_interface and selected_interface.get("up") is True),
        "configured_bitrate": (
            selected_interface.get("configured_bitrate")
            if selected_interface
            else None
        ),
    }

    warnings: list[dict[str, str]] = []
    warnings.extend(dict(issue) for issue in profile_validation["warnings"])
    warnings.extend(dict(issue) for issue in bindings_validation["warnings"])
    if not selected_interface:
        warnings.append(
            _preview_warning(
                "interface-not-present",
                "runtime.buses",
                f"{interface} is not currently detected",
            )
        )
    elif selected_interface.get("up") is not True:
        warnings.append(
            _preview_warning(
                "interface-down",
                "runtime.buses",
                f"{interface} is present but not up",
            )
        )
    configured_bitrate = interface_status["configured_bitrate"]
    if (
        bus["bitrate"] is not None
        and configured_bitrate is not None
        and bus["bitrate"] != configured_bitrate
    ):
        warnings.append(
            _preview_warning(
                "bitrate-mismatch",
                "runtime.buses",
                "the detected adapter bitrate differs from the profile",
            )
        )
    if compatibility["emitted_unbound"]:
        warnings.append(
            _preview_warning(
                "emitted-events-unbound",
                "bindings",
                f"{len(compatibility['emitted_unbound'])} emitted event(s) have no binding",
            )
        )
    if compatibility["bound_unemitted"]:
        warnings.append(
            _preview_warning(
                "bindings-unused",
                "bindings",
                f"{len(compatibility['bound_unemitted'])} binding(s) are not emitted by the profile",
            )
        )
    if compatibility["duplicate_emitted"]:
        warnings.append(
            _preview_warning(
                "duplicate-emitted-events",
                "vehicle",
                f"{len(compatibility['duplicate_emitted'])} event(s) are emitted more than once",
            )
        )
    if bus["declared_bus_count"] > 1:
        warnings.append(
            _preview_warning(
                "single-active-bus",
                "runtime.active_bus",
                "this Open MMI version listens to one selected CAN bus at a time",
            )
        )
    if interface != bus["profile_interface"]:
        warnings.append(
            _preview_warning(
                "interface-override",
                "runtime.buses",
                f"{interface} overrides the profile default {bus['profile_interface']}",
            )
        )

    changes: list[dict[str, Any]] = []
    for field, before, after in (
        ("vehicle", current.get("vehicle"), target["vehicle"]),
        ("bindings", current.get("bindings"), target["bindings"]),
        ("active_bus", current.get("active_bus"), active_bus),
        ("interface", current.get("interface"), interface),
    ):
        if before != after:
            changes.append({"field": field, "from": before, "to": after})
    requires_apply = bool(changes) or current.get("state") != "ready"
    expected_revision = str(current.get("configuration_revision") or "")
    if not vehicle_configuration.REVISION_RE.fullmatch(expected_revision):
        raise VehicleSetupError("Current configuration revision is unavailable")

    return {
        "api_version": API_VERSION,
        "read_only": True,
        "apply_available": False,
        "state": "ready",
        "expected_configuration_revision": expected_revision,
        "target_configuration_revision": vehicle_configuration.selection_revision(
            target
        ),
        "target": target,
        "active_bus": {
            "name": active_bus,
            "interface": interface,
            "profile_interface": bus["profile_interface"],
            "bitrate": bus["bitrate"],
            "provisioning": bus["provisioning"],
        },
        "interface": interface_status,
        "compatibility": compatibility,
        "validation": {"valid": True, "errors": [], "warnings": warnings},
        "plan": {
            "changes": changes,
            "effects": {
                "write_canonical_configuration": requires_apply,
                "write_systemd_runtime": requires_apply,
                "write_udev_rules": requires_apply,
                "reload_user_manager": requires_apply,
                "reload_udev": requires_apply,
                "restart_can_service": requires_apply,
            },
        },
    }
