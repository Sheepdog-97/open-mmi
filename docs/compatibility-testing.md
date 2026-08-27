# Compatibility Testing

This guide is for temporary, read-only compatibility checks on vehicles related to the SEAT Leon Mk2 / 1P and VW Group PQ35 family.

The goal is to answer a simple question:

```text
How much of the existing decoded Open MMI status works on this vehicle without changing the dashboard?
```

## Safety boundary

The dashboard should be treated as receive-only with respect to vehicle CAN. Local Vehicle Setup may change trusted host-side receive configuration, but it must not transmit vehicle control frames.

For compatibility testing:

- use a temporary/removable test lead where possible
- modify an extension harness, not the vehicle loom
- run the CAN interface in listen-only mode where supported
- do not transmit control frames from the dashboard
- do not make permanent changes for a first test

## Suggested test kit

- laptop with Open MMI already tested in demo mode
- USB CAN adapter
- known-good CAN cable
- temporary radio/Quadlock extension harness or suitable breakout
- trim tools and correct driver bits
- phone/camera for screenshots
- compatibility checklist

## Suggested first test flow

1. Photograph the original radio/dashboard state.
2. Remove the radio/head unit carefully.
3. Fit the temporary extension/breakout harness.
4. Connect CAN-H and CAN-L to the CAN adapter.
5. Bring up the CAN interface in listen-only mode where supported.
6. Confirm traffic is visible.
7. Start Open MMI.
8. Exercise the vehicle states in the checklist below.
9. Save notes, logs and screenshots.
10. Remove the test harness and restore the car exactly as it was.

Example SocketCAN shape, adjust bitrate/interface to the bus being tested:

```bash
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 100000 listen-only on
sudo ip link set can0 up
candump can0
```

Do not add termination unless you know the bus segment needs it.

## Compatibility checklist

Record the Open MMI commit hash used for the test.

```text
Vehicle:
Model year:
Engine:
Transmission:
Cluster type:
Radio/head unit:
Market/country:
Bus tapped:
Bitrate:
CAN adapter:
Open MMI commit:

Works:
[ ] dashboard starts
[ ] speed
[ ] RPM
[ ] coolant temperature
[ ] voltage
[ ] odometer where the selected profile exposes it
[ ] indicators
[ ] hazards
[ ] side/position lights
[ ] dipped beam
[ ] main beam
[ ] rear fog
[ ] handbrake
[ ] doors
[ ] boot/bonnet where available
[ ] reverse
[ ] steering wheel media keys where fitted

Missing/wrong:

Notes:

Screenshots/logs:
```

## Current compatibility status

Confirmed:

- SEAT Leon Mk2 / 1P hardware-qualified reference vehicle

Wanted testers:

- VW Golf Mk5
- Audi A3 8P
- Škoda Octavia 1Z
- Škoda Yeti

Do not report wider PQ35 compatibility as confirmed until it has been tested on real vehicles.
