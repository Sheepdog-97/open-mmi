# Getting started

This guide is for a vehicle owner using an Open MMI maintained profile. It keeps
the normal path UI-driven after the initial installation. Terminal commands are
shown only where installation or recovery still requires them.

## Before you begin

Open MMI currently:

- receives CAN frames passively and does not transmit vehicle CAN messages;
- supports one active named CAN bus at a time;
- requires a Linux system with a supported SocketCAN adapter;
- requires the correct connection point and bitrate for the maintained profile;
- has a hardware-qualified SEAT Leon reference profile plus replay-qualified Superb and Passat candidates backed by bounded real-vehicle captures.

Decoded status is informational. It is not a replacement for OEM warnings,
diagnostics, or driver judgement.

## 1. Install Open MMI

From a source checkout:

```bash
git clone https://github.com/open-mmi/open-mmi.git
cd open-mmi
sudo ./scripts/manage.sh install
```

The installer:

- deploys the application to `/opt/open-mmi`;
- creates an isolated Python environment;
- installs the CAN and dashboard services;
- installs the restricted update and vehicle-configuration coordinators;
- creates application-menu and desktop entries;
- installs the `open-mmi-*` commands used for administration and recovery.

The install process may add your account to dedicated Open MMI authorization
groups. When prompted, log out and back in once. An already-running desktop
session cannot gain newly assigned supplementary group membership.

## 2. Open the application

Open **Open MMI** from the desktop or application menu.

The launcher starts the local dashboard service when necessary, waits for its
health endpoint, and opens the managed browser window. It remembers whether the
Web dashboard or terminal UI was selected.

The separate **Open MMI Interface Chooser** always offers the choice again. Use
it if the wrong interface was remembered or a touchscreen-only installation
needs to return from the terminal UI to the Web dashboard.

## 3. Connect the CAN adapter safely

Use the documented passive connection point for the maintained profile and an
appropriate SocketCAN adapter. Before applying a setup, confirm that the Linux
interface exists:

```bash
ip link show can0
```

The SEAT reference profile currently declares:

```text
logical bus: infotainment
interface: can0
bitrate: 100000
provisioning: udev
```

Do not assume those values are correct for an unqualified vehicle, different
adapter, different connection point, or different bus.

## 4. Select the maintained vehicle in Settings

Open:

```text
Settings → Vehicle setup
```

For the hardware-qualified SEAT reference profile, select:

```text
Vehicle profile: SEAT Leon Mk2 / 1P (PQ35) · Maintained
Bindings: Default · Maintained
CAN bus: infotainment
CAN adapter: can0
```

The page initially changes only its local draft. The running CAN service remains
unchanged until review and Apply are completed.

Choose **Review changes**. Check:

- selected profile and bindings;
- selected logical bus and adapter;
- expected bitrate;
- compatibility warnings;
- whether the adapter is present and up;
- the local files and services the coordinator will update.

Choose **Apply setup** and confirm the exact reviewed target. The coordinator
revalidates current and target revisions, writes the canonical configuration and
derived receive-side files, restarts the CAN service, and verifies what the
service loaded.

A disconnected adapter or sleeping vehicle is reported as runtime health. It
does not by itself mean the configuration failed to apply.

See [Vehicle setup](vehicle-setup.md) for the full state and recovery model.

## 5. Verify normal operation

The Vehicle Setup page should report that configured and loaded revisions match.
The Drive, Climate, Vehicle, and Diagnostics views should then populate as frames
are received and decoded.

When no frames appear:

1. confirm the adapter is connected;
2. confirm the interface is present and up;
3. confirm the bitrate and connection point;
4. confirm the vehicle bus is awake;
5. open Settings → Diagnostics;
6. use [Troubleshooting](troubleshooting.md) if the issue remains.

## 6. Configure normal desktop behavior

Open:

```text
Settings → System
```

Use the UI to choose the remembered Web/TUI interface and whether Open MMI opens
after graphical login. The background dashboard service normally starts on
demand; permanent service enablement is an advanced administrator setting.

Optional Jellyfin credentials can be configured under:

```text
Settings → Media → Jellyfin setup
```

Secrets remain server-side in a private local environment file and are not
returned to the browser.

## 7. Update an installed development build

Open:

```text
Settings → System → Software updates
```

The UI can check, prepare, and explicitly install a trusted candidate when all
readiness and authorization checks pass. It cannot select a repository, branch,
ref, command, or rollback target.

At this development checkpoint, browser installation is intended for the
installer-recorded **nightly** source. The policy model also supports `beta` and
`stable`, but there is not yet a public beta or stable release to install. This
section should be revised when release channels are published.

The workflow is manual and confirmed. There is no unattended scheduling.
Advanced update and channel commands are in
[Manual administration](manual-administration.md).

## When the terminal is still appropriate

The terminal remains supported for:

- first installation and complete uninstall;
- development-branch deployment;
- recovery when the dashboard cannot start;
- service and journal inspection;
- explicit update-channel administration;
- advanced interface or environment overrides;
- contributor and qualification tooling.

A maintained-vehicle owner should not need to edit systemd units, udev rules, or
configuration paths for the normal setup flow.
