# Configuration contract

## Source classes

Browser and API language uses `maintained` and `custom`.

`maintained` resolves only from installed Open MMI content:

```text
/opt/open-mmi/vehicles/<id>/config.json
/opt/open-mmi/bindings/<id>.json
```

`custom` resolves only from the configured service user's Open MMI directory:

```text
~/.config/open-mmi/vehicles/<id>/config.json
~/.config/open-mmi/bindings/<id>.json
```

The API never accepts an absolute or relative filesystem path. A caller supplies a
source class and identifier. The backend resolves the path beneath its fixed root.

## Identifier rules

Identifiers are opaque catalogue keys rather than display names. Initial identifiers
should satisfy:

```text
^[a-z0-9][a-z0-9_-]{0,63}$
```

The backend must reject separators, dot components, control characters, Unicode
lookalikes and identifiers which resolve through symlinks.

Display names may initially be derived from maintained identifiers. Optional metadata
can later provide richer names without changing selection identity.

## Catalogue behaviour

Catalogue discovery must:

- use `/opt/open-mmi` for maintained production entries;
- use the known service user's config root for custom entries;
- use explicit development roots only when development mode is configured;
- sort entries deterministically;
- return the source class and identifier, not a caller-reusable path;
- report malformed custom entries rather than hiding them;
- expose profile buses and summary counts only after safe parsing;
- calculate a content revision used for optimistic concurrency; and
- never create, repair, migrate or activate a file during a GET operation.

Suggested profile catalogue entry:

```json
{
  "source": "maintained",
  "id": "seat-leon-1p-pq35",
  "display_name": "SEAT Leon Mk2 / 1P (PQ35)",
  "valid": true,
  "revision": "sha256:…",
  "default_bus": "comfort",
  "buses": [
    {
      "name": "comfort",
      "interface": "can0",
      "bitrate": 100000,
      "provisioning": "udev"
    }
  ],
  "event_count": 12,
  "status_rule_count": 29
}
```

Suggested bindings catalogue entry:

```json
{
  "source": "maintained",
  "id": "default",
  "display_name": "Default",
  "valid": true,
  "revision": "sha256:…",
  "binding_count": 12
}
```

## Canonical selection

The proposed canonical descriptor is:

```text
/etc/open-mmi/vehicle-configuration.json
```

It is root-owned, atomically written and readable by status tooling. It contains no
secret and no caller-provided path.

Schema version 1:

```json
{
  "schema_version": 1,
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
  "runtime": {
    "mode": "single",
    "active_bus": "comfort",
    "buses": {
      "comfort": {
        "interface": "can0"
      }
    }
  },
  "applied_at": "2026-07-19T12:00:00+00:00"
}
```

The stored content revisions describe what was applied. Status can report when a custom
file has changed since activation without silently reapplying it.

The configured service user and config root come from trusted installation metadata,
not from this descriptor or the browser request.

## Derived configuration

The coordinator derives:

```text
~/.config/systemd/user/canbusd.service.d/10-can-runtime.conf
/etc/udev/rules.d/80-canbus.rules
```

The user service drop-in continues to expose compatibility environment variables:

```text
OPEN_MMI_VEHICLE
OPEN_MMI_BINDINGS
OPEN_MMI_VEHICLE_CONFIG
OPEN_MMI_BINDINGS_FILE
OPEN_MMI_CAN_BUS
OPEN_MMI_CAN_INTERFACE
```

For a maintained selection, explicit paths resolve to installed content. For a custom
selection, they resolve beneath the fixed user config root.

The canonical descriptor, not these derived variables, is the source of truth for the
setup UI.

## Read-only status API

The foundation slice exposes this fixed, loopback-only route:

```text
GET /api/system/vehicle-setup
```

Current response shape:

