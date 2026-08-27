#!/usr/bin/env python3
"""Open MMI CAN Bus Daemon."""

import hashlib
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import can

from canbusd.can_runtime import CanRuntimeConfig, item_matches_bus, resolve_can_runtime
from canbusd.dispatcher import ActionQueue, dispatch
from canbusd import action_registry, event_registry, profile_catalogue
from canbusd import status_registry
from canbusd.status_bus import publish as publish_status
from canbusd.status_bus import publish_runtime as publish_runtime_status
from canbusd.status_bus import reset as reset_status
from canbusd.status_rules import StatusRuleState, evaluate_status_rules, parse_status_rules


log_level = os.getenv("OPEN_MMI_LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, log_level, logging.INFO),
    format="[%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("canbusd")


BASE_DIR = Path(__file__).parent.parent
USER_CONFIG_DIR = Path(
    os.getenv("OPEN_MMI_CONFIG_DIR", str(Path.home() / ".config" / "open-mmi"))
)

VEHICLE = os.getenv("OPEN_MMI_VEHICLE", "seat-leon-1p-pq35")
BINDINGS = os.getenv("OPEN_MMI_BINDINGS", "default")

DEFAULT_CAN_BUS = "comfort"
DEFAULT_CAN_INTERFACE = "can0"

CAN_RUNTIME = resolve_can_runtime(
    {},
    os.environ,
    default_bus=DEFAULT_CAN_BUS,
    default_interface=DEFAULT_CAN_INTERFACE,
)
CAN_BUS = CAN_RUNTIME.name
IFACE = CAN_RUNTIME.interface

RELOAD_INTERVAL = 60
ANY_VALUE_WILDCARD = "any"
DEFAULT_PRESENCE_TIMEOUT = 1000

_need_reload = False
_reload_lock = __import__("threading").Lock()

LOADED_VEHICLE: Optional[Dict[str, str]] = None
LOADED_BINDINGS: Optional[Dict[str, str]] = None


def _managed_configuration_mode(
    environment: Optional[Dict[str, str]] = None,
) -> bool:
    """Return whether exact coordinator-managed catalogue paths are active.

    Managed Vehicle Setup applies always provide both fixed document paths and
    restart the daemon after review. Those revisions must remain pinned for the
    lifetime of that process; periodic or signal-triggered rereads would turn an
    editor save into an implicit activation.
    """

    env = os.environ if environment is None else environment
    return bool(
        str(env.get("OPEN_MMI_VEHICLE_CONFIG") or "").strip()
        and str(env.get("OPEN_MMI_BINDINGS_FILE") or "").strip()
    )


def _sig_hup(_signo: int, _frame: Any) -> None:
    global _need_reload

    if _managed_configuration_mode():
        logger.info(
            "SIGHUP ignored for managed vehicle configuration; "
            "use reviewed Apply to load a new revision"
        )
        return

    with _reload_lock:
        _need_reload = True

    logger.info("SIGHUP received -> reload config")


signal.signal(signal.SIGHUP, _sig_hup)


def _resolve_vehicle_config_path() -> Path:
    explicit = os.getenv("OPEN_MMI_VEHICLE_CONFIG")
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.exists():
            return explicit_path
        candidate = explicit_path.absolute()
        for root in _maintained_catalogue_roots():
            legacy_path = root / "vehicles" / VEHICLE / "config.json"
            if candidate != legacy_path.expanduser().absolute():
                continue
            try:
                resolved = profile_catalogue.resolve_profile(root, VEHICLE)
            except profile_catalogue.VehicleProfileCatalogueError:
                continue
            replacement = Path(resolved["path"])
            if replacement.is_file():
                logger.warning(
                    "Migrating missing legacy maintained profile path %s to %s",
                    explicit_path,
                    replacement,
                )
                return replacement
        return explicit_path

    user_path = USER_CONFIG_DIR / "vehicles" / VEHICLE / "config.json"
    if user_path.exists():
        logger.warning(
            "User vehicle profile override exists but is not active: %s. "
            "Set OPEN_MMI_VEHICLE_CONFIG to use it explicitly.",
            user_path,
        )

    for root in _maintained_catalogue_roots():
        try:
            return profile_catalogue.resolve_profile(root, VEHICLE)["path"]
        except profile_catalogue.VehicleProfileCatalogueError:
            continue
    return BASE_DIR / "vehicles" / VEHICLE / "config.json"


def _resolve_bindings_path() -> Path:
    explicit = os.getenv("OPEN_MMI_BINDINGS_FILE")
    if explicit:
        return Path(explicit).expanduser()

    user_path = USER_CONFIG_DIR / "bindings" / f"{BINDINGS}.json"
    if user_path.exists():
        logger.warning(
            "User bindings override exists but is not active: %s. "
            "Set OPEN_MMI_BINDINGS_FILE to use it explicitly.",
            user_path,
        )

    return BASE_DIR / "bindings" / f"{BINDINGS}.json"


def _maintained_catalogue_roots() -> Tuple[Path, ...]:
    """Return trusted maintained roots for source and installed runtimes."""

    development_root = str(
        os.getenv("OPEN_MMI_VEHICLE_SETUP_DEVELOPMENT_ROOT") or ""
    ).strip()
    installed_root = str(os.getenv("OPEN_MMI_INSTALL_DIR") or "/opt/open-mmi").strip()
    configured_root = Path(development_root or installed_root).expanduser()

    roots = []
    for root in (BASE_DIR, configured_root):
        absolute = root.expanduser().absolute()
        if absolute not in roots:
            roots.append(absolute)
    return tuple(roots)


def _document_source(kind: str, identifier: str, path: Path) -> str:
    """Classify one daemon-resolved document without exposing its path."""

    if kind == "vehicle":
        custom = USER_CONFIG_DIR / "vehicles" / identifier / "config.json"
        candidate = path.expanduser().absolute()
        for root in _maintained_catalogue_roots():
            try:
                maintained = profile_catalogue.resolve_profile(root, identifier)["path"]
            except profile_catalogue.VehicleProfileCatalogueError:
                continue
            if candidate == maintained.expanduser().absolute():
                return "maintained"
        if candidate == custom.expanduser().absolute():
            return "custom"
        return "external"
    elif kind == "bindings":
        relative = Path("bindings") / f"{identifier}.json"
        custom = USER_CONFIG_DIR / relative
    else:  # pragma: no cover - internal fixed callers only
        return "external"

    candidate = path.expanduser().absolute()
    if any(candidate == root / relative for root in _maintained_catalogue_roots()):
        return "maintained"
    if candidate == custom.expanduser().absolute():
        return "custom"
    return "external"


def _canonical_vehicle_id(path: Path, identifier: str) -> str:
    candidate = path.expanduser().absolute()
    for root in _maintained_catalogue_roots():
        try:
            resolved = profile_catalogue.resolve_profile(root, identifier)
        except profile_catalogue.VehicleProfileCatalogueError:
            continue
        if candidate == resolved["path"].expanduser().absolute():
            return str(resolved["id"])
    return identifier


def _read_json_with_revision(path: Path) -> Tuple[Any, str]:
    content = path.read_bytes()
    document = json.loads(content.decode("utf-8"))
    revision = "sha256:" + hashlib.sha256(content).hexdigest()
    return document, revision


def _set_path(dst: Dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return

    cur = dst
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})

    cur[parts[-1]] = value


