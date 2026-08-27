# Vehicle setup UI

## Placement

Add **Vehicle setup** as a dedicated Settings category. Do not append the complete flow
to the existing System page.

The category owns local runtime configuration. It does not expose CAN transmit or
vehicle-control actions.

## Current contained slice

The V1 implementation displays the configured maintained/custom identities, the
revision actually loaded by `canbusd`, a page-local draft selection, selected bus and
adapter state. Changing a selector creates an unapplied draft. Saving an active custom
file updates the configured catalogue revision but the managed daemon remains pinned to
the previously loaded revision until the user completes **Review changes** and confirms
**Apply setup**. The coordinator then rechecks exact revisions, provisions the reviewed
adapter, restarts `canbusd`, verifies loaded evidence and restores the previous setup
automatically after a failed mutation.

Custom actions are source-scoped: maintained entries are copy-only; custom entries may
be imported, edited, duplicated, renamed or deleted subject to exact-revision, active
identity and lifecycle-lock checks. None of those actions implicitly applies a setup.

## Main panel

The collapsed overview contains:

```text
Vehicle setup                                      single CAN input

Configured profile  Seat 1P                     Maintained
Configured bindings Default                        Maintained
Running CAN service Loaded revisions match configured setup
Draft selection     Seat 1P / Default              matches configured
Selected CAN bus    Comfort                        profile default
CAN adapter         can0                           connected
Expected bitrate    100 kbit/s

[Review current setup]    [Technical details]
```

The action area includes its own inline status region. Success, warning and failure
messages must not appear only in a top-of-page banner or hover tooltip.

## Language

User-facing terms:

| Internal term | UI term |
| --- | --- |
| repo/default | Maintained |
| user override | Custom |
| SocketCAN interface | CAN adapter, with interface name in detail |
| source path override | Custom selection |
| daemon | CAN service, except in technical details |
| default_bus | Selected CAN bus / profile default |

Full paths, revisions and generated files belong under **Technical details**. Final UI
polish must keep SHA-256 values from widening either the disclosure or its parent cards:
show a compact fingerprint by default, retain the exact value in accessible
metadata, and keep strict overflow containment as a narrow-screen fallback.

## Profile selection

Profiles are grouped by source:

```text
Maintained
  (•) Seat 1P              VAG PQ35 · Infotainment CAN · 100 kbit/s

Custom
  ( ) My Seat              Based on SEAT Leon Mk2 / 1P (PQ35) · locally modified
  (!) Capture experiment   Invalid JSON · not available to activate

[Use selected] [Create custom copy] [Cancel]
```

Choosing an entry changes only the draft selection. It does not restart the daemon.

## Bindings selection

Bindings show compatibility against the draft profile:

```text
Maintained
  (•) Default              12 of 12 events bound

Custom
  ( ) My controls          11 of 12 events bound · 1 warning
```

Missing bindings are warnings when the profile can otherwise run. Malformed actions or
unsafe action references are activation errors.

## CAN input selection

The first release displays one active logical bus. If a profile declares several buses,
the UI states:

> This Open MMI version can listen to one CAN bus at a time. Choose the active bus for
> this setup.

Interface choices distinguish state:

```text
Use profile default (can0)          recommended
can0                               present · up · receiving
can1                               present · down
vcan0                              virtual · development/replay
Previously selected can2           not currently detected
```

Advanced manual entry accepts only a valid Linux interface name. It does not accept a
command or provisioning script.

## Configured, draft and loaded state

The panel distinguishes:

- **Configured**: the canonical maintained/custom identity and the current catalogue
  revision on disk;
- **Draft selection**: the profile and bindings currently selected in the form;
- **Loaded runtime**: the exact revisions successfully parsed by the running `canbusd`;
  and
- **Saved but unapplied**: configured and loaded identities match, but one or both exact
  revisions differ.

When the draft differs, show `Draft selections are not applied. The running CAN service
remains unchanged.` When an active custom file has been saved but not applied, show
`Saved custom revisions await review and Apply` and retain both configured and loaded
fingerprints under **Technical details**.

Managed runtime revisions are pinned for the lifetime of the daemon process. A saved
custom file is loaded only after a fresh review, explicit confirmation and coordinator
restart.

## Review screen

The review screen is produced only from the backend preview response; the browser does
not invent filesystem paths, commands, services or revisions. Changing either selector
or refreshing active status invalidates the prior review. A preview response that does
not explicitly report `read_only: true`, `apply_available: false` and `state: ready` is
rejected. Those preview fields prove that preview itself is non-mutating; the separate
coordinator capability/lock response controls whether the confirmed Apply button is
enabled.

Review shows only changed values first, followed by an expandable complete plan:

```text
Apply vehicle setup

Profile      SEAT Leon Mk2 / 1P (PQ35) · Maintained  ->  My Seat · Custom
Bindings     Default · Maintained  ->  Default · Maintained
CAN adapter  can0                  ->  can1
Provisioning can0 / 100 kbit/s     ->  can1 / 100 kbit/s

The CAN service will restart. Open MMI will not transmit CAN messages.

[Back to selection] [Apply setup]
```

The confirmed action is bound to the exact review and active configuration revision.
The browser sends no paths, commands, generated content or service names. Apply progress,
verified success, verified restoration and blocked recovery states remain inline.

## Progress and results

Progress stages shown in place:

```text
Validating setup…
Writing configuration…
Reloading adapter provisioning…
Restarting CAN service…
Verifying active configuration…
```

Success:

```text
Setup applied
Seat 1P / Default is active on infotainment via can0.
```

Success with no adapter:

```text
Setup applied
The configuration is active. can0 is not currently detected.
```

Restored failure:

```text
Setup could not be activated
The previous configuration was restored and verified.
[Technical details]
```

## Custom-copy screen

The user chooses:

- template;
- custom display name;
- generated or edited identifier; and
- whether to open the new draft immediately.

Creation does not activate the file. Existing identifiers are never overwritten.
A separate **Import profile JSON** / **Import bindings JSON** control accepts a local JSON file, prompts for a new safe identifier, validates it on the server, and selects the imported item only as an unapplied draft.

## Editor and lifecycle controls

V1 exposes a bounded raw JSON editor for custom entries only. Load returns the exact
current revision; save requires that revision, validates JSON and schema semantics, and
atomically replaces only the user-owned file. A stale revision preserves editor text and
requires reload. Closing a dirty editor requires explicit discard confirmation.

Custom entries also expose **Duplicate**, inactive-only **Rename**, and inactive-only
**Delete**. Maintained entries expose only **Use maintained … as template**. Import is
creation-only and refuses an existing identifier. Save, import and lifecycle operations
never activate or restart the CAN service.

## Tablet and accessibility requirements

- The 800×480 layout must keep the title, current selection and primary action visible
  without horizontal scrolling.
- Selection targets meet the existing touch target size.
- Modal or overlay content has an internal scroll boundary.
- Focus is restored after polling and re-rendering.
- Status is announced through an inline `aria-live` region.
- Colour is never the only distinction between maintained, custom, warning and error.
- Dropdowns and dialogs are keyboard-operable.
- Destructive replacement is absent; there is no generic overwrite action.

## Future onboarding

A first-run wizard may later reuse the same catalogue, preview and apply APIs. The first
implementation should remain a Settings feature so backend behaviour can qualify without
making startup depend on an unfinished wizard.
