# Contributing to Open MMI

Thanks for your interest in contributing to Open MMI.

`open-mmi` is an early GPLv3 vehicle integration project that connects passive vehicle CAN-bus data to configurable Linux actions, persistent vehicle state, and UI/dashboard consumers.

> Where hex meets human form.

---

## Current project status

`open-mmi` is currently an alpha/backend project.

The current maintainer-tested reference vehicle is:

```text
Seat Leon 1P / VAG PQ35
```

This is not yet a polished infotainment replacement, final tablet UI, or multi-vehicle supported product.

Useful background docs:

- [`docs/README.md`](docs/README.md) — documentation map by audience;
- [`docs/project-philosophy.md`](docs/project-philosophy.md);
- [`docs/versioning.md`](docs/versioning.md);
- [`docs/release-checklist.md`](docs/release-checklist.md);
- [`docs/status-snapshot.md`](docs/status-snapshot.md);
- [`SECURITY.md`](SECURITY.md).

---

## Project direction

Open MMI is designed to be profile-driven.

Vehicle-specific CAN knowledge should live in:

```text
maintained: vehicles/<brand>/<model>/<generation-platform>/config.json
custom:     ~/.config/open-mmi/vehicles/<custom-id>/config.json
```

The checked `vehicles/catalogue.v1.json` assigns stable IDs and compatibility aliases only
to maintained profiles. A custom profile remains user-owned and does not become a support
claim merely because it exists.

Core Python should stay generic wherever possible.

Good contributions usually improve one of these areas:

* vehicle profiles
* CAN decode notes
* status mappings
* action modules
* dashboard/UI consumers
* install/update tooling
* documentation
* tests
* screenshots or example output
* replay fixtures, capture-analysis reports, and qualification evidence

The goal is to make vehicle integration knowledge reusable, not to hardcode one car into the daemon.

## Maintained profiles state their evidence

Custom profiles and raw CAN research do not require a formal metadata envelope. A profile proposed for `vehicles/` must additionally identify the vehicle family, declare a maturity level, state the exact qualification scope, and link reviewable evidence.

Run the same gate used by CI:

```bash
open-mmi-config vehicle-setup conform --root .
```

A genuinely new vehicle can add its profile, canonical vocabulary proposals, evidence and metadata in one pull request. This is a continuity and honesty checkpoint, not advance permission or an exclusive list of supported developers. See [`docs/maintained-profile-standard.md`](docs/maintained-profile-standard.md).

## The registry is not a walled garden

The canonical event and status registries are continuity checkpoints. They do not reserve
vehicle discovery or new concepts for maintainers.

You may freely submit raw CAN captures, unknown bytes, manufacturer terminology, provisional
names and partial interpretations. A signal only needs a canonical descriptor when it is
being promoted into a maintained or distributable profile.

At that boundary:

1. confirm what the signal means to a person;
2. decide whether it is a momentary event or persistent status;
3. search for an existing canonical descriptor;
4. reuse it when the meaning matches; or
5. add a genuinely new universal descriptor in the same pull request as the mapping.

No separate permission request is required to propose a registry entry. Review exists to
keep the current SEAT profile and every future evidence-backed vehicle speaking one
understandable language rather than hundreds of private dialects.

For example, if another vehicle emits mute from CAN ID `0x431`, the contributor should reuse
`mute_toggle` and replace only the CAN ID, byte and value. A discovery label such as
`PDC_signal` is welcome in research notes, but it needs a clear human meaning—button event,
parking distance, warning state, or something else—before it becomes maintained vocabulary.

Read [`docs/vehicle-contribution-workflow.md`](docs/vehicle-contribution-workflow.md) for the
complete workflow and naming test.

Useful commands:

```bash
open-mmi-config vehicle-setup events --search mute
open-mmi-config vehicle-setup events --check mute_toggle
open-mmi-config vehicle-setup events --check pdc_signal
open-mmi-config vehicle-setup statuses --search "right door"
open-mmi-config vehicle-setup statuses --search pdc_signal
open-mmi-config vehicle-setup statuses --check doors.front_right
open-mmi-config vehicle-setup statuses --check pdc_signal
```

---

## Trust-boundary changes

Open MMI keeps security- and privacy-relevant capability declarations in
`open_mmi_trust/data/trust-manifest.v1.json`. A change that introduces, removes,
widens, narrows, or weakens enforcement for a declared capability must review the
Trust Manifest and `docs/trust-architecture.md` in the same pull request.

Do not treat a maintainer signature as permission to bypass this review boundary.
The manifest describes what a release proposes; Accepted Owner Trust State is kept
separately so candidate software cannot silently redefine the owner's accepted ceiling.
Normal production code must not call accepted-state mutation primitives, and the
`accept-current` CLI must never be extended into a post-install expansion bypass.

