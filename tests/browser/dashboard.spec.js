"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const ROOT = path.resolve(__dirname, "../..");
const STATIC = path.join(ROOT, "ui", "web_dashboard", "static");
const SETTINGS_KEY = "openmmi.dashboard.settings.v1";

const CSS_FILES = [
  "styles-core.css",
  "styles-media-layout.css",
  "styles-shell.css",
  "styles-media-sources.css",
  "styles-diagnostics.css",
  "styles-media-final.css",
  "styles-clock.css",
  "styles-system-settings.css",
  "styles-vehicle-setup.css",
  "styles-runtime-hardening.css",
];

function indexAssets() {
  const index = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const scripts = [...index.matchAll(/<script\s+src="\/([^"?]+\.js)"\s*><\/script>/g)].map((match) => match[1]);
  const documentHtml = index
    .replace(/<link\b[^>]*>/g, "")
    .replace(/<script\s+src="[^"]+"\s*><\/script>/g, "")
    .replace(
      "</head>",
      `<style data-openmmi-browser-test>${CSS_FILES.map((name) => fs.readFileSync(path.join(STATIC, name), "utf8")).join("\n")}</style></head>`,
    );
  return { documentHtml, scripts };
}

const ASSETS = indexAssets();

function basePayload(overrides = {}) {
  const payload = {
    updated_at: Date.now() / 1000,
    health: { status: "live", age_seconds: 0.1 },
    state: {
      vehicle: {
        speed_kmh: 64,
        odometer_km: 12345,
        handbrake: false,
        reverse: false,
      },
      engine: { speed_rpm: 2150, coolant_temp_c: 91 },
      electrical: { supply_voltage_v: 13.9, terminal30_voltage_v: 13.9 },
      climate: {
        outside_temp_regulation_c: 12.5,
        outside_temp_unfiltered_c: 12.8,
        blower_load_percent: 42,
        rear_window_heater_requested: false,
        recirculation_active: true,
        compressor_active: true,
      },
      lighting: {
        mode: "Auto",
        lights_on: true,
        left_indicator: false,
        right_indicator: false,
        hazards: false,
        bulb_out: false,
        dimmer_percent: 65,
      },
      doors: {
        front_left: false,
        front_right: false,
        rear_left: false,
        rear_right: false,
        boot: false,
        bonnet: false,
        any_open: false,
      },
      fuel: { level_l: 42 },
    },
  };

  if (overrides.health) Object.assign(payload.health, overrides.health);
  if (overrides.state) {
    for (const [section, values] of Object.entries(overrides.state)) {
      payload.state[section] = Object.assign({}, payload.state[section] || {}, values);
    }
  }
  return payload;
}

function captureRuntimeFailures(page) {
  const failures = [];
  page.on("pageerror", (error) => failures.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") failures.push(`console: ${message.text()}`);
  });
  return failures;
}

async function expectNoRuntimeFailures(failures) {
  expect(failures, failures.join("\n")).toEqual([]);
}

