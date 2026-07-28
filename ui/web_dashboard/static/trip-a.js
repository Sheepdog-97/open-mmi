(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.openMmiTripA = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function createTripAModule(root) {
  "use strict";

  const ENDPOINT = "/api/system/trip-a";
  const RESET_ENDPOINT = "/api/system/trip-a/reset";
  const SETTINGS_ENDPOINT = "/api/system/trip-a/settings";
  const OBSERVE_ENDPOINT = "/api/system/trip-a/observe";
  const OBSERVE_INTERVAL_MS = 5 * 60 * 1000;
  const KM_PER_MILE = 1.609344;
  const DEFAULT_SETTINGS = Object.freeze({ speedUnit: "mph" });

  function finite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function evaluate(snapshot = {}, currentOdometerKm, currentDistanceTotalKm) {
    const reset = snapshot.reset || {};
    const resetOdometer = finite(reset.odometer_km);
    const resetDistanceTotal = finite(reset.distance_total_km);
    const currentOdometer = finite(currentOdometerKm);
    const currentDistanceTotal = finite(currentDistanceTotalKm);
    const configured = snapshot.configured === true && resetOdometer !== null;
    if (!configured) return Object.freeze({ state: "setup", configured: false, tripKm: null });

    if (resetDistanceTotal !== null && currentDistanceTotal !== null) {
      if (currentDistanceTotal < resetDistanceTotal - 0.01) {
        return Object.freeze({ state: "invalid-distance", configured: true, resetDistanceTotalKm: resetDistanceTotal, currentDistanceTotalKm: currentDistanceTotal, tripKm: null });
      }
      return Object.freeze({
        state: "ok",
        configured: true,
        precise: true,
        resetOdometerKm: resetOdometer,
        currentOdometerKm: currentOdometer,
        resetDistanceTotalKm: resetDistanceTotal,
        currentDistanceTotalKm: currentDistanceTotal,
        tripKm: Math.max(0, currentDistanceTotal - resetDistanceTotal),
      });
    }

    if (currentOdometer === null) return Object.freeze({ state: "waiting", configured: true, resetOdometerKm: resetOdometer, tripKm: null });
    if (currentOdometer < resetOdometer - 1) {
      return Object.freeze({ state: "invalid-odometer", configured: true, resetOdometerKm: resetOdometer, currentOdometerKm: currentOdometer, tripKm: null });
    }
    return Object.freeze({
      state: "ok",
      configured: true,
      precise: false,
      resetOdometerKm: resetOdometer,
      currentOdometerKm: currentOdometer,
      tripKm: Math.max(0, currentOdometer - resetOdometer),
    });
  }

  function normaliseUnits(settings = {}) {
    return Object.assign({}, DEFAULT_SETTINGS, settings || {}).speedUnit === "kmh" ? "km" : "mi";
  }

  function distanceForDisplay(kilometres, settings = {}) {
    const number = finite(kilometres);
    if (number === null) return null;
    return normaliseUnits(settings) === "km" ? number : number / KM_PER_MILE;
  }

  function formatDistance(kilometres, settings = {}) {
    const number = distanceForDisplay(kilometres, settings);
    if (number === null) return "--";
    return Math.max(0, Math.round(number)).toLocaleString();
  }

  function formatDistanceWithUnit(kilometres, settings = {}) {
    const value = formatDistance(kilometres, settings);
    return value === "--" ? value : `${value} ${normaliseUnits(settings)}`;
  }

  function formatTripDistance(kilometres, settings = {}) {
    const number = distanceForDisplay(kilometres, settings);
    if (number === null) return "--";
    return Math.max(0, number).toLocaleString(undefined, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  }

  function formatTripDistanceWithUnit(kilometres, settings = {}) {
    const value = formatTripDistance(kilometres, settings);
    return value === "--" ? value : `${value} ${normaliseUnits(settings)}`;
  }

  function formatTimestamp(value) {
    if (!value) return "--";
    const timestamp = new Date(value);
    if (Number.isNaN(timestamp.getTime())) return "--";
    return timestamp.toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function autoResetLabel(hours) {
    const value = Math.max(0, Math.round(finite(hours) || 0));
    return value === 0 ? "Off" : `After ${value} hour${value === 1 ? "" : "s"} parked`;
  }

  function statusSummary(result, settings = {}) {
    if (result.state === "setup") return { label: "Trip A not set", detail: "Reset Trip A to start" };
    if (result.state === "waiting") return { label: "Waiting for odometer", detail: "Trip A will update when vehicle data is available" };
    if (result.state === "invalid-odometer") return { label: "Check Trip A reset", detail: "Odometer is below the saved reset value" };
    if (result.state === "invalid-distance") return { label: "Check Trip A reset", detail: "Trip distance is below the saved reset value" };
    return { label: "Trip A", detail: formatTripDistanceWithUnit(result.tripKm, settings) };
  }

  function install(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const api = options.api || windowRef?.openMmiApi;
    const preferences = options.preferences || windowRef?.openMmiPreferences;
    const distance = options.distance || windowRef?.openMmiTripDistanceController;
    const now = options.now || (() => Date.now());
    if (!documentRef || !api) return Object.freeze({ update() {}, refresh: async () => null });

    let snapshot = null;
    let currentOdometerKm = null;
    let vehiclePresent = false;
    let busy = false;
    let observing = false;
    let lastObservedAt = 0;
    let message = "";
    let messageKind = "";

    function dashboardSettings() {
      if (!preferences || typeof preferences.readDashboardSettings !== "function") return { ...DEFAULT_SETTINGS };
      return preferences.readDashboardSettings(DEFAULT_SETTINGS);
    }

    function currentDistanceTotalKm() {
      return typeof distance?.currentTotalKm === "function" ? finite(distance.currentTotalKm()) : null;
    }

    function result() { return evaluate(snapshot || {}, currentOdometerKm, currentDistanceTotalKm()); }

    function updateDashboardReadouts() {
      const units = dashboardSettings();
      const evaluated = result();
      const value = formatTripDistance(evaluated.tripKm, units);
      const unit = normaliseUnits(units);
      Array.from(documentRef.querySelectorAll?.("[data-openmmi-trip-a]") || []).forEach((node) => { node.textContent = value; });
      Array.from(documentRef.querySelectorAll?.("[data-openmmi-trip-a-unit]") || []).forEach((node) => { node.textContent = unit; });
    }

    function updatePanelReadouts() {
      const host = documentRef.querySelector('[data-openmmi-trip-a-panel="true"]');
      if (!host || !snapshot) return false;
      const units = dashboardSettings();
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const summaryNode = host.querySelector?.("[data-openmmi-trip-a-summary]");
      if (summaryNode) {
        summaryNode.className = `openmmi-service-summary ${evaluated.state}`;
        const label = summaryNode.querySelector?.("[data-openmmi-trip-a-summary-label]");
        const detail = summaryNode.querySelector?.("[data-openmmi-trip-a-summary-detail]");
        if (label) label.textContent = summary.label;
        if (detail) detail.textContent = summary.detail;
      }
      const current = host.querySelector?.("[data-openmmi-trip-a-current-odometer]");
      if (current) current.textContent = formatDistanceWithUnit(currentOdometerKm, units);
      const resetButton = host.querySelector?.("[data-openmmi-trip-a-reset]");
      if (resetButton) resetButton.disabled = busy || finite(currentOdometerKm) === null;
      return true;
    }

    function renderPanel() {
      const host = documentRef.querySelector('[data-openmmi-trip-a-panel="true"]');
      if (!host) return;
      if (!snapshot) {
        host.innerHTML = '<div class="openmmi-settings-panel-head"><span>Trip A</span><small>loading trip state</small></div>';
        return;
      }
      const units = dashboardSettings();
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const reset = snapshot.reset || {};
      const autoResetHours = Math.max(0, Math.round(finite(snapshot?.settings?.auto_reset_hours) || 0));
      const currentOdometer = finite(currentOdometerKm);
      const feedback = message ? `<p class="openmmi-config-message ${escapeHtml(messageKind)}" role="status">${escapeHtml(message)}</p>` : "";
      host.innerHTML = `
        <div class="openmmi-settings-panel-head"><span>Trip A</span><small>short-term high-resolution trip counter</small></div>
        <div class="openmmi-service-summary ${escapeHtml(evaluated.state)}" data-openmmi-trip-a-summary>
          <span class="openmmi-service-summary-icon" aria-hidden="true">A</span>
          <div><strong data-openmmi-trip-a-summary-label>${escapeHtml(summary.label)}</strong><small data-openmmi-trip-a-summary-detail>${escapeHtml(summary.detail)}</small></div>
        </div>
        <div class="openmmi-settings-metric"><span>Last reset</span><strong>${escapeHtml(formatTimestamp(reset.reset_at))}${reset.odometer_km == null ? "" : ` · ${escapeHtml(formatDistanceWithUnit(reset.odometer_km, units))}`}</strong></div>
        <div class="openmmi-settings-metric"><span>Current odometer</span><strong data-openmmi-trip-a-current-odometer>${escapeHtml(formatDistanceWithUnit(currentOdometer, units))}</strong></div>
        <form class="openmmi-service-form openmmi-config-form" data-openmmi-trip-a-settings-form>
          <label><span>Automatic reset</span><span class="openmmi-service-input"><select name="auto_reset_hours" aria-label="Trip A automatic reset"><option value="0"${autoResetHours === 0 ? " selected" : ""}>Off</option><option value="2"${autoResetHours === 2 ? " selected" : ""}>After 2 hours parked</option><option value="4"${autoResetHours === 4 ? " selected" : ""}>After 4 hours parked</option><option value="8"${autoResetHours === 8 ? " selected" : ""}>After 8 hours parked</option><option value="12"${autoResetHours === 12 ? " selected" : ""}>After 12 hours parked</option><option value="24"${autoResetHours === 24 ? " selected" : ""}>After 24 hours parked</option></select></span></label>
          <div class="openmmi-config-actions openmmi-service-actions">
            <button type="button" class="openmmi-setting-pill" data-openmmi-trip-a-save ${busy ? "disabled" : ""}>Save automatic reset</button>
            <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-trip-a-reset ${busy || currentOdometer === null ? "disabled" : ""}>Reset Trip A</button>
          </div>
        </form>
        <p class="openmmi-config-secret-note">${escapeHtml(autoResetLabel(autoResetHours))}. Automatic reset occurs on the next vehicle session after the parked interval; Trip B is never reset automatically.</p>
        ${feedback}
      `;
    }

    function render() { updateDashboardReadouts(); renderPanel(); }

    async function refresh() {
      try {
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        message = "";
        messageKind = "";
      } catch (error) {
        message = error?.message || "Trip A could not be loaded";
        messageKind = "error";
      }
      render();
      return snapshot;
    }

    async function saveSettings(form) {
      if (busy) return;
      const data = new windowRef.FormData(form);
      busy = true;
      message = "Saving Trip A settings…";
      messageKind = "";
      renderPanel();
      try {
        snapshot = await api.postJson(SETTINGS_ENDPOINT, { auto_reset_hours: Number(data.get("auto_reset_hours")) }, { usePayloadError: true });
        lastObservedAt = 0;
        message = "Trip A settings saved";
        messageKind = "success";
      } catch (error) {
        message = error?.message || "Trip A settings could not be saved";
        messageKind = "error";
      } finally {
        busy = false;
        render();
      }
    }

    async function resetTrip() {
      const odometer = finite(currentOdometerKm);
      if (busy || odometer === null) return;
      const displayed = formatDistanceWithUnit(odometer, dashboardSettings());
      if (!windowRef.confirm(`Reset Trip A using the current odometer of ${displayed}?`)) return;
      busy = true;
      message = "Resetting Trip A…";
      messageKind = "";
      renderPanel();
      try {
        const payload = { confirm: true, odometer_km: odometer };
        const distanceTotal = currentDistanceTotalKm();
        if (distanceTotal !== null) payload.distance_total_km = distanceTotal;
        snapshot = await api.postJson(RESET_ENDPOINT, payload, { usePayloadError: true });
        lastObservedAt = now();
        message = "Trip A reset";
        messageKind = "success";
      } catch (error) {
        message = error?.message || "Trip A could not be reset";
        messageKind = "error";
      } finally {
        busy = false;
        render();
      }
    }

    async function observeVehicle() {
      const odometer = finite(currentOdometerKm);
      const autoResetHours = finite(snapshot?.settings?.auto_reset_hours) || 0;
      const currentTime = now();
      if (observing || !vehiclePresent || !snapshot?.configured || autoResetHours <= 0 || odometer === null) return;
      if (currentTime - lastObservedAt < OBSERVE_INTERVAL_MS) return;
      observing = true;
      lastObservedAt = currentTime;
      try {
        const payload = { odometer_km: odometer };
        const distanceTotal = currentDistanceTotalKm();
        if (distanceTotal !== null) payload.distance_total_km = distanceTotal;
        const next = await api.postJson(OBSERVE_ENDPOINT, payload, { usePayloadError: true });
        snapshot = next;
        if (next.auto_reset === true) {
          message = "Trip A reset automatically after the parked interval";
          messageKind = "success";
        }
        render();
      } catch (_error) {
        // Activity heartbeats are best effort; normal Trip A display remains usable.
      } finally {
        observing = false;
      }
    }

    function activateTripAction(target) {
      const saveButton = target?.closest?.("[data-openmmi-trip-a-save]");
      if (saveButton) {
        const form = saveButton.closest?.("[data-openmmi-trip-a-settings-form]");
        if (form) saveSettings(form);
        return true;
      }
      if (target?.closest?.("[data-openmmi-trip-a-reset]")) {
        resetTrip();
        return true;
      }
      return false;
    }

    documentRef.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("[data-openmmi-trip-a-settings-form]");
      if (!form) return;
      event.preventDefault();
      saveSettings(form);
    });
    documentRef.addEventListener("click", (event) => {
      if (!activateTripAction(event.target)) return;
      event.preventDefault();
      event.stopImmediatePropagation?.();
    }, true);
    documentRef.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (!activateTripAction(event.target)) return;
      event.preventDefault();
      event.stopImmediatePropagation?.();
    }, true);
    windowRef.addEventListener?.("openmmi:settingsrender", renderPanel);
    windowRef.addEventListener?.("openmmi:pagechange", renderPanel);
    windowRef.addEventListener?.("openmmi:settingschange", () => { updateDashboardReadouts(); updatePanelReadouts(); });

    refresh();
    return Object.freeze({
      update(payload = {}) {
        currentOdometerKm = finite(payload?.state?.vehicle?.odometer_km);
        vehiclePresent = payload?.state?.vehicle?.present === true;
        updateDashboardReadouts();
        updatePanelReadouts();
        observeVehicle();
      },
      refresh,
      snapshot: () => snapshot,
      evaluate: () => result(),
    });
  }

  return Object.freeze({
    ENDPOINT,
    RESET_ENDPOINT,
    SETTINGS_ENDPOINT,
    OBSERVE_ENDPOINT,
    OBSERVE_INTERVAL_MS,
    KM_PER_MILE,
    evaluate,
    distanceForDisplay,
    formatDistance,
    formatDistanceWithUnit,
    formatTripDistance,
    formatTripDistanceWithUnit,
    autoResetLabel,
    statusSummary,
    install,
  });
});
