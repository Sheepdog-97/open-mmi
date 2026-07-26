# Open MMI Web dashboard developer reference

This directory contains the local dashboard server, provider modules, static
frontend modules, and installed assets.

User and operator documentation has moved to:

- [`../../docs/dashboard.md`](../../docs/dashboard.md) — pages, Settings,
  diagnostics, recovery, tell-tales, and demo behavior;
- [`../../docs/media-sources.md`](../../docs/media-sources.md) — Jellyfin, Radio,
  USB, and Bluetooth setup/security/privacy;
- [`../../docs/vehicle-setup.md`](../../docs/vehicle-setup.md) — current Vehicle
  Setup contract;
- [`../../docs/manual-administration.md`](../../docs/manual-administration.md) —
  service, update, and terminal operations.

The dashboard remains receive-only with respect to vehicle CAN. Fixed same-origin
Settings routes may update trusted host-side Open MMI configuration but cannot
supply arbitrary paths, commands, services, repositories, refs, or vehicle
control operations.

## Run from a checkout

```bash
python3 ui/web_dashboard/server.py
python3 ui/web_dashboard/server.py --demo
python3 ui/web_dashboard/server.py --demo --demo-scenario traffic
python3 ui/web_dashboard/server.py --host 127.0.0.1 --port 8765
```

The default address is `http://127.0.0.1:8765/`.

The server supports direct-script and module execution. The managed installation
normally starts it through `open-mmi-dashboard.service` and the desktop launcher.

## Server/provider boundary

`server.py` owns HTTP routing and delegates provider behavior to independently
importable modules:

- `jellyfin.py` — authentication, scoped catalogue access, bounded proxying;
- `radio.py` — Radio Browser catalogue, pinned validation/connection, redirects,
  stream proxying;
- `usb.py` — root discovery, opaque IDs, descriptor-safe browsing/range streaming;
- `bluetooth.py` — BlueZ status, opaque players, allowlisted controls;
- `system_settings.py` — local launcher/Jellyfin/Vehicle Setup/update routes;
- `runtime_diagnostics.py` — bounded local runtime/thermal/power state;
- `update_status.py` — installed source and policy/check state;
- `versioning.py` — server/frontend build identity and asset policy.

Provider modules do not import the dashboard handler. This keeps external
network, filesystem, D-Bus, and privileged coordinator boundaries acyclic and
independently testable.

## Frontend module ownership

Static modules load before `app.js` and have explicit state ownership:

- `api.js` — same-origin JSON requests and transport observers;
- `dashboard-connection.js` — reachability and bounded same-build recovery;
- `frontend-version.js` — build comparison and controlled reload;
- `preferences.js` — safe browser JSON persistence;
- `clock.js` — shared clock and clock preferences;
- `status.js` — DOM-independent status store and 200 ms polling lifecycle;
- `navigation.js` — page state, Home/Menu, pager, keyboard navigation;
- `overlays.js` — door/reverse detection and dismissal lifecycle;
- `vehicle.js` — decoded vehicle/climate view models and tell-tales;
- `media.js` — source preferences, source switching, Media settings;
- `media-jellyfin.js` — shared player shell and Jellyfin adapter;
- `media-radio.js` — privacy consent, Radio preferences and adapter;
- `media-usb.js` — USB browsing/navigation and adapter;
- `media-bluetooth.js` — Bluetooth polling/control and adapter;
- `system-settings.js` — System/Jellyfin/update panel rendering;
- `vehicle-setup-settings.js` — configured/draft/loaded state, custom operations,
  preview/review, confirmed Apply, coordinator polling, restoration feedback;
- `runtime-diagnostics.js` — Diagnostics-only system polling and derived state;
- `service-reminder.js` — inspection interval settings, live due-state evaluation, MIB-style detail notification and persistent acknowledgement;
- `trip-a.js` — host-backed Trip A, unit-aware display and conservative parked-time automatic reset;
- `trip-b.js` — independent host-backed long-term Trip B counter and manual reset;
- `app.js` — Settings shell, diagnostics rendering, advanced tell-tales, and
  remaining cross-cutting integration.

Modules resolve `window.fetch` and `window.localStorage` at call time to preserve
instrumentation and fail safely when browser storage is unavailable.

## Vehicle Setup routes

The read-only status route is:

```text
GET /api/system/vehicle-setup
```

Coordinator state is separate:

```text
GET /api/system/vehicle-setup/coordinator
```

Preview is fixed and non-mutating:

```text
POST /api/system/vehicle-setup/preview
```

It accepts maintained/custom identities and one bus/interface assignment. Its
`read_only: true` and `apply_available: false` fields prove that preview itself
cannot authorize mutation. The frontend combines the exact normalized target
with separate coordinator capability/lock state before enabling Apply.

Confirmed Apply uses:

```text
POST /api/system/vehicle-setup/apply
```

The body contains only the normalized target, current/target revision tokens, and
`confirm: true`. The frontend polls coordinator state through completion and
handles stale review, lifecycle contention, verified restoration, and blocked
unverified restoration.

Custom routes are fixed rather than path-shaped:

```text
POST /api/system/vehicle-custom/create
POST /api/system/vehicle-custom/load
POST /api/system/vehicle-custom/save
POST /api/system/vehicle-custom/manage
POST /api/system/vehicle-custom/import
```

## Service reminder routes

The inspection reminder is stored in the user configuration directory and is
independent of the vehicle cluster service interval:

```text
GET  /api/system/service-reminder
POST /api/system/service-reminder/settings
POST /api/system/service-reminder/reset
POST /api/system/service-reminder/acknowledge
```

