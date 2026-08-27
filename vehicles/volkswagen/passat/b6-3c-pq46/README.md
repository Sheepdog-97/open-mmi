# Volkswagen Passat B6 / 3C (PQ46)

This is a **replay-qualified candidate maintained profile** for the Volkswagen Passat B6 / 3C
on VAG PQ46. It is based on a controlled passive capture from one 2010 RHD
vehicle at the radio infotainment CAN connection, 100 kbit/s.

The profile deliberately exposes only mappings supported by that capture or by
strong cross-vehicle PQ35/PQ46 agreement. Every claimed event/status has deterministic
replay coverage. The real-car capture is recorded as mapping evidence, while wider
generation compatibility is not yet hardware-qualified.

## Current scope

- passive `infotainment` CAN receive on `can0` at 100 kbit/s
- Passat B6 steering-wheel controls, including the telephone button
- door, bonnet, boot, reverse and handbrake state
- lighting mode, telltales, bulb-warning mask and dimmer state
- steering angle
- selected climate state
- coolant temperature, engine speed and supply voltage
- outside temperature
- 20-bit odometer decoding
- `0x65F` vehicle-presence traffic

## Deliberately omitted

Road speed was skipped during the capture. The vehicle had no front windscreen
heater available for testing. The wheel had a telephone button rather than mute
or play/pause, so those events are not claimed. Fuel-range candidates remain
unqualified. Front-fog state itself was not exercised; the base lighting-mode
decoder only masks the known shared PQ front-fog bit so a fog request cannot
invalidate the underlying lamp mode.

The right-steering-angle guided step contained operator error and is not used as
evidence; centre/left observations and independent PQ35/PQ46 captures support the
decoder. The instrument-cluster coolant gauge and entered idle RPM were only
coarse references, so raw CAN agreement across vehicles is treated as stronger
evidence for those formulae.

The vehicle VIN was used privately to confirm that the repeating `0x65F`
identity sequence belonged to the captured car. The full VIN is intentionally
not stored in this maintained profile.

Open MMI remains passive receive-only and does not transmit vehicle CAN frames.
