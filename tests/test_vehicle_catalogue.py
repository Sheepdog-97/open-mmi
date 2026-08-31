from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from ui import vehicle_catalogue, vehicle_setup


class VehicleCatalogueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.maintained = self.root / "installed"
        self.custom = self.root / "user" / "open-mmi"
        self.roots = vehicle_setup.CatalogueRoots(
            maintained=self.maintained,
            custom=self.custom,
        )
        self.lifecycle_lock = self.root / "lifecycle.lock"
        self.lifecycle_lock.write_text("", encoding="utf-8")
        self.lifecycle_lock.chmod(0o644)
        self.profile_document = {
            "default_bus": "comfort",
            "can_buses": {
                "comfort": {
                    "interface": "can0",
                    "bitrate": 100000,
                    "provisioning": "udev",
                    "bring_up": False,
                }
            },
            "rules": [
                {"id": "0x100", "byte": 0, "value": 1, "event": "play_pause"}
            ],
            "presence": [],
            "status": [],
        }
        self.bindings_document = {
            "play_pause": {"action": "media.playback.toggle"}
        }
        self.profile_path = self._write_json(
            self.maintained / "vehicles" / "seat_1p" / "config.json",
            self.profile_document,
        )
        self.bindings_path = self._write_json(
            self.maintained / "bindings" / "default.json",
            self.bindings_document,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def _write_json(self, path: Path, document: object) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        directory = path.parent
        while directory != self.root:
            directory.chmod(0o700)
            directory = directory.parent
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o644)
        return path

    @staticmethod
    def _revision(path: Path) -> str:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()

    def request(self, kind: str, template_id: str, revision: str, custom_id: str):
        return {
            "kind": kind,
            "id": custom_id,
            "template_source": "maintained",
            "template_id": template_id,
            "template_revision": revision,
        }


    def _copy_custom(self, kind: str, identifier: str) -> Path:
        if kind == "profile":
            template_id = "seat_1p"
            template_path = self.profile_path
            destination = self.custom / "vehicles" / identifier / "config.json"
        else:
            template_id = "default"
            template_path = self.bindings_path
            destination = self.custom / "bindings" / f"{identifier}.json"
        vehicle_catalogue.copy_maintained_template(
            self.request(kind, template_id, self._revision(template_path), identifier),
            roots=self.roots,
        )
        return destination

    def edit_request(
        self,
        kind: str,
        identifier: str,
        revision: str,
        content: str,
    ) -> dict[str, str]:
        return {
            "kind": kind,
            "source": "custom",
            "id": identifier,
            "expected_revision": revision,
            "content": content,
        }

    def lifecycle_request(
        self,
        action: str,
        kind: str,
        identifier: str,
        revision: str,
        new_id: str | None = None,
    ) -> dict[str, str]:
        payload = {
            "action": action,
            "kind": kind,
            "source": "custom",
            "id": identifier,
            "expected_revision": revision,
        }
        if new_id is not None:
            payload["new_id"] = new_id
        return payload

    @staticmethod
    def inactive_setup() -> dict[str, dict[str, str]]:
        return {
            "vehicle": {"source": "maintained", "id": "seat_1p"},
            "bindings": {"source": "maintained", "id": "default"},
        }

    def test_profile_copy_preserves_maintained_bytes_and_writes_only_user_catalogue(self):
        maintained_before = self.profile_path.read_bytes()
        result = vehicle_catalogue.copy_maintained_template(
            self.request(
                "profile",
                "seat_1p",
                self._revision(self.profile_path),
                "my-seat",
            ),
            roots=self.roots,
        )

        destination = self.custom / "vehicles" / "my-seat" / "config.json"
        self.assertEqual(destination.read_bytes(), maintained_before)
        self.assertEqual(self.profile_path.read_bytes(), maintained_before)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual(result["action"], "copy-maintained-template")
        self.assertEqual(result["custom"], {
            "source": "custom",
            "id": "my-seat",
            "revision": self._revision(destination),
        })
        provenance_path = (
            self.custom
            / ".open-mmi-provenance"
            / "profile"
            / "my-seat.json"
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(stat.S_IMODE(provenance_path.stat().st_mode), 0o600)
        self.assertEqual(provenance["kind"], "profile")
        self.assertEqual(provenance["id"], "my-seat")
        self.assertEqual(provenance["template"]["source"], "maintained")
        self.assertEqual(provenance["template"]["id"], "seat_1p")
        self.assertEqual(
            provenance["template"]["revision"],
            self._revision(self.profile_path),
        )
        self.assertTrue(provenance["template"]["open_mmi_version"])
        catalogue = vehicle_setup.catalogue_payload(self.roots)
        self.assertIn(
            ("custom", "my-seat"),
            [(entry["source"], entry["id"]) for entry in catalogue["profiles"]],
        )

    def test_bindings_copy_is_revision_bound_and_private(self):
        result = vehicle_catalogue.copy_maintained_template(
            self.request(
                "bindings",
                "default",
                self._revision(self.bindings_path),
                "my-controls",
            ),
            roots=self.roots,
        )
        destination = self.custom / "bindings" / "my-controls.json"
        self.assertEqual(destination.read_bytes(), self.bindings_path.read_bytes())
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(result["kind"], "bindings")
        provenance_path = (
            self.custom
            / ".open-mmi-provenance"
            / "bindings"
            / "my-controls.json"
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(provenance["template"]["id"], "default")
        self.assertEqual(stat.S_IMODE(provenance_path.stat().st_mode), 0o600)


    def test_custom_profile_load_returns_exact_private_content_and_revision(self):
        destination = self._copy_custom("profile", "my-seat")
        result = vehicle_catalogue.load_custom_item(
            {"kind": "profile", "source": "custom", "id": "my-seat"},
            roots=self.roots,
        )
        self.assertEqual(result["action"], "load-custom-item")
        self.assertEqual(result["content"].encode("utf-8"), destination.read_bytes())
        self.assertEqual(result["custom"], {
            "source": "custom",
            "id": "my-seat",
            "revision": self._revision(destination),
        })
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_custom_save_is_revision_bound_validated_atomic_and_unapplied(self):
        destination = self._copy_custom("profile", "my-seat")
        provenance = self.custom / ".open-mmi-provenance" / "profile" / "my-seat.json"
        maintained_before = self.profile_path.read_bytes()
        provenance_before = provenance.read_bytes()
        previous_inode = destination.stat().st_ino
        updated = dict(self.profile_document)
        updated["rules"] = [
            {"id": "0x100", "byte": 0, "value": 2, "event": "play_pause"}
        ]
        content = json.dumps(updated, indent=2) + "\n"

        result = vehicle_catalogue.save_custom_item(
            self.edit_request(
                "profile",
                "my-seat",
                self._revision(destination),
                content,
            ),
            roots=self.roots,
        )

        self.assertEqual(destination.read_text(encoding="utf-8"), content)
        self.assertNotEqual(destination.stat().st_ino, previous_inode)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(self.profile_path.read_bytes(), maintained_before)
        self.assertEqual(provenance.read_bytes(), provenance_before)
        self.assertEqual(result["action"], "save-custom-item")
        self.assertFalse(result["applied"] )
        self.assertEqual(result["custom"]["revision"], self._revision(destination))
        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(
            list(destination.parent.glob(".open-mmi-save-*.tmp")),
            [],
        )

    def test_stale_custom_save_is_a_conflict_without_overwrite(self):
        destination = self._copy_custom("bindings", "my-controls")
        stale_revision = self._revision(destination)
        external = {
            "play_pause": {"module": "audio", "func": "stop", "args": []}
        }
        destination.write_text(json.dumps(external) + "\n", encoding="utf-8")
        destination.chmod(0o600)
        before = destination.read_bytes()

        with self.assertRaises(
            vehicle_catalogue.VehicleCatalogueConflictError
        ) as raised:
            vehicle_catalogue.save_custom_item(
                self.edit_request(
                    "bindings",
                    "my-controls",
                    stale_revision,
                    json.dumps(self.bindings_document) + "\n",
                ),
                roots=self.roots,
            )
        self.assertEqual(raised.exception.code, "custom-stale")
        self.assertEqual(destination.read_bytes(), before)

    def test_invalid_custom_content_is_never_written(self):
        destination = self._copy_custom("profile", "my-seat")
        revision = self._revision(destination)
        before = destination.read_bytes()
        for content, message in (
            ('{"rules":[', "valid UTF-8 JSON"),
            ('{"rules":[{"id":"bad"}]}\n', "not valid"),
            ('{"rules":[],"rules":[]}\n', "Duplicate"),
        ):
            with self.subTest(content=content), self.assertRaisesRegex(
                vehicle_catalogue.VehicleCatalogueError,
                message,
            ):
                vehicle_catalogue.save_custom_item(
                    self.edit_request(
                        "profile", "my-seat", revision, content
                    ),
                    roots=self.roots,
                )
            self.assertEqual(destination.read_bytes(), before)

    def test_custom_edit_routes_never_accept_maintained_or_path_shaped_identity(self):
        for payload in (
            {"kind": "profile", "source": "maintained", "id": "seat_1p"},
            {"kind": "profile", "source": "custom", "id": "../seat"},
        ):
            with self.subTest(payload=payload), self.assertRaises(
                vehicle_catalogue.VehicleCatalogueError
            ):
                vehicle_catalogue.load_custom_item(payload, roots=self.roots)
        self.assertFalse(self.custom.exists())

    def test_custom_edit_rejects_hardlinks_and_nonprivate_directories(self):
        destination = self._copy_custom("bindings", "my-controls")
        alias = destination.parent / "alias.json"
        os.link(destination, alias)
        request = {"kind": "bindings", "source": "custom", "id": "my-controls"}
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError, "untrusted"
        ):
            vehicle_catalogue.load_custom_item(request, roots=self.roots)
        alias.unlink()
        self.custom.chmod(0o755)
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError, "untrusted"
        ):
            vehicle_catalogue.load_custom_item(request, roots=self.roots)

    def test_untrusted_custom_file_is_not_loaded_or_saved(self):
        destination = self._copy_custom("bindings", "my-controls")
        destination.chmod(0o644)
        request = {"kind": "bindings", "source": "custom", "id": "my-controls"}
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError, "untrusted"
        ):
            vehicle_catalogue.load_custom_item(request, roots=self.roots)
        destination.chmod(0o600)
        outside = self.root / "outside.json"
        outside.write_text("{}\n", encoding="utf-8")
        outside.chmod(0o600)
        destination.unlink()
        destination.symlink_to(outside)
        with self.assertRaises(
            (vehicle_catalogue.VehicleCatalogueError, vehicle_setup.VehicleSetupError)
        ):
            vehicle_catalogue.load_custom_item(request, roots=self.roots)
        self.assertEqual(outside.read_text(encoding="utf-8"), "{}\n")


    def test_custom_duplicate_preserves_source_and_records_exact_derivation(self):
        source = self._copy_custom("profile", "my-seat")
        source_before = source.read_bytes()
        source_provenance = (
            self.custom / ".open-mmi-provenance" / "profile" / "my-seat.json"
        ).read_bytes()

        result = vehicle_catalogue.manage_custom_item(
            self.lifecycle_request(
                "duplicate",
                "profile",
                "my-seat",
                self._revision(source),
                "my-seat-copy",
            ),
            roots=self.roots,
            active=self.inactive_setup(),
            lifecycle_lock=self.lifecycle_lock,
        )

        destination = self.custom / "vehicles" / "my-seat-copy" / "config.json"
        provenance_path = (
            self.custom
            / ".open-mmi-provenance"
            / "profile"
            / "my-seat-copy.json"
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(source.read_bytes(), source_before)
        self.assertEqual(destination.read_bytes(), source_before)
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(provenance_path.stat().st_mode), 0o600)
        self.assertEqual(provenance["derived_from"], {
            "source": "custom",
            "id": "my-seat",
            "revision": self._revision(source),
        })
        self.assertEqual(provenance["template"]["id"], "seat_1p")
        self.assertEqual(
            (self.custom / ".open-mmi-provenance" / "profile" / "my-seat.json").read_bytes(),
            source_provenance,
        )
        self.assertEqual(result["operation"], "duplicate")
        self.assertFalse(result["applied"])
        self.assertEqual(result["custom"]["id"], "my-seat-copy")

    def test_custom_rename_moves_exact_inactive_item_and_provenance(self):
        source = self._copy_custom("bindings", "my-controls")
        revision = self._revision(source)
        result = vehicle_catalogue.manage_custom_item(
            self.lifecycle_request(
                "rename",
                "bindings",
                "my-controls",
                revision,
                "driver-controls",
            ),
            roots=self.roots,
            active=self.inactive_setup(),
            lifecycle_lock=self.lifecycle_lock,
        )

        destination = self.custom / "bindings" / "driver-controls.json"
        old_provenance = (
            self.custom / ".open-mmi-provenance" / "bindings" / "my-controls.json"
        )
        new_provenance = (
            self.custom / ".open-mmi-provenance" / "bindings" / "driver-controls.json"
        )
        provenance = json.loads(new_provenance.read_text(encoding="utf-8"))
        self.assertFalse(source.exists())
        self.assertTrue(destination.exists())
        self.assertEqual(self._revision(destination), revision)
        self.assertFalse(old_provenance.exists())
        self.assertEqual(provenance["id"], "driver-controls")
        self.assertIn("my-controls", provenance["previous_ids"])
        self.assertTrue(provenance["renamed_at"])
        self.assertEqual(result["operation"], "rename")
        self.assertEqual(result["custom"]["id"], "driver-controls")
        self.assertFalse(result["applied"])

    def test_custom_delete_removes_exact_inactive_item_and_provenance(self):
        source = self._copy_custom("profile", "old-seat")
        provenance = (
            self.custom / ".open-mmi-provenance" / "profile" / "old-seat.json"
        )
        result = vehicle_catalogue.manage_custom_item(
            self.lifecycle_request(
                "delete", "profile", "old-seat", self._revision(source)
            ),
            roots=self.roots,
            active=self.inactive_setup(),
            lifecycle_lock=self.lifecycle_lock,
        )
        self.assertFalse(source.parent.exists())
        self.assertFalse(provenance.exists())
        self.assertEqual(result["operation"], "delete")
        self.assertEqual(result["deleted"]["id"], "old-seat")
        self.assertFalse(result["applied"])
        self.assertEqual(
            list((self.custom / "vehicles").glob(".open-mmi-delete-*")), []
        )

    def test_invalid_custom_profile_can_be_loaded_renamed_and_deleted_but_not_duplicated(self):
        source = self._copy_custom("profile", "broken-seat")
        document = dict(self.profile_document)
        document["rules"] = [
            {"id": "0x100", "byte": 0, "value": 1, "event": "not_registered"}
        ]
        source.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        source.chmod(0o600)
        revision = self._revision(source)

        loaded = vehicle_catalogue.load_custom_item(
            {"kind": "profile", "source": "custom", "id": "broken-seat"},
            roots=self.roots,
        )
        self.assertFalse(loaded["validation"]["valid"])
        self.assertEqual(loaded["custom"]["revision"], revision)

        with self.assertRaises(vehicle_catalogue.VehicleCatalogueError):
            vehicle_catalogue.manage_custom_item(
                self.lifecycle_request(
                    "duplicate", "profile", "broken-seat", revision, "broken-copy"
                ),
                roots=self.roots,
                active=self.inactive_setup(),
                lifecycle_lock=self.lifecycle_lock,
            )
        self.assertFalse((self.custom / "vehicles" / "broken-copy").exists())

        renamed = vehicle_catalogue.manage_custom_item(
            self.lifecycle_request(
                "rename", "profile", "broken-seat", revision, "broken-renamed"
            ),
            roots=self.roots,
            active=self.inactive_setup(),
            lifecycle_lock=self.lifecycle_lock,
        )
        renamed_path = self.custom / "vehicles" / "broken-renamed" / "config.json"
        self.assertTrue(renamed_path.exists())
        self.assertEqual(renamed["custom"]["revision"], revision)

        deleted = vehicle_catalogue.manage_custom_item(
            self.lifecycle_request("delete", "profile", "broken-renamed", revision),
            roots=self.roots,
            active=self.inactive_setup(),
            lifecycle_lock=self.lifecycle_lock,
        )
        self.assertFalse(renamed_path.parent.exists())
        self.assertEqual(deleted["deleted"]["revision"], revision)

    def test_active_custom_items_cannot_be_renamed_or_deleted(self):
        profile = self._copy_custom("profile", "active-seat")
        bindings = self._copy_custom("bindings", "active-controls")
        active = {
            "vehicle": {"source": "custom", "id": "active-seat"},
            "bindings": {"source": "custom", "id": "active-controls"},
        }
        operations = (
            ("rename", "profile", "active-seat", profile, "renamed-seat"),
            ("delete", "bindings", "active-controls", bindings, None),
        )
        for action, kind, identifier, path, new_id in operations:
            before = path.read_bytes()
            with self.subTest(action=action), self.assertRaises(
                vehicle_catalogue.VehicleCatalogueConflictError
            ) as raised:
                vehicle_catalogue.manage_custom_item(
                    self.lifecycle_request(
                        action, kind, identifier, self._revision(path), new_id
                    ),
                    roots=self.roots,
                    active=active,
                    lifecycle_lock=self.lifecycle_lock,
                )
            self.assertEqual(raised.exception.code, "custom-active")
            self.assertEqual(path.read_bytes(), before)

    def test_lifecycle_stale_revision_and_existing_destination_never_mutate(self):
        source = self._copy_custom("bindings", "my-controls")
        before = source.read_bytes()
        with self.assertRaises(
            vehicle_catalogue.VehicleCatalogueConflictError
        ) as stale:
            vehicle_catalogue.manage_custom_item(
                self.lifecycle_request(
                    "delete",
                    "bindings",
                    "my-controls",
                    "sha256:" + "0" * 64,
                ),
                roots=self.roots,
                active=self.inactive_setup(),
                lifecycle_lock=self.lifecycle_lock,
            )
        self.assertEqual(stale.exception.code, "custom-stale")
        self.assertEqual(source.read_bytes(), before)

        existing = self._copy_custom("bindings", "existing")
        with self.assertRaises(
            vehicle_catalogue.VehicleCatalogueConflictError
        ) as conflict:
            vehicle_catalogue.manage_custom_item(
                self.lifecycle_request(
                    "rename",
                    "bindings",
                    "my-controls",
                    self._revision(source),
                    "existing",
                ),
                roots=self.roots,
                active=self.inactive_setup(),
                lifecycle_lock=self.lifecycle_lock,
            )
        self.assertEqual(conflict.exception.code, "custom-exists")
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(existing.exists())

    def test_lifecycle_rejects_maintained_path_shaped_and_busy_requests(self):
        source = self._copy_custom("profile", "my-seat")
        invalid = self.lifecycle_request(
            "delete", "profile", "my-seat", self._revision(source)
        )
        invalid["source"] = "maintained"
        with self.assertRaises(vehicle_catalogue.VehicleCatalogueError):
            vehicle_catalogue.manage_custom_item(
                invalid,
                roots=self.roots,
                active=self.inactive_setup(),
                lifecycle_lock=self.lifecycle_lock,
            )
        invalid = self.lifecycle_request(
            "rename", "profile", "my-seat", self._revision(source), "../bad"
        )
        with self.assertRaises(vehicle_catalogue.VehicleCatalogueError):
            vehicle_catalogue.manage_custom_item(
                invalid,
                roots=self.roots,
                active=self.inactive_setup(),
                lifecycle_lock=self.lifecycle_lock,
            )

        import fcntl
        with self.lifecycle_lock.open("r", encoding="utf-8") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(
                vehicle_catalogue.VehicleCatalogueConflictError
            ) as busy:
                vehicle_catalogue.manage_custom_item(
                    self.lifecycle_request(
                        "duplicate",
                        "profile",
                        "my-seat",
                        self._revision(source),
                        "copy",
                    ),
                    roots=self.roots,
                    active=self.inactive_setup(),
                    lifecycle_lock=self.lifecycle_lock,
                )
            self.assertEqual(busy.exception.code, "lifecycle-busy")

    def test_copy_rejects_custom_or_incomplete_source_identity(self):
        revision = self._revision(self.profile_path)
        for payload in (
            {
                "kind": "profile",
                "id": "copy",
                "template_source": "custom",
                "template_id": "seat_1p",
                "template_revision": revision,
            },
            {
                "kind": "profile",
                "id": "copy",
                "template_source": "maintained",
                "template_id": "seat_1p",
            },
            {
                "kind": "profile",
                "id": "../copy",
                "template_source": "maintained",
                "template_id": "seat_1p",
                "template_revision": revision,
            },
        ):
            with self.subTest(payload=payload), self.assertRaises(
                vehicle_catalogue.VehicleCatalogueError
            ):
                vehicle_catalogue.copy_maintained_template(payload, roots=self.roots)
        self.assertFalse(self.custom.exists())

    def test_stale_template_revision_is_a_conflict_without_destination(self):
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueConflictError,
            "template changed",
        ) as raised:
            vehicle_catalogue.copy_maintained_template(
                self.request(
                    "profile",
                    "seat_1p",
                    "sha256:" + "0" * 64,
                    "my-seat",
                ),
                roots=self.roots,
            )
        self.assertEqual(raised.exception.code, "template-stale")
        self.assertFalse((self.custom / "vehicles" / "my-seat").exists())

    def test_existing_custom_item_is_never_overwritten(self):
        destination = self._write_json(
            self.custom / "vehicles" / "my-seat" / "config.json",
            {"rules": []},
        )
        before = destination.read_bytes()
        with self.assertRaises(
            vehicle_catalogue.VehicleCatalogueConflictError
        ) as raised:
            vehicle_catalogue.copy_maintained_template(
                self.request(
                    "profile",
                    "seat_1p",
                    self._revision(self.profile_path),
                    "my-seat",
                ),
                roots=self.roots,
            )
        self.assertEqual(raised.exception.code, "custom-exists")
        self.assertEqual(destination.read_bytes(), before)

    def test_untrusted_custom_root_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.custom.parent.mkdir(parents=True)
        self.custom.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "untrusted",
        ):
            vehicle_catalogue.copy_maintained_template(
                self.request(
                    "bindings",
                    "default",
                    self._revision(self.bindings_path),
                    "my-controls",
                ),
                roots=self.roots,
            )
        self.assertEqual(list(outside.iterdir()), [])

    def test_writable_maintained_template_is_not_trusted(self):
        self.profile_path.chmod(0o666)
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "untrusted",
        ):
            vehicle_catalogue.copy_maintained_template(
                self.request(
                    "profile",
                    "seat_1p",
                    self._revision(self.profile_path),
                    "my-seat",
                ),
                roots=self.roots,
            )
        self.assertFalse(self.custom.exists())

    def test_invalid_maintained_template_is_not_copied(self):
        self.profile_path.write_text('{"rules":[{"id":"bad"}]}\n', encoding="utf-8")
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "not valid",
        ):
            vehicle_catalogue.copy_maintained_template(
                self.request(
                    "profile",
                    "seat_1p",
                    self._revision(self.profile_path),
                    "my-seat",
                ),
                roots=self.roots,
            )
        self.assertFalse(self.custom.exists())

    def test_profile_import_is_validated_private_and_unapplied(self):
        content = json.dumps(self.profile_document, indent=2) + "\n\n"
        maintained_before = self.profile_path.read_bytes()

        result = vehicle_catalogue.import_custom_item(
            {"kind": "profile", "id": "imported-seat", "content": content},
            roots=self.roots,
        )

        destination = self.custom / "vehicles" / "imported-seat" / "config.json"
        provenance_path = (
            self.custom / ".open-mmi-provenance" / "profile" / "imported-seat.json"
        )
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
        self.assertEqual(destination.read_text(encoding="utf-8"), content)
        self.assertEqual(self.profile_path.read_bytes(), maintained_before)
        self.assertEqual(stat.S_IMODE(destination.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(provenance_path.stat().st_mode), 0o600)
        self.assertEqual(provenance["origin"], {"type": "import"})
        self.assertNotIn("template", provenance)
        self.assertEqual(result["action"], "import-custom-item")
        self.assertEqual(result["custom"], {
            "source": "custom",
            "id": "imported-seat",
            "revision": self._revision(destination),
        })
        self.assertTrue(result["validation"]["valid"])
        self.assertFalse(result["applied"])
        catalogue = vehicle_setup.catalogue_payload(self.roots)
        self.assertIn(
            ("custom", "imported-seat"),
            [(entry["source"], entry["id"]) for entry in catalogue["profiles"]],
        )

    def test_bindings_import_preserves_exact_json_bytes(self):
        content = json.dumps(self.bindings_document, separators=(",", ":")) + "\r\n"
        result = vehicle_catalogue.import_custom_item(
            {"kind": "bindings", "id": "imported-controls", "content": content},
            roots=self.roots,
        )
        destination = self.custom / "bindings" / "imported-controls.json"
        self.assertEqual(destination.read_bytes(), content.encode("utf-8"))
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(result["custom"]["revision"], self._revision(destination))
        self.assertFalse(result["applied"])

    def test_invalid_import_and_existing_destination_never_mutate(self):
        invalid = '{"rules":[{"id":"bad"}]}\n'
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "not valid",
        ):
            vehicle_catalogue.import_custom_item(
                {"kind": "profile", "id": "invalid-seat", "content": invalid},
                roots=self.roots,
            )
        self.assertFalse((self.custom / "vehicles" / "invalid-seat").exists())
        self.assertFalse((
            self.custom / ".open-mmi-provenance" / "profile" / "invalid-seat.json"
        ).exists())

        destination = self._copy_custom("bindings", "existing-controls")
        before = destination.read_bytes()
        with self.assertRaises(
            vehicle_catalogue.VehicleCatalogueConflictError
        ) as raised:
            vehicle_catalogue.import_custom_item(
                {
                    "kind": "bindings",
                    "id": "existing-controls",
                    "content": json.dumps(self.bindings_document),
                },
                roots=self.roots,
            )
        self.assertEqual(raised.exception.code, "custom-exists")
        self.assertEqual(destination.read_bytes(), before)

    def test_import_rejects_duplicate_keys_and_oversize_before_creation(self):
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "Duplicate catalogue JSON field",
        ):
            vehicle_catalogue.import_custom_item(
                {
                    "kind": "bindings",
                    "id": "duplicate-keys",
                    "content": '{"play":{},"play":{}}',
                },
                roots=self.roots,
            )
        with self.assertRaisesRegex(
            vehicle_catalogue.VehicleCatalogueError,
            "size limit",
        ):
            vehicle_catalogue.import_custom_item(
                {
                    "kind": "bindings",
                    "id": "too-large",
                    "content": " " * (vehicle_setup.MAX_BINDINGS_BYTES + 1),
                },
                roots=self.roots,
            )
        self.assertFalse(self.custom.exists())

    def test_import_request_schema_is_strict(self):
        requests = [
            {"kind": "profile", "id": "valid-id"},
            {"kind": "profile", "id": "../escape", "content": "{}"},
            {"kind": "maintained", "id": "valid-id", "content": "{}"},
            {"kind": "bindings", "id": "valid-id", "content": {}, "path": "/tmp/x"},
        ]
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(vehicle_catalogue.VehicleCatalogueError):
                    vehicle_catalogue.import_custom_item(request, roots=self.roots)
        self.assertFalse(self.custom.exists())


if __name__ == "__main__":
    unittest.main()
