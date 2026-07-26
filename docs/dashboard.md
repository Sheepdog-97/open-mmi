# Web dashboard

The Web dashboard is Open MMI's main user-facing interface. It consumes decoded,
human-readable vehicle state and remains receive-only with respect to vehicle
CAN. Fixed local Settings routes may update trusted Open MMI host configuration,
but the dashboard does not expose CAN transmission, actuator control, coding,
adaptation, arbitrary paths, or arbitrary commands.

## Open the dashboard

After a managed installation, open **Open MMI** from the desktop or application
menu. The launcher starts the dashboard service on demand, waits for
`/api/health`, and opens the managed browser.

For checkout development:

```bash
python3 ui/web_dashboard/server.py
```

The default address is:

```text
http://127.0.0.1:8765/
```

See [Desktop shell](desktop-shell.md) for launcher and browser ownership details.

## Navigation

The dashboard includes:

- **Home/Menu** — navigation and concise system context;
- **Drive** — speed, RPM, coolant, voltage, range, odometer, outside
  temperature, health, and footer tell-tales;
- **Media** — enabled local or external media sources;
- **Climate** — decoded HVAC-related state where available;
- **Vehicle** — doors, lighting, reverse, locks, and body state where available;
- **Settings** — display, media, System, Vehicle Setup, and Diagnostics;
- **Diagnostics** — decoded/raw detail, frontend activity, connection state,
  runtime/thermal data, and technical links.

Door-open and reverse-selected overlays are display-only alerts. Dismissal is
scoped to the current decoded state and resets when that state changes.

## Local preferences

Settings stores presentation preferences in browser local storage. Current
preferences include:

- speed, distance, and temperature units;
- normal, dim, and boost display modes;
- reduced animation;
- clock visibility, format, and optional date;
- diagnostics raw/debug visibility;
- reverse-overlay mode;
- press-and-hold frontend-only tell-tale test that always releases back to live vehicle state;
- enabled/default media sources and source-specific local preferences.

Presentation settings do not alter backend decoding or vehicle state.

The remembered Web/TUI launcher choice and open-after-login setting are
server-backed and live under **Settings → System**.

## Vehicle Setup

**Settings → Vehicle setup** provides maintained/custom profile and bindings
selection, one logical bus/interface selection, review, confirmed Apply, custom
copy/import/edit/lifecycle actions, and loaded-revision status.

See [Vehicle setup](vehicle-setup.md). Saving custom content is separate from
activation, and a fresh exact review is required before Apply.

## Software updates

**Settings → System → Software updates** shows installed source/policy state,
manual check results, readiness, coordinator progress, and fixed confirmed
prepare/install actions when available.

The browser cannot select a repository, branch, ref, path, command, channel, or
rollback target. At the current development checkpoint, confirmed browser
installation is intended for the recorded nightly source; no public beta or
stable release is presently published.

See [Manual administration](manual-administration.md) for terminal equivalents
and channel policy.

## Media

The Media page supports enabled sources through one shared player shell. Current
sources are Jellyfin, Internet Radio, USB, and Bluetooth.

Provider setup, privacy, network, filesystem, and BlueZ boundaries are documented
in [Media sources](media-sources.md).

## Diagnostics and recovery

The frontend uses a shared connection controller. When the local server becomes
unavailable it:

- stops overlapping/high-frequency requests;
- keeps the current page and last successful values mounted;
- shows a reconnecting state;
- pauses server-backed controls;
- retries `/api/health` with bounded backoff;
- resumes in place when the same build returns.

When the server build identity changes after an update, the frontend performs a
controlled one-shot reload. Active editing defers that reload and offers an
explicit **Reload now** action.

Settings → Diagnostics exposes:

- local dashboard connection and recovery counters;
- status fetch/render suppression counters;
- media layout activity;
- build identity/update-ready state;
- read-only thermal, frequency, load, power, battery, memory, and disk data where
  Linux exposes it;
- decoded vehicle state and optional raw/debug values.

See [Runtime hardening](runtime-hardening.md) and
[Troubleshooting](troubleshooting.md).

## Tell-tale test

Tell-tales can be tested from Settings without changing backend state. Checkout
development also supports query forcing:

```text
http://127.0.0.1:8765/?force=left,park,lights
http://127.0.0.1:8765/?force=hazard,bulb,highbeam
http://127.0.0.1:8765/?force=all
```

Keyboard forcing is available while the dashboard is focused:

| Shortcut | Test state |
|---|---|
| `Alt+1` / `Alt+2` | Left / right indicator |
| `Alt+3` | Hazards |
| `Alt+4` | Parking brake |
| `Alt+5` | Bulb fault |
| `Alt+6` / `Alt+7` / `Alt+8` | Side / dipped / high beam |
| `Alt+9` | Rear fog |
| `Alt+0` | Coolant/voltage warning test |
| `Alt+C` | Clear keyboard-forced states |

This is frontend-only and does not write vehicle status.

## Demo mode

Run the complete UI without a car:

```bash
python3 ui/web_dashboard/server.py --demo --demo-scenario traffic
```

See [Demo mode](demo-mode.md).

## Network boundary

The dashboard binds to `127.0.0.1` by default. Configuration routes require a
literal loopback client, loopback Host, and same-origin browser request. Binding
the general dashboard server to `0.0.0.0` should be done only behind an
appropriate host firewall or authenticated reverse proxy.

Provider secrets remain server-side. The browser receives redacted
configuration state rather than stored tokens or passwords.