def _load_bindings() -> Dict[str, Dict[str, Any]]:
    global LOADED_BINDINGS

    path = _resolve_bindings_path()

    try:
        bindings, revision = _read_json_with_revision(path)
        if not isinstance(bindings, dict):
            raise ValueError("bindings root must be an object")
        resolved_bindings = {}
        for event, binding in bindings.items():
            definition = event_registry.require_event(event)
            if action_registry.is_legacy_binding(binding):
                logger.warning(
                    "Binding for event=%s uses deprecated module/func schema; "
                    "use a canonical action identifier",
                    event,
                )
            resolved_bindings[event] = action_registry.resolve_binding(
                binding,
                carries_event_payload=(definition["payload"]["type"] != "none"),
            )

        LOADED_BINDINGS = {
            "source": _document_source("bindings", BINDINGS, path),
            "id": BINDINGS,
            "revision": revision,
        }

        logger.info("Loaded %d bindings from %s", len(resolved_bindings), path)
        return resolved_bindings

    except Exception as e:
        LOADED_BINDINGS = None
        logger.error("Bindings load failed from %s: %s", path, e)
        return {}


def _filter_items_for_bus(
    items: List[Dict[str, Any]],
    runtime: CanRuntimeConfig,
) -> List[Dict[str, Any]]:
    return [
        item
        for item in items
        if item_matches_bus(item, runtime.name, runtime.default_bus)
    ]


