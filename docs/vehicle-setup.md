# Vehicle setup

Vehicle Setup is the normal end-user path for selecting an installed vehicle
profile, bindings, logical CAN bus, and SocketCAN interface. It replaces manual
systemd/udev editing for supported workflows while preserving terminal recovery
and development tools.

Open it from:

```text
Settings → Vehicle setup
```

## Safety and scope

Vehicle Setup configures local receive-side Open MMI files and services. It does
not transmit CAN frames, perform ECU coding, adapt modules, or control vehicle
actuators.

V1 supports one active named CAN bus. Profiles may declare more than one bus for
future compatibility, but the current runtime activates only the selected bus.
There is no automatic vehicle, bitrate, or adapter detection.

## Maintained and custom content

**Maintained** profiles and bindings are installed with Open MMI under
`/opt/open-mmi`. They receive project updates and cannot be edited or deleted
from the dashboard.

**Custom** profiles and bindings are private user-owned files under
`~/.config/open-mmi`. They can be copied, imported, edited, duplicated, renamed,
or deleted under the restrictions described below.

Creating a custom item does not activate it. A custom file is used only after it
is explicitly selected, reviewed, and applied.

## Configured, draft, and loaded state

Vehicle Setup deliberately shows three independent states:

- **Configured** — the maintained/custom identities and revisions represented by
  the canonical configuration;
- **Draft selection** — the current page-local profile and bindings choices;
- **Loaded runtime** — the exact identities, revisions, bus, and interface
  successfully parsed by the running CAN service.

A fourth visible condition, **saved but unapplied**, occurs when an active custom
file has been saved but the running service remains pinned to the previously
loaded revision. Saving cannot silently activate new content.

## Select a setup

Profiles and bindings are grouped by source. Invalid entries remain visible with
validation information but cannot be activated.

The selected profile determines:

- available logical buses;
- the profile default bus;
- expected bitrate;
- default interface guidance;
- receive-side provisioning metadata;
- emitted event and status capabilities.

Bindings are checked against the selected profile. Unbound events are normally
warnings; malformed actions or unsafe action references are errors.

Changing a selector changes only the draft. It does not restart the service.

## Adapter state

The interface list distinguishes:

- present and up;
- present but down;
- virtual/development;
- previously selected but not detected.

An absent interface is normally a warning rather than an activation failure.
Configuration verification proves that the intended profile, bindings, bus, and
interface were loaded. Vehicle traffic is reported separately.

## Review and Apply

Choose **Review current setup** or **Review changes**. The browser sends only:

- maintained/custom source classes;
- bounded identifiers;
- one declared logical bus;
- one valid Linux interface name.

The restricted root coordinator independently resolves trusted roots, reparses
the selected content, checks revisions and compatibility, inspects the active
runtime, and returns a normalized non-mutating preview.

The preview intentionally reports itself as read-only and does not independently
authorize a mutation. The frontend combines that exact preview with separate
coordinator capability and lock state. **Apply setup** is enabled only when:

- the preview is current and valid;
- the coordinator reports Apply enabled;
- no update, lifecycle, or configuration transaction is active;
- the reviewed target and current configuration revisions are present;
- the user explicitly confirms the operation.

Apply then:

1. rechecks the current and target revisions under all locks;
2. resolves the exact maintained/custom files again;
3. writes the root-owned canonical descriptor;
4. derives the CAN service drop-in and udev provisioning;
5. reloads the required managers/rules;
6. restarts the CAN service;
7. verifies the exact loaded identities, revisions, bus, and interface.

The browser supplies no path, command, service name, generated file, or udev rule.

## Failure and restoration

If failure occurs after mutation begins, the coordinator attempts to restore the
previous canonical and generated files, restart the service, and verify the
previous loaded runtime.

The UI distinguishes:

- a stale review rejected before mutation;
- another lifecycle operation blocking Apply;
- a failed Apply with the previous setup restored and verified;
- a failed Apply whose restoration could not be verified.

An unverified restoration blocks another Apply until the coordinator recovery
path succeeds. The browser cannot select an arbitrary rollback target.

## Custom copies and import

A maintained item exposes **Use maintained … as template**. The backend creates a
new private custom item only when:

- the supplied identifier is safe and unused;
- the installed maintained template still has the reviewed revision;
- the template parses and validates successfully;
- the destination can be created without overwrite or unsafe links.

Import accepts bounded JSON content from a local file and creates a new custom
identity. It rejects duplicate JSON keys, non-finite values, invalid semantics,
unsafe identifiers, and existing destinations. Import does not activate the
item.

## Custom editing and lifecycle

The raw JSON editor is available only for custom content. Load returns an exact
revision. Save requires that revision, validates the new content, and atomically
replaces only the private user-owned file. A stale revision preserves the editor
text and requires reload.

Custom actions:

- **Edit** — valid or invalid custom items; invalid content must be repaired before it can be reviewed or applied;
- **Duplicate** — valid active or inactive custom items;
- **Rename** — valid or invalid inactive custom items;
- **Delete** — valid or invalid inactive custom items and requires confirmation.

Invalid custom entries remain selectable for recovery and show their validation failure, but Review
and Apply stay blocked. All lifecycle actions are revision-bound and excluded from concurrent Apply
or managed update transactions. They return `applied: false`.

Maintained entries remain immutable. Their only custom-content entry point is a
new separate template copy.

## Return to maintained

Select a maintained profile or bindings entry, review the source-labelled
change, and Apply it. The custom files remain untouched, but the canonical
selection and derived runtime paths return to the maintained installed content.

## Terminal inspection and fallback

Normal users should prefer the UI. Administrators can inspect the same state
with:

```bash
open-mmi-config vehicle-setup status
open-mmi-config vehicle-setup catalogue
open-mmi-config vehicle-setup coordinator
open-mmi-config vehicle-setup preview seat-leon-1p-pq35 default \
  --bus comfort \
  --interface can0
```

The preview command is non-mutating. For maintained-profile recovery when the UI
is unavailable, the legacy management path remains supported:

```bash
sudo ./scripts/manage.sh config apply-profile seat-leon-1p-pq35 default
```

That command is an advanced/recovery path, not the normal vehicle-owner journey.
See [Manual administration](manual-administration.md).

## Related contracts

- [Profile and bindings ownership](profile-ownership.md)
- [Vehicle profiles](vehicle-profiles.md)
- [CAN bus model](can-bus-model.md)
- [Vehicle action registry](vehicle-action-registry.md)
- [Vehicle event registry](vehicle-event-registry.md)
- [Vehicle status registry](vehicle-status-registry.md)
- [Historical V1 Vehicle Setup design](design/v1-vehicle-setup/README.md)
