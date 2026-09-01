# Changelog
## Unreleased — Trust architecture generation 1

### Added
- Strict machine-readable Trust Manifest v1 with stable capability identifiers, explicit policy/assurance separation, deterministic SHA-256 fingerprinting, fail-closed validation, packaging checks, documentation, and CI verification.
- Initial CI trust tripwires that reject remote dashboard render dependencies and CAN-runtime `send`/`send_periodic` calls while CAN transmission remains prohibited.

### Changed
- Vendored the exact reviewed Bootstrap 5.3.8 stylesheet locally with published SHA-384 verification and removed the unused Bootstrap Icons stylesheet, eliminating both normal-render jsDelivr dependencies from the network-egress trust declaration.
- Project philosophy and security documentation now state that trust-boundary changes must be declared and distinguish operational local processing from telemetry-purpose collection.

## Unreleased — V1 vehicle setup coordinator
- Added formal maintained-profile qualification records, reviewed lifecycle transitions, compatibility boundaries, and stale-review warnings.
- Added a hierarchical maintained vehicle catalogue with stable IDs, legacy aliases, contributor templates, deterministic mapping replay fixtures, and exact migration of missing legacy maintained runtime paths.
- Added generated maintained-vehicle navigation and a cross-vehicle canonical capability matrix derived from checked profile identity, qualification, evidence and replay coverage.
- Added a collision-safe `vehicle-setup scaffold` source workflow that creates an explicitly experimental profile envelope, contribution directories and catalogue registration without claiming reverse engineering or hardware support.
- Added bounded classic-CAN capture normalization, filtering, before/after byte-and-bit comparison, and experimental candidate replay export that cannot write directly into the maintained vehicle tree.

### Added
- Host-backed Trip B as an independent long-term odometer counter with manual reset and miles/kilometres display.
- Configurable Trip A automatic reset after a parked interval, using a conservative vehicle-present heartbeat and odometer continuity check.
- MIB-style inspection detail notification with persistent acknowledgement tied to the exact due level and service schedule.
- Versioned maintained vehicle-profile schema, explicit identity/maturity/qualification metadata, reviewable evidence records and a single `vehicle-setup conform` catalogue-admission command shared by contributors and CI.
- Canonical machine-readable vehicle-action registry with stable human-readable behavior identifiers, configured-argument contracts, event-payload compatibility, availability requirements, lifecycle status and private Python implementation mappings.
- Action search/check CLI tooling, generated action documentation, runtime resolution and maintained-binding conformance that complete the event → action → implementation boundary without restricting same-pull-request proposals.
- Canonical machine-readable vehicle-status registry with stable path meaning, value type, unit, enum, nullability and lifecycle contracts for all maintained Seat status outputs.
- Human-language status search/check tooling, generated status documentation, runtime/profile conformance, enum-value enforcement and event guidance that redirects ambiguous persistent concepts toward the status registry.
- Canonical machine-readable vehicle-event registry with stable semantic, payload, delivery and lifecycle contracts for all maintained profile emissions and binding keys.
- Generated event reference, vehicle-integration standard, CLI registry inspection and conformance tests that reject vehicle-specific event synonyms and payload mismatches.
- Contributor-facing continuity policy that keeps raw CAN discovery open while requiring maintained profiles to reuse or propose one shared human-readable vocabulary.
- Human-language event search and reuse/proposal guidance through `open-mmi-config vehicle-setup events --search` and `--check`, plus pull-request and issue-template checkpoints.
- Dedicated root-owned vehicle configuration coordinator with atomic persistent public state, interrupted-state recovery, fixed status/preview Unix-socket actions, dedicated group authorization, and configuration/update/lifecycle lock primitives.
- Coordinator-owned non-mutating vehicle setup preview that independently rereads fixed maintained/custom catalogue roots, the installed runtime drop-in, SocketCAN state, and the current configuration revision before returning a normalized plan.
- Local-only `GET /api/system/vehicle-setup/coordinator`, `POST /api/system/vehicle-setup/preview`, fixed confirmed `POST /api/system/vehicle-setup/apply`, and matching status/preview/apply clients. Settings binds Apply to the reviewed target and revisions, shows live transaction state, and keeps caller-selected restore unavailable.
- Hardened `open-mmi-vehicle-config-coordinator.service` with an AF_UNIX-only, network-isolated, read-only system sandbox and a root-owned fixed-path environment file for the installed service user.
- Concrete apply operations for deterministic canonical/systemd/udev rendering, no-follow catalogue reads, durable root-owned rollback snapshots, atomic sibling replacement, fixed systemd/udev commands, refreshed loaded-runtime polling, exact restoration verification, and interrupted-transaction recovery.
- Root-only one-shot `qualify-vcan` round-trip command that accepts a previously reviewed preview on standard input, requires an up kernel `vcan` device plus an exact root-owned consent marker, verifies the temporary activation, restores the previous setup under the same transaction locks, and qualified the transaction before the fixed public apply protocol was enabled.
- Fixed coordinator apply action that accepts only the exact normalized reviewed target, active and target revision tokens, and `confirm: true`; rebuilds the request and plan under all transaction locks; and returns machine-readable stale/busy or restored-failure results. Preview remains read-only and Settings enables Apply only from separate coordinator capability state.
- Root-only, one-shot UI qualification commands that arm either a pre-mutation stale-review response or a post-restart verification failure. The restored-failure mode exercises the real snapshot restoration and verification path while reapplying only the already-active ready setup.
- Local same-origin custom-copy creation for profiles and bindings. A maintained catalogue item is accepted only as an exact revision-bound template, the new file is written privately beneath the service user's custom catalogue, and the UI immediately selects the new custom copy without activating it.
- Private custom-copy provenance sidecars recording the template identity, template revision, Open MMI build identity, display name and creation time without exposing those files to `canbusd`.
- Custom-only JSON loading and editing in **Settings → Vehicle setup**. Loads return exact content plus its revision; saves require that revision, validate before writing, atomically replace only the user-owned file and remain explicitly unapplied until a separate review and confirmation.
- Revision-bound custom lifecycle controls for duplicate, rename and delete. Maintained items expose no lifecycle actions; active custom items can be duplicated but cannot be renamed or deleted until another item is applied. Provenance follows duplicates and renames, and every lifecycle result remains unapplied.
- Local same-origin JSON import for new custom profiles and bindings. The server validates strict UTF-8 JSON and the complete profile/bindings schema before a private no-overwrite creation, records import provenance, selects the new item only as a draft, and never applies or restarts the CAN service.

