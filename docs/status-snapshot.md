# Status snapshot

`open-mmi` publishes decoded vehicle state as a local status snapshot.

This snapshot is the boundary between the backend daemon and UI/dashboard consumers.

```text
CAN frames
    ↓
vehicle profile decoding
    ↓
status_bus
    ↓
status snapshot
    ↓
CLI dashboard / future UI consumers
```

---

## Current status

The status snapshot interface is currently alpha.

It is useful for the included CLI dashboard and early UI work, but the schema may change
before a stable public release.

Consumers should handle missing, unknown, stale, or extra fields gracefully.

---

## Current snapshot path

When `XDG_RUNTIME_DIR` is available, the current runtime snapshot path is:

```text
$XDG_RUNTIME_DIR/open-mmi/status.json
```

For a typical user session this may resolve to something like:

```text
/run/user/1000/open-mmi/status.json
```

If `XDG_RUNTIME_DIR` is not available, the fallback path is:

```text
/tmp/open-mmi-status.json
```

The included CLI dashboard uses the same lookup behaviour.

---

## Current top-level shape

The status snapshot is currently written as a wrapper object:

```json
{
  "updated_at": 1781440496.0,
  "state": {},
  "runtime": {
    "api_version": 1,
    "state": "ready",
    "errors": [],
    "vehicle": {
      "source": "maintained",
      "id": "seat-leon-1p-pq35",
      "revision": "sha256:…"
    },
    "bindings": {
      "source": "maintained",
      "id": "default",
      "revision": "sha256:…"
    },
    "active_bus": "comfort",
    "interface": "can0"
  }
}
```

`updated_at` records when the snapshot file was written.

`state` contains the decoded vehicle state published by the daemon.

`runtime` contains daemon-owned loaded-configuration evidence. It is outside
`state`, so vehicle profile status rules cannot overwrite it. The profile and
bindings revisions hash the exact bytes successfully parsed by the running daemon.
For coordinator-managed exact document paths, those revisions remain pinned until the
daemon process restarts through reviewed Apply; later file saves do not rewrite loaded
runtime evidence. Adapter presence and recent frames remain separate runtime-health
signals.

---

## Purpose

The snapshot exists so UI consumers do not need to read raw CAN frames directly.

A UI should consume human-readable vehicle state such as:

```text
state.vehicle.present
state.vehicle.reverse
state.vehicle.handbrake
state.lighting.mode
state.lighting.dimmer_percent
state.lighting.brake
state.lighting.left_indicator
state.lighting.right_indicator
state.lighting.hazards
state.doors.any_open
state.steering.angle_degrees
```

not raw CAN IDs and bytes.

Vehicle-specific CAN knowledge belongs in maintained or custom profile files:

```text
vehicles/<brand>/<model>/<generation-platform>/config.json
~/.config/open-mmi/vehicles/<custom-id>/config.json
```

not in UI code.

---

## Example state

A snapshot may contain state like:

```json
{
  "updated_at": 1781440496.0,
  "state": {
    "vehicle": {
      "present": true,
      "reverse": false,
      "reverse_raw": 0,
      "handbrake": true,
      "handbrake_raw": 32
    },
    "doors": {
      "front_left": false,
      "front_right": false,
      "rear_left": false,
      "rear_right": false,
      "boot": false,
      "bonnet": false,
      "any_open": false,
      "raw": 0
    },
    "lighting": {
      "mode": "dip",
      "mode_raw": 195,
      "lights_on": true,
      "lights_on_raw": 100,
      "dimmer_percent": 42,
      "dimmer_raw": 42,
      "brake": false,
      "left_indicator": false,
      "right_indicator": false,
      "hazards": false,
      "secondary_raw": 0,
      "bulb_out": false,
      "bulb_out_raw": 0
    },
    "steering": {
      "angle_degrees": 0.0,
      "direction": "center",
      "angle_raw": 0,
      "angle_magnitude_raw": 0
    },
    "presence": {
      "0x65F": true
    }
  }
}
```

This example is illustrative. The exact fields depend on the active vehicle profile and
decoded status rules.

---

## Field behaviour

Consumers should assume:

