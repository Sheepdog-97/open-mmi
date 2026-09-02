from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from open_mmi_trust.manifest import (
    CAPABILITY_POLICIES,
    DEFAULT_MANIFEST_PATH,
    ManifestError,
    canonical_manifest_bytes,
    load_manifest,
    manifest_digest,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "open_mmi_trust" / "data" / "trust-manifest.v1.schema.json"


class TrustManifestTests(unittest.TestCase):
    def test_checked_manifest_is_valid_and_complete(self):
        manifest = load_manifest()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["manifest_id"], "org.open-mmi.trust-manifest")
        self.assertEqual(manifest["policy_generation"], 4)
        self.assertEqual(set(manifest["capabilities"]), set(CAPABILITY_POLICIES))
        self.assertEqual(
            manifest["capabilities"]["vehicle.can.transmit"],
            {"policy": "prohibited", "assurance": "ci-guarded"},
        )
        self.assertEqual(
            manifest["capabilities"]["telemetry.collection"],
            {"policy": "local-owner-opt-in", "assurance": "runtime-guarded"},
        )
        self.assertEqual(
            manifest["capabilities"]["network.external-egress"],
            {
                "policy": "declared-purposes-only",
                "assurance": "os-enforced",
                "purposes": [
                    "media.internet-radio",
                    "media.jellyfin",
                    "updates.release-fetch",
                ],
            },
        )
        self.assertEqual(
            manifest["capabilities"]["vehicle-data.persistence"],
            {
                "policy": "declared-purposes-only",
                "assurance": "os-enforced",
                "purposes": [
                    "service-reminder",
                    "trip-a",
                    "trip-b",
                    "trip-distance",
                    "vehicle-runtime-status",
                ],
            },
        )

    def test_manifest_digest_is_deterministic(self):
        payload = json.loads(DEFAULT_MANIFEST_PATH.read_text(encoding="utf-8"))
        reordered = {
            "capabilities": dict(reversed(list(payload["capabilities"].items()))),
            "policy_generation": payload["policy_generation"],
            "manifest_id": payload["manifest_id"],
            "schema_version": payload["schema_version"],
        }
        self.assertEqual(manifest_digest(payload), manifest_digest(reordered))
        self.assertEqual(canonical_manifest_bytes(payload), canonical_manifest_bytes(reordered))

    def test_unknown_capabilities_fail_closed(self):
        payload = load_manifest()
        payload = copy.deepcopy(payload)
        payload["capabilities"]["telemetry.magic-bypass"] = {
            "policy": "allowed",
            "assurance": "declared",
        }
        with self.assertRaisesRegex(ManifestError, "capability set"):
            validate_manifest(payload)

    def test_unknown_fields_fail_closed(self):
        payload = load_manifest()
        payload = copy.deepcopy(payload)
        payload["capabilities"]["vehicle.can.transmit"]["maintainer_override"] = True
        with self.assertRaisesRegex(ManifestError, "unknown keys"):
            validate_manifest(payload)

    def test_purpose_ids_are_sorted_and_unique(self):
        payload = load_manifest()
        payload = copy.deepcopy(payload)
        payload["capabilities"]["network.external-egress"]["purposes"] = [
            "updates.release-fetch",
            "media.jellyfin",
        ]
        with self.assertRaisesRegex(ManifestError, "must be sorted"):
            validate_manifest(payload)

    def test_schema_file_is_checked_json(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "Open MMI Trust Manifest v1")
        self.assertFalse(schema["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
