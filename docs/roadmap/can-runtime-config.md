# CAN runtime configuration roadmap

> **Historical roadmap.** Current end-user selection and Apply behavior is documented in [`../vehicle-setup.md`](../vehicle-setup.md); terminal fallback is documented in [`../manual-administration.md`](../manual-administration.md).

`open-mmi` currently supports one active SocketCAN interface, resolved through a named
CAN bus model.

This matches the current hardware-qualified SEAT Leon Mk2 / 1P (PQ35) setup while avoiding a
hard-coded `can0` assumption in the daemon and vehicle profile model.

Future vehicles and installs may need multiple CAN inputs at different bitrates.

Examples:

```text
comfort CAN       can0    100000
powertrain CAN    can1    500000
infotainment CAN  can2    vehicle-dependent
OBD capture       canX    vehicle-dependent
replay/testing    vcan0   no physical bitrate
```

This work belongs in dedicated beta branches because CAN interface setup affects core
runtime behaviour.

---

## Current implemented behaviour

Current behaviour on `main`:

* the daemon resolves an active named bus
* the default named bus is `comfort`
* the SEAT Leon Mk2 / 1P profile declares `default_bus` and `can_buses.infotainment`
* the default SocketCAN interface is `can0`
* `OPEN_MMI_CAN_BUS` can override the selected named bus
* `OPEN_MMI_CAN_INTERFACE` can override the consumed SocketCAN interface
* the daemon currently opens one SocketCAN interface at a time
* rules, presence rules, and status rules may optionally declare `bus`
* rules without `bus` belong to the profile `default_bus`
* `config apply-profile` can generate runtime and udev provisioning from the selected profile
* the generated SEAT Leon Mk2 / 1P udev rule brings up `can0` at `100000`
* the daemon consumes the already-provisioned interface and does not configure bitrate

The maintainer-tested real-car path remains:

```text
SEAT Leon Mk2 / 1P (PQ35)
bus: infotainment
interface: can0
bitrate metadata: 100000
provisioning: udev
```

---

## Compatibility requirement: udev hotplug and reboot survival

The current setup relies on udev to make the tested CAN adapter usable across hotplug
events and reboots. That behaviour must not regress.

The daemon should consume SocketCAN interfaces that are provisioned externally by udev,
systemd-networkd, or user setup scripts. It should not silently replace the provisioning
layer.

Required behaviour:

* if the configured interface is missing, the daemon keeps running and waits
* if the interface appears later, the daemon opens it
* if the interface disappears, the daemon closes the bus and waits for it to return
* after reboot, existing udev/system setup should still bring the interface back
* bitrate configuration must remain explicit and reviewable
* automatic interface setup must not silently change live vehicle CAN settings

A future optional helper may bring interfaces up, but that should be explicit user-chosen
behaviour, not hidden daemon behaviour.

---

## Current provisioning split

The current implementation separates provisioning from consumption:

* `udev/80-canbus.rules` provisions the tested SocketCAN interface
* the current rule targets `can0`
* the current rule configures `can0` at `100000`
* `OPEN_MMI_CAN_BUS` selects the active named bus and defaults to `comfort`
* `OPEN_MMI_CAN_INTERFACE` selects the SocketCAN interface consumed by the daemon and
  defaults to `can0`
* `systemd/user/canbusd.service` starts the daemon but does not configure CAN
* `scripts/manage.sh` installs/removes the udev rule and user service
* `scripts/manage.sh config edit-can` creates or edits the CAN runtime override drop-in
* the daemon waits for the configured interface to exist and reconnects if it disappears

Named buses describe which already-provisioned interface the daemon should consume. They do
not automatically replace the udev/system provisioning layer.

---

## Vehicle profile bus metadata

Vehicle profiles may declare named CAN bus metadata:

