"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const vehicleSetup = require("../../ui/web_dashboard/static/vehicle-setup-settings.js");

function payload() {
  return {
    api_version: 1,
    read_only: true,
    runtime_mode: "single",
    active: {
      state: "ready",
      errors: [],
      vehicle: { source: "maintained", id: "seat_1p", revision: "sha256:profile" },
      bindings: { source: "maintained", id: "default", revision: "sha256:bindings" },
      active_bus: "comfort",
      interface: "can0",
      interface_present: false,
      configuration_revision: "sha256:configuration",
      loaded: {
        api_version: 1,
        state: "ready",
        errors: [],
        vehicle: { source: "maintained", id: "seat_1p", revision: "sha256:profile" },
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
          source: "maintained", id: "seat_1p", display_name: "Seat 1P", valid: true, revision: "sha256:profile",
          default_bus: "comfort", buses: [{ name: "comfort", interface: "can0", bitrate: 100000 }],
          validation: { valid: true, errors: [], warnings: [] },
        },
        {
          source: "custom", id: "my-seat", display_name: "My <Seat>", valid: true, revision: "sha256:custom-profile",
          default_bus: "comfort", buses: [{ name: "comfort", interface: "can1", bitrate: 100000 }],
          validation: { valid: true, errors: [], warnings: [] },
        },
        {
          source: "custom", id: "broken", display_name: "Broken", valid: false, revision: "sha256:broken-profile",
          default_bus: "comfort", buses: [{ name: "comfort", interface: "can0", bitrate: 100000 }],
          validation: { valid: false, errors: [{ code: "invalid-document", path: "status[25].primary" }], warnings: [] },
        },
      ],
      bindings: [
        {
          source: "maintained", id: "default", display_name: "Default", valid: true, revision: "sha256:bindings",
          binding_count: 12,
          validation: { valid: true, errors: [], warnings: [] },
        },
        {
          source: "custom", id: "my-controls", display_name: "My controls", valid: true, revision: "sha256:custom-bindings",
          binding_count: 11,
          validation: { valid: true, errors: [], warnings: [] },
        },
      ],
    },
    compatibility: {
      emitted_and_bound: ["play_pause", "volume_up"],
      emitted_unbound: [],
      bound_unemitted: ["stop_playback"],
      duplicate_emitted: [],
    },
    interfaces: [],
  };
}