def _load_config(
    prev_rules=None,
    prev_mtime=None,
    prev_presence=None,
    prev_status_rules=None,
    prev_path=None,
    prev_runtime=None,
):
    global _need_reload, CAN_RUNTIME, CAN_BUS, IFACE, LOADED_VEHICLE

    path = _resolve_vehicle_config_path()

    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        logger.error("Vehicle config not found: %s", path)
        return (
            prev_rules,
            prev_mtime,
            prev_presence or [],
            prev_status_rules or {},
            prev_path,
            prev_runtime or CAN_RUNTIME,
        )

    with _reload_lock:
        reload_requested = _need_reload

    path_changed = prev_path is not None and Path(prev_path) != path
    if prev_mtime == mtime and not reload_requested and not path_changed:
        return (
            prev_rules,
            prev_mtime,
            prev_presence or [],
            prev_status_rules or {},
            path,
            prev_runtime or CAN_RUNTIME,
        )

    try:
        cfg, revision = _read_json_with_revision(path)
        if not isinstance(cfg, dict):
            raise ValueError("vehicle profile root must be an object")

        runtime = resolve_can_runtime(
            cfg,
            os.environ,
            default_bus=DEFAULT_CAN_BUS,
            default_interface=DEFAULT_CAN_INTERFACE,
        )

        all_rule_items = cfg.get("rules", [])
        for item in all_rule_items:
            raw_value = item.get("value")
            carries_payload = (
                "matches" not in item
                and isinstance(raw_value, str)
                and raw_value.lower() == ANY_VALUE_WILDCARD
            )
            event_registry.require_event(
                item["event"],
                carries_payload=carries_payload,
            )
        rule_items = _filter_items_for_bus(all_rule_items, runtime)

        rules: Dict[int, List[Any]] = {}
        for r in rule_items:
            cid = int(r["id"], 16) if isinstance(r["id"], str) else int(r["id"])

            if "matches" in r:
                matches = []
                for match in r["matches"]:
                    byte_index = int(match["byte"])
                    raw_match_value = match["value"]
                    match_value = (
                        int(raw_match_value, 16)
                        if isinstance(raw_match_value, str)
                        and raw_match_value.lower().startswith("0x")
                        else int(raw_match_value)
                    )
                    matches.append((byte_index, match_value))
                rules.setdefault(cid, []).append(
                    {
                        "matches": tuple(matches),
                        "event": r["event"],
                    }
                )
                continue

            b = int(r.get("byte", 0))
            v = r.get("value")
            carries_payload = (
                isinstance(v, str)
                and v.lower() == ANY_VALUE_WILDCARD
            )

            if carries_payload:
                v = None
            elif v is not None:
                v = int(v)

            rules.setdefault(cid, []).append((b, v, r["event"]))

        all_presence_items = cfg.get("presence", [])
        for item in all_presence_items:
            for event_key in ("on_present", "on_absent"):
                if item.get(event_key) is not None:
                    event_registry.require_event(
                        item[event_key],
                        carries_payload=False,
                    )
        presence_items = _filter_items_for_bus(all_presence_items, runtime)

        presence = []
        for p in presence_items:
            presence.append(
                {
                    "id": int(p["id"], 16) if isinstance(p["id"], str) else int(p["id"]),
                    "timeout_ms": int(p.get("timeout_ms", DEFAULT_PRESENCE_TIMEOUT)),
                    "on_present": p.get("on_present"),
                    "on_absent": p.get("on_absent"),
                    "status_path": p.get("status_path", "vehicle.present"),
                }
            )

        all_status_items = cfg.get("status", [])
        status_registry.require_profile_statuses(cfg)
        status_items = _filter_items_for_bus(all_status_items, runtime)
        status_rules = parse_status_rules(status_items)

        CAN_RUNTIME = runtime
        CAN_BUS = runtime.name
        IFACE = runtime.interface
        LOADED_VEHICLE = {
            "source": _document_source("vehicle", VEHICLE, path),
            "id": _canonical_vehicle_id(path, VEHICLE),
            "revision": revision,
        }

        if runtime.profile_has_buses and not runtime.declared:
            logger.warning(
                "CAN bus '%s' is not declared in profile metadata; using interface '%s'",
                runtime.name,
                runtime.interface,
            )

        if runtime.bring_up:
            logger.warning(
                "CAN bus '%s' has bring_up=true metadata, but daemon-side interface "
                "configuration is intentionally not implemented",
                runtime.name,
            )

        with _reload_lock:
            _need_reload = False

        logger.info(
            "Loaded config from %s: bus=%s interface=%s interface_source=%s "
            "bitrate=%s provisioning=%s capture_point=%s rules=%d/%d "
            "presence=%d/%d status CAN ids=%d",
            path,
            runtime.name,
            runtime.interface,
            runtime.interface_source,
            runtime.bitrate,
            runtime.provisioning,
            runtime.capture_point,
            len(rule_items),
            len(all_rule_items),
            len(presence_items),
            len(all_presence_items),
            len(status_rules),
        )

        return (rules, mtime, presence, status_rules, path, runtime)

    except Exception as e:
        logger.error("Config load failed from %s: %s", path, e)
        return (
            prev_rules,
            prev_mtime,
            prev_presence or [],
            prev_status_rules or {},
            prev_path,
            prev_runtime or CAN_RUNTIME,
        )


