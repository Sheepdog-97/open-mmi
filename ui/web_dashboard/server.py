#!/usr/bin/env python3
"""Open MMI factory-style web dashboard.

Read-only local web UI for the persistent Open MMI status snapshot.
It does not subscribe to CAN directly and does not transmit/control the car.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict
from urllib.parse import parse_qs, urlparse

STALE_AFTER_SECONDS = 3.0


def default_status_path() -> Path:
    runtime_dir = os.getenv("XDG_RUNTIME_DIR")
    if runtime_dir:
        return Path(runtime_dir) / "open-mmi" / "status.json"
    return Path("/tmp/open-mmi-status.json")


def load_status(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r") as f:
            payload = json.load(f)
    except FileNotFoundError:
        return {
            "updated_at": None,
            "state": {},
            "error": "status file not found",
            "path": str(path),
        }
    except json.JSONDecodeError:
        return {
            "updated_at": None,
            "state": {},
            "error": "invalid status json",
            "path": str(path),
        }
    except Exception as exc:  # pragma: no cover - defensive path
        return {
            "updated_at": None,
            "state": {},
            "error": str(exc),
            "path": str(path),
        }

    if not isinstance(payload, dict):
        payload = {"updated_at": None, "state": {}, "error": "status root is not an object"}

    payload.setdefault("state", {})
    payload["path"] = str(path)
    return payload


def health_for(payload: Dict[str, Any]) -> Dict[str, Any]:
    updated_at = payload.get("updated_at")
    now = time.time()

    if payload.get("error"):
        return {
            "status": "error",
            "age_seconds": None,
            "stale": True,
            "message": payload["error"],
        }

    if updated_at is None:
        return {
            "status": "waiting",
            "age_seconds": None,
            "stale": True,
            "message": "waiting for status snapshot",
        }

    try:
        age = max(0.0, now - float(updated_at))
    except (TypeError, ValueError):
        return {
            "status": "error",
            "age_seconds": None,
            "stale": True,
            "message": "invalid updated_at value",
        }

    stale = age > STALE_AFTER_SECONDS
    return {
        "status": "stale" if stale else "live",
        "age_seconds": age,
        "stale": stale,
        "message": "stale" if stale else "live",
    }


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _wave(t: float, period: float, phase: float = 0.0) -> float:
    """Smooth -1..1 sine wave used by the dynamic demo generator."""
    return math.sin((t / period) * math.tau + phase)


def demo_status(scenario: str, started_at: float) -> Dict[str, Any]:
    """Return a synthetic, changing status payload for UI work away from the car.

    The real dashboard polls /api/status regularly. By generating values from
    elapsed time here, demo mode exercises the existing frontend without a fake
    CAN source, browser framework, or status writer process.
    """

    scenario = (scenario or "drive").strip().lower()
    aliases = {
        "1": "drive",
        "true": "drive",
        "live": "drive",
        "doors": "doors-open",
        "door": "doors-open",
        "warning": "warnings",
        "fault": "warnings",
    }
    scenario = aliases.get(scenario, scenario)

    now = time.time()
    t = max(0.0, now - started_at)

    # A gentle driving trace: accelerate, cruise, lift, repeat. Keep it smooth so
    # the current simple gauges do not jitter too much while still looking alive.
    speed_kmh = 62.0 + 38.0 * _wave(t, 18.0) + 8.0 * _wave(t, 5.5, 0.6)
    speed_kmh = _clamp(speed_kmh, 0.0, 135.0)

    # Approximate RPM response with small gear-change dips. It is deliberately
    # plausible-looking, not a vehicle model.
    shift_dip = max(0.0, _wave(t, 7.0, 1.2)) * 420.0
    rpm = 780.0 + speed_kmh * 31.0 + 280.0 * _wave(t, 4.0, 0.25) - shift_dip
    rpm = _clamp(rpm, 720.0, 5200.0)

    coolant_c = 88.0 + 5.0 * (1.0 - math.exp(-t / 80.0)) + 1.2 * _wave(t, 45.0)
    voltage_v = 13.85 + 0.12 * _wave(t, 9.0)
    outside_c = 12.2 + 0.6 * _wave(t, 60.0)
    blower_pct = 34.0 + 22.0 * (0.5 + 0.5 * _wave(t, 11.0, 0.7))
    dimmer_pct = 55.0 + 25.0 * (0.5 + 0.5 * _wave(t, 15.0, 0.9))
    odometer_km = 214302.4 + speed_kmh * t / 3600.0
    blink_on = int(t * 2.0) % 2 == 0

    vehicle = {
        "speed_kmh": round(speed_kmh, 1),
        "odometer_km": round(odometer_km, 1),
        "handbrake": False,
        "reverse": False,
    }
    engine = {
        "speed_rpm": round(rpm),
        "coolant_temp_c": round(coolant_c, 1),
    }
    electrical = {
        "supply_voltage_v": round(voltage_v, 2),
        "terminal30_voltage_v": round(voltage_v, 2),
    }
    recirculation_active = 45.0 < (t % 90.0) < 58.0
    climate = {
        "outside_temp_regulation_c": round(outside_c, 1),
        "outside_temp_unfiltered_c": round(outside_c + 0.3 * _wave(t, 8.0), 1),
        "blower_load_percent": round(blower_pct, 1),
        "rear_window_heater_requested": 20.0 < (t % 80.0) < 35.0,
        "recirculation_active": recirculation_active,
        "compressor_active": _wave(t, 30.0) > -0.35,
        "air_intake": "Recirc" if recirculation_active else "Normal",
    }
    lighting = {
        "mode": "Auto",
        "lights_on": _wave(t, 40.0) < 0.1,
        "left_indicator": False,
        "right_indicator": False,
        "hazards": False,
        "bulb_out": False,
        "dimmer_percent": round(dimmer_pct, 1),
    }
    doors = {
        "front_left": False,
        "front_right": False,
        "rear_left": False,
        "rear_right": False,
        "boot": False,
        "bonnet": False,
        "any_open": False,
    }

    if scenario == "traffic":
        stop_go = 0.5 + 0.5 * _wave(t, 16.0, -1.4)
        vehicle["speed_kmh"] = round(_clamp(stop_go * 42.0, 0.0, 48.0), 1)
        engine["speed_rpm"] = round(780.0 + stop_go * 1600.0 + 90.0 * _wave(t, 2.5))
        lighting["left_indicator"] = blink_on and 8.0 < (t % 28.0) < 16.0
        lighting["right_indicator"] = blink_on and 18.0 < (t % 34.0) < 26.0
    elif scenario == "doors-open":
        vehicle["speed_kmh"] = 0.0
        engine["speed_rpm"] = round(805.0 + 25.0 * _wave(t, 3.0))
        vehicle["handbrake"] = True
        doors["front_left"] = True
        doors["boot"] = (int(t / 4.0) % 2) == 0
        doors["any_open"] = any(doors.values())
    elif scenario == "reverse":
        vehicle["speed_kmh"] = round(3.0 + 2.0 * (0.5 + 0.5 * _wave(t, 5.0)), 1)
        engine["speed_rpm"] = round(880.0 + 130.0 * _wave(t, 3.5))
        vehicle["reverse"] = True
    elif scenario == "warnings":
        vehicle["speed_kmh"] = 0.0
        engine["speed_rpm"] = round(830.0 + 35.0 * _wave(t, 3.0))
        engine["coolant_temp_c"] = round(112.0 + 5.5 * (0.5 + 0.5 * _wave(t, 10.0)), 1)
        electrical["supply_voltage_v"] = round(11.4 + 0.15 * _wave(t, 6.0), 2)
        electrical["terminal30_voltage_v"] = electrical["supply_voltage_v"]
        vehicle["handbrake"] = True
        lighting["hazards"] = blink_on
        lighting["left_indicator"] = blink_on
        lighting["right_indicator"] = blink_on
        lighting["bulb_out"] = True
        doors["front_left"] = True
        doors["boot"] = True
        doors["any_open"] = True
    elif scenario == "stale":
        # Values move in the JSON, but updated_at is deliberately old so the
        # existing health indicator can be checked.
        pass

    updated_at = now - (STALE_AFTER_SECONDS + 3.0) if scenario == "stale" else now

    return {
        "updated_at": updated_at,
        "state": {
            "vehicle": vehicle,
            "engine": engine,
            "electrical": electrical,
            "climate": climate,
            "lighting": lighting,
            "fuel": {
                "level_l": round(max(0.0, 42.0 - (t / 900.0)), 1),
                "level_raw": round(max(0.0, 42.0 - (t / 900.0))),
                "low_level_warning": t % 900.0 > 840.0,
            },
            "doors": doors,
        },
        "path": f"demo://{scenario}",
        "demo": {
            "enabled": True,
            "scenario": scenario,
            "elapsed_seconds": round(t, 1),
        },
    }






# Jellyfin media provider. Keep the fallback so
# ``python ui/web_dashboard/server.py`` continues to work when executed
# directly rather than as a package module.
try:
    from ui.web_dashboard import jellyfin as jellyfin_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import jellyfin as jellyfin_backend  # type: ignore[no-redef]



# Radio is a separate provider module.  The absolute import supports package and
# spec-based test loading; the local fallback keeps ``python server.py`` working.
try:
    from ui.web_dashboard import radio as radio_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import radio as radio_backend  # type: ignore[no-redef]

# USB media provider. Keep the fallback so ``python ui/web_dashboard/server.py``
# continues to work when executed directly rather than as a package module.
try:
    from ui.web_dashboard import usb as usb_backend
except ModuleNotFoundError:  # pragma: no cover - direct-script compatibility
    import usb as usb_backend  # type: ignore[no-redef]

# Bluetooth media provider. Keep the fallback so
# ``python ui/web_dashboard/server.py`` continues to work when executed
# directly rather than as a package module.
try:
    from ui.web_dashboard import bluetooth as bluetooth_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import bluetooth as bluetooth_backend  # type: ignore[no-redef]
except ImportError as exc:  # pragma: no cover - older installed package fallback
    if exc.name != "ui.web_dashboard":
        raise
    import bluetooth as bluetooth_backend  # type: ignore[no-redef]

try:
    from ui.web_dashboard import versioning as versioning_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import versioning as versioning_backend  # type: ignore[no-redef]

# Local-only system and credential settings provider.
try:
    from ui.web_dashboard import system_settings as system_settings_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import system_settings as system_settings_backend  # type: ignore[no-redef]

try:
    from ui.web_dashboard import runtime_diagnostics as runtime_diagnostics_backend
except ModuleNotFoundError as exc:  # pragma: no cover - direct script fallback
    if exc.name not in {"ui", "ui.web_dashboard"}:
        raise
    import runtime_diagnostics as runtime_diagnostics_backend  # type: ignore[no-redef]


class DashboardHandler(SimpleHTTPRequestHandler):
    status_path: Path = default_status_path()
    static_dir: Path = Path(__file__).with_name("static")
    demo_mode: bool = False
    demo_scenario: str = "drive"
    demo_started_at: float = time.time()
    build_id: str = versioning_backend.resolve_build_id()
    frontend_id: str = build_id

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(self.static_dir), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.getenv("OPEN_MMI_WEB_LOG", "").strip():
            super().log_message(fmt, *args)

    def end_headers(self) -> None:
        cache_control = getattr(self, "_open_mmi_cache_control", "")
        if cache_control:
            self.send_header("Cache-Control", cache_control)
            self._open_mmi_cache_control = ""
        super().end_headers()

    def _send_html(self, body: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_index(self) -> None:
        template = (self.static_dir / "index.html").read_text(encoding="utf-8")
        self._send_html(versioning_backend.render_index(template, self.frontend_id))

    def _send_json(self, body: Dict[str, Any], status: int = 200) -> None:
        encoded = json.dumps(body, indent=2, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _current_payload(self, parsed_query: str) -> Dict[str, Any]:
        if self.demo_mode:
            query = parse_qs(parsed_query)
            scenario = query.get("demo", [self.demo_scenario])[0]
            return demo_status(scenario, self.demo_started_at)
        return load_status(self.status_path)


    # --- Open MMI Bluetooth media POST route start ---
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if system_settings_backend._handle_post(self, parsed.path):
            return
        if parsed.path != "/api/bluetooth/control":
            self.send_error(404)
            return
        if not bluetooth_backend._bluetooth_same_origin(self):
            self._send_json({"ok": False, "error": "Cross-origin control request rejected"}, 403)
            return
        try:
            payload = bluetooth_backend._bluetooth_json_body(self)
            result = bluetooth_backend._bluetooth_control(payload.get("player_id"), payload.get("action"))
            self._send_json(result)
        except ValueError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 400)
        except FileNotFoundError as exc:
            self._send_json({"ok": False, "error": str(exc)}, 409)
        except (RuntimeError, TimeoutError, OSError):
            self._send_json({"ok": False, "error": "Bluetooth media control failed"}, 502)
    # --- Open MMI Bluetooth media POST route end ---

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if system_settings_backend._handle_get(self, parsed.path):
            return

        if parsed.path == "/api/jellyfin/status":
            self._send_json(jellyfin_backend._jellyfin_status_payload(self.demo_mode))
            return

        if parsed.path == "/api/jellyfin/search":
            query = parse_qs(parsed.query or "")
            q = query.get("q", [""])[0]
            media_filter = query.get("filter", ["recent"])[0]
            try:
                limit = int(query.get("limit", ["24"])[0])
            except (TypeError, ValueError):
                limit = 24
            self._send_json(
                jellyfin_backend._jellyfin_search_payload(q, limit, media_filter, self.demo_mode)
            )
            return

        if parsed.path.startswith("/api/jellyfin/stream/"):
            from urllib.parse import unquote
            item_id = unquote(parsed.path.rsplit("/", 1)[-1])
            jellyfin_backend._jellyfin_proxy_audio(self, item_id)
            return

        if parsed.path.startswith("/api/jellyfin/image/"):
            from urllib.parse import unquote

            item_id = unquote(parsed.path.rsplit("/", 1)[-1])
            jellyfin_backend._jellyfin_proxy_image(self, item_id)
            return

        if parsed.path == "/api/radio/status":
            self._send_json(radio_backend._radio_status_payload())
            return
        if parsed.path == "/api/radio/options":
            try:
                self._send_json(radio_backend._radio_filter_options_payload())
            except Exception as exc:
                self._send_json({"configured": True, "source": "radio", "error": str(exc)}, 502)
            return
        if parsed.path == "/api/radio/search":
            query = parse_qs(parsed.query or "")
            q = query.get("q", [""])[0]
            media_filter = query.get("filter", ["popular"])[0]
            country_code = query.get("country", [""])[0]
            language = query.get("language", [""])[0]
            try:
                limit = int(query.get("limit", ["60"])[0])
            except (TypeError, ValueError):
                limit = 60
            self._send_json(
                radio_backend._radio_search_payload(
                    q,
                    limit,
                    media_filter,
                    country_code=country_code,
                    language=language,
                )
            )
            return
        if parsed.path.startswith("/api/radio/stream/"):
            from urllib.parse import unquote

            station_id = unquote(parsed.path.rsplit("/", 1)[-1])
            radio_backend._radio_proxy_audio(self, station_id)
            return


        # --- Open MMI USB media routes start ---
        if parsed.path == "/api/usb/status":
            self._send_json(usb_backend._usb_status_payload())
            return
        if parsed.path == "/api/usb/browse":
            query = parse_qs(parsed.query or "")
            directory_id = query.get("dir", [""])[0]
            q = query.get("q", [""])[0]
            media_filter = query.get("filter", ["browse"])[0]
            try:
                limit = int(query.get("limit", ["60"])[0])
                payload = usb_backend._usb_browse_payload(directory_id, q, limit, media_filter)
                self._send_json(payload)
            except ValueError as exc:
                self._send_json({"configured": True, "source": "usb", "items": [], "error": str(exc)}, 400)
            except PermissionError as exc:
                self._send_json({"configured": True, "source": "usb", "items": [], "error": str(exc)}, 403)
            except FileNotFoundError as exc:
                self._send_json({"configured": True, "source": "usb", "items": [], "error": str(exc)}, 404)
            except Exception:
                self._send_json({"configured": True, "source": "usb", "items": [], "error": "USB media browse failed"}, 500)
            return
        if parsed.path.startswith("/api/usb/stream/"):
            from urllib.parse import unquote

            item_id = unquote(parsed.path.rsplit("/", 1)[-1])
            usb_backend._usb_send_file(self, item_id)
            return
        if parsed.path.startswith("/api/usb/art/"):
            from urllib.parse import unquote

            item_id = unquote(parsed.path.rsplit("/", 1)[-1])
            usb_backend._usb_send_file(self, item_id, artwork=True)
            return
        # --- Open MMI USB media routes end ---

        # --- Open MMI Bluetooth media GET route start ---
        if parsed.path == "/api/bluetooth/status":
            self._send_json(bluetooth_backend._bluetooth_status_payload())
            return
        # --- Open MMI Bluetooth media GET route end ---
        if parsed.path == "/api/version":
            self._send_json(versioning_backend.version_payload(self.build_id))
            return

        if parsed.path == "/api/system/diagnostics/runtime":
            self._send_json(runtime_diagnostics_backend.runtime_diagnostics_payload())
            return

        if parsed.path == "/api/status":
            payload = self._current_payload(parsed.query)
            payload["health"] = health_for(payload)
            self._send_json(payload)
            return

        if parsed.path == "/api/health":
            payload = self._current_payload(parsed.query)
            self._send_json({"path": payload.get("path", str(self.status_path)), "health": health_for(payload)})
            return

        if parsed.path in {"/", "/index.html"}:
            self._send_index()
            return

        if parsed.path.endswith((".js", ".css")):
            query = parse_qs(parsed.query or "")
            version = query.get("v", [""])[0]
            if version and version == self.frontend_id:
                self._open_mmi_cache_control = "public, max-age=31536000, immutable"
            else:
                self._open_mmi_cache_control = "no-cache"
            self.path = parsed.path

        return super().do_GET()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Open MMI factory-style web dashboard")
    parser.add_argument("--host", default=os.getenv("OPEN_MMI_WEB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("OPEN_MMI_WEB_PORT", "8765")))
    parser.add_argument(
        "--path",
        type=Path,
        default=Path(os.getenv("OPEN_MMI_STATUS_PATH", str(default_status_path()))),
        help="status JSON path",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=os.getenv("OPEN_MMI_WEB_DEMO", "").strip().lower() in {"1", "true", "yes", "on"},
        help="serve a changing synthetic status snapshot instead of reading the status file",
    )
    parser.add_argument(
        "--demo-scenario",
        default=os.getenv("OPEN_MMI_WEB_DEMO_SCENARIO", "drive"),
        choices=("drive", "traffic", "doors-open", "reverse", "warnings", "stale"),
        help="demo scenario to use when --demo is enabled",
    )

    args = parser.parse_args()

    DashboardHandler.status_path = args.path
    DashboardHandler.demo_mode = args.demo
    DashboardHandler.demo_scenario = args.demo_scenario
    DashboardHandler.demo_started_at = time.time()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Open MMI web dashboard: http://{args.host}:{args.port}")
    if args.demo:
        print(f"Demo mode: {args.demo_scenario}")
    else:
        print(f"Status snapshot: {args.path}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
