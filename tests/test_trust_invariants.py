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
        transition_module = ROOT / "open_mmi_trust" / "transition_gate.py"
        mutation_names = {
            "_record_accepted_manifest",
            "_record_acknowledged_expansion",
            "_write_accepted_state",
        }
        allowed = {
            owner_cli: {"_record_accepted_manifest"},
            transition_module: {
                "_record_accepted_manifest",
                "_record_acknowledged_expansion",
            },
        }
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
                        if alias.name in allowed.get(path, set()):
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
                if name in allowed.get(path, set()):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders,
            [],
            "production accepted-state mutation calls found: " + ", ".join(offenders),
        )

    def test_transition_authorization_mutation_is_confined_to_owner_cli_and_gate(self):
        gate = ROOT / "open_mmi_trust" / "transition_gate.py"
        owner_cli = ROOT / "open_mmi_trust" / "transition_gate_cli.py"
        installer = ROOT / "ui" / "update_installer.py"
        mutation_names = {
            "_authorize_prepared_expansion",
            "_write_transition_authorization",
            "_clear_transition_authorization",
            "activate_acknowledged_expansion",
            "finalize_successful_transition",
        }
        allowed = {
            owner_cli: {"_authorize_prepared_expansion"},
            installer: {
                "activate_acknowledged_expansion",
                "finalize_successful_transition",
            },
        }
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts):
                continue
            if path == gate:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name not in mutation_names:
                            continue
                        if alias.name in allowed.get(path, set()):
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
                if name in allowed.get(path, set()):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders,
            [],
            "production transition-authorization mutation calls found: " + ", ".join(offenders),
        )

    def test_transition_lineage_mutation_is_confined_to_trusted_owner_and_gate_paths(self):
        lineage_module = ROOT / "open_mmi_trust" / "lineage.py"
        lineage_cli = ROOT / "open_mmi_trust" / "lineage_cli.py"
        accepted_cli = ROOT / "open_mmi_trust" / "accepted_state_cli.py"
        transition_gate = ROOT / "open_mmi_trust" / "transition_gate.py"
        mutation_names = {
            "_append_lineage_record",
            "_record_lineage_baseline",
            "_record_state_transition",
        }
        allowed = {
            lineage_cli: {"_record_lineage_baseline", "_record_state_transition"},
            accepted_cli: {"_record_lineage_baseline", "_record_state_transition"},
            transition_gate: {"_record_state_transition"},
        }
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts):
                continue
            if path == lineage_module:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name not in mutation_names:
                            continue
                        if alias.name in allowed.get(path, set()):
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
                if name not in mutation_names or name in allowed.get(path, set()):
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders,
            [],
            "production transition-lineage mutation calls found: " + ", ".join(offenders),
        )

    def test_accepted_state_changes_are_followed_by_lineage_before_update_flow_continues(self):
        gate = ROOT / "open_mmi_trust" / "transition_gate.py"
        tree = ast.parse(gate.read_text(encoding="utf-8"), filename=str(gate))
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        def call_lines(function_name: str, called_name: str) -> list[int]:
            result: list[int] = []
            for node in ast.walk(functions[function_name]):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name == called_name:
                    result.append(node.lineno)
            return result

        expansion_state = call_lines("activate_acknowledged_expansion", "_record_acknowledged_expansion")
        expansion_lineage = call_lines("activate_acknowledged_expansion", "_record_state_transition")
        expansion_clear = call_lines("activate_acknowledged_expansion", "_clear_transition_authorization")
        final_state = call_lines("finalize_successful_transition", "_record_accepted_manifest")
        final_lineage = call_lines("finalize_successful_transition", "_record_state_transition")
        self.assertTrue(expansion_state and expansion_lineage and expansion_clear)
        self.assertLess(max(expansion_state), min(expansion_lineage))
        self.assertLess(max(expansion_lineage), min(expansion_clear))
        self.assertTrue(final_state and final_lineage)
        self.assertLess(max(final_state), min(final_lineage))

    def test_candidate_deployment_is_ordered_after_old_trusted_transition_gate(self):
        installer = ROOT / "ui" / "update_installer.py"
        coordinator = ROOT / "ui" / "update_coordinator.py"
        installer_tree = ast.parse(installer.read_text(encoding="utf-8"), filename=str(installer))
        coordinator_tree = ast.parse(coordinator.read_text(encoding="utf-8"), filename=str(coordinator))

        def lines(tree: ast.AST, name: str) -> list[int]:
            result: list[int] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    called = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    called = node.func.attr
                else:
                    called = ""
                if called == name:
                    result.append(node.lineno)
            return sorted(result)

        installer_integrity = lines(installer_tree, "require_current_integrity")
        installer_current_provenance = lines(installer_tree, "require_current_release_provenance")
        installer_candidate_provenance = lines(installer_tree, "require_candidate_release_provenance")
        installer_gate = lines(installer_tree, "require_prepared_candidate_allowed")
        installer_activate = lines(installer_tree, "activate_acknowledged_expansion")
        installer_deploy = lines(installer_tree, "_run_deployment")
        self.assertTrue(
            installer_integrity
            and installer_current_provenance
            and installer_candidate_provenance
            and installer_gate
            and installer_activate
            and installer_deploy
        )
        self.assertLess(min(installer_integrity), min(installer_current_provenance))
        self.assertLess(min(installer_current_provenance), min(installer_candidate_provenance))
        self.assertLess(min(installer_candidate_provenance), min(installer_gate))
        self.assertLess(min(installer_gate), min(installer_activate))
        self.assertLess(min(installer_activate), min(installer_deploy))

        coordinator_gate = lines(coordinator_tree, "require_prepared_candidate_allowed")
        installer_service_lines: list[int] = []
        for node in ast.walk(coordinator_tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "run" or not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
                continue
            values = [
                element.value
                for element in node.args[0].elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            ]
            if "open-mmi-update-installer.service" in values:
                installer_service_lines.append(node.lineno)
        self.assertTrue(coordinator_gate and installer_service_lines)
        self.assertLess(min(coordinator_gate), min(installer_service_lines))

    def test_transition_gate_treats_candidate_as_git_object_data_only(self):
        path = ROOT / "open_mmi_trust" / "transition_gate.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_imports = {"importlib", "runpy"}
        forbidden_calls = {"exec", "eval", "compile", "__import__"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_imports:
                        offenders.append(f"{node.lineno}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".", 1)[0] in forbidden_imports:
                    offenders.append(f"{node.lineno}:import:{node.module}")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in forbidden_calls:
                    offenders.append(f"{node.lineno}:call:{node.func.id}")
        self.assertEqual(offenders, [])
        self.assertIn('"ls-tree"', source)
        self.assertIn('"cat-file"', source)
        self.assertNotIn("scripts/manage.sh", source)

    def test_installed_integrity_mutation_is_confined_to_owner_cli_and_trusted_installer(self):
        integrity_module = ROOT / "open_mmi_trust" / "release_integrity.py"
        integrity_cli = ROOT / "open_mmi_trust" / "release_integrity_cli.py"
        installer = ROOT / "ui" / "update_installer.py"
        mutation_names = {"_record_integrity_state", "_write_integrity_state"}
        allowed = {
            integrity_cli: {"_record_integrity_state"},
            installer: {"_record_integrity_state"},
        }
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts) or path == integrity_module:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in mutation_names and alias.name not in allowed.get(path, set()):
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:import:{alias.name}")
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name in mutation_names and name not in allowed.get(path, set()):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders, [],
            "production installed-integrity mutation calls found: " + ", ".join(offenders),
        )

    def test_candidate_byte_integrity_is_ordered_around_candidate_execution(self):
        source = (ROOT / "ui" / "update_installer.py").read_text(encoding="utf-8")
        start = source.index("def install_prepared(")
        end = source.index("\ndef main(", start)
        block = source[start:end]
        markers = [
            "release_integrity.require_current_integrity(",
            "release_provenance.require_current_release_provenance(",
            "release_provenance.require_candidate_release_provenance(",
            "transition_gate.require_prepared_candidate_allowed(",
            "release_integrity.expected_release_from_git(",
            "transition_gate.activate_acknowledged_expansion(",
            "_prepare_candidate_wheel(",
            "_run_deployment(",
            "release_integrity.verify_runtime_inventory(",
            "transition_gate.finalize_successful_transition(",
            "release_integrity._record_integrity_state(",
        ]
        positions = [block.index(value) for value in markers]
        self.assertEqual(positions, sorted(positions))

    def test_candidate_deployment_consumes_old_trusted_preverified_wheel(self):
        source = (ROOT / "scripts" / "manage.sh").read_text(encoding="utf-8")
        start = source.index("cmd_deploy_prepared() {")
        end = source.index("# UNINSTALL", start)
        block = source[start:end]
        self.assertIn("OPEN_MMI_PREPARED_WHEEL", block)
        self.assertIn('deployment_stage="package-artifact"', block)
        self.assertNotIn("pip wheel --no-deps", block)
        self.assertNotIn("tools/verify_wheel.py", block)
        self.assertIn('install_open_mmi_package "$candidate_wheel"', block)

    def test_release_integrity_candidate_inventory_uses_git_object_data(self):
        path = ROOT / "open_mmi_trust" / "release_integrity.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_calls = {"exec", "eval", "compile", "__import__"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in forbidden_calls:
                    offenders.append(f"{node.lineno}:{node.func.id}")
        self.assertEqual(offenders, [])
        self.assertIn('"ls-tree"', source)
        self.assertIn('"cat-file"', source)
        self.assertIn('"show"', source)

    def test_release_signer_root_mutation_is_confined_to_local_bootstrap_cli(self):
        provenance_module = ROOT / "open_mmi_trust" / "release_provenance.py"
        provenance_cli = ROOT / "open_mmi_trust" / "release_provenance_cli.py"
        mutation_names = {"_write_provenance_root"}
        offenders: list[str] = []
        ignored_roots = {"tests", "tools", ".git", ".venv", "venv", "__pycache__", "build", "dist"}
        for path in sorted(ROOT.rglob("*.py")):
            if ignored_roots.intersection(path.relative_to(ROOT).parts) or path == provenance_module:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    for alias in node.names:
                        if alias.name in mutation_names and path != provenance_cli:
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:import:{alias.name}")
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                else:
                    name = ""
                if name in mutation_names and path != provenance_cli:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}:{name}")
        self.assertEqual(
            offenders, [],
            "production release-signer root mutation calls found: " + ", ".join(offenders),
        )

    def test_release_provenance_verifier_is_offline_isolated_and_fixed_program_bound(self):
        path = ROOT / "open_mmi_trust" / "release_provenance.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        forbidden_imports = {"requests", "urllib", "http", "socket"}
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", 1)[0] in forbidden_imports:
                        offenders.append(f"{node.lineno}:import:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".", 1)[0] in forbidden_imports:
                    offenders.append(f"{node.lineno}:import:{node.module}")
        self.assertEqual(offenders, [])
        self.assertIn('GPG_PROGRAM = Path("/usr/bin/gpg")', source)
        self.assertIn('GIT_PROGRAM = Path("/usr/bin/git")', source)
        self.assertIn('"GNUPGHOME"', source)
        self.assertIn('"GIT_CONFIG_NOSYSTEM"', source)
        self.assertIn('"GIT_CONFIG_GLOBAL"', source)
        self.assertIn('"verify-commit", "--raw"', source)
        self.assertNotIn("--auto-key-retrieve", source)
        self.assertNotIn("--keyserver", source)

    def test_current_assurance_matches_enforcement_layers(self):
        manifest = load_manifest()
        self.assertEqual(
            manifest["capabilities"]["network.external-egress"]["assurance"],
            "os-enforced",
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
            "os-enforced",
        )


if __name__ == "__main__":
    unittest.main()
