# SEAT Leon 1P (PQ35) — Unpromoted Manual CAN Observations

**Confidence:** Medium  
**Status:** Research only; not a maintained runtime specification  
**Vehicle context:** UK right-hand-drive SEAT Leon 1P / VAG PQ35  
**Byte numbering:** Zero-based (`byte 0` is the first payload byte)

## Scope

This file preserves manual observations that are not documented more
authoritatively elsewhere in the repository.

It intentionally excludes signals already covered in greater detail by the
maintained vehicle profile, replay fixtures, or later research notes, including
reverse, handbrake, door state, lighting mode, dimmer percentage, bulb warning,
steering-angle decoding, HVAC decoding, and TPMS.

Values below are exact observed byte values unless a mask is explicitly shown.
They should not be promoted to stable runtime behaviour without controlled
captures and replay evidence.

---

## CAN ID `0x181` — Window-switch commands

Observed from the front-right window-switch assembly.

### Byte 0 — front windows

| Byte 0 value | Observation | Notes |
|---:|---|---|
| `0x01` | Front-right window up, single press | |
| `0x02` | Front-right window up, double press | |
| `0x04` | Front-right window down, single press | |
| `0x08` | Front-right window down, double press | |
| `0x10` | Front-left window up, single press | |
| `0x20` | Front-left window up, double press | |
| `0x40` | Front-left window down, single press | Tentative |
| `0x80` | Front-left window down, double press | |

### Byte 1 — rear windows

| Byte 1 value | Observation | Notes |
|---:|---|---|
| `0x01` | Rear-left window up, single press | |
| `0x04` | Rear-left window down, single press | |
| `0x08` | Rear-left window down, double press | |
| `0x10` | Rear-right window up, single press | |
| `0x40` | Rear-right window down, single press | |
| `0x80` | Rear-right window down, double press | |

No distinct rear-window up double-press values were recorded.

---

## CAN ID `0x2C1` — Horn, washers and wipers

### Byte 1 — stalk command values

| Byte 1 value | Observation |
|---:|---|
| `0x00` | No input |
| `0x01` | Single front wipe |
| `0x02` | Front intermittent-wiper mode |
| `0x04` | Front wiper stage 1 |
| `0x08` | Front wiper stage 2 |
| `0x20` | Stalk release |
| `0x30` | Front washer |
| `0x40` | Rear wiper |
| `0x80` | Rear washer |

These are preserved as exact observed byte values. Whether some values are
composable bitfields rather than exclusive states has not been confirmed.

### Byte 2 — front intermittent-wiper speed

| Byte 2 value | Observation |
|---:|---|
| `0x81` | Intermittent speed 1 |
| `0x85` | Intermittent speed 2 |
| `0x89` | Intermittent speed 3 |
| `0x8D` | Intermittent speed 4 |

The original notes duplicated “speed 3” for `0x81` and `0x89`. The table above
corrects that typo using the regular `+0x04` progression. The corrected labels
remain medium confidence until verified against another controlled capture.

### Horn frame fragment

The horn was observed with:

| Byte | Value |
|---:|---:|
| `2` | `0x80` |
| `3` | `0x00` |
| `4` | `0x06` |

Other bytes were not isolated in the original observation.

---

## CAN ID `0x601` — Mirror controls

### Byte 0 — mode bits

| Mask | Observation |
|---:|---|
| `0x80` | Mirror fold |
| `0x40` | Heated mirrors on |
| `0x20` | Right mirror selected |
| `0x10` | Left mirror selected |

### Byte 0 — low-nibble adjustment command

Interpret the low nibble with `byte0 & 0x0F`.

| Low-nibble value | Observation |
|---:|---|
| `0x01` | Adjust up |
| `0x02` | Adjust down |
| `0x04` | Adjust left |
| `0x08` | Adjust right |
| `0x00` | Idle or transition between adjustment modes |

The selection/mode bits and adjustment nibble may appear together in the same
byte.

---

## CAN ID `0x5C1` — Additional cluster-stalk states

The maintained profile documents the confirmed steering-wheel media and
navigation commands. These two additional values were present only in the
original manual notes:

| Byte | Value | Observation |
|---:|---:|---|
| `0` | `0x00` | Control at rest |
| `0` | `0x28` | Cluster-stalk OK/reset |

The exact distinction between OK, acknowledge, and reset behaviour needs a
controlled capture before promotion.

---

## CAN ID `0x65F` — Cyclic VIN broadcast

**Confidence:** High

Every observed `0x65F` frame is one segment of the vehicle's 17-character VIN.
The message cycles through three segment indexes:

| Segment index | Payload layout | VIN characters |
|---:|---|---|
| `0x00` | bytes 1–4 are `0x00`; bytes 5–7 are ASCII | characters 1–3 |
| `0x01` | bytes 1–7 are ASCII | characters 4–10 |
| `0x02` | bytes 1–7 are ASCII | characters 11–17 |

Reconstruct the VIN as:

```text
VIN =
  segment 0, bytes 5–7
  + segment 1, bytes 1–7
  + segment 2, bytes 1–7
```

The three TPMS research captures contained 113 `0x65F` frames in total. They
contained exactly three unique payloads, repeated in an uninterrupted
`0x01 → 0x02 → 0x00` wire sequence.

Observed timing:

```text
approximately 200 ms between VIN segments
approximately 600 ms for one complete 17-character VIN cycle
```

The exact captured VIN is intentionally omitted from this public research note.
Using `0x65F` as a presence heartbeat is valid because the VIN broadcast repeats
continuously, but the frame's primary observed content is VIN data rather than a
generic presence message.

---

## CAN ID `0x3C3` — Unresolved rolling fields

The maintained profile already documents steering-angle decoding from bytes 0
and 1.

The original manual captures also showed:

| Byte | Observation |
|---:|---|
| `5` | Changes rapidly; possible rolling counter or protected-field input |
| `7` | Changes rapidly; possible checksum or protected-field output |

Their exact roles and relationship have not been isolated.

---

## CAN ID `0x531` — Unresolved bit in combined telltale values

The maintained profile already documents the confirmed brake, indicator, and
hazard masks. The original captures preserve these exact combined values:

| Byte 1 value | Observed combination |
|---:|---|
| `0x11` | Left indicator |
| `0x12` | Right indicator |
| `0x1B` | Hazards |
| `0x51` | Brake plus left indicator |
| `0x52` | Brake plus right indicator |
| `0x5B` | Brake plus hazards |

Known masks explain `0x01`, `0x02`, `0x08`, and `0x40`. These captures also
contain `0x10`, whose meaning remains unknown. Preserving the exact combined
values may help identify that bit in later testing.

---

## Promotion criteria

Promote an observation from this file only after:

1. a controlled before/after capture isolates the relevant byte or mask;
2. the result repeats across more than one transition or ignition cycle;
3. unrelated vehicle state changes are excluded;
4. a replay fixture covers both active and inactive states where applicable;
5. the maintained profile and research note are updated together.
