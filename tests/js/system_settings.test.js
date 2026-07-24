"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const settings = require("../../ui/web_dashboard/static/system-settings.js");

function fixture(options = {}) {
  const listeners = {};
  const panel = { innerHTML: "", appendChild() {}, querySelector() { return null; } };
  const active = { dataset: { openmmiSettingsSection: options.section || "system" } };
  const document = {
    body: {},
    activeElement: null,
    querySelector(selector) {
      if (selector === "[data-openmmi-settings-section].active") return active;
      if (selector === "#openmmiSettingsPanel") return panel;
      return null;
    },
    addEventListener(name, callback) { listeners[name] = callback; },
    createElement() { return { innerHTML: "", id: "" }; },
  };
  const window = {
    document,
    __openMmiFrontendVersionController: {
      snapshot() {
        return { loadedId: "frontend-abc", serverId: "server-def", state: "update-ready" };
      },
    },
    addEventListener() {},
    requestAnimationFrame(callback) { callback(); },
    MutationObserver: class { observe() {} },
    FormData: class {},
    setTimeout,
    confirm(message) {
      confirmMessages.push(String(message));
      return options.confirm !== false;
    },
  };
  const systemPayload = {
    launcher: { default_ui: "web", open_at_login: true, service_active: true, dashboard_reachable: true },
    jellyfin: {
      configured: true,
      url: "https://media.test",
      username: "driver",
      password_configured: true,
      token_configured: false,
      path: "/home/user/.config/open-mmi/dashboard.env",
    },
  };
  const updatePayload = {
    api_version: 1,
    read_only: true,
    installed: { managed: true, version: "v1-runtime-42-gabc1234", commit: "abc1234def56" },
    channel: "nightly",
    policy: { state: "configured", implicit: false, updated_at: "2026-07-18T12:00:00+00:00" },
    source: { configured: true, state: "ready", clean: true, branch: "main", upstream: "origin/main", trusted: true },
    update: {
      state: "not-checked", checked_at: null, available_version: "", available_commit: "",
      remote_differs: null, update_available: null, error: "",
    },
    readiness: { state: "ready", blockers: [] },
  };
  const readinessPayload = {
    api_version: 1,
    state: "ready",
    install_allowed: true,
    blockers: [],
    checks: [],
  };
  let coordinatorPayload = {
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
  if (options.coordinatorState) {
    coordinatorPayload = {
      ...coordinatorPayload,
      state: { ...coordinatorPayload.state, ...options.coordinatorState },
    };
  }
  const coordinatorResponses = Array.from(options.coordinatorResponses || []);
  const calls = [];
  const confirmMessages = [];
  const api = {
    async getJson(path) {
      calls.push(["GET", path]);
      if (path === "/api/system/settings") return systemPayload;
      if (path === "/api/system/update-status") return updatePayload;
      if (path === "/api/system/update-readiness") return readinessPayload;
      if (path === "/api/system/update-coordinator") {
        if (coordinatorResponses.length) {
          coordinatorPayload = {
            ...coordinatorPayload,
            state: { ...coordinatorPayload.state, ...coordinatorResponses.shift() },
          };
        }
        return coordinatorPayload;
      }
      throw new Error(`Unexpected GET ${path}`);
    },
    async postJson(path, body) {
      calls.push(["POST", path, body]);
      if (path === "/api/system/update-check") {
        const checkedUpdate = {
          state: "update-available",
          checked_at: "2026-07-18T14:32:00+00:00",
          available_version: "def5678abc90",
          available_commit: "def5678abc901234567890123456789012345678",
          remote_differs: true,
          update_available: true,
          error: "",
          ...(options.checkUpdate || {}),
        };
        return {
          ...updatePayload,
          source: { ...updatePayload.source, ...(options.checkSource || {}) },
          update: checkedUpdate,
          readiness: { ...updatePayload.readiness, ...(options.checkReadiness || {}) },
        };
      }
      if (path === "/api/system/update-prepare") {
        coordinatorPayload = {
          ...coordinatorPayload,
          state: {
            ...coordinatorPayload.state,
            state: "prepared",
            stage: "prepared",
            target_version: "v1-runtime-43-gdef5678",
            candidate_commit: "def5678abc901234567890123456789012345678",
            transaction_id: "prepare-0123456789abcdef0123456789abcdef",
          },
        };
        return coordinatorPayload;
      }
      if (path === "/api/system/update-install") {
        coordinatorPayload = {
          ...coordinatorPayload,
          state: { ...coordinatorPayload.state, state: "complete", stage: "complete" },
        };
        return coordinatorPayload;
      }
      return { ok: true };
    },
  };
  return {
    active, api, calls, confirmMessages, document, panel, readinessPayload, systemPayload, updatePayload, window,
    updatePollIntervalMs: options.updatePollIntervalMs || 25,
    updateActionTimeoutMs: options.updateActionTimeoutMs || 1000,
  };
}

test("system settings escape server-provided values", () => {
  assert.equal(settings.escapeHtml('<script>"x"</script>'), "&lt;script&gt;&quot;x&quot;&lt;/script&gt;");
});

test("system and Jellyfin templates expose the fixed managed update flow without stored secrets", async () => {
  const state = fixture();
  const controller = settings.createController(state);
  await controller.refresh();
  const systemHtml = controller.systemTemplate();
  assert.match(systemHtml, /Default interface/);
  assert.match(systemHtml, /Open Open MMI at login/);
  assert.match(systemHtml, /data-openmmi-system-settings-ready="true"/);
  assert.match(systemHtml, /Dashboard version/);
  assert.match(systemHtml, /frontend-abc/);
  assert.match(systemHtml, /server-def/);
  assert.match(systemHtml, /reload ready/);
  assert.match(systemHtml, /Software updates/);
  assert.match(systemHtml, /v1-runtime-42-gabc1234/);
  assert.match(systemHtml, /nightly/);
  assert.match(systemHtml, /not checked/);
  assert.match(systemHtml, /Technical details/);
  assert.match(systemHtml, /data-testid="system-update-technical"/);
  assert.match(systemHtml, /Repository health/);
  assert.match(systemHtml, /Installation readiness/);
  assert.match(systemHtml, /Update progress/);
  assert.match(systemHtml, /Check for updates/);
  assert.match(systemHtml, /Prepare update/);
  assert.match(systemHtml, /Install update/);
  assert.match(systemHtml, /Nightly follows the newest available Open MMI build/);
  assert.match(systemHtml, /roll back automatically if validation fails/);
  assert.doesNotMatch(systemHtml, /data-testid="system-update-target"/);
  assert.ok(systemHtml.indexOf('data-openmmi-update-status="true"') < systemHtml.indexOf('role="status"'));
  assert.ok(systemHtml.indexOf('data-openmmi-update-prepare="true"') < systemHtml.indexOf('role="status"'));
  assert.doesNotMatch(systemHtml, /repository_path|https:\/\/github/);
  assert.deepEqual(state.calls.slice(0, 4), [
    ["GET", "/api/system/settings"],
    ["GET", "/api/system/update-status"],
    ["GET", "/api/system/update-readiness"],
    ["GET", "/api/system/update-coordinator"],
  ]);

  state.active.dataset.openmmiSettingsSection = "media";
  const jellyfinHtml = controller.jellyfinTemplate();
  assert.match(jellyfinHtml, /server-side credentials/);
  assert.doesNotMatch(jellyfinHtml, /secret-value|saved-password|saved-token/);
  assert.match(jellyfinHtml, /Leave blank to keep saved password/);
});

test("manual update check sends only fixed confirmation and refreshes the panel", async () => {
  const state = fixture();
  const controller = settings.createController(state);
  await controller.refresh();
  const payload = await controller.checkForUpdates();
  assert.equal(payload.update.state, "update-available");
  assert.deepEqual(
    state.calls.find((call) => call[0] === "POST" && call[1] === "/api/system/update-check"),
    ["POST", "/api/system/update-check", { confirm: true }],
  );
  assert.deepEqual(state.calls.slice(-2), [
    ["GET", "/api/system/update-readiness"],
    ["GET", "/api/system/update-coordinator"],
  ]);
  const html = controller.systemTemplate();
  assert.match(html, /update available/);
  assert.match(html, /def5678abc90/);
  assert.match(html, /2026-07-18 14:32:00 UTC/);
  assert.equal(controller.updateControlState().canPrepare, true);
});

test("source mismatch blocks prepare even when detailed readiness is stale", async () => {
  const state = fixture();
  const controller = settings.createController(state);
  await controller.refresh();
  state.updatePayload.source.state = "source-changed";
  state.updatePayload.readiness = {
    state: "blocked",
    blockers: ["repository-source-changed"],
  };
  await controller.checkForUpdates();
  const controls = controller.updateControlState();
  assert.equal(controls.readinessReady, true);
  assert.equal(controls.managedSourceReady, false);
  assert.equal(controls.canPrepare, false);
  assert.match(controller.systemTemplate(), /blocked: update source is not ready/);
});

test("source feedback and equivalent readiness blockers are shown only once", async () => {
  const sourceError = "Managed update source is not ready";
  const state = fixture({
    checkSource: { state: "source-changed" },
    checkReadiness: {
      state: "blocked",
      blockers: ["repository-source-changed", "managed-source"],
    },
    checkUpdate: {
      state: "blocked",
      update_available: false,
      error: sourceError,
    },
  });
  const controller = settings.createController(state);
  await controller.refresh();
  await controller.checkForUpdates();
  const html = controller.systemTemplate();
  assert.equal((html.match(new RegExp(sourceError, "g")) || []).length, 1);
  assert.equal((html.match(/blocked: update source is not ready/g) || []).length, 1);
  assert.doesNotMatch(html, /data-testid="system-update-error"/);
  assert.match(html, /openmmi-update-feedback warning/);
});


test("blocked readiness explains the low-battery policy outside technical details", async () => {
  const state = fixture();
  state.readinessPayload.state = "blocked";
  state.readinessPayload.install_allowed = false;
  state.readinessPayload.blockers = ["power"];
  state.readinessPayload.checks = [{
    code: "power",
    state: "block",
    summary: "Connect external power or charge the battery to at least 30%",
    ac_online: false,
    capacity_percent: 18,
  }];
  const controller = settings.createController(state);
  await controller.refresh();
  const html = controller.systemTemplate();
  assert.match(html, /data-testid="system-update-readiness-detail"/);
  assert.match(html, /Connect external power or charge the battery to at least 30% \(battery 18%\)/);
  assert.ok(
    html.indexOf('data-testid="system-update-readiness-detail"')
      < html.indexOf('data-testid="system-update-technical"'),
  );
  assert.equal(controller.updateControlState().canPrepare, false);
  assert.equal(controller.updateControlState().canInstall, false);
});

test("dashboard connection state cannot re-enable managed update controls", async () => {
  const state = fixture();
  state.document.body.dataset = { openmmiDashboardConnection: "ready" };
  const controller = settings.createController(state);
  await controller.refresh();
  await controller.checkForUpdates();
  assert.equal(controller.updateControlState().canPrepare, true);

  state.document.body.dataset.openmmiDashboardConnection = "reconnecting";
  assert.equal(controller.updateControlState().canCheck, false);
  assert.equal(controller.updateControlState().canPrepare, false);
  assert.equal(controller.updateControlState().canInstall, false);

  state.document.body.dataset.openmmiDashboardConnection = "ready";
  assert.equal(controller.updateControlState().canPrepare, true);
});

test("completed and failed coordinator states are labelled as transaction history", async () => {
  for (const transactionState of ["complete", "failed"]) {
    const state = fixture({
      coordinatorState: {
        state: transactionState,
        stage: transactionState,
        target_version: "v1-runtime-41-gold1234",
      },
    });
    const controller = settings.createController(state);
    await controller.refresh();
    const html = controller.systemTemplate();
    assert.match(html, /data-testid="system-update-transaction-label">Last update/);
    assert.match(html, /data-testid="system-update-target-label">Last update version/);
    assert.match(html, /v1-runtime-41-gold1234/);
  }
});

test("managed prepare and install require confirmation and send no caller-selected target", async () => {
  const state = fixture();
  const controller = settings.createController(state);
  await controller.refresh();
  await controller.checkForUpdates();

  const prepared = await controller.prepareUpdate();
  assert.equal(prepared.state.state, "prepared");
  assert.equal(controller.updateControlState().canInstall, true);
  assert.deepEqual(
    state.calls.find((call) => call[0] === "POST" && call[1] === "/api/system/update-prepare"),
    ["POST", "/api/system/update-prepare", { confirm: true }],
  );

  const installed = await controller.installUpdate();
  assert.equal(installed.state.state, "complete");
  assert.deepEqual(
    state.calls.find((call) => call[0] === "POST" && call[1] === "/api/system/update-install"),
    ["POST", "/api/system/update-install", { confirm: true }],
  );
  assert.equal(state.confirmMessages.length, 2);
  assert.match(state.confirmMessages[0], /Download and verify/);
  assert.match(state.confirmMessages[1], /services will restart automatically/);
});

test("an active transaction resumes polling after a dashboard page reload", async () => {
  const state = fixture({
    coordinatorState: { state: "installing", stage: "installing", target_version: "v1-runtime-43-gdef5678" },
    coordinatorResponses: [
      { state: "installing", stage: "installing" },
      { state: "complete", stage: "complete" },
    ],
  });
  const controller = settings.createController(state);
  await controller.refresh();
  assert.equal(controller.updateControlState().transactionState, "installing");
  await new Promise((resolve) => setTimeout(resolve, 125));
  assert.equal(controller.updateControlState().transactionState, "complete");
  assert.ok(state.calls.filter((call) => call[1] === "/api/system/update-coordinator").length >= 2);
});

test("frontend and update states use conservative user-facing labels", () => {
  const state = fixture();
  const controller = settings.createController(state);
  const frontendLabels = {
    current: "up to date",
    reloading: "applying update",
    "update-ready": "reload ready",
    "mismatch-after-reload": "reload required",
    reconnecting: "checking…",
    unavailable: "unavailable",
  };
  for (const [value, label] of Object.entries(frontendLabels)) {
    assert.equal(controller.frontendVersionStateLabel(value), label);
  }
  assert.equal(controller.updateStateLabel("remote-different"), "remote differs");
  assert.equal(controller.updateStateLabel("update-available"), "update available");
  assert.equal(controller.updateStateLabel("downgrade-blocked"), "downgrade blocked");
  assert.equal(controller.updateStateLabel("release-rewritten"), "release tag changed");
  assert.equal(controller.repositoryStateLabel("dirty"), "local changes");
  assert.equal(controller.repositoryStateLabel("untrusted-remote"), "untrusted remote");
  assert.equal(controller.checkedAtLabel(null), "never");
});
