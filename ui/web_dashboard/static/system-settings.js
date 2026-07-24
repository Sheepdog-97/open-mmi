(function openMmiSystemSettingsModule(root, factory) {
  const moduleApi = factory(root);
  if (typeof module === "object" && module.exports) module.exports = moduleApi;
  if (root) root.openMmiSystemSettings = moduleApi;
})(typeof globalThis !== "undefined" ? globalThis : this, function createSystemSettingsModule(root) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function createController(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const api = options.api || windowRef?.openMmiApi;
    if (!windowRef || !documentRef || !api) throw new Error("System settings require window, document and API client");

    let snapshot = null;
    let updateSnapshot = null;
    let updateReadinessSnapshot = null;
    let updateCoordinatorSnapshot = null;
    let busy = false;
    let updateBusy = "";
    let message = "";
    let messageKind = "";
    let lastSystemHtml = "";
    let lastJellyfinHtml = "";
    let jellyfinDraft = null;
    let transactionPollTimer = null;
    let transactionPollDeadline = 0;

    function activeSection() {
      return documentRef.querySelector("[data-openmmi-settings-section].active")?.dataset?.openmmiSettingsSection || "";
    }

    function setMessage(text, kind = "") {
      message = String(text || "");
      messageKind = kind;
      const banner = documentRef.querySelector(".openmmi-config-message[role='status']");
      if (banner) {
        banner.textContent = message;
        banner.hidden = !message;
        if (banner.classList) {
          banner.classList.remove("error", "warning", "success");
          if (messageKind) banner.classList.add(messageKind);
        } else {
          banner.className = `openmmi-config-message ${messageKind}`.trim();
        }
        return;
      }
      renderActive();
    }

    function statusBanner(extraClass = "") {
      const classes = ["openmmi-config-message", extraClass, messageKind]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" ");
      return `<div class="${classes}" role="status"${message ? "" : " hidden"}>${escapeHtml(message)}</div>`;
    }

    function pill(label, selected, attributes) {
      return `<button type="button" class="openmmi-setting-pill${selected ? " is-selected" : ""}" ${attributes} aria-pressed="${selected ? "true" : "false"}">${escapeHtml(label)}</button>`;
    }

    function row(title, note, controls) {
      return `<div class="openmmi-setting-row"><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(note)}</small></div><div class="openmmi-setting-controls">${controls}</div></div>`;
    }

    function frontendVersionSnapshot() {
      try {
        return windowRef.__openMmiFrontendVersionController?.snapshot?.() || {};
      } catch (_) {
        return {};
      }
    }

    function frontendVersionStateLabel(value) {
      const labels = {
        current: "up to date",
        reloading: "applying update",
        "update-ready": "reload ready",
        "mismatch-after-reload": "reload required",
        reconnecting: "checking…",
        unavailable: "unavailable",
      };
      return labels[String(value || "")] || String(value || "unavailable");
    }

    function updateStateLabel(value) {
      const labels = {
        "not-checked": "not checked",
        "up-to-date": "up to date",
        "update-available": "update available",
        "remote-different": "remote differs",
        "local-ahead": "local source ahead",
        diverged: "source diverged",
        "downgrade-blocked": "downgrade blocked",
        "release-rewritten": "release tag changed",
        unavailable: "check unavailable",
        blocked: "check blocked",
        "source-unavailable": "source not configured",
        "source-invalid": "source invalid",
      };
      return labels[String(value || "")] || String(value || "unavailable");
    }

    function repositoryStateLabel(value) {
      const labels = {
        ready: "ready",
        dirty: "local changes",
        "source-changed": "source differs from installed",
        detached: "detached HEAD",
        "branch-mismatch": "different branch",
        "channel-source-mismatch": "channel/source mismatch",
        "untrusted-remote": "untrusted remote",
        unavailable: "unavailable",
        unconfigured: "not configured",
        invalid: "invalid configuration",
      };
      return labels[String(value || "")] || String(value || "unavailable");
    }

    function transactionStateLabel(value) {
      const labels = {
        idle: "idle",
        preparing: "preparing…",
        downloading: "downloading…",
        validating: "validating…",
        prepared: "ready to install",
        installing: "installing…",
        complete: "complete",
        failed: "failed",
        unavailable: "coordinator unavailable",
      };
      return labels[String(value || "")] || String(value || "unavailable");
    }

    function readinessBlockerLabel(value) {
      const key = String(value || "").trim();
      const labels = {
        "repository-source-changed": "update source is not ready",
        "managed-source": "update source is not ready",
        "active-transaction": "another update is active",
        "disk-space": "not enough disk space",
        "required-commands": "required system tools are unavailable",
        "privileged-coordinator": "update service is unavailable",
        "execution-authorization": "update installation is not authorised",
        "configuration-preservation": "configuration cannot be preserved",
        power: "power requirements are not met",
        thermal: "thermal limits prevent installation",
        "service-health": "required services are unhealthy",
        "readiness-unavailable": "readiness is unavailable",
      };
      return labels[key] || key.replaceAll("-", " ");
    }

    function readinessCheckMessage(check) {
      const summary = String(check?.summary || "").trim();
      if (!summary) return "";
      if (String(check?.code || "") === "power" && check?.ac_online !== true) {
        const capacity = Number(check?.capacity_percent);
        if (Number.isFinite(capacity)) return `${summary} (battery ${Math.round(capacity)}%)`;
      }
      const missing = Array.isArray(check?.missing) ? check.missing.map(String).filter(Boolean) : [];
      if (missing.length) return `${summary}: ${missing.join(", ")}`;
      return summary;
    }

    function updateControlState() {
      const transaction = updateCoordinatorSnapshot?.state || {};
      const transactionState = String(transaction.state || "unavailable");
      const transactionActive = ["preparing", "downloading", "validating", "installing"].includes(transactionState);
      const connectionState = String(documentRef.body?.dataset?.openmmiDashboardConnection || "");
      const dashboardReady = !connectionState || connectionState === "ready";
      const readinessReady = updateReadinessSnapshot?.state === "ready"
        && updateReadinessSnapshot?.install_allowed === true;
      const managedSourceReady = updateSnapshot?.readiness?.state === "ready"
        && updateSnapshot?.source?.state === "ready";
      const coordinatorReady = updateCoordinatorSnapshot?.ok === true;
      const installationEnabled = coordinatorReady
        && updateCoordinatorSnapshot?.installation_enabled === true;
      return Object.freeze({
        transactionActive,
        transactionState,
        readinessReady,
        managedSourceReady,
        canCheck: dashboardReady && !updateBusy && !transactionActive,
        canPrepare: dashboardReady
          && !updateBusy
          && !transactionActive
          && readinessReady
          && managedSourceReady
          && installationEnabled
          && updateCoordinatorSnapshot?.preparation_enabled === true
          && updateSnapshot?.update?.update_available === true,
        canInstall: dashboardReady
          && !updateBusy
          && readinessReady
          && managedSourceReady
          && installationEnabled
          && transactionState === "prepared",
      });
    }

    function scheduleTransactionPoll() {
      const state = String(updateCoordinatorSnapshot?.state?.state || "unavailable");
      const active = ["preparing", "downloading", "validating", "installing"].includes(state);
      const timeoutMs = Math.max(1000, Number(options.updateActionTimeoutMs || 370000));
      if (active && !transactionPollDeadline) transactionPollDeadline = Date.now() + timeoutMs;
      if (["prepared", "complete", "failed", "idle"].includes(state)) transactionPollDeadline = 0;
      const recovering = state === "unavailable" && transactionPollDeadline > Date.now();
      if (documentRef.hidden || transactionPollTimer || updateBusy || (!active && !recovering)) return;
      const pollIntervalMs = Math.max(25, Number(options.updatePollIntervalMs || 500));
      transactionPollTimer = windowRef.setTimeout(async () => {
        transactionPollTimer = null;
        if (documentRef.hidden) return;
        await refreshUpdateTransaction(false);
        const nextState = String(updateCoordinatorSnapshot?.state?.state || "unavailable");
        const nextError = transactionError();
        if (nextState === "complete" || nextState === "failed") {
          await refreshUpdateStatus();
          if (nextState === "complete") setMessage("Update installed successfully", "success");
          else setMessage(nextError || "Update transaction failed", "error");
        } else {
          renderSystem();
        }
        scheduleTransactionPoll();
      }, pollIntervalMs);
    }

    function checkedAtLabel(value) {
      const text = String(value || "").trim();
      if (!text) return "never";
      return text.replace("T", " ").replace("+00:00", " UTC");
    }

    function updateCheckMessage(payload) {
      const state = String(payload?.update?.state || "");
      if (state === "up-to-date") return ["Open MMI is up to date", "success"];
      if (state === "update-available") return ["An update is available", "warning"];
      if (["remote-different", "local-ahead", "diverged", "release-rewritten"].includes(state)) {
        return [payload?.update?.error || "The tracked source differs; installation direction is not assumed", "warning"];
      }
      if (state === "downgrade-blocked") {
        return [payload?.update?.error || "The selected channel would require a downgrade", "warning"];
      }
      if (["blocked", "source-unavailable", "source-invalid"].includes(state)) {
        return [payload?.update?.error || "Update check is blocked", "warning"];
      }
      if (state === "unavailable") return [payload?.update?.error || "Update check is unavailable", "error"];
      return ["Update status refreshed", "success"];
    }

    function systemTemplate() {
      const launcher = snapshot?.launcher || {};
      const version = frontendVersionSnapshot();
      const defaultUi = launcher.default_ui || "web";
      const autostart = launcher.open_at_login === true;
      const reachable = launcher.dashboard_reachable ? "reachable" : "unreachable";
      const loadedVersion = version.loadedId || "--";
      const serverVersion = version.serverId || "--";
      const versionState = frontendVersionStateLabel(version.state);
      const installed = updateSnapshot?.installed || {};
      const update = updateSnapshot?.update || {};
      const source = updateSnapshot?.source || {};
      const installedVersion = installed.version || "--";
      const availableVersion = update.available_version || "--";
      const channel = updateSnapshot?.channel || "unconfigured";
      const updateState = updateStateLabel(update.state || "not-checked");
      const repositoryState = repositoryStateLabel(source.state || "unconfigured");
      const lastChecked = checkedAtLabel(update.checked_at);
      const readiness = updateReadinessSnapshot || {};
      const coordinator = updateCoordinatorSnapshot || {};
      const transaction = coordinator.state || {};
      const controls = updateControlState();
      const readinessBlockers = Array.from(new Set([
        ...(Array.isArray(updateSnapshot?.readiness?.blockers) ? updateSnapshot.readiness.blockers : []),
        ...(Array.isArray(readiness.blockers) ? readiness.blockers : []),
      ].map(readinessBlockerLabel).filter(Boolean)));
      const readinessUnknown = Array.from(new Set(
        (Array.isArray(readiness.unknown) ? readiness.unknown : [])
          .map(readinessBlockerLabel)
          .filter(Boolean),
      ));
      const readinessChecks = Array.isArray(readiness.checks) ? readiness.checks : [];
      const readinessIssues = Array.from(new Set(
        readinessChecks
          .filter((check) => ["block", "unknown"].includes(String(check?.state || "")))
          .map(readinessCheckMessage)
          .filter(Boolean),
      ));
      const readinessFallbackIssues = Array.from(new Set([
        ...readinessBlockers,
        ...readinessUnknown,
      ]));
      const visibleReadinessIssues = readinessIssues.length ? readinessIssues : readinessFallbackIssues;
      const readinessIssueHtml = (!controls.readinessReady || !controls.managedSourceReady)
        && visibleReadinessIssues.length
        ? `<p class="openmmi-config-message warning openmmi-update-readiness-detail" data-testid="system-update-readiness-detail"><strong>Update unavailable:</strong> ${escapeHtml(visibleReadinessIssues.join(". "))}</p>`
        : "";
      const readinessLabel = controls.readinessReady && controls.managedSourceReady
        ? "ready"
        : readinessBlockers.length
          ? `blocked: ${readinessBlockers.join(", ")}`
          : controls.managedSourceReady
            ? "unavailable"
            : "blocked: update source is not ready";
      const transactionLabel = transactionStateLabel(controls.transactionState);
      const transactionIsHistory = ["complete", "failed"].includes(controls.transactionState);
      const transactionTitle = transactionIsHistory ? "Last update" : "Update progress";
      const targetTitle = transactionIsHistory ? "Last update version" : "Update version";
      const targetVersion = String(transaction.target_version || "").trim();
      const targetHtml = targetVersion
        ? `<div class="openmmi-settings-metric"><span data-testid="system-update-target-label">${escapeHtml(targetTitle)}</span><strong data-testid="system-update-target">${escapeHtml(targetVersion)}</strong></div>`
        : "";
      const visibleMessage = String(message || "").trim();
      const updateErrorText = String(update.error || "").trim();
      const updateError = updateErrorText && updateErrorText !== visibleMessage
        ? `<p class="openmmi-update-status-note" data-testid="system-update-error">${escapeHtml(updateErrorText)}</p>`
        : "";
      const transactionError = String(transaction.error || coordinator.error || "").trim();
      const transactionErrorHtml = transactionError && transactionError !== visibleMessage
        ? `<p class="openmmi-update-status-note" data-testid="system-update-transaction-error">${escapeHtml(transactionError)}</p>`
        : "";
      return `
        <div data-openmmi-system-settings-panel="true" data-openmmi-system-settings-ready="true">
          <div class="openmmi-settings-panel-head"><span>System</span><small>desktop shell and updates</small></div>
          <div class="openmmi-settings-metric"><span>Dashboard version</span><strong data-testid="system-frontend-version">${escapeHtml(loadedVersion)}</strong></div>
          <div class="openmmi-settings-metric"><span>Server version</span><strong data-testid="system-server-version">${escapeHtml(serverVersion)}</strong></div>
          <div class="openmmi-settings-metric"><span>Frontend state</span><strong data-testid="system-version-state">${escapeHtml(versionState)}</strong></div>
          <div class="openmmi-settings-metric"><span>Health endpoint</span><strong>${escapeHtml(reachable)}</strong></div>
          <div class="openmmi-settings-subhead"><span>Software updates</span><small>managed installation</small></div>
          <div data-openmmi-update-status="true" aria-live="polite">
            <div class="openmmi-settings-metric"><span>Installed version</span><strong data-testid="system-installed-version">${escapeHtml(installedVersion)}</strong></div>
            <div class="openmmi-settings-metric"><span>Channel</span><strong data-testid="system-update-channel">${escapeHtml(channel)}</strong></div>
            <div class="openmmi-settings-metric"><span>Available version</span><strong data-testid="system-available-version">${escapeHtml(availableVersion)}</strong></div>
            <div class="openmmi-settings-metric"><span>Update status</span><strong data-testid="system-update-state">${escapeHtml(updateState)}</strong></div>
            <div class="openmmi-settings-metric"><span>Last checked</span><strong data-testid="system-update-checked-at">${escapeHtml(lastChecked)}</strong></div>
            <div class="openmmi-settings-metric"><span data-testid="system-update-transaction-label">${escapeHtml(transactionTitle)}</span><strong data-testid="system-update-transaction">${escapeHtml(transactionLabel)}</strong></div>
            ${targetHtml}
            ${updateError}
            ${transactionErrorHtml}
            ${readinessIssueHtml}
            <div class="openmmi-config-actions openmmi-update-actions">
              <button type="button" class="openmmi-setting-pill" data-openmmi-update-check="true" data-testid="system-update-check" ${controls.canCheck ? "" : "disabled"}>${updateBusy === "checking" ? "Checking…" : "Check for updates"}</button>
              <button type="button" class="openmmi-setting-pill" data-openmmi-update-prepare="true" data-testid="system-update-prepare" ${controls.canPrepare ? "" : "disabled"}>${updateBusy === "preparing" ? "Preparing…" : "Prepare update"}</button>
              <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-update-install="true" data-testid="system-update-install" ${controls.canInstall ? "" : "disabled"}>${updateBusy === "installing" ? "Installing…" : "Install update"}</button>
            </div>
            ${statusBanner("openmmi-update-feedback")}
            <p class="openmmi-update-status-note">Nightly follows the newest available Open MMI build. Updates start only when you choose them and roll back automatically if validation fails.</p>
            <details class="openmmi-update-technical" data-testid="system-update-technical">
              <summary>Technical details</summary>
              <div class="openmmi-settings-metric"><span>Repository health</span><strong data-testid="system-update-repository">${escapeHtml(repositoryState)}</strong></div>
              <div class="openmmi-settings-metric"><span>Installation readiness</span><strong data-testid="system-update-readiness">${escapeHtml(readinessLabel)}</strong></div>
            </details>
          </div>
          ${row("Default interface", "Used by the desktop icon and open-mmi-launcher without arguments.",
            pill("Web", defaultUi === "web", 'data-openmmi-launcher-ui="web" data-openmmi-requires-dashboard="true" data-testid="launcher-default-web"')
            + pill("TUI", defaultUi === "tui", 'data-openmmi-launcher-ui="tui" data-openmmi-requires-dashboard="true" data-testid="launcher-default-tui"'))}
          ${row("Open Open MMI at login", "Launch the remembered interface after graphical login. The launcher starts the dashboard service when needed.",
            pill("off", !autostart, 'data-openmmi-launcher-autostart="false" data-openmmi-requires-dashboard="true" data-testid="launcher-autostart-off"')
            + pill("on", autostart, 'data-openmmi-launcher-autostart="true" data-openmmi-requires-dashboard="true" data-testid="launcher-autostart-on"'))}
          <button type="button" class="openmmi-settings-link openmmi-config-refresh" data-openmmi-system-refresh="true" data-openmmi-requires-dashboard="true" ${busy ? "disabled" : ""}>Refresh status</button>
        </div>`;
    }

    function effectiveJellyfinConfig() {
      return Object.assign({}, snapshot?.jellyfin || {}, jellyfinDraft || {});
    }

    function jellyfinTemplate() {
      const config = effectiveJellyfinConfig();
      const authMode = config.auth_mode || "username";
      const restartNote = config.restart_required
        ? '<div class="openmmi-config-message warning">Saved credentials differ from the running dashboard. Restart the dashboard to activate them.</div>'
        : "";
      return `
        <div data-openmmi-jellyfin-settings="true">
          <div class="openmmi-settings-panel-head"><span>Jellyfin setup</span><small>server-side credentials</small></div>
          ${statusBanner()}
          ${restartNote}
          <form id="openMmiJellyfinSettingsForm" class="openmmi-config-form" autocomplete="off">
            <label><span>Server URL</span><input name="url" type="url" required value="${escapeHtml(config.url || "")}" placeholder="https://jellyfin.example:8096" data-testid="jellyfin-url"></label>
            <label><span>Authentication</span><select name="auth_mode" data-testid="jellyfin-auth-mode"><option value="username"${authMode === "username" ? " selected" : ""}>Username and password</option><option value="token"${authMode === "token" ? " selected" : ""}>API token</option></select></label>
            <label><span>Username / scope user</span><input name="username" value="${escapeHtml(config.username || "")}" autocomplete="username" data-testid="jellyfin-username"></label>
            <label data-openmmi-jellyfin-secret="password"${authMode === "token" ? " hidden" : ""}><span>Password</span><input name="password" type="password" autocomplete="new-password" value="${escapeHtml(config.password || "")}" placeholder="${config.password_configured ? "Leave blank to keep saved password" : "Required"}" data-testid="jellyfin-password"></label>
            <label data-openmmi-jellyfin-secret="token"${authMode === "token" ? "" : " hidden"}><span>API token</span><input name="token" type="password" autocomplete="off" value="${escapeHtml(config.token || "")}" placeholder="${config.token_configured ? "Leave blank to keep saved token" : "Required"}" data-testid="jellyfin-token"></label>
            <label><span>User ID (optional)</span><input name="user_id" value="${escapeHtml(config.user_id || "")}"></label>
            <label><span>Library ID (optional)</span><input name="library_id" value="${escapeHtml(config.library_id || "")}"></label>
            <div class="openmmi-config-checks">
              <label><input name="insecure_tls" type="checkbox"${config.insecure_tls ? " checked" : ""}> Allow insecure TLS</label>
              <label><input name="allow_global" type="checkbox"${config.allow_global ? " checked" : ""}> Allow legacy global API-key scope</label>
            </div>
            <p class="openmmi-config-secret-note">Passwords and tokens are written to <code>${escapeHtml(config.path || "~/.config/open-mmi/dashboard.env")}</code> with mode 0600. They are never returned to this browser.</p>
            <div class="openmmi-config-actions">
              <button type="button" class="openmmi-setting-pill" data-openmmi-jellyfin-test="true" data-openmmi-requires-dashboard="true" data-testid="jellyfin-test" ${busy ? "disabled" : ""}>test</button>
              <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-jellyfin-save="true" data-openmmi-requires-dashboard="true" data-testid="jellyfin-save" ${busy ? "disabled" : ""}>save</button>
              <button type="button" class="openmmi-setting-pill" data-openmmi-jellyfin-clear="true" data-openmmi-requires-dashboard="true" data-testid="jellyfin-clear" ${busy ? "disabled" : ""}>clear</button>
              ${config.restart_required ? `<button type="button" class="openmmi-setting-pill" data-openmmi-dashboard-restart="true" data-openmmi-requires-dashboard="true" ${busy ? "disabled" : ""}>restart dashboard</button>` : ""}
            </div>
          </form>
        </div>`;
    }

    function renderSystem() {
      const panel = documentRef.querySelector("#openmmiSettingsPanel");
      if (activeSection() !== "system" || !panel) return;
      const html = systemTemplate();
      const missing = !panel.querySelector?.('[data-openmmi-system-settings-ready="true"]');
      if (html !== lastSystemHtml || missing) {
        lastSystemHtml = html;
        panel.innerHTML = html;
      }
    }

    function focusedControlState(host) {
      const active = documentRef.activeElement;
      if (!active || !host?.contains?.(active)) return null;
      return {
        name: String(active.name || ""),
        testid: String(active.dataset?.testid || active.getAttribute?.("data-testid") || ""),
        selectionStart: Number.isInteger(active.selectionStart) ? active.selectionStart : null,
        selectionEnd: Number.isInteger(active.selectionEnd) ? active.selectionEnd : null,
        selectionDirection: active.selectionDirection || "none",
      };
    }

    function restoreFocusedControl(host, state) {
      if (!host || !state) return;
      const controls = Array.from(host.querySelectorAll?.("input, select, textarea, button") || []);
      const control = controls.find((candidate) => (
        (state.testid && String(candidate.dataset?.testid || candidate.getAttribute?.("data-testid") || "") === state.testid)
        || (state.name && String(candidate.name || "") === state.name)
      ));
      if (!control || typeof control.focus !== "function") return;
      try { control.focus({ preventScroll: true }); }
      catch (_) { control.focus(); }
      if (
        state.selectionStart !== null
        && state.selectionEnd !== null
        && typeof control.setSelectionRange === "function"
      ) {
        try { control.setSelectionRange(state.selectionStart, state.selectionEnd, state.selectionDirection); }
        catch (_) {}
      }
    }

    function renderJellyfin() {
      if (activeSection() !== "media") return;
      let host = documentRef.querySelector("#openMmiJellyfinSettingsHost");
      const panel = documentRef.querySelector("#openmmiSettingsPanel");
      if (!host && panel) {
        host = documentRef.createElement("div");
        host.id = "openMmiJellyfinSettingsHost";
        panel.appendChild(host);
      }
      if (host) {
        const html = jellyfinTemplate();
        const missing = !host.querySelector?.('[data-openmmi-jellyfin-settings="true"]');
        if (html !== lastJellyfinHtml || missing) {
          const focusState = focusedControlState(host);
          lastJellyfinHtml = html;
          host.innerHTML = html;
          restoreFocusedControl(host, focusState);
        }
      }
    }

    function renderActive() {
      if (activeSection() === "system") renderSystem();
      if (activeSection() === "media") renderJellyfin();
    }

    async function refreshUpdateStatus() {
      try {
        updateSnapshot = await api.getJson("/api/system/update-status", { usePayloadError: true });
      } catch (error) {
        updateSnapshot = {
          channel: "unavailable",
          installed: { version: "--" },
          source: { state: "unavailable" },
          update: { state: "unavailable", checked_at: null, error: error?.message || "Could not load update status" },
        };
      }
      await Promise.all([refreshUpdateReadiness(false), refreshUpdateTransaction(false)]);
      renderSystem();
      scheduleTransactionPoll();
      return updateSnapshot;
    }

    async function refreshUpdateReadiness(render = true) {
      try {
        updateReadinessSnapshot = await api.getJson("/api/system/update-readiness", { usePayloadError: true });
      } catch (error) {
        updateReadinessSnapshot = {
          state: "blocked",
          install_allowed: false,
          blockers: ["readiness-unavailable"],
          error: error?.message || "Could not inspect update readiness",
        };
      }
      if (render) renderSystem();
      return updateReadinessSnapshot;
    }

    async function refreshUpdateTransaction(render = true) {
      try {
        updateCoordinatorSnapshot = await api.getJson("/api/system/update-coordinator", { usePayloadError: true });
      } catch (error) {
        updateCoordinatorSnapshot = {
          ok: false,
          preparation_enabled: false,
          installation_enabled: false,
          error: error?.message || "Could not reach the update coordinator",
          state: { state: "unavailable", target_version: "", error: "" },
        };
      }
      if (render) renderSystem();
      return updateCoordinatorSnapshot;
    }

    async function refresh() {
      try {
        snapshot = await api.getJson("/api/system/settings", { usePayloadError: true });
        message = "";
        messageKind = "";
      } catch (error) {
        message = error?.message || "Could not load local system settings";
        messageKind = "error";
      }
      await refreshUpdateStatus();
      renderActive();
      return snapshot;
    }

    async function checkForUpdates() {
      updateBusy = "checking";
      renderSystem();
      try {
        updateSnapshot = await api.postJson("/api/system/update-check", { confirm: true }, { usePayloadError: true });
        await Promise.all([refreshUpdateReadiness(false), refreshUpdateTransaction(false)]);
        const [text, kind] = updateCheckMessage(updateSnapshot);
        setMessage(text, kind);
        return updateSnapshot;
      } catch (error) {
        setMessage(error?.message || "Update check failed", "error");
        throw error;
      } finally {
        updateBusy = "";
        renderSystem();
      }
    }

    function confirmed(messageText) {
      return typeof windowRef.confirm === "function" && windowRef.confirm(messageText) === true;
    }

    function transactionError() {
      return String(updateCoordinatorSnapshot?.state?.error || updateCoordinatorSnapshot?.error || "").trim();
    }

    async function runUpdateAction(path, successStates) {
      const pollIntervalMs = Math.max(25, Number(options.updatePollIntervalMs || 500));
      const timeoutMs = Math.max(1000, Number(options.updateActionTimeoutMs || 370000));
      const deadline = Date.now() + timeoutMs;
      let requestDone = false;
      let requestResult = null;
      let requestError = null;
      const request = api.postJson(path, { confirm: true }, { usePayloadError: true })
        .then((result) => {
          requestResult = result;
          if (result?.state) updateCoordinatorSnapshot = result;
          return result;
        })
        .catch((error) => {
          requestError = error;
          return null;
        })
        .finally(() => { requestDone = true; });

      while (Date.now() < deadline) {
        await Promise.resolve();
        const responseState = String(requestResult?.state?.state || "");
        if (successStates.includes(responseState)) return requestResult;
        if (responseState === "failed") throw new Error(requestResult?.state?.error || "Update transaction failed");

        if (!requestDone) {
          await Promise.race([
            request,
            new Promise((resolve) => windowRef.setTimeout(resolve, pollIntervalMs)),
          ]);
        } else if (requestError?.connection_unreachable) {
          await new Promise((resolve) => windowRef.setTimeout(resolve, pollIntervalMs));
        } else {
          await new Promise((resolve) => windowRef.setTimeout(resolve, pollIntervalMs));
        }

        await refreshUpdateTransaction(false);
        renderSystem();
        const state = String(updateCoordinatorSnapshot?.state?.state || "");
        if (successStates.includes(state)) return updateCoordinatorSnapshot;
        if (state === "failed") throw new Error(transactionError() || "Update transaction failed");
        if (requestDone && requestError && !requestError.connection_unreachable) throw requestError;
      }
      throw new Error("Timed out waiting for the update transaction");
    }

    async function prepareUpdate() {
      const controls = updateControlState();
      if (!controls.canPrepare) throw new Error("No installable managed update is ready to prepare");
      const available = updateSnapshot?.update?.available_version || "the available update";
      if (!confirmed(`Download and verify ${available} for installation?`)) return null;
      updateBusy = "preparing";
      setMessage("Downloading and verifying the update…", "warning");
      renderSystem();
      try {
        const result = await runUpdateAction("/api/system/update-prepare", ["prepared"]);
        await refreshUpdateStatus();
        setMessage(`Update ${result?.state?.target_version || available} is verified and ready to install`, "success");
        return result;
      } catch (error) {
        setMessage(error?.message || "Update preparation failed", "error");
        throw error;
      } finally {
        updateBusy = "";
        renderSystem();
      }
    }

    async function installUpdate() {
      const controls = updateControlState();
      if (!controls.canInstall) throw new Error("No verified update is ready to install");
      const target = updateCoordinatorSnapshot?.state?.target_version || "the prepared update";
      if (!confirmed(`Install ${target} now? Open MMI services will restart automatically.`)) return null;
      updateBusy = "installing";
      setMessage("Installing the verified update; Open MMI will reconnect automatically…", "warning");
      renderSystem();
      try {
        const result = await runUpdateAction("/api/system/update-install", ["complete"]);
        await refresh();
        setMessage(`Update ${result?.state?.target_version || target} installed successfully`, "success");
        return result;
      } catch (error) {
        setMessage(error?.message || "Update installation failed", "error");
        throw error;
      } finally {
        updateBusy = "";
        renderSystem();
      }
    }

    async function post(path, payload, successMessage, options = {}) {
      busy = true;
      try {
        const result = await api.postJson(path, payload, { usePayloadError: true });
        if (options.clearJellyfinDraft) jellyfinDraft = null;
        if (options.refresh !== false) await refresh();
        setMessage(successMessage, "success");
        return result;
      } catch (error) {
        setMessage(error?.message || "Configuration operation failed", "error");
        throw error;
      } finally {
        busy = false;
      }
    }

    function captureJellyfinDraft() {
      const form = documentRef.querySelector("#openMmiJellyfinSettingsForm");
      if (!form) throw new Error("Jellyfin form is unavailable");
      const data = new windowRef.FormData(form);
      jellyfinDraft = {
        url: data.get("url") || "",
        auth_mode: data.get("auth_mode") || "username",
        username: data.get("username") || "",
        password: data.get("password") || "",
        token: data.get("token") || "",
        user_id: data.get("user_id") || "",
        library_id: data.get("library_id") || "",
        insecure_tls: data.get("insecure_tls") === "on",
        allow_global: data.get("allow_global") === "on",
      };
      return Object.assign({}, jellyfinDraft);
    }

    function jellyfinPayload() {
      return captureJellyfinDraft();
    }

    async function restartAndWait() {
      await api.postJson("/api/system/dashboard/restart", { confirm: true }, { usePayloadError: true });
      setMessage("Dashboard is restarting…", "success");
      for (let attempt = 0; attempt < 40; attempt += 1) {
        await new Promise((resolve) => windowRef.setTimeout(resolve, 250));
        try {
          await api.getJson("/api/health");
          await refresh();
          setMessage("Dashboard restarted", "success");
          return;
        } catch (_) {}
      }
      setMessage("Restart requested; refresh if the dashboard does not reconnect", "warning");
    }

    async function clickHandler(event) {
      const target = event.target.closest?.("button");
      if (!target) return;
      if (target.dataset.openmmiLauncherUi) {
        await post("/api/system/launcher", { default_ui: target.dataset.openmmiLauncherUi }, "Default interface saved");
      } else if (target.dataset.openmmiLauncherAutostart) {
        await post("/api/system/launcher", { open_at_login: target.dataset.openmmiLauncherAutostart === "true" }, "Login launch preference saved");
      } else if (target.dataset.openmmiSystemRefresh) {
        await refresh();
      } else if (target.dataset.openmmiUpdateCheck) {
        await checkForUpdates();
      } else if (target.dataset.openmmiUpdatePrepare) {
        await prepareUpdate();
      } else if (target.dataset.openmmiUpdateInstall) {
        await installUpdate();
      } else if (target.dataset.openmmiJellyfinTest) {
        await post("/api/system/jellyfin/test", jellyfinPayload(), "Jellyfin connection succeeded", { refresh: false });
      } else if (target.dataset.openmmiJellyfinSave) {
        await post("/api/system/jellyfin", jellyfinPayload(), "Jellyfin settings saved; restart the dashboard to activate them", { clearJellyfinDraft: true });
      } else if (target.dataset.openmmiJellyfinClear) {
        await post("/api/system/jellyfin/clear", { confirm: true }, "Jellyfin credentials cleared", { clearJellyfinDraft: true });
      } else if (target.dataset.openmmiDashboardRestart) {
        busy = true;
        renderActive();
        try { await restartAndWait(); } finally { busy = false; renderActive(); }
      } else if (target.closest?.('[data-openmmi-settings-section="system"], [data-openmmi-settings-section="media"]')) {
        windowRef.requestAnimationFrame(() => refresh());
      }
    }

    async function submitHandler(event) {
      if (event.target?.id !== "openMmiJellyfinSettingsForm") return;
      event.preventDefault();
      await post("/api/system/jellyfin", jellyfinPayload(), "Jellyfin settings saved; restart the dashboard to activate them", { clearJellyfinDraft: true });
    }

    function inputHandler(event) {
      if (!event.target?.closest?.("#openMmiJellyfinSettingsForm")) return;
      try { captureJellyfinDraft(); } catch (_) {}
    }

    function changeHandler(event) {
      if (!event.target?.closest?.("#openMmiJellyfinSettingsForm")) return;
      try { captureJellyfinDraft(); } catch (_) {}
      if (event.target?.name !== "auth_mode") return;
      const mode = event.target.value;
      documentRef.querySelector('[data-openmmi-jellyfin-secret="password"]')?.toggleAttribute("hidden", mode === "token");
      documentRef.querySelector('[data-openmmi-jellyfin-secret="token"]')?.toggleAttribute("hidden", mode !== "token");
    }

    documentRef.addEventListener("click", (event) => { clickHandler(event).catch(() => {}); });
    documentRef.addEventListener("submit", (event) => { submitHandler(event).catch(() => {}); });
    documentRef.addEventListener("input", inputHandler);
    documentRef.addEventListener("change", changeHandler);
    documentRef.addEventListener("visibilitychange", scheduleTransactionPoll);
    windowRef.addEventListener("openmmi:settingsrender", () => windowRef.requestAnimationFrame(renderActive));
    windowRef.addEventListener("openmmi:frontendversion", () => windowRef.requestAnimationFrame(renderSystem));
    windowRef.addEventListener("openmmi:dashboardconnection", () => windowRef.requestAnimationFrame(renderSystem));
    documentRef.addEventListener("DOMContentLoaded", () => refresh());

    const Observer = windowRef.MutationObserver;
    if (typeof Observer === "function") {
      const observer = new Observer(() => {
        if (["system", "media"].includes(activeSection())) renderActive();
      });
      try { observer.observe(documentRef.body, { childList: true, subtree: true }); } catch (_) {}
    }

    return Object.freeze({
      activeSection,
      captureJellyfinDraft,
      jellyfinPayload,
      checkForUpdates,
      prepareUpdate,
      installUpdate,
      refresh,
      refreshUpdateReadiness,
      refreshUpdateStatus,
      refreshUpdateTransaction,
      renderActive,
      renderJellyfin,
      renderSystem,
      systemTemplate,
      frontendVersionSnapshot,
      frontendVersionStateLabel,
      updateStateLabel,
      repositoryStateLabel,
      transactionStateLabel,
      updateControlState,
      scheduleTransactionPoll,
      checkedAtLabel,
      jellyfinTemplate,
    });
  }

  function install(options = {}) {
    return createController(options);
  }

  return Object.freeze({ createController, escapeHtml, install });
});