```json
{
  "api_version": 1,
  "read_only": true,
  "runtime_mode": "single",
  "catalogue": {
    "profiles": [],
    "bindings": [],
    "issues": []
  },
  "active": {
    "state": "ready",
    "errors": [],
    "vehicle": {"source": "maintained", "id": "seat-leon-1p-pq35"},
    "bindings": {"source": "maintained", "id": "default"},
    "active_bus": "comfort",
    "interface": "can0",
    "interface_present": true,
    "configuration_revision": "sha256:…",
    "loaded": {
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
      "interface": "can0",
      "updated_at": 1712345678.5
    }
  },
  "interfaces": [],
  "compatibility": {
    "emitted_and_bound": [],
    "emitted_unbound": [],
    "bound_unemitted": [],
    "duplicate_emitted": []
  }
}
```

This endpoint performs no mutation, accepts no query-selected path or source and does
not imply that activation is available. `loaded` is `null` until bounded daemon evidence
is available. It reports the exact identities, content revisions, bus and interface
successfully loaded by `canbusd`; it does not require an adapter or recent frames.
Coordinator capability and transaction state are exposed separately through the
fixed coordinator boundary described below.

Interface entries distinguish configuration from live health:

```json
{
  "name": "can0",
  "kind": "socketcan",
  "present": true,
  "up": true,
  "configured_bitrate": 100000,
  "last_frame_age_seconds": 0.15
}
```

No recent frames is not the same as a missing interface, and neither means that profile
activation failed.

## Coordinator status API

Implemented fixed route:

```text
GET /api/system/vehicle-setup/coordinator
```

The dashboard delegates this request to the dedicated root-owned Unix-socket service.
The response reports strict persistent transaction state plus configuration, update
and lifecycle lock activity. Installed production service capability returns
`preview_enabled: true`, `apply_enabled: true` and `restore_enabled: false`. Restore is
coordinator-owned recovery only; callers cannot select an arbitrary rollback target.

The equivalent fixed CLI action is:

```text
open-mmi-config vehicle-setup coordinator
```

## Preview API

Implemented fixed route:

```text
POST /api/system/vehicle-setup/preview
```

The same-origin dashboard passes the bounded identity request over the dedicated
Unix socket. The root coordinator independently resolves the fixed maintained/custom
roots, rereads the current runtime drop-in, discovers interfaces, rebuilds the shared
plan, and returns lock activity. It does not acquire a mutation lock and cannot write
the canonical descriptor, systemd runtime or udev configuration.

Payload:

```json
{
  "vehicle": {"source": "maintained", "id": "seat-leon-1p-pq35"},
  "bindings": {"source": "maintained", "id": "default"},
  "runtime": {
    "active_bus": "comfort",
    "buses": {"comfort": {"interface": "can0"}}
  }
}
```

Preview is read-only. It returns a normalized plan, compatibility results, warnings and
the current configuration revision required by Apply. It deliberately returns
`apply_available: false` because the preview response is not an authorization signal and
must never enable mutation by itself. The frontend combines the exact normalized preview
with the separately fetched coordinator capability and lock state before enabling its
confirmed **Apply setup** control. Preview performs no filesystem write, systemd/udev
reload or service restart, and its response contains no resolved filesystem path or
generated command text.

The normalized target uses the canonical selection shape without `applied_at`. The
coordinator adds its own trusted application timestamp only after it has independently
resolved, revalidated and applied that selection.

## Apply API

Implemented fixed route:

```text
POST /api/system/vehicle-setup/apply
```

Payload is copied directly from one reviewed preview and includes the complete
normalized target, both revision tokens and explicit confirmation:

```json
{
  "target": {
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
    "runtime": {
      "mode": "single",
      "active_bus": "comfort",
      "buses": {"comfort": {"interface": "can0"}}
    }
  },
  "expected_configuration_revision": "sha256:…",
  "target_configuration_revision": "sha256:…",
  "confirm": true
}
```

The dashboard route accepts only literal-loopback, same-origin, bounded strict JSON and
sends one fixed coordinator action. The coordinator discards caller-generated planning
data, reconstructs the identity-only preview request from `target`, then rereads and
revalidates the active revision, target content revisions, bus, interface and runtime
drop-ins while all lifecycle/update/configuration locks are held. Existing network
interfaces must be SocketCAN; an absent interface is accepted only with the conservative
`canN` name. Public apply rejects `vcanN`, which remains confined to the root-only
qualification command. Stale or busy requests return machine-readable conflict codes. A post-mutation
failure returns bounded transaction state distinguishing verified restoration from an
unverified restoration failure.

