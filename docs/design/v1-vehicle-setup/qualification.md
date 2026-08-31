# Delivery and qualification

> **Historical qualification record.** Early sections preserve the order in which
> preview, coordinator mutation, and browser Apply were enabled. Current behavior is
> documented in [`../../vehicle-setup.md`](../../vehicle-setup.md). Unless a line is
> explicitly marked pending, pre-enablement wording is historical rather than current.

## Delivery slices

### Slice 0: contract cleanup

- make profile ownership language consistent;
- define maintained production resolution as installed `/opt/open-mmi` content;
- document explicit development checkout mode;
- correct the `config paths` lookup description;
- state that creating a custom file does not activate it; and
- amend the dashboard read-only constraint to permit explicit local setup while
  retaining the no-CAN-transmit boundary.

Gate: documentation and existing behaviour have one unambiguous contract.

### Slice 1: validation and catalogue

- shared identifiers and fixed-root resolution;
- profile and bindings validators;
- profile/bindings compatibility report;
- maintained/custom catalogue;
- interface discovery;
- read-only active runtime status; and
- CLI status command using the same backend.

Gate: no mutation endpoints; malformed and symlinked fixtures fail closed.

### Slice 2: canonical state and coordinator

Current foundation status: the root-owned coordinator service, persistent state schema, recovery path, dedicated authorization group, configuration/update/lifecycle lock primitives, and independent non-mutating preview action are implemented. Dashboard and CLI preview now cross the fixed coordinator socket. Managed installation is not considered successful until the service is active, its socket exists, and a status round trip succeeds; prepared rollback preserves the previous coordinator unit and fixed-path environment. At this historical foundation checkpoint, Apply, systemd/udev mutation, runtime verification and restoration remained gated. Those gates were subsequently completed in this milestone.

- root-owned descriptor;
- deterministic plan rendering;
- dedicated configuration transaction lock/state;
- preview and apply coordinator actions;
- systemd/udev generation;
- restart, verification and automatic restoration; and
- update/configuration lifecycle exclusion.

Gate: maintained selection qualifies through CLI on `vcan` and restoration tests.

### Slice 3: maintained-selection UI

- Vehicle setup Settings category;
- profile and bindings selectors;
- one-bus/interface selector;
- compatibility and provisioning preview;
- apply confirmation;
- local progress and final status; and
- technical detail disclosure.

Gate: browser tests pass at 800×480 and portrait; real device retains setup across reboot
and adapter hotplug.

### Slice 4: custom copies

- [x] create from an installed, revision-bound maintained template;
- [x] private provenance sidecars outside the daemon catalogue;
- [x] installer/update ownership hardening scoped to the custom catalogue only;
- [x] safe custom-only load/save with revision tokens, validation and atomic replacement;
- [x] managed daemon revisions remain pinned across editor saves until reviewed Apply restarts it;
- [x] revision-bound custom duplicate, inactive rename and inactive deletion with lifecycle-lock exclusion;
- [x] strict JSON import into a new private custom identity with validation, no overwrite and no activation;
- [x] active custom identities are protected from rename/delete in both UI and backend;
- [x] install/update pre-creates trusted transaction locks without replacing held lock inodes;
- [x] qualify maintained/custom activation switching through the existing reviewed apply flow;
- [x] qualify explicit return-to-maintained through the existing source-labelled selector; and
- [ ] last-known-good user revision for editor saves.

Gate: sacred custom files survive update, apply, switching and failed activation.

### Slice 5: editors

- [x] advanced custom-only JSON validation editor;
- [x] canonical action registry and maintained binding validation;
- [ ] bindings matrix;
- [ ] structured bus metadata; and
- [ ] later rule-specific forms.

Gate: UI cannot create an action outside the registry and invalid drafts never reach the
active runtime.

### Slice 6: simultaneous multi-CAN

This is a separate beta milestone after single-input setup has soaked successfully.

## Unit tests

Required coverage includes:

- identifier allowlist and traversal rejection;
- maintained and custom fixed-root resolution;
- symlinked files and components;
- deterministic catalogue ordering;
- malformed catalogue entries;
- profile bus metadata and rule bus references;
- bindings action registry and argument schemas;
- event compatibility results;
- canonical descriptor parsing and revisioning;
- deterministic systemd and udev generation;
- request key allowlists and body limits;
- stale preview revision rejection;
- atomic draft writes and conflicts;
- transaction state persistence;
- apply failure at every stage; and
- verified restoration.

