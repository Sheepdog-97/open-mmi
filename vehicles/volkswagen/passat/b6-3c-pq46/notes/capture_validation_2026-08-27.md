# Volkswagen Passat B6 infotainment CAN validation — 2026-08-27

**Status:** Reviewed controlled hardware-capture evidence for replay-qualified candidate
**Vehicle:** Volkswagen Passat B6 / 3C, model year 2010, RHD; operator engine entry `CBA`
**Capture point:** radio infotainment CAN
**Bitrate:** 100 kbit/s
**Method:** passive receive-only guided CAN capture

## Identity / presence

CAN ID `0x65F` repeats a three-frame identity sequence. Reassembling its ASCII
segments reproduced the operator-supplied chassis/VIN exactly and contained the
expected Volkswagen/3C identity markers. The full VIN is intentionally omitted
from the repository. The same three-frame structure was observed on the separately
captured Škoda Superb 3T.

The capture did not exercise a full sleep/wake cycle, so the common 6000 ms
absence timeout remains experimental rather than hardware-qualified on this car.

## Steering-wheel controls (`0x5C1`)

| Physical control | byte0 | Canonical event |
| --- | ---: | --- |
| Volume up | `0x06` | `volume_up` |
| Volume down | `0x07` | `volume_down` |
| Next | `0x02` | `next_track` |
| Previous | `0x03` | `previous_track` |
| Right | `0x0A` | `arrow_right` |
| Left | `0x09` | `arrow_left` |
| Telephone | `0x1A` | `telephone` |

The captured wheel has a telephone symbol and no mute button. The guided
`PLAY/PAUSE` prompt was physically answered with the telephone control and emitted
`0x1A`; it must therefore **not** be mapped to `play_pause`. The mute prompt
produced no `0x5C1` frame and no mute event is claimed.

## Body and lighting

The capture directly agreed with the common PQ mappings for:

- `0x470 byte1`: FR `0x01`, FL `0x02`, RL `0x04`, RR `0x08`, bonnet `0x10`
- boot field `(byte1 & 0x60) == 0x60`; the boot result appeared as `0x61` while a front door was also open
- `0x351 byte0 == 0x02`: reverse
- `0x621 byte0 & 0x20`: handbrake
- all twelve maintained `0x531 byte0` base lighting-mode enum values
- `0x531 byte1`: brake, left/right indicators and hazards
- `0x470 byte2` and `0x635 byte0`: dimmer state

The bulb-warning truth entry was **off**. `0x470 byte4` was ordinarily zero but
reached `0x20` with rear fog while the bulb warning remained off. That directly
supports masked decoding of bit `0x10` rather than exact-byte equality. A bulb-on
state was not generated on this vehicle; the true-bit interpretation is also
supported by the independent Leon capture.

Front fog was not exercised on the Passat. The profile masks bit `0x08` from the
base `0x531 byte0` lighting enum because the Superb capture independently showed
that bit to be front fog; the Passat profile does not expose a front-fog status
until it is tested directly.

## Steering angle

`0x3C3` matched the common sign-magnitude 15-bit little-endian decoder with scale
`0.04375`. Centre and approximately 90° left were clean. The guided right-angle
procedure was operator-error contaminated and is explicitly excluded as evidence;
the decoder is retained because the clean Passat observations and independent
Leon/Superb captures agree structurally.

## Engine, climate and electrical observations

The capture supported the common PQ decoders for:

- `0x3E1 byte4 * 0.4`: blower load
- `0x35B byte3 * 0.75 - 49`: coolant temperature
- `0x35B bytes1..2 little-endian * 0.25`: engine speed
- `0x527 bytes5/6 * 0.5 - 50`: outside-temperature fields
- `0x3E1`: rear-window-heater request and compressor state
- `0x3E3 byte4 & 0x80`: recirculation
- `0x571 byte0 * 0.05 + 5.1`: supply voltage
- `0x65D`: lower 20-bit little-endian odometer value

The cluster coolant gauge showed 90 °C while the raw CAN formula was around the
mid-70s °C during the capture; this is consistent with a buffered dashboard gauge
and is not treated as a decoder failure. Likewise the entered idle display value
was a coarse 850 rpm reference while raw CAN values clustered around 780–800 rpm.
The odometer's lower 20 bits decoded to 174684 km, closely matching the entered
108541 mi using the VAG conversion relationship seen across the three captures.


## Cross-profile candidates retained without runtime authority

The capture also provides useful evidence for several mappings that are not yet
claimed by the Passat runtime profile. They are recorded in
`notes/candidate_mappings.v1.json` as ready-to-verify rules:

- `0x621` byte 3 lower seven bits decoded to 18 or 19 litres in 6450 of 6452
  frames, with only two transient zero values. That is a plausible fuel quantity,
  but no independent litre truth was recorded. Bit `0x80` never set, so the
  low-fuel warning has only a negative local observation.
- `0x351` speed bytes decoded to zero in 6451 of 6452 frames while the Passat
  remained stationary. The lone non-zero value was sentinel-like `0xFF88`; this
  supports the zero state but does not validate the moving scale.
- front fog was not exercised locally, but the Leon and Superb independently
  identify `0x531 byte0 & 0x08` as the front-fog bit.
- the front-windscreen-heater bit remains an equipment-conditional lead from the
  related Leon profile because the captured Passat did not expose a testable
  control.

These candidates are deliberately excluded from `config.json`, replay capability
counts and runtime publication until their local verification steps are completed.

## Deliberately not exposed

- **Road speed:** operator skipped the moving test; stationary zero-state evidence is retained only as a structured non-runtime candidate.
- **Front windscreen heater:** feature unavailable on the captured vehicle; retained only as a structured non-runtime candidate for equipped variants.
- **Fuel range:** entered dashboard range did not agree with prior candidate fields; omitted.
- **Mute / play-pause:** not present as physical controls on this wheel.
- **Front fog:** base-mode masking retained; the shared bit is recorded as a structured non-runtime candidate, but no Passat front-fog status is claimed.
