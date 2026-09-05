from __future__ import annotations

import inspect
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from open_mmi_trust.inspector import FAIL, PASS, UNVERIFIED
from ui import trust_status_coordinator as coordinator


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ui" / "trust_status_coordinator.py"
UNIT = ROOT / "systemd" / "system" / "open-mmi-trust-status.service"


class TrustStatusCoordinatorTests(unittest.TestCase):
    def test_exact_status_request_returns_privileged_inspector_report(self) -> None:
        for status in (PASS, FAIL, UNVERIFIED):
            with self.subTest(status=status):
                report = {"status": status, "checks": [], "manifest": {"available": True}}
                response = coordinator.response_for_request(
                    {"api_version": 1, "action": "status"},
                    inspector=lambda report=report: report,
                )
                self.assertTrue(response["ok"])
                self.assertEqual(response["status"], status)
                self.assertEqual(response["report"], report)
                self.assertIsNone(response["error"])

    def test_schema_is_fixed_and_has_no_mutation_action(self) -> None:
        calls = 0

        def inspector():
            nonlocal calls
            calls += 1
            return {"status": PASS, "checks": []}

        invalid = (
            None,
            {},
            {"api_version": 1, "action": "status", "path": "/var/lib/open-mmi/trust"},
            {"api_version": 1, "action": "acknowledge"},
            {"api_version": 1, "action": "bootstrap"},
            {"api_version": 2, "action": "status"},
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                response = coordinator.response_for_request(payload, inspector=inspector)
                self.assertFalse(response["ok"])
        self.assertEqual(calls, 0)

    def test_inspector_failure_is_sanitized_and_unverified(self) -> None:
        def broken():
            raise RuntimeError("/private/root/path must not leak")

        response = coordinator.response_for_request(
            {"api_version": 1, "action": "status"},
            inspector=broken,
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["status"], UNVERIFIED)
        self.assertIsNone(response["report"])
        self.assertNotIn("/private/root/path", response["error"])

    def test_local_socket_client_round_trip(self) -> None:
        with TemporaryDirectory() as temporary:
            socket_path = Path(temporary) / "trust-status.sock"
            report = {"status": PASS, "checks": [], "manifest": {"available": True}}
            with coordinator.TrustStatusServer(
                socket_path,
                inspector=lambda: report,
            ) as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    response = coordinator.client_status(socket_path)
                finally:
                    server.shutdown()
                    thread.join(timeout=5)
            self.assertTrue(response["ok"])
            self.assertEqual(response["report"], report)

    def test_source_imports_inspector_but_no_trust_mutation_primitive(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("inspect_system", source)
        for forbidden in (
            "_record_accepted_manifest",
            "_record_lineage_baseline",
            "_record_state_transition",
            "_record_integrity_state",
            "_write_provenance_root",
            "_authorize_prepared_expansion",
            "activate_acknowledged_expansion",
            "_create_authorization",
            "_revoke_authorization",
            "subprocess",
            "urllib",
            "requests",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_systemd_unit_is_root_read_only_and_network_denied(self) -> None:
        unit = UNIT.read_text(encoding="utf-8")
        for required in (
            "User=root",
            "Group=open-mmi-update",
            "ExecStart=/opt/open-mmi/venv/bin/python -I -m ui.trust_status_coordinator serve",
            "NoNewPrivileges=true",
            "PrivateDevices=true",
            "ProtectSystem=strict",
            "RestrictAddressFamilies=AF_UNIX",
            "IPAddressDeny=any",
            "ReadWritePaths=/run/open-mmi",
        ):
            self.assertIn(required, unit)
        self.assertNotIn("AF_INET", unit)
        self.assertNotIn("EnvironmentFile=", unit)

    def test_service_has_only_serve_cli_action(self) -> None:
        parser_source = inspect.getsource(coordinator.build_parser)
        self.assertIn('add_parser("serve"', parser_source)
        for forbidden in ("acknowledge", "accept", "bootstrap", "reconcile", "authorize", "revoke"):
            self.assertNotIn(forbidden, parser_source)


if __name__ == "__main__":
    unittest.main()