async function loadDashboard(page, options = {}) {
  const payload = options.payload || basePayload();
  const storage = options.storage || {};
  const bluetoothPayload = options.bluetoothPayload || { available: false, players: [] };
  const versionPayload = options.versionPayload || {
    api_version: 1,
    build_id: "__OPEN_MMI_FRONTEND_ID__",
    frontend_id: "__OPEN_MMI_FRONTEND_ID__",
    reload_supported: true,
  };
  const jellyfinStatusPayload = options.jellyfinStatusPayload || {
    configured: false,
    status: "unconfigured",
    connection_state: "configuration-missing",
    retryable: false,
    state_label: "not configured",
    subtitle: "Jellyfin is not configured",
  };
  const jellyfinSearchPayload = options.jellyfinSearchPayload || {
    configured: false,
    connection_state: "configuration-missing",
    retryable: false,
    items: [],
    error: "Jellyfin is not configured",
  };
  const runtimeDiagnosticsPayload = options.runtimeDiagnosticsPayload || {
    api_version: 1,
    sampled_at: "2026-07-16T22:29:54+00:00",
    cpu: {
      online_count: 4,
      current_mhz: [400, 399, 400, 400],
      average_mhz: 399.8,
      current_min_mhz: 399,
      current_max_mhz: 400,
      minimum_mhz: 400,
      maximum_mhz: 3500,
      governors: ["powersave"],
      load_1m: 6.21,
      load_high: true,
      near_minimum: true,
      cpus: [0, 1, 2, 3].map((index) => ({
        cpu: `cpu${index}`, current_mhz: index === 1 ? 399 : 400, minimum_mhz: 400, maximum_mhz: 3500, governor: "powersave",
      })),
      intel_pstate: { status: "active", no_turbo: 0, min_perf_pct: 11, max_perf_pct: 100 },
    },
    thermal: {
      summary: "thermal-limit-active",
      selected_zone: "GEN4",
      temperature_c: 52.5,
      relevant_trip: { temperature_c: 48.05, types: ["active", "passive"], margin_c: -4.45 },
      zones: [{
        zone: "thermal_zone1", type: "GEN4", temperature_c: 52.5, state: "thermal-limit-active",
        relevant_trip: { temperature_c: 48.05, types: ["active", "passive"], margin_c: -4.45 },
        trips: [{ type: "active", temperature_c: 48.05 }, { type: "passive", temperature_c: 48.05 }],
      }],
      cooling_devices: [{ device: "cooling_device8", type: "TCC Offset", current_state: 10, maximum_state: 63 }],
    },
    power: {
      ac_online: true, battery_status: "Not charging", capacity_percent: 65, energy_wh: 21.13, charging_state: "not-charging",
      supplies: [{ name: "ADP1", type: "Mains", online: true }, { name: "BAT1", type: "Battery", status: "Not charging", capacity_percent: 65, reported_power_w: 53.946 }],
    },
  };
  const systemPayload = options.systemPayload || {
    local_only: true,
    launcher: {
      default_ui: "web",
      open_at_login: true,
      service_active: true,
      service_enabled: true,
      dashboard_reachable: true,
    },
    jellyfin: {
      configured: false,
      url: "",
      auth_mode: "",
      username: "",
      user_id: "",
      library_id: "",
      password_configured: false,
      token_configured: false,
      insecure_tls: false,
      allow_global: false,
      restart_required: false,
      path: "/home/test/.config/open-mmi/dashboard.env",
    },
  };

  const serviceReminderPayload = options.serviceReminderPayload || {
    ok: true,
    api_version: 2,
    configured: false,
    path: "/home/test/.config/open-mmi/service-reminder.json",
    settings: {
      enabled: true,
      distance_interval_km: 16093.44,
      time_interval_months: 12,
      warning_distance_km: 1609.344,
      warning_days: 30,
    },
    reset: { reset_date: null, odometer_km: null },
    next_due: { date: null, odometer_km: null },
    acknowledgement: { acknowledged_at: null, level: null, reset_date: null, reset_odometer_km: null, due_date: null, due_odometer_km: null },
  };

  const tripAPayload = options.tripAPayload || {
    ok: true,
    api_version: 2,
    configured: false,
    path: "/home/test/.config/open-mmi/trip-a.json",
    settings: { auto_reset_hours: 0 },
    reset: { reset_at: null, odometer_km: null },
    activity: { last_active_at: null, odometer_km: null },
  };

  const tripBPayload = options.tripBPayload || {
    ok: true,
    api_version: 1,
    configured: false,
    path: "/home/test/.config/open-mmi/trip-b.json",
    reset: { reset_at: null, odometer_km: null },
  };

  const vehicleSetupPayload = options.vehicleSetupPayload || {
    api_version: 1,
    read_only: true,
    runtime_mode: "single",
    active: {
      state: "ready",
      errors: [],
      vehicle: { source: "maintained", id: "seat-leon-1p-pq35", revision: "sha256:profile" },
      bindings: { source: "maintained", id: "default", revision: "sha256:bindings" },
      active_bus: "comfort",
      interface: "can0",
      interface_present: false,
      configuration_revision: "sha256:configuration",
      loaded: {
        api_version: 1,
        state: "ready",
        errors: [],
        vehicle: { source: "maintained", id: "seat-leon-1p-pq35", revision: "sha256:profile" },
        bindings: { source: "maintained", id: "default", revision: "sha256:bindings" },
        active_bus: "comfort",
        interface: "can0",
        updated_at: 1,
      },
    },
    catalogue: {
      development_mode: false,
      issues: [],
      profiles: [
        {
          source: "maintained", id: "seat-leon-1p-pq35", display_name: "SEAT Leon 1P / Mk2 (PQ35)", valid: true,
          revision: "sha256:profile", default_bus: "comfort",
          buses: [{ name: "comfort", interface: "can0", bitrate: 100000, provisioning: "udev" }],
          validation: { valid: true, errors: [], warnings: [] },
        },
        {
          source: "custom", id: "my-seat", display_name: "My Seat", valid: true,
          revision: "sha256:custom-profile", default_bus: "comfort",
          buses: [{ name: "comfort", interface: "can1", bitrate: 100000, provisioning: "manual" }],
          validation: { valid: true, errors: [], warnings: [] },
        },
      ],
      bindings: [
        {
          source: "maintained", id: "default", display_name: "Default", valid: true,
          revision: "sha256:bindings", binding_count: 12,
          validation: { valid: true, errors: [], warnings: [] },
        },
        {
          source: "custom", id: "my-controls", display_name: "My controls", valid: true,
          revision: "sha256:custom-bindings", binding_count: 11,
          validation: { valid: true, errors: [], warnings: [] },
        },
      ],
    },
    compatibility: {
      emitted_and_bound: ["play_pause", "volume_up"], emitted_unbound: [],
      bound_unemitted: ["stop_playback"], duplicate_emitted: [],
    },
    interfaces: [],
  };

  if (options.vehicleSetupPendingRevisions) {
    const revisions = options.vehicleSetupPendingRevisions;
    vehicleSetupPayload.active.vehicle = { source: "custom", id: "my-seat", revision: revisions.configuredProfile };
    vehicleSetupPayload.active.bindings = { source: "custom", id: "my-controls", revision: revisions.configuredBindings };
    vehicleSetupPayload.active.interface = "can1";
    vehicleSetupPayload.active.loaded = {
      api_version: 1,
      state: "ready",
      errors: [],
      vehicle: { source: "custom", id: "my-seat", revision: revisions.loadedProfile },
      bindings: { source: "custom", id: "my-controls", revision: revisions.loadedBindings },
      active_bus: "comfort",
      interface: "can1",
      updated_at: 1,
    };
    vehicleSetupPayload.catalogue.profiles.find((entry) => entry.id === "my-seat").revision = revisions.configuredProfile;
    vehicleSetupPayload.catalogue.bindings.find((entry) => entry.id === "my-controls").revision = revisions.configuredBindings;
  }

  const vehicleSetupPreviewPayload = options.vehicleSetupPreviewPayload || {
    api_version: 1,
    read_only: true,
    apply_available: false,
    state: "ready",
    expected_configuration_revision: "sha256:configuration",
    target_configuration_revision: "sha256:target",
    target: {
      vehicle: { source: "custom", id: "my-seat", revision: "sha256:custom-profile" },
      bindings: { source: "custom", id: "my-controls", revision: "sha256:custom-bindings" },
      runtime: { mode: "single", active_bus: "comfort", buses: { comfort: { interface: "can1" } } },
    },
    active_bus: { name: "comfort", interface: "can1", profile_interface: "can1", bitrate: 100000, provisioning: "manual" },
    interface: { name: "can1", present: false, up: false, configured_bitrate: null },
    compatibility: {
      emitted_and_bound: ["play_pause", "volume_up"], emitted_unbound: [],
      bound_unemitted: ["stop_playback"], duplicate_emitted: [],
    },
    validation: {
      valid: true,
      errors: [],
      warnings: [{ code: "bindings-unused", message: "1 binding is not emitted by the profile" }],
    },
    coordinator: {
      previewed: true,
      read_only: true,
      locks: { configuration_active: false, lifecycle_active: false, update_active: false },
      apply_blocked: false,
    },
    plan: {
      changes: [
        {
          field: "vehicle",
          from: { source: "maintained", id: "seat-leon-1p-pq35", revision: "sha256:profile" },
          to: { source: "custom", id: "my-seat", revision: "sha256:custom-profile" },
        },
        { field: "bindings", from: { source: "maintained", id: "default" }, to: { source: "custom", id: "my-controls" } },
        { field: "interface", from: "can0", to: "can1" },
      ],
      effects: {
        write_canonical_configuration: true,
        write_systemd_runtime: true,
        write_udev_rules: true,
        reload_user_manager: true,
        reload_udev: true,
        restart_can_service: true,
      },
    },
  };

  const vehicleSetupCoordinatorPayload = options.vehicleSetupCoordinatorPayload || {
    ok: true,
    api_version: 1,
    read_only: false,
    preview_enabled: true,
    apply_enabled: true,
    restore_enabled: false,
    locks: { configuration_active: false, lifecycle_active: false, update_active: false },
    state: {
      state: "idle", stage: "idle", error: "", restoration_attempted: false,
      restoration_verified: false,
    },
  };
  const vehicleSetupApplyPayload = options.vehicleSetupApplyPayload || {
    ok: true,
    api_version: 1,
    action: "apply",
    state: {
      state: "complete", stage: "complete", error: "", restoration_attempted: false,
      restoration_verified: false, transaction_id: "configuration-browser-test",
      target: { interface: "can1" },
    },
  };

  const updateStatusPayload = options.updateStatusPayload || {
    api_version: 1,
    read_only: true,
    installed: { managed: true, version: "v1-runtime-hardening-42-gabc1234", commit: "abc1234def56" },
    channel: "nightly",
    policy: { state: "configured", implicit: false, updated_at: "2026-07-18T12:00:00+00:00" },
    source: {
      configured: true,
      state: "ready",
      clean: true,
      branch: "v1-update-management",
      expected_branch: "v1-update-management",
      upstream: "origin/v1-update-management",
      commit: "abc1234def56",
      trusted: true,
    },
    update: {
      state: "not-checked",
      checked_at: null,
      available_version: "",
      available_commit: "",
      remote_differs: null,
      update_available: null,
      error: "",
    },
    readiness: { state: "ready", blockers: [] },
  };
  const updateCheckPayload = options.updateCheckPayload || {
    ...updateStatusPayload,
    update: {
      state: "update-available",
      checked_at: "2026-07-18T14:32:00+00:00",
      available_version: "def5678abc90",
      available_commit: "def5678abc901234567890123456789012345678",
      remote_differs: true,
      update_available: true,
      error: "",
    },
  };
  const updateReadinessPayload = options.updateReadinessPayload || {
    api_version: 1,
    state: "ready",
    install_allowed: true,
    blockers: [],
    checks: [],
  };
  const updateCoordinatorPayload = options.updateCoordinatorPayload || {
    api_version: 1,
    ok: true,
    preparation_enabled: true,
    execution_enabled: true,
    installation_enabled: true,
    state: {
      state: "idle", stage: "idle", target_version: "", candidate_commit: "",
      transaction_id: null, error: "",
    },
  };

  await page.setContent(ASSETS.documentHtml, { waitUntil: "domcontentloaded" });
  await page.evaluate(({ initialPayload, initialStorage, initialBluetoothPayload, initialSystemPayload, initialServiceReminderPayload, initialTripAPayload, initialTripBPayload, initialVehicleSetupPayload, initialVehicleSetupPreviewPayload, initialVehicleSetupCoordinatorPayload, initialVehicleSetupApplyPayload, initialUpdateStatusPayload, initialUpdateCheckPayload, initialUpdateReadinessPayload, initialUpdateCoordinatorPayload, initialVersionPayload, initialJellyfinStatusPayload, initialJellyfinSearchPayload, initialRuntimeDiagnosticsPayload, runtimeDiagnosticsIntervalMs, dashboardRetryDelaysMs }) => {
    const values = Object.assign({}, initialStorage);
    const localStorageMock = {
      get length() { return Object.keys(values).length; },
      key(index) { return Object.keys(values)[index] ?? null; },
      getItem(key) { return Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : null; },
      setItem(key, value) { values[String(key)] = String(value); },
      removeItem(key) { delete values[String(key)]; },
      clear() { Object.keys(values).forEach((key) => delete values[key]); },
    };
    Object.defineProperty(window, "localStorage", { configurable: true, value: localStorageMock });
    window.__openMmiBrowserStorage = values;
    window.__openMmiStatusFixture = initialPayload;
    window.__openMmiBluetoothFixture = initialBluetoothPayload;
    window.__openMmiSystemFixture = initialSystemPayload;
    window.__openMmiServiceReminderFixture = initialServiceReminderPayload;
    window.__openMmiTripAFixture = initialTripAPayload;
    window.__openMmiTripBFixture = initialTripBPayload;
    window.__openMmiVehicleSetupFixture = initialVehicleSetupPayload;
    window.__openMmiVehicleSetupPreviewFixture = initialVehicleSetupPreviewPayload;
    window.__openMmiVehicleSetupCoordinatorFixture = initialVehicleSetupCoordinatorPayload;
    window.__openMmiVehicleSetupApplyFixture = initialVehicleSetupApplyPayload;
    window.__openMmiUpdateStatusFixture = initialUpdateStatusPayload;
    window.__openMmiUpdateCheckFixture = initialUpdateCheckPayload;
    window.__openMmiUpdateReadinessFixture = initialUpdateReadinessPayload;
    window.__openMmiUpdateCoordinatorFixture = initialUpdateCoordinatorPayload;
    window.__openMmiVersionFixture = initialVersionPayload;
    window.__openMmiJellyfinStatusFixture = initialJellyfinStatusPayload;
    window.__openMmiJellyfinSearchFixture = initialJellyfinSearchPayload;
    window.__openMmiRuntimeDiagnosticsFixture = initialRuntimeDiagnosticsPayload;
    window.__openMmiRuntimeDiagnosticsRequests = 0;
    window.__openMmiRuntimeDiagnosticsIntervalMs = runtimeDiagnosticsIntervalMs;
    window.__openMmiDashboardRetryDelaysMs = dashboardRetryDelaysMs;
    window.__openMmiDashboardOnline = true;
    window.__openMmiDashboardHealthRequests = 0;
    window.__openMmiDashboardStatusRequests = 0;
    window.__openMmiDashboardVersionRequests = 0;
    window.__openMmiVehicleSetupRequests = 0;
    window.__openMmiVehicleSetupPreviewRequests = 0;
    window.__openMmiVehicleSetupPreviewBodies = [];
    window.__openMmiVehicleSetupCoordinatorRequests = 0;
    window.__openMmiVehicleSetupApplyRequests = 0;
    window.__openMmiVehicleSetupApplyBodies = [];
    window.__openMmiVehicleSetupCopyRequests = 0;
    window.__openMmiVehicleSetupCopyBodies = [];
    window.__openMmiVehicleSetupLoadRequests = 0;
    window.__openMmiVehicleSetupLoadBodies = [];
    window.__openMmiVehicleSetupSaveRequests = 0;
    window.__openMmiVehicleSetupSaveBodies = [];
    window.__openMmiVehicleSetupManageRequests = 0;
    window.__openMmiVehicleSetupManageBodies = [];
    window.__openMmiVehicleSetupImportRequests = 0;
    window.__openMmiVehicleSetupImportBodies = [];
    window.__openMmiVehicleSetupCustomContents = {
      "profile:my-seat": '{\n  "default_bus": "comfort",\n  "can_buses": {"comfort": {"interface": "can1", "bitrate": 100000}},\n  "rules": [],\n  "presence": [],\n  "status": []\n}\n',
      "bindings:my-controls": '{}\n',
    };
    window.__openMmiUpdateStatusRequests = 0;
    window.__openMmiUpdateCheckRequests = 0;
    window.__openMmiUpdateCoordinatorRequests = 0;
    window.__openMmiUpdatePrepareRequests = 0;
    window.__openMmiUpdateInstallRequests = 0;
    window.__openMmiJellyfinStatusRequests = 0;
    window.__openMmiJellyfinSearchRequests = 0;

    const json = (body, status = 200) => new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
    window.fetch = async (input, init = {}) => {
      const url = String(input instanceof Request ? input.url : input);
      if (url.includes("/api/") && window.__openMmiDashboardOnline === false) {
        throw new TypeError("dashboard offline");
      }
      if (url.includes("/api/version")) {
        window.__openMmiDashboardVersionRequests += 1;
        return json(window.__openMmiVersionFixture);
      }
      if (url.includes("/api/status")) {
        window.__openMmiDashboardStatusRequests += 1;
        return json(window.__openMmiStatusFixture);
      }
      if (url.includes("/api/health")) {
        window.__openMmiDashboardHealthRequests += 1;
        return json({ ok: true });
      }
      if (url.includes("/api/system/diagnostics/runtime")) {
        window.__openMmiRuntimeDiagnosticsRequests += 1;
        return json(window.__openMmiRuntimeDiagnosticsFixture);
      }
      if (url.includes("/api/system/vehicle-custom/load")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupLoadRequests += 1;
        window.__openMmiVehicleSetupLoadBodies.push(body);
        const collection = body.kind === "profile" ? "profiles" : "bindings";
        const entry = window.__openMmiVehicleSetupFixture.catalogue[collection].find((item) =>
          item.source === "custom" && item.id === body.id
        );
        const content = window.__openMmiVehicleSetupCustomContents[`${body.kind}:${body.id}`];
        if (!entry || typeof content !== "string") {
          return json({ ok: false, error: "Custom item not found" }, 400);
        }
        return json({
          ok: true, api_version: 1, action: "load-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: entry.revision },
          content,
          validation: { valid: true, errors: [], warnings: [] },
        });
      }
      if (url.includes("/api/system/vehicle-custom/save")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupSaveRequests += 1;
        window.__openMmiVehicleSetupSaveBodies.push(body);
        const collection = body.kind === "profile" ? "profiles" : "bindings";
        const entry = window.__openMmiVehicleSetupFixture.catalogue[collection].find((item) =>
          item.source === "custom" && item.id === body.id
        );
        if (!entry || entry.revision !== body.expected_revision) {
          return json({ ok: false, code: "custom-stale", error: "Custom item changed" }, 409);
        }
        window.__openMmiVehicleSetupCustomContents[`${body.kind}:${body.id}`] = body.content;
        entry.revision = "sha256:saved-custom";
        return json({
          ok: true, api_version: 1, action: "save-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: entry.revision },
          validation: { valid: true, errors: [], warnings: [] },
          applied: false,
        });
      }
      if (url.includes("/api/system/vehicle-custom/manage")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupManageRequests += 1;
        window.__openMmiVehicleSetupManageBodies.push(body);
        const collectionName = body.kind === "profile" ? "profiles" : "bindings";
        const collection = window.__openMmiVehicleSetupFixture.catalogue[collectionName];
        const index = collection.findIndex((entry) => entry.source === "custom" && entry.id === body.id);
        if (index < 0 || collection[index].revision !== body.expected_revision) {
          return json({ ok: false, code: "custom-stale", error: "Custom item changed" }, 409);
        }
        const source = collection[index];
        const sourceKey = `${body.kind}:${body.id}`;
        if (body.action === "delete") {
          collection.splice(index, 1);
          delete window.__openMmiVehicleSetupCustomContents[sourceKey];
          return json({
            ok: true, api_version: 1, action: "manage-custom-item", operation: "delete", kind: body.kind,
            deleted: { source: "custom", id: body.id, revision: source.revision }, applied: false,
          });
        }
        const managed = {
          ...source,
          id: body.new_id,
          display_name: body.new_id.split(/[-_]/).map((part) => part ? part[0].toUpperCase() + part.slice(1) : "").join(" "),
        };
        const destinationKey = `${body.kind}:${body.new_id}`;
        window.__openMmiVehicleSetupCustomContents[destinationKey] = window.__openMmiVehicleSetupCustomContents[sourceKey];
        if (body.action === "rename") {
          collection.splice(index, 1, managed);
          delete window.__openMmiVehicleSetupCustomContents[sourceKey];
        } else {
          collection.push(managed);
        }
        return json({
          ok: true, api_version: 1, action: "manage-custom-item", operation: body.action, kind: body.kind,
          source: { source: "custom", id: body.id, revision: source.revision },
          custom: { source: "custom", id: body.new_id, revision: source.revision }, applied: false,
        });
      }
      if (url.includes("/api/system/vehicle-custom/import")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupImportRequests += 1;
        window.__openMmiVehicleSetupImportBodies.push(body);
        const collection = body.kind === "profile" ? "profiles" : "bindings";
        const custom = {
          source: "custom",
          id: body.id,
          display_name: body.id.split(/[-_]/).map((part) => part ? part[0].toUpperCase() + part.slice(1) : "").join(" "),
          valid: true,
          revision: "sha256:imported-custom",
          validation: { valid: true, errors: [], warnings: [] },
        };
        if (body.kind === "profile") {
          custom.default_bus = "comfort";
          custom.buses = [{ name: "comfort", interface: "can0", bitrate: 100000 }];
        } else {
          custom.binding_count = 1;
        }
        window.__openMmiVehicleSetupFixture.catalogue[collection].push(custom);
        window.__openMmiVehicleSetupCustomContents[`${body.kind}:${body.id}`] = body.content;
        return json({
          ok: true, api_version: 1, action: "import-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: custom.revision },
          validation: { valid: true, errors: [], warnings: [] }, applied: false,
        });
      }
      if (url.includes("/api/system/vehicle-custom/create")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupCopyRequests += 1;
        window.__openMmiVehicleSetupCopyBodies.push(body);
        const collection = body.kind === "profile" ? "profiles" : "bindings";
        const template = window.__openMmiVehicleSetupFixture.catalogue[collection].find((entry) =>
          entry.source === "maintained" && entry.id === body.template_id
        );
        const custom = {
          ...template,
          source: "custom",
          id: body.id,
          display_name: body.id.split(/[-_]/).map((part) => part ? part[0].toUpperCase() + part.slice(1) : "").join(" "),
          revision: body.template_revision,
        };
        window.__openMmiVehicleSetupFixture.catalogue[collection].push(custom);
        return json({
          ok: true,
          api_version: 1,
          action: "copy-maintained-template",
          kind: body.kind,
          template: { source: "maintained", id: body.template_id, revision: body.template_revision },
          custom: { source: "custom", id: body.id, revision: body.template_revision },
        });
      }
      if (url.includes("/api/system/vehicle-setup/apply")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupApplyRequests += 1;
        window.__openMmiVehicleSetupApplyBodies.push(body);
        window.__openMmiVehicleSetupCoordinatorFixture = {
          ...window.__openMmiVehicleSetupCoordinatorFixture,
          state: window.__openMmiVehicleSetupApplyFixture.state,
        };
        return json(window.__openMmiVehicleSetupApplyFixture);
      }
      if (url.includes("/api/system/vehicle-setup/preview")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiVehicleSetupPreviewRequests += 1;
        window.__openMmiVehicleSetupPreviewBodies.push(body);
        return json(window.__openMmiVehicleSetupPreviewFixture);
      }
      if (url.includes("/api/system/vehicle-setup/coordinator")) {
        window.__openMmiVehicleSetupCoordinatorRequests += 1;
        return json(window.__openMmiVehicleSetupCoordinatorFixture);
      }
      if (url.endsWith("/api/system/vehicle-setup")) {
        window.__openMmiVehicleSetupRequests += 1;
        return json(window.__openMmiVehicleSetupFixture);
      }
      if (url.includes("/api/system/update-status")) {
        window.__openMmiUpdateStatusRequests += 1;
        return json(window.__openMmiUpdateStatusFixture);
      }
      if (url.includes("/api/system/update-check")) {
        window.__openMmiUpdateCheckRequests += 1;
        window.__openMmiUpdateStatusFixture = window.__openMmiUpdateCheckFixture;
        return json(window.__openMmiUpdateCheckFixture);
      }
      if (url.includes("/api/system/update-readiness")) {
        return json(window.__openMmiUpdateReadinessFixture);
      }
      if (url.includes("/api/system/update-coordinator")) {
        window.__openMmiUpdateCoordinatorRequests += 1;
        return json(window.__openMmiUpdateCoordinatorFixture);
      }
      if (url.includes("/api/system/update-prepare")) {
        const body = JSON.parse(init.body || "{}");
        if (JSON.stringify(body) !== JSON.stringify({ confirm: true })) return json({ ok: false, error: "Invalid preparation request" }, 400);
        window.__openMmiUpdatePrepareRequests += 1;
        window.__openMmiUpdateCoordinatorFixture = {
          ...window.__openMmiUpdateCoordinatorFixture,
          state: {
            ...window.__openMmiUpdateCoordinatorFixture.state,
            state: "prepared",
            stage: "prepared",
            target_version: "v1-runtime-hardening-43-gdef5678",
            candidate_commit: "def5678abc901234567890123456789012345678",
            transaction_id: "prepare-0123456789abcdef0123456789abcdef",
          },
        };
        return json(window.__openMmiUpdateCoordinatorFixture);
      }
      if (url.includes("/api/system/update-install")) {
        const body = JSON.parse(init.body || "{}");
        if (JSON.stringify(body) !== JSON.stringify({ confirm: true })) return json({ ok: false, error: "Invalid installation request" }, 400);
        window.__openMmiUpdateInstallRequests += 1;
        window.__openMmiUpdateCoordinatorFixture = {
          ...window.__openMmiUpdateCoordinatorFixture,
          state: { ...window.__openMmiUpdateCoordinatorFixture.state, state: "complete", stage: "complete" },
        };
        window.__openMmiUpdateStatusFixture = {
          ...window.__openMmiUpdateStatusFixture,
          installed: {
            managed: true,
            version: "v1-runtime-hardening-43-gdef5678",
            commit: "def5678abc90",
          },
          update: {
            state: "not-checked", checked_at: null, available_version: "", available_commit: "",
            remote_differs: null, update_available: null, error: "",
          },
        };
        return json(window.__openMmiUpdateCoordinatorFixture);
      }
      if (url.includes("/api/system/service-reminder/settings")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiServiceReminderFixture = {
          ...window.__openMmiServiceReminderFixture,
          settings: { ...window.__openMmiServiceReminderFixture.settings, ...body },
        };
        return json(window.__openMmiServiceReminderFixture);
      }
      if (url.includes("/api/system/service-reminder/acknowledge")) {
        const body = JSON.parse(init.body || "{}");
        const fixture = window.__openMmiServiceReminderFixture;
        window.__openMmiServiceReminderFixture = {
          ...fixture,
          acknowledgement: {
            acknowledged_at: "2026-07-26T20:30:00+00:00", level: body.level,
            reset_date: fixture.reset.reset_date, reset_odometer_km: fixture.reset.odometer_km,
            due_date: fixture.next_due.date, due_odometer_km: fixture.next_due.odometer_km,
          },
        };
        return json(window.__openMmiServiceReminderFixture);
      }
      if (url.includes("/api/system/service-reminder/reset")) {
        const body = JSON.parse(init.body || "{}");
        const resetDate = "2026-07-26";
        const settings = window.__openMmiServiceReminderFixture.settings;
        window.__openMmiServiceReminderFixture = {
          ...window.__openMmiServiceReminderFixture,
          configured: true,
          reset: { reset_date: resetDate, odometer_km: body.odometer_km },
          next_due: { date: "2027-07-26", odometer_km: body.odometer_km + settings.distance_interval_km },
        };
        return json(window.__openMmiServiceReminderFixture);
      }
      if (url.includes("/api/system/service-reminder")) return json(window.__openMmiServiceReminderFixture);
      if (url.includes("/api/system/trip-a/settings")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiTripAFixture = { ...window.__openMmiTripAFixture, settings: body };
        return json(window.__openMmiTripAFixture);
      }
      if (url.includes("/api/system/trip-a/observe")) return json({ ...window.__openMmiTripAFixture, auto_reset: false });
      if (url.includes("/api/system/trip-a/reset")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiTripAFixture = {
          ...window.__openMmiTripAFixture,
          configured: true,
          reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: body.odometer_km },
        };
        return json(window.__openMmiTripAFixture);
      }
      if (url.includes("/api/system/trip-a")) return json(window.__openMmiTripAFixture);
      if (url.includes("/api/system/trip-b/reset")) {
        const body = JSON.parse(init.body || "{}");
        window.__openMmiTripBFixture = {
          ...window.__openMmiTripBFixture, configured: true,
          reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: body.odometer_km },
        };
        return json(window.__openMmiTripBFixture);
      }
      if (url.includes("/api/system/trip-b")) return json(window.__openMmiTripBFixture);
      if (url.includes("/api/system/settings")) return json(window.__openMmiSystemFixture);
      if (url.includes("/api/system/launcher")) {
        const body = JSON.parse(init.body || "{}");
        Object.assign(window.__openMmiSystemFixture.launcher, body);
        return json({ ok: true, launcher: window.__openMmiSystemFixture.launcher });
      }
      if (url.includes("/api/system/jellyfin/test")) return json({ ok: true, test: { connected: true, server_name: "Test Jellyfin" } });
      if (url.endsWith("/api/system/jellyfin") && init.method === "POST") {
        const body = JSON.parse(init.body || "{}");
        Object.assign(window.__openMmiSystemFixture.jellyfin, {
          configured: true,
          url: body.url,
          auth_mode: body.auth_mode,
          username: body.username,
          password_configured: body.auth_mode === "username",
          token_configured: body.auth_mode === "token",
          restart_required: true,
        });
        return json({ ok: true, jellyfin: window.__openMmiSystemFixture.jellyfin, test: { connected: true } });
      }
      if (url.includes("/api/system/jellyfin/clear")) {
        Object.assign(window.__openMmiSystemFixture.jellyfin, { configured: false, restart_required: true });
        return json({ ok: true, jellyfin: window.__openMmiSystemFixture.jellyfin });
      }
      if (url.includes("/api/system/dashboard/restart")) {
        window.__openMmiSystemFixture.jellyfin.restart_required = false;
        return json({ ok: true, restarting: true });
      }
      if (url.includes("/api/jellyfin/status")) {
        window.__openMmiJellyfinStatusRequests += 1;
        return json(window.__openMmiJellyfinStatusFixture);
      }
      if (url.includes("/api/jellyfin/search")) {
        window.__openMmiJellyfinSearchRequests += 1;
        return json(window.__openMmiJellyfinSearchFixture);
      }
      if (url.includes("/api/jellyfin/")) return json({ configured: false, connection_state: "configuration-missing", retryable: false, items: [] });
      if (url.includes("/api/bluetooth/status")) return json(window.__openMmiBluetoothFixture);
      if (url.includes("/api/bluetooth/control")) return json({ ok: false, error: "Unavailable" }, 409);
      if (url.includes("/api/usb/status")) return json({ available: false, roots: [] });
      if (url.includes("/api/usb/")) return json({ available: false, entries: [] });
      if (url.includes("/api/radio/")) return json({ available: false, stations: [], countries: [], languages: [] });
      return json({ ok: true, method: init.method || "GET" });
    };

    if (window.HTMLMediaElement) {
      window.HTMLMediaElement.prototype.play = function play() {
        Object.defineProperty(this, "paused", { configurable: true, value: false });
        return Promise.resolve();
      };
      window.HTMLMediaElement.prototype.pause = function pause() {
        Object.defineProperty(this, "paused", { configurable: true, value: true });
      };
    }
  }, {
    initialPayload: payload,
    initialStorage: storage,
    initialBluetoothPayload: bluetoothPayload,
    initialSystemPayload: systemPayload,
    initialServiceReminderPayload: serviceReminderPayload,
    initialTripAPayload: tripAPayload,
    initialTripBPayload: tripBPayload,
    initialVehicleSetupPayload: vehicleSetupPayload,
    initialVehicleSetupPreviewPayload: vehicleSetupPreviewPayload,
    initialVehicleSetupCoordinatorPayload: vehicleSetupCoordinatorPayload,
    initialVehicleSetupApplyPayload: vehicleSetupApplyPayload,
    initialUpdateStatusPayload: updateStatusPayload,
    initialUpdateCheckPayload: updateCheckPayload,
    initialUpdateReadinessPayload: updateReadinessPayload,
    initialUpdateCoordinatorPayload: updateCoordinatorPayload,
    initialVersionPayload: versionPayload,
    initialJellyfinStatusPayload: jellyfinStatusPayload,
    initialJellyfinSearchPayload: jellyfinSearchPayload,
    initialRuntimeDiagnosticsPayload: runtimeDiagnosticsPayload,
    runtimeDiagnosticsIntervalMs: options.runtimeDiagnosticsIntervalMs || 60,
    dashboardRetryDelaysMs: options.dashboardRetryDelaysMs || [50, 75, 100],
  });

  if (options.focusBeforeReady) {
    await page.evaluate(() => {
      const input = document.createElement("input");
      input.id = "openMmiUnsavedTestInput";
      input.value = "unsaved";
      input.dataset.openmmiDirty = "true";
      document.body.appendChild(input);
      input.focus();
    });
  }
  for (const script of ASSETS.scripts) {
    const content = `${fs.readFileSync(path.join(STATIC, script), "utf8")}
//# sourceURL=${script}`;
    await page.addScriptTag({ content });
  }
  await page.evaluate(() => document.dispatchEvent(new Event("DOMContentLoaded", { bubbles: true })));
  await expect(page.locator("#pageHome")).toHaveClass(/active/);
  return {
    async setPayload(nextPayload) {
      await page.evaluate((next) => { window.__openMmiStatusFixture = next; }, nextPayload);
    },
    async setBluetoothPayload(nextPayload) {
      await page.evaluate((next) => { window.__openMmiBluetoothFixture = next; }, nextPayload);
    },
    async setJellyfinStatus(nextPayload) {
      await page.evaluate((next) => { window.__openMmiJellyfinStatusFixture = next; }, nextPayload);
    },
    async setJellyfinSearch(nextPayload) {
      await page.evaluate((next) => { window.__openMmiJellyfinSearchFixture = next; }, nextPayload);
    },
    async setRuntimeDiagnostics(nextPayload) {
      await page.evaluate((next) => { window.__openMmiRuntimeDiagnosticsFixture = next; }, nextPayload);
    },
    async runtimeDiagnosticsRequests() {
      return page.evaluate(() => window.__openMmiRuntimeDiagnosticsRequests);
    },
    async vehicleSetupRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupRequests);
    },
    async vehicleSetupPreviewRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupPreviewRequests);
    },
    async vehicleSetupPreviewBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupPreviewBodies);
    },
    async vehicleSetupCoordinatorRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupCoordinatorRequests);
    },
    async vehicleSetupApplyRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupApplyRequests);
    },
    async vehicleSetupApplyBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupApplyBodies);
    },
    async vehicleSetupCopyRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupCopyRequests);
    },
    async vehicleSetupCopyBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupCopyBodies);
    },
    async vehicleSetupLoadBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupLoadBodies);
    },
    async vehicleSetupSaveBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupSaveBodies);
    },
    async vehicleSetupManageRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupManageRequests);
    },
    async vehicleSetupManageBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupManageBodies);
    },
    async vehicleSetupImportRequests() {
      return page.evaluate(() => window.__openMmiVehicleSetupImportRequests);
    },
    async vehicleSetupImportBodies() {
      return page.evaluate(() => window.__openMmiVehicleSetupImportBodies);
    },
    async setDashboardOnline(online) {
      await page.evaluate((value) => { window.__openMmiDashboardOnline = Boolean(value); }, online);
    },
    async dashboardRequestCounts() {
      return page.evaluate(() => ({
        health: window.__openMmiDashboardHealthRequests,
        status: window.__openMmiDashboardStatusRequests,
        version: window.__openMmiDashboardVersionRequests,
      }));
    },
    async updateRequestCounts() {
      return page.evaluate(() => ({
        status: window.__openMmiUpdateStatusRequests,
        checks: window.__openMmiUpdateCheckRequests,
        coordinator: window.__openMmiUpdateCoordinatorRequests,
        prepares: window.__openMmiUpdatePrepareRequests,
        installs: window.__openMmiUpdateInstallRequests,
      }));
    },
    async storage() {
      return page.evaluate(() => Object.assign({}, window.__openMmiBrowserStorage));
    },
  };
}

