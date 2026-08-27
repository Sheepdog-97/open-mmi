# Škoda Superb II / 3T (PQ46)

This is a **replay-qualified candidate maintained profile** for the Škoda Superb II / 3T on
VAG PQ46. It is based on a controlled passive capture from one 2012 RHD 2.0 TDI
vehicle at the radio infotainment CAN connection, 100 kbit/s.

The profile deliberately exposes only mappings supported by that capture or by
strong cross-vehicle PQ35/PQ46 agreement. Every claimed event/status has deterministic
replay coverage. The real-car capture is recorded as mapping evidence, while wider
generation compatibility is not yet hardware-qualified.

## Current scope

- passive `infotainment` CAN receive on `can0` at 100 kbit/s
- Superb 3T steering-wheel controls, including multi-byte thumbwheel events
- door/boot, reverse and handbrake state
- lighting, front fog and dimmer state
- steering angle
- selected climate state
- coolant temperature, engine speed and supply voltage
- outside temperature
- 20-bit odometer decoding
- `0x65F` vehicle-presence traffic

## Deliberately omitted

Road speed was skipped during the capture. The bonnet switch could not be tested
because its wiring had been cut. The captured vehicle had no front windscreen
heater, no independent bulb-warning truth was available, and fuel-range
candidates remain unqualified. AUTO-light, horn, interior-lock and rear-PDC
observations remain research notes only.

The vehicle VIN was used privately to confirm that the repeating `0x65F`
identity sequence belonged to the captured car. The full VIN is intentionally
not stored in this maintained profile.

Open MMI remains passive receive-only and does not transmit vehicle CAN frames.
