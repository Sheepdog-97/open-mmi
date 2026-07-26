from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "ui" / "web_dashboard" / "static"
INDEX = STATIC / "index.html"
APP = STATIC / "app.js"
API = STATIC / "api.js"
DASHBOARD_CONNECTION = STATIC / "dashboard-connection.js"
FRONTEND_VERSION = STATIC / "frontend-version.js"
PREFERENCES = STATIC / "preferences.js"
SYSTEM_SETTINGS = STATIC / "system-settings.js"
SYSTEM_SETTINGS_STYLES = STATIC / "styles-system-settings.css"
VEHICLE_SETUP_SETTINGS = STATIC / "vehicle-setup-settings.js"
RUNTIME_DIAGNOSTICS = STATIC / "runtime-diagnostics.js"
SERVICE_REMINDER = STATIC / "service-reminder.js"
TRIP_A = STATIC / "trip-a.js"
TRIP_B = STATIC / "trip-b.js"
TRIP_SWITCHER = STATIC / "trip-switcher.js"
STATUS = STATIC / "status.js"
NAVIGATION = STATIC / "navigation.js"
OVERLAYS = STATIC / "overlays.js"
VEHICLE = STATIC / "vehicle.js"
MEDIA = STATIC / "media.js"
JELLYFIN_RECONNECTION = STATIC / "jellyfin-reconnection.js"
MEDIA_JELLYFIN = STATIC / "media-jellyfin.js"
MEDIA_RADIO = STATIC / "media-radio.js"
MEDIA_USB = STATIC / "media-usb.js"
MEDIA_BLUETOOTH = STATIC / "media-bluetooth.js"


