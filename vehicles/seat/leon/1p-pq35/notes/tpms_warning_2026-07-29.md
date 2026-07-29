# TPMS warning research — 2026-07-29

## Result

CAN ID: `0x555`  
Signal: `byte[0]`, mask `0x10`  
Formula: `pressure_monitoring_warning = (byte[0] & 0x10) != 0`

Observed representative frames:

```text
warning active:  555#F02B800064000000
warning cleared: 555#E02B800064000000
```

This is a strong experimental mapping for the tyre-pressure monitoring warning
indicator. It does not distinguish low pressure from another TPMS warning cause.

## Capture evidence

All three captures used `can0` and contained the same 27 CAN IDs.

| Capture | Total frames | Duration | `0x555` frames | `byte[0]` |
| --- | ---: | ---: | ---: | --- |
| `TPMLon` | 494 | 2.336 s | 23 | `0xF0` in 23/23 |
| `TPMLonigon` | 654 | 3.095 s | 31 | `0xF0` in 31/31 |
| `igonresettpms` | 3,584 | 17.047 s | 171 | `0xE0` in 171/171 |

Source SHA-256 values:

```text
TPMLon          bed1d97eeda2c9b600da55618782873ac4501b2279598608ec149492f3cbbd1d
TPMLonigon      0be86a4da3a741d605d592fb25107adb05a1048258ba85ca3d1e7d25e26ed224
igonresettpms   790febeca78aa7c0d985ba92cec72d994a35c761364cd02450c0516ad52e7781
```

The isolated `0x10` bit was set throughout both warning captures and clear
throughout the reset capture. Other bits in `byte[0]` remain masked because their
meanings are not established.

## Confounders reviewed

- `0x470 byte[1] bit 0` is already the maintained `doors.front_right` signal.
  Its change in the reset capture reflects door state, not a TPMS reset request.
- `0x3C3 byte[0:1]` is already the maintained steering-angle source. Its change
  reflects a different steering position between captures.
- `0x527 byte[5:6]` is already mapped to outside-temperature sources. The reset
  capture began approximately 14.31 hours later, so those changes are not TPMS
  evidence.

## Profile decision

- Register `tyres.pressure_monitoring_warning` as an experimental boolean status.
- Decode it from `0x555 byte[0]` with mask `0x10`.
- Retain the full source byte at `tyres.pressure_monitoring_warning_raw` for
  diagnostics.
- Use the observed warning-active frame in the maintained replay fixture.
- Keep the mapping experimental until an independent warning cycle confirms the
  same bit through warning appearance and clearance.