async function openHome(page) {
  await expect(page.locator("#pageHome")).toHaveClass(/active/);
  await expect(page.locator("#pageTitle")).toHaveText("Home");
}

async function openSettings(page) {
  await openHome(page);
  await page.locator('[data-openmmi-settings="true"]').click();
  await expect(page.locator("#pageSettings")).toHaveClass(/active/);
  await expect(page.locator("#pageTitle")).toHaveText("Settings");
}

async function openSettingsSection(page, section) {
  const item = page.locator(`[data-openmmi-settings-section="${section}"]`);
  if (!(await item.isVisible())) {
    const parent = await item.getAttribute("data-openmmi-settings-parent");
    if (parent) {
      const branch = page.locator(`[data-openmmi-settings-group="${parent}"] [data-openmmi-settings-group-toggle]`);
      if (!(await branch.isVisible())) {
        const currentBranch = page.locator('[data-openmmi-settings-group-toggle][aria-expanded="true"]');
        if (await currentBranch.count()) await currentBranch.first().click();
      }
      if (await branch.getAttribute("aria-expanded") !== "true") await branch.click();
    }
  }
  await item.click();
  await expect(item).toHaveClass(/active/);
}

async function openMedia(page) {
  await openHome(page);
  await page.locator('[data-openmmi-page="0"]').click();
  await expect(page.locator("#pageElectrical")).toHaveClass(/active/);
  await expect(page.locator("#pageTitle")).toHaveText("Media");
  await expect(page.locator("#openMmiMediaRoot")).toBeVisible();
}


