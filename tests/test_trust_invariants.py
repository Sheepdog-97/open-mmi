from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

from open_mmi_trust import load_manifest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ui" / "web_dashboard" / "static"


class TrustInvariantTests(unittest.TestCase):
    def test_dashboard_remote_dependencies_are_exactly_declared_current_assets(self):
        source = (STATIC / "index.html").read_text(encoding="utf-8")
        remote_dependency = re.compile(
            r"<(?:script|link)\b[^>]*(?:src|href)=[\"'](?P<url>(?:https?:)?//[^\"']+)",
            re.IGNORECASE,
        )
        urls = {match.group("url") for match in remote_dependency.finditer(source)}
        self.assertEqual(
            urls,
            {
                "https://cdn.jsdelivr.net/npm/bootstrap@5.3.8/dist/css/bootstrap.min.css",
                "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css",
            },
        )
        manifest = load_manifest()
        purposes = set(
            manifest["capabilities"]["network.external-egress"]["purposes"]
        )
        self.assertIn("frontend.bootstrap-cdn", purposes)
        self.assertIn("frontend.bootstrap-icons-cdn", purposes)

    def test_dashboard_remote_assets_remain_version_pinned(self):
        source = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("bootstrap@latest", source)
        self.assertNotIn("bootstrap-icons@latest", source)
        self.assertIn("bootstrap@5.3.8", source)
        self.assertIn("bootstrap-icons@1.11.3", source)

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

    def test_current_assurance_does_not_overclaim_runtime_enforcement(self):
        manifest = load_manifest()
        self.assertEqual(
            manifest["capabilities"]["network.external-egress"]["assurance"],
            "declared",
        )
        self.assertEqual(
            manifest["capabilities"]["telemetry.collection"]["assurance"],
            "declared",
        )
        self.assertEqual(
            manifest["capabilities"]["vehicle-data.persistence"]["assurance"],
            "declared",
        )


if __name__ == "__main__":
    unittest.main()