* fields may be missing
* values may be `null`
* unknown states may be reported as `"unknown"`
* raw debug values may exist alongside decoded values
* profile-specific fields may appear
* future versions may add fields

Consumers should not crash if a field is missing.

---

## Raw values

Profiles may publish raw values using paths such as:

```text
state.lighting.mode_raw
state.vehicle.handbrake_raw
state.steering.angle_raw
```

Raw values are useful for debugging and profile development.

UI consumers may display raw values in debug modes, but normal user-facing UI should prefer
decoded values.

---

## Freshness

`updated_at` should be used as a basic freshness indicator.

A UI should treat the snapshot as live vehicle state, not permanent truth.

If the snapshot is missing, invalid, or old, a UI should show a safe disconnected/stale
state rather than displaying stale data as if it is current.

Future versions may expose clearer freshness metadata, such as:

```text
snapshot_age_ms
source_vehicle_profile
daemon_state
schema_version
can.<bus>.last_frame_at
can.<bus>.interface_present
```

Until that is stable, consumers should handle stale or missing snapshots safely.

---

## Safety

Decoded status is informational.

It must not be treated as a replacement for OEM warnings, diagnostics, safety systems, or
driver judgement. Incorrect profile mappings may misrepresent vehicle state.

---

## Consumer guidance

A dashboard or UI consumer should:

* read the status snapshot
* display decoded state
* handle missing fields
* handle stale or absent snapshots
* avoid parsing raw CAN frames directly
* avoid hardcoding vehicle-specific CAN IDs
* clearly label debug/raw values

---

## Seat 1P profile-specific fields

Some vehicle profiles expose additional passive status fields when the relevant CAN signals are available.

The Seat 1P infotainment-CAN profile currently publishes:

```text
state.vehicle.speed_kmh
state.vehicle.speed_raw
state.climate.blower_load_percent
state.climate.blower_load_raw
```

`state.vehicle.speed_kmh` is decoded from the infotainment CAN speed signal and is stored internally as kilometres per hour. User interfaces may choose to display this value as mph, km/h, or both.

`state.climate.blower_load_percent` is decoded as an approximate HVAC blower load percentage.

Raw companion fields such as `speed_raw` and `blower_load_raw` are intended for debugging and profile development.

Consumers should treat these fields as optional because they may not exist for every vehicle profile.

---

## Stability promise

Before a stable release, the status snapshot schema may change.

After a stable schema is declared, breaking changes should be documented and versioned.

---

## Publication guarantees

Status updates are deep-merged in publication order and the resulting snapshot is written
using a temporary file in the destination directory followed by an atomic replacement.
The file and parent directory are synchronised on platforms that support it.

A persistence failure is logged but does not stop the CAN receive loop. In-process
subscribers still receive the decoded update, and one failing subscriber is isolated from
other subscribers.

Snapshots returned to publishers and subscribers are deep copies. A consumer cannot mutate
the daemon's in-memory status by modifying a received dictionary.

At daemon start, successful vehicle-profile/status-rule reload, or active CAN-interface
change, the in-memory state is cleared and an empty snapshot is persisted. This prevents
fields belonging to an earlier profile or runtime from remaining visible as current state.

A damaged or partially written older snapshot is not read back into live state. The next
successful publication atomically replaces it with valid JSON. Consumers should continue to
handle missing or invalid snapshots defensively.

## Timestamp format

`updated_at` is a Unix timestamp expressed as seconds since the epoch, for example:

```json
{
  "updated_at": 1784042096.25,
  "state": {}
}
```

Consumers may convert this value to a local or ISO-8601 display format as needed.

## Compatibility aliases

During an alpha-schema migration, a scalar status rule may publish a canonical path and one or more
temporary compatibility aliases. Profile rules use:

```json
{
  "path": "example.canonical_status",
  "aliases": ["example.legacy_status"],
  "raw_path": "example.canonical_status_raw",
  "raw_aliases": ["example.legacy_status_raw"]
}
```

All paths receive the same decoded value. New consumers should prefer the canonical path and
fall back to the alias only for older snapshots. Aliases must be removed only at a documented
schema compatibility boundary.
