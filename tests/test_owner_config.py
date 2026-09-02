from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from open_mmi_trust import release_integrity
from ui import owner_config, owner_config_client

ROOT = Path(__file__).resolve().parents[1]


class OwnerConfigTests(unittest.TestCase):
    def test_launcher_preferences_cross_fixed_unix_broker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "run"
            config_home = root / "config"
            home = root / "home"
            runtime.mkdir()
            config_home.mkdir()
            home.mkdir()
            socket_path = runtime / "owner.sock"
            server = owner_config.OwnerConfigServer(socket_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch.dict(
                os.environ,
                {
                    "OPEN_MMI_OWNER_CONFIG_SOCKET": str(socket_path),
                    "XDG_CONFIG_HOME": str(config_home),
                    "HOME": str(home),
                },
                clear=False,
            ), patch.object(
                owner_config.launcher, "configure_open_at_login"
            ) as autostart, patch.object(
                owner_config.launcher, "status_payload", return_value={"default_ui": "tui"}
            ):
                thread.start()
                try:
                    result = owner_config_client.update_launcher(
                        {"default_ui": "tui", "open_at_login": False}
                    )
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)
            self.assertTrue(result["ok"])
            autostart.assert_called_once_with(False)
            self.assertEqual(
                (config_home / "open-mmi" / "launcher.json")
                .read_text(encoding="utf-8")
                .count('"default_ui": "tui"'),
                1,
            )

    def test_unknown_owner_config_operation_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            socket_path = root / "owner.sock"
            server = owner_config.OwnerConfigServer(socket_path)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            with patch.dict(os.environ, {"OPEN_MMI_OWNER_CONFIG_SOCKET": str(socket_path)}):
                thread.start()
                try:
                    with self.assertRaisesRegex(
                        owner_config_client.OwnerConfigClientError,
                        "operation is not declared",
                    ):
                        owner_config_client.request_json("/v1/arbitrary", {"value": "vehicle-data"})
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

    def test_owner_config_unit_is_home_scoped_and_unix_only(self):
        unit = (ROOT / "systemd/user/open-mmi-owner-config.service").read_text(encoding="utf-8")
        self.assertIn("ProtectHome=read-only", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("ReadWritePaths=%h/.config/open-mmi %h/.config/autostart", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", unit)
        self.assertNotIn("AF_INET", unit)
        self.assertIn("open-mmi-owner-config.service", release_integrity.PRIVILEGED_USER_UNITS)

    def test_dashboard_has_no_direct_owner_config_write_calls(self):
        source = (ROOT / "ui/web_dashboard/system_settings.py").read_text(encoding="utf-8")
        self.assertIn("owner_config_client.update_launcher", source)
        self.assertIn("owner_config_client.save_custom", source)
        self.assertNotIn("launcher.save_preferences", source)
        self.assertNotIn("vehicle_catalogue.save_custom_item", source)
        self.assertNotIn("vehicle_catalogue.import_custom_item", source)


if __name__ == "__main__":
    unittest.main()