### Changed
- Settings → Display now uses a non-persistent press-and-hold tell-tale Test button; releasing, losing focus or leaving the page always restores live vehicle state.
- Removed the obsolete SEAT 1P climate demist compatibility aliases; recirculation now publishes only canonical status paths.
- Door-open overlays are suppressed while the Vehicle page is visible because that page already shows the full door state.
- Hazard activation now drives the left and right indicator tell-tales without adding a separate warning triangle.
- Settings now uses a four-branch drill-down tree so categories and nested pages fit the 800×480 sidebar without a scrollbar.
- Dashboard trip readouts now share one compact Trip A/B card with an arrow control and a persisted selection.
- Maintained default bindings now use canonical action identifiers instead of Python module/function names. Existing custom legacy bindings remain supported with a migration warning.
- Settings → Vehicle setup now distinguishes configured catalogue content, page-local draft selection and exact loaded-runtime evidence. Saved active custom revisions are labelled as awaiting review and Apply instead of appearing already loaded.
- Technical details render compact SHA-256 fingerprints with the full exact value retained in accessible metadata, preventing long revisions from widening the 800×480 panel or the cards above it.
- CAN summary labels now describe the selected draft bus, adapter and compatibility rather than implying those draft values are already active.

### Fixed
- Corrected Playwright Trip B rounding and made the Settings tree locator unambiguous after drilling into Vehicle.
- Playwright Trip B coverage now opens the visible Drive card before using the A/B selector instead of waiting on a hidden duplicate.

### Security
- Privileged rendering reopens catalogue files through descriptor-relative no-follow traversal, verifies maintained/custom ownership and non-writable modes, and rechecks the reviewed content revisions immediately before installation.
- Generated destinations reject symlinks and non-regular files; rollback manifests and payloads are checksum-validated, root-only, bounded, and retained in a bounded archive.
- The qualification command consumes its `0600` consent marker before mutation, accepts no paths or commands from the caller, binds to the reviewed target and revisions, suppresses hardware udev provisioning, and removes its rollback snapshot only after restoration is verified.
- UI failure qualification is armed only by root through a fixed-path `0600` marker with one of two exact contents. The coordinator consumes it inside all transaction locks, requires a no-change preview of the current ready setup, and never exposes a browser-selectable qualification mode or an unverified-restoration injection.
- Apply rejects duplicate/non-finite HTTP JSON, additional runtime drop-ins that override coordinator-owned keys, existing selected network interfaces that are not kernel SocketCAN devices, absent non-`canN` interface names, and all `vcanN` targets. Virtual CAN remains available only through the root-only one-shot qualification boundary.
- Custom-copy requests accept no paths or content, require `template_source: maintained`, bind to the maintained file revision, reject existing destinations, validate the source document, reject unsafe user catalogue directories and write new content and provenance as private no-overwrite files. No route edits or deletes maintained content.
- Custom editor routes accept only `source: custom`, fixed kind/id identities, an expected SHA-256 revision and bounded JSON text. They reject maintained identities, symlinks, hard links, non-private ownership or modes, stale revisions, duplicate keys, non-finite values and invalid profile/bindings schemas before atomic replacement.
- Custom lifecycle mutations acquire the shared Open MMI lifecycle lock, recheck the exact source revision, reject path-shaped or maintained identities, refuse overwrite, preserve private ownership and provenance, and fail closed when rename or delete targets the active custom identity. Managed installation pre-creates the root-owned lock files without replacing live inodes.

