# Profile and bindings ownership

Open MMI exposes two user-visible configuration kinds:

1. vehicle profiles;
2. event-to-action bindings.

Each kind may come from either an installed **maintained** source or a private
user-owned **custom** source.

The ownership rule is:

> Maintained content is installed and updated by Open MMI. Custom content is
> sacred, explicit, and never silently selected or overwritten.

## Maintained vehicle profiles

Maintained profiles live in the checked catalogue tree:

```text
source checkout:
vehicles/<brand>/<model>/<generation-platform>/config.json

installed production tree:
/opt/open-mmi/vehicles/<brand>/<model>/<generation-platform>/config.json
```

`vehicles/catalogue.v1.json` maps those human-browsable paths to stable profile
IDs and deprecated compatibility aliases. Runtime callers select the stable ID;
they do not construct the nested path.

Normal production resolution uses the installed `/opt/open-mmi` content so the
active profile remains aligned with the installed Open MMI build. A mutable
checkout is used only before installation or through an explicit development
workflow.

## Maintained bindings

Maintained bindings live at:

```text
source checkout:       bindings/<id>.json
installed production: /opt/open-mmi/bindings/<id>.json
```

Maintained bindings use canonical event keys and canonical action identifiers.
They receive project improvements when Open MMI is updated.

## Custom vehicle profiles and bindings

Custom content lives under the configured service user's private Open MMI root:

```text
~/.config/open-mmi/vehicles/<custom-id>/config.json
~/.config/open-mmi/bindings/<custom-id>.json
```

Custom files are user-owned. Open MMI install and update operations must not
silently overwrite, refresh, migrate, or delete them.

The dashboard can create custom content by:

- copying an exact installed maintained template;
- importing valid bounded JSON as a new identity;
- duplicating an existing custom identity.

Creation never activates the result.

## Canonical selection

Normal Vehicle Setup does not treat file presence or environment lookup order as
the source of truth. A root-owned canonical descriptor records the intended
maintained/custom identities, exact content revisions, one active logical bus,
and its selected interface:

```text
/etc/open-mmi/vehicle-configuration.json
```

The coordinator derives the exact installed/custom paths, CAN service drop-in,
and receive-side udev provisioning from that descriptor. The browser supplies
source classes and identifiers, never arbitrary paths.

Configured state is separate from loaded state. `canbusd` publishes bounded
evidence describing the exact identities, revisions, bus, and interface it
successfully loaded.

## Custom content is opt-in

Creating this file:

```text
~/.config/open-mmi/vehicles/example/config.json
```

does not activate it. Vehicle Setup must explicitly select:

```json
{"source": "custom", "id": "example"}
```

and complete a fresh review and confirmed Apply.

This prevents stale private copies from silently shadowing improved maintained
content after an update.

## Save and Apply are separate

Saving a custom item validates and atomically replaces only that private file.
It returns a new content revision and `applied: false`.

When coordinator-managed exact paths are active, the running CAN service pins
the revisions it parsed until restart. A saved active custom file therefore
appears as **saved but unapplied**. It is loaded only after a fresh review,
explicit confirmation, and coordinator restart/verification.

## Sacred-file rules

Open MMI must not:

- overwrite custom content during install or update;
- replace a custom item with a maintained template during Apply;
- automatically merge maintained changes into a custom copy;
- rename or delete an active custom identity;
- delete unrelated files from the user configuration root;
- activate a custom file merely because it exists.

Returning to maintained changes only the canonical selection and derived runtime
paths. The custom files remain untouched.

## Custom lifecycle restrictions

- **Edit** may open valid or invalid custom content for recovery; invalid content remains
  ineligible for Review or Apply until repaired.
- **Duplicate** may copy an active or inactive **valid** custom item because the source is
  unchanged.
- **Rename** is allowed for valid or invalid custom identities only when inactive.
- **Delete** is allowed for valid or invalid custom identities only when inactive and the user
  explicitly confirms it.
- Every operation requires the exact current revision and participates in the
  shared lifecycle lock.
- Existing destinations, stale revisions, unsafe ownership/modes, symlinks,
  hard links, and unsupported extra profile-directory content fail closed.

## Advanced direct overrides

Direct environment selection remains supported for development, migration, and
unusual recovery:

```text
OPEN_MMI_VEHICLE_CONFIG=/absolute/path/to/config.json
OPEN_MMI_BINDINGS_FILE=/absolute/path/to/bindings.json
```

These are advanced explicit overrides, not the normal custom-profile workflow.
They should be configured only through trusted local administration. Files under
`~/.config/open-mmi` are not scanned and preferred automatically.

The maintained ID variables remain compatibility inputs used by generated
runtime configuration:

```text
OPEN_MMI_VEHICLE
OPEN_MMI_BINDINGS
OPEN_MMI_VEHICLE_CONFIG
OPEN_MMI_BINDINGS_FILE
OPEN_MMI_CAN_BUS
OPEN_MMI_CAN_INTERFACE
```

For coordinator-managed installations, the canonical descriptor—not a manually
edited environment file—is the Vehicle Setup source of truth.

## Administrator fallback

The UI is the intended normal path. Administrators can inspect or recover a
maintained selection with:

```bash
open-mmi-config vehicle-setup status
open-mmi-config vehicle-setup catalogue
sudo ./scripts/manage.sh config apply-profile seat-leon-1p-pq35 default
sudo ./scripts/manage.sh config paths
```

The management-script Apply path is retained for recovery and development. It
must preserve custom files.

## Runtime logging

At startup, `canbusd` logs the active profile and bindings paths and publishes
machine-readable loaded-revision evidence. Use Vehicle Setup technical details
or administrator logs to confirm which content is actually running.

## Summary

- Maintained content comes from the installed Open MMI tree.
- Custom content remains private and sacred.
- Custom content is selected explicitly by identity.
- The canonical descriptor records the intended active configuration.
- Saving and activation are separate.
- The UI is the normal workflow; environment and management-script paths remain
  supported for advanced administration and recovery.