test("frontend build mismatch defers reload while an editable field is active", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await loadDashboard(page, {
    focusBeforeReady: true,
    versionPayload: {
      api_version: 1,
      build_id: "build-b",
      frontend_id: "build-b",
      reload_supported: true,
    },
  });

  await expect(page.locator("#openMmiUpdateNotice")).toBeVisible();
  await expect(page.locator("[data-openmmi-update-message]")).toContainText("Finish editing");
  await expect(page.locator("#openMmiUnsavedTestInput")).toBeFocused();
  await expectNoRuntimeFailures(failures);
});

test("loads, renders status and navigates with buttons and keyboard", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await loadDashboard(page);
  await openHome(page);

  await expect(page.locator("#healthText")).toHaveText("live");
  await expect(page.locator("#homeSpeed")).not.toHaveText("--");

  await page.locator('[data-openmmi-page="2"]').click();
  await expect(page.locator("#pageDrive")).toHaveClass(/active/);
  await expect(page.locator('[data-field="speed_mph"]').first()).not.toHaveText("--");
  await expect(page.locator('#pageDrive [data-field="fuel_l"]')).toHaveText("42");

  await page.keyboard.press("ArrowRight");
  await expect(page.locator("#pageTitle")).toHaveText("Media");
  await page.keyboard.press("Home");
  await expect(page.locator("#pageTitle")).toHaveText("Home");

  await page.locator('[data-openmmi-menu="climate"]').click();
  await expect(page.locator("#pageClimate")).toHaveClass(/active/);
  await expect(page.locator('[data-bool="recirculation"]')).toHaveText("ON");

  await expectNoRuntimeFailures(failures);
});


test("diagnostics renders canonical profile values and all decoded paths", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await loadDashboard(page);
  await openSettings(page);
  await openSettingsSection(page, "diagnostics");

  const metricValue = (label) => page.locator(".openmmi-settings-metric").filter({ has: page.locator("span", { hasText: label }) }).first().locator("strong");
  await expect(metricValue("Outside display")).toHaveText("12.5 °C");
  await expect(metricValue("Coolant")).toHaveText("91.0 °C");
  await expect(metricValue("Voltage")).toHaveText("13.9 V");
  await expect(metricValue("RPM")).toHaveText("2150 rpm");
  await expect(page.locator(".openmmi-settings-diagnostics-details")).toContainText("engine.speed_rpm");
  await expect(page.locator(".openmmi-settings-diagnostics-details")).toContainText("electrical.supply_voltage_v");
  await expect(page.locator(".openmmi-settings-diagnostics-details")).toContainText("climate.blower_load_percent");

  await expectNoRuntimeFailures(failures);
});

test("diagnostics updates values in place without flashing or rebuilding fields", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);
  await openSettingsSection(page, "diagnostics");

  const voltage = page.locator('[data-openmmi-diagnostic-key="electrical.voltage"]');
  await expect(voltage).toHaveText("13.9 V");
  await page.evaluate(() => {
    window.__openMmiDiagnosticsVoltageNode = document.querySelector(
      '[data-openmmi-diagnostic-key="electrical.voltage"]',
    );
  });

  await dashboard.setPayload(basePayload({
    state: { electrical: { supply_voltage_v: 14.2, terminal30_voltage_v: 14.2 } },
  }));
  await expect(voltage).toHaveText("14.2 V");
  await page.waitForTimeout(500);

  expect(await page.evaluate(() => (
    window.__openMmiDiagnosticsVoltageNode
      === document.querySelector('[data-openmmi-diagnostic-key="electrical.voltage"]')
  ))).toBe(true);
  await expectNoRuntimeFailures(failures);
});

test("thermal and power diagnostics poll only while visible and identify the Surface limit", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, { runtimeDiagnosticsIntervalMs: 50 });
  await openSettings(page);
  expect(await dashboard.runtimeDiagnosticsRequests()).toBe(0);

  await openSettingsSection(page, "diagnostics");
  const runtimePanel = page.locator("#openMmiRuntimeDiagnostics");
  await expect(runtimePanel).toBeVisible();
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="cpu.clock"]')).toHaveText("400 MHz average");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="thermal.sensor"]')).toHaveText("GEN4 52.5 °C");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="power.state"]')).toHaveText("AC connected — not charging");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="system.state"]')).toHaveText("Performance limited by temperature");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="frontend.status"]')).toContainText("fetches");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="frontend.render"]')).toContainText("renders");
  await expect(runtimePanel.locator('[data-openmmi-runtime-key="frontend.media"]')).toContainText("layouts");
  const activeCount = await dashboard.runtimeDiagnosticsRequests();
  expect(activeCount).toBeGreaterThanOrEqual(2);

  await openSettingsSection(page, "system");
  await page.waitForTimeout(80);
  const stoppedCount = await dashboard.runtimeDiagnosticsRequests();
  await page.waitForTimeout(180);
  expect(await dashboard.runtimeDiagnosticsRequests()).toBe(stoppedCount);

  await openSettingsSection(page, "diagnostics");
  await expect.poll(() => dashboard.runtimeDiagnosticsRequests()).toBeGreaterThan(stoppedCount);
  await expectNoRuntimeFailures(failures);
});