### Fixed
- Coordinator-managed `canbusd` runtimes now pin the exact successfully parsed profile and bindings revisions until process restart. Legacy periodic and SIGHUP reloads remain available only outside managed Vehicle Setup, preventing an active custom editor save from silently becoming loaded before reviewed Apply.
- Install, interactive update and prepared deployment now preflight the fixed custom vehicle catalogue for symlinks, hard links, special files and foreign ownership, then repair only its directories/files to user-owned `0700`/`0600`. Unrelated settings such as `dashboard.env`, `launcher.json` and qualification backups are not recursively changed.
- Managed installs now require a live coordinator socket and successful status round trip before reporting success, and prepared-update rollback preserves the previous coordinator unit and environment file.
- The update and vehicle-configuration coordinators preserve their shared `/run/open-mmi` runtime directory across service restarts, preventing one coordinator restart from deleting the other coordinator's live Unix socket.
- Managed installation writes an exact per-user systemd writable-path override so interrupted vcan qualification can be restored by the hardened coordinator service without granting write access to the rest of the service user's home directory.
- Physical CAN provisioning now runs through a separate fixed oneshot service in the host network namespace with only `CAP_NET_ADMIN`/read-search capabilities. The long-running JSON coordinator remains network-isolated, no longer performs a broad `udevadm trigger`, and retries retained `restore-unverified` snapshots before reopening apply.
- Fixed the coordinator sandbox installer so its shell function is defined outside the environment-file Python heredoc, and normalize the exact managed canbusd drop-in directory to trusted `0755` permissions before qualification.


## Unreleased — V1 update management

### Added
- Managed `/opt/open-mmi/.update-source.json` metadata recording the managed source checkout, nightly channel, branch, upstream, installed commit, and installed version.
- Local-only update status, readiness, coordinator status, manual check, candidate preparation, and confirmed installation endpoints.
- Settings → System visibility and fixed controls for checking, preparing, and installing a managed nightly candidate with live transaction state.
- Branch-specific update source, API, UI, execution, rollback, health, and permission design records.
- Root-owned `/etc/open-mmi/update-policy.json` with fixed `stable`, `beta`, and `nightly` channel selection plus automatic migration from the legacy `development` label.
- Administrative `open-mmi-config updates status`, `updates check`, and `updates channel` commands.
- Read-only pre-update readiness inspection through `GET /api/system/update-readiness` and `open-mmi-config updates readiness`.
- Fail-closed disk, command, coordinator, transaction-lock, configuration-preservation, power, thermal, and service restart-loop checks.
- Root-owned update coordinator service with atomic persistent state, crash recovery, exclusive transaction locking, and fixed status/prepare/install Unix-socket actions.
- Restricted candidate preparation with fixed confirmation, root-owned staging, forward-ancestry proof, release-tag identity validation, and persistent preparation state.
- Confirmed CLI and same-origin browser nightly candidate installation through a no-arguments one-shot root service, with identity/ancestry revalidation, deployment backup, fixed health checks, and automatic restoration on failure.
- Automatic transaction-artifact cleanup with one active/prepared staging tree and two retained rollback archives.
- Stable/beta semantic release-tag filtering, official-repository enforcement, downgrade refusal, and rewritten-tag detection.

### Changed
- Settings → System keeps the everyday nightly update state in the main panel and moves repository/readiness diagnostics into expandable technical details.
- Update transaction labels now use the user-facing **Update progress**, **Last update**, and **Last update version** wording.

### Fixed
- Source mismatch feedback is no longer duplicated between the update notice and raw error text, and equivalent source-readiness blockers collapse into one reason.
- Update notices now sit directly below their action controls with consistent spacing.

