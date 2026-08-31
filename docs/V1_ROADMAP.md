# Open MMI V1 Roadmap

> **Planning record.** This file preserves the V1 planning history. Current user and operator behavior is documented in [`getting-started.md`](getting-started.md), [`vehicle-setup.md`](vehicle-setup.md), and [`manual-administration.md`](manual-administration.md).

Open MMI V1 is the first complete public shape of the project: a local, receive-only vehicle dashboard/MMI built from decoded vehicle state.

V1 is not a finished car operating system. It is the point where the project has a complete, usable loop:

```text
vehicle state → dashboard → media → settings → events/overlays → docs → compatibility testing path
```

## Current baseline

- Local web dashboard is now the main project face.
- Drive page shows live speed, RPM, coolant, voltage, outside temperature and lighting state; the range tile remains a placeholder because no maintained canonical fuel-range source is currently registered.
- Media page supports optional Jellyfin-backed local playback and browser media keys.
- Climate page shows decoded HVAC-related state.
- Vehicle/status page shows doors, handbrake, reverse, indicators, hazards and related status.
- Footer tell-tales are stable and use local icon assets.
- Demo mode can run without a vehicle.
- SEAT Leon Mk2 / 1P is the hardware-qualified reference; Superb II / 3T and Passat B6 / 3C are replay-qualified PQ46 candidates backed by bounded real-vehicle captures.
- Dashboard is receive-only from the vehicle side.

## Required before V1

### 1. Home/Menu navigation

Target layout:

```text
Media ← Home/Menu → Drive
```

Home/Menu should become the neutral landing page and provide quick access to:

- Drive
- Media
- Climate
- Settings / Display boost
- Diagnostics/Vehicle

The first implementation should keep Drive and Media internals mostly untouched. The goal is to improve navigation around the existing pages, not rewrite the working pages.

### 2. Settings

Initial settings should cover:

- units, such as mph/kmh and °C/°F
- display preferences
- press-and-hold tell-tale test
- raw/debug visibility
- Jellyfin status
- reverse assist placeholder

Settings that are private or security-sensitive must stay server-side or environment-based. Tokens and service credentials must not be stored in frontend code.

### 3. Diagnostics cleanup

Move raw/unfiltered values out of driver-facing pages where possible.

Examples:

- outside unfiltered temperature
- snapshot age
- decoded lighting mode
- missing fields
- raw status link

Vehicle may become a Diagnostics/Status page, or parts of it may feed the Home page and event overlays.

### 4. Vehicle event overlays

Add a reusable overlay system for important vehicle events.

Initial overlays:

- door-open popup with an easy dismiss action
- reverse selected placeholder

The overlay system should not replace the footer tell-tales. Dismissed overlays should return only when the underlying state changes or resets.

### 5. Reverse assist foundation

V1 does not need a working camera/PDC implementation, but it should pave the way for one.

Initial setting/placeholder options:

- Off
- PDC only, placeholder
- Camera only, placeholder
- Camera + PDC, placeholder

The dashboard must remain receive-only with respect to vehicle control.

### 6. Compatibility testing path

Document a temporary, read-only test process for other PQ35-family vehicles.

Wanted test vehicles:

- VW Golf Mk5
- Audi A3 8P
- Škoda Octavia 1Z
- Škoda Yeti

Compatibility claims should stay conservative until tested. SEAT Leon Mk2 / 1P remains the PQ35 hardware-qualified reference; the separately captured Superb II / 3T and Passat B6 / 3C are PQ46 replay-qualified candidates and do not extend PQ35 compatibility claims.

### 7. V1 release docs

Before tagging V1:

- README screenshots are current and privacy-respecting.
- Demo mode works from a clean clone.
- No secrets, backup files or local debug junk are committed.
- Compatibility limits are clearly stated.
- Known limitations are documented.
- Jellyfin/media setup is optional and token-safe.
- Tell-tale asset attributions are present.

## Suggested version path

```text
v0.95
  Current dashboard beta merged to main, with screenshots/docs good enough for public testers.

v0.99
  Roadmap feature-complete: Home/Menu, Settings, overlays, diagnostics cleanup and compatibility docs.

v1.0
  First complete Open MMI release.
```

## V1 non-goals

These are explicitly after V1 unless they fall out naturally:

- full rear camera/PDC implementation
- maps/Pure Maps integration
- multi-vehicle confirmed PQ35 support
- event-driven SSE/WebSocket status transport
- installer image
- full tablet power/amp install guide
- CAN transmit/control from the dashboard

## V1 roadmap checkpoint: Settings shell

