from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from open_mmi_trust import release_provenance
from open_mmi_trust.release_provenance import (
    ReleaseProvenanceError,
    _write_provenance_root,
    build_provenance_root,
    canonical_provenance_root_bytes,
    describe_public_key,
    provenance_root_digest,
    read_provenance_root,
    validate_provenance_root,
    verify_commit_provenance,
)
from open_mmi_trust import release_provenance_cli


GPG = shutil.which("gpg")
GIT = shutil.which("git")


@unittest.skipUnless(GPG and GIT, "gpg and git are required for release provenance tests")
class ReleaseProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._key_home_temp = tempfile.TemporaryDirectory()
        cls.key_home = Path(cls._key_home_temp.name)
        cls.key_home.chmod(0o700)
        cls.primary, cls.signing, cls.public_key = cls._generate_key(
            cls.key_home, "Open MMI Test Release <release-one@open-mmi.invalid>"
        )
        cls.other_primary, cls.other_signing, cls.other_public_key = cls._generate_key(
            cls.key_home, "Open MMI Other Release <release-two@open-mmi.invalid>"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._key_home_temp.cleanup()

    @classmethod
    def _gpg(cls, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(GPG), "--batch", "--homedir", str(cls.key_home), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    @classmethod
    def _generate_key(cls, home: Path, identity: str) -> tuple[str, str, bytes]:
        del home
        cls._gpg("--passphrase", "", "--quick-gen-key", identity, "ed25519", "cert", "2y")
        listing = cls._gpg("--with-colons", "--list-keys", identity).stdout.decode()
        primary = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        cls._gpg("--passphrase", "", "--quick-add-key", primary, "ed25519", "sign", "2y")
        listing = cls._gpg("--with-colons", "--list-keys", primary).stdout.decode()
        lines = listing.splitlines()
        signing = ""
        saw_sub = False
        for line in lines:
            if line.startswith("sub:"):
                saw_sub = True
                continue
            if saw_sub and line.startswith("fpr:"):
                signing = line.split(":")[9]
                break
        if not signing:
            raise AssertionError("test signing subkey was not generated")
        public_key = cls._gpg("--export", primary).stdout
        return primary, signing, public_key

    def _repo_with_signed_commit(self, signing: str | None = None) -> tuple[tempfile.TemporaryDirectory, Path, str]:
        temporary = tempfile.TemporaryDirectory()
        repo = Path(temporary.name).resolve()
        subprocess.run([str(GIT), "init", "-b", "main", str(repo)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(GIT), "-C", str(repo), "config", "user.name", "Open MMI Test"], check=True)
        subprocess.run([str(GIT), "-C", str(repo), "config", "user.email", "release-one@open-mmi.invalid"], check=True)
        (repo / "payload.txt").write_text("signed release\n", encoding="utf-8")
        subprocess.run([str(GIT), "-C", str(repo), "add", "payload.txt"], check=True)
        environment = {**os.environ, "GNUPGHOME": str(self.key_home)}
        subprocess.run(
            [
                str(GIT), "-C", str(repo),
                "-c", f"user.signingkey={signing or self.signing}",
                "commit", "-S", "-m", "signed release",
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        commit = subprocess.run(
            [str(GIT), "-C", str(repo), "rev-parse", "HEAD"],
            check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        return temporary, repo, commit

    def _root(self, public_key: bytes, commit: str, *, timestamp: str = "2026-09-01T12:00:00+00:00") -> dict:
        return build_provenance_root(
            key_bytes=public_key,
            baseline_commit=commit,
            baseline_integrity_state_digest="sha256:" + "1" * 64,
            established_at=timestamp,
        )

    def test_openpgp_signature_is_verified_offline_against_pinned_primary_and_subkey(self):
        temporary, repo, commit = self._repo_with_signed_commit()
        self.addCleanup(temporary.cleanup)
        root = self._root(self.public_key, commit)
        self.assertEqual(root["primary_fingerprint"], self.primary)
        self.assertIn(self.signing, root["signing_fingerprints"])
        evidence = verify_commit_provenance(repo, commit, root)
        self.assertTrue(evidence["verified"])
        self.assertEqual(evidence["primary_fingerprint"], self.primary)
        self.assertEqual(evidence["signing_fingerprint"], self.signing)
        self.assertEqual(evidence["candidate_commit"], commit)

    def test_wrong_pinned_key_and_unsigned_commit_fail_closed(self):
        temporary, repo, commit = self._repo_with_signed_commit()
        self.addCleanup(temporary.cleanup)
        wrong = self._root(self.other_public_key, commit)
        with self.assertRaisesRegex(ReleaseProvenanceError, "valid signature from the pinned signer"):
            verify_commit_provenance(repo, commit, wrong)

        (repo / "payload.txt").write_text("unsigned next release\n", encoding="utf-8")
        subprocess.run([str(GIT), "-C", str(repo), "add", "payload.txt"], check=True)
        subprocess.run(
            [str(GIT), "-C", str(repo), "-c", "commit.gpgSign=false", "commit", "-m", "unsigned"],
            check=True, stdout=subprocess.DEVNULL,
        )
        unsigned = subprocess.run(
            [str(GIT), "-C", str(repo), "rev-parse", "HEAD"],
            check=True, text=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        good_root = self._root(self.public_key, commit)
        with self.assertRaisesRegex(ReleaseProvenanceError, "valid signature from the pinned signer"):
            verify_commit_provenance(repo, unsigned, good_root)

    def test_public_key_description_rejects_secret_material_and_multiple_primaries(self):
        secret = self._gpg("--passphrase", "", "--export-secret-keys", self.primary).stdout
        with self.assertRaisesRegex(ReleaseProvenanceError, "public key material only"):
            describe_public_key(secret)
        with self.assertRaisesRegex(ReleaseProvenanceError, "exactly one OpenPGP primary key"):
            describe_public_key(self.public_key + self.other_public_key)

    def test_state_validation_is_strict_deterministic_and_key_digest_bound(self):
        temporary, _repo, commit = self._repo_with_signed_commit()
        self.addCleanup(temporary.cleanup)
        root = self._root(self.public_key, commit)
        self.assertEqual(canonical_provenance_root_bytes(root), canonical_provenance_root_bytes(copy.deepcopy(root)))
        self.assertTrue(provenance_root_digest(root).startswith("sha256:"))

        bad = copy.deepcopy(root)
        bad["unexpected"] = True
        with self.assertRaisesRegex(ReleaseProvenanceError, "unknown keys"):
            validate_provenance_root(bad)
        bad = copy.deepcopy(root)
        bad["public_key_sha256"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(ReleaseProvenanceError, "digest does not match"):
            validate_provenance_root(bad)
        bad = copy.deepcopy(root)
        bad["signing_fingerprints"] = list(reversed(bad["signing_fingerprints"])) + bad["signing_fingerprints"][:1]
        with self.assertRaisesRegex(ReleaseProvenanceError, "sorted and unique"):
            validate_provenance_root(bad)

    def test_state_file_is_private_create_once_and_weakened_modes_fail(self):
        temporary, _repo, commit = self._repo_with_signed_commit()
        self.addCleanup(temporary.cleanup)
        root = self._root(self.public_key, commit)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trust" / "release-signer-root.v1.json"
            stored = _write_provenance_root(root, path)
            self.assertEqual(read_provenance_root(path), stored)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            with self.assertRaisesRegex(ReleaseProvenanceError, "already established"):
                _write_provenance_root(root, path)
            path.chmod(0o644)
            with self.assertRaisesRegex(ReleaseProvenanceError, "untrusted"):
                read_provenance_root(path)

    def test_duplicate_json_fields_and_nonfinite_values_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "root.json"
            path.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")
            path.chmod(0o600)
            with self.assertRaisesRegex(ReleaseProvenanceError, "duplicate"):
                read_provenance_root(path)
            path.write_text('{"schema_version":NaN}\n', encoding="utf-8")
            with self.assertRaisesRegex(ReleaseProvenanceError, "invalid release signer root JSON number"):
                read_provenance_root(path)

    def test_fixed_verifier_programs_must_be_root_controlled_regular_executables(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "bin"
            parent.mkdir()
            program = parent / "gpg"
            program.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            program.chmod(0o775)
            # A group-writable verifier is mutable outside the fixed root-controlled boundary.
            with self.assertRaisesRegex(ReleaseProvenanceError, "untrusted"):
                release_provenance._require_trusted_system_program(program, "OpenPGP")

        release_provenance._require_trusted_system_program(Path("/usr/bin/gpg"), "OpenPGP")
        release_provenance._require_trusted_system_program(Path("/usr/bin/git"), "Git")

    def test_cli_has_no_rotation_or_noninteractive_bypass_surface(self):
        parser = release_provenance_cli.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["rotate"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["bootstrap", "--key-file", "/tmp/key.asc", "--yes"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["bootstrap", "--key-file", "/tmp/key.asc", "--state-file", "/tmp/root.json"])

    def test_key_file_reader_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.asc"
            key.write_bytes(self.public_key)
            link = root / "link.asc"
            link.symlink_to(key)
            with self.assertRaisesRegex(ReleaseProvenanceError, "cannot be opened safely"):
                release_provenance_cli._read_public_key_file(link)

    def test_bootstrap_confirmation_is_full_primary_fingerprint_bound(self):
        temporary, repo, commit = self._repo_with_signed_commit()
        self.addCleanup(temporary.cleanup)
        proposed = self._root(self.public_key, commit, timestamp="2000-01-01T00:00:00+00:00")
        integrity = {
            "candidate_commit": commit,
        }
        key_file = Path(temporary.name) / "signer.asc"
        key_file.write_bytes(self.public_key)
        fake_integrity_digest = "sha256:" + "2" * 64

        with patch.object(release_provenance_cli, "_require_root"), patch.object(
            release_provenance_cli, "_require_local_tty"
        ), patch.object(
            release_provenance_cli, "read_provenance_root", return_value=None
        ), patch.object(
            release_provenance_cli, "_current_integrity", return_value=(integrity, repo, repo, repo)
        ), patch.object(
            release_provenance_cli.release_integrity, "integrity_state_digest", return_value=fake_integrity_digest
        ), patch.object(
            release_provenance_cli, "build_provenance_root", return_value=proposed
        ), patch.object(
            release_provenance_cli, "verify_commit_provenance", return_value={
                "verified": True, "candidate_commit": commit,
                "primary_fingerprint": self.primary, "signing_fingerprint": self.signing,
                "signature_date": "2026-09-01", "signature_timestamp": 1,
                "provenance_root_digest": "sha256:" + "3" * 64,
            }
        ), patch.object(
            release_provenance_cli, "_write_provenance_root", return_value=proposed
        ) as writer, patch("builtins.input", return_value="PIN RELEASE SIGNER wrong"):
            result = release_provenance_cli.main(["bootstrap", "--key-file", str(key_file)])
        self.assertEqual(result, 2)
        writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
