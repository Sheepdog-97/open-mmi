# Release checklist

## Branch promotion gates

Normal development moves through:

```text
feature/* -> nightly -> beta -> main
```

For a Nightly to Beta promotion:

- [ ] The promotion pull request has `beta` as its base and `nightly` as its head.
- [ ] CI is green on the exact Nightly commit being promoted.
- [ ] Required tablet, CAN, suspend/resume, update, and vehicle checks pass on that commit.
- [ ] Any Beta-only correction has been intentionally reconciled with Nightly.
- [ ] The exact promoted commit is recorded in the pull request or qualification evidence.

For a Beta to Main promotion:

- [ ] The promotion pull request has `main` as its base and `beta` as its head.
- [ ] The tested Beta candidate has completed its intended soak period.
- [ ] CI remains green and no unqualified commit was added after final testing.
- [ ] Changelog, migration notes, version information, and rollback records are current.
- [ ] The exact Beta commit being merged is recorded.

For an urgent production hotfix:

- [ ] Create the hotfix from `main` and merge it into `main` first.
- [ ] Cherry-pick the resulting fix commit into `beta` and `nightly`.
- [ ] Resolve conflicts against each branch's newer implementation rather than discarding it.
- [ ] Run CI on every branch receiving the hotfix.

See [`branch-workflow.md`](branch-workflow.md) for the complete policy.

## Automated gates

- [ ] Python 3.9 and current Python CI are green.
- [ ] Unit, Node, Playwright, CSS-cascade, package-content, and dashboard-smoke jobs are green.
- [ ] Maintained profiles and bindings pass canonical event, status, and action registry checks.
- [ ] `open-mmi-config vehicle-setup conform --root .` passes for the complete maintained profile catalogue.
- [ ] `open-mmi-config vehicle-setup scaffold ... --dry-run` produces a safe non-mutating plan and the scaffold test suite passes.
- [ ] Generated action, event, and status references are current.
- [ ] `bindings/default.json` uses canonical `action` identifiers and contains no module/function implementation names.
- [ ] The wheel installs in a clean environment and all console entry points start.
- [ ] `npm ci` reproduces the browser-test environment from `package-lock.json`.

## Runtime checks

- [ ] Replay representative CAN captures and verify clean startup, reload, interface loss, and shutdown.
- [ ] Hold a subprocess-backed action while replaying CAN traffic; confirm status frames continue and the bounded action queue does not overflow.
- [ ] After a cold daemon start, verify the first steering-wheel play/pause, next, previous, volume, and mute press acts immediately.
- [ ] With Bluetooth audio playing, verify steering-wheel pause, resume, next, previous, and stop through BlueZ AVRCP.
- [ ] Confirm Diagnostics displays canonical RPM, supply voltage, outside temperatures, and all currently decoded profile paths.
- [ ] Exercise dashboard navigation, overlays, settings persistence, and each enabled media provider.
- [ ] Confirm the dashboard remains receive-only with respect to vehicle CAN transmission.
- [ ] Confirm loopback binding is retained unless deployment security has been explicitly reviewed.

## Installation checks

- [ ] Test fresh install, desktop `install`, `reinstall`, and `remove`.
- [ ] Test upgrade from the previous `main` checkpoint.
- [ ] Record a rollback tag and procedure.

## Documentation

- [ ] Update changelog and migration notes.
- [ ] Record supported vehicles, tested hardware, known limitations, and security assumptions.
- [ ] Freeze the reviewed commit and merge that exact SHA.

## Runtime-hardening checkpoint

Use [`runtime-hardening-qualification.md`](runtime-hardening-qualification.md) for the full branch close-out. At minimum:

- [ ] Changed-build installed update reloads once without cache/profile deletion.
- [ ] Same-build dashboard restart recovers in place.
- [ ] Jellyfin restart recovers without Chromium reload.
- [ ] Touch-only TUI → chooser → Web recovery works on Linux Mint.
- [ ] Thermal/power Diagnostics stops polling outside its visible panel.
- [ ] Runtime counters confirm unchanged vehicle renders and inactive Media layout are suppressed.
- [ ] Repeat the cold-condition vehicle qualification on the exact merge candidate.

## Update-management checkpoint

Use
[`design/v1-update-management/qualification.md`](design/v1-update-management/qualification.md)
for the branch-specific evidence and exact-SHA merge gate. At minimum:

- [ ] Manual nightly Check → Prepare → Install completes from Settings.
- [ ] Installed metadata, checkout HEAD, tracked remote, and `/api/version`
  identify the same target commit after installation.
- [ ] Coordinator readiness is available to the desktop account after the
  first-install logout/login group refresh.
- [ ] One controlled health failure automatically restores the previous build
  and reports verified rollback.
- [ ] Terminal staging is removed and only two rollback archives are retained.
- [ ] A cold reboot and a prepared-state suspend/resume pass on the exact merge
  candidate.
- [ ] Stable/beta installation, scheduling, unattended updates, browser channel
  selection, and caller-selected rollback remain unavailable.
