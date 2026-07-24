"""Verify and enable wake support for a SocketCAN USB adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from powerd.runtime import PHYSICAL_CAN_INTERFACE_RE


DEFAULT_SYS_CLASS_NET = Path("/sys/class/net")


@dataclass(frozen=True)
class WakeNode:
    device: Path
    subsystem: str
    control: Path


def _subsystem_name(path: Path) -> str:
    try:
        return (path / "subsystem").resolve(strict=True).name
    except OSError:
        return ""


def _enabled(path: Path) -> bool:
    try:
        return path.read_text(encoding="utf-8").strip() == "enabled"
    except OSError:
        return False


def _wake_nodes(
    interface: str,
    sys_class_net: Path,
) -> Optional[tuple[WakeNode, ...]]:
    if not PHYSICAL_CAN_INTERFACE_RE.fullmatch(interface):
        return None

    try:
        current = (sys_class_net / interface / "device").resolve(strict=True)
    except OSError:
        return None

    wake_nodes: list[WakeNode] = []
    visited: set[Path] = set()
    while current not in visited:
        visited.add(current)
        wake = current / "power" / "wakeup"
        if wake.is_file():
            wake_nodes.append(
                WakeNode(
                    device=current,
                    subsystem=_subsystem_name(current),
                    control=wake,
                )
            )
        parent = current.parent
        if parent == current:
            break
        current = parent

    return tuple(wake_nodes)


def _required_topology_present(wake_nodes: tuple[WakeNode, ...]) -> bool:
    direct_usb = any(
        node.subsystem == "usb"
        and not node.device.name.startswith("usb")
        and ":" not in node.device.name
        for node in wake_nodes
    )
    pci_controller = any(node.subsystem == "pci" for node in wake_nodes)
    return direct_usb and pci_controller


def enable_remote_wake(
    interface: str,
    sys_class_net: Path = DEFAULT_SYS_CLASS_NET,
) -> bool:
    """Enable every verified wake control in the adapter's host ancestry.

    The topology is validated before any write occurs. This prevents a partial
    or unrelated sysfs path from being modified when the direct USB adapter or
    its PCI host controller cannot be identified.
    """

    wake_nodes = _wake_nodes(interface, sys_class_net)
    if not wake_nodes or not _required_topology_present(wake_nodes):
        return False

    for node in wake_nodes:
        if _enabled(node.control):
            continue
        try:
            node.control.write_text("enabled\n", encoding="utf-8")
        except OSError:
            return False

    return all(_enabled(node.control) for node in wake_nodes)


def remote_wake_ready(
    interface: str,
    sys_class_net: Path = DEFAULT_SYS_CLASS_NET,
) -> bool:
    """Return whether the adapter and host wake ancestry are enabled.

    The check is topology-driven. It does not assume a USB port or PCI address.
    A direct USB device and a PCI controller must both expose enabled wake nodes;
    every additional wake node found on the parent chain must also be enabled.
    """

    wake_nodes = _wake_nodes(interface, sys_class_net)
    return bool(
        wake_nodes
        and _required_topology_present(wake_nodes)
        and all(_enabled(node.control) for node in wake_nodes)
    )