```json
{
  "default_bus": "infotainment",
  "can_buses": {
    "infotainment": {
      "interface": "can0",
      "bitrate": 100000,
      "capture_point": "maintainer-tested radio infotainment CAN connection",
      "provisioning": "udev",
      "bring_up": false
    }
  }
}
```

`bitrate` is metadata and documentation for the expected bus speed. The daemon does not
currently configure the bitrate.

`bring_up` is reserved as explicit metadata. Setting it does not currently make the daemon
bring the interface up.

---

## Rule bus selection

Vehicle profile entries may optionally declare a bus:

```json
{
  "id": "0x470",
  "bus": "comfort",
  "byte": 1,
  "type": "bitfield",
  "path": "doors"
}
```

If `bus` is missing, the entry belongs to the profile `default_bus`.

This keeps existing one-bus profiles compatible while allowing future profiles to
distinguish comfort, powertrain, infotainment, OBD, or replay traffic.

---

## Roadmap phase status

### Phase 1: single named bus

Status: **done for alpha**.

The daemon has a named bus concept and defaults to:

```text
default_bus = comfort
comfort.interface = can0
```

Existing profiles continue working because missing `bus` values default to the selected
profile default bus.

### Phase 2: runtime-configurable interface

Status: **done for alpha**.

The single active bus interface can be changed without editing Python code.

Examples:

```text
OPEN_MMI_CAN_INTERFACE=can0
OPEN_MMI_CAN_INTERFACE=can1
OPEN_MMI_CAN_INTERFACE=vcan0
OPEN_MMI_CAN_INTERFACE=slcan0
```

The default remains `can0`.

### Phase 3: bus metadata in vehicle/profile docs

Status: **done for the SEAT Leon Mk2 / 1P reference profile**.

The SEAT Leon Mk2 / 1P (PQ35) profile now declares:

```text
default bus: infotainment
tested interface: can0
tested bitrate: 100000
provisioning: udev
```

### Phase 4: per-rule bus field

Status: **implemented, lightly used**.

Rules, presence rules, and status rules may declare `bus`.

The current SEAT Leon Mk2 / 1P profile does not need explicit `bus` fields yet because all entries
belong to the default `infotainment` bus.

### Phase 5: multiple active buses

Status: **not started**.

The daemon does not yet open and monitor multiple SocketCAN interfaces at the same time.

Decoded values from multiple buses should eventually be merged into one vehicle-state
snapshot, while debug views expose bus health and source details.

---

## Recommended next work

### Profile-driven provisioning

Status: **implemented in `beta/profile-provisioning`**.

The selected vehicle profile now drives the normal local setup path. `config
apply-profile` reads `default_bus` and `can_buses` metadata, writes the daemon
runtime drop-in, and generates udev provisioning rules.

This keeps normal setup as one profile-selection workflow while preserving
`config edit-can` as an advanced override.

### CAN bus health/debug state

Expose the active bus, interface, configured metadata, interface presence, open/closed
state, and last-frame freshness in the status snapshot.

This should happen before full multi-bus runtime because it will make later debugging much
easier.

### Replay / vcan workflow

Document and test a repeatable `vcan0` workflow for replaying captures off-car.

Example:

```text
OPEN_MMI_CAN_BUS=comfort
OPEN_MMI_CAN_INTERFACE=vcan0
```

### Profile validation

Add lightweight validation for profile shape, CAN bus metadata, rule bus fields, CAN IDs,
masks, and status paths.

### Multi-bus runtime

After health/debug state, replay workflow, and validation exist, add support for opening
multiple named SocketCAN interfaces simultaneously.

---

## Non-goals for the current model

Do not mix the current named-bus model with:

* CAN transmit/control
* automatic bitrate guessing
* automatic vehicle detection
* hidden daemon-side interface provisioning
* full multi-bus arbitration
* large vehicle profile rewrites
* UI redesign
* release/tag work

The current model should preserve the tested SEAT Leon Mk2 / 1P behaviour while giving the project a
clean path toward multiple CAN buses later.
