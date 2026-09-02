from __future__ import annotations

import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from open_mmi_trust.inspector import (
    PASS,
    UNVERIFIED,
    _inspect_vehicle_identity_remote_resolution_enforcement,
    _vehicle_identity_source_contract,
    _vehicle_identity_unit_contract,
)
from open_mmi_trust.manifest import load_manifest
from open_mmi_trust.vehicle_identity import (
    RemoteVehicleIdentityDenied,
    contains_vehicle_identity_material,
    require_remote_identity_safe,
)
from ui import egress_client, media_egress


ROOT = Path(__file__).resolve().parents[1]
VIN = "WVWZZZ1KZ6W000001"


class VehicleIdentityGuardTests(unittest.TestCase):
    def test_canonical_vin_is_identity_material(self):
        self.assertTrue(contains_vehicle_identity_material(VIN))
        self.assertTrue(contains_vehicle_identity_material(f"lookup {VIN}"))

    def test_explicit_identity_label_is_rejected_even_without_full_vin(self):
        for value in (
            "vin:partial",
            "vin_hash=abc123",
            "vehicle_identity:lookup-me",
            "registration_plate=AB12CDE",
            "license-plate:AB12CDE",
        ):
            with self.subTest(value=value):
                self.assertTrue(contains_vehicle_identity_material(value))

    def test_normal_media_search_is_not_identity_material(self):
        for value in ("Vin Diesel soundtrack", "registration song", "plate reverb", "WVWZZZ"):
            with self.subTest(value=value):
                self.assertFalse(contains_vehicle_identity_material(value))

    def test_denial_does_not_echo_identity(self):
        with self.assertRaises(RemoteVehicleIdentityDenied) as caught:
            require_remote_identity_safe([VIN])
        self.assertNotIn(VIN, str(caught.exception))


class VehicleIdentityMediaBrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.socket = Path(self.temp.name) / "egress.sock"
        group = SimpleNamespace(gr_gid=os.getgid())
        self.group_patch = patch.object(media_egress.grp, "getgrnam", return_value=group)
        self.group_patch.start()
        self.server = media_egress.MediaEgressServer(self.socket)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.env = patch.dict(
            os.environ,
            {"OPEN_MMI_MEDIA_EGRESS_SOCKET": str(self.socket)},
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

    def _request(self, query: str) -> None:
        egress_client.request_json(
            "/v1/media/proxy",
            {
                "source": "radio",
                "path": "/api/radio/search",
                "query": query,
                "demo_mode": False,
                "range": "",
            },
        )

    def test_raw_vin_search_is_denied_before_remote_media_call(self):
        with patch.object(media_egress.radio, "_radio_search_payload") as search:
            with self.assertRaisesRegex(
                egress_client.EgressClientError,
                "vehicle identity material is prohibited from remote egress",
            ):
                self._request(f"q={VIN}")
        search.assert_not_called()

    def test_urlencoded_identity_label_is_denied_before_remote_media_call(self):
        with patch.object(media_egress.radio, "_radio_search_payload") as search:
            with self.assertRaisesRegex(
                egress_client.EgressClientError,
                "vehicle identity material is prohibited from remote egress",
            ):
                self._request("q=vin%3Apartial")
        search.assert_not_called()


class VehicleIdentityEnforcementTests(unittest.TestCase):
    def _source_fixture(self, destination: Path) -> None:
        for relative in (
            "open_mmi_telemetry/guard.py",
            "open_mmi_telemetry/cli.py",
            "open_mmi_trust/vehicle_identity.py",
            "open_mmi_trust/inspector.py",
            "open_mmi_trust/accepted_state.py",
            "open_mmi_trust/manifest.py",
            "ui/media_egress.py",
            "ui/update_coordinator.py",
        ):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

    def test_os_units_separate_identity_sources_from_network_actors(self):
        failures, evidence = _vehicle_identity_unit_contract(ROOT)
        self.assertEqual(failures, [])
        self.assertIn("systemd/system/open-mmi-media-egress.service", evidence)
        self.assertIn("systemd/system/open-mmi-update-coordinator.service", evidence)

    def test_source_tripwire_accepts_current_local_only_identity_paths(self):
        self.assertEqual(_vehicle_identity_source_contract(ROOT), [])

    def test_source_tripwire_rejects_new_identity_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            self._source_fixture(fixture)
            bad = fixture / "ui" / "remote_vehicle_lookup.py"
            bad.write_text(f'VIN = "{VIN}"\n', encoding="utf-8")
            failures = _vehicle_identity_source_contract(fixture)
        self.assertTrue(
            any("remote_vehicle_lookup.py:undeclared-identity-consumer" in item for item in failures),
            failures,
        )

    def test_source_tripwire_rejects_identity_import_in_network_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            self._source_fixture(fixture)
            path = fixture / "ui" / "update_coordinator.py"
            path.write_text(
                path.read_text(encoding="utf-8")
                + "\nfrom open_mmi_telemetry import guard as telemetry_guard\n",
                encoding="utf-8",
            )
            failures = _vehicle_identity_source_contract(fixture)
        self.assertTrue(
            any("identity-source-import:open_mmi_telemetry" in item for item in failures),
            failures,
        )

    def test_source_tripwire_rejects_known_remote_resolver_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp)
            self._source_fixture(fixture)
            bad = fixture / "ui" / "resolver.py"
            bad.write_text('URL = "https://vpic.nhtsa.gov/api/vehicles"\n', encoding="utf-8")
            failures = _vehicle_identity_source_contract(fixture)
        self.assertTrue(any("remote-resolver-marker" in item for item in failures), failures)

    def test_inspector_proves_current_contract_when_network_boundary_is_proven(self):
        check = _inspect_vehicle_identity_remote_resolution_enforcement(
            ROOT,
            load_manifest(),
            {"status": PASS},
            production=True,
        )
        assert check is not None
        self.assertEqual(check["status"], PASS)
        self.assertEqual(check["evidence"]["authorized_remote_identity_purposes"], [])

    def test_nonproduction_fixture_is_truthfully_unverified(self):
        check = _inspect_vehicle_identity_remote_resolution_enforcement(
            ROOT,
            load_manifest(),
            {"status": PASS},
            production=False,
        )
        assert check is not None
        self.assertEqual(check["status"], UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
