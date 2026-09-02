from __future__ import annotations

import inspect
import subprocess
import sys
import unittest
from pathlib import Path

from ui import vehicle_setup
from ui.web_dashboard import bluetooth, jellyfin, radio, runtime_diagnostics, server, system_settings, update_status, usb


ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = ROOT / "ui" / "web_dashboard" / "server.py"


class DashboardModuleBoundaryTests(unittest.TestCase):
    def test_radio_provider_does_not_depend_on_dashboard_handler(self):
        self.assertFalse(hasattr(radio, "DashboardHandler"))
        source = inspect.getsource(radio)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)

    def test_radio_can_import_without_importing_server(self):
        command = (
            "import sys; "
            "import ui.web_dashboard.radio; "
            "assert 'ui.web_dashboard.server' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_routes_radio_through_egress_broker(self):
        source = inspect.getsource(server.DashboardHandler.do_GET)
        self.assertIn('parsed.path.startswith("/api/radio/")', source)
        self.assertIn("egress_client.proxy_media(", source)
        self.assertIn('source="radio"', source)
        self.assertNotIn("config=radio_backend._radio_config()", source)
        self.assertNotIn("config=", source[source.index('source="radio"'):source.index('source="radio"') + 180])
        self.assertNotIn("radio_backend._radio_proxy_audio", source)

    def test_jellyfin_provider_does_not_depend_on_dashboard_handler(self):
        self.assertFalse(hasattr(jellyfin, "DashboardHandler"))
        source = inspect.getsource(jellyfin)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)

    def test_jellyfin_can_import_without_importing_server(self):
        command = (
            "import sys; "
            "import ui.web_dashboard.jellyfin; "
            "assert 'ui.web_dashboard.server' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_routes_jellyfin_through_egress_broker(self):
        source = inspect.getsource(server.DashboardHandler.do_GET)
        self.assertIn('parsed.path.startswith("/api/jellyfin/")', source)
        self.assertIn("egress_client.proxy_media(", source)
        self.assertIn('source="jellyfin"', source)
        self.assertNotIn("config=jellyfin_backend._jellyfin_config()", source)
        self.assertNotIn("config=", source[source.index('source="jellyfin"'):source.index('source="jellyfin"') + 180])
        self.assertNotIn("jellyfin_backend._jellyfin_proxy_audio", source)
        self.assertNotIn("jellyfin_backend._jellyfin_proxy_image", source)

    def test_usb_provider_does_not_depend_on_dashboard_handler(self):
        self.assertFalse(hasattr(usb, "DashboardHandler"))
        source = inspect.getsource(usb)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)

    def test_usb_can_import_without_importing_server(self):
        command = (
            "import sys; "
            "import ui.web_dashboard.usb; "
            "assert 'ui.web_dashboard.server' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_routes_delegate_to_usb_provider(self):
        source = inspect.getsource(server.DashboardHandler.do_GET)
        self.assertIn("usb_backend._usb_status_payload()", source)
        self.assertIn("usb_backend._usb_browse_payload(", source)
        self.assertIn("usb_backend._usb_send_file(self, item_id)", source)
        self.assertIn(
            "usb_backend._usb_send_file(self, item_id, artwork=True)", source
        )

    def test_bluetooth_provider_does_not_depend_on_dashboard_handler(self):
        self.assertFalse(hasattr(bluetooth, "DashboardHandler"))
        source = inspect.getsource(bluetooth)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)

    def test_bluetooth_can_import_without_importing_server(self):
        command = (
            "import sys; "
            "import ui.web_dashboard.bluetooth; "
            "assert 'ui.web_dashboard.server' not in sys.modules"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_server_routes_delegate_to_bluetooth_provider(self):
        get_source = inspect.getsource(server.DashboardHandler.do_GET)
        post_source = inspect.getsource(server.DashboardHandler.do_POST)
        self.assertIn("bluetooth_backend._bluetooth_status_payload()", get_source)
        self.assertIn("bluetooth_backend._bluetooth_same_origin(self)", post_source)
        self.assertIn("bluetooth_backend._bluetooth_json_body(self)", post_source)
        self.assertIn("bluetooth_backend._bluetooth_control(", post_source)



    def test_server_routes_delegate_to_local_system_settings_provider(self):
        get_source = inspect.getsource(server.DashboardHandler.do_GET)
        post_source = inspect.getsource(server.DashboardHandler.do_POST)
        self.assertIn("system_settings_backend._handle_get(self, parsed.path)", get_source)
        self.assertIn("system_settings_backend._handle_post(self, parsed.path)", post_source)
        self.assertFalse(hasattr(system_settings, "DashboardHandler"))

    def test_vehicle_setup_status_provider_is_server_independent(self):
        routes_source = inspect.getsource(system_settings._handle_get)
        post_routes_source = inspect.getsource(system_settings._handle_post)
        self.assertIn(
            '"/api/system/vehicle-setup": vehicle_setup.status_payload',
            routes_source,
        )
        self.assertIn(
            '"/api/system/vehicle-setup/preview"',
            post_routes_source,
        )
        self.assertIn(
            "vehicle_config_coordinator.client_preview(_json_body(handler))",
            post_routes_source,
        )
        self.assertIn(
            '"/api/system/vehicle-setup/apply"',
            post_routes_source,
        )
        self.assertIn(
            "vehicle_config_coordinator.client_apply(_json_body(handler))",
            post_routes_source,
        )
        self.assertFalse(hasattr(vehicle_setup, "DashboardHandler"))
        source = inspect.getsource(vehicle_setup)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("shell=True", source)

    def test_update_status_provider_is_server_independent_and_uses_argument_lists(self):
        self.assertFalse(hasattr(update_status, "DashboardHandler"))
        source = inspect.getsource(update_status)
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("shell=True", source)
        self.assertIn('["git", "-C", str(repository), *arguments]', source)
        self.assertIn('GIT_TERMINAL_PROMPT', source)
        self.assertIn('ls-remote', source)

    def test_server_routes_delegate_to_runtime_diagnostics_provider(self):
        get_source = inspect.getsource(server.DashboardHandler.do_GET)
        self.assertIn(
            "runtime_diagnostics_backend.runtime_diagnostics_payload()",
            get_source,
        )
        self.assertFalse(hasattr(runtime_diagnostics, "DashboardHandler"))
        source = inspect.getsource(runtime_diagnostics)
        self.assertNotIn("from ui.web_dashboard.server", source)

    def test_server_does_not_reexport_provider_private_helpers(self):
        private_names = (
            "_jellyfin_request_json",
            "_radio_open_stream",
            "_usb_open_file",
            "_bluetooth_control",
            "_JELLYFIN_LOGIN_CACHE",
            "_USB_ID_REGISTRY",
            "_BLUETOOTH_STATUS_CACHE",
        )
        for name in private_names:
            with self.subTest(name=name):
                self.assertFalse(hasattr(server, name))

    def test_dashboard_still_runs_as_a_direct_script(self):
        result = subprocess.run(
            [sys.executable, str(SERVER_PATH), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Open MMI factory-style web dashboard", result.stdout)


if __name__ == "__main__":
    unittest.main()