class FrontendModuleBoundaryTests(unittest.TestCase):
    def test_modules_load_before_application(self):
        html = INDEX.read_text(encoding="utf-8")
        api_index = html.index('<script src="/api.js"></script>')
        dashboard_connection_index = html.index('<script src="/dashboard-connection.js"></script>')
        frontend_version_index = html.index('<script src="/frontend-version.js"></script>')
        preferences_index = html.index('<script src="/preferences.js"></script>')
        system_settings_index = html.index('<script src="/system-settings.js"></script>')
        vehicle_setup_settings_index = html.index('<script src="/vehicle-setup-settings.js"></script>')
        runtime_diagnostics_index = html.index('<script src="/runtime-diagnostics.js"></script>')
        service_reminder_index = html.index('<script src="/service-reminder.js"></script>')
        trip_a_index = html.index('<script src="/trip-a.js"></script>')
        trip_b_index = html.index('<script src="/trip-b.js"></script>')
        trip_switcher_index = html.index('<script src="/trip-switcher.js"></script>')
        status_index = html.index('<script src="/status.js"></script>')
        navigation_index = html.index('<script src="/navigation.js"></script>')
        overlays_index = html.index('<script src="/overlays.js"></script>')
        vehicle_index = html.index('<script src="/vehicle.js"></script>')
        media_index = html.index('<script src="/media.js"></script>')
        jellyfin_reconnection_index = html.index('<script src="/jellyfin-reconnection.js"></script>')
        jellyfin_index = html.index('<script src="/media-jellyfin.js"></script>')
        radio_index = html.index('<script src="/media-radio.js"></script>')
        usb_index = html.index('<script src="/media-usb.js"></script>')
        bluetooth_index = html.index('<script src="/media-bluetooth.js"></script>')
        app_index = html.index('<script src="/app.js"></script>')
        self.assertLess(api_index, dashboard_connection_index)
        self.assertLess(dashboard_connection_index, frontend_version_index)
        self.assertLess(frontend_version_index, preferences_index)
        self.assertLess(preferences_index, system_settings_index)
        self.assertLess(system_settings_index, vehicle_setup_settings_index)
        self.assertLess(vehicle_setup_settings_index, runtime_diagnostics_index)
        self.assertLess(runtime_diagnostics_index, service_reminder_index)
        self.assertLess(service_reminder_index, trip_a_index)
        self.assertLess(trip_a_index, trip_b_index)
        self.assertLess(trip_b_index, trip_switcher_index)
        self.assertLess(trip_switcher_index, status_index)
        self.assertLess(status_index, navigation_index)
        self.assertLess(navigation_index, overlays_index)
        self.assertLess(overlays_index, vehicle_index)
        self.assertLess(vehicle_index, media_index)
        self.assertLess(media_index, jellyfin_reconnection_index)
        self.assertLess(jellyfin_reconnection_index, jellyfin_index)
        self.assertLess(jellyfin_index, radio_index)
        self.assertLess(radio_index, usb_index)
        self.assertLess(usb_index, bluetooth_index)
        self.assertLess(bluetooth_index, app_index)

    def test_application_uses_frontend_modules(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("window.openMmiApi", source)
        self.assertIn("window.openMmiDashboardConnection", source)
        self.assertIn("window.openMmiPreferences", source)
        self.assertIn("window.openMmiStatus", source)
        self.assertIn("window.openMmiSystemSettings", source)
        self.assertIn("window.openMmiVehicleSetupSettings", source)
        self.assertIn("window.openMmiRuntimeDiagnostics", source)
        self.assertIn("window.openMmiServiceReminder", source)
        self.assertIn("window.openMmiTripA", source)
        self.assertIn("window.openMmiTripB", source)
        self.assertIn("window.openMmiTripSwitcher", source)
        self.assertIn("window.openMmiNavigation", source)
        self.assertIn("window.openMmiOverlays", source)
        self.assertIn("window.openMmiVehicle", source)
        self.assertIn("window.openMmiMediaShell", source)
        self.assertIn("window.openMmiJellyfinMedia", source)
        self.assertIn("window.openMmiRadioMedia", source)
        self.assertIn("window.openMmiUsbMediaController", source)
        self.assertIn("window.openMmiBluetoothMediaController", source)
        self.assertIn("openMmiStatusClient.createStore()", source)
        self.assertIn("openMmiDashboardConnectionClient.createController({ api: openMmiApiClient })", source)
        self.assertIn("openMmiNavigationClient.createController()", source)
        self.assertIn("openMmiOverlaysClient.createController()", source)
        self.assertIn("openMmiVehicleClient.createRenderer({ preferences: openMmiPrefs })", source)
        self.assertIn("openMmiMediaClient.createController({ preferences: openMmiPrefs })", source)
        self.assertIn("openMmiVehicleSetupSettingsClient.install({ api: openMmiApiClient })", source)
        self.assertIn("openMmiRadioMediaClient.installPrivacy({ preferences: openMmiPrefs })", source)
        self.assertIn("openMmiJellyfinMediaClient.installController({", source)
        self.assertIn("openMmiRadioMediaClient.installController({ preferences: openMmiPrefs })", source)
        self.assertIn("openMmiJellyfinPlayer.boot()", source)
        self.assertIn("openMmiUsbMediaClient.installController()", source)
        self.assertIn("openMmiBluetoothMediaClient.installController({ api: openMmiApiClient })", source)
        self.assertIn("window.setPage = (index) => openMmiNavigationController.setPage(index);", source)
        self.assertIn("openMmiStatusClient.createPoller({", source)
        self.assertNotIn('openMmiApiClient.getJson("/api/status"', source)
        self.assertNotIn("localStorage.", source)

    def test_modules_are_browser_and_commonjs_compatible(self):
        api = API.read_text(encoding="utf-8")
        dashboard_connection = DASHBOARD_CONNECTION.read_text(encoding="utf-8")
        frontend_version = FRONTEND_VERSION.read_text(encoding="utf-8")
        preferences = PREFERENCES.read_text(encoding="utf-8")
        system_settings = SYSTEM_SETTINGS.read_text(encoding="utf-8")
        vehicle_setup_settings = VEHICLE_SETUP_SETTINGS.read_text(encoding="utf-8")
        runtime_diagnostics = RUNTIME_DIAGNOSTICS.read_text(encoding="utf-8")
        status = STATUS.read_text(encoding="utf-8")
        navigation = NAVIGATION.read_text(encoding="utf-8")
        overlays = OVERLAYS.read_text(encoding="utf-8")
        vehicle = VEHICLE.read_text(encoding="utf-8")
        media = MEDIA.read_text(encoding="utf-8")
        jellyfin_reconnection = JELLYFIN_RECONNECTION.read_text(encoding="utf-8")
        media_jellyfin = MEDIA_JELLYFIN.read_text(encoding="utf-8")
        media_radio = MEDIA_RADIO.read_text(encoding="utf-8")
        media_usb = MEDIA_USB.read_text(encoding="utf-8")
        media_bluetooth = MEDIA_BLUETOOTH.read_text(encoding="utf-8")
        for source, global_name in (
            (api, "openMmiApi"),
            (dashboard_connection, "openMmiDashboardConnection"),
            (frontend_version, "openMmiFrontendVersion"),
            (preferences, "openMmiPreferences"),
            (system_settings, "openMmiSystemSettings"),
            (vehicle_setup_settings, "openMmiVehicleSetupSettings"),
            (runtime_diagnostics, "openMmiRuntimeDiagnostics"),
            (SERVICE_REMINDER.read_text(encoding="utf-8"), "openMmiServiceReminder"),
            (TRIP_A.read_text(encoding="utf-8"), "openMmiTripA"),
            (TRIP_B.read_text(encoding="utf-8"), "openMmiTripB"),
            (TRIP_SWITCHER.read_text(encoding="utf-8"), "openMmiTripSwitcher"),
            (status, "openMmiStatus"),
            (navigation, "openMmiNavigation"),
            (overlays, "openMmiOverlays"),
            (vehicle, "openMmiVehicle"),
            (media, "openMmiMediaShell"),
            (jellyfin_reconnection, "openMmiJellyfinReconnect"),
            (media_jellyfin, "openMmiJellyfinMedia"),
            (media_radio, "openMmiRadioMedia"),
            (media_usb, "openMmiUsbMediaController"),
            (media_bluetooth, "openMmiBluetoothMediaController"),
        ):
            self.assertIn("typeof module === \"object\"", source)
            self.assertIn(f"root.{global_name}", source)
            if global_name not in {
                "openMmiDashboardConnection",
                "openMmiFrontendVersion",
                "openMmiVehicleSetupSettings",
                "openMmiRuntimeDiagnostics",
                "openMmiServiceReminder",
                "openMmiTripA",
                "openMmiTripB",
                "openMmiTripSwitcher",
                "openMmiMediaShell",
                "openMmiJellyfinReconnect",
                "openMmiJellyfinMedia",
                "openMmiRadioMedia",
                "openMmiUsbMediaController",
                "openMmiBluetoothMediaController",
            }:
                self.assertNotIn("document.", source)



    def test_system_settings_owns_fixed_managed_update_flow(self):
        source = SYSTEM_SETTINGS.read_text(encoding="utf-8")
        self.assertIn('api.getJson("/api/system/update-status"', source)
        self.assertIn('api.getJson("/api/system/update-readiness"', source)
        self.assertIn('api.getJson("/api/system/update-coordinator"', source)
        self.assertIn('api.postJson("/api/system/update-check", { confirm: true }', source)
        self.assertIn('runUpdateAction("/api/system/update-prepare", ["prepared"]', source)
        self.assertIn('runUpdateAction("/api/system/update-install", ["complete"]', source)
        self.assertIn("function scheduleTransactionPoll()", source)
        self.assertIn('documentRef.addEventListener("visibilitychange", scheduleTransactionPoll)', source)
        self.assertIn('data-testid="system-update-check"', source)
        self.assertIn('data-testid="system-update-prepare"', source)
        self.assertIn('data-testid="system-update-install"', source)
        self.assertIn('"remote-different": "remote differs"', source)
        self.assertIn('"downgrade-blocked": "downgrade blocked"', source)
        self.assertIn('Nightly follows the newest available Open MMI build', source)
        self.assertIn("Install update", source)
        self.assertIn("roll back automatically if validation fails", source)
        self.assertNotIn('data-openmmi-update-channel', source)
        self.assertNotIn("repository_path", source)

    def test_vehicle_setup_settings_owns_reviewed_apply_workflow(self):
        source = VEHICLE_SETUP_SETTINGS.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn('const ENDPOINT = "/api/system/vehicle-setup";', source)
        self.assertIn('const PREVIEW_ENDPOINT = "/api/system/vehicle-setup/preview";', source)
        self.assertIn('const APPLY_ENDPOINT = "/api/system/vehicle-setup/apply";', source)
        self.assertIn('const COPY_ENDPOINT = "/api/system/vehicle-custom/create";', source)
        self.assertIn('const LOAD_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/load";', source)
        self.assertIn('const SAVE_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/save";', source)
        self.assertIn('const MANAGE_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/manage";', source)
        self.assertIn('const IMPORT_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/import";', source)
        self.assertIn(
            'const COORDINATOR_ENDPOINT = "/api/system/vehicle-setup/coordinator";',
            source,
        )
        self.assertIn("api.getJson(ENDPOINT", source)
        self.assertIn("api.postJson(PREVIEW_ENDPOINT, request", source)
        self.assertIn("api.postJson(APPLY_ENDPOINT, body", source)
        self.assertIn("api.postJson(COPY_ENDPOINT", source)
        self.assertIn("api.postJson(LOAD_CUSTOM_ENDPOINT", source)
        self.assertIn("api.postJson(SAVE_CUSTOM_ENDPOINT", source)
        self.assertIn("api.postJson(MANAGE_CUSTOM_ENDPOINT", source)
        self.assertIn("api.postJson(IMPORT_CUSTOM_ENDPOINT", source)
        self.assertIn("Use maintained ${label} as template", source)
        self.assertIn("Stored in your user catalogue", source)
        self.assertIn('data-openmmi-vehicle-custom-edit', source)
        self.assertIn('data-openmmi-vehicle-custom-manage="duplicate"', source)
        self.assertIn('data-openmmi-vehicle-custom-manage="rename"', source)
        self.assertIn('data-openmmi-vehicle-custom-manage="delete"', source)
        self.assertIn("expected_revision", source)
        self.assertIn("Lifecycle changes do not apply or restart", source)
        self.assertIn("Import ${label} JSON", source)
        self.assertIn("selected as an unapplied draft", source)
        self.assertNotIn("Edit maintained", source)
        self.assertNotIn("Delete maintained", source)
        self.assertIn("expected_configuration_revision", source)
        self.assertIn("target_configuration_revision", source)
        self.assertIn("confirm: true", source)
        self.assertIn("windowRef.confirm", source)
        self.assertIn('code === "stale-preview"', source)
        self.assertIn('code === "apply-failed-restored"', source)
        self.assertIn('code === "apply-failed-restore-unverified"', source)
        self.assertNotIn("localStorage", source)
        self.assertNotIn("/restore", source)
        self.assertIn('activeSection() !== "vehicle-setup"', source)
        self.assertIn('data-testid="vehicle-setup-profile"', source)
        self.assertIn('data-testid="vehicle-setup-bindings"', source)
        self.assertIn('data-testid="vehicle-setup-preview"', source)
        self.assertIn('data-openmmi-vehicle-setup-apply="true"', source)
        self.assertIn('data-openmmi-settings-section="vehicle-setup"', app)

    def test_settings_sidebar_uses_a_bounded_drill_down_tree(self):
        app = APP.read_text(encoding="utf-8")
        styles = (ROOT / "ui/web_dashboard/static/styles-shell.css").read_text(encoding="utf-8")
        self.assertIn('aria-label="Settings tree" role="tree"', app)
        self.assertIn('data-openmmi-settings-group="general"', app)
        self.assertIn('data-openmmi-settings-group="vehicle"', app)
        self.assertIn('data-openmmi-settings-group="system"', app)
        self.assertIn('data-openmmi-settings-group="advanced"', app)
        self.assertIn('data-openmmi-settings-parent="vehicle" data-openmmi-settings-section="trip"', app)
        self.assertIn('Back to settings categories from ${label}', app)
        self.assertIn('.openmmi-settings-tree.is-drilled > .openmmi-settings-tree-group:not(.is-expanded)', styles)
        self.assertIn('.openmmi-settings-sidebar-card p {\n    display: none;', styles)

    def test_service_reminder_preserves_editing_state_and_uses_settings_controls(self):
        source = SERVICE_REMINDER.read_text(encoding="utf-8")
        update_start = source.index("      update(payload = {}) {")
        update_end = source.index("      refresh,", update_start)
        update_block = source[update_start:update_end]
        self.assertIn("renderIndicator();", update_block)
        self.assertIn("updatePanelReadouts();", update_block)
        self.assertNotIn("render();", update_block)
        self.assertIn('class="openmmi-setting-pill is-selected" data-openmmi-service-save', source)
        self.assertIn('class="openmmi-setting-pill" data-openmmi-service-reset', source)
        self.assertIn('event.stopImmediatePropagation?.();', source)
        styles = SYSTEM_SETTINGS_STYLES.read_text(encoding="utf-8")
        self.assertIn('.openmmi-service-form > .openmmi-service-toggle', styles)
        self.assertIn('justify-content: flex-start;', styles)
        self.assertIn('width: fit-content;', styles)
        self.assertIn('.openmmi-service-toggle input[type="checkbox"]', styles)
        self.assertIn('width: 1rem;', styles)
        self.assertIn('margin: 0;', styles)
        self.assertIn('padding: 0;', styles)


    def test_trip_a_uses_host_reset_and_live_odometer(self):
        source = TRIP_A.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('const ENDPOINT = "/api/system/trip-a";', source)
        self.assertIn('const RESET_ENDPOINT = "/api/system/trip-a/reset";', source)
        self.assertIn('const SETTINGS_ENDPOINT = "/api/system/trip-a/settings";', source)
        self.assertIn('const OBSERVE_ENDPOINT = "/api/system/trip-a/observe";', source)
        self.assertIn('payload?.state?.vehicle?.odometer_km', source)
        self.assertIn('data-openmmi-trip-a-reset', source)
        self.assertIn('openmmi-config-actions openmmi-service-actions', source)
        self.assertIn('event.stopImmediatePropagation?.();', source)
        self.assertIn('data-openmmi-settings-section="trip"', app)
        self.assertIn('data-openmmi-trip-a-panel="true"', app)
        self.assertEqual(html.count('data-openmmi-trip-a>'), 2)
        trip_b = TRIP_B.read_text(encoding="utf-8")
        self.assertIn('const ENDPOINT = "/api/system/trip-b";', trip_b)
        self.assertIn('data-openmmi-trip-b-reset', trip_b)
        self.assertIn('data-openmmi-trip-b-panel="true"', app)
        self.assertEqual(html.count('data-openmmi-trip-b>'), 2)
        switcher = TRIP_SWITCHER.read_text(encoding="utf-8")
        self.assertIn('const STORAGE_KEY = "openmmi.trip.display.v1";', switcher)
        self.assertIn('data-openmmi-trip-next', html)
        self.assertEqual(html.count('data-openmmi-trip-card'), 2)
        self.assertEqual(html.count('data-openmmi-trip-next'), 2)
        self.assertNotIn('<div class="label">Trip B</div>', html)

    def test_application_does_not_override_module_owned_control_state(self):
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn(
            'panel?.querySelectorAll?.(".openmmi-setting-pill").forEach((pill) => { pill.disabled = false; });',
            source,
        )

    def test_runtime_diagnostics_module_owns_visibility_aware_polling(self):
        source = RUNTIME_DIAGNOSTICS.read_text(encoding="utf-8")
        self.assertIn('const ENDPOINT = "/api/system/diagnostics/runtime";', source)
        self.assertIn('activeSection() === "diagnostics"', source)
        self.assertIn('!documentRef?.hidden', source)
        self.assertIn('scheduler.setTimeout(poll', source)
        self.assertIn('Reported power values are battery-side driver readings, not charger capacity.', source)


    def test_dashboard_connection_module_owns_shared_recovery(self):
        source = DASHBOARD_CONNECTION.read_text(encoding="utf-8")
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('id="openMmiDashboardConnectionNotice"', html)
        self.assertIn('const HEALTH_PATH = "/api/health";', source)
        self.assertIn('DEFAULT_RETRY_DELAYS_MS', source)
        self.assertIn('api.subscribeConnection(onApiConnection)', source)
        self.assertIn('"openmmi:dashboardconnected"', source)
        self.assertIn('documentRef?.hidden', source)
        self.assertIn('documentRef.body.dataset.openmmiDashboardConnection', source)

    def test_frontend_version_module_owns_cache_recovery(self):
        source = FRONTEND_VERSION.read_text(encoding="utf-8")
        html = INDEX.read_text(encoding="utf-8")
        self.assertIn('meta name="open-mmi-frontend-id"', html)
        self.assertIn('id="openMmiUpdateNotice"', html)
        self.assertIn('const VERSION_PATH = "/api/version";', source)
        self.assertIn('windowRef.location.replace(targetUrl(target))', source)
        self.assertIn('"openmmi:dashboardconnected"', source)
        self.assertIn('documentRef?.hidden', source)

    def test_status_module_owns_polling_and_shared_state(self):
        source = STATUS.read_text(encoding="utf-8")
        self.assertIn('const DEFAULT_STATUS_PATH = "/api/status";', source)
        self.assertIn("const DEFAULT_STATUS_INTERVAL_MS = 200;", source)
        self.assertIn("function createStore(initialPayload = null)", source)
        self.assertIn("function createPoller(options = {})", source)
        self.assertIn("scheduler.setInterval(fetchStatus, intervalMs)", source)
        self.assertNotIn("document.", source)


    def test_navigation_module_owns_page_state_and_home_menu(self):
        source = NAVIGATION.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn("function createController(options = {})", source)
        self.assertIn("function showPageById(id, title, quickIndex = HOME_INDEX)", source)
        self.assertIn("function ensureHomePage()", source)
        self.assertIn('windowRef.addEventListener("keydown", keyHandler)', source)
        self.assertNotIn("function ensureHomePage()", app)
        self.assertNotIn("const QUICK_PAGES =", app)

    def test_overlay_module_owns_detection_and_visibility_lifecycle(self):
        source = OVERLAYS.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn("function collectOpenDoors(payload)", source)
        self.assertIn("function reverseSelected(payload)", source)
        self.assertIn("function reduceDoorOverlay(state, openDoors)", source)
        self.assertIn("function reduceReverseOverlay(state, active)", source)
        self.assertIn("function vehiclePageActive(documentRef)", source)
        self.assertIn('windowRef.addEventListener("openmmi:pagechange", pageChangeHandler)', source)
        self.assertIn('overlay.id = "openMmiVehicleOverlay"', source)
        self.assertIn('overlay.id = "openMmiReverseOverlay"', source)
        self.assertNotIn("function collectOpenDoors(payload)", app)
        self.assertNotIn("function reverseSelected(payload)", app)


    def test_vehicle_module_owns_vehicle_and_climate_rendering(self):
        source = VEHICLE.read_text(encoding="utf-8")
        app = APP.read_text(encoding="utf-8")
        self.assertIn("function buildViewModel(payload = {}, settings = DEFAULT_SETTINGS)", source)
        self.assertIn("function createRenderer(options = {})", source)
        self.assertIn("recirculation: climate.recirculation_active", source)
        self.assertIn("function updateTach(rpm)", source)
        self.assertIn("function updateCoolantGauge(payload)", source)
        self.assertNotIn("function updateTach(rpm)", app)
        self.assertNotIn("function updateDoor(name, value)", app)
        self.assertNotIn("function openMmiFormatTempFromC", app)
        self.assertNotIn('{ key: "hazard", label: "Hazard warning"', app)
        self.assertNotIn('setSlot(strip, "hazard"', app)

    def test_media_modules_own_all_frontend_controllers(self):
        app = APP.read_text(encoding="utf-8")
        media = MEDIA.read_text(encoding="utf-8")
        reconnect = JELLYFIN_RECONNECTION.read_text(encoding="utf-8")
        jellyfin = MEDIA_JELLYFIN.read_text(encoding="utf-8")
        radio = MEDIA_RADIO.read_text(encoding="utf-8")
        usb = MEDIA_USB.read_text(encoding="utf-8")
        bluetooth = MEDIA_BLUETOOTH.read_text(encoding="utf-8")
        self.assertIn("function createController(options = {})", media)
        self.assertIn("function activeSourceFromPreferences(prefs)", media)
        self.assertIn("function createController(options = {})", reconnect)
        self.assertIn("function installController(options = {})", jellyfin)
        self.assertIn("function installPrivacy(options = {})", radio)
        self.assertIn("function installController(options = {})", radio)
        self.assertIn("function installController(options = {})", usb)
        self.assertIn("function installController(options = {})", bluetooth)
        self.assertIn("DEFAULT_RETRY_DELAYS_MS", reconnect)
        self.assertIn('/api/jellyfin/status', jellyfin)
        self.assertIn('/api/bluetooth/status', bluetooth)
        self.assertIn('id: "radio"', radio)
        self.assertIn('id: "usb"', usb)
        self.assertNotIn("Open MMI Jellyfin real Bootstrap media v5 start", app)
        self.assertNotIn("Open MMI Bluetooth media source start", app)
        self.assertNotIn("Open MMI media source shell v1 start", app)
        self.assertNotIn("Open MMI media source adapters/radio start", app)
        self.assertNotIn("Open MMI USB media source start", app)

    def test_diagnostics_uses_canonical_profile_paths_and_lists_decoded_state(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("climate.outside_temp_regulation_c", source)
        self.assertIn("climate.outside_temp_unfiltered_c", source)
        self.assertIn("electrical.supply_voltage_v", source)
        self.assertIn("engine.speed_rpm", source)
        self.assertIn("function flattenDiagnosticState", source)
        self.assertIn("Decoded profile values", source)

    def test_api_reads_fetch_at_call_time_for_instrumentation(self):
        source = API.read_text(encoding="utf-8")
        self.assertIn("const fetchImpl = root && root.fetch", source)
        self.assertNotIn("const fetchImpl = root.fetch.bind", source)


if __name__ == "__main__":
    unittest.main()