test("door and reverse overlays dismiss and reactivate on lifecycle changes", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);

  await dashboard.setPayload(basePayload({ state: { doors: { front_left: true, any_open: true } } }));
  await expect(page.locator("#openMmiVehicleOverlay")).toBeVisible();
  await expect(page.locator("#openMmiVehicleOverlay [data-door-mark=\"front_left\"]")).toHaveClass(/open/);
  await page.locator("#openMmiDoorOverlayDismiss").click();
  await expect(page.locator("#openMmiVehicleOverlay")).toBeHidden();
  await page.waitForTimeout(350);
  await expect(page.locator("#openMmiVehicleOverlay")).toBeHidden();

  await dashboard.setPayload(basePayload({ state: { doors: { front_left: true, rear_left: true, any_open: true } } }));
  await expect(page.locator("#openMmiVehicleOverlay")).toBeVisible();
  await page.locator("#openMmiDoorOverlayDismiss").click();

  await dashboard.setPayload(basePayload({ state: { vehicle: { reverse: true } } }));
  await expect(page.locator("#openMmiReverseOverlay")).toBeVisible();
  await page.locator("#openMmiReverseOverlayDismiss").click();
  await expect(page.locator("#openMmiReverseOverlay")).toBeHidden();
  await page.waitForTimeout(350);
  await expect(page.locator("#openMmiReverseOverlay")).toBeHidden();

  await dashboard.setPayload(basePayload());
  await page.waitForTimeout(250);
  await dashboard.setPayload(basePayload({ state: { vehicle: { reverse: true } } }));
  await expect(page.locator("#openMmiReverseOverlay")).toBeVisible();

  await expectNoRuntimeFailures(failures);
});

test("service reminder shows MIB-style due-soon state and preserves active edits", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, {
    serviceReminderPayload: {
      ok: true,
      api_version: 2,
      configured: true,
      path: "/home/test/.config/open-mmi/service-reminder.json",
      settings: {
        enabled: true,
        distance_interval_km: 10000,
        time_interval_months: 12,
        warning_distance_km: 1000,
        warning_days: 30,
      },
      reset: { reset_date: "2026-01-01", odometer_km: 3000 },
      next_due: { date: "2027-01-01", odometer_km: 13000 },
      acknowledgement: { acknowledged_at: null, level: null, reset_date: null, reset_odometer_km: null, due_date: null, due_odometer_km: null },
    },
  });

  const indicator = page.locator("#openMmiServiceReminderIndicator");
  await expect(indicator).toBeVisible();
  await expect(indicator).toContainText("407 mi or");
  await expect(page.locator("#openMmiServiceDetailOverlay")).toBeVisible();
  await page.getByRole("button", { name: "Acknowledge" }).click();
  await expect(page.locator("#openMmiServiceDetailOverlay")).toBeHidden();
  await expect(indicator).toBeVisible();

  await openSettings(page);
  await openSettingsSection(page, "service");
  await expect(page.locator('[data-openmmi-service-reminder-panel="true"]')).toContainText("Inspection due soon");
  const distanceInput = page.getByLabel("Distance interval");
  await expect(distanceInput).toHaveValue("6214");
  await expect(page.getByRole("button", { name: "Save intervals" })).toHaveClass(/openmmi-setting-pill/);
  await expect(page.getByRole("button", { name: "Reset inspection interval" })).toHaveClass(/openmmi-setting-pill/);
  await expect(page.getByRole("button", { name: "Reset inspection interval" })).toBeEnabled();

  await distanceInput.fill("7000");
  await dashboard.setPayload(basePayload({ state: { vehicle: { odometer_km: 12346 } } }));
  await page.waitForTimeout(300);
  await expect(distanceInput).toBeFocused();
  await expect(distanceInput).toHaveValue("7000");
  await expectNoRuntimeFailures(failures);
});


test("Trip A displays live distance and resets from Settings", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, {
    tripAPayload: {
      ok: true,
      api_version: 2,
      configured: true,
      path: "/home/test/.config/open-mmi/trip-a.json",
      settings: { auto_reset_hours: 0 },
      activity: { last_active_at: null, odometer_km: null },
      reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: 12000 },
    },
  });

  await expect(page.locator("[data-openmmi-trip-a]").first()).toHaveText("214");
  await expect(page.locator("[data-openmmi-trip-a-unit]").first()).toHaveText("mi");

  await openSettings(page);
  await openSettingsSection(page, "trip");
  await expect(page.locator('[data-openmmi-trip-a-panel="true"]')).toContainText("Trip A");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Reset Trip A" }).click();
  await expect(page.locator('[data-openmmi-trip-a-summary-detail]')).toHaveText("0 mi");
  await expect(page.locator("[data-openmmi-trip-a]").first()).toHaveText("0");
  const autoReset = page.getByLabel("Trip A automatic reset");
  await autoReset.selectOption("2");
  await page.getByRole("button", { name: "Save automatic reset" }).click();
  await expect(autoReset).toHaveValue("2");
  await expectNoRuntimeFailures(failures);
});

test("Trip B displays independently and resets manually", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, {
    tripBPayload: {
      ok: true, api_version: 1, configured: true,
      path: "/home/test/.config/open-mmi/trip-b.json",
      reset: { reset_at: "2026-07-20T12:00:00+00:00", odometer_km: 11000 },
    },
  });
  await page.locator('[data-openmmi-page="2"]').click();
  await expect(page.locator("#pageDrive")).toHaveClass(/active/);
  const tripCard = page.locator("#pageDrive [data-openmmi-trip-card]");
  await expect(tripCard.locator("[data-openmmi-trip-b]")).toBeHidden();
  await expect(tripCard.locator("[data-openmmi-trip-label]")).toHaveText("Trip A");
  await tripCard.getByRole("button", { name: "Show Trip B" }).click();
  await expect(tripCard.locator("[data-openmmi-trip-label]")).toHaveText("Trip B");
  await expect(tripCard.locator("[data-openmmi-trip-b]")).toBeVisible();
  await expect(tripCard.locator("[data-openmmi-trip-b]")).toHaveText("835");
  await expect(tripCard.getByRole("button", { name: "Show Trip A" })).toBeVisible();
  await page.keyboard.press("Home");
  await openSettings(page);
  await openSettingsSection(page, "trip");
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "Reset Trip B" }).click();
  await expect(tripCard.locator("[data-openmmi-trip-b]")).toHaveText("0");
  await expectNoRuntimeFailures(failures);
});

test("settings categories use an expandable tree without overflowing the sidebar", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await loadDashboard(page);
  await openSettings(page);

  const tree = page.getByRole("tree", { name: "Settings tree" });
  const general = tree.getByRole("treeitem", { name: /General/ });
  const vehicle = tree.getByRole("treeitem", { name: /Vehicle/ });
  await expect(general).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator('[data-openmmi-settings-section="units"]')).toBeHidden();
  await expect(page.locator('[data-openmmi-settings-section="service"]')).toBeHidden();

  await vehicle.click();
  await expect(vehicle).toHaveAttribute("aria-expanded", "true");
  await expect(vehicle).toHaveAttribute("aria-label", "Back to settings categories from Vehicle");
  await expect(general).toHaveAttribute("aria-expanded", "false");
  await expect(page.locator('[data-openmmi-settings-section="service"]')).toBeVisible();
  await expect(page.locator('[data-openmmi-settings-section="trip"]')).toBeVisible();

  const fitsSidebar = await page.locator(".openmmi-settings-sidebar-card").evaluate((sidebar) => {
    const visibleItems = Array.from(sidebar.querySelectorAll('[role="treeitem"]'))
      .filter((item) => item.getClientRects().length > 0);
    const last = visibleItems[visibleItems.length - 1]?.getBoundingClientRect();
    const bounds = sidebar.getBoundingClientRect();
    return Boolean(last && last.bottom <= bounds.bottom + 1);
  });
  expect(fitsSidebar).toBe(true);

  await openSettingsSection(page, "trip");
  await expect(page.locator('[data-openmmi-trip-a-panel="true"]')).toBeVisible();
  await expectNoRuntimeFailures(failures);
});

test("settings persist units and display mode across page reconstruction", async ({ page, context }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);

  const speedRow = page.locator(".openmmi-setting-row").filter({ hasText: "Speed" });
  await speedRow.getByRole("button", { name: "km/h" }).click();

  await openSettingsSection(page, "display");
  const dimRow = page.locator(".openmmi-setting-row").filter({ hasText: "Dim mode" });
  await dimRow.getByRole("button", { name: "on", exact: true }).click();

  const saved = await dashboard.storage();
  const prefs = JSON.parse(saved[SETTINGS_KEY]);
  expect(prefs.speedUnit).toBe("kmh");
  expect(prefs.dimMode).toBe(true);
  await expect(page.locator("html")).toHaveClass(/openmmi-dim-mode/);

  const rebuilt = await context.newPage();
  const rebuiltFailures = captureRuntimeFailures(rebuilt);
  await loadDashboard(rebuilt, { storage: saved });
  await expect(rebuilt.locator("html")).toHaveClass(/openmmi-dim-mode/);
  await rebuilt.locator('[data-openmmi-settings="true"]').click();
  await expect(rebuilt.locator("#pageSettings")).toHaveClass(/active/);
  await expect(rebuilt.locator(".openmmi-setting-row").filter({ hasText: "Speed" }).getByRole("button", { name: "km/h" })).toHaveAttribute("aria-pressed", "true");

  await expectNoRuntimeFailures(failures);
  await expectNoRuntimeFailures(rebuiltFailures);
  await rebuilt.close();
});

test("media source selection persists and hides disabled sources", async ({ page, context }) => {
  const failures = captureRuntimeFailures(page);
  const initialSettings = {
    mediaActiveSource: "jellyfin",
    mediaDefaultSource: "jellyfin",
    mediaSources: { jellyfin: true, radio: false, usb: true, bluetooth: true },
  };
  const dashboard = await loadDashboard(page, {
    storage: { [SETTINGS_KEY]: JSON.stringify(initialSettings) },
  });
  await openMedia(page);

  const usb = page.locator('[data-openmmi-media-source="usb"]');
  await expect(usb).toBeVisible();
  await usb.click();
  await expect(usb).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-openmmi-media-source="radio"]')).toHaveCount(0);

  const saved = await dashboard.storage();
  expect(JSON.parse(saved[SETTINGS_KEY]).mediaActiveSource).toBe("usb");

  const rebuilt = await context.newPage();
  const rebuiltFailures = captureRuntimeFailures(rebuilt);
  await loadDashboard(rebuilt, { storage: saved });
  await rebuilt.locator('[data-openmmi-page="0"]').click();
  await expect(rebuilt.locator('[data-openmmi-media-source="usb"]')).toHaveAttribute("aria-pressed", "true");

  await expectNoRuntimeFailures(failures);
  await expectNoRuntimeFailures(rebuiltFailures);
  await rebuilt.close();
});