Settings define independent distance and calendar-month intervals plus advance
warning thresholds. Reset requires explicit confirmation and the current decoded
`vehicle.odometer_km`; it records the host date and odometer in
`~/.config/open-mmi/service-reminder.json` with mode `0600`. The reminder becomes
due when either the distance or date deadline is reached. Due-soon and due alerts open a dedicated inspection detail view. Acknowledgement is stored against the exact reset, deadline and alert level, so a changed interval or transition from due-soon to due is shown again.

## Trip A routes

Trip A is independent of the instrument-cluster trip counter and uses the
confirmed decoded odometer:

```text
GET  /api/system/trip-a
POST /api/system/trip-a/reset
POST /api/system/trip-a/settings
POST /api/system/trip-a/observe
```

Reset requires explicit confirmation and the current `vehicle.odometer_km`. The
reset timestamp and odometer are written atomically to
`~/.config/open-mmi/trip-a.json` with mode `0600`. The dashboard subtracts that
saved odometer from the live value and follows the existing miles/kilometres
preference. Optional automatic reset records a low-frequency vehicle-present heartbeat and resets only after the configured parked interval when the odometer has not materially advanced during the inactive gap.

## Trip B routes

Trip B is an independent long-term counter and is never reset by the Trip A parked-time policy:

```text
GET  /api/system/trip-b
POST /api/system/trip-b/reset
```

Its reset is stored privately in `~/.config/open-mmi/trip-b.json`.

## Update routes

Local update state and readiness are exposed separately from execution:

```text
GET  /api/system/update-status
GET  /api/system/update-readiness
GET  /api/system/update-coordinator
POST /api/system/update-check
POST /api/system/update-prepare
POST /api/system/update-install
```

Prepare/install accept only fixed confirmation objects. The browser cannot select
source, channel, ref, path, command, or rollback. Persistent coordinator state
survives dashboard restart during installation.

## Local configuration request defence

Configuration requests require:

- loopback client address;
- loopback Host (`localhost` or loopback IP);
- same-origin Origin when present;
- `application/json`;
- bounded content length;
- unique JSON keys and finite numbers;
- route-specific exact key allowlists.

The dashboard process never runs `sudo`, `pkexec`, a shell, or `scripts/manage.sh`.
Privileged Vehicle Setup and update operations cross separate fixed AF_UNIX
coordinator protocols.

## Build identity and cache recovery

The server resolves one build identity from the installed build metadata,
environment, checkout, or package fallback. `GET /api/version` is `no-store`.
HTML includes that identity in metadata and local asset URLs.

Versioned assets are immutable; unversioned compatibility URLs revalidate. The
frontend checks after startup, connectivity recovery, visibility recovery, and at
a low visible-page cadence. A changed build triggers one controlled reload.
Active editing defers reload and presents **Reload now**. Session storage prevents
a loop for one failed target identity.

The first release introducing this controller can require one manual reload when
an older pre-controller page is already open; subsequent updates recover without
clearing the browser profile.

## Runtime efficiency and recovery

The status cadence remains visible and responsive while avoiding duplicate work:

- requests do not overlap and pause while hidden;
- unchanged vehicle values skip the full render path;
- Media fitting is event-driven and inactive off-page;
- tell-tale and media-key setup do not use permanent probe timers;
- diagnostics refreshes value nodes in place unless decoded structure changes.

A transport failure stops the fast poller, leaves mounted state intact, disables
server-backed controls, and retries `/api/health` with bounded backoff. Same-build
recovery resumes in place; changed-build recovery delegates to the version
controller.

## CSS structure

`styles.css` is an import-only compatibility manifest. The cascade-preserving
modules are:

- `styles-core.css`;
- `styles-media-layout.css`;
- `styles-shell.css`;
- `styles-media-sources.css`;
- `styles-diagnostics.css`;
- `styles-media-final.css`;
- `styles-clock.css`;
- `styles-runtime-hardening.css`;
- `styles-vehicle-setup.css`.

`tools/verify_css_split.py` verifies the protected legacy-module ordering and
concatenated bytes.

## Tell-tale assets

Assets live under:

```text
ui/web_dashboard/static/icons/telltales/
```

Keep licence/source attribution in:

```text
ui/web_dashboard/static/icons/telltales/NOTICE.md
```

Before adding or replacing an icon, record filename, source, author, and licence.

## Development checks

```bash
python3 -m py_compile \
  ui/web_dashboard/server.py \
  ui/web_dashboard/versioning.py \
  ui/web_dashboard/runtime_diagnostics.py \
  ui/web_dashboard/update_status.py \
  ui/web_dashboard/bluetooth.py \
  ui/web_dashboard/jellyfin.py \
  ui/web_dashboard/radio.py \
  ui/web_dashboard/usb.py

find ui/web_dashboard/static -maxdepth 1 -name '*.js' -print0 \
  | xargs -0 -n1 node --check

node --test tests/js/*.test.js
python3 -m unittest discover -s tests
python tools/verify_css_split.py
python3 ui/web_dashboard/server.py --demo --demo-scenario warnings
```

Browser coverage uses real HTML/CSS/JavaScript ordering with deterministic
same-origin fixtures. It covers navigation, status rendering, overlays, Settings,
media persistence, Vehicle Setup review/Apply/restoration, 800×480 containment,
portrait layout, and uncaught console/page errors.

## Design constraints

- Keep vehicle behavior receive-only.
- Keep secrets server-side and out of committed/browser storage.
- Keep provider boundaries independently importable and testable.
- Keep external addresses, filesystem roots, D-Bus objects, and coordinator
  operations opaque and allowlisted.
- Keep Drive/Climate/Vehicle stable while changing Media.
- Keep footer tell-tales in fixed slots.
- Keep feedback inline and usable at 800×480.
- Prefer small, reversible frontend modules over new shared global state.