Examples of trust-relevant changes include:

- CAN transmission or a weaker receive-only control;
- telemetry or analytics collection;
- a new external-network purpose;
- remote vehicle-identity resolution;
- a new purpose for persistent vehicle-derived data;
- removal or weakening of an established trust guard.

Run:

```bash
python tools/verify_trust_manifest.py
python -m unittest tests.test_trust_invariants tests.test_trust_manifest tests.test_telemetry_guard tests.test_trust_inspector tests.test_accepted_trust_state
```

The first Trust Manifest generation is intentionally conservative: some capabilities
are only `declared` today. Do not raise an assurance level unless the corresponding
control is actually present and tested.

---

## Branch workflow

Open MMI uses three long-lived branches with a one-way promotion path:

```text
feature/* -> nightly -> beta -> main
```

- `nightly` is the active integration branch for new development.
- `beta` is the vehicle-tested release-candidate branch.
- `main` is the conservative production branch.

Start normal feature work from an up-to-date `nightly` branch and open the pull
request back into `nightly`:

```bash
git switch nightly
git pull --ff-only origin nightly
git switch -c feature/my-change
```

Avoid mixing unrelated work in one branch. Runtime behaviour, udev, installer,
CAN setup, and dashboard changes should normally be isolated so each change can
be tested and promoted independently.

Promote tested work through pull requests from `nightly` to `beta`, then from
`beta` to `main`. Prefer merge commits for those promotion pull requests so the
branch ancestry remains visible. A promotion merge can leave `beta` one commit
ahead of `nightly` even when their file contents are identical; do not merge
`beta` back merely to remove that count.

Production hotfixes start from `main` and are merged into `main` first. Propagate
the resulting fix commit by cherry-picking it into `beta` and `nightly`. This
copies only the hotfix and does not pull unfinished development into production.
Resolve any cherry-pick conflict in the receiving branch so both the hotfix and
that branch's newer implementation are preserved.

See [`docs/branch-workflow.md`](docs/branch-workflow.md) for the complete
promotion, hotfix, conflict-resolution, and branch-naming policy.

### Managed updater behaviour on development branches

The managed updater is intentionally bound to the branch that produced the
installed runtime. Switching branches without deploying the new branch makes
Settings report a branch mismatch and disables browser update actions.

After a feature branch contains a build that must be tested directly, authorize
and deploy it once from the terminal. For example:

```bash
git switch feature/status-dashboard
git pull --ff-only origin feature/status-dashboard
sudo ./scripts/manage.sh update
```

Later forward commits on that recorded branch can use **Check**, **Prepare**,
and **Install** in Settings. After the feature is merged, switch to the intended
long-lived branch and perform one terminal deployment to record it again:

```bash
git switch nightly
git pull --ff-only origin nightly
sudo ./scripts/manage.sh update
```

Returning an installation to `main` uses the same one-time terminal deployment.
The browser does not offer a branch selector and must not be used to authorize
an arbitrary repository or ref.

---

## Issue templates

Please use the GitHub issue templates where possible.

Current templates include:

```text
Vehicle profile request
CAN capture submission
Bug report
Feature request
```

Structured issues are much easier to review than free-form reports.

For vehicle support, useful information includes:

* vehicle make/model/year
* platform/chassis if known
* CAN adapter used
* capture point used
* bitrate
* candump logs or short excerpts
* what physical state was triggered
* VCDS/OBDeleven/diagnostic notes if available
* whether the mapping was tested on a real vehicle

Please avoid posting sensitive vehicle data such as full VINs, private locations, personal details, credentials, or complete logs containing sensitive information.

### Maintained vehicle folder placement

Maintained profiles live at:

```text
vehicles/<brand>/<model>/<generation-platform>/
```

The checked `vehicles/catalogue.v1.json` maps that human-browsable path to a
stable machine ID and any deprecated compatibility aliases. Do not create empty
brand folders or split profiles only for trim, engine, steering side, or market
badges unless the CAN mappings genuinely differ. Start a real integration with:

```bash
open-mmi-config vehicle-setup scaffold \
  --root . \
  --brand "Brand" \
  --model "Model" \
  --generation "Generation" \
  --platform "Platform" \
  --year-from 2000 \
  --year-to 2005
```

The values are placeholders, not supported-vehicle claims. Run the same command
with `--dry-run` first when reviewing the derived ID and path. The
[scaffolding guide](docs/vehicle-profile-scaffolding.md) documents the safety and
follow-up workflow.