test("Bluetooth transport follows steering-wheel status changes", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const initialSettings = {
    mediaActiveSource: "bluetooth",
    mediaDefaultSource: "bluetooth",
    mediaSources: { jellyfin: true, radio: true, usb: true, bluetooth: true },
  };
  const playing = {
    available: true,
    configured: true,
    status: "playing",
    state_label: "playing",
    player_id: "player-1",
    playback_status: "playing",
    position_seconds: 12,
    duration_seconds: 120,
    track: { id: "track-1", name: "Test track", title: "Test track", artist: "Artist" },
    controls: { play_pause: true, play: true, pause: true, stop: true, previous: true, next: true },
  };
  const dashboard = await loadDashboard(page, {
    storage: { [SETTINGS_KEY]: JSON.stringify(initialSettings) },
    bluetoothPayload: playing,
  });
  await openMedia(page);

  const playButton = page.locator("#ommiMediaPlay");
  await expect(playButton).toHaveAttribute("aria-label", /Pause Bluetooth playback/);

  // Simulate a prior dashboard-issued optimistic state, then a genuine BlueZ
  // transition caused by the steering wheel or connected phone.
  await page.evaluate(() => {
    window.openMmiBluetoothMedia.state.playbackOverride = "playing";
    window.openMmiBluetoothMedia.state.lastServerPlaybackStatus = "playing";
  });
  await dashboard.setBluetoothPayload({
    ...playing,
    status: "paused",
    state_label: "paused",
    playback_status: "paused",
    position_seconds: 12.2,
  });
  await page.evaluate(() => window.openMmiBluetoothMedia.refresh());

  await expect(playButton).toHaveAttribute("aria-label", /Play Bluetooth media/);
  await expectNoRuntimeFailures(failures);
});

test("Diagnostics remains scrollable while live values refresh", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const payload = basePayload();
  payload.state.profile_debug = Object.fromEntries(
    Array.from({ length: 80 }, (_, index) => [`signal_${String(index).padStart(2, "0")}`, index]),
  );
  const dashboard = await loadDashboard(page, { payload });
  await openSettings(page);
  await openSettingsSection(page, "diagnostics");

  const scroller = page.locator("#pageSettings .openmmi-settings-panel-card");
  await expect(scroller.locator(".openmmi-settings-diagnostics-details summary")).toContainText("Decoded profile values");
  await scroller.evaluate((element) => { element.scrollTop = Math.max(1, element.scrollHeight - element.clientHeight - 40); });
  const before = await scroller.evaluate((element) => element.scrollTop);
  expect(before).toBeGreaterThan(0);

  await scroller.dispatchEvent("pointerdown");
  for (let index = 0; index < 4; index += 1) {
    const next = basePayload({ state: { engine: { speed_rpm: 2200 + index } } });
    next.state.profile_debug = payload.state.profile_debug;
    await dashboard.setPayload(next);
    await page.waitForTimeout(120);
  }
  const during = await scroller.evaluate((element) => element.scrollTop);
  expect(during).toBeGreaterThanOrEqual(before - 2);

  await page.waitForTimeout(500);
  const after = await scroller.evaluate((element) => element.scrollTop);
  expect(after).toBeGreaterThanOrEqual(before - 2);
  await expectNoRuntimeFailures(failures);
});

test("switching away from Bluetooth releases shared transport controls", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const initialSettings = {
    mediaActiveSource: "bluetooth",
    mediaDefaultSource: "bluetooth",
    mediaSources: { jellyfin: true, radio: true, usb: true, bluetooth: true },
  };
  await loadDashboard(page, {
    storage: { [SETTINGS_KEY]: JSON.stringify(initialSettings) },
  });
  await openMedia(page);

  const transportButtons = [
    page.locator("#ommiMediaPlay"),
    page.locator("#ommiMediaPrev"),
    page.locator("#ommiMediaNext"),
    page.locator("#ommiMediaStop"),
  ];
  await expect(transportButtons[0]).toBeDisabled();

  await page.locator('[data-openmmi-media-source="usb"]').click();
  for (const button of transportButtons) await expect(button).toBeEnabled();

  await expectNoRuntimeFailures(failures);
});

for (const viewport of [
  { name: "vehicle display", width: 800, height: 480 },
  { name: "narrow portrait", width: 390, height: 844 },
]) {
  test(`keeps critical UI within the viewport on ${viewport.name}`, async ({ page }) => {
    const failures = captureRuntimeFailures(page);
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await loadDashboard(page);

    const metrics = await page.evaluate(() => {
      const rect = (selector) => {
        const value = document.querySelector(selector)?.getBoundingClientRect();
        return value ? { width: value.width, height: value.height, left: value.left, right: value.right } : null;
      };
      return {
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        bodyWidth: document.body.scrollWidth,
        topbar: rect(".topbar"),
        footer: rect("footer.status-strip"),
        activePage: rect(".page.active"),
      };
    });

    expect(metrics.documentWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1);
    expect(metrics.bodyWidth).toBeLessThanOrEqual(metrics.viewportWidth + 1);
    expect(metrics.topbar.width).toBeGreaterThan(0);
    expect(metrics.footer.width).toBeGreaterThan(0);
    expect(metrics.activePage.width).toBeGreaterThan(0);
    expect(metrics.topbar.left).toBeGreaterThanOrEqual(-1);
    expect(metrics.footer.right).toBeLessThanOrEqual(metrics.viewportWidth + 1);
    await expect(page.locator("#pageTitle")).toBeVisible();
    await expect(page.locator(".pager")).toBeVisible();

    await expectNoRuntimeFailures(failures);
  });
}
test("Bluetooth button follows a synthetic remote pause promptly", async ({ page }) => {
  const failures = captureRuntimeFailures(page);

  const initialSettings = {
    mediaActiveSource: "bluetooth",
    mediaDefaultSource: "bluetooth",
    mediaSources: {
      jellyfin: true,
      radio: true,
      usb: true,
      bluetooth: true,
    },
  };

  await loadDashboard(page, {
    storage: {
      [SETTINGS_KEY]: JSON.stringify(initialSettings),
    },
  });

  await page.evaluate(() => {
    window.__syntheticBluetoothPlaybackStatus = "playing";

    const originalFetch = window.fetch;

    window.fetch = async (input, init = {}) => {
      const url = String(input instanceof Request ? input.url : input);

      if (url.includes("/api/bluetooth/status")) {
        return new Response(
          JSON.stringify({
            configured: true,
            available: true,
            status: "ready",
            state_label: "connected",
            subtitle: "Synthetic Bluetooth player",
            playback_status: window.__syntheticBluetoothPlaybackStatus,
            player_id: "synthetic-player",
            device_name: "Synthetic phone",
            player_name: "Synthetic Bluetooth",
            position_seconds: 20,
            duration_seconds: 180,
            controls: {
              play_pause: true,
              previous: true,
              next: true,
              stop: true,
            },
            track: {
              id: "synthetic-track",
              title: "Synthetic track",
              artist: "Open MMI",
            },
          }),
          {
            status: 200,
            headers: {
              "Content-Type": "application/json",
            },
          },
        );
      }

      return originalFetch(input, init);
    };
  });

  await openMedia(page);

  await page.evaluate(() => window.openMmiBluetoothMedia.refresh());

  const playButton = page.locator("#ommiMediaPlay");

  await expect(playButton).toHaveAttribute(
    "aria-label",
    /pause bluetooth playback/i,
    { timeout: 2500 },
  );

  await page.evaluate(() => {
    window.__syntheticBluetoothPlaybackStatus = "paused";
  });

  await page.evaluate(() => window.openMmiBluetoothMedia.refresh());

  await expect(playButton).toHaveAttribute(
    "aria-label",
    /play bluetooth media/i,
    { timeout: 2500 },
  );

  await expectNoRuntimeFailures(failures);
});
test("shared clock persists display preferences and survives page navigation", async ({ page, context }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);

  const clock = page.locator("#openMmiClock");
  await expect(clock).toBeVisible();
  await expect(page.locator("#openMmiClockValue")).toHaveText(/^\d{2}:\d{2}$/);
  await expect(clock).toHaveAttribute("data-clock-format", "24h");
  await expect(clock).toHaveAttribute("data-show-date", "false");

  await page.evaluate(() => { window.__openMmiClockElementIdentity = document.querySelector("#openMmiClock"); });
  await page.locator('[data-openmmi-page="2"]').click();
  await expect(page.locator("#pageDrive")).toHaveClass(/active/);
  await page.locator('.pager button[data-page="0"]').click();
  await expect(page.locator("#pageElectrical")).toHaveClass(/active/);
  expect(await page.evaluate(() => window.__openMmiClockElementIdentity === document.querySelector("#openMmiClock"))).toBe(true);

  await page.keyboard.press("Home");
  await openSettings(page);
  await openSettingsSection(page, "display");

  const formatRow = page.locator('[data-openmmi-clock-setting-row="clockFormat"]');
  const dateRow = page.locator('[data-openmmi-clock-setting-row="showDate"]');
  const visibilityRow = page.locator('[data-openmmi-clock-setting-row="showClock"]');
  await expect(formatRow).toBeVisible();
  await formatRow.getByRole("button", { name: "12-hour" }).click();
  await dateRow.getByRole("button", { name: "on", exact: true }).click();

  await expect(clock).toHaveAttribute("data-clock-format", "12h");
  await expect(clock).toHaveAttribute("data-show-date", "true");
  await expect(page.locator("#openMmiClockValue")).toHaveText(/^\d{1,2}:\d{2}\s(?:am|pm)$/i);
  await expect(page.locator("#openMmiClockDate")).toBeVisible();

  await visibilityRow.getByRole("button", { name: "off", exact: true }).click();
  await expect(clock).toBeHidden();
  await visibilityRow.getByRole("button", { name: "on", exact: true }).click();
  await expect(clock).toBeVisible();

  const saved = await dashboard.storage();
  const prefs = JSON.parse(saved[SETTINGS_KEY]);
  expect(prefs.clockFormat).toBe("12h");
  expect(prefs.showDate).toBe(true);
  expect(prefs.showClock).toBe(true);

  const rebuilt = await context.newPage();
  const rebuiltFailures = captureRuntimeFailures(rebuilt);
  await loadDashboard(rebuilt, { storage: saved });
  await expect(rebuilt.locator("#openMmiClock")).toHaveAttribute("data-clock-format", "12h");
  await expect(rebuilt.locator("#openMmiClockDate")).toBeVisible();

  await expectNoRuntimeFailures(failures);
  await expectNoRuntimeFailures(rebuiltFailures);
  await rebuilt.close();
});


test("vehicle setup copies maintained templates into the user catalogue", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);

  await openSettingsSection(page, "vehicle-setup");
  await expect(page.locator('[data-openmmi-vehicle-setup-ready="true"]')).toBeVisible();
  await expect(page.getByTestId("vehicle-setup-copy-vehicle")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-copy-bindings")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-copy-vehicle")).toHaveText("Use maintained profile as template");

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    expect(dialog.defaultValue()).toBe("seat-leon-1p-pq35-custom");
    await dialog.accept("seat-template");
  });
  await page.getByTestId("vehicle-setup-copy-vehicle").click();

  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("custom:seat-template");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("created in your user catalogue");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("maintained template was not changed");
  await expect(page.getByTestId("vehicle-setup-copy-vehicle")).toHaveCount(0);
  await expect(page.getByText("Stored in your user catalogue. Lifecycle changes do not apply or restart the CAN service.")).toBeVisible();

  expect(await dashboard.vehicleSetupCopyRequests()).toBe(1);
  expect(await dashboard.vehicleSetupCopyBodies()).toEqual([{
    kind: "profile",
    id: "seat-template",
    template_source: "maintained",
    template_id: "seat-leon-1p-pq35",
    template_revision: "sha256:profile",
  }]);
  expect(await dashboard.vehicleSetupApplyRequests()).toBe(0);
  await expectNoRuntimeFailures(failures);
});


