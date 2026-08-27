# TPMS warning research — 2026-07-29

## Status

**No maintained TPMS warning mapping is currently supported by these captures.**

Two candidates were investigated and rejected:

```text
0x555 byte 0 mask 0x10
0x621 byte 2 mask 0x40, active-low
```

Neither candidate follows the physical warning lamp through the recorded reset
transition. They must not be restored to the runtime profile without new,
independent evidence.

## Capture interpretation correction

`igonresettpms` is not a steady lamp-off capture. It records the physical
sequence:

```text
lamp on -> TPMS reset -> lamp off
```

This makes it the primary temporal reference. The separate `16.5.txt` and
`19deg.txt` recordings are independent lamp-off controls captured under different
vehicle temperatures and operating conditions.

## Rejected candidate: `0x555 byte 0 mask 0x10`

The initial comparison treated `0xF0` as warning-on and `0xE0` as warning-off:

```text
TPMLon:          byte 0 = 0xF0 in 23/23 0x555 frames
TPMLonigon:      byte 0 = 0xF0 in 31/31 0x555 frames
igonresettpms:   byte 0 = 0xE0 in 171/171 0x555 frames
```

However, `0x555` remains `E0 2B 80 00 64 00 00 00` throughout the entire
17.047-second `igonresettpms` capture, including both the lamp-on period before
reset and the lamp-off period afterwards. The bit therefore does not encode the
lamp state.

The independent lamp-off controls also contain both candidate values:

```text
16.5.txt:  555#E02B800064000000
19deg.txt: 555#F02B800065000000
```

The `0x10` difference correlates with other session conditions, including
coolant/temperature state, rather than TPMS warning state.

## Rejected candidate: `0x621 byte 2 mask 0x40`, active-low

The steady-state comparison initially found a perfect separation between the
warning-labelled captures and the independent lamp-off controls. The transition
capture disproves it.

All 170 `0x621` frames in `igonresettpms` retain byte 2 as `0x46`, so bit `0x40`
remains set while the physical lamp changes from on to off:

```text
20 AE 46 08 02
20 AE 46 09 02
24 AE 46 09 02
```

The observed changes are in byte 3 (`0x08 -> 0x09`) and byte 0 (`0x20 -> 0x24`).
Existing profile evidence associates these regions with fuel/handbrake or other
session state, not TPMS.

## Source evidence

| Capture | Physical interpretation | Frames | Duration | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `TPMLon` | lamp on | 494 | 2.336 s | `bed1d97eeda2c9b600da55618782873ac4501b2279598608ec149492f3cbbd1d` |
| `TPMLonigon` | lamp on, ignition on | 654 | 3.095 s | `0be86a4da3a741d605d592fb25107adb05a1048258ba85ca3d1e7d25e26ed224` |
| `igonresettpms` | lamp on -> reset -> lamp off | 3,584 | 17.047 s | `790febeca78aa7c0d985ba92cec72d994a35c761364cd02450c0516ad52e7781` |
| `16.5.txt` | lamp off control | 323 | not timestamped | `7d0f9ad0e4af663c3bc64732731203255520837faf664a202f15dfb354d4c435` |
| `19deg.txt` | lamp off control | 714 | not timestamped | `a1a55bbc79600a9878421c35ab25442aa175b02e587de707452fd6b8f1b929cd` |

All five recordings contain the same sparse 100 kbit/s radio/infotainment-side
traffic set used by the maintained profile research.

## Architecture implications

Vehicle-specific wiring diagrams show:

- the TPMS SET button is hard-wired to ABS/ESP control unit J104;
- J104 is connected to drivetrain CAN;
- the instrument cluster J285 receives gatewayed data over its dedicated cluster
  CAN connection to J533;
- the Open MMI adapter is connected at the radio on infotainment CAN.

A TPMS status may therefore exist on drivetrain CAN or cluster CAN without being
forwarded to infotainment CAN. The existing captures do not establish a usable
TPMS signal on the maintained 100 kbit/s capture point.

## Profile decision

- Remove both rejected SEAT-specific TPMS mappings from the runtime profile.
- Keep the generic canonical TPMS status vocabulary available for vehicles or
  future captures that provide verified evidence.
- Keep this note as negative research evidence so the rejected mappings are not
  rediscovered and promoted from the same correlations.
- Do not deliberately flatten or drive on an underinflated tyre to create another
  warning cycle. Future work should use an already-present warning, diagnostics,
  drivetrain CAN, or the dedicated cluster CAN.
