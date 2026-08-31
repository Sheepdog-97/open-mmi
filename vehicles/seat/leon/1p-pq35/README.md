# SEAT Leon Mk2 / 1P (PQ35)

- Canonical profile ID: `seat-leon-1p-pq35`
- Deprecated compatibility ID: `seat_1p`

This is the maintainer-qualified reference profile for passive infotainment-CAN
reception at 100 kbit/s. The exact qualification scope and limitations are in
`config.json`; broader hardware acceptance is recorded in the Vehicle Setup
qualification document.

Replay every declared mapping:

```bash
open-mmi-config vehicle-setup replay --root . seat-leon-1p-pq35
```

The fixture covers all canonical events and statuses produced by the profile.
Research and diagnostic mappings remain labelled through the status registry and
profile limitations rather than being presented as universally qualified data.