def _safe_publish_status(update: Dict[str, Any]) -> None:
    """Publish decoded status without allowing UI persistence to stop CAN input."""

    try:
        publish_status(update)
    except Exception:
        logger.exception("Status publication failed update=%s", update)


def _safe_reset_status() -> None:
    """Clear stale decoded fields at a daemon/profile lifecycle boundary."""

    try:
        reset_status(persist=True, notify=True)
    except Exception:
        logger.exception("Status reset failed")


def _loaded_runtime_payload(runtime: CanRuntimeConfig) -> Dict[str, Any]:
    errors = []
    if LOADED_VEHICLE is None:
        errors.append("vehicle-profile-not-loaded")
    if LOADED_BINDINGS is None:
        errors.append("bindings-not-loaded")
    return {
        "api_version": 1,
        "state": "ready" if not errors else "invalid",
        "errors": errors,
        "vehicle": dict(LOADED_VEHICLE or {}),
        "bindings": dict(LOADED_BINDINGS or {}),
        "active_bus": runtime.name,
        "interface": runtime.interface,
    }


def _safe_publish_loaded_runtime(runtime: CanRuntimeConfig) -> None:
    """Publish exact loaded identities without interrupting CAN reception."""

    try:
        publish_runtime_status(_loaded_runtime_payload(runtime))
    except Exception:
        logger.exception("Loaded runtime publication failed")


def _publish_presence(
    presence_rule: Dict[str, Any],
    is_present: bool,
    bindings: Dict[str, Dict[str, Any]],
    dispatch_fn=None,
) -> None:
    if dispatch_fn is None:
        dispatch_fn = dispatch

    cid = presence_rule["id"]
    event = presence_rule.get("on_present") if is_present else presence_rule.get("on_absent")

    update: Dict[str, Any] = {
        "presence": {
            f"0x{cid:X}": is_present,
        }
    }

    _set_path(update, presence_rule.get("status_path", "vehicle.present"), is_present)
    _safe_publish_status(update)

    if event:
        logger.info(
            "Presence changed: 0x%X -> %s",
            cid,
            "present" if is_present else "absent",
        )
        dispatch_fn(event, bindings.get(event))


def _check_presence(
    presence: List[Dict[str, Any]],
    last_seen: Dict[int, float],
    present_state: Dict[int, Optional[bool]],
    bindings: Dict[str, Dict[str, Any]],
    now: float,
    dispatch_fn=None,
) -> None:
    for p in presence:
        cid = p["id"]
        timeout_s = p["timeout_ms"] / 1000.0
        is_present = cid in last_seen and (now - last_seen[cid]) <= timeout_s
        previous = present_state.get(cid)

        if previous is None or previous != is_present:
            present_state[cid] = is_present
            if dispatch_fn is None:
                _publish_presence(p, is_present, bindings)
            else:
                _publish_presence(p, is_present, bindings, dispatch_fn=dispatch_fn)


