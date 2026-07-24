# open-mmi

Open vehicle MMI integration framework for Linux.

`open-mmi` connects passive vehicle CAN-bus data to a local Linux dashboard,
persistent vehicle state, and configurable local actions. Vehicle-specific CAN
knowledge lives in profiles so it can be reviewed, replayed, qualified, and
shared without hard-coding one car into the runtime.

> Where hex meets human form.

## Current status

Open MMI is an alpha vehicle-integration project with a working local web
dashboard, installed desktop launcher, vehicle-setup workflow, update
management, terminal diagnostics, and contributor tooling.

The only vehicle currently reverse engineered and maintained by Open MMI is:

```text
SEAT Leon 1P / Mk2
VAG PQ35
profile: seat-leon-1p-pq35
```

Templates, synthetic captures, scaffolds, and catalogue infrastructure do not
claim support for a second vehicle. The maintained SEAT profile is qualified
only for the equipment, connection point, vehicle variants, and signal scope
recorded in the [vehicle catalogue](docs/vehicle-catalogue.md).

Open MMI currently remains:

- passive on the vehicle bus: it does not transmit CAN frames;
- limited to one active named CAN bus at a time;
- manually configured for vehicle, bitrate, and SocketCAN interface;
- informational rather than a replacement for OEM warnings or diagnostics;
- an alpha project rather than a finished infotainment replacement.

## Dashboard

The web dashboard is the main user-facing interface. It displays decoded vehicle
state and provides local Settings workflows without exposing vehicle-control or
arbitrary system-command access.

<p align="center">
  <img src="docs/images/web-dashboard-drive.png" alt="Open MMI Drive page showing speed, RPM, coolant, voltage and footer tell-tales" width="900">
</p>

| Media | Climate |
|---|---|
| ![Open MMI Media page with demo track](docs/images/web-dashboard-media-demo.png) | ![Open MMI Climate page showing decoded climate state](docs/images/web-dashboard-climate.png) |
| Vehicle | Diagnostics/status |
| ![Open MMI Vehicle page showing door and reverse status](docs/images/web-dashboard-vehicle.png) | Settings includes live diagnostics and technical status where needed. |

The dashboard includes Home/Menu navigation, Drive, Media, Climate, Vehicle,
Settings, diagnostics, tell-tales, and read-only door/reverse overlays. Optional
media providers include Jellyfin, Internet Radio, USB, and Bluetooth.

## Start here

Choose the path that matches what you are doing:

| You are… | Start with… |
|---|---|
| Installing Open MMI for a maintained vehicle | [Getting started](docs/getting-started.md) |
| Selecting a vehicle profile or CAN adapter | [Vehicle setup](docs/vehicle-setup.md) |
| Looking for all documentation | [Documentation index](docs/README.md) |
| Using terminal commands for maintenance or recovery | [Manual administration](docs/manual-administration.md) |
| Diagnosing a problem | [Troubleshooting](docs/troubleshooting.md) |
| Adding support for a vehicle | [Vehicle contribution workflow](docs/vehicle-contribution-workflow.md) |
| Developing Open MMI itself | [Contributing](CONTRIBUTING.md) |

## Install and open

From a source checkout:

```bash
git clone https://github.com/open-mmi/open-mmi.git
cd open-mmi
sudo ./scripts/manage.sh install
```

The installer deploys Open MMI under `/opt/open-mmi`, installs its Python
environment and services, and creates application-menu and desktop launchers.
Open **Open MMI** from the desktop or application menu after installation.

The launcher starts the local dashboard service when needed, waits for it to be
healthy, and opens the managed browser window. The separate **Open MMI Interface
Chooser** can switch between the Web dashboard and terminal UI if the remembered
choice is unsuitable.

A fresh install may add the account to dedicated Open MMI authorization groups.
When the installer asks, log out and back in once so the existing desktop session
can use Vehicle Setup or browser update actions.

For the complete first-run flow, including CAN-adapter preparation and selecting
the maintained SEAT profile, see [Getting started](docs/getting-started.md).

## Vehicle setup: UI first

For normal use, configure the active vehicle from:

```text
Settings → Vehicle setup
```

The page lets a user:

1. choose a maintained or custom vehicle profile;
2. choose maintained or custom bindings independently;
3. select the active logical CAN bus and SocketCAN adapter;
4. review validation, compatibility, and provisioning effects;
5. confirm an exact revision-bound setup;
6. apply it through the restricted local coordinator; and
7. see whether the intended configuration was loaded successfully.

The interface distinguishes three states that should not be conflated:

- **Configured** — the selection recorded by Open MMI;
- **Draft** — the current unapplied selection in the page;
- **Loaded** — the exact profile and bindings revisions parsed by the running CAN service.

Saving or importing a custom file does not silently activate it. Activation
requires a fresh review and explicit **Apply setup** confirmation. If a mutation
fails after it begins, the coordinator attempts to restore and verify the
previous configuration.