The dashboard now has a Settings page shell for units, display preferences, diagnostics visibility, integrations, and reverse-assist placeholders. These controls are intentionally mostly non-functional at this stage; the first step is to establish where preferences live before wiring persistent behaviour.

### Settings shell scroll behaviour

The Settings page shell is scrollable inside the dashboard content area. This keeps the footer/tell-tales fixed while allowing units, display, diagnostics, integrations, and reverse-assist cards to remain accessible on tablet-sized screens.

### Settings option tree

The Settings page now uses a compact option-tree layout instead of a full page of cards. Categories stay visible on the left, and only the selected group is shown on the right. This keeps the dashboard shell fixed and avoids page-level scrolling on tablet screens.

### Diagnostics panel

Settings → Diagnostics now shows live decoded state such as status age, lighting mode, outside display/raw temperature, coolant, voltage, RPM, reverse, handbrake, and a link to `/api/status`. This is the first step toward moving raw/unfiltered values away from driver-facing pages.

### Door overlay

A global, dismissible door-open overlay now appears above any dashboard page when decoded door state reports an open door. Dismissal is tied to the current door-state signature: closing all doors resets it, and a changed door set can show the popup again. This creates the shared overlay foundation for future reverse/PDC/camera prompts.

### Reverse overlay foundation

A global reverse-selected overlay is now available as the shared foundation for later rear camera/PDC work. It is deliberately a placeholder in this pass: it appears when decoded reverse state is active, can be dismissed for the current reverse selection, and resets after leaving reverse. Future work can replace the placeholder panel with camera/PDC content and wire Settings → Reverse assist to choose the behaviour.
### Settings local wiring
Settings now persist simple dashboard preferences in browser localStorage without rebuilding the static settings panes on every status poll. Wired preferences currently cover speed/temperature display preference, diagnostics raw-value visibility, display dim/reduced-motion modes, and reverse-assist overlay mode.
### Settings wiring stability
Raw/debug visibility controls now live outside the diagnostics refresh target, so status updates can refresh live values without rebuilding the toggle itself.


- Settings Display tell-tale visual test: frontend-only icon strip for safe local verification.
- Settings Display tell-tale test is a press-and-hold control using existing footer icons only; releasing restores live state and no backend or CAN state changes are made.

<!-- OPENMMI_V1_STATUS_START -->
## Current V1 status

Completed for the current pre-V1 dashboard checkpoint:

- [x] Home/Menu navigation added.
- [x] Settings option tree added.
- [x] Stable Settings rendering, avoiding static panel rebuilds during live status refresh.
- [x] Local Settings wiring for units, display mode, reduced animation, reverse overlay mode and diagnostics raw/debug toggle.
- [x] Unit settings applied inside the status render loop for speed, distance and temperature fields.
- [x] Display boost mode added alongside normal and dim display modes.
- [x] Frontend-only tell-tale test renders through the existing footer icon path.
- [x] Diagnostics panel expanded for live decoded state inspection.
- [x] Door-open overlay foundation added.
- [x] Reverse-selected overlay foundation added.
- [x] Compatibility testing document and PQ35 compatibility report issue template started.

Still pending before calling V1 complete:

- [ ] Final public README/docs pass after all dashboard wording is reviewed.
- [ ] Release notes/changelog for the V1 checkpoint.
- [ ] Additional PQ35-family compatibility reports beyond the SEAT Leon Mk2 / 1P reference vehicle.
- [ ] PQ35-family validation using reversible, listen-only test setups.
- [ ] Keep the dashboard receive-only with respect to vehicle CAN; no transmit/control path from the web UI.
<!-- OPENMMI_V1_STATUS_END -->

## Recent dashboard cleanup

- Driver-facing pages cleaned up: Home no longer duplicates Drive data, unfiltered/raw values stay in Diagnostics.

- Driver-facing dashboard cleanup v2: Home summary noise and duplicate Diagnostics raw/debug controls cleaned up.

### Media source shell v1

- Added browser-local media source enable/default preferences in Settings.
- Media page has a source switcher for Jellyfin, Internet Radio, USB, and Bluetooth.
- Jellyfin can be disabled from Settings so the frontend does not poll/search Jellyfin while it is not the active enabled source.

### Media source disabled visibility v2

- Media page source switcher now shows enabled sources only. Disabled sources remain configurable from Settings → Media and do not appear as disabled placeholders on the Media page.
- Jellyfin assigned-user login: `OPEN_MMI_JELLYFIN_USERNAME` / `OPEN_MMI_JELLYFIN_PASSWORD` with token mode retained as an override.
