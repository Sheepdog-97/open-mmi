import unittest

from canbusd.status_rules import evaluate_status_rules, parse_status_rules


class StatusUintLeDecoderTests(unittest.TestCase):
    def test_u24le_odometer_with_raw_path(self):
        grouped = parse_status_rules([
            {
                "id": "0x65D",
                "type": "u24le",
                "start_byte": 1,
                "path": "vehicle.odometer_km",
                "mask": "0x0FFFFF",
                "scale": 1.0,
                "round": 0,
                "raw_path": "vehicle.odometer_raw",
            }
        ])

        update = evaluate_status_rules(
            grouped[0x65D],
            bytes([0x21, 0x7E, 0xDA, 0xF4, 0x00, 0x30, 0x37, 0x14]),
            8,
        )

        self.assertEqual(update["vehicle"]["odometer_raw"], 16046718)
        self.assertEqual(update["vehicle"]["odometer_km"], 318078.0)

    def test_u24le_ignores_short_frames(self):
        grouped = parse_status_rules([
            {
                "id": "0x65D",
                "type": "u24le",
                "start_byte": 1,
                "path": "vehicle.odometer_km",
            }
        ])

        update = evaluate_status_rules(
            grouped[0x65D],
            bytes([0x21, 0x7E, 0xDA]),
            3,
        )

        self.assertEqual(update, {})

    def test_uint_le_with_configurable_length(self):
        grouped = parse_status_rules([
            {
                "id": "0x655",
                "type": "uint_le",
                "start_byte": 3,
                "length": 2,
                "path": "fuel.range_km",
                "scale": 0.01,
                "round": 1,
                "raw_path": "fuel.range_raw",
            }
        ])

        update = evaluate_status_rules(
            grouped[0x655],
            bytes([0x75, 0x00, 0x60, 0x3F, 0x1C, 0x00, 0x00, 0x40]),
            8,
        )

        self.assertEqual(update["fuel"]["range_raw"], 7231)
        self.assertEqual(update["fuel"]["range_km"], 72.3)

    def test_uint_le_rejects_invalid_length(self):
        grouped = parse_status_rules([
            {
                "id": "0x65D",
                "type": "uint_le",
                "start_byte": 1,
                "length": 0,
                "path": "vehicle.odometer_km",
            }
        ])

        update = evaluate_status_rules(grouped[0x65D], bytes([0] * 8), 8)
        self.assertEqual(update, {})


if __name__ == "__main__":
    unittest.main()
