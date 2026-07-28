(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.openMmiTripB = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function createTripBModule(root) {
  "use strict";

  const ENDPOINT = "/api/system/trip-b";
  const RESET_ENDPOINT = "/api/system/trip-b/reset";
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

  function statusSummary(result, settings = {}) {
    if (result.state === "setup") return { label: "Trip B not set", detail: "Reset Trip B to start" };
    if (result.state === "waiting") return { label: "Waiting for odometer", detail: "Trip B will update when vehicle data is available" };
    if (result.state === "invalid-odometer") return { label: "Check Trip B reset", detail: "Odometer is below the saved reset value" };
    if (result.state === "invalid-distance") return { label: "Check Trip B reset", detail: "Trip distance is below the saved reset value" };
    return { label: "Trip B", detail: formatTripDistanceWithUnit(result.tripKm, settings) };
  }

  function install(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const api = options.api || windowRef?.openMmiApi;
    const preferences = options.preferences || windowRef?.openMmiPreferences;
    const distance = options.distance || windowRef?.openMmiTripDistanceController;
    if (!documentRef || !api) return Object.freeze({ update() {}, refresh: async () => null });

    let snapshot = null;
    let currentOdometerKm = null;
    let busy = false;
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
      Array.from(documentRef.querySelectorAll?.("[data-openmmi-trip-b]") || []).forEach((node) => { node.textContent = value; });
      Array.from(documentRef.querySelectorAll?.("[data-openmmi-trip-b-unit]") || []).forEach((node) => { node.textContent = unit; });
    }

    function updatePanelReadouts() {
      const host = documentRef.querySelector('[data-openmmi-trip-b-panel="true"]');
      if (!host || !snapshot) return false;
      const units = dashboardSettings();
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const summaryNode = host.querySelector?.("[data-openmmi-trip-b-summary]");
      if (summaryNode) {
        summaryNode.className = `openmmi-service-summary ${evaluated.state}`;
        const label = summaryNode.querySelector?.("[data-openmmi-trip-b-summary-label]");
        const detail = summaryNode.querySelector?.("[data-openmmi-trip-b-summary-detail]");
        if (label) label.textContent = summary.label;
        if (detail) detail.textContent = summary.detail;
      }
      const current = host.querySelector?.("[data-openmmi-trip-b-current-odometer]");
      if (current) current.textContent = formatDistanceWithUnit(currentOdometerKm, units);
      const resetButton = host.querySelector?.("[data-openmmi-trip-b-reset]");
      if (resetButton) resetButton.disabled = busy || finite(currentOdometerKm) === null;
      return true;
    }

    function renderPanel() {
      const host = documentRef.querySelector('[data-openmmi-trip-b-panel="true"]');
      if (!host) return;
      if (!snapshot) {
        host.innerHTML = '<div class="openmmi-settings-panel-head"><span>Trip B</span><small>loading trip state</small></div>';
        return;
      }
      const units = dashboardSettings();
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const reset = snapshot.reset || {};
      const currentOdometer = finite(currentOdometerKm);
      const feedback = message ? `<p class="openmmi-config-message ${escapeHtml(messageKind)}" role="status">${escapeHtml(message)}</p>` : "";
      host.innerHTML = `
        <div class="openmmi-settings-panel-head"><span>Trip B</span><small>long-term high-resolution trip counter</small></div>
        <div class="openmmi-service-summary ${escapeHtml(evaluated.state)}" data-openmmi-trip-b-summary>
          <span class="openmmi-service-summary-icon" aria-hidden="true">B</span>
          <div><strong data-openmmi-trip-b-summary-label>${escapeHtml(summary.label)}</strong><small data-openmmi-trip-b-summary-detail>${escapeHtml(summary.detail)}</small></div>
        </div>
        <div class="openmmi-settings-metric"><span>Last reset</span><strong>${escapeHtml(formatTimestamp(reset.reset_at))}${reset.odometer_km == null ? "" : ` · ${escapeHtml(formatDistanceWithUnit(reset.odometer_km, units))}`}</strong></div>
        <div class="openmmi-settings-metric"><span>Current odometer</span><strong data-openmmi-trip-b-current-odometer>${escapeHtml(formatDistanceWithUnit(currentOdometer, units))}</strong></div>
        <div class="openmmi-config-actions openmmi-service-actions">
          <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-trip-b-reset ${busy || currentOdometer === null ? "disabled" : ""}>Reset Trip B</button>
        </div>
        <p class="openmmi-config-secret-note">Trip B is intended for a longer manually reset interval and is not affected by Trip A automatic reset.</p>
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
        message = error?.message || "Trip B could not be loaded";
        messageKind = "error";
      }
      render();
      return snapshot;
    }

    async function resetTrip() {
      const odometer = finite(currentOdometerKm);
      if (busy || odometer === null) return;
      const displayed = formatDistanceWithUnit(odometer, dashboardSettings());
      if (!windowRef.confirm(`Reset Trip B using the current odometer of ${displayed}?`)) return;
      busy = true;
      message = "Resetting Trip B…";
      messageKind = "";
      renderPanel();
      try {
        const payload = { confirm: true, odometer_km: odometer };
        const distanceTotal = currentDistanceTotalKm();
        if (distanceTotal !== null) payload.distance_total_km = distanceTotal;
        snapshot = await api.postJson(RESET_ENDPOINT, payload, { usePayloadError: true });
        message = "Trip B reset";
        messageKind = "success";
      } catch (error) {
        message = error?.message || "Trip B could not be reset";
        messageKind = "error";
      } finally {
        busy = false;
        render();
      }
    }

    function activateTripAction(target) {
      if (!target?.closest?.("[data-openmmi-trip-b-reset]")) return false;
      resetTrip();
      return true;
    }

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
        updateDashboardReadouts();
        updatePanelReadouts();
      },
      refresh,
      snapshot: () => snapshot,
      evaluate: () => result(),
    });
  }

  return Object.freeze({ ENDPOINT, RESET_ENDPOINT, KM_PER_MILE, evaluate, distanceForDisplay, formatDistance, formatDistanceWithUnit, formatTripDistance, formatTripDistanceWithUnit, statusSummary, install });
});
