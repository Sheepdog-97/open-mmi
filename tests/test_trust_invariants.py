from __future__ import annotations

import ast
import base64
import hashlib
import re
import unittest
from pathlib import Path

from open_mmi_trust import load_manifest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ui" / "web_dashboard" / "static"
BOOTSTRAP = STATIC / "vendor" / "bootstrap-5.3.8.min.css"
BOOTSTRAP_SHA384_BASE64 = "sRIl4kxILFvY47J16cr9ZwB07vP4J8+LH7qKQnuqkuIAvNWLzeN8tE5YBujZqJLB"


class TrustInvariantTests(unittest.TestCase):
    def test_dashboard_render_has_no_remote_script_or_stylesheet_dependencies(self):
        source = (STATIC / "index.html").read_text(encoding="utf-8")
        remote_dependency = re.compile(
            r"<(?:script|link)\b[^>]*(?:src|href)=[\"'](?P<url>(?:https?:)?//[^\"']+)",
            re.IGNORECASE,
        )
        urls = {match.group("url") for match in remote_dependency.finditer(source)}
        self.assertEqual(urls, set())
        self.assertIn('href="/vendor/bootstrap-5.3.8.min.css"', source)
        self.assertNotIn("bootstrap-icons", source)

        manifest = load_manifest()
        purposes = set(manifest["capabilities"]["network.external-egress"]["purposes"])
        self.assertNotIn("frontend.bootstrap-cdn", purposes)
        self.assertNotIn("frontend.bootstrap-icons-cdn", purposes)

    def test_vendored_bootstrap_is_exact_reviewed_5_3_8_asset(self):
        data = BOOTSTRAP.read_bytes()
        digest = base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
        self.assertEqual(digest, BOOTSTRAP_SHA384_BASE64)
        self.assertTrue(
            b"Bootstrap  v5.3.8" in data[:512] or b"Bootstrap v5.3.8" in data[:512],
            "vendored stylesheet must identify Bootstrap v5.3.8",
        )

    def test_bootstrap_icon_font_dependency_is_not_reintroduced(self):
        offenders: list[str] = []
        for path in sorted(STATIC.rglob("*")):
            if path.suffix not in {".html", ".js", ".css"}:
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            if "bootstrap-icons@" in text or "bootstrap-icons.css" in text:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_can_runtime_contains_no_send_calls_while_manifest_prohibits_transmit(self):
        manifest = load_manifest()
        self.assertEqual(
            manifest["capabilities"]["vehicle.can.transmit"]["policy"],
            "prohibited",
        )
        offenders: list[str] = []
        for path in sorted((ROOT / "canbusd").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"send", "send_periodic"}:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.func.attr}")
        self.assertEqual(offenders, [], "CAN transmit-like calls found: " + ", ".join(offenders))

    def test_production_code_cannot_self_authorize_telemetry(self):
        allowed_path = ROOT / "open_mmi_telemetry" / "cli.py"
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts):
                continue
            if path == allowed_path or path == ROOT / "open_mmi_telemetry" / "guard.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            mutation_names = {"_create_authorization", "_write_authorization", "_revoke_authorization"}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in mutation_names:
                            offenders.append(
                                f"{path.relative_to(ROOT)}:{node.lineno}:import:{alias.name}"
                            )
                if not isinstance(node, ast.Call):
                    continue
                name = None
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in mutation_names:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders,
            [],
            "production telemetry self-authorization calls found: " + ", ".join(offenders),
        )

    def test_production_code_cannot_silently_mutate_accepted_owner_trust_state(self):
        state_module = ROOT / "open_mmi_trust" / "accepted_state.py"
        owner_cli = ROOT / "open_mmi_trust" / "accepted_state_cli.py"
        mutation_names = {"_record_accepted_manifest", "_write_accepted_state"}
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts):
                continue
            if path == state_module:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name not in mutation_names:
                            continue
                        if path == owner_cli and alias.name == "_record_accepted_manifest":
                            continue
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}:import:{alias.name}"
                        )
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name not in mutation_names:
                    continue
                if path == owner_cli and name == "_record_accepted_manifest":
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders,
            [],
            "production accepted-state mutation calls found: " + ", ".join(offenders),
        )

    def test_current_assurance_matches_enforcement_layers(self):
        manifest = load_manifest()
        self.assertEqual(
            manifest["capabilities"]["network.external-egress"]["assurance"],
            "declared",
        )
        self.assertEqual(
            manifest["capabilities"]["telemetry.collection"]["policy"],
            "local-owner-opt-in",
        )
        self.assertEqual(
            manifest["capabilities"]["telemetry.collection"]["assurance"],
            "runtime-guarded",
        )
        self.assertEqual(
            manifest["capabilities"]["vehicle-data.persistence"]["assurance"],
            "declared",
        )


if __name__ == "__main__":
    unittest.main()