Candidate and qualified profiles include `fixtures/mappings.v1.json`. The replay
gate must cover every canonical event and status output claimed by the profile.
Generated catalogue and capability documentation must remain current:

```bash
python tools/generate_vehicle_catalogue_docs.py --check
python tools/verify_trust_manifest.py
```

---

## Before opening a pull request

For a maintained vehicle mapping, complete the **reuse or propose** checkpoint:

- [ ] The human meaning is confirmed rather than guessed.
- [ ] Event versus persistent status is identified.
- [ ] The canonical vocabulary was searched using ordinary human terms.
- [ ] An existing descriptor is reused where its meaning matches.
- [ ] Any genuinely new descriptor is included with its contract, docs and tests in this pull request.
- [ ] Manufacturer names, CAN IDs, ECU abbreviations and action implementation names do not leak into the canonical name.

Please check:

```bash
python tools/generate_vehicle_action_docs.py --check
python tools/generate_vehicle_event_docs.py --check
python tools/generate_vehicle_status_docs.py --check
python tools/generate_vehicle_catalogue_docs.py --check
python3 -m py_compile canbusd/core.py canbusd/can_runtime.py canbusd/status_rules.py canbusd/status_bus.py
python3 -m unittest discover -s tests
python3 -m json.tool vehicles/seat/leon/1p-pq35/config.json >/dev/null
open-mmi-config vehicle-setup conform --root .
open-mmi-config vehicle-setup qualification report --root .
open-mmi-config vehicle-setup replay --root . seat-leon-1p-pq35
python3 -m json.tool bindings/default.json >/dev/null
bash -n scripts/manage.sh
```

If your change affects install/update behaviour, test the management script where possible:

```bash
sudo ./scripts/manage.sh status
sudo ./scripts/manage.sh logs
```

If your change affects documentation only, say that clearly in the pull request.

---

## Pull request expectations

A good pull request explains:

* what changed
* why it changed
* how it was tested
* whether it was tested in a vehicle
* whether it touches CAN receive, CAN transmit, actions, install/update, udev, service permissions, UI, or a declared trust boundary
* whether documentation needs updating
* whether the change is experimental, alpha, or intended as a longer-term interface

If the change affects the status snapshot, update or check:

```text
docs/status-snapshot.md
```

If the change affects release/version wording, update or check:

```text
docs/versioning.md
docs/release-checklist.md
```

If the change affects permissions, local actions, CAN safety, or trusted config, update or check:

```text
SECURITY.md
```

---

## Vehicle profiles

Vehicle profiles should contain vehicle-specific CAN IDs, byte positions, masks, values, and status mappings.

Use the correct ownership boundary:

```text
maintained profile:
  vehicles/seat/leon/1p-pq35/config.json

user-owned custom profile:
  ~/.config/open-mmi/vehicles/my-vehicle/config.json
```

Do not add a flat `vehicles/my_vehicle/` directory to the maintained repository tree.

Avoid hardcoding vehicle-specific CAN IDs or values in:

```text
canbusd/core.py
canbusd/status_rules.py
actions/
ui/
```

The backend should provide generic primitives such as:

* rules
* presence
* status
* bool
* enum
* bitfield
* percent
* raw

Vehicle profiles should be reviewable by someone who understands the vehicle but may not understand the full daemon internals.

---

## CAN capture contributions

CAN captures are useful, but they need context.

When submitting a capture, include:

* vehicle make/model/year
* platform/chassis if known
* adapter used
* capture point
* bitrate
* exact actions performed
* approximate timing of each action
* any known CAN IDs or byte changes
* diagnostic tool notes if relevant

Example action notes:

```text
00:00 ignition on
00:05 sidelights on
00:10 dipped beam on
00:15 left indicator
00:20 hazards on
00:25 brake pressed
00:30 handbrake applied
```

Good capture notes make decoding much easier. Normalize and compare bounded classic-CAN logs with:

```bash
open-mmi-config vehicle-setup capture normalize captures/action.log
open-mmi-config vehicle-setup capture compare captures/before.log captures/after.log
open-mmi-config vehicle-setup capture export \
  captures/before.log captures/after.log \
  --profile-id example-profile \
  --output tmp/action.candidate.json \
  --root .
```

The example ID is a placeholder, not a support claim. Generated reports and candidate
fixtures are refused beneath `vehicles/`; manually confirm the human meaning and canonical
contract before moving reviewed cases into a maintained profile. See
[`docs/vehicle-capture-analysis.md`](docs/vehicle-capture-analysis.md).

---

## Status snapshot and UI consumers

Dashboards and UI consumers should read the decoded status snapshot rather than parsing raw CAN frames directly.

See:

```text
docs/status-snapshot.md
```

UI consumers should:

* handle missing fields
* handle stale or absent snapshots
* avoid hardcoding vehicle-specific CAN IDs
* display decoded state where possible
* clearly label raw/debug values
* avoid treating decoded state as safety-critical truth

Vehicle-specific CAN knowledge belongs in vehicle profiles, not UI code.

---

## Safety guidelines

Open MMI currently focuses on passive CAN receive and local Linux actions.

Do not add vehicle CAN transmit/control behaviour without:

* a separate safety design
* explicit allowlists
* clear user-facing warnings
* maintainer review
* extensive off-car testing
* controlled real-vehicle testing
* documentation explaining the risk

Avoid features that could:

* distract the driver
* misrepresent vehicle state
* interfere with vehicle-critical systems
* encourage unsafe testing on public roads

Test new vehicle mappings carefully and preferably while stationary before relying on them during normal driving.

Decoded status is informational and should not be treated as a replacement for OEM warnings, diagnostics, safety systems, or driver judgement.

---

## Trusted configuration

Vehicle profiles and bindings are trusted local configuration.

Maintained bindings map canonical events to canonical action identifiers such as
`media.mute.toggle`. The action registry owns the private Python module/function mapping.
This keeps bindings readable and prevents implementation names from becoming public API.
Existing custom `module`/`func` bindings remain supported during migration, but they are
deprecated and receive a validation warning.

A malicious or careless binding may still trigger unwanted local actions. Only use profiles,
bindings, action implementations, scripts, and udev rules that you trust or have reviewed.

Before proposing an action, search with `open-mmi-config vehicle-setup actions --search
<meaning>`. Reuse a matching behavior, or add a genuinely new universal action, its
implementation, documentation and tests in the same pull request. This is a continuity
checkpoint, not a permission gate.

---

## User config safety

Application files are installed to:

```text
/opt/open-mmi
```

User-editable config should live in:

```text
~/.config/open-mmi
```

Contributions should not overwrite user config during install or update.

Install/update changes should preserve the safe user config workflow.

---

## Local permissions

Some installs may need permissions for local Linux actions such as virtual input, brightness control, or screen wake/sleep behaviour.

These permissions are local security tradeoffs.

A system with these permissions should be treated as a trusted local vehicle computer, not as a shared untrusted desktop.

If your contribution changes permissions, udev rules, input behaviour, backlight access, or service behaviour, update:

```text
SECURITY.md
```

---

## Commit style

Use short, practical commit messages.

Examples:

```text
add Seat 1P door status mapping
publish vehicle presence state from presence rules
add desktop launcher for dashboard
fix updater copy order
document status snapshot interface
add issue templates for community reports
```

Prefer commits that do one clear thing.

---

## Documentation style

Keep documentation honest and specific.

Use clear maturity labels:

```text
working / tested
experimental
planned
unsupported
unknown
```

Avoid making claims that are ahead of tested behaviour.

For example, prefer:

```text
CLI dashboard prototype
```

over:

```text
finished tablet UI
```

Prefer:

```text
Seat 1P / VAG PQ35 maintainer-tested reference profile
```

over:

```text
full VAG support
```

---

## Reporting bugs

When reporting a bug, include:

* OS/distro
* install method
* branch, tag, or commit
* CAN adapter if relevant
* vehicle profile if relevant
* capture point if relevant
* relevant logs from `sudo ./scripts/manage.sh logs`
* whether the issue happens off-car, in-car, or both

Please avoid posting sensitive vehicle data such as full VINs, private locations, personal details, credentials, or complete logs containing sensitive information.

---

## Releases

Do not create a GitHub Release casually.

A git tag is a source checkpoint.

A GitHub Release is a public artefact for users and contributors.

Before creating a GitHub Release, check:

```text
docs/release-checklist.md
docs/versioning.md
```

Release notes should clearly state:

* source tag
* project maturity status
* tested environment
* tested vehicle/profile, if applicable
* highlights
* known limitations
* safety/security notes
* contribution notes

---

## Licence

By contributing, you agree that your contribution will be distributed under the project licence:

```text
GPL-3.0-only
```

See:

```text
LICENSE
```

## Qualification transitions

Do not change a maintained profile from experimental/candidate/qualified by editing labels alone.
Use `open-mmi-config vehicle-setup qualification transition` with `--dry-run`, review the
machine-readable plan, and include complete replay or hardware evidence. The formal workflow is
documented in `docs/vehicle-qualification-workflow.md`.

Frontend third-party assets must be vendored rather than loaded during normal dashboard rendering. Bootstrap updates use `python tools/vendor_bootstrap.py`, which accepts only the pinned URL and published SHA-384 digest; commit the resulting local asset and license with the change.