## Integration tests

Use temporary roots and user-manager doubles for ordinary CI. Add Linux integration tests
where systemd/udev are available.

Required workflows:

1. maintained profile plus maintained bindings;
2. maintained profile plus custom bindings;
3. custom profile plus maintained bindings;
4. switch custom back to maintained;
5. absent selected interface;
6. invalid selected custom file;
7. service restart failure;
8. runtime loads an unexpected revision;
9. configuration apply blocked during update transaction; and
10. update blocked during configuration transaction.

`vcan0` qualification exercises successful activation without vehicle hardware.

## Browser tests

Playwright coverage includes:

- catalogue grouping and labels;
- draft selection without mutation;
- maintained/custom compatibility warnings;
- multi-declared-bus single-runtime warning;
- interface present, down and absent states;
- review differences;
- explicit confirmation;
- progress polling;
- success with no frames;
- coordinator unavailable;
- restored failure;
- custom identifier conflict;
- valid and invalid custom JSON import, existing import destination and unapplied selection;
- stale custom lifecycle revision and existing lifecycle destination;
- active custom rename/delete rejection;
- lifecycle-lock contention during duplicate, rename or delete;
- unsaved editor navigation warning;
- active custom save leaves loaded runtime revisions and PID unchanged beyond the legacy reload interval;
- focus preservation across status refresh;
- no off-screen-only feedback;
- configured, draft and loaded revision state remains distinct after a custom save;
- compact SHA fingerprints retain the exact value in accessible technical detail metadata; and
- 800×480 containment with touch-sized controls and no horizontal expansion when technical details open.

## Concrete apply-operation tests

Before exposing apply, unit and integration tests must prove:

- maintained/custom catalogue reads reject symlink traversal, unsafe ownership and writable files;
- profile and bindings revisions are rechecked immediately before generation;
- canonical, systemd and udev output is deterministic for one reviewed target;
- existing generated files are snapshotted twice around stable loaded-runtime evidence;
- rollback manifests and payload checksums reject tampering;
- absent files are restored as absent and existing ownership/modes are preserved;
- destination symlinks and non-regular files are rejected;
- only fixed manager reload, udev reload/trigger and canbusd restart commands execute;
- stale status evidence from before restart is not accepted;
- an injected post-mutation failure restores both files and previous runtime evidence; and
- interrupted mutation recovery reopens the durable snapshot and reports restored versus unverified restoration.

These tests qualified the concrete operation layer before the public socket action and browser control were enabled. The current frontend now requires the same exact reviewed target, explicit confirmation, coordinator capability, and lock checks.

## Root-only vcan round-trip qualification

Before the public apply route was enabled, the reference tablet qualified the concrete
operation layer through the coordinator binary's local `qualify-vcan` command. This is
not a general administrative apply path:

- it runs only as root and is not available over either coordinator socket or HTTP;
- it accepts one bounded strict preview object on standard input, not a path;
- the reviewed target must use an existing, up kernel virtual CAN device named
  `vcanN` under `/sys/devices/virtual/net` with the SocketCAN link type;
- an exact root-owned mode-`0600` one-shot marker under `/etc/open-mmi` must exist and
  is consumed before the transaction starts;
- active and target revisions are rebuilt and compared again under the shared
  lifecycle/update/configuration locks;
- additional canbusd drop-ins that set or unset coordinator-owned runtime keys are
  rejected before the consent marker is consumed;
- hardware udev provisioning is suppressed and the temporary qualification rules are
  never reloaded or triggered;
- the previous generated files and loaded runtime are restored and verified before
  the locks are released; and
- the durable snapshot is deleted only after that restoration succeeds.

Success is reported as `state=complete`, `stage=qualification-restored`, with both
restoration flags true. Any target-apply or restoration failure remains a failed
transaction and retains enough root-owned rollback material for interrupted recovery.

The coordinator service receives only the exact generated-file and runtime-drop-in
writable paths required to recover an interrupted qualification after process or tablet
restart. The fixed socket/HTTP Apply action was enabled only after this round trip. The browser control was connected later and now uses that same qualified protocol.

## Fixed apply protocol qualification

Before Settings was connected, the fixed HTTP route was tested independently:

