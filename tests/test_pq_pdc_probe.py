import unittest

from tools.pq_pdc_probe import decode_5b5_payload, format_sample, parse_candump_line


class PqPdcProbeTests(unittest.TestCase):
    def test_decodes_superb_baseline_frame_into_four_10bit_channels(self):
        payload = bytes.fromhex("930A20C0F081070F")
        self.assertEqual(decode_5b5_payload(payload), (32, 48, 31, 30))

    def test_decodes_superb_constant_tone_frame(self):
        payload = bytes.fromhex("4B006AF08146190F")
        self.assertEqual(decode_5b5_payload(payload), (106, 124, 104, 101))

    def test_rolling_prefix_does_not_change_decoded_channels(self):
        first = bytes.fromhex("450E6AF08146190F")
        second = bytes.fromhex("4B006AF08146190F")
        self.assertEqual(decode_5b5_payload(first), decode_5b5_payload(second))

    def test_parse_candump_l_line(self):
        sample = parse_candump_line("(1787854431.007949) can0 5B5#4B006AF08146190F")
        self.assertIsNotNone(sample)
        assert sample is not None
        self.assertEqual(sample.timestamp, "1787854431.007949")
        self.assertEqual(sample.channels, (106, 124, 104, 101))

    def test_ignores_other_ids_and_short_frames(self):
        self.assertIsNone(parse_candump_line("(1.0) can0 351#0000000000000000"))
        self.assertIsNone(parse_candump_line("(1.0) can0 5B5#000102"))

    def test_candidate_labels_are_explicitly_provisional(self):
        sample = parse_candump_line("can0 5B5#4B006AF08146190F")
        assert sample is not None
        rendered = format_sample(sample, candidate_labels=True)
        self.assertIn("rear_outer_left?= 106", rendered)
        self.assertIn("rear_inner_left?= 124", rendered)
        self.assertIn("rear_inner_right?= 104", rendered)
        self.assertIn("rear_outer_right?= 101", rendered)


if __name__ == "__main__":
    unittest.main()
