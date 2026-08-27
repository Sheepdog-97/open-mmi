import unittest

from canbusd.status_rules import evaluate_status_rules, parse_status_rules


class StatusBitfieldDecoderTests(unittest.TestCase):
    def test_masked_equals_can_coexist_with_other_bits(self):
        grouped = parse_status_rules([
            {
                "id": "0x470",
                "byte": 1,
                "type": "bitfield",
                "path": "doors",
                "fields": {
                    "front_right": "0x01",
                    "front_left": "0x02",
                },
                "equals": {
                    "boot": {
                        "mask": "0x60",
                        "value": "0x60",
                    }
                },
                "any": "any_open",
                "raw": "raw",
            }
        ])

        update = evaluate_status_rules(
            grouped[0x470],
            bytes([0x00, 0x61]),
            2,
        )

        self.assertIs(update["doors"]["front_right"], True)
        self.assertIs(update["doors"]["front_left"], False)
        self.assertIs(update["doors"]["boot"], True)
        self.assertIs(update["doors"]["any_open"], True)
        self.assertEqual(update["doors"]["raw"], 0x61)

    def test_masked_equals_remains_false_for_partial_field(self):
        grouped = parse_status_rules([
            {
                "id": "0x470",
                "byte": 1,
                "type": "bitfield",
                "path": "doors",
                "equals": {
                    "boot": {
                        "mask": "0x60",
                        "value": "0x60",
                    }
                },
            }
        ])

        for raw in (0x00, 0x20, 0x40):
            update = evaluate_status_rules(grouped[0x470], bytes([0x00, raw]), 2)
            self.assertIs(update["doors"]["boot"], False)


if __name__ == "__main__":
    unittest.main()
