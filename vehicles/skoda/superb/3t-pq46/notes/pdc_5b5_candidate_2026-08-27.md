# Superb 3T `0x5B5` rear-PDC candidate — 2026-08-27

This note preserves a directly observed rear parking-distance-control pattern without
promoting an uncalibrated distance decoder into the maintained runtime profile.

## Controlled observation

During the controlled 2012 RHD Superb capture the car was reversed approximately square-on
towards another car until the rear parking aid reached a continuous tone.  The action window
was recorded as `extra_rear_pdc_test`; `0x5B5` changed progressively during the approach.

Ignoring bytes 0..1 (which change independently of the sensor payload) and byte 7, bytes
2..6 form a 40-bit little-endian value that separates cleanly into four 10-bit channels:

```text
packed = little_endian(bytes[2:7])
ch0 = (packed >>  0) & 0x3ff
ch1 = (packed >> 10) & 0x3ff
ch2 = (packed >> 20) & 0x3ff
ch3 = (packed >> 30) & 0x3ff
```

Representative capture values:

```text
before approach:  930A20C0F081070F ->  32,  48,  31,  30
near constant tone: 4B006AF08146190F -> 106, 124, 104, 101
```

All four channels rose as the vehicle approached the obstacle and then stabilised.  That is
strong evidence that the four 10-bit values are rear-PDC proximity channels rather than
unrelated changing bytes.

## Physical-order candidate

The vehicle approached the other car approximately square-on.  If the four channels are in
physical left-to-right order, the final values become:

```text
rear outer-left   106
rear inner-left   124
rear inner-right  104
rear outer-right  101
```

That ordering is consistent with the observation: on each side the candidate inner sensor
reported at least as close as its adjacent outer sensor, with the left inner channel clearly
closest.  This is useful corroboration but is **not** enough to make the left-to-right labels
a maintained runtime contract.  A one-sensor-at-a-time target test is still required.

## Why this is not yet `parking.distance.*`

The canonical parking-distance statuses are expressed in centimetres.  This capture did not
record a tape-measured bumper-to-obstacle distance, so the conversion from the 10-bit
proximity code to centimetres is not independently known.  The Superb handbook describes
rear parking-aid warning out to roughly 160 cm and continuous tone near 30 cm, but those
bounds alone are not enough to assert a CAN scaling formula.

The existing canonical registry also exposes left/right parking distance rather than four
individually named rear sensor positions.  Do not silently collapse or relabel the four raw
channels until their physical order and distance conversion are verified.

## Live test probe

The repository contains a diagnostic-only decoder:

```bash
candump -L can0,5B5:7FF | python3 tools/pq_pdc_probe.py --changes-only
```

For the provisional left-to-right labels inferred above:

```bash
candump -L can0,5B5:7FF | python3 tools/pq_pdc_probe.py --changes-only --candidate-labels
```

The question marks in those labels are deliberate.  The tool does not feed `canbusd`, does
not publish canonical statuses and therefore carries no runtime compatibility authority.

## Fast verification plan

1. Hold a flat target close to only one rear sensor at a time and confirm which channel rises.
2. Repeat for all four sensors to establish physical left-to-right ordering.
3. For an inner sensor, record stable values at measured distances such as 150, 100, 60, 40
   and 30 cm; repeat several points while moving both towards and away from the target.
4. Check whether the raw-to-centimetre relationship is linear and whether the outer sensors
   use the same conversion/range.
5. Only after that calibration, define the appropriate canonical `parking.distance.*`
   mapping and replay fixtures.

The original raw capture remains the evidence source; no VIN is stored in this note.
