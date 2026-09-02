"""Local-only dashboard configuration endpoints.

This module keeps privileged-looking operations narrow and fixed. It never
returns Jellyfin secrets and refuses configuration writes from non-loopback
clients or cross-origin browser requests.
"""

from __future__ import annotations

import ipaddress
import json
import sys
import threading
import time
from typing import Any, Dict, Mapping
from urllib.parse import urlparse

try:
    from ui import egress_client, launcher, owner_config_client, update_coordinator, update_readiness, vehicle_store_client
    from ui import vehicle_catalogue, vehicle_config_coordinator, vehicle_setup
    from ui.configuration import (
        ConfigurationError,
        client_is_loopback,
        restart_dashboard,
    )
    from ui.web_dashboard import update_status
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name != "ui":
        raise
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2]))
    from ui import egress_client, launcher, owner_config_client, update_coordinator, update_readiness, vehicle_store_client
    from ui import vehicle_catalogue, vehicle_config_coordinator, vehicle_setup
    from ui.configuration import (
        ConfigurationError,
        client_is_loopback,
        restart_dashboard,
    )
    from ui.web_dashboard import update_status

SYSTEM_MAX_BODY_BYTES = 16 * 1024
SYSTEM_CUSTOM_EDIT_MAX_BODY_BYTES = vehicle_setup.MAX_PROFILE_BYTES * 6 + SYSTEM_MAX_BODY_BYTES


