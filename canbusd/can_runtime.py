"""CAN runtime configuration helpers.

This module keeps the daemon's CAN bus selection logic separate from vehicle-specific
signal decoding. It models one active named bus today while leaving a path toward
multiple SocketCAN inputs later.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class CanRuntimeConfig:
    """Resolved runtime selection for the current single-bus daemon."""

    name: str
    default_bus: str
    interface: str
    interface_source: str
    bitrate: Optional[int] = None
    capture_point: Optional[str] = None
    provisioning: Optional[str] = None
    bring_up: bool = False
    declared: bool = False
    profile_has_buses: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)
    requested_name: str = ""


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, str):
        value = value.strip()
        return int(value, 16) if value.lower().startswith("0x") else int(value)

    return int(value)


def _optional_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")

    return bool(value)


def resolve_can_runtime(
    profile: Mapping[str, Any],
    env: Optional[Mapping[str, str]] = None,
    default_bus: str = "comfort",
    default_interface: str = "can0",
) -> CanRuntimeConfig:
    """Resolve the active named bus and SocketCAN interface.

    Resolution order:

    1. ``OPEN_MMI_CAN_BUS`` selects the active named bus.
    2. Otherwise the profile ``default_bus`` is used.
    3. Otherwise ``default_bus`` is used.

    Interface resolution order:

    1. ``OPEN_MMI_CAN_INTERFACE`` overrides everything.
    2. Active bus metadata ``interface``.
    3. Active bus metadata ``tested_interface`` for older docs/configs.
    4. ``default_interface``.

    This function deliberately does not configure bitrate or bring interfaces up.
    It only describes which already-provisioned SocketCAN interface the daemon should
    consume.
    """

    env = env or {}

    profile_default_bus = str(profile.get("default_bus") or default_bus)
    requested_bus = str(env.get("OPEN_MMI_CAN_BUS") or profile_default_bus)

    can_buses = profile.get("can_buses") or {}
    if not isinstance(can_buses, Mapping):
        can_buses = {}

    raw_aliases = profile.get("can_bus_aliases") or {}
    aliases = raw_aliases if isinstance(raw_aliases, Mapping) else {}

    def canonical_bus(name: str) -> str:
        # A declared bus always wins. This prevents malformed/custom alias metadata
        # from redefining a real logical bus. Aliases are accepted only when they
        # point directly at a declared canonical bus.
        if name in can_buses:
            return name
        target = aliases.get(name)
        if isinstance(target, str) and target in can_buses:
            return target
        return name

    selected_bus = canonical_bus(requested_bus)
    resolved_default_bus = canonical_bus(profile_default_bus)

    raw_metadata = can_buses.get(selected_bus) or {}
    if not isinstance(raw_metadata, Mapping):
        raw_metadata = {}

    metadata = dict(raw_metadata)

    env_interface = env.get("OPEN_MMI_CAN_INTERFACE")
    if env_interface:
        interface = str(env_interface)
        interface_source = "env:OPEN_MMI_CAN_INTERFACE"
    elif metadata.get("interface"):
        interface = str(metadata["interface"])
        interface_source = f"profile:{selected_bus}.interface"
    elif metadata.get("tested_interface"):
        interface = str(metadata["tested_interface"])
        interface_source = f"profile:{selected_bus}.tested_interface"
    else:
        interface = default_interface
        interface_source = "default"

    return CanRuntimeConfig(
        name=selected_bus,
        default_bus=resolved_default_bus,
        interface=interface,
        interface_source=interface_source,
        bitrate=_optional_int(metadata.get("bitrate")),
        capture_point=metadata.get("capture_point"),
        provisioning=metadata.get("provisioning"),
        bring_up=_optional_bool(metadata.get("bring_up"), False),
        declared=selected_bus in can_buses,
        profile_has_buses=bool(can_buses),
        metadata=metadata,
        requested_name=requested_bus,
    )


def item_matches_bus(
    item: Mapping[str, Any],
    active_bus: str,
    default_bus: str = "comfort",
) -> bool:
    """Return whether a profile item belongs to the active named bus.

    Items without a ``bus`` field belong to the profile default bus. ``bus`` may be a
    single string or a list of strings.
    """

    bus_value = item.get("bus", default_bus)

    if bus_value is None:
        bus_value = default_bus

    if isinstance(bus_value, (list, tuple, set)):
        return str(active_bus) in {str(value) for value in bus_value}

    return str(bus_value) == str(active_bus)
