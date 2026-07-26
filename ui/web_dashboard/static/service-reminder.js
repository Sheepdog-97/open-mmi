(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.openMmiServiceReminder = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function createServiceReminderModule(root) {
  "use strict";

  const ENDPOINT = "/api/system/service-reminder";
  const SETTINGS_ENDPOINT = "/api/system/service-reminder/settings";
  const RESET_ENDPOINT = "/api/system/service-reminder/reset";
  const KM_PER_MILE = 1.609344;
  const DEFAULT_SETTINGS = Object.freeze({ speedUnit: "mph" });

  function finite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function calendarDayNumber(value) {
    const date = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    return Math.floor(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()) / 86400000);
  }

  function daysBetween(today, dueDate) {
    const start = calendarDayNumber(today);
    const end = calendarDayNumber(`${dueDate}T00:00:00`);
    return start === null || end === null ? null : end - start;
  }

  function evaluate(snapshot = {}, currentOdometerKm, now = new Date()) {
    const settings = snapshot.settings || {};
    const reset = snapshot.reset || {};
    const nextDue = snapshot.next_due || {};
    const enabled = settings.enabled !== false;
    const resetOdometer = finite(reset.odometer_km);
    const currentOdometer = finite(currentOdometerKm);
    const dueOdometer = finite(nextDue.odometer_km);
    const dueDate = typeof nextDue.date === "string" ? nextDue.date : "";
    const configured = snapshot.configured === true
      && resetOdometer !== null
      && dueOdometer !== null
      && dueDate !== "";

    if (!enabled) return Object.freeze({ state: "disabled", configured, enabled });
    if (!configured) return Object.freeze({ state: "setup", configured: false, enabled });

    const daysRemaining = daysBetween(now, dueDate);
    if (daysRemaining === null) {
      return Object.freeze({ state: "invalid", configured: true, enabled, error: "Invalid inspection date" });
    }
    if (currentOdometer === null) {
      return Object.freeze({ state: "waiting", configured: true, enabled, daysRemaining });
    }
    if (currentOdometer < resetOdometer - 1) {
      return Object.freeze({
        state: "invalid-odometer",
        configured: true,
        enabled,
        currentOdometerKm: currentOdometer,
        resetOdometerKm: resetOdometer,
        daysRemaining,
      });
    }

    const distanceRemainingKm = dueOdometer - currentOdometer;
    const warningDistanceKm = Math.max(0, finite(settings.warning_distance_km) || 0);
    const warningDays = Math.max(0, finite(settings.warning_days) || 0);
    const due = distanceRemainingKm <= 0 || daysRemaining <= 0;
    const soon = !due && (distanceRemainingKm <= warningDistanceKm || daysRemaining <= warningDays);
    return Object.freeze({
      state: due ? "due" : (soon ? "soon" : "ok"),
      configured: true,
      enabled,
      distanceRemainingKm,
      daysRemaining,
      dueDate,
      dueOdometerKm: dueOdometer,
      resetDate: reset.reset_date,
      resetOdometerKm: resetOdometer,
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

  function distanceFromDisplay(value, settings = {}) {
    const number = finite(value);
    if (number === null) return null;
    return normaliseUnits(settings) === "km" ? number : number * KM_PER_MILE;
  }

  function formatDistance(kilometres, settings = {}, options = {}) {
    const number = distanceForDisplay(kilometres, settings);
    if (number === null) return "--";
    const rounded = options.absolute ? Math.abs(Math.round(number)) : Math.round(number);
    return `${rounded.toLocaleString()} ${normaliseUnits(settings)}`;
  }

  function formatDate(value) {
    if (!value) return "--";
    const date = new Date(`${value}T00:00:00`);
    if (Number.isNaN(date.getTime())) return "--";
    return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function statusSummary(result, settings = {}) {
    if (result.state === "disabled") return { label: "Inspection reminder off", detail: "" };
    if (result.state === "setup") return { label: "Inspection interval not set", detail: "Reset after servicing" };
    if (result.state === "waiting") return { label: "Waiting for odometer", detail: `${Math.max(0, result.daysRemaining)} days` };
    if (result.state === "invalid-odometer") return { label: "Check inspection reset", detail: "Odometer is below the saved reset value" };
    if (result.state === "invalid") return { label: "Inspection reminder unavailable", detail: result.error || "Invalid settings" };
    if (result.state === "due") {
      const overdue = [];
      if (result.distanceRemainingKm <= 0) overdue.push(`${formatDistance(result.distanceRemainingKm, settings, { absolute: true })} overdue`);
      if (result.daysRemaining <= 0) overdue.push(`${Math.abs(result.daysRemaining)} days overdue`);
      return { label: "Inspection now!", detail: overdue.join(" or ") };
    }
    const detail = `${formatDistance(Math.max(0, result.distanceRemainingKm), settings)} or ${Math.max(0, result.daysRemaining)} days`;
    return { label: result.state === "soon" ? "Inspection due soon" : "Next inspection", detail };
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function install(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const api = options.api || windowRef?.openMmiApi;
    const preferences = options.preferences || windowRef?.openMmiPreferences;
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

    function result() {
      return evaluate(snapshot || {}, currentOdometerKm, new Date());
    }

    function ensureIndicator() {
      let node = documentRef.querySelector("#openMmiServiceReminderIndicator");
      if (node) return node;
      const footer = documentRef.querySelector("footer.status-strip");
      if (!footer || !documentRef.createElement) return null;
      node = documentRef.createElement("div");
      node.id = "openMmiServiceReminderIndicator";
      node.className = "footer-item openmmi-service-reminder-indicator";
      node.hidden = true;
      node.innerHTML = '<span class="openmmi-service-spanner" aria-hidden="true">🔧</span><div><span>Inspection</span><strong data-openmmi-service-indicator-text>--</strong></div>';
      const pager = footer.querySelector?.(".pager");
      footer.insertBefore(node, pager || null);
      return node;
    }

    function renderIndicator() {
      const node = ensureIndicator();
      if (!node) return;
      const evaluated = result();
      const visible = evaluated.state === "soon" || evaluated.state === "due";
      node.hidden = !visible;
      node.classList?.toggle("is-due", evaluated.state === "due");
      const target = node.querySelector?.("[data-openmmi-service-indicator-text]");
      if (!target || !visible) return;
      target.textContent = evaluated.state === "due"
        ? "Inspection now!"
        : statusSummary(evaluated, dashboardSettings()).detail;
    }

    function inputValue(kilometres) {
      const value = distanceForDisplay(kilometres, dashboardSettings());
      return value === null ? "" : String(Math.round(value));
    }

    function updatePanelReadouts() {
      const host = documentRef.querySelector('[data-openmmi-service-reminder-panel="true"]');
      if (!host || !snapshot) return false;
      const units = dashboardSettings();
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const summaryNode = host.querySelector?.('[data-openmmi-service-summary]');
      if (summaryNode) {
        summaryNode.className = `openmmi-service-summary ${evaluated.state}`;
        const label = summaryNode.querySelector?.('[data-openmmi-service-summary-label]');
        const detail = summaryNode.querySelector?.('[data-openmmi-service-summary-detail]');
        if (label) label.textContent = summary.label;
        if (detail) detail.textContent = summary.detail;
      }
      const current = host.querySelector?.('[data-openmmi-service-current-odometer]');
      if (current) current.textContent = formatDistance(finite(currentOdometerKm), units);
      const resetButton = host.querySelector?.('[data-openmmi-service-reset]');
      if (resetButton) resetButton.disabled = busy || finite(currentOdometerKm) === null;
      return true;
    }

    function renderPanel() {
      const host = documentRef.querySelector('[data-openmmi-service-reminder-panel="true"]');
      if (!host) return;
      const units = dashboardSettings();
      const unit = normaliseUnits(units);
      if (!snapshot) {
        host.innerHTML = '<div class="openmmi-settings-panel-head"><span>Service</span><small>loading inspection reminder</small></div>';
        return;
      }
      const evaluated = result();
      const summary = statusSummary(evaluated, units);
      const settings = snapshot.settings || {};
      const reset = snapshot.reset || {};
      const nextDue = snapshot.next_due || {};
      const currentOdometer = finite(currentOdometerKm);
      const resetDisabled = busy || currentOdometer === null;
      const feedback = message
        ? `<p class="openmmi-config-message ${escapeHtml(messageKind)}" role="status">${escapeHtml(message)}</p>`
        : "";
      host.innerHTML = `
        <div class="openmmi-settings-panel-head"><span>Service</span><small>inspection reminder</small></div>
        <div class="openmmi-service-summary ${escapeHtml(evaluated.state)}" data-openmmi-service-summary>
          <span class="openmmi-service-summary-icon" aria-hidden="true">🔧</span>
          <div><strong data-openmmi-service-summary-label>${escapeHtml(summary.label)}</strong><small data-openmmi-service-summary-detail>${escapeHtml(summary.detail)}</small></div>
        </div>
        <div class="openmmi-settings-metric"><span>Last reset</span><strong>${escapeHtml(formatDate(reset.reset_date))}${reset.odometer_km == null ? "" : ` · ${escapeHtml(formatDistance(reset.odometer_km, units))}`}</strong></div>
        <div class="openmmi-settings-metric"><span>Next inspection</span><strong>${escapeHtml(formatDate(nextDue.date))}${nextDue.odometer_km == null ? "" : ` · ${escapeHtml(formatDistance(nextDue.odometer_km, units))}`}</strong></div>
        <div class="openmmi-settings-metric"><span>Current odometer</span><strong data-openmmi-service-current-odometer>${escapeHtml(formatDistance(currentOdometer, units))}</strong></div>
        <form class="openmmi-service-form openmmi-config-form" data-openmmi-service-form>
          <label class="openmmi-service-toggle"><input type="checkbox" name="enabled" ${settings.enabled !== false ? "checked" : ""}> <span>Inspection reminder enabled</span></label>
          <label><span>Distance interval</span><span class="openmmi-service-input"><input name="distance_interval" type="number" min="1" step="1" value="${escapeHtml(inputValue(settings.distance_interval_km))}" required><small>${escapeHtml(unit)}</small></span></label>
          <label><span>Time interval</span><span class="openmmi-service-input"><input name="time_interval_months" type="number" min="1" max="120" step="1" value="${escapeHtml(settings.time_interval_months)}" required><small>months</small></span></label>
          <label><span>Advance distance warning</span><span class="openmmi-service-input"><input name="warning_distance" type="number" min="0" step="1" value="${escapeHtml(inputValue(settings.warning_distance_km))}" required><small>${escapeHtml(unit)}</small></span></label>
          <label><span>Advance time warning</span><span class="openmmi-service-input"><input name="warning_days" type="number" min="0" max="3650" step="1" value="${escapeHtml(settings.warning_days)}" required><small>days</small></span></label>
          <div class="openmmi-config-actions openmmi-service-actions">
            <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-service-save ${busy ? "disabled" : ""}>Save intervals</button>
            <button type="button" class="openmmi-setting-pill" data-openmmi-service-reset ${resetDisabled ? "disabled" : ""}>Reset inspection interval</button>
          </div>
        </form>
        <p class="openmmi-config-secret-note">Reset stores the current confirmed odometer and host date. It does not change the vehicle cluster service interval.</p>
        ${feedback}
      `;
    }

    function render() {
      renderIndicator();
      renderPanel();
    }

    async function refresh() {
      try {
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        message = "";
        messageKind = "";
      } catch (error) {
        message = error?.message || "Inspection reminder could not be loaded";
        messageKind = "error";
      }
      render();
      return snapshot;
    }

    async function save(form) {
      if (busy) return;
      const data = new windowRef.FormData(form);
      const intervalKm = distanceFromDisplay(data.get("distance_interval"), dashboardSettings());
      const warningKm = distanceFromDisplay(data.get("warning_distance"), dashboardSettings());
      busy = true;
      message = "Saving inspection intervals…";
      messageKind = "";
      renderPanel();
      try {
        snapshot = await api.postJson(SETTINGS_ENDPOINT, {
          enabled: data.get("enabled") === "on",
          distance_interval_km: intervalKm,
          time_interval_months: Number(data.get("time_interval_months")),
          warning_distance_km: warningKm,
          warning_days: Number(data.get("warning_days")),
        }, { usePayloadError: true });
        message = "Inspection intervals saved";
        messageKind = "success";
      } catch (error) {
        message = error?.message || "Inspection intervals could not be saved";
        messageKind = "error";
      } finally {
        busy = false;
        render();
      }
    }

    async function resetInterval() {
      const odometer = finite(currentOdometerKm);
      if (busy || odometer === null) return;
      const displayed = formatDistance(odometer, dashboardSettings());
      if (!windowRef.confirm(`Reset the inspection interval using the current odometer of ${displayed}?`)) return;
      busy = true;
      message = "Resetting inspection interval…";
      messageKind = "";
      renderPanel();
      try {
        snapshot = await api.postJson(RESET_ENDPOINT, { confirm: true, odometer_km: odometer }, { usePayloadError: true });
        message = "Inspection interval reset";
        messageKind = "success";
      } catch (error) {
        message = error?.message || "Inspection interval could not be reset";
        messageKind = "error";
      } finally {
        busy = false;
        render();
      }
    }

    documentRef.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("[data-openmmi-service-form]");
      if (!form) return;
      event.preventDefault();
      save(form);
    });

    function activateServiceAction(target) {
      const saveButton = target?.closest?.("[data-openmmi-service-save]");
      if (saveButton) {
        const form = saveButton.closest?.("[data-openmmi-service-form]");
        if (form) save(form);
        return true;
      }
      if (target?.closest?.("[data-openmmi-service-reset]")) {
        resetInterval();
        return true;
      }
      return false;
    }

    documentRef.addEventListener("click", (event) => {
      if (!activateServiceAction(event.target)) return;
      event.preventDefault();
      event.stopImmediatePropagation?.();
    }, true);
    documentRef.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (!activateServiceAction(event.target)) return;
      event.preventDefault();
      event.stopImmediatePropagation?.();
    }, true);
    windowRef.addEventListener?.("openmmi:settingsrender", renderPanel);
    windowRef.addEventListener?.("openmmi:pagechange", renderPanel);

    refresh();
    return Object.freeze({
      update(payload = {}) {
        currentOdometerKm = finite(payload?.state?.vehicle?.odometer_km);
        renderIndicator();
        updatePanelReadouts();
      },
      refresh,
      snapshot: () => snapshot,
      evaluate: () => result(),
    });
  }

  return Object.freeze({
    ENDPOINT,
    SETTINGS_ENDPOINT,
    RESET_ENDPOINT,
    KM_PER_MILE,
    evaluate,
    distanceForDisplay,
    distanceFromDisplay,
    formatDistance,
    statusSummary,
    install,
  });
});