This fixed route is used by both device qualification and the installed
**Settings → Vehicle setup** workflow. The frontend submits only the exact reviewed
target after explicit confirmation, polls coordinator state through completion, refreshes
loaded-runtime evidence, and reports stale, busy, restored-failure, and unverified-
restoration outcomes inline.

## Custom-file routes

Custom operations remain fixed routes rather than path-shaped routes:

```text
POST /api/system/vehicle-custom/create
POST /api/system/vehicle-custom/load
POST /api/system/vehicle-custom/save
POST /api/system/vehicle-custom/manage
POST /api/system/vehicle-custom/import
```

Creation accepts only this exact small body:

```json
{
  "kind": "profile",
  "id": "my-seat",
  "template_source": "maintained",
  "template_id": "seat-leon-1p-pq35",
  "template_revision": "sha256:…"
}
```

Only `profile` and `bindings` are accepted kinds. `template_source` must be
`maintained`; the server resolves the installed template under the fixed maintained
root, verifies the exact content revision, parses and validates it, and creates a new
private file under the fixed custom root. Existing custom identifiers are conflicts and
are never replaced. Creation does not activate the new item.

Import accepts only a fixed kind, new identifier and bounded JSON text:

```json
{
  "kind": "profile",
  "id": "imported-seat",
  "content": "{\n  \"rules\": []\n}\n"
}
```

The server rejects duplicate keys, non-finite values, invalid UTF-8, invalid profile or
bindings semantics, path-shaped identifiers and existing destinations before any custom
file is published. A successful import creates a private provenance sidecar with
`origin.type: import`, returns `applied: false`, and does not change the active setup.

Load accepts only:

```json
{"kind":"profile","source":"custom","id":"my-seat"}
```

Save accepts only:

```json
{
  "kind": "profile",
  "source": "custom",
  "id": "my-seat",
  "expected_revision": "sha256:…",
  "content": "{\n  …\n}\n"
}
```

The server resolves the fixed custom path, requires user ownership, exact private
`0700` directories and `0600` single-link regular files, validates the submitted JSON,
compares the expected revision and atomically replaces the file. A stale revision is a
`custom-stale` conflict. A successful save returns `applied: false`; review and apply remain a separate
operation. When both exact coordinator-managed document paths are active, `canbusd`
pins the successfully parsed profile and bindings revisions until process restart.
Legacy periodic and SIGHUP reloads are disabled in that managed mode so a save cannot
become an implicit activation.

Lifecycle management accepts one of three exact action schemas:

```json
{"action":"duplicate","kind":"profile","source":"custom","id":"my-seat","expected_revision":"sha256:…","new_id":"my-seat-copy"}
{"action":"rename","kind":"profile","source":"custom","id":"my-seat-copy","expected_revision":"sha256:…","new_id":"driver-seat"}
{"action":"delete","kind":"profile","source":"custom","id":"driver-seat","expected_revision":"sha256:…"}
```

Managed installation creates the shared root-owned transaction lock files before the
dashboard is enabled. The server acquires the lifecycle lock without replacing its inode
before re-reading the fixed custom path and expected revision. Duplicate requires a valid
custom source and writes a new private item and provenance sidecar without changing the source.
Rename may recover a valid or invalid inactive item by moving the exact identity and updating its
provenance without changing its content revision. Delete likewise accepts valid or invalid inactive
items, verifies that the custom identity is not active, then removes only that exact item and its
sidecar. Invalid content remains ineligible for Review or Apply.
Existing destinations are `custom-exists` conflicts, stale source revisions are
`custom-stale`, active rename/delete requests are `custom-active`, and an active
apply/update lifecycle transaction is `lifecycle-busy`. Every successful response
returns `applied: false`.

The browser supplies no path and creation supplies no document content. Maintained content has no save, lifecycle or delete route. The route also writes a private provenance sidecar beneath
`~/.config/open-mmi/.open-mmi-provenance/`.

Draft save content uses a separate bounded request limit large enough for existing
profiles and JSON string escaping. Existing small configuration routes retain their
current lower limit.
