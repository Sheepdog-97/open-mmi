from __future__ import annotations

import ast
import base64
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path, PurePath

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "independent_checker" / "open_mmi_trust_check.py"
spec = importlib.util.spec_from_file_location("independent_checker_under_test", CHECKER)
assert spec and spec.loader
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)

GPG = shutil.which("gpg")
GIT = shutil.which("git")


def write(path: Path, data: str | bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)
    path.chmod(mode)


def current_manifest() -> dict:
    return checker.validate_manifest(
        {
            "schema_version": 1,
            "manifest_id": checker.MANIFEST_ID,
            "policy_generation": 6,
            "capabilities": {
                "network.external-egress": {
                    "policy": "declared-purposes-only",
                    "assurance": "os-enforced",
                    "purposes": list(checker.KNOWN_NETWORK_PURPOSES),
                },
                "telemetry.collection": {
                    "policy": "local-owner-opt-in",
                    "assurance": "runtime-guarded",
                },
                "vehicle-data.persistence": {
                    "policy": "declared-purposes-only",
                    "assurance": "os-enforced",
                    "purposes": list(checker.KNOWN_PERSISTENCE_PURPOSES),
                },
                "vehicle.can.receive": {"policy": "allowed", "assurance": "ci-guarded"},
                "vehicle.can.transmit": {"policy": "prohibited", "assurance": "os-enforced"},
                "vehicle.identity.remote-resolution": {
                    "policy": "prohibited",
                    "assurance": "runtime-guarded",
                },
            },
        }
    )


UNIT_TEXTS = {
    "system/open-mmi-media-egress.service": (
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        "ProtectSystem=strict\n"
        "InaccessiblePaths=-/var/lib/open-mmi/trust/telemetry-authorization.v1.json -/var/lib/open-mmi/vehicle-data\n"
    ),
    "system/open-mmi-update-coordinator.service": (
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        "ProtectSystem=strict\n"
        "ReadOnlyPaths=/var/lib/open-mmi/network-egress\n"
        "InaccessiblePaths=-/var/lib/open-mmi/trust/telemetry-authorization.v1.json -/var/lib/open-mmi/vehicle-data\n"
        "ReadWritePaths=/var/lib/open-mmi /run/open-mmi\n"
    ),
    "system/open-mmi-update-installer.service": (
        "IPAddressDeny=any\nIPAddressAllow=localhost\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\nProtectSystem=strict\n"
        "ReadOnlyPaths=/var/lib/open-mmi/network-egress /var/lib/open-mmi/vehicle-data\n"
    ),
    "system/open-mmi-vehicle-store.service": (
        "StateDirectory=open-mmi/vehicle-data\nProtectSystem=strict\nRestrictAddressFamilies=AF_UNIX\n"
    ),
    "system/open-mmi-vehicle-can-provision.service": (
        "ProtectSystem=strict\n"
        "RestrictAddressFamilies=AF_NETLINK AF_UNIX\n"
        "CapabilityBoundingSet=CAP_NET_ADMIN CAP_DAC_READ_SEARCH\n"
    ),
    "system/open-mmi-vehicle-config-coordinator.service": (
        "ProtectSystem=strict\nReadOnlyPaths=/var/lib/open-mmi/vehicle-data\nRestrictAddressFamilies=AF_UNIX\n"
    ),
    "user/open-mmi-dashboard.service": (
        "IPAddressDeny=any\nIPAddressAllow=localhost\nProtectHome=read-only\nProtectSystem=strict\n"
    ),
    "user/canbusd.service": (
        "ProtectHome=read-only\nProtectSystem=strict\nReadWritePaths=%t/open-mmi\n"
        "RestrictAddressFamilies=AF_CAN AF_UNIX\n"
        "CapabilityBoundingSet=\nAmbientCapabilities=\n"
    ),
    "user/open-mmi-owner-config.service": (
        "ProtectHome=read-only\nProtectSystem=strict\n"
        "ReadWritePaths=%h/.config/open-mmi %h/.config/autostart\nRestrictAddressFamilies=AF_UNIX\n"
    ),
}