def main(
    max_iterations: Optional[int] = None,
    dispatch_fn=None,
) -> None:
    """Run the CAN receive loop.

    ``max_iterations`` is intentionally a test hook. Production callers leave
    it as ``None`` for the normal unbounded daemon loop.
    """

    managed_configuration = _managed_configuration_mode()
    rules, mtime, presence, status_rules, cfg_path, runtime = _load_config(None, None)
    bindings = _load_bindings()

    if managed_configuration:
        logger.info(
            "Managed vehicle configuration is pinned until the daemon restarts"
        )

    action_queue = None
    if dispatch_fn is None:
        action_queue = ActionQueue()
        dispatch_fn = action_queue.dispatch

    last_seen: Dict[int, float] = {}
    last_codes: Dict[Tuple[int, int, Optional[int]], int] = {}
    last_match_states: Dict[
        Tuple[int, Tuple[Tuple[int, int], ...], str], bool
    ] = {}
    present_state: Dict[int, Optional[bool]] = {}
    status_state = StatusRuleState()
    _safe_reset_status()
    _safe_publish_loaded_runtime(runtime)
    bus = None
    opened_interface: Optional[str] = None
    last_check = 0.0
    iterations = 0

    logger.info("canbusd starting")
    logger.info("vehicle=%s bindings=%s config_dir=%s", VEHICLE, BINDINGS, USER_CONFIG_DIR)

    try:
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            now = time.monotonic()

            if not managed_configuration and now - last_check > RELOAD_INTERVAL:
                previous_status_rules = status_rules
                (
                    rules,
                    mtime,
                    presence,
                    status_rules,
                    cfg_path,
                    runtime,
                ) = _load_config(
                    rules,
                    mtime,
                    presence,
                    status_rules,
                    cfg_path,
                    runtime,
                )
                if status_rules is not previous_status_rules:
                    status_state.reset()
                    _safe_reset_status()
                bindings = _load_bindings()
                _safe_publish_loaded_runtime(runtime)
                last_check = now

            if opened_interface != IFACE:
                if bus:
                    bus.shutdown()
                    bus = None

                if opened_interface is not None:
                    _safe_reset_status()

                opened_interface = IFACE
                last_seen.clear()
                present_state.clear()
                status_state.reset()

            if not Path(f"/sys/class/net/{IFACE}").exists():
                if bus:
                    bus.shutdown()
                    bus = None

                _check_presence(presence, last_seen, present_state, bindings, now, dispatch_fn=dispatch_fn)
                time.sleep(1)
                continue

            if bus is None:
                logger.info("Opening CAN bus '%s' on interface '%s'", CAN_BUS, IFACE)
                bus = can.interface.Bus(channel=IFACE, interface="socketcan")

            msg = bus.recv(timeout=0.2)
            now = time.monotonic()

            if msg is None:
                _check_presence(presence, last_seen, present_state, bindings, now, dispatch_fn=dispatch_fn)
                continue

            last_seen[msg.arbitration_id] = now
            cid = msg.arbitration_id

            if cid in status_rules:
                status_update = evaluate_status_rules(
                    status_rules[cid],
                    msg.data,
                    msg.dlc,
                    state=status_state,
                )
                if status_update:
                    _safe_publish_status(status_update)

            if cid in rules:
                for rule in rules[cid]:
                    if isinstance(rule, dict) and "matches" in rule:
                        matches = tuple(rule["matches"])
                        if any(msg.dlc <= byte_index for byte_index, _ in matches):
                            continue

                        matched = all(
                            msg.data[byte_index] == expected
                            for byte_index, expected in matches
                        )
                        event = rule["event"]
                        key = (cid, matches, event)
                        previous = last_match_states.get(key, False)
                        if matched and not previous:
                            dispatch_fn(event, bindings.get(event))
                        last_match_states[key] = matched
                        continue

                    b, v, event = rule
                    if msg.dlc <= b:
                        continue

                    code = msg.data[b]
                    key = (cid, b, v)

                    if v is None:
                        if last_codes.get(key) != code:
                            dispatch_fn(event, bindings.get(event), [code])
                        last_codes[key] = code
                        continue

                    previous = last_codes.get(key)
                    if code == v and previous != v:
                        dispatch_fn(event, bindings.get(event))

                    last_codes[key] = code

            _check_presence(presence, last_seen, present_state, bindings, now, dispatch_fn=dispatch_fn)
    finally:
        if bus:
            bus.shutdown()
        if action_queue is not None:
            action_queue.close()


if __name__ == "__main__":
    main()
