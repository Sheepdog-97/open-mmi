# Škoda Superb 3T infotainment CAN validation — 2026-08-27

**Status:** Reviewed controlled hardware-capture evidence for replay-qualified candidate
**Vehicle:** Škoda Superb II / 3T, model year 2012, RHD, 2.0 TDI engine code CFG
**Capture point:** radio infotainment CAN
**Bitrate:** 100 kbit/s
**Method:** passive receive-only guided CAN capture

## Identity / presence

CAN ID `0x65F` repeats a three-frame identity sequence. Reassembling its ASCII
segments reproduced the operator-supplied chassis/VIN exactly. The full VIN is
intentionally omitted from the repository. The same frame structure was also
observed on the separately captured 2010 Passat, supporting `0x65F` as a common
VAG infotainment identity/presence source.

The capture did not exercise a full sleep/wake cycle, so the profile retains the
common 6000 ms absence timeout as experimental rather than claiming it as
hardware-qualified on this vehicle.

## Steering-wheel controls (`0x5C1`)

The Superb wheel uses thumbwheels, and direction byte values are shared between
the two wheels. Matching byte 2 alone is therefore unsafe.

| Control | Frame predicate | Canonical event |
| --- | --- | --- |
| Volume thumbwheel up | byte0 `0x13` and byte2 `0x01` | `volume_up` |
| Volume thumbwheel down | byte0 `0x13` and byte2 `0x0F` | `volume_down` |
| MFD thumbwheel up | byte0 `0x14` and byte2 `0x01` | `arrow_up` |
| MFD thumbwheel down | byte0 `0x14` and byte2 `0x0F` | `arrow_down` |
| MFD centre / OK | byte0 `0x28` | `confirm` |
| MFD back | byte0 `0x29` | `back` |
| Mute | byte0 `0x2A` | `mute_toggle` |
| Telephone | byte0 `0x1A` | `telephone` |
| Next / right media control | byte0 `0x02` | `next_track` |
| Previous / left media control | byte0 `0x03` | `previous_track` |

The guided script's original `PLAY/PAUSE` prompt corresponded physically to the
MFD centre/OK control on this wheel. `0x28` must therefore **not** be mapped to
`play_pause`.

## Cross-vehicle mappings confirmed

The capture agrees with the Leon/PQ35 mappings for:

- `0x470` door bits: FR `0x01`, FL `0x02`, RL `0x04`, RR `0x08`
- `0x470` boot field: `(byte1 & 0x60) == 0x60`
- `0x351 byte0 == 0x02`: reverse
- `0x351 bytes1..2 little-endian * 0.005`: road speed; crawl-speed motion was observed despite the scripted road-speed step being skipped
- `0x621 byte0 & 0x20`: handbrake
- `0x3C3`: sign-magnitude steering angle, scale `0.04375`
- `0x531`: base lighting-mode enum
- `0x531 byte0 & 0x08`: independent front-fog bit
- `0x531 byte1`: brake / indicators / hazards
- `0x3E1 byte4 * 0.4`: blower load
- `0x35B byte3 * 0.75 - 49`: coolant temperature
- `0x35B bytes1..2 little-endian * 0.25`: engine speed
- `0x527 bytes5/6 * 0.5 - 50`: outside temperature fields
- `0x571 byte0 * 0.05 + 5.1`: supply voltage
- `0x3E1`: rear-window-heater request and compressor state
- `0x3E3 byte4 & 0x80`: recirculation
- `0x635`: dimmer mirror
- `0x65D`: lower 20-bit little-endian odometer value

Entered truth values strongly supported the common engine/electrical decoders:
idle was approximately 780 rpm, coolant was 18–19 °C, outside temperature was
19 °C, and ignition-off supply voltage was 11.9 V.


## Corrected bulb-warning observation

The guided `bulb_out_dash_state` metadata entry is blank because Enter was pressed
instead of `Y`. The operator subsequently corrected the physical truth: the Superb
bulb-warning lamp **was on** during the capture. Around the guided observation,
`0x470` carried byte 4 as `0x30`; masking `0x10` therefore decodes the warning as
on while leaving the unrelated `0x20` bit independent. This supersedes the blank
guided metadata entry and independently corroborates the Leon positive state and
Passat negative-state masking result.

## Incidental crawl-speed validation

The scripted road-speed step was skipped, but the later rear-PDC exercise required
the vehicle to creep backwards and forwards. During those movements `0x351`
bytes 1..2 produced non-zero values that decode with the maintained Leon scale to
approximately 0.3–2.5 km/h. A captured forward frame `0x351#0048010000000000`
decodes to 1.64 km/h (about 1.02 mph), matching the live Open MMI observation of
roughly 1 mph while crawling. Reverse-motion samples carried byte 0 `0x02` at the
same time as plausible low-speed values.

This is positive real-car evidence for the field and scale at crawl speed. Higher
speeds were not exercised and remain outside the current claim.

## Fuel-level candidate observation

Open MMI was running the Leon decoder during the Superb session and displayed a
sane-looking fuel quantity. Independently, `0x621` byte 3 lower seven bits ranged
from 26 to 30 throughout the capture, while bit `0x80` never set. No exact
independent fuel quantity was recorded, so `fuel.level_l` and the low-fuel bit are
kept as structured non-runtime candidates rather than promoted capabilities.

## Deliberately not exposed

- **Bonnet:** not testable; the vehicle's bonnet-switch wiring had been cut. The common `0x470 byte1 & 0x10` mapping is retained as a structured cross-profile candidate.
- **Front windscreen heater:** feature unavailable on the captured vehicle; retained as a structured cross-profile candidate for equipped variants.
- **Fuel level / reserve warning:** plausible shared `0x621` mapping, but no exact independent quantity or positive reserve-warning truth; retained as structured non-runtime candidates.
- **Fuel range:** no usable truth value; prior Leon/Passat candidates disagree.
- **AUTO lights:** `0x531 byte1 & 0x20` is a strong candidate but remains research.
- **Horn:** `0x2C1 byte0 = 0x80` appeared only at horn presses; research only.
- **Interior lock:** `0x523 byte2 = 0x10` correlated with the lock button; likely a
  momentary request rather than persistent lock state.
- **Rear PDC:** `0x5B5` changed progressively while approaching constant tone. Bytes 2..6 decode cleanly as four packed little-endian 10-bit proximity channels; physical ordering and centimetre scaling remain unqualified. See `pdc_5b5_candidate_2026-08-27.md` and `tools/pq_pdc_probe.py`.
