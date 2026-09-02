from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ui import egress_client, media_egress, media_egress_config
from ui.web_dashboard import jellyfin, radio


ROOT = Path(__file__).resolve().parents[1]


class NetworkEgressUnitTests(unittest.TestCase):
    def test_dashboard_is_loopback_only(self):
        unit = (ROOT / "systemd/user/open-mmi-dashboard.service").read_text(encoding="utf-8")
        self.assertIn("IPAddressDeny=any", unit)
        self.assertIn("IPAddressAllow=localhost", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", unit)

    def test_canbusd_cannot_create_inet_sockets(self):
        unit = (ROOT / "systemd/user/canbusd.service").read_text(encoding="utf-8")
        self.assertIn("RestrictAddressFamilies=AF_CAN AF_UNIX", unit)
        self.assertNotIn("AF_INET", unit)
        self.assertNotIn("AF_INET6", unit)

    def test_media_egress_is_separate_unprivileged_system_service(self):
        unit = (ROOT / "systemd/system/open-mmi-media-egress.service").read_text(encoding="utf-8")
        self.assertIn("DynamicUser=yes", unit)
        self.assertIn("SupplementaryGroups=open-mmi", unit)
        self.assertIn("ProtectHome=yes", unit)
        self.assertIn("ProtectSystem=strict", unit)
        self.assertIn("CapabilityBoundingSet=\n", unit)
        self.assertIn("AmbientCapabilities=\n", unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6", unit)
        self.assertIn("ExecStart=/opt/open-mmi/venv/bin/python -I -m ui.media_egress serve", unit)
        self.assertIn("LoadCredential=media-config:/var/lib/open-mmi/network-egress/media.v1.json", unit)

    def test_release_integrity_tracks_media_egress_unit(self):
        from open_mmi_trust import release_integrity

        self.assertIn(
            "open-mmi-media-egress.service",
            release_integrity.PRIVILEGED_SYSTEM_UNITS,
        )


class MediaEgressProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket = Path(self.temp.name) / "egress.sock"
        self.config_path = Path(self.temp.name) / "media.json"
        config = media_egress_config.empty_config()
        config["jellyfin"] = {
            "OPEN_MMI_JELLYFIN_URL": "https://jellyfin.test",
            "OPEN_MMI_JELLYFIN_TOKEN": "token",
            "OPEN_MMI_JELLYFIN_USER_ID": "user-1",
        }
        self.config_path.write_text(__import__("json").dumps(config), encoding="utf-8")
        group = SimpleNamespace(gr_gid=os.getgid())
        self.group_patch = patch.object(media_egress.grp, "getgrnam", return_value=group)
        self.group_patch.start()
        self.server = media_egress.MediaEgressServer(self.socket)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(
            os.environ,
            {
                "OPEN_MMI_MEDIA_EGRESS_SOCKET": str(self.socket),
                "OPEN_MMI_MEDIA_EGRESS_CONFIG": str(self.config_path),
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.group_patch.stop()
        self.temp.cleanup()

    def test_radio_status_crosses_only_local_unix_rpc(self):
        config = radio._radio_config()
        result = egress_client.request_json(
            "/v1/media/proxy",
            {
                "source": "radio",
                "path": "/api/radio/status",
                "query": "",
                "demo_mode": False,
                "range": "",
            },
        )
        self.assertEqual(result["source"], "radio")
        self.assertTrue(result["configured"])

    def test_unknown_media_purpose_fails_closed(self):
        with self.assertRaisesRegex(egress_client.EgressClientError, "purpose is not declared"):
            egress_client.request_json(
                "/v1/media/proxy",
                {
                    "source": "telemetry",
                    "path": "/anything",
                    "query": "",
                    "demo_mode": False,
                    "range": "",
                },
            )

    def test_jellyfin_test_uses_broker_and_exact_config(self):
        expected = {"connected": True, "server_name": "test"}
        config = jellyfin._jellyfin_config_from_mapping(
            media_egress_config.read_config(self.config_path)["jellyfin"]
        )
        with patch.object(
            media_egress.jellyfin,
            "_jellyfin_test_connection",
            return_value=expected,
        ) as test:
            result = egress_client.test_jellyfin()
        self.assertEqual(result, expected)
        test.assert_called_once_with(config)


    def test_rpc_cannot_override_radio_destination(self):
        with self.assertRaisesRegex(egress_client.EgressClientError, "fields are invalid"):
            egress_client.request_json(
                "/v1/media/proxy",
                {
                    "source": "radio",
                    "path": "/api/radio/status",
                    "query": "",
                    "demo_mode": False,
                    "range": "",
                    "config": {"url": "https://evil.test"},
                },
            )

    def test_broker_rejects_generic_arbitrary_url_operation(self):
        config = radio._radio_config()
        with self.assertRaisesRegex(egress_client.EgressClientError, "operation is not declared"):
            egress_client.request_json(
                "/v1/media/proxy",
                {
                    "source": "radio",
                    "path": "https://example.test/arbitrary",
                    "query": "",
                        "demo_mode": False,
                    "range": "",
                },
            )


if __name__ == "__main__":
    unittest.main()
