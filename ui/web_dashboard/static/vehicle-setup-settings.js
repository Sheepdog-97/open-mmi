(function openMmiVehicleSetupSettingsModule(root, factory) {
  const moduleApi = factory(root);
  if (typeof module === "object" && module.exports) module.exports = moduleApi;
  if (root) root.openMmiVehicleSetupSettings = moduleApi;
})(typeof globalThis !== "undefined" ? globalThis : this, function createVehicleSetupSettingsModule(root) {
  "use strict";

  const ENDPOINT = "/api/system/vehicle-setup";
  const PREVIEW_ENDPOINT = "/api/system/vehicle-setup/preview";
  const APPLY_ENDPOINT = "/api/system/vehicle-setup/apply";
  const COPY_ENDPOINT = "/api/system/vehicle-custom/create";
  const LOAD_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/load";
  const SAVE_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/save";
  const MANAGE_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/manage";
  const IMPORT_CUSTOM_ENDPOINT = "/api/system/vehicle-custom/import";
  const COORDINATOR_ENDPOINT = "/api/system/vehicle-setup/coordinator";
  const ACTIVE_APPLY_STATES = new Set(["validating", "applying", "reloading", "verifying", "restoring"]);
  const TERMINAL_APPLY_STATES = new Set(["idle", "complete", "failed"]);
  const SOURCES = Object.freeze(["maintained", "custom"]);
  const IDENTIFIER_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
  const INTERFACE_RE = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$/;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function identityKey(identity = {}) {
    const source = String(identity.source || "");
    const identifier = String(identity.id || "");
    return source && IDENTIFIER_RE.test(identifier) ? `${source}:${identifier}` : "";
  }

  function identityFromKey(key) {
    const separator = String(key || "").indexOf(":");
    if (separator < 1) return null;
    const source = String(key).slice(0, separator);
    const identifier = String(key).slice(separator + 1);
    if (!SOURCES.includes(source) || !IDENTIFIER_RE.test(identifier)) return null;
    return { source, id: identifier };
  }

  function sourceLabel(source) {
    if (source === "maintained") return "Maintained";
    if (source === "custom") return "Custom";
    if (source === "external") return "External";
    return "Unavailable";
  }

  function bitrateLabel(value) {
    const bitrate = Number(value);
    if (!Number.isFinite(bitrate) || bitrate <= 0) return "not declared";
    if (bitrate % 1000 === 0) return `${bitrate / 1000} kbit/s`;
    return `${bitrate} bit/s`;
  }

  function entryFor(entries, key) {
    return (Array.isArray(entries) ? entries : []).find((entry) => identityKey(entry) === key) || null;
  }

  function optionGroups(entries, selectedKey, fallbackIdentity = {}) {
    const catalogue = Array.isArray(entries) ? entries : [];
    const groups = SOURCES.map((source) => {
      const options = catalogue
        .filter((entry) => entry?.source === source)
        .map((entry) => {
          const key = identityKey(entry);
          const selected = key === selectedKey ? " selected" : "";
          const disabled = entry?.valid === false && entry?.source !== "custom" ? " disabled" : "";
          const suffix = entry?.valid === false ? " — invalid" : "";
          return `<option value="${escapeHtml(key)}"${selected}${disabled}>${escapeHtml(entry?.display_name || entry?.id || "Unnamed")}${escapeHtml(suffix)}</option>`;
        })
        .join("");
      return options ? `<optgroup label="${sourceLabel(source)}">${options}</optgroup>` : "";
    }).join("");

    if (selectedKey && !entryFor(catalogue, selectedKey)) {
      const label = fallbackIdentity?.id || "Unavailable selection";
      return `<optgroup label="Unavailable"><option value="${escapeHtml(selectedKey)}" selected disabled>${escapeHtml(label)}</option></optgroup>${groups}`;
    }
    return groups;
  }

  function createController(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const api = options.api || windowRef?.openMmiApi;
    if (!windowRef || !documentRef || !api) {
      throw new Error("Vehicle setup settings require window, document and API client");
    }

    let snapshot = null;
    let draft = null;
    let draftDirty = false;
    let loading = false;
    let attempted = false;
    let connectionUnavailable = false;
    let errorMessage = "";
    let preview = null;
    let previewLoading = false;
    let previewError = "";
    let previewGeneration = 0;
    let coordinator = null;
    let coordinatorError = "";
    let applyBusy = false;
    let applyMessage = "";
    let applyMessageKind = "";
    let applyState = null;
    let applyPollTimer = null;
    let applyPollDeadline = 0;
    let copyBusyKind = "";
    let copyMessage = "";
    let copyMessageKind = "";
    let editor = null;
    let editorBusy = false;
    let editorMessage = "";
    let editorMessageKind = "";
    let lifecycleBusyKind = "";
    let importBusyKind = "";

    function activeSection() {
      return documentRef.querySelector("[data-openmmi-settings-section].active")
        ?.dataset?.openmmiSettingsSection || "";
    }

    function activeIdentity(kind) {
      return snapshot?.active?.[kind] || {};
    }

    function seedDraft() {
      draft = {
        vehicle: identityKey(activeIdentity("vehicle")),
        bindings: identityKey(activeIdentity("bindings")),
      };
      draftDirty = false;
    }

    function catalogue(kind) {
      return snapshot?.catalogue?.[kind === "vehicle" ? "profiles" : "bindings"] || [];
    }

    function selectedEntry(kind) {
      return entryFor(catalogue(kind), draft?.[kind] || "");
    }

    function identityDisplay(kind, identity) {
      return entryFor(catalogue(kind), identityKey(identity))?.display_name
        || identity?.id
        || "--";
    }

    function revisionFingerprint(value) {
      const revision = String(value || "");
      if (!revision) return "--";
      if (revision.startsWith("sha256:") && revision.length > 31) {
        const digest = revision.slice(7);
        return `sha256:${digest.slice(0, 10)}…${digest.slice(-8)}`;
      }
      if (revision.length > 31) return `${revision.slice(0, 18)}…${revision.slice(-8)}`;
      return revision;
    }

    function revisionTemplate(value, testId = "") {
      const revision = String(value || "");
      const attributes = revision
        ? ` title="${escapeHtml(revision)}" aria-label="${escapeHtml(`Full revision ${revision}`)}"`
        : "";
      const testAttribute = testId ? ` data-testid="${escapeHtml(testId)}"` : "";
      return `<code class="openmmi-vehicle-revision"${testAttribute}${attributes}>${escapeHtml(revisionFingerprint(revision))}</code>`;
    }

    function loadedIdentity(kind) {
      return snapshot?.active?.loaded?.[kind] || {};
    }

    function runtimeSync() {
      const active = snapshot?.active || {};
      const loaded = active.loaded || {};
      if (loaded.state !== "ready") {
        return { state: "unavailable", label: "Loaded runtime unavailable" };
      }
      const currentVehicle = activeIdentity("vehicle");
      const currentBindings = activeIdentity("bindings");
      const runtimeVehicle = loadedIdentity("vehicle");
      const runtimeBindings = loadedIdentity("bindings");
      const sameSelections = identityKey(currentVehicle) === identityKey(runtimeVehicle)
        && identityKey(currentBindings) === identityKey(runtimeBindings)
        && String(active.active_bus || "") === String(loaded.active_bus || "")
        && String(active.interface || "") === String(loaded.interface || "");
      if (!sameSelections) {
        return { state: "different-selection", label: "Loaded runtime uses a different setup" };
      }
      const profilePending = String(currentVehicle.revision || "") !== String(runtimeVehicle.revision || "");
      const bindingsPending = String(currentBindings.revision || "") !== String(runtimeBindings.revision || "");
      if (profilePending || bindingsPending) {
        return {
          state: "saved-unapplied",
          label: "Saved revisions await review and Apply",
          profilePending,
          bindingsPending,
        };
      }
      return { state: "matched", label: "Loaded runtime matches configured revisions" };
    }

    function draftDiffers() {
      if (!draft || !snapshot) return false;
      return draft.vehicle !== identityKey(activeIdentity("vehicle"))
        || draft.bindings !== identityKey(activeIdentity("bindings"));
    }

    function clearPreview() {
      previewGeneration += 1;
      preview = null;
      previewLoading = false;
      previewError = "";
    }

    function coordinatorState() {
      return applyState || coordinator?.state || {};
    }

    function coordinatorApplyReady() {
      const locks = coordinator?.locks || {};
      return coordinator?.ok === true
        && coordinator?.apply_enabled === true
        && coordinator?.read_only === false
        && preview?.coordinator?.apply_blocked === false
        && preview?.validation?.valid !== false
        && !Object.values(locks).some((value) => value === true)
        && !applyBusy;
    }

    function applyStateLabel(value) {
      const labels = {
        idle: "idle",
        validating: "validating reviewed setup…",
        applying: "writing configuration…",
        reloading: "provisioning CAN adapter…",
        verifying: "verifying loaded runtime…",
        restoring: "restoring previous setup…",
        complete: "complete",
        failed: "failed",
      };
      return labels[String(value || "")] || String(value || "waiting…");
    }

    function applyPayload() {
      if (
        !preview
        || preview.read_only !== true
        || preview.apply_available !== false
        || preview.state !== "ready"
        || !preview.target
        || typeof preview.expected_configuration_revision !== "string"
        || typeof preview.target_configuration_revision !== "string"
      ) return null;
      return {
        target: preview.target,
        expected_configuration_revision: preview.expected_configuration_revision,
        target_configuration_revision: preview.target_configuration_revision,
        confirm: true,
      };
    }

    function applyCapabilityMessage() {
      const state = coordinatorState();
      if (applyBusy) return `Apply progress: ${applyStateLabel(state.state)}`;
      if (coordinatorError) return coordinatorError;
      if (coordinator?.apply_enabled !== true) {
        if (state.stage === "restore-unverified") {
          return "A previous setup could not be restored safely. Applying remains blocked until recovery succeeds.";
        }
        return "The privileged apply service is unavailable.";
      }
      if (Object.values(coordinator?.locks || {}).some((value) => value === true)) {
        return "Another Open MMI lifecycle operation is active. Refresh the review before applying.";
      }
      return "Applying restarts the CAN service and verifies the loaded runtime. A failed mutation is restored automatically.";
    }

    function setApplyMessage(text, kind = "") {
      applyMessage = String(text || "");
      applyMessageKind = String(kind || "");
    }

    function setCopyMessage(text, kind = "") {
      copyMessage = String(text || "");
      copyMessageKind = String(kind || "");
    }

    function copyFeedbackTemplate() {
      if (!copyMessage) return "";
      const kind = copyMessageKind || "warning";
      return `<div class="openmmi-config-message ${escapeHtml(kind)}" role="status" aria-live="polite" data-testid="vehicle-setup-copy-feedback">${escapeHtml(copyMessage)}</div>`;
    }

    function customCopyControl(kind, entry) {
      const label = kind === "vehicle" ? "profile" : "bindings";
      if (entry?.source === "custom") {
        const editing = editor?.kind === kind && editor?.id === entry.id;
        const active = identityKey(activeIdentity(kind)) === identityKey(entry);
        const disabled = loading || previewLoading || applyBusy || Boolean(copyBusyKind)
          || Boolean(lifecycleBusyKind) || Boolean(importBusyKind) || editorBusy || Boolean(editor);
        const duplicateDisabled = disabled || entry.valid === false;
        const destructiveDisabled = disabled || active;
        const busy = lifecycleBusyKind === kind;
        const locationNote = active
          ? "Active custom items can be duplicated or edited, but not renamed or deleted. Switch and apply another item first."
          : entry.valid === false
            ? "Invalid custom items can be edited, renamed or deleted. Duplicate, Review and Apply remain blocked until validation passes."
            : "Stored in your user catalogue. Lifecycle changes do not apply or restart the CAN service.";
        return `
          <div class="openmmi-vehicle-custom-controls">
            <div class="openmmi-vehicle-custom-actions">
              <button type="button" class="openmmi-settings-link openmmi-vehicle-edit-custom" data-openmmi-vehicle-custom-edit="${escapeHtml(kind)}" data-testid="vehicle-setup-edit-${escapeHtml(kind)}" ${disabled ? "disabled" : ""}>${editing ? "Editing custom " + label + "…" : "Edit"}</button>
              <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-custom-manage="duplicate" data-openmmi-vehicle-custom-kind="${escapeHtml(kind)}" data-testid="vehicle-setup-duplicate-${escapeHtml(kind)}" ${duplicateDisabled ? "disabled" : ""}>${busy ? "Working…" : "Duplicate"}</button>
              <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-custom-manage="rename" data-openmmi-vehicle-custom-kind="${escapeHtml(kind)}" data-testid="vehicle-setup-rename-${escapeHtml(kind)}" ${destructiveDisabled ? "disabled" : ""}>Rename</button>
              <button type="button" class="openmmi-settings-link openmmi-vehicle-custom-delete" data-openmmi-vehicle-custom-manage="delete" data-openmmi-vehicle-custom-kind="${escapeHtml(kind)}" data-testid="vehicle-setup-delete-${escapeHtml(kind)}" ${destructiveDisabled ? "disabled" : ""}>Delete</button>
            </div>
            <small class="openmmi-vehicle-custom-location">${locationNote}</small>
          </div>`;
      }
      if (entry?.source !== "maintained") return "";
      const busy = copyBusyKind === kind;
      const disabled = loading || previewLoading || applyBusy || Boolean(copyBusyKind)
        || Boolean(lifecycleBusyKind) || Boolean(importBusyKind) || editorBusy || Boolean(editor)
        || entry.valid === false
        || typeof entry.revision !== "string"
        || !entry.revision;
      return `<button type="button" class="openmmi-settings-link openmmi-vehicle-copy-template" data-openmmi-vehicle-setup-copy="${escapeHtml(kind)}" data-testid="vehicle-setup-copy-${escapeHtml(kind)}" ${disabled ? "disabled" : ""}>${busy ? "Creating custom copy…" : `Use maintained ${label} as template`}</button>`;
    }

    function suggestedDuplicateId(identifier) {
      const base = `${String(identifier || "custom")}-copy`;
      if (base.length <= 64 && IDENTIFIER_RE.test(base)) return base;
      const shortened = `${String(identifier || "item").slice(0, 58)}-copy`;
      return IDENTIFIER_RE.test(shortened) ? shortened : "custom-copy";
    }

    function suggestedCustomId(identifier) {
      const base = `${String(identifier || "custom")}-custom`;
      if (base.length <= 64 && IDENTIFIER_RE.test(base)) return base;
      const shortened = `custom-${String(identifier || "item").slice(0, 56)}`;
      return IDENTIFIER_RE.test(shortened) ? shortened : "custom-item";
    }

    function suggestedImportId(filename, kind) {
      const fallback = kind === "vehicle" ? "imported-profile" : "imported-bindings";
      const withoutExtension = String(filename || "").replace(/\.json$/i, "");
      const normalized = withoutExtension
        .toLowerCase()
        .replace(/[^a-z0-9_-]+/g, "-")
        .replace(/^[-_]+|[-_]+$/g, "")
        .slice(0, 64);
      return IDENTIFIER_RE.test(normalized) ? normalized : fallback;
    }

    function importControl(kind) {
      const label = kind === "vehicle" ? "profile" : "bindings";
      const busy = importBusyKind === kind;
      const disabled = loading || previewLoading || applyBusy || Boolean(copyBusyKind)
        || Boolean(lifecycleBusyKind) || Boolean(importBusyKind) || editorBusy || Boolean(editor);
      return `
        <div class="openmmi-vehicle-custom-import">
          <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-custom-import="${escapeHtml(kind)}" data-testid="vehicle-setup-import-${escapeHtml(kind)}" ${disabled ? "disabled" : ""}>${busy ? "Importing…" : `Import ${label} JSON`}</button>
          <input class="openmmi-vehicle-custom-import-file" type="file" accept="application/json,.json" data-openmmi-vehicle-custom-import-file="${escapeHtml(kind)}" data-testid="vehicle-setup-import-file-${escapeHtml(kind)}" tabindex="-1" aria-hidden="true">
        </div>`;
    }

    async function importCustomFile(kind, file) {
      const label = editorKind(kind);
      if (!file || typeof file.text !== "function") {
        throw new Error(`Choose a JSON file to import as custom ${label}`);
      }
      const maximum = label === "profile" ? 1024 * 1024 : 256 * 1024;
      if (Number(file.size) > maximum) {
        throw new Error(`The custom ${label} JSON file exceeds the size limit`);
      }
      const suggested = suggestedImportId(file.name, kind);
      const answer = windowRef.prompt(
        `Choose an id for the imported custom ${label}. Use lowercase letters, numbers, hyphens or underscores.`,
        suggested,
      );
      if (answer === null) return null;
      const identifier = String(answer || "").trim();
      if (!IDENTIFIER_RE.test(identifier)) {
        setCopyMessage("The custom id is invalid. Use lowercase letters, numbers, hyphens or underscores.", "warning");
        render();
        return null;
      }
      importBusyKind = kind;
      setCopyMessage(`Reading and validating custom ${label} JSON…`, "warning");
      clearPreview();
      render();
      try {
        const content = await file.text();
        if (typeof content !== "string") throw new Error("The selected JSON file could not be read");
        const result = await api.postJson(IMPORT_CUSTOM_ENDPOINT, {
          kind: label,
          id: identifier,
          content,
        }, { usePayloadError: true });
        if (result?.custom?.source !== "custom"
          || result?.custom?.id !== identifier
          || typeof result?.custom?.revision !== "string"
          || result?.applied !== false) {
          throw new Error("The custom catalogue import response was unsafe");
        }
        const previousDraft = draft ? { ...draft } : null;
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        if (!previousDraft) seedDraft();
        else draft = previousDraft;
        const key = identityKey(result.custom);
        if (!entryFor(catalogue(kind), key)) {
          throw new Error("The imported custom catalogue item could not be reloaded");
        }
        draft[kind] = key;
        draftDirty = draftDiffers();
        setCopyMessage(`Custom ${label} ${identifier} was imported and selected as an unapplied draft.`, "success");
        return result;
      } catch (error) {
        const code = String(error?.payload?.code || "");
        if (code === "custom-exists") {
          setCopyMessage("A custom item with that id already exists. Choose another id.", "warning");
        } else {
          setCopyMessage(error?.message || `Could not import the custom ${label}`, "error");
        }
        throw error;
      } finally {
        importBusyKind = "";
        render();
      }
    }

    function editorKind(kind) {
      return kind === "vehicle" ? "profile" : "bindings";
    }

    function editorDirty() {
      return Boolean(editor) && editor.content !== editor.originalContent;
    }

    function setEditorMessage(text, kind = "") {
      editorMessage = String(text || "");
      editorMessageKind = String(kind || "");
    }

    function setEditorContent(content) {
      if (!editor || editorBusy) return false;
      editor.content = String(content ?? "");
      setEditorMessage("", "");
      return true;
    }

    async function openCustomEditor(kind) {
      const entry = selectedEntry(kind);
      if (entry?.source !== "custom") {
        throw new Error("Select a custom catalogue item to edit");
      }
      if (editor || editorBusy) return null;
      const label = editorKind(kind);
      editorBusy = true;
      setCopyMessage(`Loading custom ${label}…`, "warning");
      clearPreview();
      render();
      try {
        const result = await api.postJson(LOAD_CUSTOM_ENDPOINT, {
          kind: label,
          source: "custom",
          id: entry.id,
        }, { usePayloadError: true });
        if (result?.custom?.source !== "custom"
          || result?.custom?.id !== entry.id
          || typeof result?.custom?.revision !== "string"
          || typeof result?.content !== "string") {
          throw new Error("The custom catalogue editor received an unsafe response");
        }
        editor = {
          kind,
          id: entry.id,
          revision: result.custom.revision,
          content: result.content,
          originalContent: result.content,
          validation: result.validation || null,
        };
        setCopyMessage("", "");
        setEditorMessage(`Editing custom ${label} ${entry.id}.`, "warning");
        return result;
      } catch (error) {
        setCopyMessage(error?.message || `Could not load the custom ${label}`, "error");
        throw error;
      } finally {
        editorBusy = false;
        render();
      }
    }

    function editorTemplate() {
      if (!editor) return "";
      const label = editorKind(editor.kind);
      const message = editorMessage
        ? `<div class="openmmi-config-message ${escapeHtml(editorMessageKind || "warning")}" role="status" aria-live="polite" data-testid="vehicle-custom-editor-feedback">${escapeHtml(editorMessage)}</div>`
        : "";
      return `
        <section class="openmmi-vehicle-custom-editor" data-testid="vehicle-custom-editor" aria-label="Custom ${escapeHtml(label)} editor">
          <div class="openmmi-settings-subhead"><span>Edit custom ${escapeHtml(label)}</span><small>${escapeHtml(editor.id)}</small></div>
          <textarea class="openmmi-vehicle-custom-editor-content" data-openmmi-vehicle-custom-editor-content="true" data-testid="vehicle-custom-editor-content" spellcheck="false" ${editorBusy ? "disabled" : ""}>${escapeHtml(editor.content)}</textarea>
          <div class="openmmi-vehicle-custom-editor-meta">Expected revision: ${revisionTemplate(editor.revision, "vehicle-custom-editor-revision")}</div>
          ${message}
          <div class="openmmi-config-actions openmmi-vehicle-custom-editor-actions">
            <button type="button" class="openmmi-setting-pill" data-openmmi-vehicle-custom-save="true" data-testid="vehicle-custom-editor-save" ${editorBusy ? "disabled" : ""}>${editorBusy ? "Saving…" : "Save custom " + escapeHtml(label)}</button>
            <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-custom-close="true" data-testid="vehicle-custom-editor-close" ${editorBusy ? "disabled" : ""}>Close editor</button>
          </div>
          <p class="openmmi-vehicle-setup-note">Saving validates and replaces only this user-owned file. It does not update the maintained template, apply the revision, or restart canbusd.</p>
        </section>`;
    }

    async function saveCustomEditor() {
      if (!editor || editorBusy) return null;
      const textarea = documentRef.querySelector?.("[data-openmmi-vehicle-custom-editor-content]");
      if (textarea && typeof textarea.value === "string") editor.content = textarea.value;
      const editing = { ...editor };
      const label = editorKind(editing.kind);
      editorBusy = true;
      setEditorMessage(`Validating and saving custom ${label}…`, "warning");
      render();
      try {
        const result = await api.postJson(SAVE_CUSTOM_ENDPOINT, {
          kind: label,
          source: "custom",
          id: editing.id,
          expected_revision: editing.revision,
          content: editing.content,
        }, { usePayloadError: true });
        if (result?.custom?.source !== "custom"
          || result?.custom?.id !== editing.id
          || typeof result?.custom?.revision !== "string"
          || result?.applied !== false) {
          throw new Error("The custom catalogue save response was unsafe");
        }
        const previousDraft = draft ? { ...draft } : null;
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        if (!previousDraft) seedDraft();
        else draft = previousDraft;
        const key = identityKey(result.custom);
        if (!entryFor(catalogue(editing.kind), key)) {
          throw new Error("The saved custom catalogue item could not be reloaded");
        }
        editor = {
          ...editing,
          revision: result.custom.revision,
          originalContent: editing.content,
          content: editing.content,
          validation: result.validation || null,
        };
        draftDirty = draftDiffers();
        clearPreview();
        setEditorMessage(`Custom ${label} saved. Close the editor, review the setup, and apply the new revision when ready.`, "success");
        return result;
      } catch (error) {
        const code = String(error?.payload?.code || "");
        if (code === "custom-stale") {
          setEditorMessage("This custom item changed after it was opened. Your text was not written; close and reload before saving again.", "warning");
        } else {
          setEditorMessage(error?.message || `Could not save the custom ${label}`, "error");
        }
        throw error;
      } finally {
        editorBusy = false;
        render();
      }
    }

    function closeCustomEditor() {
      if (!editor || editorBusy) return false;
      if (editorDirty() && typeof windowRef.confirm === "function"
        && !windowRef.confirm("Discard the unsaved custom catalogue changes?")) {
        return false;
      }
      editor = null;
      setEditorMessage("", "");
      render();
      return true;
    }

    async function copyTemplate(kind) {
      const entry = selectedEntry(kind);
      if (entry?.source !== "maintained" || entry.valid === false || typeof entry.revision !== "string") {
        throw new Error("Select a valid maintained catalogue item to use as a template");
      }
      if (typeof windowRef.prompt !== "function") {
        throw new Error("Custom catalogue naming is unavailable");
      }
      const label = kind === "vehicle" ? "profile" : "bindings";
      const response = windowRef.prompt(
        `Choose an id for the new custom ${label}. Use lowercase letters, numbers, hyphens or underscores.`,
        suggestedCustomId(entry.id),
      );
      if (response === null) return null;
      const customId = String(response || "").trim();
      if (!IDENTIFIER_RE.test(customId)) {
        setCopyMessage("Custom ids must start with a lowercase letter or number and use only lowercase letters, numbers, hyphens or underscores.", "error");
        render();
        return null;
      }

      copyBusyKind = kind;
      setCopyMessage(`Creating custom ${label}…`, "warning");
      clearPreview();
      render();
      try {
        const result = await api.postJson(COPY_ENDPOINT, {
          kind: kind === "vehicle" ? "profile" : "bindings",
          id: customId,
          template_source: "maintained",
          template_id: entry.id,
          template_revision: entry.revision,
        }, { usePayloadError: true });
        const previousDraft = draft ? { ...draft } : null;
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        if (!previousDraft) seedDraft();
        else draft = previousDraft;
        const customKey = identityKey(result?.custom || { source: "custom", id: customId });
        if (!entryFor(catalogue(kind), customKey)) {
          throw new Error("The custom catalogue copy was created but could not be loaded");
        }
        draft[kind] = customKey;
        draftDirty = draftDiffers();
        setCopyMessage(`Custom ${label} ${customId} was created in your user catalogue. The maintained template was not changed.`, "success");
        return result;
      } catch (error) {
        const code = String(error?.payload?.code || "");
        if (code === "template-stale") {
          setCopyMessage("The maintained template changed. Refresh Vehicle Setup and copy it again.", "warning");
        } else if (code === "custom-exists") {
          setCopyMessage("That custom id already exists. Choose a different id.", "warning");
        } else {
          setCopyMessage(error?.message || `Could not create the custom ${label}`, "error");
        }
        throw error;
      } finally {
        copyBusyKind = "";
        render();
      }
    }

    async function manageCustomItem(action, kind) {
      if (loading || previewLoading || applyBusy || copyBusyKind || lifecycleBusyKind
        || editor || editorBusy) return null;
      const entry = selectedEntry(kind);
      const label = editorKind(kind);
      if (!entry || entry.source !== "custom"
        || typeof entry.revision !== "string" || !entry.revision) {
        throw new Error(`Select a custom ${label} to manage`);
      }
      if (!['duplicate', 'rename', 'delete'].includes(action)) {
        throw new Error("Custom catalogue lifecycle action is invalid");
      }
      if (entry.valid === false && action === "duplicate") {
        setCopyMessage(`Fix validation errors before duplicating this custom ${label}.`, "warning");
        render();
        return null;
      }
      const active = identityKey(activeIdentity(kind)) === identityKey(entry);
      if (active && action !== "duplicate") {
        setCopyMessage(`The active custom ${label} cannot be ${action === "rename" ? "renamed" : "deleted"}. Apply another ${label} first.`, "warning");
        render();
        return null;
      }
      let newId = "";
      if (action === "duplicate" || action === "rename") {
        if (typeof windowRef.prompt !== "function") throw new Error("Custom catalogue naming is unavailable");
        const response = windowRef.prompt(
          `${action === "duplicate" ? "Choose an id for the duplicate" : "Choose the new id"}. Use lowercase letters, numbers, hyphens or underscores.`,
          action === "duplicate" ? suggestedDuplicateId(entry.id) : entry.id,
        );
        if (response === null) return null;
        newId = String(response || "").trim();
        if (!IDENTIFIER_RE.test(newId) || newId === entry.id) {
          setCopyMessage("Choose a different valid id using lowercase letters, numbers, hyphens or underscores.", "error");
          render();
          return null;
        }
      }
      if (action === "delete") {
        if (typeof windowRef.confirm !== "function"
          || !windowRef.confirm(`Delete custom ${label} ${entry.id}? This cannot be undone.`)) return null;
      }

      lifecycleBusyKind = kind;
      setCopyMessage(`${action === "duplicate" ? "Duplicating" : action === "rename" ? "Renaming" : "Deleting"} custom ${label}…`, "warning");
      clearPreview();
      render();
      try {
        const request = {
          action,
          kind: label,
          source: "custom",
          id: entry.id,
          expected_revision: entry.revision,
        };
        if (newId) request.new_id = newId;
        const result = await api.postJson(MANAGE_CUSTOM_ENDPOINT, request, { usePayloadError: true });
        if (result?.operation !== action || result?.applied !== false) {
          throw new Error("The custom catalogue lifecycle response was unsafe");
        }
        if ((action === "duplicate" || action === "rename")
          && (result?.custom?.source !== "custom" || result?.custom?.id !== newId
            || typeof result?.custom?.revision !== "string")) {
          throw new Error("The custom catalogue lifecycle response was unsafe");
        }
        if (action === "delete"
          && (result?.deleted?.source !== "custom" || result?.deleted?.id !== entry.id)) {
          throw new Error("The custom catalogue lifecycle response was unsafe");
        }
        const previousDraft = draft ? { ...draft } : null;
        snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
        if (!previousDraft) seedDraft();
        else draft = previousDraft;
        if (action === "delete") {
          if (draft?.[kind] === identityKey(entry)) draft[kind] = identityKey(activeIdentity(kind));
        } else {
          const newKey = identityKey(result.custom);
          if (!entryFor(catalogue(kind), newKey)) {
            throw new Error("The managed custom catalogue item could not be reloaded");
          }
          draft[kind] = newKey;
        }
        draftDirty = draftDiffers();
        const verb = action === "duplicate" ? "duplicated" : action === "rename" ? "renamed" : "deleted";
        setCopyMessage(`Custom ${label} ${verb}. Review and Apply separately if the draft selection changed.`, "success");
        return result;
      } catch (error) {
        const code = String(error?.payload?.code || "");
        if (code === "custom-stale") {
          setCopyMessage("This custom item changed. Refresh Vehicle Setup and try again.", "warning");
        } else if (code === "custom-active") {
          setCopyMessage(`The active custom ${label} cannot be renamed or deleted. Apply another ${label} first.`, "warning");
        } else if (code === "custom-exists") {
          setCopyMessage("That custom id already exists. Choose a different id.", "warning");
        } else if (code === "lifecycle-busy") {
          setCopyMessage("Another Open MMI lifecycle operation is active. Try again after it completes.", "warning");
        } else {
          setCopyMessage(error?.message || `Could not ${action} the custom ${label}`, "error");
        }
        throw error;
      } finally {
        lifecycleBusyKind = "";
        render();
      }
    }

    function applyFeedbackTemplate() {
      const state = coordinatorState();
      const stateName = String(state.state || "");
      const active = ACTIVE_APPLY_STATES.has(stateName);
      const message = applyMessage || (active ? `Apply progress: ${applyStateLabel(stateName)}` : "");
      if (!message) return "";
      const kind = applyMessageKind || (active ? "warning" : stateName === "complete" ? "success" : "error");
      const restoration = state.restoration_attempted === true
        ? state.restoration_verified === true
          ? " Previous setup restoration was verified."
          : " Previous setup restoration could not be verified."
        : "";
      return `<div class="openmmi-config-message ${escapeHtml(kind)}" role="status" aria-live="polite" data-testid="vehicle-setup-apply-feedback">${escapeHtml(message)}${escapeHtml(restoration)}</div>`;
    }

    function terminalApplyMessage(state = {}) {
      if (state.state === "complete") return ["Vehicle setup applied and verified.", "success"];
      if (state.stage === "restored" && state.restoration_verified === true) {
        return [`${state.error || "Vehicle setup apply failed"}. The previous setup was restored and verified.`, "error"];
      }
      if (state.stage === "restore-unverified") {
        return [`${state.error || "Vehicle setup apply failed"}. Restoration could not be verified; do not retry until coordinator recovery succeeds.`, "error"];
      }
      return [state.error || "Vehicle setup apply failed", "error"];
    }

    function scheduleApplyPoll() {
      const stateName = String(coordinatorState().state || "");
      const active = ACTIVE_APPLY_STATES.has(stateName);
      const timeoutMs = Math.max(1000, Number(options.applyActionTimeoutMs || 60000));
      if (active && !applyPollDeadline) applyPollDeadline = Date.now() + timeoutMs;
      if (TERMINAL_APPLY_STATES.has(stateName)) applyPollDeadline = 0;
      const recovering = !coordinator && applyPollDeadline > Date.now();
      if (documentRef.hidden || applyPollTimer || applyBusy || (!active && !recovering)) return;
      const pollIntervalMs = Math.max(25, Number(options.applyPollIntervalMs || 250));
      applyPollTimer = windowRef.setTimeout(async () => {
        applyPollTimer = null;
        if (documentRef.hidden) return;
        await refreshCoordinator(false);
        const next = coordinatorState();
        if (TERMINAL_APPLY_STATES.has(String(next.state || "")) && next.state !== "idle") {
          const [message, kind] = terminalApplyMessage(next);
          setApplyMessage(message, kind);
          try {
            snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
            if (!draftDirty) seedDraft();
          } catch (_) {}
        }
        render();
        scheduleApplyPoll();
      }, pollIntervalMs);
    }

    async function refreshCoordinator(renderAfter = true) {
      try {
        coordinator = await api.getJson(COORDINATOR_ENDPOINT, { usePayloadError: true });
        coordinatorError = "";
        if (coordinator?.state) applyState = coordinator.state;
      } catch (error) {
        coordinator = null;
        coordinatorError = error?.message || "Could not reach the vehicle configuration coordinator";
      }
      scheduleApplyPoll();
      if (renderAfter) render();
      return coordinator;
    }

    function confirmed(messageText) {
      return typeof windowRef.confirm === "function" && windowRef.confirm(messageText) === true;
    }

    async function runApplyAction(body) {
      const pollIntervalMs = Math.max(25, Number(options.applyPollIntervalMs || 250));
      const timeoutMs = Math.max(1000, Number(options.applyActionTimeoutMs || 60000));
      const deadline = Date.now() + timeoutMs;
      let requestDone = false;
      let requestResult = null;
      let requestError = null;
      const request = api.postJson(APPLY_ENDPOINT, body, { usePayloadError: true })
        .then((result) => {
          requestResult = result;
          if (result?.state) applyState = result.state;
          return result;
        })
        .catch((error) => {
          requestError = error;
          if (error?.payload?.state) applyState = error.payload.state;
          return null;
        })
        .finally(() => { requestDone = true; });

      while (Date.now() < deadline) {
        await Promise.resolve();
        if (requestDone) {
          if (requestError) throw requestError;
          if (requestResult?.ok === true && requestResult?.state?.state === "complete") return requestResult;
          throw new Error("Vehicle setup apply returned an invalid result");
        }
        await Promise.race([
          request,
          new Promise((resolve) => windowRef.setTimeout(resolve, pollIntervalMs)),
        ]);
        if (!requestDone) {
          await refreshCoordinator(false);
          render();
        }
      }
      throw new Error("Timed out waiting for vehicle setup apply");
    }

    function applyFailureMessage(error) {
      const code = String(error?.payload?.code || "");
      const message = error?.message || "Vehicle setup apply failed";
      if (code === "stale-preview") {
        return ["The reviewed setup is stale. Review the current selection again before applying.", "warning"];
      }
      if (code === "busy") {
        return ["Another Open MMI lifecycle operation is active. Review again after it finishes.", "warning"];
      }
      if (code === "apply-failed-restored") {
        return [`${message}. The previous setup was restored and verified.`, "error"];
      }
      if (code === "apply-failed-restore-unverified") {
        return [`${message}. Restoration could not be verified; do not retry until coordinator recovery succeeds.`, "error"];
      }
      return [message, "error"];
    }

    async function applyDraft() {
      const body = applyPayload();
      if (!body || !coordinatorApplyReady()) {
        throw new Error("The reviewed vehicle setup is not ready to apply");
      }
      const vehicle = `${identityDisplay("vehicle", body.target.vehicle)} · ${sourceLabel(body.target.vehicle?.source)}`;
      const bindings = `${identityDisplay("bindings", body.target.bindings)} · ${sourceLabel(body.target.bindings?.source)}`;
      const bus = body.target.runtime?.active_bus || "--";
      const interfaceName = body.target.runtime?.buses?.[bus]?.interface || "--";
      if (!confirmed(`Apply ${vehicle} with ${bindings} on ${interfaceName}? The CAN service will restart and the loaded runtime will be verified.`)) {
        return null;
      }

      applyBusy = true;
      applyState = { state: "validating", stage: "submitting", error: "" };
      setApplyMessage("Applying the reviewed vehicle setup…", "warning");
      render();
      try {
        const result = await runApplyAction(body);
        try {
          snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
          seedDraft();
        } catch (_) {}
        await refreshCoordinator(false);
        clearPreview();
        applyState = result.state;
        setApplyMessage("Vehicle setup applied and verified.", "success");
        return result;
      } catch (error) {
        const failureState = error?.payload?.state && typeof error.payload.state === "object"
          ? error.payload.state
          : null;
        const [message, kind] = applyFailureMessage(error);
        if (String(error?.payload?.code || "") === "stale-preview") clearPreview();
        setApplyMessage(message, kind);
        await refreshCoordinator(false);
        if (failureState) applyState = failureState;
        throw error;
      } finally {
        applyBusy = false;
        render();
      }
    }

    function setDraft(kind, key) {
      if (editor || editorBusy) return false;
      if (!SOURCES.some((source) => String(key).startsWith(`${source}:`))) return false;
      const entry = entryFor(catalogue(kind), key);
      if (!entry || (entry.valid === false && entry.source !== "custom")) return false;
      if (!draft) seedDraft();
      draft[kind] = key;
      draftDirty = draftDiffers();
      clearPreview();
      render();
      return true;
    }

    function validationNote(entry, fallback) {
      if (!entry) return fallback;
      const errors = entry.validation?.errors?.length || 0;
      const warnings = entry.validation?.warnings?.length || 0;
      if (errors) {
        const first = entry.validation?.errors?.[0] || {};
        const detail = [first.code, first.path].filter(Boolean).join(" · ");
        return `${errors} validation error${errors === 1 ? "" : "s"}${detail ? ` · ${detail}` : ""}`;
      }
      if (warnings) return `${warnings} catalogue warning${warnings === 1 ? "" : "s"}`;
      return `${sourceLabel(entry.source)} catalogue · valid`;
    }

    function selectedBus() {
      const profile = selectedEntry("vehicle");
      const name = profile?.default_bus || snapshot?.active?.active_bus || "";
      const bus = (profile?.buses || []).find((entry) => entry?.name === name)
        || profile?.buses?.[0]
        || {};
      const active = snapshot?.active || {};
      const sameVehicle = draft?.vehicle === identityKey(activeIdentity("vehicle"));
      const activeInterface = String(active.interface || "");
      const preserveActiveInterface = sameVehicle
        && String(active.active_bus || "") === String(name)
        && INTERFACE_RE.test(activeInterface);
      return {
        name,
        ...bus,
        interface: preserveActiveInterface ? activeInterface : bus.interface,
      };
    }

    function previewRequest() {
      if (!draft || !snapshot) return null;
      const profile = selectedEntry("vehicle");
      const bindingsEntry = selectedEntry("bindings");
      if (!profile || !bindingsEntry || profile.valid === false || bindingsEntry.valid === false) return null;
      const vehicle = identityFromKey(draft.vehicle);
      const bindings = identityFromKey(draft.bindings);
      const bus = selectedBus();
      const busName = String(bus.name || "");
      const interfaceName = String(bus.interface || snapshot.active?.interface || "");
      if (!vehicle || !bindings || !IDENTIFIER_RE.test(busName) || !INTERFACE_RE.test(interfaceName)) {
        return null;
      }
      return {
        vehicle,
        bindings,
        runtime: {
          active_bus: busName,
          buses: { [busName]: { interface: interfaceName } },
        },
      };
    }

    function compatibilityLabel() {
      const report = snapshot?.compatibility || {};
      const matched = report.emitted_and_bound?.length || 0;
      const emittedMissing = report.emitted_unbound?.length || 0;
      const unused = report.bound_unemitted?.length || 0;
      if (emittedMissing) return `${matched} matched · ${emittedMissing} unbound event${emittedMissing === 1 ? "" : "s"}`;
      if (unused) return `${matched} matched · ${unused} unused binding${unused === 1 ? "" : "s"}`;
      return `${matched} events matched`;
    }

    function changeValue(field, value) {
      if (value === null || value === undefined || value === "") return "Not configured";
      if (field === "vehicle" || field === "bindings") {
        const kind = field === "vehicle" ? "vehicle" : "bindings";
        return `${identityDisplay(kind, value)} · ${sourceLabel(value?.source)}`;
      }
      return String(value);
    }

    function changeLabel(field) {
      return ({
        vehicle: "Vehicle profile",
        bindings: "Bindings",
        active_bus: "Active CAN bus",
        interface: "CAN adapter",
      })[field] || String(field || "Configuration");
    }

    function previewEffects(effects = {}) {
      const labels = [
        ["write_canonical_configuration", "Save the reviewed vehicle setup"],
        ["write_systemd_runtime", "Update the CAN service configuration"],
        ["write_udev_rules", "Update CAN adapter provisioning"],
        ["reload_user_manager", "Reload the user service manager"],
        ["reload_udev", "Reload CAN adapter rules"],
        ["restart_can_service", "Restart the CAN service"],
      ];
      return labels.filter(([key]) => effects?.[key] === true).map(([, label]) => label);
    }

    function previewTemplate() {
      if (previewLoading) {
        return `<div class="openmmi-config-message warning" role="status" data-testid="vehicle-setup-preview-loading">Checking the selected setup…</div>`;
      }
      if (previewError) {
        return `<div class="openmmi-config-message error" role="status" data-testid="vehicle-setup-preview-error">${escapeHtml(previewError)}</div>`;
      }
      if (!preview) return "";

      const canApply = Boolean(applyPayload()) && coordinatorApplyReady();
      const capabilityKind = canApply ? "warning" : coordinatorError ? "error" : "warning";
      const transaction = coordinatorState();
      const changes = Array.isArray(preview.plan?.changes) ? preview.plan.changes : [];
      const warnings = Array.isArray(preview.validation?.warnings) ? preview.validation.warnings : [];
      const effects = previewEffects(preview.plan?.effects);
      const interfaceState = preview.interface || {};
      const interfaceText = interfaceState.name
        ? `${interfaceState.name}${interfaceState.present === true ? interfaceState.up === true ? " · present and up" : " · present but down" : " · not detected"}`
        : "CAN adapter unavailable";
      const changeRows = changes.length
        ? changes.map((change) => `
            <div class="openmmi-vehicle-preview-change">
              <span>${escapeHtml(changeLabel(change?.field))}</span>
              <strong><span>${escapeHtml(changeValue(change?.field, change?.from))}</span><b aria-hidden="true">→</b><span>${escapeHtml(changeValue(change?.field, change?.to))}</span></strong>
            </div>
          `).join("")
        : `<p class="openmmi-vehicle-preview-empty">No active configuration values would change.</p>`;
      const warningRows = warnings.length
        ? `<ul class="openmmi-vehicle-preview-warnings">${warnings.map((warning) => `<li>${escapeHtml(warning?.message || warning?.code || "Configuration warning")}</li>`).join("")}</ul>`
        : `<p class="openmmi-vehicle-preview-ok">No compatibility warnings were reported.</p>`;
      const effectRows = effects.length
        ? `<ul>${effects.map((effect) => `<li>${escapeHtml(effect)}</li>`).join("")}</ul>`
        : `<p>No service or adapter changes are required.</p>`;

      return `
        <section class="openmmi-vehicle-setup-preview" aria-label="Vehicle setup review" data-testid="vehicle-setup-preview">
          <div class="openmmi-settings-subhead"><span>Review changes</span><small>confirmed apply</small></div>
          <div class="openmmi-vehicle-preview-changes">${changeRows}</div>
          <div class="openmmi-settings-metric openmmi-vehicle-preview-interface"><span>Selected CAN adapter</span><strong data-testid="vehicle-setup-preview-interface">${escapeHtml(interfaceText)}</strong></div>
          <div class="openmmi-vehicle-preview-block">
            <strong>Compatibility</strong>
            ${warningRows}
          </div>
          <details class="openmmi-vehicle-preview-effects" data-testid="vehicle-setup-preview-effects">
            <summary>What applying this setup would do</summary>
            ${effectRows}
          </details>
          ${applyBusy ? `<div class="openmmi-settings-metric openmmi-vehicle-apply-progress" aria-busy="true"><span>Apply progress</span><strong data-testid="vehicle-setup-apply-progress">${escapeHtml(applyStateLabel(transaction.state))}</strong></div>` : ""}
          ${applyFeedbackTemplate()}
          <div class="openmmi-config-message ${capabilityKind}" role="status" data-testid="vehicle-setup-apply-capability">${escapeHtml(applyCapabilityMessage())}</div>
          <div class="openmmi-config-actions openmmi-vehicle-setup-actions">
            <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-setup-preview-close="true" data-testid="vehicle-setup-preview-close" ${applyBusy ? "disabled" : ""}>Back to selection</button>
            <button type="button" class="openmmi-setting-pill is-selected" data-openmmi-vehicle-setup-apply="true" data-testid="vehicle-setup-apply" ${canApply ? "" : "disabled"}>${applyBusy ? "Applying…" : "Apply setup"}</button>
          </div>
        </section>
      `;
    }

    async function reviewDraft() {
      const request = previewRequest();
      if (!request) {
        previewError = "Choose a valid setup before requesting a review";
        render();
        return null;
      }
      const generation = ++previewGeneration;
      preview = null;
      previewError = "";
      previewLoading = true;
      render();
      try {
        const result = await api.postJson(PREVIEW_ENDPOINT, request, { usePayloadError: true });
        if (generation !== previewGeneration) return null;
        if (result?.read_only !== true || result?.apply_available !== false || result?.state !== "ready") {
          throw new Error("Vehicle setup preview was not safely available");
        }
        preview = result;
        await refreshCoordinator(false);
        return preview;
      } catch (error) {
        if (generation === previewGeneration) {
          previewError = error?.message || "Could not preview vehicle setup";
        }
        throw error;
      } finally {
        if (generation === previewGeneration) {
          previewLoading = false;
          render();
        }
      }
    }

    function template() {
      if (!snapshot) {
        const message = errorMessage || (loading ? "Loading vehicle catalogue…" : "Vehicle setup has not been loaded");
        const kind = errorMessage ? "error" : "warning";
        return `
          <div class="openmmi-vehicle-setup-panel" data-openmmi-vehicle-setup-panel="true">
            <div class="openmmi-settings-panel-head"><span>Vehicle setup</span><small>single CAN input</small></div>
            <div class="openmmi-config-message ${kind}" role="status">${escapeHtml(message)}</div>
            <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-setup-refresh="true" data-testid="vehicle-setup-refresh" ${loading ? "disabled" : ""}>Retry</button>
          </div>
        `;
      }

      if (!draft) seedDraft();
      const active = snapshot.active || {};
      const activeVehicle = activeIdentity("vehicle");
      const activeBindings = activeIdentity("bindings");
      const loadedVehicle = loadedIdentity("vehicle");
      const loadedBindings = loadedIdentity("bindings");
      const sync = runtimeSync();
      const profile = selectedEntry("vehicle");
      const bindings = selectedEntry("bindings");
      const bus = selectedBus();
      const changed = draftDiffers();
      const canReview = Boolean(previewRequest()) && !loading && !previewLoading && !applyBusy
        && !copyBusyKind && !lifecycleBusyKind && !importBusyKind && !editor && !editorBusy;
      const activeReady = active.state === "ready";
      const statusText = preview
        ? changed
          ? "Review ready — draft selections remain unapplied."
          : sync.state === "saved-unapplied"
            ? "Review ready — saved revisions remain unloaded until Apply is confirmed."
            : "Review ready — the configured and loaded setup would remain unchanged."
        : changed
          ? "Draft selections are not applied. The running CAN service remains unchanged."
          : sync.state === "saved-unapplied"
            ? "Saved custom revisions await review and Apply. The CAN service is still using the previously loaded revisions."
            : activeReady && sync.state === "matched"
              ? "Configured and loaded setup match."
              : activeReady
                ? sync.label
                : `Configured setup needs attention: ${(active.errors || []).join(", ") || "status unavailable"}`;
      const statusKind = changed || (activeReady && sync.state !== "matched")
        ? "warning"
        : activeReady && sync.state === "matched"
          ? "success"
          : "error";
      const interfaceName = bus.interface || active.interface || "";
      const interfaceEntry = (snapshot.interfaces || []).find((entry) => entry?.name === interfaceName);
      const interfacePresent = interfaceEntry?.present === true
        || (interfaceName === active.interface && active.interface_present === true);
      const interfaceText = interfaceName
        ? `${interfaceName}${interfacePresent ? " · connected" : " · not detected"}`
        : "not selected";
      const issues = snapshot.catalogue?.issues || [];
      const issueText = issues.length
        ? `${issues.length} catalogue issue${issues.length === 1 ? "" : "s"}`
        : "catalogue ready";

      return `
        <div class="openmmi-vehicle-setup-panel" data-openmmi-vehicle-setup-panel="true" data-openmmi-vehicle-setup-ready="true">
          <div class="openmmi-settings-panel-head"><span>Vehicle setup</span><small>${escapeHtml(snapshot.runtime_mode === "single" ? "single CAN input" : snapshot.runtime_mode || "runtime")}</small></div>
          <div class="openmmi-config-message ${statusKind}" role="status" data-testid="vehicle-setup-status">${escapeHtml(statusText)}</div>

          <div class="openmmi-vehicle-setup-active" aria-label="Configured vehicle setup">
            <div class="openmmi-settings-metric"><span>Configured profile</span><strong data-testid="vehicle-setup-active-profile">${escapeHtml(identityDisplay("vehicle", activeVehicle))} · ${sourceLabel(activeVehicle.source)}</strong></div>
            <div class="openmmi-settings-metric"><span>Configured bindings</span><strong data-testid="vehicle-setup-active-bindings">${escapeHtml(identityDisplay("bindings", activeBindings))} · ${sourceLabel(activeBindings.source)}</strong></div>
          </div>

          <div class="openmmi-vehicle-runtime-sync is-${escapeHtml(sync.state)}" data-testid="vehicle-setup-runtime-sync">
            <span>Running CAN service</span><strong>${escapeHtml(sync.label)}</strong>
          </div>

          <div class="openmmi-settings-subhead"><span>Draft selection</span><small>${changed ? "not applied" : "matches configured selection"}</small></div>
          <div class="openmmi-vehicle-setup-selectors">
            <div class="openmmi-vehicle-setup-selector">
              <label for="openMmiVehicleProfile"><strong>Vehicle profile</strong><small>${escapeHtml(validationNote(profile, "Choose a valid profile"))}</small></label>
              <div class="openmmi-vehicle-setup-selection-control">
                <select id="openMmiVehicleProfile" data-openmmi-vehicle-setup-select="vehicle" data-testid="vehicle-setup-profile" ${loading || previewLoading || applyBusy || copyBusyKind || lifecycleBusyKind || importBusyKind || editor || editorBusy ? "disabled" : ""}>
                  ${optionGroups(catalogue("vehicle"), draft.vehicle, activeVehicle)}
                </select>
                ${customCopyControl("vehicle", profile)}
                ${importControl("vehicle")}
              </div>
            </div>
            <div class="openmmi-vehicle-setup-selector">
              <label for="openMmiVehicleBindings"><strong>Bindings</strong><small>${escapeHtml(validationNote(bindings, "Choose valid bindings"))}</small></label>
              <div class="openmmi-vehicle-setup-selection-control">
                <select id="openMmiVehicleBindings" data-openmmi-vehicle-setup-select="bindings" data-testid="vehicle-setup-bindings" ${loading || previewLoading || applyBusy || copyBusyKind || lifecycleBusyKind || importBusyKind || editor || editorBusy ? "disabled" : ""}>
                  ${optionGroups(catalogue("bindings"), draft.bindings, activeBindings)}
                </select>
                ${customCopyControl("bindings", bindings)}
                ${importControl("bindings")}
              </div>
            </div>
          </div>
          ${copyFeedbackTemplate()}
          ${editorTemplate()}

          <div class="openmmi-settings-subhead"><span>CAN input</span><small>profile summary</small></div>
          <div class="openmmi-vehicle-setup-summary">
            <div class="openmmi-settings-metric"><span>Selected CAN bus</span><strong data-testid="vehicle-setup-bus">${escapeHtml(bus.name || active.active_bus || "--")}</strong></div>
            <div class="openmmi-settings-metric"><span>Selected CAN adapter</span><strong data-testid="vehicle-setup-interface">${escapeHtml(interfaceText)}</strong></div>
            <div class="openmmi-settings-metric"><span>Expected bitrate</span><strong data-testid="vehicle-setup-bitrate">${escapeHtml(bitrateLabel(bus.bitrate))}</strong></div>
            <div class="openmmi-settings-metric"><span>Selected compatibility</span><strong data-testid="vehicle-setup-compatibility">${escapeHtml(compatibilityLabel())}</strong></div>
          </div>

          <div class="openmmi-config-actions openmmi-vehicle-setup-actions">
            <button type="button" class="openmmi-settings-link" data-openmmi-vehicle-setup-refresh="true" data-testid="vehicle-setup-refresh" ${loading || previewLoading || applyBusy || copyBusyKind || lifecycleBusyKind || importBusyKind || editor || editorBusy ? "disabled" : ""}>Refresh status</button>
            <button type="button" class="openmmi-setting-pill" data-openmmi-vehicle-setup-review="true" data-testid="vehicle-setup-review" ${canReview ? "" : "disabled"}>${previewLoading ? "Checking setup…" : preview ? "Refresh review" : changed ? "Review changes" : "Review current setup"}</button>
          </div>
          <p class="openmmi-vehicle-setup-note">Applying requires this exact review and an explicit confirmation. The coordinator verifies the loaded runtime and restores the previous setup after a failed mutation.</p>

          ${preview ? "" : applyFeedbackTemplate()}
          ${previewTemplate()}

          <details class="openmmi-vehicle-setup-technical" data-testid="vehicle-setup-technical">
            <summary>Technical details</summary>
            <div class="openmmi-settings-metric"><span>Configured state</span><strong>${escapeHtml(active.state || "unavailable")}</strong></div>
            <div class="openmmi-settings-metric"><span>Loaded state</span><strong>${escapeHtml(active.loaded?.state || "unavailable")}</strong></div>
            <div class="openmmi-settings-metric"><span>Catalogue</span><strong>${escapeHtml(issueText)}</strong></div>
            <div class="openmmi-settings-metric"><span>Configured profile revision</span>${revisionTemplate(activeVehicle.revision, "vehicle-setup-configured-profile-revision")}</div>
            <div class="openmmi-settings-metric"><span>Loaded profile revision</span>${revisionTemplate(loadedVehicle.revision, "vehicle-setup-loaded-profile-revision")}</div>
            <div class="openmmi-settings-metric"><span>Configured bindings revision</span>${revisionTemplate(activeBindings.revision, "vehicle-setup-configured-bindings-revision")}</div>
            <div class="openmmi-settings-metric"><span>Loaded bindings revision</span>${revisionTemplate(loadedBindings.revision, "vehicle-setup-loaded-bindings-revision")}</div>
          </details>
        </div>
      `;
    }

    function render() {
      if (activeSection() !== "vehicle-setup") return false;
      const panel = documentRef.querySelector("#openmmiSettingsPanel");
      if (!panel) return false;
      panel.innerHTML = template();
      return true;
    }

    async function refresh() {
      clearPreview();
      loading = true;
      attempted = true;
      errorMessage = "";
      setApplyMessage("", "");
      applyState = null;
      render();
      try {
        const next = await api.getJson(ENDPOINT, { usePayloadError: true });
        snapshot = next;
        if (!draft || !draftDirty) seedDraft();
        await refreshCoordinator(false);
        return snapshot;
      } catch (error) {
        errorMessage = error?.message || "Could not load vehicle setup";
        connectionUnavailable = true;
        throw error;
      } finally {
        loading = false;
        render();
      }
    }

    function renderActive() {
      if (activeSection() !== "vehicle-setup") return;
      render();
      if (!attempted && !loading) refresh().catch(() => {});
    }

    function inputHandler(event) {
      const textarea = event.target?.closest?.("[data-openmmi-vehicle-custom-editor-content]");
      if (!textarea || !editor || editorBusy) return;
      editor.content = String(textarea.value ?? "");
      setEditorMessage("", "");
    }

    function changeHandler(event) {
      const importInput = event.target?.closest?.("[data-openmmi-vehicle-custom-import-file]");
      if (importInput) {
        const file = importInput.files?.[0] || null;
        importInput.value = "";
        if (file) importCustomFile(importInput.dataset.openmmiVehicleCustomImportFile, file).catch(() => {});
        return;
      }
      const select = event.target?.closest?.("[data-openmmi-vehicle-setup-select]");
      if (!select) return;
      setDraft(select.dataset.openmmiVehicleSetupSelect, select.value);
    }

    function clickHandler(event) {
      const refreshButton = event.target?.closest?.("[data-openmmi-vehicle-setup-refresh]");
      if (refreshButton) {
        refresh().catch(() => {});
        return;
      }
      const importButton = event.target?.closest?.("[data-openmmi-vehicle-custom-import]");
      if (importButton && !importButton.disabled) {
        documentRef.querySelector?.(`[data-openmmi-vehicle-custom-import-file="${importButton.dataset.openmmiVehicleCustomImport}"]`)?.click?.();
        return;
      }
      const copyButton = event.target?.closest?.("[data-openmmi-vehicle-setup-copy]");
      if (copyButton && !copyButton.disabled) {
        copyTemplate(copyButton.dataset.openmmiVehicleSetupCopy).catch(() => {});
        return;
      }
      const manageButton = event.target?.closest?.("[data-openmmi-vehicle-custom-manage]");
      if (manageButton && !manageButton.disabled) {
        manageCustomItem(
          manageButton.dataset.openmmiVehicleCustomManage,
          manageButton.dataset.openmmiVehicleCustomKind,
        ).catch(() => {});
        return;
      }
      const editButton = event.target?.closest?.("[data-openmmi-vehicle-custom-edit]");
      if (editButton && !editButton.disabled) {
        openCustomEditor(editButton.dataset.openmmiVehicleCustomEdit).catch(() => {});
        return;
      }
      const saveButton = event.target?.closest?.("[data-openmmi-vehicle-custom-save]");
      if (saveButton && !saveButton.disabled) {
        saveCustomEditor().catch(() => {});
        return;
      }
      const closeEditorButton = event.target?.closest?.("[data-openmmi-vehicle-custom-close]");
      if (closeEditorButton && !closeEditorButton.disabled) {
        closeCustomEditor();
        return;
      }
      const reviewButton = event.target?.closest?.("[data-openmmi-vehicle-setup-review]");
      if (reviewButton && !reviewButton.disabled) {
        reviewDraft().catch(() => {});
        return;
      }
      const applyButton = event.target?.closest?.("[data-openmmi-vehicle-setup-apply]");
      if (applyButton && !applyButton.disabled) {
        applyDraft().catch(() => {});
        return;
      }
      const closeButton = event.target?.closest?.("[data-openmmi-vehicle-setup-preview-close]");
      if (closeButton) {
        clearPreview();
        render();
      }
    }

    documentRef.addEventListener("input", inputHandler);
    documentRef.addEventListener("change", changeHandler);
    documentRef.addEventListener("click", clickHandler);
    documentRef.addEventListener("visibilitychange", scheduleApplyPoll);
    windowRef.addEventListener("openmmi:settingsrender", () => windowRef.requestAnimationFrame(renderActive));
    windowRef.addEventListener("openmmi:dashboardconnection", (event) => {
      if (event?.detail?.state !== "ready") {
        if (attempted) connectionUnavailable = true;
        return;
      }
      if (connectionUnavailable && attempted && !loading && activeSection() === "vehicle-setup") {
        connectionUnavailable = false;
        refresh().catch(() => {});
      }
    });

    return Object.freeze({
      activeSection,
      applyDraft,
      copyTemplate,
      openCustomEditor,
      manageCustomItem,
      importCustomFile,
      saveCustomEditor,
      closeCustomEditor,
      setEditorContent,
      editorDirty,
      applyPayload,
      applyStateLabel,
      coordinatorApplyReady,
      draftDiffers,
      previewRequest,
      refresh,
      refreshCoordinator,
      render,
      renderActive,
      reviewDraft,
      scheduleApplyPoll,
      setDraft,
      snapshot: () => snapshot,
      draft: () => draft ? { ...draft } : null,
      preview: () => preview,
      coordinator: () => coordinator,
      applyState: () => coordinatorState(),
      editor: () => editor ? { ...editor } : null,
      template,
    });
  }

  function install(options = {}) {
    return createController(options);
  }

  return Object.freeze({
    ENDPOINT,
    PREVIEW_ENDPOINT,
    APPLY_ENDPOINT,
    COPY_ENDPOINT,
    LOAD_CUSTOM_ENDPOINT,
    SAVE_CUSTOM_ENDPOINT,
    MANAGE_CUSTOM_ENDPOINT,
    IMPORT_CUSTOM_ENDPOINT,
    COORDINATOR_ENDPOINT,
    createController,
    escapeHtml,
    identityFromKey,
    identityKey,
    install,
  });
});
