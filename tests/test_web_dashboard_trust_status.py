from __future__ import annotations

import inspect
import unittest
from pathlib import Path

from open_mmi_trust.inspector import FAIL, PASS, UNVERIFIED
from ui.web_dashboard import server, trust_status


ROOT = Path(__file__).resolve().parents[1]
PROVIDER = ROOT / "ui" / "web_dashboard" / "trust_status.py"


class TrustStatusProviderTests(unittest.TestCase):
    def test_preserves_backend_status_without_reclassification(self):
        for status in (PASS, FAIL, UNVERIFIED):
            with self.subTest(status=status):
                report = {
                    "status": status,
                    "manifest": {"available": True},
                    "checks": [],
                }

                payload = trust_status.trust_status_payload(lambda: report)

                self.assertEqual(payload["api_version"], 1)
                self.assertEqual(payload["status"], status)
                self.assertEqual(payload["report"], report)
                self.assertIsNone(payload["error"])

    def test_inspector_failure_is_unverified_not_pass(self):
        def broken():
            raise RuntimeError("synthetic failure")

        payload = trust_status.trust_status_payload(broken)

        self.assertEqual(payload["status"], UNVERIFIED)
        self.assertIsNone(payload["report"])
        self.assertEqual(
            payload["error"],
            "Trust inspection evidence is unavailable.",
        )

    def test_malformed_inspector_status_is_unverified(self):
        payload = trust_status.trust_status_payload(
            lambda: {"status": "GREEN", "checks": []}
        )

        self.assertEqual(payload["status"], UNVERIFIED)
        self.assertIsNone(payload["report"])

    def test_provider_has_no_trust_mutation_or_remote_dependency(self):
        source = PROVIDER.read_text(encoding="utf-8")

        self.assertIn(
            "from open_mmi_trust.inspector import",
            source,
        )

        for forbidden in (
            "accepted_state",
            "transition_gate",
            "release_provenance",
            "_write_accepted_state",
            "_record_acknowledged_expansion",
            "activate_acknowledged_expansion",
            "acknowledge",
            "postJson",
            "requests",
            "urllib",
            "socket",
            "subprocess",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_server_exposes_trust_status_on_get_only(self):
        get_source = inspect.getsource(server.DashboardHandler.do_GET)
        post_source = inspect.getsource(server.DashboardHandler.do_POST)

        self.assertIn(
            'parsed.path == "/api/trust/status"',
            get_source,
        )
        self.assertIn(
            "trust_status_backend.trust_status_payload()",
            get_source,
        )
        self.assertNotIn("/api/trust/", post_source)

    def test_provider_is_independent_of_dashboard_handler(self):
        self.assertFalse(hasattr(trust_status, "DashboardHandler"))
        source = PROVIDER.read_text(encoding="utf-8")
        self.assertNotIn("from ui.web_dashboard.server", source)
        self.assertNotIn("import server", source)


    def test_trust_ui_is_required_in_built_and_installed_package(self):
        wheel_verifier = (
            ROOT / "tools" / "verify_wheel.py"
        ).read_text(encoding="utf-8")
        ci = (
            ROOT / ".github" / "workflows" / "ci.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(
            '"ui/web_dashboard/trust_status.py"',
            wheel_verifier,
        )
        self.assertIn(
            '"ui/web_dashboard/static/trust-status.js"',
            wheel_verifier,
        )

        self.assertIn(
            '"dashboard_trust_status": Path(trust_status.__file__)',
            ci,
        )
        self.assertIn(
            '"frontend_trust_status": '
            'Path(ui.web_dashboard.__file__).parent / "static" / "trust-status.js"',
            ci,
        )


if __name__ == "__main__":
    unittest.main()
