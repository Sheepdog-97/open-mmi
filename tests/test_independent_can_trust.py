from __future__ import annotations

import ast
import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = (
    ROOT
    / "independent_checker"
    / "open_mmi_can_trust_test.py"
)

SPEC = importlib.util.spec_from_file_location(
    "independent_can_trust_under_test",
    CHECKER_PATH,
)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class IndependentCanTrustTests(unittest.TestCase):
    def test_checker_imports_no_open_mmi_runtime(self) -> None:
        tree = ast.parse(
            CHECKER_PATH.read_text(encoding="utf-8")
        )
        forbidden = {
            "open_mmi_trust",
            "open_mmi_telemetry",
            "ui",
            "canbusd",
            "powerd",
            "actions",
            "bindings",
            "vehicles",
        }
        observed = []

        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)

            observed.extend(
                module
                for module in modules
                if module.split(".", 1)[0] in forbidden
            )

        self.assertEqual(observed, [])

    def test_explicit_listen_only_passes(self) -> None:
        result = checker.production_listen_only_check(
            [
                {
                    "ifname": "can0",
                    "linkinfo": {
                        "info_kind": "can",
                        "info_data": {
                            "ctrlmode": [
                                "LISTEN-ONLY",
                                "BERR-REPORTING",
                            ]
                        },
                    },
                }
            ],
            "can0",
        )

        self.assertEqual(result["status"], checker.PASS)

    def test_explicit_non_listen_only_fails(self) -> None:
        result = checker.production_listen_only_check(
            [
                {
                    "ifname": "can0",
                    "linkinfo": {
                        "info_kind": "can",
                        "info_data": {
                            "ctrlmode": [
                                "BERR-REPORTING",
                            ]
                        },
                    },
                }
            ],
            "can0",
        )

        self.assertEqual(result["status"], checker.FAIL)

    def test_missing_ctrlmode_is_unverified(self) -> None:
        result = checker.production_listen_only_check(
            [
                {
                    "ifname": "can0",
                    "linkinfo": {
                        "info_kind": "can",
                        "info_data": {},
                    },
                }
            ],
            "can0",
        )

        self.assertEqual(
            result["status"],
            checker.UNVERIFIED,
        )

    def test_non_can_production_interface_fails(self) -> None:
        result = checker.production_listen_only_check(
            [
                {
                    "ifname": "can0",
                    "linkinfo": {
                        "info_kind": "vcan",
                        "info_data": {},
                    },
                }
            ],
            "can0",
        )

        self.assertEqual(result["status"], checker.FAIL)

    def test_challenge_profile_is_receive_side_only(self) -> None:
        profile = checker.challenge_profile(
            "vcan99",
            0x5A5,
        )

        self.assertEqual(profile["rules"], [])
        self.assertEqual(profile["presence"], [])
        self.assertEqual(
            profile["status"][0]["path"],
            "engine.speed_raw",
        )
        self.assertEqual(
            profile["can_buses"]["trust-challenge"][
                "provisioning"
            ],
            "manual",
        )

    def test_exact_fresh_observation_passes(self) -> None:
        challenge = {
            "schema_version": 1,
            "can_id": 0x5A5,
            "values": list(range(16)),
            "digest": "sha256:" + "1" * 64,
        }
        frames = [
            (0x5A5, bytes([value]))
            for value in challenge["values"]
        ]

        result = checker.classify_challenge_observation(
            challenge,
            challenge["values"],
            frames,
        )

        self.assertEqual(result["status"], checker.PASS)

    def test_stale_or_wrong_status_cannot_pass(self) -> None:
        challenge = {
            "schema_version": 1,
            "can_id": 0x5A5,
            "values": list(range(16)),
            "digest": "sha256:" + "2" * 64,
        }
        frames = [
            (0x5A5, bytes([value]))
            for value in challenge["values"]
        ]

        observed = list(challenge["values"])
        observed[-1] = 200

        result = checker.classify_challenge_observation(
            challenge,
            observed,
            frames,
        )

        self.assertEqual(
            result["status"],
            checker.UNVERIFIED,
        )

    def test_additional_bus_traffic_cannot_pass(self) -> None:
        challenge = {
            "schema_version": 1,
            "can_id": 0x5A5,
            "values": list(range(16)),
            "digest": "sha256:" + "3" * 64,
        }
        frames = [
            (0x5A5, bytes([value]))
            for value in challenge["values"]
        ]
        frames.append((0x123, b"\x00"))

        result = checker.classify_challenge_observation(
            challenge,
            challenge["values"],
            frames,
        )

        self.assertEqual(
            result["status"],
            checker.UNVERIFIED,
        )

    def test_challenge_contains_sixteen_unique_values(self) -> None:
        challenge = checker.make_challenge()

        self.assertEqual(
            len(challenge["values"]),
            checker.CHALLENGE_STEPS,
        )
        self.assertEqual(
            len(set(challenge["values"])),
            checker.CHALLENGE_STEPS,
        )
        self.assertRegex(
            challenge["digest"],
            r"^sha256:[0-9a-f]{64}$",
        )


if __name__ == "__main__":
    unittest.main()
