"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const api = require("../../ui/web_dashboard/static/api.js");
const preferences = require("../../ui/web_dashboard/static/preferences.js");

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

test("preferences safely reads, writes, and removes JSON", () => {
  const storage = memoryStorage();
  assert.deepEqual(preferences.readObject("missing", { fallback: true }, storage), { fallback: true });
  assert.equal(preferences.writeJson("settings", { speedUnit: "kmh" }, storage), true);
  assert.deepEqual(preferences.readObject("settings", {}, storage), { speedUnit: "kmh" });
  assert.equal(preferences.remove("settings", storage), true);
  assert.deepEqual(preferences.readObject("settings", {}, storage), {});
});

test("preferences tolerate malformed and unavailable storage", () => {
  const malformed = memoryStorage({ broken: "{" });
  assert.deepEqual(preferences.readObject("broken", { safe: true }, malformed), { safe: true });
  const denied = {
    getItem() { throw new Error("denied"); },
    setItem() { throw new Error("denied"); },
    removeItem() { throw new Error("denied"); },
  };
  assert.deepEqual(preferences.readObject("x", { safe: true }, denied), { safe: true });
  assert.equal(preferences.writeJson("x", {}, denied), false);
  assert.equal(preferences.remove("x", denied), false);
});

test("dashboard settings merge defaults", () => {
  const storage = memoryStorage({
    [preferences.DASHBOARD_SETTINGS_KEY]: JSON.stringify({ speedUnit: "kmh" }),
  });
  assert.deepEqual(
    preferences.readDashboardSettings({ speedUnit: "mph", tempUnit: "c" }, storage),
    { speedUnit: "kmh", tempUnit: "c" },
  );
});

test("API GET requests are no-store and parse JSON", async () => {
  let observed = null;
  global.fetch = async (path, init) => {
    observed = { path, init };
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  };
  assert.deepEqual(await api.getJson("/api/status"), { ok: true });
  assert.equal(observed.path, "/api/status");
  assert.equal(observed.init.cache, "no-store");
});

test("API POST requests preserve same-origin JSON semantics", async () => {
  let observed = null;
  global.fetch = async (path, init) => {
    observed = { path, init };
    return { ok: true, status: 200, json: async () => ({ performed_action: "play" }) };
  };
  const payload = await api.postJson("/api/bluetooth/control", { action: "play" });
  assert.deepEqual(payload, { performed_action: "play" });
  assert.equal(observed.init.method, "POST");
  assert.equal(observed.init.credentials, "same-origin");
  assert.equal(observed.init.headers["Content-Type"], "application/json");
  assert.equal(observed.init.body, JSON.stringify({ action: "play" }));
});

test("API errors expose status and server payload", async () => {
  global.fetch = async () => ({
    ok: false,
    status: 403,
    json: async () => ({ error: "denied" }),
  });
  await assert.rejects(
    () => api.getJson("/api/private", { usePayloadError: true }),
    (error) => error.message === "denied" && error.status === 403,
  );
});


test("API connection observers distinguish transport loss from HTTP errors", async () => {
  const events = [];
  const unsubscribe = api.subscribeConnection((detail) => events.push(detail));

  global.fetch = async () => ({
    ok: false,
    status: 503,
    json: async () => ({ error: "busy" }),
  });
  await assert.rejects(() => api.getJson("/api/busy", { usePayloadError: true }));
  assert.equal(events.at(-1).reachable, true, "an HTTP response proves the dashboard server is reachable");
  assert.equal(events.at(-1).status, 503);

  global.fetch = async () => { throw new TypeError("network offline"); };
  await assert.rejects(() => api.getJson("/api/status"), /network offline/);
  assert.equal(events.at(-1).reachable, false);
  assert.equal(events.at(-1).error.connection_unreachable, true);
  unsubscribe();
});

const status = require("../../ui/web_dashboard/static/status.js");