function previewPayload() {
  return {
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
      emitted_and_bound: ["play_pause"], emitted_unbound: [],
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
      locks: {
        configuration_active: false,
        lifecycle_active: false,
        update_active: false,
      },
      apply_blocked: false,
    },
    plan: {
      changes: [
        {
          field: "vehicle",
          from: { source: "maintained", id: "seat_1p", revision: "sha256:profile" },
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
}


function coordinatorPayload(overrides = {}) {
  return {
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
    ...overrides,
  };
}

function applyResult() {
  return {
    ok: true,
    api_version: 1,
    action: "apply",
    state: {
      state: "complete", stage: "complete", error: "", restoration_attempted: false,
      restoration_verified: false, target: { interface: "can1" },
    },
  };
}

function fixture(options = {}) {
  const listeners = {};
  const calls = [];
  const confirmations = [];
  const prompts = [];
  const statusPayload = options.status || payload();
  const customDocuments = {
    "profile:my-seat": options.profileContent || '{\n  "rules": [],\n  "note": "<custom>"\n}\n',
    "profile:broken": options.brokenProfileContent || '{\n  "status": []\n}\n',
    "bindings:my-controls": options.bindingsContent || '{}\n',
  };
  const timers = [];
  const coordinatorResponses = Array.isArray(options.coordinators)
    ? [...options.coordinators]
    : null;
  const panel = { innerHTML: "" };
  const active = { dataset: { openmmiSettingsSection: "vehicle-setup" } };
  const document = {
    querySelector(selector) {
      if (selector === "[data-openmmi-settings-section].active") return active;
      if (selector === "#openmmiSettingsPanel") return panel;
      return null;
    },
    addEventListener(name, callback) { listeners[name] = callback; },
  };
  const window = {
    document,
    addEventListener() {},
    confirm(message) {
      confirmations.push(message);
      return options.confirm !== false;
    },
    prompt(message, defaultValue) {
      prompts.push([message, defaultValue]);
      return Object.prototype.hasOwnProperty.call(options, "promptResult")
        ? options.promptResult
        : defaultValue;
    },
    requestAnimationFrame(callback) { callback(); },
    setTimeout(callback) {
      timers.push(callback);
      if (options.runTimers) queueMicrotask(callback);
      return timers.length;
    },
  };
  const api = {
    async getJson(path) {
      calls.push(["GET", path]);
      if (options.error) throw new Error(options.error);
      if (path === vehicleSetup.ENDPOINT) return statusPayload;
      if (path === vehicleSetup.COORDINATOR_ENDPOINT) {
        if (options.coordinatorError) throw new Error(options.coordinatorError);
        if (coordinatorResponses?.length) return coordinatorResponses.shift();
        return options.coordinator || coordinatorPayload();
      }
      throw new Error("Unexpected vehicle setup GET");
    },
    async postJson(path, body) {
      calls.push(["POST", path, body]);
      if (path === vehicleSetup.PREVIEW_ENDPOINT) {
        if (options.previewError) throw new Error(options.previewError);
        return options.preview || previewPayload();
      }
      if (path === vehicleSetup.APPLY_ENDPOINT) {
        if (options.applyError) throw options.applyError;
        return options.apply || applyResult();
      }
      if (path === vehicleSetup.LOAD_CUSTOM_ENDPOINT) {
        if (options.loadError) throw options.loadError;
        const key = `${body.kind}:${body.id}`;
        const entry = statusPayload.catalogue[body.kind === "profile" ? "profiles" : "bindings"]
          .find((item) => item.source === "custom" && item.id === body.id);
        if (!entry || !(key in customDocuments)) throw new Error("Custom item not found");
        return options.load || {
          ok: true, api_version: 1, action: "load-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: entry.revision },
          content: customDocuments[key],
          validation: { valid: true, errors: [], warnings: [] },
        };
      }
      if (path === vehicleSetup.SAVE_CUSTOM_ENDPOINT) {
        if (options.saveError) throw options.saveError;
        const key = `${body.kind}:${body.id}`;
        customDocuments[key] = body.content;
        const entry = statusPayload.catalogue[body.kind === "profile" ? "profiles" : "bindings"]
          .find((item) => item.source === "custom" && item.id === body.id);
        if (!entry) throw new Error("Custom item not found");
        entry.revision = options.savedRevision || "sha256:saved-custom";
        return options.save || {
          ok: true, api_version: 1, action: "save-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: entry.revision },
          validation: { valid: true, errors: [], warnings: [] },
          applied: false,
        };
      }
      if (path === vehicleSetup.IMPORT_CUSTOM_ENDPOINT) {
        if (options.importError) throw options.importError;
        const collectionName = body.kind === "profile" ? "profiles" : "bindings";
        const collection = statusPayload.catalogue[collectionName];
        const imported = {
          source: "custom", id: body.id, display_name: body.id.replaceAll(/[-_]/g, " "),
          valid: true, revision: options.importedRevision || "sha256:imported-custom",
          validation: { valid: true, errors: [], warnings: [] },
        };
        if (body.kind === "profile") {
          imported.default_bus = "comfort";
          imported.buses = [{ name: "comfort", interface: "can0", bitrate: 100000 }];
        } else {
          imported.binding_count = 1;
        }
        collection.push(imported);
        customDocuments[`${body.kind}:${body.id}`] = body.content;
        return options.imported || {
          ok: true, api_version: 1, action: "import-custom-item", kind: body.kind,
          custom: { source: "custom", id: body.id, revision: imported.revision },
          validation: { valid: true, errors: [], warnings: [] }, applied: false,
        };
      }
      if (path === vehicleSetup.MANAGE_CUSTOM_ENDPOINT) {
        if (options.manageError) throw options.manageError;
        const collection = statusPayload.catalogue[body.kind === "profile" ? "profiles" : "bindings"];
        const index = collection.findIndex((item) => item.source === "custom" && item.id === body.id);
        if (index < 0) throw new Error("Custom item not found");
        const source = collection[index];
        if (body.action === "delete") {
          collection.splice(index, 1);
          return options.manage || {
            ok: true, api_version: 1, action: "manage-custom-item", operation: "delete", kind: body.kind,
            deleted: { source: "custom", id: body.id, revision: source.revision }, applied: false,
          };
        }
        const managed = {
          ...source,
          id: body.new_id,
          display_name: body.new_id.replaceAll(/[-_]/g, " "),
        };
        if (body.action === "rename") collection.splice(index, 1, managed);
        else collection.push(managed);
        return options.manage || {
          ok: true, api_version: 1, action: "manage-custom-item", operation: body.action, kind: body.kind,
          source: { source: "custom", id: body.id, revision: source.revision },
          custom: { source: "custom", id: body.new_id, revision: source.revision }, applied: false,
        };
      }
      if (path === vehicleSetup.COPY_ENDPOINT) {
        if (options.copyError) throw options.copyError;
        const kind = body.kind === "profile" ? "profiles" : "bindings";
        const custom = {
          source: "custom",
          id: body.id,
          display_name: body.id.replaceAll(/[-_]/g, " "),
          valid: true,
          revision: body.template_revision,
          validation: { valid: true, errors: [], warnings: [] },
        };
        if (kind === "profiles") {
          custom.default_bus = "comfort";
          custom.buses = [{ name: "comfort", interface: "can0", bitrate: 100000 }];
        } else {
          custom.binding_count = 12;
        }
        statusPayload.catalogue[kind].push(custom);
        return options.copy || {
          ok: true, api_version: 1, action: "copy-maintained-template",
          kind: body.kind, template: { source: "maintained", id: body.template_id, revision: body.template_revision },
          custom: { source: "custom", id: body.id, revision: body.template_revision },
        };
      }
      throw new Error("Unexpected vehicle setup POST");
    },
  };
  return { active, api, calls, confirmations, customDocuments, document, listeners, panel, prompts, timers, window };
}

test("vehicle setup renders maintained and custom draft choices before review", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  const html = controller.template();

  assert.match(html, /Vehicle setup/);
  assert.match(html, /Configured and loaded setup match/);
  assert.match(html, /Seat 1P/);
  assert.match(html, /Default · Maintained/);
  assert.match(html, /Maintained/);
  assert.match(html, /Custom/);
  assert.match(html, /My &lt;Seat&gt;/);
  assert.match(html, /value="custom:broken">Broken — invalid<\/option>/);
  assert.match(html, /can0 · not detected/);
  assert.match(html, /100 kbit\/s/);
  assert.match(html, /Review current setup/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-review" disabled/);
  assert.deepEqual(state.calls, [
    ["GET", "/api/system/vehicle-setup"],
    ["GET", "/api/system/vehicle-setup/coordinator"],
  ]);
  assert.equal(state.calls.some((call) => call[0] === "POST"), false);
});

test("saved revisions are clearly distinguished from loaded runtime and SHAs stay compact", async () => {
  const status = payload();
  const configuredProfile = `sha256:${"a".repeat(64)}`;
  const loadedProfile = `sha256:${"b".repeat(64)}`;
  const configuredBindings = `sha256:${"c".repeat(64)}`;
  const loadedBindings = `sha256:${"d".repeat(64)}`;

  status.active.vehicle = { source: "custom", id: "my-seat", revision: configuredProfile };
  status.active.bindings = { source: "custom", id: "my-controls", revision: configuredBindings };
  status.active.interface = "can1";
  status.active.loaded = {
    api_version: 1,
    state: "ready",
    errors: [],
    vehicle: { source: "custom", id: "my-seat", revision: loadedProfile },
    bindings: { source: "custom", id: "my-controls", revision: loadedBindings },
    active_bus: "comfort",
    interface: "can1",
    updated_at: 1,
  };
  status.catalogue.profiles.find((entry) => entry.id === "my-seat").revision = configuredProfile;
  status.catalogue.bindings.find((entry) => entry.id === "my-controls").revision = configuredBindings;

  const controller = vehicleSetup.createController(fixture({ status }));
  await controller.refresh();
  const html = controller.template();

  assert.match(html, /Saved custom revisions await review and Apply/);
  assert.match(html, /Saved revisions await review and Apply/);
  assert.match(html, /Configured profile/);
  assert.match(html, /matches configured selection/);
  assert.match(html, /sha256:aaaaaaaaaa…aaaaaaaa/);
  assert.match(html, /sha256:bbbbbbbbbb…bbbbbbbb/);
  assert.match(html, new RegExp(`title="${configuredProfile}"`));
  assert.match(html, new RegExp(`title="${loadedProfile}"`));
  assert.doesNotMatch(html, new RegExp(`>${configuredProfile}<`));
  assert.doesNotMatch(html, new RegExp(`>${loadedProfile}<`));
});

test("maintained items create revision-bound custom copies in the user catalogue", async () => {
  const state = fixture({ promptResult: "seat-track" });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();

  let html = controller.template();
  assert.match(html, /Use maintained profile as template/);
  assert.match(html, /Use maintained bindings as template/);
  assert.doesNotMatch(html, /Edit maintained|Delete maintained/);

  const result = await controller.copyTemplate("vehicle");
  assert.equal(result.custom.id, "seat-track");
  assert.deepEqual(state.prompts, [[
    "Choose an id for the new custom profile. Use lowercase letters, numbers, hyphens or underscores.",
    "seat_1p-custom",
  ]]);
  assert.deepEqual(state.calls.find((call) => call[0] === "POST"), [
    "POST",
    "/api/system/vehicle-custom/create",
    {
      kind: "profile",
      id: "seat-track",
      template_source: "maintained",
      template_id: "seat_1p",
      template_revision: "sha256:profile",
    },
  ]);
  assert.equal(controller.draft().vehicle, "custom:seat-track");
  assert.equal(controller.draftDiffers(), true);
  html = controller.template();
  assert.match(html, /Stored in your user catalogue/);
  assert.match(html, /maintained template was not changed/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-copy-vehicle"/);
});


test("custom JSON files import as validated unapplied drafts", async () => {
  const state = fixture({ promptResult: "imported-seat" });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();

  let html = controller.template();
  assert.match(html, /Import profile JSON/);
  assert.match(html, /Import bindings JSON/);

  const content = '{\n  "default_bus": "comfort",\n  "rules": [],\n  "presence": [],\n  "status": []\n}\n';
  const file = {
    name: "Seat Profile.json",
    size: Buffer.byteLength(content),
    async text() { return content; },
  };
  const result = await controller.importCustomFile("vehicle", file);

  assert.equal(result.custom.id, "imported-seat");
  assert.deepEqual(state.prompts, [[
    "Choose an id for the imported custom profile. Use lowercase letters, numbers, hyphens or underscores.",
    "seat-profile",
  ]]);
  assert.deepEqual(state.calls.find((call) => call[1] === vehicleSetup.IMPORT_CUSTOM_ENDPOINT), [
    "POST", vehicleSetup.IMPORT_CUSTOM_ENDPOINT,
    { kind: "profile", id: "imported-seat", content },
  ]);
  assert.equal(controller.draft().vehicle, "custom:imported-seat");
  assert.equal(controller.draftDiffers(), true);
  html = controller.template();
  assert.match(html, /imported and selected as an unapplied draft/);
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("custom lifecycle controls are custom-only and active items stay protected", async () => {
  const status = payload();
  status.active.vehicle = { source: "custom", id: "my-seat", revision: "sha256:custom-profile" };
  const state = fixture({ status });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  let html = controller.template();
  assert.match(html, /data-testid="vehicle-setup-duplicate-vehicle"/);
  assert.match(html, /data-testid="vehicle-setup-rename-vehicle" disabled/);
  assert.match(html, /data-testid="vehicle-setup-delete-vehicle" disabled/);
  assert.match(html, /Active custom items can be duplicated or edited/);
  assert.equal(await controller.manageCustomItem("delete", "vehicle"), null);
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.MANAGE_CUSTOM_ENDPOINT), false);

  controller.setDraft("bindings", "custom:my-controls");
  html = controller.template();
  assert.match(html, /data-testid="vehicle-setup-rename-bindings"/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-rename-bindings" disabled/);
  assert.doesNotMatch(controller.template(), /Rename maintained|Delete maintained/);
});

test("invalid custom profiles stay recoverable but cannot be reviewed or applied", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();

  assert.equal(controller.setDraft("vehicle", "custom:broken"), true);
  let html = controller.template();
  assert.match(html, /Broken — invalid/);
  assert.match(html, /1 validation error · invalid-document · status\[25\]\.primary/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-edit-vehicle" disabled/);
  assert.match(html, /data-testid="vehicle-setup-duplicate-vehicle" disabled/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-rename-vehicle" disabled/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-delete-vehicle" disabled/);
  assert.match(html, /Duplicate, Review and Apply remain blocked until validation passes/);
  assert.match(html, /data-testid="vehicle-setup-review" disabled/);
  assert.equal(controller.previewRequest(), null);
  assert.equal(await controller.manageCustomItem("duplicate", "vehicle"), null);

  await controller.openCustomEditor("vehicle");
  assert.equal(controller.editor().id, "broken");
  assert.match(controller.template(), /data-testid="vehicle-custom-editor"/);
  assert.equal(controller.closeCustomEditor(), true);

  const deleted = await controller.manageCustomItem("delete", "vehicle");
  assert.equal(deleted.operation, "delete");
  assert.equal(controller.draft().vehicle, "maintained:seat_1p");
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("custom duplicate and rename are exact-revision operations and remain unapplied", async () => {
  const state = fixture({ promptResult: "my-seat-copy" });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");

  const duplicated = await controller.manageCustomItem("duplicate", "vehicle");
  assert.equal(duplicated.operation, "duplicate");
  assert.deepEqual(state.calls.find((call) => call[1] === vehicleSetup.MANAGE_CUSTOM_ENDPOINT), [
    "POST", vehicleSetup.MANAGE_CUSTOM_ENDPOINT,
    {
      action: "duplicate", kind: "profile", source: "custom", id: "my-seat",
      expected_revision: "sha256:custom-profile", new_id: "my-seat-copy",
    },
  ]);
  assert.equal(controller.draft().vehicle, "custom:my-seat-copy");
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);

  state.window.prompt = () => "renamed-controls";
  controller.setDraft("bindings", "custom:my-controls");
  const renamed = await controller.manageCustomItem("rename", "bindings");
  assert.equal(renamed.operation, "rename");
  assert.equal(controller.draft().bindings, "custom:renamed-controls");
  const calls = state.calls.filter((call) => call[1] === vehicleSetup.MANAGE_CUSTOM_ENDPOINT);
  assert.deepEqual(calls[1][2], {
    action: "rename", kind: "bindings", source: "custom", id: "my-controls",
    expected_revision: "sha256:custom-bindings", new_id: "renamed-controls",
  });
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("inactive custom delete requires confirmation and resets the draft safely", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("bindings", "custom:my-controls");
  const deleted = await controller.manageCustomItem("delete", "bindings");
  assert.equal(deleted.operation, "delete");
  assert.match(state.confirmations.at(-1), /cannot be undone/);
  assert.deepEqual(state.calls.find((call) => call[1] === vehicleSetup.MANAGE_CUSTOM_ENDPOINT), [
    "POST", vehicleSetup.MANAGE_CUSTOM_ENDPOINT,
    {
      action: "delete", kind: "bindings", source: "custom", id: "my-controls",
      expected_revision: "sha256:custom-bindings",
    },
  ]);
  assert.equal(controller.draft().bindings, "maintained:default");
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("only custom items expose the revision-safe JSON editor", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  let html = controller.template();
  assert.doesNotMatch(html, /Edit maintained/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-edit-vehicle"/);

  assert.equal(controller.setDraft("vehicle", "custom:my-seat"), true);
  html = controller.template();
  assert.match(html, /data-testid="vehicle-setup-edit-vehicle"/);
  assert.match(html, /Lifecycle changes do not apply or restart/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-copy-vehicle"/);

  await controller.openCustomEditor("vehicle");
  assert.deepEqual(state.calls.find((call) => call[1] === vehicleSetup.LOAD_CUSTOM_ENDPOINT), [
    "POST", vehicleSetup.LOAD_CUSTOM_ENDPOINT,
    { kind: "profile", source: "custom", id: "my-seat" },
  ]);
  assert.equal(controller.editor().revision, "sha256:custom-profile");
  assert.equal(controller.editorDirty(), false);
  html = controller.template();
  assert.match(html, /data-testid="vehicle-custom-editor"/);
  assert.match(html, /&lt;custom&gt;/);
  assert.doesNotMatch(html, /<custom>/);
  assert.match(html, /does not update the maintained template/);
  assert.match(html, /data-testid="vehicle-setup-review" disabled/);
});

test("closing a typed custom edit requires discard confirmation", async () => {
  const state = fixture({ confirm: false });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.openCustomEditor("vehicle");
  state.listeners.input({
    target: {
      closest(selector) {
        return selector === "[data-openmmi-vehicle-custom-editor-content]"
          ? { value: '{"rules":[]}\n' }
          : null;
      },
    },
  });
  assert.equal(controller.editorDirty(), true);
  assert.equal(controller.closeCustomEditor(), false);
  assert.ok(controller.editor());
  assert.match(state.confirmations[0], /Discard the unsaved/);
});

test("custom saves require the loaded revision and remain unapplied", async () => {
  const state = fixture({ savedRevision: "sha256:next-custom" });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("bindings", "custom:my-controls");
  await controller.openCustomEditor("bindings");
  const content = '{\n  "play_pause": {"module": "audio", "func": "play_pause", "args": []}\n}\n';
  assert.equal(controller.setEditorContent(content), true);
  assert.equal(controller.editorDirty(), true);

  const result = await controller.saveCustomEditor();
  assert.equal(result.applied, false);
  assert.deepEqual(state.calls.find((call) => call[1] === vehicleSetup.SAVE_CUSTOM_ENDPOINT), [
    "POST", vehicleSetup.SAVE_CUSTOM_ENDPOINT,
    {
      kind: "bindings", source: "custom", id: "my-controls",
      expected_revision: "sha256:custom-bindings", content,
    },
  ]);
  assert.equal(controller.editor().revision, "sha256:next-custom");
  assert.equal(controller.editorDirty(), false);
  assert.match(controller.template(), /Close the editor, review the setup, and apply the new revision/);
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("stale custom saves preserve editor text and require reload", async () => {
  const error = new Error("Custom catalogue item changed");
  error.status = 409;
  error.payload = { ok: false, code: "custom-stale", error: error.message };
  const state = fixture({ saveError: error });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.openCustomEditor("vehicle");
  controller.setEditorContent('{"rules":[]}\n');
  await assert.rejects(controller.saveCustomEditor(), /changed/);
  assert.equal(controller.editor().content, '{"rules":[]}\n');
  assert.match(controller.template(), /Your text was not written/);
  assert.equal(state.calls.some((call) => call[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("invalid custom ids are rejected before a copy request", async () => {
  const state = fixture({ promptResult: "../seat" });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  assert.equal(await controller.copyTemplate("vehicle"), null);
  assert.equal(state.calls.some((call) => call[0] === "POST"), false);
  assert.match(controller.template(), /Custom ids must start/);
});

test("the current setup can be reviewed when no alternative catalogue entry exists", async () => {
  const currentPreview = {
    ...previewPayload(),
    plan: {
      changes: [],
      effects: {
        write_canonical_configuration: false,
        write_systemd_runtime: false,
        write_udev_rules: false,
        reload_user_manager: false,
        reload_udev: false,
        restart_can_service: false,
      },
    },
  };
  const state = fixture({ preview: currentPreview });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  assert.equal(controller.draftDiffers(), false);
  await controller.reviewDraft();
  assert.deepEqual(state.calls.find((call) => call[0] === "POST"), ["POST", "/api/system/vehicle-setup/preview", {
    vehicle: { source: "maintained", id: "seat_1p" },
    bindings: { source: "maintained", id: "default" },
    runtime: { active_bus: "comfort", buses: { comfort: { interface: "can0" } } },
  }]);
  const html = controller.template();
  assert.match(html, /configured and loaded setup would remain unchanged/);
  assert.match(html, /No active configuration values would change/);
  assert.match(html, /No service or adapter changes are required/);
  assert.doesNotMatch(html, /data-testid="vehicle-setup-apply" disabled/);
});

test("profile and bindings changes produce an exact read-only review request", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();

  assert.equal(controller.setDraft("vehicle", "custom:my-seat"), true);
  assert.equal(controller.setDraft("bindings", "custom:my-controls"), true);
  assert.equal(controller.draftDiffers(), true);
  assert.deepEqual(controller.draft(), {
    vehicle: "custom:my-seat",
    bindings: "custom:my-controls",
  });
  assert.match(controller.template(), /Draft selections are not applied/);
  assert.match(controller.template(), /can1/);
  assert.doesNotMatch(controller.template(), /data-testid="vehicle-setup-review" disabled/);
  assert.equal(controller.setDraft("vehicle", "custom:broken"), true);
  assert.equal(controller.previewRequest(), null);
  assert.match(controller.template(), /data-testid="vehicle-setup-review" disabled/);
  assert.equal(controller.setDraft("vehicle", "custom:my-seat"), true);

  const request = controller.previewRequest();
  assert.deepEqual(request, {
    vehicle: { source: "custom", id: "my-seat" },
    bindings: { source: "custom", id: "my-controls" },
    runtime: { active_bus: "comfort", buses: { comfort: { interface: "can1" } } },
  });
  await controller.reviewDraft();
  assert.deepEqual(state.calls.find((call) => call[0] === "POST"), ["POST", "/api/system/vehicle-setup/preview", request]);
  assert.equal(controller.preview().read_only, true);
  const review = controller.template();
  assert.match(review, /Review ready/);
  assert.match(review, /data-testid="vehicle-setup-preview"/);
  assert.match(review, /My &lt;Seat&gt; · Custom/);
  assert.match(review, /can1 · not detected/);
  assert.match(review, /1 binding is not emitted by the profile/);
  assert.match(review, /Restart the CAN service/);
  assert.doesNotMatch(review, /data-testid="vehicle-setup-apply" disabled/);
  assert.match(review, /failed mutation is restored automatically/);
  assert.doesNotMatch(JSON.stringify(request), /path|command|revision/);
});


test("confirmed apply sends only the exact reviewed target and revisions", async () => {
  const state = fixture();
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  controller.setDraft("bindings", "custom:my-controls");
  await controller.reviewDraft();

  const reviewed = controller.preview();
  const result = await controller.applyDraft();
  const call = state.calls.find((entry) => entry[0] === "POST" && entry[1] === vehicleSetup.APPLY_ENDPOINT);
  assert.deepEqual(call, ["POST", vehicleSetup.APPLY_ENDPOINT, {
    target: reviewed.target,
    expected_configuration_revision: reviewed.expected_configuration_revision,
    target_configuration_revision: reviewed.target_configuration_revision,
    confirm: true,
  }]);
  assert.equal(result.state.state, "complete");
  assert.equal(state.confirmations.length, 1);
  assert.match(state.confirmations[0], /My <Seat>/);
  assert.match(state.confirmations[0], /can1/);
  assert.equal(controller.preview(), null);
  assert.match(controller.template(), /Vehicle setup applied and verified/);
});

test("apply cancellation never sends a mutation request", async () => {
  const state = fixture({ confirm: false });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.reviewDraft();
  assert.equal(await controller.applyDraft(), null);
  assert.equal(state.calls.some((entry) => entry[1] === vehicleSetup.APPLY_ENDPOINT), false);
});

test("stale previews fail closed and require a fresh review", async () => {
  const error = new Error("Vehicle configuration preview is stale");
  error.status = 409;
  error.payload = { ok: false, code: "stale-preview", error: error.message };
  const state = fixture({ applyError: error });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.reviewDraft();
  await assert.rejects(controller.applyDraft(), /stale/);
  assert.equal(controller.preview(), null);
  assert.match(controller.template(), /reviewed setup is stale/i);
});

test("verified rollback is reported explicitly after a failed apply", async () => {
  const error = new Error("Applied vehicle configuration could not be verified");
  error.status = 500;
  error.payload = {
    ok: false,
    code: "apply-failed-restored",
    error: error.message,
    state: {
      state: "failed", stage: "restored", error: error.message,
      restoration_attempted: true, restoration_verified: true,
    },
  };
  const state = fixture({ applyError: error });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.reviewDraft();
  await assert.rejects(controller.applyDraft(), /could not be verified/);
  const html = controller.template();
  assert.match(html, /previous setup was restored and verified/i);
  assert.match(html, /Previous setup restoration was verified/);
});

test("active coordinator transactions resume progress polling after panel reload", async () => {
  const activeCoordinator = coordinatorPayload({
    locks: { configuration_active: true, lifecycle_active: true, update_active: true },
    state: {
      state: "validating", stage: "validated", error: "", restoration_attempted: false,
      restoration_verified: false, transaction_id: "configuration-active",
    },
  });
  const completeCoordinator = coordinatorPayload({
    state: {
      state: "complete", stage: "complete", error: "", restoration_attempted: false,
      restoration_verified: false, transaction_id: "configuration-active",
    },
  });
  const state = fixture({ coordinators: [activeCoordinator, completeCoordinator] });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  assert.match(controller.template(), /Apply progress: validating reviewed setup/);
  assert.ok(state.timers.length > 0);
  await state.timers.shift()();
  assert.match(controller.template(), /Vehicle setup applied and verified/);
});

test("unverified restoration blocks retries with explicit recovery guidance", async () => {
  const error = new Error("Vehicle configuration restoration could not be verified");
  error.status = 500;
  error.payload = {
    ok: false,
    code: "apply-failed-restore-unverified",
    error: error.message,
    state: {
      state: "failed", stage: "restore-unverified", error: error.message,
      restoration_attempted: true, restoration_verified: false,
    },
  };
  const state = fixture({ applyError: error });
  const controller = vehicleSetup.createController(state);
  await controller.refresh();
  controller.setDraft("vehicle", "custom:my-seat");
  await controller.reviewDraft();
  await assert.rejects(controller.applyDraft(), /could not be verified/);
  const html = controller.template();
  assert.match(html, /do not retry until coordinator recovery succeeds/i);
  assert.match(html, /Previous setup restoration could not be verified/);
});

test("preview failures remain inline and unsafe capability responses fail closed", async () => {
  const failed = fixture({ previewError: "preview unavailable" });
  const failedController = vehicleSetup.createController(failed);
  await failedController.refresh();
  failedController.setDraft("vehicle", "custom:my-seat");
  await assert.rejects(failedController.reviewDraft(), /preview unavailable/);
  assert.match(failedController.template(), /data-testid="vehicle-setup-preview-error"/);
  assert.match(failedController.template(), /preview unavailable/);

  const unsafe = fixture({ preview: { ...previewPayload(), apply_available: true } });
  const unsafeController = vehicleSetup.createController(unsafe);
  await unsafeController.refresh();
  unsafeController.setDraft("vehicle", "custom:my-seat");
  await assert.rejects(unsafeController.reviewDraft(), /not safely available/);
  assert.equal(unsafeController.preview(), null);
  assert.match(unsafeController.template(), /not safely available/);
});

test("endpoint failures stay inside the vehicle setup panel", async () => {
  const state = fixture({ error: "catalogue unavailable" });
  const controller = vehicleSetup.createController(state);
  await assert.rejects(controller.refresh(), /catalogue unavailable/);
  assert.match(controller.template(), /catalogue unavailable/);
  assert.match(controller.template(), /data-testid="vehicle-setup-refresh"/);
});

test("identity helpers never accept path-shaped input", () => {
  assert.equal(vehicleSetup.identityKey({ source: "maintained", id: "seat_1p" }), "maintained:seat_1p");
  assert.equal(vehicleSetup.identityKey({ source: "maintained", id: "../../tmp" }), "");
  assert.deepEqual(vehicleSetup.identityFromKey("custom:my_controls"), { source: "custom", id: "my_controls" });
  assert.equal(vehicleSetup.identityFromKey("custom:../../tmp"), null);
  assert.equal(vehicleSetup.escapeHtml('../../tmp/<script>'), "../../tmp/&lt;script&gt;");
});