test("vehicle setup imports JSON as a new unapplied custom draft", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);
  await openSettingsSection(page, "vehicle-setup");
  await expect(page.locator('[data-openmmi-vehicle-setup-ready="true"]')).toBeVisible();

  await expect(page.getByTestId("vehicle-setup-import-vehicle")).toBeEnabled();
  const content = '{\n  "default_bus": "comfort",\n  "can_buses": {"comfort": {"interface": "can0", "bitrate": 100000}},\n  "rules": [],\n  "presence": [],\n  "status": []\n}\n';
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    expect(dialog.defaultValue()).toBe("seat-import");
    await dialog.accept("imported-seat");
  });
  await page.getByTestId("vehicle-setup-import-file-vehicle").setInputFiles({
    name: "Seat Import.json",
    mimeType: "application/json",
    buffer: Buffer.from(content),
  });

  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("custom:imported-seat");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("imported and selected as an unapplied draft");
  expect(await dashboard.vehicleSetupImportRequests()).toBe(1);
  expect(await dashboard.vehicleSetupImportBodies()).toEqual([{
    kind: "profile", id: "imported-seat", content,
  }]);
  expect(await dashboard.vehicleSetupApplyRequests()).toBe(0);
  await expectNoRuntimeFailures(failures);
});

test("vehicle setup manages only inactive custom catalogue items without applying", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);
  await openSettingsSection(page, "vehicle-setup");
  await expect(page.locator('[data-openmmi-vehicle-setup-ready="true"]')).toBeVisible();

  await page.getByTestId("vehicle-setup-profile").selectOption("custom:my-seat");
  await expect(page.getByTestId("vehicle-setup-duplicate-vehicle")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-rename-vehicle")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-delete-vehicle")).toBeEnabled();
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    await dialog.accept("my-seat-copy");
  });
  await page.getByTestId("vehicle-setup-duplicate-vehicle").click();
  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("custom:my-seat-copy");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("duplicated");

  await page.getByTestId("vehicle-setup-bindings").selectOption("custom:my-controls");
  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("prompt");
    await dialog.accept("driver-controls");
  });
  await page.getByTestId("vehicle-setup-rename-bindings").click();
  await expect(page.getByTestId("vehicle-setup-bindings")).toHaveValue("custom:driver-controls");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("renamed");

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("cannot be undone");
    await dialog.accept();
  });
  await page.getByTestId("vehicle-setup-delete-bindings").click();
  await expect(page.getByTestId("vehicle-setup-bindings")).toHaveValue("maintained:default");
  await expect(page.getByTestId("vehicle-setup-copy-feedback")).toContainText("deleted");

  expect(await dashboard.vehicleSetupManageRequests()).toBe(3);
  expect(await dashboard.vehicleSetupManageBodies()).toEqual([
    {
      action: "duplicate", kind: "profile", source: "custom", id: "my-seat",
      expected_revision: "sha256:custom-profile", new_id: "my-seat-copy",
    },
    {
      action: "rename", kind: "bindings", source: "custom", id: "my-controls",
      expected_revision: "sha256:custom-bindings", new_id: "driver-controls",
    },
    {
      action: "delete", kind: "bindings", source: "custom", id: "driver-controls",
      expected_revision: "sha256:custom-bindings",
    },
  ]);
  expect(await dashboard.vehicleSetupApplyRequests()).toBe(0);
  await expectNoRuntimeFailures(failures);
});

test("vehicle setup edits only custom JSON and leaves it unapplied", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);
  await openSettingsSection(page, "vehicle-setup");
  await expect(page.locator('[data-openmmi-vehicle-setup-ready="true"]')).toBeVisible();

  await page.getByTestId("vehicle-setup-profile").selectOption("custom:my-seat");
  await expect(page.getByTestId("vehicle-setup-edit-vehicle")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-copy-vehicle")).toHaveCount(0);
  await page.getByTestId("vehicle-setup-edit-vehicle").click();

  const editor = page.getByTestId("vehicle-custom-editor-content");
  await expect(editor).toBeVisible();
  await expect(editor).toContainText('"default_bus": "comfort"');
  await expect(page.getByTestId("vehicle-setup-review")).toBeDisabled();
  const updated = '{\n  "default_bus": "comfort",\n  "can_buses": {"comfort": {"interface": "can1", "bitrate": 100000}},\n  "rules": [],\n  "presence": [],\n  "status": [],\n  "editor_qualification": true\n}\n';
  await editor.fill(updated);
  await page.getByTestId("vehicle-custom-editor-save").click();

  await expect(page.getByTestId("vehicle-custom-editor-feedback")).toContainText("saved");
  await expect(page.getByTestId("vehicle-custom-editor-feedback")).toContainText("apply the new revision");
  expect(await dashboard.vehicleSetupLoadBodies()).toEqual([
    { kind: "profile", source: "custom", id: "my-seat" },
  ]);
  expect(await dashboard.vehicleSetupSaveBodies()).toEqual([{
    kind: "profile", source: "custom", id: "my-seat",
    expected_revision: "sha256:custom-profile", content: updated,
  }]);
  expect(await dashboard.vehicleSetupApplyRequests()).toBe(0);
  await expect(page.getByTestId("vehicle-custom-editor-close")).toBeEnabled();
  await page.getByTestId("vehicle-custom-editor-close").click();
  await expect(page.getByTestId("vehicle-custom-editor")).toHaveCount(0);
  await expect(page.getByTestId("vehicle-setup-review")).toBeEnabled();
  await expectNoRuntimeFailures(failures);
});

test("vehicle setup contains long revisions and distinguishes saved from loaded state", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const revisions = {
    configuredProfile: `sha256:${"a".repeat(64)}`,
    loadedProfile: `sha256:${"b".repeat(64)}`,
    configuredBindings: `sha256:${"c".repeat(64)}`,
    loadedBindings: `sha256:${"d".repeat(64)}`,
  };
  await loadDashboard(page, { vehicleSetupPendingRevisions: revisions });
  await openSettings(page);

  await openSettingsSection(page, "vehicle-setup");
  await expect(page.getByTestId("vehicle-setup-status")).toContainText("Saved custom revisions await review and Apply");
  await expect(page.getByTestId("vehicle-setup-runtime-sync")).toContainText("Saved revisions await review and Apply");
  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("custom:my-seat");
  await expect(page.getByTestId("vehicle-setup-bindings")).toHaveValue("custom:my-controls");

  const technical = page.getByTestId("vehicle-setup-technical");
  await technical.locator("summary").click();
  const configuredProfile = page.getByTestId("vehicle-setup-configured-profile-revision");
  const loadedProfile = page.getByTestId("vehicle-setup-loaded-profile-revision");
  await expect(configuredProfile).toHaveText("sha256:aaaaaaaaaa…aaaaaaaa");
  await expect(loadedProfile).toHaveText("sha256:bbbbbbbbbb…bbbbbbbb");
  await expect(configuredProfile).toHaveAttribute("title", revisions.configuredProfile);
  await expect(loadedProfile).toHaveAttribute("title", revisions.loadedProfile);

  const overflow = await page.getByTestId("vehicle-setup-technical").evaluate((details) => ({
    details: details.scrollWidth - details.clientWidth,
    panel: details.closest("[data-openmmi-vehicle-setup-panel]").scrollWidth
      - details.closest("[data-openmmi-vehicle-setup-panel]").clientWidth,
    rows: Array.from(details.querySelectorAll(".openmmi-settings-metric"))
      .map((row) => row.scrollWidth - row.clientWidth),
  }));
  expect(overflow.details).toBeLessThanOrEqual(1);
  expect(overflow.panel).toBeLessThanOrEqual(1);
  expect(Math.max(...overflow.rows)).toBeLessThanOrEqual(1);
  await expectNoRuntimeFailures(failures);
});

test("vehicle setup reviews and applies an exact confirmed draft", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);

  await openSettingsSection(page, "vehicle-setup");
  await expect(page.locator('[data-openmmi-vehicle-setup-ready="true"]')).toBeVisible();
  await expect(page.getByTestId("vehicle-setup-active-profile")).toHaveText("SEAT Leon 1P / Mk2 (PQ35) · Maintained");
  await expect(page.getByTestId("vehicle-setup-active-bindings")).toHaveText("Default · Maintained");
  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("maintained:seat-leon-1p-pq35");
  await expect(page.getByTestId("vehicle-setup-bindings")).toHaveValue("maintained:default");
  await expect(page.getByTestId("vehicle-setup-interface")).toHaveText("can0 · not detected");
  await expect(page.getByTestId("vehicle-setup-bitrate")).toHaveText("100 kbit/s");
  await expect(page.getByTestId("vehicle-setup-review")).toBeEnabled();
  await expect(page.getByTestId("vehicle-setup-technical")).not.toHaveAttribute("open", "");
  expect(await dashboard.vehicleSetupRequests()).toBe(1);
  expect(await dashboard.vehicleSetupCoordinatorRequests()).toBe(1);

  await page.getByTestId("vehicle-setup-profile").selectOption("custom:my-seat");
  await page.getByTestId("vehicle-setup-bindings").selectOption("custom:my-controls");
  await expect(page.getByTestId("vehicle-setup-status")).toContainText("Draft selections are not applied");
  await expect(page.getByTestId("vehicle-setup-profile")).toHaveValue("custom:my-seat");
  await expect(page.getByTestId("vehicle-setup-bindings")).toHaveValue("custom:my-controls");
  await expect(page.getByTestId("vehicle-setup-interface")).toHaveText("can1 · not detected");
  await expect(page.getByTestId("vehicle-setup-review")).toBeEnabled();
  expect(await dashboard.vehicleSetupRequests()).toBe(1);
  expect(await dashboard.vehicleSetupCoordinatorRequests()).toBe(1);
  expect(await dashboard.vehicleSetupPreviewRequests()).toBe(0);

  await page.getByTestId("vehicle-setup-review").click();
  await expect(page.getByTestId("vehicle-setup-preview")).toBeVisible();
  await expect(page.getByTestId("vehicle-setup-status")).toContainText("Review ready");
  await expect(page.getByTestId("vehicle-setup-preview-interface")).toHaveText("can1 · not detected");
  await expect(page.getByTestId("vehicle-setup-preview")).toContainText("1 binding is not emitted by the profile");
  await expect(page.getByTestId("vehicle-setup-apply")).toBeEnabled();
  expect(await dashboard.vehicleSetupPreviewRequests()).toBe(1);
  expect(await dashboard.vehicleSetupCoordinatorRequests()).toBe(2);
  expect(await dashboard.vehicleSetupPreviewBodies()).toEqual([{
    vehicle: { source: "custom", id: "my-seat" },
    bindings: { source: "custom", id: "my-controls" },
    runtime: { active_bus: "comfort", buses: { comfort: { interface: "can1" } } },
  }]);

  page.once("dialog", async (dialog) => {
    expect(dialog.type()).toBe("confirm");
    expect(dialog.message()).toContain("Apply My Seat · Custom with My controls · Custom on can1?");
    await dialog.accept();
  });
  await page.getByTestId("vehicle-setup-apply").click();
  await expect(page.getByTestId("vehicle-setup-apply-feedback")).toHaveText("Vehicle setup applied and verified.");
  await expect(page.getByTestId("vehicle-setup-preview")).toHaveCount(0);
  expect(await dashboard.vehicleSetupApplyRequests()).toBe(1);
  expect(await dashboard.vehicleSetupApplyBodies()).toEqual([{
    target: {
      vehicle: { source: "custom", id: "my-seat", revision: "sha256:custom-profile" },
      bindings: { source: "custom", id: "my-controls", revision: "sha256:custom-bindings" },
      runtime: { mode: "single", active_bus: "comfort", buses: { comfort: { interface: "can1" } } },
    },
    expected_configuration_revision: "sha256:configuration",
    target_configuration_revision: "sha256:target",
    confirm: true,
  }]);
  expect(await dashboard.vehicleSetupRequests()).toBe(2);
  expect(await dashboard.vehicleSetupCoordinatorRequests()).toBe(3);
  await expectNoRuntimeFailures(failures);
});

