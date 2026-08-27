# CAN bus model

`open-mmi` treats CAN inputs as named buses.

The current daemon still consumes one SocketCAN interface at a time, but the profile model
should describe that interface using a stable bus name such as `comfort`, `powertrain`,
`infotainment`, or `replay`.

This keeps vehicle CAN knowledge out of core logic and avoids baking `can0` into profile
rules.

---

## Current alpha model

The current single-bus runtime resolves:

```text
named bus label  ->  SocketCAN interface
infotainment     ->  can0
```

The daemon consumes an already-provisioned SocketCAN interface. It does not silently
configure bitrate and does not bring interfaces up.

For the maintainer-tested Seat Leon 1P / VAG PQ35 setup, udev currently provisions:

```text
can0 at 100000
```

The daemon then opens that interface.

---

## Profile fields

Vehicle profiles may declare:

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

`default_bus` is the named bus used by rules, presence rules, and status rules when they do
not explicitly set `bus`.

`can_buses` is metadata and runtime selection guidance. `bitrate` documents the expected
bus speed, but the daemon does not configure that bitrate.

---

## Runtime selection and advanced overrides

Normal installed selection is recorded by Vehicle Setup in the canonical
descriptor and derived into the CAN service runtime drop-in.

The compatibility environment variables are:

```text
OPEN_MMI_CAN_BUS
OPEN_MMI_CAN_INTERFACE
```

`OPEN_MMI_CAN_BUS` selects the active named bus. `OPEN_MMI_CAN_INTERFACE`
selects the SocketCAN interface consumed by the daemon and overrides profile
metadata. This remains useful for development and replay, for example:

```text
OPEN_MMI_CAN_BUS=comfort
OPEN_MMI_CAN_INTERFACE=vcan0
```

Direct environment editing is an advanced development/recovery path. It is not
the normal vehicle-owner setup workflow and does not replace the canonical
maintained/custom identity contract.

---

## Per-rule bus fields

Rules may optionally declare a bus:

```json
{
  "id": "0x470",
  "bus": "comfort",
  "byte": 1,
  "type": "bitfield",
  "path": "doors"
}
```

If `bus` is missing, the rule belongs to `default_bus`.

This keeps existing profiles compatible while allowing future profiles to distinguish
comfort, powertrain, infotainment, OBD, or replay traffic.

---

## Profile-driven provisioning

The normal installed path is **Settings → Vehicle setup**. The reviewed Apply
transaction:

1. resolves the selected installed maintained or private custom profile by identity;
2. rereads the exact profile and bindings revisions;
3. validates the selected `default_bus` or explicit active bus;
4. reads the bus `interface`, `bitrate`, and `provisioning`;
5. writes the canonical descriptor and derived CAN service drop-in;
6. generates the receive-side udev rule where declared;
7. restarts and verifies the loaded runtime.

The legacy terminal equivalent remains available for maintained-profile
recovery and development:

```bash
sudo ./scripts/manage.sh config apply-profile seat-leon-1p-pq35 default
```

For a managed installation, maintained content resolves from `/opt/open-mmi`.
The daemon remains passive and consumes the selected receive interface. It does
not silently guess a vehicle or bitrate, and Vehicle Setup does not add CAN
transmit behavior.

---

## Non-goals

The current model does not yet implement:

* multiple simultaneous SocketCAN listeners
* automatic bitrate configuration
* automatic interface bring-up
* CAN transmit/control
* vehicle auto-detection

Those should remain separate reviewed changes.
