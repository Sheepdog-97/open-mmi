# Experimental CAN candidates — 2026-07-04

## Strong candidates

### Coolant temperature

CAN ID: 0x35B  
Signal: byte[3]  
Formula: coolant_temp_c = raw - 92

Evidence:
- Correlated against HVAC group 004.4 Coolant Temperature
- corr ≈ +0.950
- RMSE ≈ 0.40 °C
- raw range 171..178
- truth range 79..85 °C

Status:
- Strong candidate
- Needs cold-to-hot validation, ideally 40–90 °C

### Terminal 30 / battery voltage

CAN ID: 0x571  
Signal: byte[0]  
Tentative formula: voltage_v ≈ raw * 0.05 + 5.1

Evidence:
- Correlated against HVAC group 007.4 Voltage Terminal 30
- split capture 1: corr ≈ +0.814, RMSE ≈ 0.09 V, raw 127..134
- split capture 2: corr ≈ +0.811, RMSE ≈ 0.12 V, raw 171..176

Status:
- Strong byte-location candidate
- Formula needs one continuous validation capture

### Fuel level and low-fuel warning

CAN ID: 0x621

Signal: byte[3]
Tentative formulas:
- `fuel_level_l = raw & 0x7F`
- `low_fuel_warning = (raw & 0x80) != 0`

Evidence:
- Before refuelling, byte[3] was `0x84`, decoding to approximately 4 L with bit 7 set.
- After adding £15 of V-Power diesel, byte[3] changed to `0x09` and gradually settled through `0x0A`, `0x0B`, and `0x0C` to `0x0D`.
- The final decoded value was approximately 13 L, an observed increase of approximately 9 L.
- In a separate capture with the screen-wash warning active and the fuel warning off, all 55 observed `0x621` frames carried byte[3] = `0x0B`; bit 7 remained clear throughout.
- The gradual post-refuel rise is consistent with tank-sender or cluster filtering.

Status:
- Strong experimental fuel-level candidate.
- Strong experimental low-fuel-warning candidate.
- Validate another controlled reserve-warning transition before promotion to stable.

## Rejected / not enough evidence

RPM:
- No convincing passive infotainment CAN candidate found
- best brute-force candidates only corr ≈ 0.54–0.58, RMSE ≈ 334–347 rpm
- do not add to runtime profile yet

Heater flaps:
- No convincing passive candidate found in current capture
- keep diagnostic-only for now

## Runtime test note

On the Surface install, the user service loaded the per-user profile copy:

```text
/home/open-mmi/.config/open-mmi/vehicles/seat_1p/config.json

## Runtime test note

On the Surface install, the user service loaded the per-user profile copy:

```text
/home/open-mmi/.config/open-mmi/vehicles/seat_1p/config.json

The updated profile had to be copied there from:

/opt/open-mmi/vehicles/seat/leon/1p-pq35/config.json

Runtime observations:

Terminal 30 voltage:
- idle: ~14.45 V
- ignition on with loads: ~12.45 V
- ignition on loads off: ~12.55 V

Coolant:
- OpenMMI value: ~61 °C
- cluster indicated roughly ~64 °C
- difference appears plausible due to cluster smoothing/display behaviour


## Outside temperature correction

Initial outside-temperature candidates used `raw * 0.5 - 100`, which produced impossible live values:

```text
regulation:  -26.5 °C on OpenMMI / ~23 °C on VCDS
unfiltered:  -26.0 °C on OpenMMI / ~24 °C on VCDS

Those OpenMMI values imply raw bytes around 147 and 148. Using raw * 0.5 - 50 gives:

147 -> 23.5 °C
148 -> 24.0 °C

So both outside-temperature candidates now use:

outside_temp_c = raw * 0.5 - 50

