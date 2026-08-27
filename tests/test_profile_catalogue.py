from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from canbusd import profile_catalogue


ROOT = Path(__file__).resolve().parents[1]


class ProfileCatalogueTests(unittest.TestCase):
    def test_repository_catalogue_resolves_canonical_and_legacy_ids(self) -> None:
        canonical = profile_catalogue.resolve_profile(
            ROOT, "seat-leon-1p-pq35"
        )
        legacy = profile_catalogue.resolve_profile(ROOT, "seat_1p")

        self.assertEqual(canonical["id"], "seat-leon-1p-pq35")
        self.assertEqual(legacy["id"], canonical["id"])
        self.assertEqual(legacy["requested_status"], "alias")
        self.assertEqual(
            canonical["relative_path"],
            "vehicles/seat/leon/1p-pq35/config.json",
        )
        self.assertEqual(legacy["path"], canonical["path"])

        superb = profile_catalogue.resolve_profile(ROOT, "skoda-superb-3t-pq46")
        self.assertEqual(superb["id"], "skoda-superb-3t-pq46")
        self.assertEqual(
            superb["relative_path"],
            "vehicles/skoda/superb/3t-pq46/config.json",
        )

    def test_repository_tree_has_no_orphaned_profiles(self) -> None:
        report = profile_catalogue.verify_tree(ROOT)
        self.assertTrue(report["valid"], report)
        self.assertEqual(report["count"], 2)
        self.assertEqual(report["issues"], [])

    def test_catalogue_rejects_identity_and_path_collisions(self) -> None:
        with self.assertRaises(profile_catalogue.VehicleProfileCatalogueError):
            profile_catalogue.normalize_catalogue(
                {
                    "schema_version": 1,
                    "catalogue_id": "open-mmi.maintained-vehicles",
                    "profiles": {
                        "one": {
                            "path": "brand/model/one/config.json",
                            "aliases": ["legacy"],
                        },
                        "two": {
                            "path": "brand/model/one/config.json",
                            "aliases": ["legacy"],
                        },
                    },
                }
            )

    def test_legacy_flat_tree_remains_readable_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profile = root / "vehicles" / "legacy" / "config.json"
            profile.parent.mkdir(parents=True)
            profile.write_text("{}", encoding="utf-8")

            resolved = profile_catalogue.resolve_profile(root, "legacy")

            self.assertEqual(resolved["id"], "legacy")
            self.assertEqual(resolved["path"], profile)
            self.assertTrue(
                profile_catalogue.catalogue_payload(root)["legacy_flat_fallback"]
            )

    def test_catalogue_validation_is_independent_of_installer_file_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "catalogue.v1.json"
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "catalogue_id": "open-mmi.maintained-vehicles",
                        "profiles": {
                            "example": {
                                "path": "brand/model/generation/config.json",
                                "aliases": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            # pip can create installed package data with 0o666 even when the
            # wheel archive records 0o644. The catalogue shares the package's
            # trust boundary with the Python code that loads it, so validate
            # file type, size and content rather than installer-selected mode.
            path.chmod(0o666)

            catalogue = profile_catalogue.load_catalogue(path)

            self.assertIn("example", catalogue["profiles"])


    def test_registered_profile_rejects_symlinked_path_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            vehicles = root / "vehicles"
            vehicles.mkdir()
            outside_root = Path(outside)
            target = outside_root / "model" / "generation" / "config.json"
            target.parent.mkdir(parents=True)
            target.write_text("{}", encoding="utf-8")
            (vehicles / "brand").symlink_to(outside_root, target_is_directory=True)
            (vehicles / "catalogue.v1.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "catalogue_id": "open-mmi.maintained-vehicles",
                        "profiles": {
                            "example": {
                                "path": "brand/model/generation/config.json",
                                "aliases": [],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(
                profile_catalogue.VehicleProfileCatalogueError
            ):
                profile_catalogue.resolve_profile(root, "example")

            report = profile_catalogue.verify_tree(root)
            self.assertFalse(report["valid"])
            self.assertTrue(
                any("symlinked catalogue directory" in issue for issue in report["issues"]),
                report,
            )

    def test_manifest_path_cannot_escape_vehicles_tree(self) -> None:
        with self.assertRaises(profile_catalogue.VehicleProfileCatalogueError):
            profile_catalogue.normalize_catalogue(
                {
                    "schema_version": 1,
                    "catalogue_id": "open-mmi.maintained-vehicles",
                    "profiles": {
                        "escape": {
                            "path": "../outside/config.json",
                            "aliases": [],
                        }
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