test("status store publishes snapshots and isolates subscribers", () => {
  const store = status.createStore();
  const seen = [];
  store.subscribe((payload, snapshot) => {
    seen.push([payload.value, snapshot.version]);
  });
  store.subscribe(() => { throw new Error("observer failure"); });

  const payload = { value: 7 };
  assert.equal(store.publish(payload), payload);
  assert.deepEqual(seen, [[7, 1]]);
  assert.equal(store.getSnapshot().payload, payload);
  assert.equal(store.getSnapshot().error, null);
});

test("status store supports current-value delivery and unsubscribe", () => {
  const store = status.createStore({ value: 1 });
  const seen = [];
  const unsubscribe = store.subscribe((payload) => seen.push(payload.value), { emitCurrent: true });
  store.publish({ value: 2 });
  unsubscribe();
  store.publish({ value: 3 });
  assert.deepEqual(seen, [1, 2]);
});

test("status poller preserves immediate and fixed-interval polling", async () => {
  let intervalCallback = null;
  let intervalDelay = null;
  let cleared = null;
  const scheduler = {
    setInterval(callback, delay) {
      intervalCallback = callback;
      intervalDelay = delay;
      return 42;
    },
    clearInterval(identifier) {
      cleared = identifier;
    },
  };
  const payloads = [{ sequence: 1 }, { sequence: 2 }];
  const api = {
    async getJson(path, options) {
      assert.equal(path, "/api/status");
      assert.deepEqual(options, { requireOk: false });
      return payloads.shift();
    },
  };
  const seen = [];
  const poller = status.createPoller({ api, scheduler, onPayload: (payload) => seen.push(payload.sequence) });

  assert.equal(poller.start(), true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(intervalDelay, 200);
  assert.deepEqual(seen, [1]);
  await intervalCallback();
  assert.deepEqual(seen, [1, 2]);
  assert.equal(poller.stop(), true);
  assert.equal(cleared, 42);
  assert.equal(poller.isRunning(), false);
});


test("status poller pauses while hidden and never overlaps requests", async () => {
  let intervalCallback = null;
  let cleared = 0;
  const listeners = new Map();
  const document = {
    visibilityState: "visible",
    hidden: false,
    addEventListener(name, callback) { listeners.set(name, callback); },
    removeEventListener(name) { listeners.delete(name); },
  };
  const scheduler = {
    setInterval(callback) { intervalCallback = callback; return 7; },
    clearInterval() { cleared += 1; intervalCallback = null; },
  };
  let resolveRequest = null;
  let calls = 0;
  const poller = status.createPoller({
    document,
    scheduler,
    api: {
      getJson() {
        calls += 1;
        return new Promise((resolve) => { resolveRequest = resolve; });
      },
    },
  });

  poller.start();
  assert.equal(calls, 1);
  const overlapping = poller.fetchStatus();
  assert.equal(calls, 1);
  assert.equal(poller.getMetrics().overlapping_fetches_skipped, 1);
  resolveRequest({ sequence: 1 });
  await overlapping;

  document.hidden = true;
  document.visibilityState = "hidden";
  listeners.get("visibilitychange")();
  assert.equal(cleared, 1);
  assert.equal(intervalCallback, null);

  document.hidden = false;
  document.visibilityState = "visible";
  listeners.get("visibilitychange")();
  assert.equal(calls, 2);
  resolveRequest({ sequence: 2 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(poller.getMetrics().visibility_pauses, 1);
  assert.equal(poller.getMetrics().visibility_resumes, 1);
  poller.stop();
});

test("status poller records failures and keeps the render error path separate", async () => {
  const failure = new Error("offline");
  const errors = [];
  const store = status.createStore({ previous: true });
  const poller = status.createPoller({
    api: { async getJson() { throw failure; } },
    store,
    scheduler: { setInterval() { return 1; }, clearInterval() {} },
    onError: (error) => errors.push(error),
  });

  assert.equal(await poller.fetchStatus(), null);
  assert.equal(store.getSnapshot().payload.previous, true);
  assert.equal(store.getSnapshot().error, failure);
  assert.deepEqual(errors, [failure]);
});

const navigation = require("../../ui/web_dashboard/static/navigation.js");
const overlays = require("../../ui/web_dashboard/static/overlays.js");
const vehicle = require("../../ui/web_dashboard/static/vehicle.js");
const media = require("../../ui/web_dashboard/static/media.js");
const radioMedia = require("../../ui/web_dashboard/static/media-radio.js");
const usbMedia = require("../../ui/web_dashboard/static/media-usb.js");

function fakeClassList() {
  const values = new Set();
  return {
    contains(name) { return values.has(name); },
    toggle(name, force) {
      if (force === undefined ? !values.has(name) : force) values.add(name);
      else values.delete(name);
      return values.has(name);
    },
  };
}

test("navigation normalises quick-page indices in both directions", () => {
  assert.equal(navigation.normaliseIndex(0), 0);
  assert.equal(navigation.normaliseIndex(3), 0);
  assert.equal(navigation.normaliseIndex(-1), 2);
  assert.equal(navigation.normaliseIndex("invalid"), navigation.HOME_INDEX);
});

test("navigation recognises editable controls before applying global shortcuts", () => {
  const input = {
    closest(selector) {
      return selector.includes("input") ? this : null;
    },
  };
  const body = { closest() { return null; } };
  assert.equal(navigation.isEditableTarget(input), true);
  assert.equal(navigation.isEditableTarget(body), false);
});

test("media settings keep the existing panel when source preferences are unchanged", () => {
  let writes = 0;
  let renderedRoot = null;
  const panel = {
    set innerHTML(value) {
      this._innerHTML = String(value);
      writes += 1;
      renderedRoot = { dataset: {} };
    },
    get innerHTML() { return this._innerHTML || ""; },
    querySelector(selector) {
      if (selector === '[data-openmmi-media-settings-panel="true"]') return renderedRoot;
      return null;
    },
  };
  const activeSection = { dataset: { openmmiSettingsSection: "media" } };
  const document = {
    querySelector(selector) {
      if (selector === "[data-openmmi-settings-section].active") return activeSection;
      if (selector === "#openmmiSettingsPanel") return panel;
      return null;
    },
    addEventListener() {},
  };
  const window = {
    document,
    addEventListener() {},
    requestAnimationFrame(callback) { callback(); },
    setTimeout(callback) { callback(); },
  };
  const preferences = {
    readObject() { return {}; },
    writeJson() {},
  };
  const controller = media.createController({ document, window, preferences });

  controller.renderSettingsPanel();
  const firstRoot = renderedRoot;
  controller.renderSettingsPanel();

  assert.equal(writes, 1);
  assert.equal(renderedRoot, firstRoot);
});

test("navigation controller owns active page, pager and page-change state", () => {
  const pages = ["pageElectrical", "pageHome", "pageDrive", "pageClimate"].map((id) => ({ id, classList: fakeClassList() }));
  const buttons = [0, 1, 2].map(() => ({ classList: fakeClassList() }));
  const title = { textContent: "" };
  const events = [];
  const document = {
    querySelector(selector) {
      if (selector === "#pageTitle") return title;
      if (selector.startsWith("#")) return pages.find((page) => page.id === selector.slice(1)) || null;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === ".page") return pages;
      if (selector === ".pager button") return buttons;
      return [];
    },
  };
  const window = {
    CustomEvent: class CustomEvent {
      constructor(type, init) { this.type = type; this.detail = init.detail; }
    },
    dispatchEvent(event) { events.push(event); },
  };
  const controller = navigation.createController({ document, window });

  controller.setPage(-1);
  assert.equal(controller.getSnapshot().activePageId, "pageDrive");
  assert.equal(title.textContent, "Drive");
  assert.equal(pages[2].classList.contains("active"), true);
  assert.equal(buttons[2].classList.contains("active"), true);

  controller.showPageById("pageClimate", "Climate", navigation.HOME_INDEX);
  assert.equal(controller.getSnapshot().activePageId, "pageClimate");
  assert.equal(title.textContent, "Climate");
  assert.deepEqual(events.at(-1).detail, { id: "pageClimate", title: "Climate", quickIndex: 1 });
});

test("door overlay visibility survives dismissal until the open set changes", () => {
  const openDoors = overlays.collectOpenDoors({
    state: {
      doors: { front_left: true, rear_right: false, locked: true },
      body: { boot_ajar: "open" },
    },
  });
  assert.deepEqual(openDoors, ["Boot", "Front left door"]);

  let state = overlays.reduceDoorOverlay(null, openDoors);
  assert.equal(state.visible, true);
  state = overlays.dismissDoorOverlay(state);
  assert.equal(state.visible, false);
  state = overlays.reduceDoorOverlay(state, openDoors);
  assert.equal(state.visible, false);
  state = overlays.reduceDoorOverlay(state, ["Front left door"]);
  assert.equal(state.visible, true);
  state = overlays.reduceDoorOverlay(state, []);
  assert.deepEqual(state, { currentSignature: "", dismissedSignature: "", visible: false });
});

test("reverse overlay detection and dismissal reset at the end of reverse", () => {
  assert.equal(overlays.reverseSelected({ state: { vehicle: { reverse_selected: true } } }), true);
  assert.equal(overlays.reverseSelected({ state: { transmission: { gear: "R" } } }), true);
  assert.equal(overlays.reverseSelected({ state: { vehicle: { reverse: false }, reverse_overlay_mode: "camera" } }), false);

  let state = overlays.reduceReverseOverlay(null, true);
  assert.deepEqual(state, { active: true, dismissedThisReverse: false, visible: true });
  state = overlays.dismissReverseOverlay(state);
  state = overlays.reduceReverseOverlay(state, true);
  assert.deepEqual(state, { active: true, dismissedThisReverse: true, visible: false });
  state = overlays.reduceReverseOverlay(state, false);
  assert.deepEqual(state, { active: false, dismissedThisReverse: false, visible: false });
  state = overlays.reduceReverseOverlay(state, true);
  assert.equal(state.visible, true);
});


test("vehicle renderer skips unchanged non-health DOM work", () => {
  const document = {
    documentElement: {
      style: { setProperty() {}, removeProperty() {} },
      classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  const renderer = vehicle.createRenderer({
    document,
    enhancements: false,
    preferences: { readDashboardSettings(defaults) { return defaults; } },
  });
  const payload = { health: { status: "ok", age_seconds: 0.1 }, state: { vehicle: { speed_kmh: 50 } } };
  renderer.render(payload);
  renderer.render({ health: { status: "ok", age_seconds: 0.2 }, state: { vehicle: { speed_kmh: 50 } } });
  const metrics = renderer.getMetrics();
  assert.equal(metrics.render_calls, 2);
  assert.equal(metrics.vehicle_renders, 1);
  assert.equal(metrics.unchanged_renders_skipped, 1);
});

test("vehicle view model formats representative imperial status", () => {
  const view = vehicle.buildViewModel({
    health: { status: "ok", age_seconds: 1.24 },
    state: {
      vehicle: { speed_kmh: 100, odometer_km: 1000, handbrake: true, reverse: false },
      engine: { speed_rpm: 2500, coolant_temp_c: 90 },
      electrical: { supply_voltage_v: 13.8 },
      fuel: {
        level_l: 42,
      },
      climate: {
        outside_temp_regulation_c: 10.5,
        outside_temp_unfiltered_c: 11,
        blower_load_percent: 37.5,
        recirculation_active: true,
        rear_window_heater_requested: false,
        compressor_active: true,
      },
      lighting: {
        dimmer_percent: 60,
        mode: "auto",
        lights_on: true,
        left_indicator: true,
        right_indicator: false,
        hazards: false,
        bulb_out: false,
      },
      doors: { front_left: true, any_open: true },
    },
  }, { speedUnit: "mph", tempUnit: "f" });

  assert.equal(view.health.status, "ok");
  assert.equal(view.health.ageText, "1.2s ago");
  assert.equal(view.fields.speed_mph, "62");
  assert.equal(view.fields.odo_mi, "621");
  assert.equal(view.fields.fuel_l, "42");
  assert.equal(view.fields.coolant_c, "194");
  assert.equal(view.fields.outside_reg_c, "50.9");
  assert.equal(view.fields.voltage_v, "13.8");
  assert.equal(view.fields.indicators, "Left");
  assert.equal(view.booleans.recirculation, true);
  assert.equal(view.doors.front_left, true);
  assert.equal(view.anyDoorOpen, true);
  assert.equal(view.units.speed_mph, "mph");
  assert.equal(view.units.fuel_l, "L");
  assert.equal(view.units.coolant_c, "°F");
});

test("vehicle view model preserves canonical recirculation fallback and missing data", () => {
  const legacy = vehicle.buildViewModel({
    state: { climate: { front_demist_air_request: false } },
  }, { speedUnit: "kmh", tempUnit: "c" });

  assert.equal(legacy.booleans.recirculation, false);
  assert.equal(legacy.fields.speed_mph, "--");
  assert.equal(legacy.fields.coolant_c, "--");
  assert.equal(legacy.fields.fuel_l, "--");
  assert.equal(legacy.health.status, "waiting");
  assert.equal(legacy.health.ageText, "--");
  assert.equal(legacy.units.speed_mph, "km/h");
  assert.equal(legacy.units.odo_mi, "km");
});

test("vehicle formatting utilities reject invalid values and respect units", () => {
  assert.equal(vehicle.formatNumber(null), "--");
  assert.equal(vehicle.formatNumber("unknown"), "unknown");
  assert.equal(vehicle.formatSpeedFromKmh("bad", 0, { speedUnit: "mph" }), "--");
  assert.equal(vehicle.formatSpeedFromKmh(80, 0, { speedUnit: "kmh" }), "80");
  assert.equal(vehicle.formatTempFromC(0, 0, { tempUnit: "f" }), "32");
  assert.equal(vehicle.indicatorLabel({ hazards: true }), "Hazards");
});

test("media source preferences choose active and fallback sources deterministically", () => {
  const prefs = media.normalisePreferences({
    mediaActiveSource: "radio",
    mediaDefaultSource: "usb",
    mediaSources: { jellyfin: false, radio: false, usb: true, bluetooth: false },
  });
  assert.equal(media.activeSourceFromPreferences(prefs), "usb");
  const enabled = media.updateSourceEnabled(prefs, "radio", true);
  const active = media.updateActiveSource(enabled, "radio");
  assert.equal(media.activeSourceFromPreferences(active), "radio");
  const disabled = media.updateSourceEnabled(active, "radio", false);
  assert.equal(media.activeSourceFromPreferences(disabled), "usb");
});

test("media source preferences reject unknown defaults and disabled selections", () => {
  const prefs = media.normalisePreferences({
    mediaActiveSource: "future",
    mediaDefaultSource: "missing",
    mediaSources: { jellyfin: true },
  });
  assert.equal(prefs.mediaActiveSource, "jellyfin");
  assert.equal(prefs.mediaDefaultSource, "jellyfin");
  assert.equal(media.updateDefaultSource(prefs, "radio").mediaDefaultSource, "jellyfin");
});

test("radio controller normalises locale filters and safe favourites", () => {
  assert.deepEqual(
    radioMedia.normaliseRadioFilterPreferences({}, "en-GB"),
    { country: "GB", language: "" },
  );
  assert.deepEqual(
    radioMedia.normaliseRadioFilterPreferences({ country: "de", language: "German" }, "en-GB"),
    { country: "DE", language: "German" },
  );
  const station = radioMedia.safeFavoriteStation({
    id: 42,
    source: "radio",
    name: "Test FM",
    country: "United Kingdom",
    bitrate: "128",
  });
  assert.equal(station.id, "42");
  assert.equal(station.is_live, true);
  assert.equal(station.bitrate, 128);
  assert.equal(radioMedia.safeFavoriteStation({ id: "x", source: "usb" }), null);
});

test("radio favourite filtering honours query, country and language", () => {
  const favourites = {
    one: { name: "London Jazz", artist: "UK", country_code: "GB", language: "English" },
    two: { name: "Berlin Rock", artist: "DE", country_code: "DE", language: "German" },
  };
  assert.deepEqual(
    radioMedia.filterRadioFavorites(favourites, "jazz", { country: "GB", language: "eng" }).map((item) => item.name),
    ["London Jazz"],
  );
  assert.deepEqual(
    radioMedia.filterRadioFavorites(favourites, "", { country: "DE", language: "" }).map((item) => item.name),
    ["Berlin Rock"],
  );
});

test("USB controller formats unresolved durations and builds scoped browse URLs", () => {
  assert.equal(usbMedia.formatUsbDuration(null), "…");
  assert.equal(usbMedia.formatUsbDuration(65), "1:05");
  assert.equal(usbMedia.formatUsbDuration(65, (value) => `duration:${value}`), "duration:65");
  const url = usbMedia.buildUsbBrowseUrl("folder-id", "live set", "recent");
  const parsed = new URL(url, "http://localhost");
  assert.equal(parsed.pathname, "/api/usb/browse");
  assert.equal(parsed.searchParams.get("dir"), "folder-id");
  assert.equal(parsed.searchParams.get("q"), "live set");
  assert.equal(parsed.searchParams.get("filter"), "recent");
  assert.equal(parsed.searchParams.get("limit"), "60");
});

const jellyfinMedia = require("../../ui/web_dashboard/static/media-jellyfin.js");
const bluetoothMedia = require("../../ui/web_dashboard/static/media-bluetooth.js");

test("Jellyfin player helpers escape labels and format durations", () => {
  assert.equal(jellyfinMedia.escapeHtml('<Track & "Artist">'), "&lt;Track &amp; &quot;Artist&quot;&gt;");
  assert.equal(jellyfinMedia.formatTime(65), "1:05");
  assert.equal(jellyfinMedia.formatTime(3661), "1:01:01");
  assert.equal(jellyfinMedia.formatTime(-1), "--:--");
});

test("Bluetooth controller normalises playback state and exposes its adapter", () => {
  assert.equal(bluetoothMedia.normalisePlaybackStatus({ playback_status: "PLAYING" }), "playing");
  assert.equal(bluetoothMedia.normalisePlaybackStatus({ playback_status: "unknown" }), "stopped");
  assert.equal(
    bluetoothMedia.effectivePlaybackStatus({ playback_status: "playing" }, "paused"),
    "paused",
  );
  assert.equal(
    bluetoothMedia.serverPlaybackStatusChanged("playing", { playback_status: "paused" }),
    true,
  );
  assert.equal(
    bluetoothMedia.serverPlaybackStatusChanged("paused", { playback_status: "PAUSED" }),
    false,
  );
  assert.equal(
    bluetoothMedia.serverPlaybackStatusChanged(null, { playback_status: "playing" }),
    false,
  );
  assert.deepEqual(
    bluetoothMedia.bluetoothAdapterDescriptor(),
    {
      id: "bluetooth",
      label: "Bluetooth",
      defaultFilter: "now",
      filters: { now: "Now playing" },
      searchPlaceholder: "Bluetooth uses the connected player",
      searchLabel: "Bluetooth media does not support library search",
      emptyText: "No Bluetooth track metadata is available.",
      loadingText: "Checking connected Bluetooth media…",
      readyText: "Controls are sent to the connected Bluetooth player.",
      statusUrl: "/api/bluetooth/status",
    },
  );
});

test("Bluetooth cleanup releases shared transport controls for other sources", () => {
  const buttons = new Map(
    ["#ommiMediaPlay", "#ommiMediaPrev", "#ommiMediaNext", "#ommiMediaStop"]
      .map((selector) => [selector, {
        disabled: true,
        attributes: new Map([["aria-disabled", "true"]]),
        setAttribute(name, value) { this.attributes.set(name, value); },
      }]),
  );
  const document = { querySelector(selector) { return buttons.get(selector) || null; } };

  bluetoothMedia.releaseSharedTransportControls(document);

  for (const button of buttons.values()) {
    assert.equal(button.disabled, false);
    assert.equal(button.attributes.get("aria-disabled"), "false");
  }
});