The terminal tooling remains available for administrators, development, and
recovery. It is documented separately in
[Manual administration](docs/manual-administration.md) so the normal vehicle-owner
journey does not begin with service files, environment variables, or shell
commands.

## Updates

For an installed development build, the normal update workflow is:

```text
Settings → System → Software updates
```

The browser can check, prepare, and explicitly install a trusted candidate only
when the installed source policy, readiness checks, authorization, and update
coordinator allow it. The browser cannot choose a repository, branch, ref, path,
command, or rollback target.

At this development checkpoint, confirmed browser installation is intended for
the recorded **nightly** source. The policy model also contains `beta` and
`stable`, but no public beta or stable release channel is currently published.
That release-state wording must be updated when those channels become real.
There is no unattended schedule or browser channel editor.

Terminal update and channel-administration commands are preserved in
[Manual administration](docs/manual-administration.md).

## Demo mode

The dashboard can run without a vehicle:

```bash
python3 ui/web_dashboard/server.py --demo --demo-scenario traffic
```

Then open:

```text
http://127.0.0.1:8765/
```

Other scenarios exercise doors, reverse, warnings, stale data, and steady
road-speed behavior. See [Demo mode](docs/demo-mode.md).

## How it works

```text
vehicle CAN frame
        ↓
SocketCAN receive
        ↓
active vehicle profile
        ↓
canonical events + persistent statuses
        ↓
bindings + local actions + status snapshot
        ↓
web dashboard, terminal diagnostics, and future consumers
```

The project separates three vocabularies:

- a **vehicle profile** describes how one vehicle encodes a signal;
- a canonical **event or status** describes what that signal means to a person;
- a canonical **action** describes what Open MMI should do locally.

For example, different vehicles can map different CAN IDs and byte values to the
same `mute_toggle` event, while a binding maps that event to the stable local
action `media.mute.toggle`.

The checked machine-readable registries are the source of truth. Their generated
references are:

- [Vehicle event registry](docs/vehicle-event-registry.md)
- [Vehicle status registry](docs/vehicle-status-registry.md)
- [Vehicle action registry](docs/vehicle-action-registry.md)
- [Maintained vehicle catalogue](docs/vehicle-catalogue.md)
- [Vehicle capability matrix](docs/vehicle-capability-matrix.md)

Generated reference documents are verified in CI and should not be edited by
hand.

## Safety boundary

Open MMI interfaces with vehicle CAN buses. Incorrect mappings can misrepresent
vehicle state or trigger unexpected local Linux actions.

Always begin with passive observation, use an appropriate isolated connection
point, review vehicle profiles and bindings before use, and treat decoded status
as informational. Do not add CAN transmission, coding, adaptation, or actuator
control without a separate safety design, explicit allowlists, warnings,
maintainer review, and extensive testing.

Vehicle Setup may configure local receive-side files and restart Open MMI
services, but neither the dashboard nor the coordinator sends vehicle CAN
messages.

See [Security](SECURITY.md) and the
[vehicle integration standard](docs/vehicle-integration-standard.md).

## Development and contribution

Contributions are welcome for vehicle profiles, CAN research, status mappings,
local actions, UI work, installation and update tooling, tests, documentation,
and replay or qualification evidence.

Before proposing a maintained vehicle profile, use the structured workflow:

```bash
open-mmi-config vehicle-setup scaffold --help
open-mmi-config vehicle-setup capture --help
open-mmi-config vehicle-setup conform --root .
open-mmi-config vehicle-setup replay --root . <profile-id>
open-mmi-config vehicle-setup qualification report --root .
```

Raw discovery remains open: provisional names and uncertain observations are
welcome when uncertainty is explicit. Canonical event/status/action review begins
when a signal is proposed for a maintained or distributable profile.

See [Contributing](CONTRIBUTING.md) and the
[vehicle contribution workflow](docs/vehicle-contribution-workflow.md).

## Validation

The repository CI covers Python versions, unit tests, JavaScript module tests,
Playwright browser workflows, maintained-profile conformance, deterministic
profile replay, qualification records, capture tooling, generated documentation,
JSON and shell validation, package contents, installed console entry points,
and a live dashboard smoke/performance probe.

Run the main local checks with:

```bash
python3 -m unittest discover -s tests
node --test tests/js/*.test.js
python tools/generate_vehicle_action_docs.py --check
python tools/generate_vehicle_event_docs.py --check
python tools/generate_vehicle_status_docs.py --check
python tools/generate_vehicle_catalogue_docs.py --check
```

## Licence

GPL-3.0-only. See [LICENSE](LICENSE).

## Host power management

Optional, fail-closed automatic suspend from qualified CAN-bus silence is
documented in [`docs/power-management.md`](docs/power-management.md). The
standalone `open-mmi-powerd` service follows the active profile-selected
SocketCAN interface and remains disabled by default.
