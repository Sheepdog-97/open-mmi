from __future__ import annotations

import ast
import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from open_mmi_telemetry.guard import _create_authorization
from open_mmi_trust.accepted_state import _record_accepted_manifest
from open_mmi_trust.inspector import (
    FAIL,
    PASS,
    UNVERIFIED,
    canonical_report_bytes,
    inspect_system,
)
from open_mmi_trust.inspector_cli import render_text


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "open_mmi_trust" / "data" / "trust-manifest.v1.json"


def scope() -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": "owner-diagnostics",
        "signals": ["vehicle.rpm", "vehicle.speed"],
        "retention": "session",
        "destination": "local-only",
    }


class TrustInspectorTests(unittest.TestCase):
    def fixture(self, root: Path) -> tuple[Path, Path, str]:
        canbus = root / "canbusd"
        canbus.mkdir(parents=True)
        (canbus / "core.py").write_text(
            "def receive(bus):\n    return bus.recv()\n",
            encoding="utf-8",
        )

        static = root / "ui" / "web_dashboard" / "static"
        vendor = static / "vendor"
        vendor.mkdir(parents=True)
        (static / "index.html").write_text(
            '<link href="/vendor/bootstrap-5.3.8.min.css" rel="stylesheet">\n',
            encoding="utf-8",
        )
        bootstrap = b"/* Bootstrap v5.3.8 synthetic inspector fixture */\n"
        (vendor / "bootstrap-5.3.8.min.css").write_bytes(bootstrap)
        expected = base64.b64encode(hashlib.sha384(bootstrap).digest()).decode("ascii")
        return (
            root / "trust" / "telemetry-authorization.v1.json",
            root / "trust" / "accepted-owner-trust.v1.json",
            expected,
        )

    def inspect_fixture(
        self, root: Path, authorization: Path, accepted_state: Path, expected_bootstrap: str
    ):
        with mock.patch(
            "open_mmi_trust.inspector.BOOTSTRAP_SHA384_BASE64",
            expected_bootstrap,
        ):
            return inspect_system(
                manifest_path=MANIFEST,
                authorization_path=authorization,
                accepted_state_path=accepted_state,
                install_root=root,
            )

    def test_clean_fixture_is_truthfully_unverified_without_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["status"], UNVERIFIED)
        self.assertEqual(report["manifest"]["policy_generation"], 2)
        self.assertEqual(
            report["manifest"]["digest"],
            "sha256:90e11e8cbc1948477cfd08fdd2757f1f2aa22367f48d594c3cf4ab06d57bfd39",
        )
        self.assertEqual(report["telemetry_authorization"], {"authorized": False, "state": "not-authorized"})
        self.assertNotIn("accepted_owner_trust", report)
        statuses = {check["id"]: check["status"] for check in report["checks"]}
        self.assertEqual(statuses["manifest.valid"], PASS)
        self.assertEqual(statuses["telemetry.default-deny-runtime"], PASS)
        self.assertEqual(statuses["telemetry.self-authorization-source-tripwire"], PASS)
        self.assertEqual(
            statuses["owner.accepted-state-self-authorization-source-tripwire"], PASS
        )
        self.assertEqual(statuses["owner.accepted-release-state"], UNVERIFIED)
        self.assertEqual(statuses["can.transmit-source-tripwire"], PASS)
        self.assertEqual(statuses["dashboard.render-egress"], PASS)
        self.assertEqual(statuses["dashboard.bootstrap-integrity"], PASS)
        self.assertEqual(statuses["release.file-integrity"], UNVERIFIED)
        self.assertNotIn(FAIL, statuses.values())

    def test_authorized_state_is_redacted_but_scope_visible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            created = _create_authorization("WVWZZZ1KZ6W000001", scope(), authorization)
            self.assertIn("salt", created["vin_binding"])
            self.assertIn("fingerprint", created["vin_binding"])
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        visible = report["telemetry_authorization"]
        self.assertTrue(visible["authorized"])
        self.assertEqual(visible["scope"], scope())
        self.assertEqual(set(visible["vin_binding"]), {"algorithm", "iterations"})
        self.assertNotIn("salt", visible["vin_binding"])
        self.assertNotIn("fingerprint", visible["vin_binding"])
        text = render_text(report)
        self.assertIn("Telemetry authorization: AUTHORIZED", text)
        self.assertNotIn(created["vin_binding"]["salt"], text)
        self.assertNotIn(created["vin_binding"]["fingerprint"], text)

    def test_established_accepted_state_passes_when_current_boundary_is_equal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            state = _record_accepted_manifest(json.loads(MANIFEST.read_text(encoding="utf-8")), accepted_state)
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        check = next(check for check in report["checks"] if check["id"] == "owner.accepted-release-state")
        self.assertEqual(check["status"], PASS)
        evidence = check["evidence"]
        self.assertTrue(evidence["established"])
        self.assertEqual(evidence["accepted_manifest_digest"], state["manifest_digest"])
        self.assertEqual(evidence["current_relation"], "equal")
        self.assertIn("Accepted owner trust: ESTABLISHED", render_text(report))

    def test_installed_policy_expansion_beyond_accepted_state_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            accepted_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
            accepted_manifest["policy_generation"] = 1
            accepted_manifest["capabilities"]["telemetry.collection"] = {
                "policy": "prohibited",
                "assurance": "runtime-guarded",
            }
            _record_accepted_manifest(accepted_manifest, accepted_state)
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        self.assertEqual(report["status"], FAIL)
        check = next(check for check in report["checks"] if check["id"] == "owner.accepted-release-state")
        self.assertEqual(check["status"], FAIL)
        self.assertEqual(check["evidence"]["current_relation"], "expansion")
        self.assertFalse(check["evidence"]["comparison"]["allowed_without_owner_ack"])

    def test_invalid_authorization_state_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            authorization.parent.mkdir(parents=True, mode=0o700)
            authorization.write_text('{"broken": true}\n', encoding="utf-8")
            authorization.chmod(0o600)
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        self.assertEqual(report["status"], FAIL)
        check = next(check for check in report["checks"] if check["id"] == "telemetry.authorization-state")
        self.assertEqual(check["status"], FAIL)

    def test_can_send_call_contradicts_prohibited_transmit_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            (root / "canbusd" / "core.py").write_text(
                "def unsafe(bus, msg):\n    bus.send(msg)\n",
                encoding="utf-8",
            )
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        self.assertEqual(report["status"], FAIL)
        check = next(check for check in report["checks"] if check["id"] == "can.transmit-source-tripwire")
        self.assertEqual(check["status"], FAIL)
        self.assertTrue(check["evidence"]["offenders"])

    def test_remote_dashboard_dependency_is_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            index = root / "ui" / "web_dashboard" / "static" / "index.html"
            index.write_text(
                '<link href="/vendor/bootstrap-5.3.8.min.css" rel="stylesheet">\n'
                '<script src="https://example.invalid/tracker.js"></script>\n',
                encoding="utf-8",
            )
            report = self.inspect_fixture(root, authorization, accepted_state, expected)

        check = next(check for check in report["checks"] if check["id"] == "dashboard.render-egress")
        self.assertEqual(check["status"], FAIL)
        self.assertEqual(check["evidence"]["remote_dependencies"], ["https://example.invalid/tracker.js"])

    def test_inspection_does_not_create_authorization_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            authorization, accepted_state, expected = self.fixture(root)
            self.assertFalse(authorization.exists())
            self.assertFalse(accepted_state.exists())
            self.inspect_fixture(root, authorization, accepted_state, expected)
            self.assertFalse(authorization.exists())
            self.assertFalse(accepted_state.exists())


    def test_inspector_source_has_no_trust_mutation_or_network_surface(self):
        forbidden_modules = {"socket", "subprocess", "urllib", "http", "requests"}
        forbidden_calls = {
            "_create_authorization",
            "_write_authorization",
            "_revoke_authorization",
            "_record_accepted_manifest",
            "_write_accepted_state",
            "write_text",
            "write_bytes",
            "unlink",
            "replace",
            "rename",
            "mkdir",
            "makedirs",
        }
        offenders = []
        for path in (
            ROOT / "open_mmi_trust" / "inspector.py",
            ROOT / "open_mmi_trust" / "inspector_cli.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in forbidden_modules:
                            offenders.append(f"{path.name}:{node.lineno}:import:{alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.split(".", 1)[0] in forbidden_modules:
                        offenders.append(f"{path.name}:{node.lineno}:import:{node.module}")
                    for alias in node.names:
                        if alias.name in forbidden_calls:
                            offenders.append(f"{path.name}:{node.lineno}:import:{alias.name}")
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    else:
                        name = ""
                    if name in forbidden_calls:
                        offenders.append(f"{path.name}:{node.lineno}:call:{name}")
        self.assertEqual(offenders, [])

    def test_report_schema_is_checked_and_forbids_vin_binding_secrets(self):
        schema = json.loads(
            (ROOT / "open_mmi_trust" / "data" / "trust-inspection.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(schema["title"], "Open MMI Trust Inspection v1")
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("accepted_owner_trust", schema["required"])
        binding = schema["$defs"]["vinBindingRedacted"]
        self.assertFalse(binding["additionalProperties"])
        self.assertEqual(set(binding["properties"]), {"algorithm", "iterations"})

    def test_canonical_report_bytes_are_deterministic(self):
        report = {"b": 2, "a": {"y": 2, "x": 1}}
        self.assertEqual(
            canonical_report_bytes(report),
            b'{"a":{"x":1,"y":2},"b":2}\n',
        )


if __name__ == "__main__":
    unittest.main()