### Security
- Update checks accept no browser-selected repository, path, remote, branch, ref, timeout, or command.
- Git credential prompts are disabled, checks use bounded argument-list subprocesses, and raw remote errors are not exposed to the browser.
- A remote commit mismatch is reported conservatively when update direction cannot be proven without changing the checkout.
- Channel policy rejects symlinks, writable files, unknown fields, unsupported channels, non-root production ownership, untrusted release remotes, and browser-selected source data.
- Git inspection invoked through `sudo open-mmi-config` drops back to the original user before reading the user-owned checkout.
- Browser execution accepts only exact confirmation objects over literal-loopback, same-origin JSON routes and delegates to the fixed coordinator protocol; it cannot select update inputs or pass DNS-rebinding hostnames.
- Coordinator handoff completes even when the dashboard connection closes during its expected self-restart.
- Artifact pruning accepts only contained, non-symlinked coordinator transaction directories and leaves unrelated entries untouched.

### Not yet included
- No browser channel editor, scheduling, unattended updates, stable/beta installation, or caller-selected/manual rollback target.

## Unreleased — V1 runtime hardening

### Added
- Build-aware frontend identity, versioned local assets, `/api/version`, and one-shot automatic reload after installed updates.
- Shared dashboard connection recovery with bounded health backoff and in-place same-build restart recovery.
- Jellyfin provider reconnection without reloading Chromium.
- Touch-safe **Open MMI Interface Chooser** and guarded graphical TUI recovery.
- Read-only thermal, CPU-frequency, power, charging, cooling-device, and runtime activity diagnostics.
- Branch-specific design records plus permanent runtime-hardening and vehicle-tablet installation guidance.

### Changed
- User-facing version and update state now appear in Settings → System; lower-level build comparison remains in Diagnostics.
- Vehicle rendering skips unchanged state while retaining the visible 200 ms status cadence.
- Media layout, tell-tale maintenance, media-key setup, Diagnostics polling, and retry work now follow page/document visibility and explicit ownership.
- Server-backed controls pause during dashboard transport loss without destroying navigation or unsaved forms.

### Fixed
- Managed Chromium no longer requires routine cache/profile clearing after later updates.
- Diagnostics fields remain mounted instead of flashing from repeated panel reconstruction.
- Settings → System no longer remains stuck on “loading desktop shell status”.
- Linux Mint terminal-wrapper handling now launches the actual TUI instead of an idle shell in `/opt/open-mmi`.
- Touchscreen users can return from a remembered TUI default without terminal commands.

### Known limitations
- The first update from a frontend that predates the version controller may require one manual reload.
- Hot, passively cooled tablets may still throttle and suspend charging; firmware protections are not bypassed.
- CAN-daemon profiling and cooling hardware remain later work; stable/beta installation and caller-selected rollback remain outside the manual-nightly update scope.

## Unreleased — V1 foundation hardening

### Added
- Dashboard System settings for the remembered launcher UI and graphical-login application autostart.
- Secure Jellyfin setup through the dashboard and the new `open-mmi-config` CLI.
- Private `~/.config/open-mmi/dashboard.env` service configuration with connection testing and fixed-action dashboard restart.
- GitHub Actions for Python, packaging, browser, JavaScript, CSS, and live dashboard checks.
- Virtual-CAN, daemon lifecycle, action boundary, status publication, and browser interaction coverage.
- Installable Python package with console entry points and complete runtime assets.
- Playwright coverage for navigation, overlays, persistence, media selection, responsive layouts, and browser errors.

### Changed
- The dashboard service now loads its user-owned environment file; fresh installs start it on demand, graphical-login application launch is user-configurable, and advanced service enablement remains in `open-mmi-config`.
- Split dashboard media backends into Radio, USB, Jellyfin, and Bluetooth providers.
- Split frontend API, preferences, status, navigation, overlays, vehicle rendering, media controllers, and CSS into focused modules.
- Moved stateful CAN rule data into explicitly owned runtime state.
- Moved subprocess-backed actions onto a bounded single-worker queue so CAN reception remains responsive.
- Routed steering-wheel media transport through BlueZ/MPRIS-aware actions with a synthetic-key fallback.
- Expanded Diagnostics to use canonical profile paths and list every currently decoded state value.
- Made `climate.recirculation_active` the canonical status field.

### Security
- Restricted configuration APIs to loopback, same-origin requests and never return stored Jellyfin passwords or tokens.
- Write dashboard credentials atomically with mode `0600`, reject symlink targets, and keep secrets out of browser storage and command arguments.
- Pinned validated radio DNS addresses through connection and redirects.
- Opened USB media through descriptor-relative, no-follow traversal.
- Bounded Jellyfin JSON/image reads and hardened authentication-cache lifecycle.
- Isolated subscriber and persistence failures from CAN reception.

### Compatibility