- exact strict body from a fresh preview succeeds with `confirm: true`;
- missing/false confirmation and extra fields mutate nothing;
- a stale active revision returns `code=stale-preview`;
- an active update or configuration transaction returns `code=busy`;
- an existing selected non-SocketCAN interface is rejected before snapshot;
- a post-mutation injected failure returns `apply-failed-restored` with verified state;
- an unverified restoration returns `apply-failed-restore-unverified`; and
- preview continues to return `apply_available: false` because preview is non-mutating; the frontend enables Apply only after separate coordinator capability and lock checks.

On the tablet, a confirmed reapply of the already-active maintained `can0` target is a
safe first route smoke test: it exercises the socket, snapshot, fixed writes, host-network
CAN provisioning, restart and runtime verification without changing the intended
selection. The first attempt exposed that a broad `udevadm trigger` cannot run inside the
network-isolated coordinator sandbox and also prevented rollback verification. The fixed
implementation reloads udev rules without triggering all network devices and delegates
only the reviewed physical CAN link setup to the bounded oneshot helper. A retained
`restore-unverified` snapshot is retried automatically before apply is advertised again.
The fixed public apply protocol rejects `vcanN`; virtual CAN remains confined to the
root-only one-shot qualification command so the temporary target cannot be persisted
accidentally.

## Browser failure and restoration qualification

The installed coordinator provides two root-only, one-shot qualification commands for
the browser workflow. They do not add an HTTP parameter, public restore action, caller
selected path, or general failure-injection interface. Both commands arm a fixed
`0600` root-owned marker at
`/etc/open-mmi/vehicle-configuration-ui-qualification`. The next confirmed apply must
be a no-change review of the current ready setup; any changed selection consumes the
marker and is rejected before snapshot.

To qualify stale-review handling without mutation:

```bash
sudo /opt/open-mmi/venv/bin/open-mmi-vehicle-config-coordinator arm-ui-stale
```

Review the current setup in Settings and choose **Apply setup**. The coordinator consumes
the marker under all transaction locks and returns `code=stale-preview` before creating a
snapshot. The browser must discard the review, explain that it is stale, and require a
fresh review. `canbusd` must not restart.

To qualify verified automatic restoration:

```bash
sudo /opt/open-mmi/venv/bin/open-mmi-vehicle-config-coordinator arm-ui-restored-failure
```

Review and apply the current setup again. The coordinator creates the real durable
snapshot, installs and reloads the same active target, provisions/restarts normally, then
injects a fixed verification-stage failure. It must automatically restore the snapshot,
restart `canbusd`, verify the previous loaded runtime, and return
`code=apply-failed-restored`, `stage=restored`, `restoration_attempted=true`, and
`restoration_verified=true`. The browser must report that apply failed but the previous
setup was restored and verified. Apply remains available after a fresh review.

The qualification marker must be absent after either attempt. Deliberately causing an
unverified restoration on connected hardware is out of scope; automated coordinator and
frontend tests cover `apply-failed-restore-unverified`, explicit recovery guidance, and
retry blocking.

## Device qualification

On the reference tablet and SEAT Leon Mk2 / 1P profile verify:

- fresh installation default;
- selecting and reapplying the maintained profile;
- daemon restart;
- graphical logout/login;
- reboot;
- CAN adapter unplug/replug;
- vehicle asleep/no frames;
- adapter present but interface down;
- wrong interface followed by corrected apply;
- update after custom files exist;
- custom activation followed by return to maintained;
- dashboard reload during apply; and
- journal output contains actionable but non-secret errors.

## First-release acceptance

The V1 setup-management release is complete when all of the following are true:

- a user can select and safely apply maintained or custom profile/bindings identities
  and one CAN adapter entirely from Settings;
- the exact reviewed revisions are loaded only after explicit confirmation and daemon
  restart, while custom saves remain visibly unapplied beyond the legacy reload interval;
- apply failure restores and verifies the prior generated files and loaded runtime;
- maintained entries remain immutable and expose template-copy only;
- custom copy, creation-only import, revision-safe JSON editing, duplicate, inactive
  rename and inactive delete preserve private ownership and never activate implicitly;
- install/update repair only the fixed custom catalogue trees and preserve unrelated
  user settings;
- active custom rename/delete is blocked both in the UI and server contract;
- configured, draft and loaded states remain unambiguous at 800×480, and full SHA-256
  values cannot widen the panel; and
- maintained-to-custom and custom-to-maintained source switches retain ready runtime
  evidence and real receive-only CAN traffic when the vehicle is connected.

Simultaneous multi-CAN reception, broader structured rule forms, the graphical bindings matrix, and per-save last-known-good custom archives remain later milestones and do not block this single-input V1 release.