test("system settings and Jellyfin setup use the shared local configuration API", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page);
  await openSettings(page);

  await openSettingsSection(page, "system");
  await expect(page.locator('[data-openmmi-system-settings-panel="true"]')).toBeVisible();
  await expect(page.getByTestId("launcher-default-web")).toHaveClass(/is-selected/);
  await expect(page.getByTestId("launcher-autostart-on")).toHaveClass(/is-selected/);
  await expect(page.getByTestId("system-frontend-version")).toHaveText("__OPEN_MMI_FRONTEND_ID__");
  await expect(page.getByTestId("system-server-version")).toHaveText("__OPEN_MMI_FRONTEND_ID__");
  await expect(page.getByTestId("system-version-state")).toHaveText("up to date");
  await expect(page.getByTestId("system-installed-version")).toHaveText("v1-runtime-hardening-42-gabc1234");
  await expect(page.getByTestId("system-update-channel")).toHaveText("nightly");
  await expect(page.getByTestId("system-available-version")).toHaveText("--");
  await expect(page.getByTestId("system-update-state")).toHaveText("not checked");
  await expect(page.getByTestId("system-update-checked-at")).toHaveText("never");
  await expect(page.getByTestId("system-update-repository")).toHaveText("ready");
  await expect(page.getByTestId("system-update-readiness")).toHaveText("ready");
  await expect(page.getByTestId("system-update-technical")).not.toHaveAttribute("open", "");
  await expect(page.getByTestId("system-update-transaction-label")).toHaveText("Update progress");
  await expect(page.getByTestId("system-update-transaction")).toHaveText("idle");
  await expect(page.getByTestId("system-update-target-label")).toHaveCount(0);
  await expect(page.getByTestId("system-update-prepare")).toBeDisabled();
  await expect(page.getByTestId("system-update-install")).toBeDisabled();
  const updateCountsBeforeCheck = await dashboard.updateRequestCounts();
  expect(updateCountsBeforeCheck.status).toBeGreaterThanOrEqual(1);
  expect(updateCountsBeforeCheck.checks).toBe(0);

  await page.getByTestId("system-update-check").click();
  await expect(page.getByTestId("system-update-state")).toHaveText("update available");
  await expect(page.getByTestId("system-available-version")).toHaveText("def5678abc90");
  await expect(page.getByTestId("system-update-checked-at")).toHaveText("2026-07-18 14:32:00 UTC");
  await expect(page.locator('[data-openmmi-update-status="true"]').getByRole("status")).toContainText("An update is available");
  await expect(page.getByTestId("system-update-prepare")).toBeEnabled();
  const updateCountsAfterCheck = await dashboard.updateRequestCounts();
  expect(updateCountsAfterCheck.status).toBe(updateCountsBeforeCheck.status);
  expect(updateCountsAfterCheck.checks).toBe(1);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("system-update-prepare").click();
  await expect(page.getByTestId("system-update-transaction")).toHaveText("ready to install");
  await expect(page.getByTestId("system-update-target")).toHaveText("v1-runtime-hardening-43-gdef5678");
  await expect(page.getByTestId("system-update-install")).toBeEnabled();
  const updateCountsAfterPrepare = await dashboard.updateRequestCounts();
  expect(updateCountsAfterPrepare.prepares).toBe(1);

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("system-update-install").click();
  await expect(page.getByTestId("system-update-transaction-label")).toHaveText("Last update");
  await expect(page.getByTestId("system-update-transaction")).toHaveText("complete");
  await expect(page.getByTestId("system-update-target-label")).toHaveText("Last update version");
  await expect(page.getByTestId("system-installed-version")).toHaveText("v1-runtime-hardening-43-gdef5678");
  await expect(page.getByRole("status")).toContainText("installed successfully");
  const updateCountsAfterInstall = await dashboard.updateRequestCounts();
  expect(updateCountsAfterInstall.installs).toBe(1);

  await page.evaluate(() => {
    const panel = document.querySelector("#openmmiSettingsPanel");
    panel.innerHTML = `
      <div data-openmmi-system-settings-panel="true">
        <div class="openmmi-settings-panel-head"><span>System</span><small>loading desktop shell status</small></div>
      </div>`;
    window.dispatchEvent(new CustomEvent("openmmi:settingsrender"));
  });
  await expect(page.getByTestId("launcher-default-web")).toBeVisible();
  await expect(page.getByTestId("system-version-state")).toHaveText("up to date");

  await page.getByTestId("launcher-default-tui").click();
  await expect(page.getByTestId("launcher-default-tui")).toHaveClass(/is-selected/);
  await page.getByTestId("launcher-autostart-off").click();
  await expect(page.getByTestId("launcher-autostart-off")).toHaveClass(/is-selected/);

  await openSettingsSection(page, "media");
  await expect(page.locator('[data-openmmi-jellyfin-settings="true"]')).toBeVisible();
  const username = page.getByTestId("jellyfin-username");
  await username.click();
  await page.keyboard.type("dr");
  await page.waitForTimeout(1250);
  await expect(username).toBeFocused();
  await page.keyboard.type("h");
  await expect(username).toBeFocused();
  await expect(page.locator("#pageSettings")).toHaveClass(/active/);
  await expect(page.locator('[data-openmmi-settings-section="media"]')).toHaveClass(/active/);
  await expect(username).toHaveValue("drh");

  await page.getByTestId("jellyfin-url").fill("https://jellyfin.test:8096");
  await username.fill("driver");
  await page.getByTestId("jellyfin-password").fill("not-exposed-after-submit");
  await page.getByTestId("jellyfin-test").click();
  await expect(page.getByRole("status")).toContainText("connection succeeded");
  await expect(page.getByTestId("jellyfin-url")).toHaveValue("https://jellyfin.test:8096");
  await expect(page.getByTestId("jellyfin-username")).toHaveValue("driver");
  await expect(page.getByTestId("jellyfin-password")).toHaveValue("not-exposed-after-submit");
  await page.getByTestId("jellyfin-save").click();
  await expect(page.getByRole("status")).toContainText("restart the dashboard");
  await expect(page.getByTestId("jellyfin-password")).toHaveValue("");
  await expect(page.locator('[data-openmmi-jellyfin-settings="true"]')).not.toContainText("not-exposed-after-submit");

  await expectNoRuntimeFailures(failures);
});

test("inactive Media performs no recurring layout work", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  await loadDashboard(page);

  await openMedia(page);
  await page.waitForTimeout(250);
  const activeLayouts = await page.evaluate(() => window.openMmiMediaPerformanceMetrics?.layout_runs || 0);
  expect(activeLayouts).toBeGreaterThan(0);

  await page.evaluate(() => window.setPage(1));
  await expect(page.locator("#pageHome")).toHaveClass(/active/);
  const beforeIdle = await page.evaluate(() => window.openMmiMediaPerformanceMetrics?.layout_runs || 0);
  await page.waitForTimeout(1800);
  const afterIdle = await page.evaluate(() => window.openMmiMediaPerformanceMetrics?.layout_runs || 0);
  expect(afterIdle).toBe(beforeIdle);

  await expectNoRuntimeFailures(failures);
});

test("Jellyfin restart recovers without reloading Chromium or rebuilding Media", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, {
    jellyfinStatusPayload: {
      configured: true,
      status: "ready",
      connection_state: "ready",
      retryable: false,
      state_label: "local player ready",
      subtitle: "Jellyfin ready",
    },
    jellyfinSearchPayload: {
      configured: true,
      connection_state: "ready",
      retryable: false,
      filter: "recent",
      items: [{ id: "track-1", name: "Before restart", artist: "Artist", album: "Album", duration_seconds: 60 }],
    },
  });

  await openMedia(page);
  await expect(page.locator("#ommiMediaRemoteState")).toHaveText("READY");
  await expect(page.locator("#ommiMediaResults")).toContainText("Before restart");
  await page.evaluate(() => { document.querySelector("#openMmiMediaRoot").__reconnectIdentity = "preserved"; });

  await dashboard.setJellyfinStatus({
    configured: true,
    status: "error",
    connection_state: "reconnecting",
    retryable: true,
    state_label: "reconnecting",
    subtitle: "Jellyfin connection failed",
  });
  await page.evaluate(() => window.openMmiJellyfinPlayer.reconnection.refreshNow("browser-test-offline"));

  await expect(page.locator("#ommiMediaRemoteState")).toHaveText("RECONNECTING");
  await expect(page.locator("#ommiMediaResults")).toContainText("Before restart");
  await expect(page.locator("#ommiMediaRetry")).toBeVisible();
  expect(await page.evaluate(() => document.querySelector("#openMmiMediaRoot").__reconnectIdentity)).toBe("preserved");

  await dashboard.setJellyfinSearch({
    configured: true,
    connection_state: "ready",
    retryable: false,
    filter: "recent",
    items: [{ id: "track-2", name: "After restart", artist: "Artist", album: "Album", duration_seconds: 61 }],
  });
  await dashboard.setJellyfinStatus({
    configured: true,
    status: "ready",
    connection_state: "ready",
    retryable: false,
    state_label: "local player ready",
    subtitle: "Jellyfin ready",
  });
  await expect(page.locator("#ommiMediaRemoteState")).toHaveText("READY", { timeout: 4000 });
  await expect(page.locator("#ommiMediaResults")).toContainText("After restart");
  await expect(page.locator("#pageElectrical")).toHaveClass(/active/);
  await expect(page.locator("#pageTitle")).toHaveText("Media");
  expect(await page.evaluate(() => document.querySelector("#openMmiMediaRoot").__reconnectIdentity)).toBe("preserved");
  await expectNoRuntimeFailures(failures);
});

test("same-build dashboard restart recovers in place with one shared retry owner", async ({ page }) => {
  const failures = captureRuntimeFailures(page);
  const dashboard = await loadDashboard(page, { dashboardRetryDelaysMs: [50, 75, 100] });

  await expect(page.locator("body")).toHaveAttribute("data-openmmi-dashboard-connection", "ready");
  await page.evaluate(() => { document.querySelector("#pageHome").__dashboardRecoveryIdentity = "preserved"; });
  const before = await dashboard.dashboardRequestCounts();

  await dashboard.setDashboardOnline(false);
  await expect(page.locator("#openMmiDashboardConnectionNotice")).toBeVisible({ timeout: 2000 });
  await expect(page.locator("body")).toHaveAttribute("data-openmmi-dashboard-connection", /reconnecting|unavailable/);
  await expect(page.locator("#openMmiDashboardConnectionNotice")).toContainText(/Dashboard (reconnecting|unavailable)/);

  const afterFailure = await dashboard.dashboardRequestCounts();
  await page.waitForTimeout(350);
  const whileOffline = await dashboard.dashboardRequestCounts();
  expect(whileOffline.status - afterFailure.status).toBeLessThanOrEqual(1);
  expect(whileOffline.health).toBeGreaterThanOrEqual(before.health);

  await dashboard.setDashboardOnline(true);
  await expect(page.locator("body")).toHaveAttribute("data-openmmi-dashboard-connection", "ready", { timeout: 3000 });
  await expect(page.locator("#openMmiDashboardConnectionNotice")).toBeHidden();
  expect(await page.evaluate(() => document.querySelector("#pageHome").__dashboardRecoveryIdentity)).toBe("preserved");

  await expect.poll(async () => (await dashboard.dashboardRequestCounts()).status).toBeGreaterThan(whileOffline.status);
  await expect.poll(async () => (await dashboard.dashboardRequestCounts()).version).toBeGreaterThan(before.version);
  await expectNoRuntimeFailures(failures);
});