def _unique_json_object(pairs: list[tuple[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"Duplicate JSON field: {key}")
        payload[key] = value
    return payload


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number: {value}")


def _same_origin(handler: Any) -> bool:
    origin = str(handler.headers.get("Origin") or "").strip()
    if not origin:
        return True
    host = str(handler.headers.get("Host") or "").strip().casefold()
    try:
        parsed = urlparse(origin)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and parsed.netloc.casefold() == host


def _loopback_host(handler: Any) -> bool:
    host = str(handler.headers.get("Host") or "").strip()
    try:
        hostname = urlparse(f"//{host}").hostname or ""
        if hostname.casefold() == "localhost":
            return True
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def _request_allowed(handler: Any) -> bool:
    return (
        client_is_loopback(getattr(handler, "client_address", None))
        and _loopback_host(handler)
        and _same_origin(handler)
    )


def _json_body(handler: Any, *, maximum_bytes: int = SYSTEM_MAX_BODY_BYTES) -> Dict[str, Any]:
    content_type = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ValueError("Configuration requests require application/json")
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid request length") from exc
    if length <= 0 or length > maximum_bytes:
        raise ValueError("Invalid request length")
    try:
        payload = json.loads(
            handler.rfile.read(length).decode("utf-8"),
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Invalid JSON request") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON request must be an object")
    return payload


def _launcher_status() -> Dict[str, Any]:
    path = launcher.default_config_path()
    config = launcher.load_config(path)
    return launcher.status_payload(config, path)


def _settings_status() -> Dict[str, Any]:
    return {
        "local_only": True,
        "launcher": _launcher_status(),
        "jellyfin": egress_client.jellyfin_status(),
    }


def _update_launcher(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return owner_config_client.update_launcher(payload)


def _jellyfin_authority_change_requires_cli() -> ConfigurationError:
    return ConfigurationError(
        "Jellyfin network authority is root-owned; use sudo open-mmi-config jellyfin setup or clear"
    )


def _test_jellyfin(payload: Mapping[str, Any]) -> Dict[str, Any]:
    del payload
    raise _jellyfin_authority_change_requires_cli()


def _save_jellyfin(payload: Mapping[str, Any]) -> Dict[str, Any]:
    del payload
    raise _jellyfin_authority_change_requires_cli()


def _clear_jellyfin() -> Dict[str, Any]:
    raise _jellyfin_authority_change_requires_cli()


def _restart_after_response(delay: float = 0.25) -> None:
    def worker() -> None:
        time.sleep(delay)
        try:
            restart_dashboard()
        except Exception:
            # The caller has already received a response. systemd/journal carries
            # the actionable failure without exposing internals to the browser.
            return

    threading.Thread(target=worker, name="open-mmi-dashboard-restart", daemon=True).start()


def _handle_get(handler: Any, path: str) -> bool:
    routes = {
        "/api/system/settings": _settings_status,
        "/api/system/vehicle-setup": vehicle_setup.status_payload,
        "/api/system/vehicle-setup/coordinator": vehicle_config_coordinator.client_status,
        "/api/system/update-status": update_status.status_payload,
        "/api/system/update-readiness": lambda: update_readiness.readiness_payload(update_status.status_payload()),
        "/api/system/update-coordinator": update_coordinator.client_status,
        "/api/system/service-reminder": vehicle_store_client.service_reminder_status,
        "/api/system/trip-distance": vehicle_store_client.trip_distance_status,
        "/api/system/trip-a": vehicle_store_client.trip_a_status,
        "/api/system/trip-b": vehicle_store_client.trip_b_status,
    }
    if path not in routes:
        return False
    if not _request_allowed(handler):
        handler._send_json({"ok": False, "error": "Local configuration access required"}, 403)
        return True
    try:
        handler._send_json(routes[path]())
    except ConfigurationError as exc:
        handler._send_json({"ok": False, "error": str(exc)}, 400)
    except (
        update_coordinator.CoordinatorError,
        vehicle_config_coordinator.CoordinatorError,
        vehicle_store_client.VehicleStoreClientError,
    ) as exc:
        handler._send_json({"ok": False, "error": str(exc)}, 502)
    except (RuntimeError, TimeoutError, OSError):
        handler._send_json({"ok": False, "error": "System status operation failed"}, 502)
    return True


def _handle_post(handler: Any, path: str) -> bool:
    routes = {
        "/api/system/launcher": _update_launcher,
        "/api/system/jellyfin/test": _test_jellyfin,
        "/api/system/jellyfin": _save_jellyfin,
    }
    if path not in routes and path not in {
        "/api/system/jellyfin/clear",
        "/api/system/dashboard/restart",
        "/api/system/vehicle-setup/preview",
        "/api/system/vehicle-setup/apply",
        "/api/system/vehicle-custom/create",
        "/api/system/vehicle-custom/load",
        "/api/system/vehicle-custom/save",
        "/api/system/vehicle-custom/manage",
        "/api/system/vehicle-custom/import",
        "/api/system/update-check",
        "/api/system/update-prepare",
        "/api/system/update-install",
        "/api/system/service-reminder/settings",
        "/api/system/service-reminder/reset",
        "/api/system/trip-a/reset",
        "/api/system/trip-a/settings",
        "/api/system/trip-a/observe",
        "/api/system/trip-b/reset",
        "/api/system/trip-distance/observe",
        "/api/system/service-reminder/acknowledge",
    }:
        return False
    if not _request_allowed(handler):
        handler._send_json({"ok": False, "error": "Local same-origin configuration access required"}, 403)
        return True

    try:
        if path == "/api/system/jellyfin/clear":
            payload = _json_body(handler)
            if payload not in ({}, {"confirm": True}):
                raise ValueError("Invalid clear request")
            result = _clear_jellyfin()
        elif path == "/api/system/vehicle-setup/preview":
            result = vehicle_config_coordinator.client_preview(_json_body(handler))
        elif path == "/api/system/vehicle-setup/apply":
            result = vehicle_config_coordinator.client_apply(_json_body(handler))
        elif path == "/api/system/vehicle-custom/create":
            result = owner_config_client.create_custom(_json_body(handler))
        elif path == "/api/system/vehicle-custom/load":
            result = vehicle_catalogue.load_custom_item(_json_body(handler))
        elif path == "/api/system/vehicle-custom/save":
            result = owner_config_client.save_custom(
                _json_body(handler, maximum_bytes=SYSTEM_CUSTOM_EDIT_MAX_BODY_BYTES)
            )
        elif path == "/api/system/vehicle-custom/manage":
            result = owner_config_client.manage_custom(_json_body(handler))
        elif path == "/api/system/vehicle-custom/import":
            result = owner_config_client.import_custom(
                _json_body(handler, maximum_bytes=SYSTEM_CUSTOM_EDIT_MAX_BODY_BYTES)
            )
        elif path == "/api/system/service-reminder/settings":
            result = vehicle_store_client.service_reminder_settings(_json_body(handler))
        elif path == "/api/system/service-reminder/reset":
            result = vehicle_store_client.service_reminder_reset(_json_body(handler))
        elif path == "/api/system/trip-a/reset":
            result = vehicle_store_client.trip_a_reset(_json_body(handler))
        elif path == "/api/system/trip-a/settings":
            result = vehicle_store_client.trip_a_settings(_json_body(handler))
        elif path == "/api/system/trip-a/observe":
            result = vehicle_store_client.trip_a_observe(_json_body(handler))
        elif path == "/api/system/trip-b/reset":
            result = vehicle_store_client.trip_b_reset(_json_body(handler))
        elif path == "/api/system/trip-distance/observe":
            result = vehicle_store_client.trip_distance_observe(_json_body(handler))
        elif path == "/api/system/service-reminder/acknowledge":
            result = vehicle_store_client.service_reminder_acknowledge(_json_body(handler))
        elif path == "/api/system/update-check":
            payload = _json_body(handler)
            if payload not in ({}, {"confirm": True}):
                raise ValueError("Invalid update check request")
            result = update_coordinator.client_check()
        elif path == "/api/system/update-prepare":
            payload = _json_body(handler)
            if payload != {"confirm": True}:
                raise ValueError("Invalid update preparation request")
            result = update_coordinator.client_prepare()
        elif path == "/api/system/update-install":
            payload = _json_body(handler)
            if payload != {"confirm": True}:
                raise ValueError("Invalid update installation request")
            result = update_coordinator.client_install()
        elif path == "/api/system/dashboard/restart":
            payload = _json_body(handler)
            if payload not in ({}, {"confirm": True}):
                raise ValueError("Invalid restart request")
            result = {"ok": True, "service": "open-mmi-dashboard.service", "restarting": True}
            _restart_after_response()
        else:
            result = routes[path](_json_body(handler))
        handler._send_json(result)
    except owner_config_client.OwnerConfigConflictError as exc:
        handler._send_json(
            {"ok": False, "code": exc.code, "error": str(exc)},
            409,
        )
    except vehicle_catalogue.VehicleCatalogueConflictError as exc:
        handler._send_json(
            {"ok": False, "code": exc.code, "error": str(exc)},
            409,
        )
    except vehicle_config_coordinator.CoordinatorConflictError as exc:
        handler._send_json(
            {"ok": False, "code": exc.code, "error": str(exc)},
            409,
        )
    except vehicle_config_coordinator.CoordinatorApplyError as exc:
        handler._send_json(
            {
                "ok": False,
                "code": exc.code,
                "error": str(exc),
                "state": exc.state,
            },
            500,
        )
    except vehicle_config_coordinator.CoordinatorUnavailableError as exc:
        handler._send_json({"ok": False, "error": str(exc)}, 502)
    except (
        ValueError,
        ConfigurationError,
        launcher.LauncherError,
        update_coordinator.CoordinatorError,
        vehicle_config_coordinator.CoordinatorError,
        vehicle_store_client.VehicleStoreClientError,
        owner_config_client.OwnerConfigClientError,
        update_status.UpdateStatusError,
        vehicle_catalogue.VehicleCatalogueError,
        vehicle_setup.VehicleSetupError,
    ) as exc:
        handler._send_json({"ok": False, "error": str(exc)}, 400)
    except (RuntimeError, TimeoutError, OSError):
        handler._send_json({"ok": False, "error": "Configuration operation failed"}, 502)
    return True
