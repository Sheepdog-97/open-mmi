from __future__ import annotations

import json
import re
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch

from ui.web_dashboard import server, versioning


class BuildIdentityTests(unittest.TestCase):
    def test_explicit_environment_value_has_highest_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / ".version"
            version_file.write_text("deployed-build\n", encoding="utf-8")
            result = versioning.resolve_build_id(
                environ={"OPEN_MMI_BUILD_ID": " explicit build ", "OPEN_MMI_VERSION_FILE": str(version_file)},
                module_path=Path(tmp) / "module.py",
            )
        self.assertEqual(result, "explicit-build")

    def test_deployed_version_file_precedes_repository_and_package_fallbacks(self):
        with tempfile.TemporaryDirectory() as tmp:
            version_file = Path(tmp) / ".version"
            version_file.write_text("v1-runtime-abc123\n", encoding="utf-8")
            with patch.object(versioning, "_git_build_id", return_value="git-build"), patch.object(
                versioning, "_package_build_id", return_value="package-build"
            ):
                result = versioning.resolve_build_id(
                    environ={}, version_file=version_file, module_path=Path(tmp) / "module.py"
                )
        self.assertEqual(result, "v1-runtime-abc123")

    def test_unknown_identity_disables_automatic_reload(self):
        self.assertEqual(
            versioning.version_payload(versioning.UNKNOWN_BUILD_ID),
            {
                "api_version": 1,
                "build_id": "unknown-dev",
                "frontend_id": "unknown-dev",
                "reload_supported": False,
            },
        )

    def test_rendered_index_versions_every_local_mutable_asset_consistently(self):
        template = (server.DashboardHandler.static_dir / "index.html").read_text(encoding="utf-8")
        rendered = versioning.render_index(template, "build 123")
        self.assertIn('content="build-123"', rendered)
        mutable_assets = re.findall(r'(?:href|src)="(/[^"?]+\.(?:css|js))\?v=([^"&]+)"', rendered)
        self.assertGreater(len(mutable_assets), 10)
        self.assertEqual({token for _, token in mutable_assets}, {"build-123"})
        self.assertNotIn("__OPEN_MMI_FRONTEND_ID__", rendered)
        self.assertIn(
            'href="/vendor/bootstrap-5.3.8.min.css?v=build-123"',
            rendered,
        )
        self.assertNotIn("cdn.jsdelivr.net", rendered)


class DashboardVersionHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class TestHandler(server.DashboardHandler):
            build_id = "test-build-42"
            frontend_id = "test-build-42"

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), TestHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def fetch(self, path: str):
        with urlopen(self.base_url + path, timeout=3) as response:
            return response.status, response.headers, response.read()

    def test_version_endpoint_is_uncached_and_exposes_stable_contract(self):
        status, headers, body = self.fetch("/api/version")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(
            json.loads(body),
            {
                "api_version": 1,
                "build_id": "test-build-42",
                "frontend_id": "test-build-42",
                "reload_supported": True,
            },
        )

    def test_root_and_index_are_no_store_and_contain_versioned_assets(self):
        for path in ("/", "/index.html"):
            status, headers, body = self.fetch(path)
            html = body.decode("utf-8")
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Cache-Control"), "no-store")
            self.assertIn('content="test-build-42"', html)
            self.assertIn('/dashboard-connection.js?v=test-build-42', html)
            self.assertIn('/navigation.js?v=test-build-42', html)
            self.assertIn('/jellyfin-reconnection.js?v=test-build-42', html)
            self.assertIn('/styles-runtime-hardening.css?v=test-build-42', html)

    def test_static_cache_policy_distinguishes_versioned_and_compatibility_urls(self):
        _, versioned_headers, versioned_body = self.fetch("/frontend-version.js?v=test-build-42")
        _, plain_headers, plain_body = self.fetch("/frontend-version.js")
        self.assertEqual(versioned_headers.get("Cache-Control"), "public, max-age=31536000, immutable")
        self.assertEqual(plain_headers.get("Cache-Control"), "no-cache")
        self.assertEqual(versioned_body, plain_body)

    def test_runtime_diagnostics_endpoint_is_uncached_and_read_only(self):
        fixture = {
            "api_version": 1,
            "sampled_at": "2026-07-16T22:29:54+00:00",
            "cpu": {"average_mhz": 400.0},
            "thermal": {"summary": "thermal-limit-active"},
            "power": {"charging_state": "not-charging"},
        }
        with patch.object(
            server.runtime_diagnostics_backend,
            "runtime_diagnostics_payload",
            return_value=fixture,
        ):
            status, headers, body = self.fetch("/api/system/diagnostics/runtime")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(json.loads(body), fixture)


if __name__ == "__main__":
    unittest.main()