@unittest.skipUnless(GPG and GIT, "gpg and git are required for independent checker tests")
class IndependentTrustCheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._key_temp = tempfile.TemporaryDirectory()
        cls.key_home = Path(cls._key_temp.name)
        cls.key_home.chmod(0o700)
        cls.primary, cls.signing, cls.public_key = cls._generate_key(
            "Independent Checker Test <checker-one@open-mmi.invalid>"
        )
        cls.other_primary, cls.other_signing, cls.other_public_key = cls._generate_key(
            "Independent Checker Other <checker-two@open-mmi.invalid>"
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._key_temp.cleanup()

    @classmethod
    def _gpg(cls, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [str(GPG), "--batch", "--homedir", str(cls.key_home), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    @classmethod
    def _generate_key(cls, identity: str) -> tuple[str, str, bytes]:
        cls._gpg("--passphrase", "", "--quick-gen-key", identity, "ed25519", "cert", "2y")
        listing = cls._gpg("--with-colons", "--list-keys", identity).stdout.decode()
        primary = next(line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:"))
        cls._gpg("--passphrase", "", "--quick-add-key", primary, "ed25519", "sign", "2y")
        listing = cls._gpg("--with-colons", "--list-keys", primary).stdout.decode()
        signing = ""
        saw_sub = False
        for line in listing.splitlines():
            if line.startswith("sub:"):
                saw_sub = True
            elif saw_sub and line.startswith("fpr:"):
                signing = line.split(":")[9]
                break
        if not signing:
            raise AssertionError("signing subkey was not generated")
        return primary, signing, cls._gpg("--export", primary).stdout

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.base = Path(self._temp.name)
        self.target = self.base / "target"
        self.repo = self.base / "repo"
        previous_umask = os.umask(0o022)
        try:
            self._build_fixture()
        finally:
            os.umask(previous_umask)

    def _build_fixture(self) -> None:
        subprocess.run([str(GIT), "init", "-b", "main", str(self.repo)], check=True, stdout=subprocess.DEVNULL)
        subprocess.run([str(GIT), "-C", str(self.repo), "config", "user.name", "Open MMI Test"], check=True)
        subprocess.run([str(GIT), "-C", str(self.repo), "config", "user.email", "checker-one@open-mmi.invalid"], check=True)
        manifest = current_manifest()
        write(self.repo / "open_mmi_trust/data/trust-manifest.v1.json", json.dumps(manifest, indent=2) + "\n")
        write(self.repo / "open_mmi_trust/vehicle_identity.py", "VALUE = 'signed-package-file'\n")
        write(
            self.repo / "scripts/profile_provision.py",
            'RULE = "listen-only on"\n'
            'DENY = "physical CAN interfaces require bitrate and udev listen-only provisioning"\n',
        )
        write(
            self.repo / "ui/vehicle_config_apply.py",
            'RULE = "listen-only on"\n'
            'DENY = "Physical CAN activation requires bitrate and udev listen-only provisioning"\n'
            'LIVE = ("listen-only", "on")\n',
        )
        for name in checker.SOURCE_RELEASE_FILES:
            write(self.repo / name, f"{name}\n")
        for relative, text in UNIT_TEXTS.items():
            write(self.repo / "systemd" / relative, text)
        subprocess.run([str(GIT), "-C", str(self.repo), "add", "."], check=True)
        environment = {**os.environ, "GNUPGHOME": str(self.key_home)}
        subprocess.run(
            [str(GIT), "-C", str(self.repo), "-c", f"user.signingkey={self.signing}", "commit", "-S", "-m", "signed fixture"],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        self.commit = subprocess.run(
            [str(GIT), "-C", str(self.repo), "rev-parse", "HEAD"],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        inventory = checker.inventory_from_git_commit(self.repo, self.commit)
        self.manifest = manifest

        for entry in inventory:
            relative = entry["path"]
            first = PurePath(relative).parts[0]
            data = subprocess.run(
                [str(GIT), "-C", str(self.repo), "show", f"{self.commit}:{relative}"],
                check=True,
                stdout=subprocess.PIPE,
            ).stdout
            if relative in checker.SOURCE_RELEASE_FILES or first in checker.SOURCE_RELEASE_ROOTS:
                write(self.target / "opt/open-mmi" / relative, data)
            if checker.is_package_runtime_path(relative):
                write(self.target / "opt/open-mmi/venv/lib/python3.12/site-packages" / relative, data)

        write(self.target / "usr/bin/python3", b"python-placeholder", 0o755)
        (self.target / "opt/open-mmi/venv/bin").mkdir(parents=True, exist_ok=True)
        (self.target / "opt/open-mmi/venv/bin/python").symlink_to("/usr/bin/python3")
        for relative, text in UNIT_TEXTS.items():
            write(self.target / "etc/systemd" / relative, text)
        write(
            self.target / "etc/udev/rules.d/80-canbus.rules",
            'SUBSYSTEM=="net", KERNEL=="can0", ACTION=="add", '
            'RUN+="/sbin/ip link set can0 down", '
            'RUN+="/sbin/ip link set can0 type can bitrate 100000 listen-only on", '
            'RUN+="/sbin/ip link set can0 up"\n',
        )

        accepted = checker.validate_accepted_state(
            {
                "schema_version": 1,
                "state_id": checker.ACCEPTED_STATE_ID,
                "accepted_at": "2026-09-02T12:00:00+00:00",
                "manifest_digest": checker.manifest_digest(manifest),
                "manifest": manifest,
            }
        )
        accepted_digest = checker.accepted_state_digest(accepted)
        lineage = checker.validate_lineage_record(
            {
                "schema_version": 1,
                "record_id": checker.LINEAGE_RECORD_ID,
                "sequence": 1,
                "recorded_at": "2026-09-02T12:00:01+00:00",
                "previous_record_digest": None,
                "source": "existing-accepted-state",
                "transaction_id": None,
                "candidate_commit": None,
                "accepted_state_before_digest": None,
                "accepted_state_after_digest": accepted_digest,
                "accepted_manifest_before_digest": None,
                "accepted_manifest_after_digest": accepted["manifest_digest"],
                "policy_generation_before": None,
                "policy_generation_after": manifest["policy_generation"],
                "accepted_state_after": accepted,
                "manifest_after": manifest,
                "relation": "baseline",
                "changes": [],
                "decision": "baseline-existing-state",
                "owner_acknowledgement": {
                    "required": True,
                    "method": "local-interactive-lineage-baseline",
                },
                "authorization_digest": None,
            }
        )
        lineage_digest = checker.lineage_record_digest(lineage)
        integrity = checker.validate_integrity_state(
            {
                "schema_version": 1,
                "state_id": checker.INTEGRITY_STATE_ID,
                "recorded_at": "2026-09-02T12:00:02+00:00",
                "record_source": "baseline-existing-state",
                "candidate_commit": self.commit,
                "trust_manifest": manifest,
                "trust_manifest_digest": checker.manifest_digest(manifest),
                "inventory": inventory,
                "inventory_digest": checker.inventory_digest(inventory),
                "accepted_state_digest_at_recording": accepted_digest,
                "lineage_head_record_digest_at_recording": lineage_digest,
            }
        )
        description = checker.describe_key(self.public_key)
        provenance = checker.validate_provenance_root(
            {
                "schema_version": 1,
                "root_id": checker.PROVENANCE_ROOT_ID,
                "established_at": "2026-09-02T12:00:03+00:00",
                "root_source": "owner-pinned-local-key",
                "algorithm": "openpgp",
                "primary_fingerprint": description["primary_fingerprint"],
                "signing_fingerprints": description["signing_fingerprints"],
                "public_key_base64": base64.b64encode(description["public_key"]).decode("ascii"),
                "public_key_sha256": checker.sha256_bytes(description["public_key"]),
                "baseline_commit": self.commit,
                "baseline_integrity_state_digest": checker.integrity_state_digest(integrity),
                "history_before_baseline": "unverified",
            }
        )
        self.accepted = accepted
        self.integrity = integrity
        self.provenance = provenance
        self.lineage = lineage
        self.lineage_digest = lineage_digest

        trust = self.target / "var/lib/open-mmi/trust"
        trust.mkdir(parents=True, mode=0o700)
        write(trust / "accepted-owner-trust.v1.json", json.dumps(accepted, indent=2) + "\n", 0o600)
        write(trust / "installed-release-integrity.v1.json", json.dumps(integrity, indent=2) + "\n", 0o600)
        write(trust / "release-signer-root.v1.json", json.dumps(provenance, indent=2) + "\n", 0o600)
        lineage_root = trust / "transition-lineage.v1.d"
        lineage_root.mkdir(mode=0o700)
        write(
            lineage_root / f"00000001-{lineage_digest.split(':', 1)[1]}.json",
            checker.canonical_json(lineage),
            0o600,
        )
        for directory in (
            self.target / "opt/open-mmi",
            self.target / "opt/open-mmi/venv/lib",
            self.target / "opt/open-mmi/venv/lib/python3.12",
            self.target / "opt/open-mmi/venv/lib/python3.12/site-packages",
            self.target / "usr/bin",
            self.target / "etc/systemd/system",
            self.target / "etc/systemd/user",
        ):
            directory.chmod(0o755)

    def _run(self, *, signer: str | None = None, target: Path | None = None) -> tuple[int, dict]:
        result = subprocess.run(
            [
                str(CHECKER),
                "--target-root", str(target or self.target),
                "--expected-signer-fingerprint", signer or self.primary,
                "--expected-owner-uid", str(os.geteuid()),
                "--repository", str(self.repo),
                "--json",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return result.returncode, json.loads(result.stdout)

    def assert_failed(self, check_id: str) -> None:
        code, report = self._run()
        self.assertEqual(code, 1, report)
        item = next(item for item in report["checks"] if item["id"] == check_id)
        self.assertEqual(item["status"], "FAIL", report)

    def test_clean_external_fixture_passes_without_open_mmi_runtime(self):
        code, report = self._run()
        self.assertEqual(code, 0, report)
        self.assertEqual(report["overall_status"], "PASS")
        self.assertNotIn("CAN challenge", json.dumps(report["checks"]))

    def test_altered_manifest_is_detected(self):
        path = self.target / "opt/open-mmi/venv/lib/python3.12/site-packages/open_mmi_trust/data/trust-manifest.v1.json"
        path.write_text(path.read_text() + "\n", encoding="utf-8")
        self.assert_failed("release.runtime-inventory")

    def test_altered_integrity_state_is_detected(self):
        path = self.target / "var/lib/open-mmi/trust/installed-release-integrity.v1.json"
        payload = json.loads(path.read_text())
        payload["inventory_digest"] = "sha256:" + "0" * 64
        path.write_text(json.dumps(payload, indent=2) + "\n")
        self.assert_failed("release.integrity-state")

    def test_lineage_break_is_detected(self):
        path = next((self.target / "var/lib/open-mmi/trust/transition-lineage.v1.d").iterdir())
        payload = json.loads(path.read_text())
        payload["recorded_at"] = "2026-09-02T12:00:09+00:00"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.assert_failed("owner.transition-lineage")

    def test_altered_opt_file_is_detected(self):
        (self.target / "opt/open-mmi/README.md").write_text("tampered\n", encoding="utf-8")
        self.assert_failed("release.runtime-inventory")

    def test_altered_site_package_file_is_detected(self):
        path = self.target / "opt/open-mmi/venv/lib/python3.12/site-packages/open_mmi_trust/vehicle_identity.py"
        path.write_text("tampered = True\n", encoding="utf-8")
        self.assert_failed("release.runtime-inventory")

    def test_replaced_updater_unit_is_detected(self):
        path = self.target / "etc/systemd/system/open-mmi-update-coordinator.service"
        path.write_text("[Service]\nExecStart=/bin/true\n", encoding="utf-8")
        self.assert_failed("release.privileged-units")

    def test_can_udev_regression_is_detected(self):
        path = self.target / "etc/udev/rules.d/80-canbus.rules"
        source = path.read_text(encoding="utf-8")
        self.assertIn("listen-only on", source)

        path.write_text(
            source.replace(" listen-only on", ""),
            encoding="utf-8",
        )

        self.assert_failed("capability.static-enforcement")

    def test_can_daemon_capability_regression_is_detected(self):
        path = self.target / "etc/systemd/user/canbusd.service"
        text = path.read_text(encoding="utf-8")
        text = text.replace(
            "CapabilityBoundingSet=\n",
            "CapabilityBoundingSet=CAP_NET_ADMIN\n",
        )
        path.write_text(text, encoding="utf-8")
        self.assert_failed("capability.static-enforcement")

    def test_wrong_external_signer_is_detected(self):
        code, report = self._run(signer=self.other_primary)
        self.assertEqual(code, 1, report)
        item = next(item for item in report["checks"] if item["id"] == "release.signer-root")
        self.assertEqual(item["status"], "FAIL")

    def test_fake_inspector_pass_cannot_mask_tampering(self):
        write(
            self.target / "var/lib/open-mmi/trust/fake-inspector-output.json",
            '{"overall_status":"PASS","checks":[]}\n',
            0o600,
        )
        (self.target / "opt/open-mmi/README.md").write_text("tampered\n", encoding="utf-8")
        code, report = self._run()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["overall_status"], "FAIL")

    def test_checker_source_imports_no_open_mmi_runtime_package(self):
        tree = ast.parse(CHECKER.read_text(encoding="utf-8"))
        forbidden = {"open_mmi_trust", "open_mmi_telemetry", "ui", "canbusd", "powerd", "actions", "bindings", "vehicles"}
        observed = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                observed.extend(alias.name for alias in node.names if alias.name.split(".", 1)[0] in forbidden)
            elif isinstance(node, ast.ImportFrom) and node.module and node.module.split(".", 1)[0] in forbidden:
                observed.append(node.module)
        self.assertEqual(observed, [])
        self.assertNotIn("inspect_system", CHECKER.read_text(encoding="utf-8"))

    def test_missing_evidence_is_unverified_not_pass(self):
        empty = self.base / "empty-target"
        empty.mkdir()
        code, report = self._run(target=empty)
        self.assertEqual(code, 2, report)
        self.assertEqual(report["overall_status"], "UNVERIFIED")
        self.assertNotIn("FAIL", {item["status"] for item in report["checks"]})

    def test_missing_lineage_dependency_is_unverified_not_fail(self):
        shutil.rmtree(self.target / "var/lib/open-mmi/trust/transition-lineage.v1.d")
        code, report = self._run()
        self.assertEqual(code, 2, report)
        self.assertEqual(report["overall_status"], "UNVERIFIED")
        integrity = next(item for item in report["checks"] if item["id"] == "release.integrity-state")
        self.assertEqual(integrity["status"], "UNVERIFIED")

    def test_integrity_inventory_omission_cannot_hide_signed_file(self):
        path = self.target / "var/lib/open-mmi/trust/installed-release-integrity.v1.json"
        payload = copy.deepcopy(self.integrity)
        payload["inventory"] = [item for item in payload["inventory"] if item["path"] != "README.md"]
        payload["inventory_digest"] = checker.inventory_digest(payload["inventory"])
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        code, report = self._run()
        self.assertEqual(code, 1, report)
        provenance = next(item for item in report["checks"] if item["id"] == "release.provenance")
        self.assertEqual(provenance["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
