# Cross-vehicle infotainment-CAN validation — 2026-08-27

**Status:** Research evidence supporting decoder hardening  
**Bus:** VAG 100 kbit/s radio / infotainment CAN  
**Method:** Passive `candump -L` capture with guided baseline/action/result windows

## Vehicles

The comparison used controlled captures from:

- 2005 SEAT Leon 1P, 2.0 TDI, RHD — captured 2026-08-25.
- 2010 Volkswagen Passat B6, RHD — captured 2026-08-27.
- 2012 Škoda Superb, 2.0 TDI, RHD — captured 2026-08-27.

The SEAT Leon maintained profile was used as the reference manifest while capturing the
other vehicles. Cross-vehicle agreement is used here to harden reusable decoder
primitives; it does not by itself qualify the Passat or Superb as the Leon profile.

## `0x65D` odometer is a 20-bit field

The maintained profile previously decoded bytes 1..3 as an unrestricted little-endian
24-bit value. The Leon capture happened to have a zero upper nibble, which hid the
problem.

Observed examples:

| Vehicle | bytes 1..3 | lower 20 bits | entered cluster value |
| --- | --- | ---: | ---: |
| Leon | `08 E2 04` | 320008 km | 198840 mi |
| Passat | `5C AA F2` | 174684 km | 108541 mi |
| Superb | `A9 44 F4` | 279721 km | 173807 mi |

The upper nibble of byte 3 is unrelated to the odometer. The maintained decoder should
therefore retain the raw 24-bit source for diagnostics but calculate kilometres from:

```text
u24le(bytes 1..3) & 0x0FFFFF
```

No offset is justified by these captures.

## `0x470` boot state must coexist with door bits

On the Superb, opening the boot while the front-right door was also open produced
`0x470` byte 1 = `0x61`. With the front-right door shut and boot still open, the value
was `0x60`.

An exact whole-byte comparison (`byte1 == 0x60`) therefore loses the boot state whenever
another closure bit is present. The boot field should use masked equality:

```text
(byte1 & 0x60) == 0x60
```

The bitfield decoder now supports `{ "mask": ..., "value": ... }` entries inside
`equals` for this kind of composable field.

The Superb bonnet test is deliberately excluded from validation because that vehicle's
bonnet-switch wiring is cut.

## `0x470` bulb warning is bit `0x10`

The Leon capture recorded byte 4 = `0x10` with the cluster bulb-warning telltale active.
On the Passat, the cluster reported no bulb warning while byte 4 reached `0x20` during
rear-fog operation.

The previous whole-byte true/false comparison could therefore suppress a valid false
state or mis-handle unrelated bits. Decode only bit `0x10`:

```text
bulb_out = (byte4 & 0x10) == 0x10
```

## `0x531` front fog is an independent `0x08` bit

The Superb lighting matrix showed the same base lighting modes as the Leon with bit
`0x08` added whenever the front fog lights were active:

| Base mode | Front fog active |
| --- | --- |
| `C3` dipped beam | `CB` |
| `D3` rear fog | `DB` |
| `D7` main beam + rear fog | `DF` |
| `F3` rear fog + reverse | `FB` |

Running Open MMI live during this test reproduced the decoder failure: when front fog
was enabled, the previously known lighting telltales disappeared because the unmasked
enum no longer recognised the byte.

The decoder should therefore publish front fog independently and evaluate the existing
lighting-mode enum from:

```text
base_mode = byte0 & 0xF7
front_fog = bool(byte0 & 0x08)
```

`lighting.front_fog` is introduced as experimental until it has direct maintained-Leon
hardware qualification.

## `0x635` remains a dimmer source, not a lights-on boolean

Cross-vehicle captures reinforce the earlier removal of `lighting.lights_on` from
`0x635` byte 0. Values track dimmer level (`0x1E`, `0x44`, `0x64`) rather than a stable
exterior-lights boolean. `lighting.mode` on `0x531` remains the authoritative decoded
lighting state.

## Not promoted by this patch

The following observations remain deliberately outside maintained runtime behaviour:

- Fuel-range candidates on `0x651` / `0x655`: values did not correlate consistently
  with the displayed range across vehicles, and VCDS radio measured values did not
  expose a fuel-range quantity.
- AUTO-light candidate on `0x531` byte 1 bit `0x20`: promising but not yet qualified.
- Passat and Superb steering-wheel variants: require vehicle-specific profiles and, for
  the Superb thumbwheels, multi-byte event matching.
- Horn, central-lock switch and parking-distance candidates from the Superb: research
  only pending dedicated replay/profile work.

For future temperature calibration, VCDS live values are preferred over the buffered
analogue coolant gauge as an independent ground-truth source.
