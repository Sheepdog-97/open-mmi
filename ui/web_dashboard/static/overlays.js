(function openMmiOverlaysModule(root, factory) {
  const overlays = factory(root);
  if (typeof module === "object" && module.exports) module.exports = overlays;
  if (root) root.openMmiOverlays = overlays;
})(typeof globalThis !== "undefined" ? globalThis : this, function createOpenMmiOverlays(root) {
  "use strict";

  const DOOR_LABELS = Object.freeze({
    driver: "Driver door",
    driver_door: "Driver door",
    front_left: "Front left door",
    front_left_door: "Front left door",
    passenger: "Passenger door",
    passenger_door: "Passenger door",
    front_right: "Front right door",
    front_right_door: "Front right door",
    rear_left: "Rear left door",
    rear_left_door: "Rear left door",
    rear_right: "Rear right door",
    rear_right_door: "Rear right door",
    boot: "Boot",
    trunk: "Boot",
    tailgate: "Tailgate",
    hatch: "Tailgate",
    bonnet: "Bonnet",
    hood: "Bonnet",
  });

  function normaliseDoorKey(key) {
    return String(key || "")
      .trim()
      .toLowerCase()
      .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
      .replace(/[\s\-.]+/g, "_")
      .replace(/^is_/, "")
      .replace(/^door_/, "")
      .replace(/_status$/, "")
      .replace(/_state$/, "")
      .replace(/_ajar$/, "")
      .replace(/_open$/, "")
      .replace(/^open_/, "")
      .replace(/^ajar_/, "");
  }

  function doorLabel(path) {
    const normalised = normaliseDoorKey(path);
    if (DOOR_LABELS[normalised]) return DOOR_LABELS[normalised];

    const parts = normalised.split("_").filter(Boolean);
    const hasDoorWord = parts.includes("door");
    const joined = parts.filter((part) => part !== "door").join("_");
    if (DOOR_LABELS[joined]) return DOOR_LABELS[joined];

    if (normalised.includes("driver")) return "Driver door";
    if (normalised.includes("passenger")) return "Passenger door";
    if (normalised.includes("front_left") || normalised.includes("left_front")) return "Front left door";
    if (normalised.includes("front_right") || normalised.includes("right_front")) return "Front right door";
    if (normalised.includes("rear_left") || normalised.includes("left_rear")) return "Rear left door";
    if (normalised.includes("rear_right") || normalised.includes("right_rear")) return "Rear right door";
    if (normalised.includes("boot") || normalised.includes("trunk")) return "Boot";
    if (normalised.includes("tailgate") || normalised.includes("hatch")) return "Tailgate";
    if (normalised.includes("bonnet") || normalised.includes("hood")) return "Bonnet";

    const readable = parts
      .filter((part) => part && part !== "open" && part !== "ajar" && part !== "status" && part !== "state")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
    return hasDoorWord ? readable : `${readable || "Door"} door`;
  }

  function looksDoorRelated(path) {
    const value = String(path || "").toLowerCase();
    if (/(lock|locked|unlock|window|mirror|seat|module|count)/.test(value)) return false;
    return /(door|boot|trunk|tailgate|hatch|bonnet|hood)/.test(value);
  }

  function isOpenDoorValue(value) {
    if (value === true) return true;
    if (value === false || value === null || value === undefined) return false;
    if (typeof value === "number") return Number.isFinite(value) && value !== 0;
    const text = String(value).trim().toLowerCase();
    if (!text) return false;
    if (["open", "opened", "ajar", "unlatched", "active", "true", "yes", "on", "1"].includes(text)) return true;
    if (["closed", "shut", "latched", "inactive", "false", "no", "off", "0"].includes(text)) return false;
    return /\b(open|ajar|unlatched)\b/.test(text);
  }

  function scanDoorObject(obj, basePath, out) {
    if (!obj || typeof obj !== "object") return;
    if (Array.isArray(obj)) {
      obj.forEach((item, index) => scanDoorObject(item, `${basePath}[${index}]`, out));
      return;
    }

    for (const [key, value] of Object.entries(obj)) {
      const path = basePath ? `${basePath}.${key}` : key;
      if (value && typeof value === "object") {
        scanDoorObject(value, path, out);
        continue;
      }
      if (looksDoorRelated(path) && isOpenDoorValue(value)) out.set(doorLabel(path), true);
    }
  }

  function collectOpenDoors(payload) {
    const source = payload || {};
    const decoded = source.state || source.decoded || source;
    const vehicle = decoded.vehicle || {};
    const body = decoded.body || decoded.comfort || decoded.central_convenience || {};
    const doors = decoded.doors || vehicle.doors || body.doors || {};
    const out = new Map();

    scanDoorObject(doors, "doors", out);
    scanDoorObject(vehicle, "vehicle", out);
    scanDoorObject(body, "body", out);
    scanDoorObject(decoded.doors_status || decoded.door_status || {}, "door_status", out);

    return Array.from(out.keys()).sort((left, right) => left.localeCompare(right));
  }

  function normalReverseText(value) {
    return String(value === null || value === undefined ? "" : value)
      .trim()
      .toLowerCase()
      .replace(/[\s-]+/g, "_");
  }

  function truthyReverseValue(value) {
    if (value === true) return true;
    if (value === false || value === null || value === undefined) return false;
    if (typeof value === "number") return Number.isFinite(value) && value !== 0;

    const text = normalReverseText(value);
    if (!text) return false;
    if (["false", "no", "off", "0", "inactive", "not_reverse", "not_reversing", "park", "parking", "neutral", "drive", "d"].includes(text)) return false;
    if (["true", "yes", "on", "1", "active", "reverse", "reversing", "reverse_selected", "r", "gear_r"].includes(text)) return true;
    return /(^|_)(reverse|reversing)(_|$)/.test(text) || text === "r";
  }

  function firstValue(...values) {
    for (const value of values) {
      if (value !== undefined && value !== null && value !== "") return value;
    }
    return undefined;
  }

  function scanForReverse(obj, basePath = "") {
    if (!obj || typeof obj !== "object") return false;
    if (Array.isArray(obj)) return obj.some((item, index) => scanForReverse(item, `${basePath}[${index}]`));

    for (const [key, value] of Object.entries(obj)) {
      const path = basePath ? `${basePath}.${key}` : key;
      const lowerPath = path.toLowerCase();
      if (value && typeof value === "object") {
        if (scanForReverse(value, path)) return true;
        continue;
      }
      if (/(reverse|reversing|gear|selector|transmission)/.test(lowerPath)
        && !/(assist|overlay|camera|pdc|setting|mode)/.test(lowerPath)
        && truthyReverseValue(value)) {
        return true;
      }
    }
    return false;
  }

  function reverseSelected(payload) {
    const source = payload || {};
    const decoded = source.state || source.decoded || source;
    const vehicle = decoded.vehicle || {};
    const drivetrain = decoded.drivetrain || decoded.transmission || decoded.gearbox || {};
    const status = decoded.status || source.status || {};

    const direct = firstValue(
      vehicle.reverse,
      vehicle.reverse_selected,
      vehicle.reverse_gear,
      vehicle.reversing,
      drivetrain.reverse,
      drivetrain.reverse_selected,
      drivetrain.gear,
      drivetrain.selector,
      status.reverse,
      status.reverse_selected,
      decoded.reverse,
      decoded.reverse_selected,
    );

    if (truthyReverseValue(direct)) return true;
    if (direct !== undefined && direct !== null && direct !== "") return false;
    return scanForReverse(decoded);
  }

  function reduceDoorOverlay(state, openDoors) {
    const current = state || { currentSignature: "", dismissedSignature: "", visible: false };
    const signature = Array.from(openDoors || []).join("|");
    const next = {
      currentSignature: signature,
      dismissedSignature: current.dismissedSignature || "",
      visible: false,
    };
    if (!signature) {
      next.dismissedSignature = "";
      return next;
    }
    next.visible = signature !== next.dismissedSignature;
    return next;
  }

  function dismissDoorOverlay(state) {
    return {
      currentSignature: state.currentSignature || "",
      dismissedSignature: state.currentSignature || "",
      visible: false,
    };
  }

  function reduceReverseOverlay(state, active) {
    const current = state || { active: false, dismissedThisReverse: false, visible: false };
    if (!active) return { active: false, dismissedThisReverse: false, visible: false };
    return {
      active: true,
      dismissedThisReverse: !!current.dismissedThisReverse,
      visible: !current.dismissedThisReverse,
    };
  }

  function dismissReverseOverlay(state) {
    return {
      active: !!state.active,
      dismissedThisReverse: true,
      visible: false,
    };
  }

  function vehiclePageActive(documentRef) {
    const page = documentRef && typeof documentRef.querySelector === "function"
      ? documentRef.querySelector("#pageVehicle")
      : null;
    return !!(page && page.classList && typeof page.classList.contains === "function" && page.classList.contains("active"));
  }

  function createController(options = {}) {
    const documentRef = options.document || (root && root.document);
    const windowRef = options.window || root;
    if (!documentRef || typeof documentRef.querySelector !== "function") {
      throw new TypeError("Overlays require a DOM document");
    }

    const doorState = { currentSignature: "", dismissedSignature: "", visible: false };
    const reverseState = { active: false, dismissedThisReverse: false, visible: false };
    const suppressDoorOverlay = typeof options.suppressDoorOverlay === "function"
      ? options.suppressDoorOverlay
      : () => vehiclePageActive(documentRef);
    let initialized = false;
    let pageChangeHandler = null;
    const one = (selector) => documentRef.querySelector(selector);

    function copyState(target, next) {
      Object.keys(target).forEach((key) => { delete target[key]; });
      Object.assign(target, next);
      return target;
    }

    function syncDoorOverlayVehicleVisual(overlay) {
      if (!overlay) return;
      const host = overlay.querySelector("#openMmiDoorOverlayCarHost");
      const source = one("#carShell");
      if (!host || !source) return;

      let clone = host.querySelector(".car-shell");
      if (!clone) {
        clone = source.cloneNode(true);
        clone.removeAttribute("id");
        clone.classList.add("openmmi-door-overlay-car-shell");
        clone.setAttribute("aria-hidden", "true");
        host.replaceChildren(clone);
      }

      clone.classList.toggle("any-open", source.classList.contains("any-open"));
      clone.querySelectorAll("[data-door-mark]").forEach((mark) => {
        const key = mark.getAttribute("data-door-mark");
        const liveMark = source.querySelector(`[data-door-mark="${key}"]`);
        mark.classList.toggle("open", !!(liveMark && liveMark.classList.contains("open")));
      });

      const list = overlay.querySelector("#openMmiDoorOverlayList");
      if (list) {
        list.textContent = "";
        list.hidden = true;
        list.setAttribute("aria-hidden", "true");
      }
    }

    function hideDoorOverlay() {
      const overlay = one("#openMmiVehicleOverlay");
      if (!overlay) return;
      overlay.setAttribute("hidden", "");
      overlay.classList.remove("is-visible");
    }

    function showDoorOverlay() {
      const overlay = ensureDoorOverlay();
      const list = overlay.querySelector("#openMmiDoorOverlayList");
      if (list) {
        list.textContent = "";
        list.hidden = true;
        list.setAttribute("aria-hidden", "true");
      }
      overlay.removeAttribute("hidden");
      overlay.classList.add("is-visible");
    }

    function ensureDoorOverlay() {
      let overlay = one("#openMmiVehicleOverlay");
      if (overlay) return overlay;

      overlay = documentRef.createElement("div");
      overlay.id = "openMmiVehicleOverlay";
      overlay.className = "openmmi-vehicle-overlay";
      overlay.setAttribute("aria-live", "polite");
      overlay.setAttribute("hidden", "");
      overlay.innerHTML = `
        <div class="openmmi-vehicle-overlay-card openmmi-door-overlay-visual-card" role="status" aria-label="Door open alert">
          <div class="openmmi-door-overlay-car-host" id="openMmiDoorOverlayCarHost" aria-hidden="true"></div>
          <div class="openmmi-vehicle-overlay-list" id="openMmiDoorOverlayList" hidden aria-hidden="true"></div>
          <button type="button" class="openmmi-vehicle-overlay-dismiss" id="openMmiDoorOverlayDismiss">Dismiss</button>
        </div>
      `;

      const footer = one("footer.status-strip") || one("footer");
      ((footer && footer.parentNode) || documentRef.body).insertBefore(overlay, footer || null);
      syncDoorOverlayVehicleVisual(overlay);

      const dismiss = overlay.querySelector("#openMmiDoorOverlayDismiss");
      if (dismiss) dismiss.addEventListener("click", () => {
        copyState(doorState, dismissDoorOverlay(doorState));
        hideDoorOverlay();
      });
      return overlay;
    }

    function hideReverseOverlay() {
      const overlay = one("#openMmiReverseOverlay");
      if (!overlay) return;
      overlay.setAttribute("hidden", "");
      overlay.classList.remove("is-visible");
    }

    function showReverseOverlay() {
      const overlay = ensureReverseOverlay();
      overlay.removeAttribute("hidden");
      overlay.classList.add("is-visible");
    }

    function ensureReverseOverlay() {
      let overlay = one("#openMmiReverseOverlay");
      if (overlay) return overlay;

      overlay = documentRef.createElement("div");
      overlay.id = "openMmiReverseOverlay";
      overlay.className = "openmmi-reverse-overlay";
      overlay.setAttribute("aria-live", "polite");
      overlay.setAttribute("hidden", "");
      overlay.innerHTML = `
        <div class="openmmi-reverse-overlay-card" role="status" aria-label="Reverse assist alert">
          <div class="openmmi-reverse-overlay-kicker">Reverse assist</div>
          <h2>Reverse selected</h2>
          <p>Camera/PDC overlay placeholder. Rear assist settings will live under Settings → Reverse assist.</p>
          <div class="openmmi-reverse-overlay-grid" aria-hidden="true">
            <span></span><span></span><span></span><span></span>
          </div>
          <button type="button" class="openmmi-reverse-overlay-dismiss" id="openMmiReverseOverlayDismiss">Dismiss</button>
        </div>
      `;

      const footer = one("footer.status-strip") || one("footer");
      ((footer && footer.parentNode) || documentRef.body).insertBefore(overlay, footer || null);
      const dismiss = overlay.querySelector("#openMmiReverseOverlayDismiss");
      if (dismiss) dismiss.addEventListener("click", () => {
        copyState(reverseState, dismissReverseOverlay(reverseState));
        hideReverseOverlay();
      });
      return overlay;
    }

    function doorOverlaySuppressed() {
      try { return !!suppressDoorOverlay(); }
      catch (_) { return false; }
    }

    function syncDoorOverlayVisibility() {
      const visible = doorState.visible && !doorOverlaySuppressed();
      if (visible) showDoorOverlay();
      else hideDoorOverlay();
      return visible;
    }

    function updateDoor(payload) {
      copyState(doorState, reduceDoorOverlay(doorState, collectOpenDoors(payload)));
      return syncDoorOverlayVisibility();
    }

    function updateReverse(payload) {
      copyState(reverseState, reduceReverseOverlay(reverseState, reverseSelected(payload)));
      if (reverseState.visible) showReverseOverlay();
      else hideReverseOverlay();
      return reverseState.visible;
    }

    function update(payload) {
      return Object.freeze({
        doorVisible: updateDoor(payload),
        reverseVisible: updateReverse(payload),
      });
    }

    function init() {
      if (initialized) return false;
      ensureDoorOverlay();
      ensureReverseOverlay();
      initialized = true;
      if (windowRef) {
        pageChangeHandler = () => syncDoorOverlayVisibility();
        if (typeof windowRef.addEventListener === "function") {
          windowRef.addEventListener("openmmi:pagechange", pageChangeHandler);
        }
        windowRef.__openMmiDoorOverlayV1Loaded = true;
        windowRef.__openMmiReverseOverlayV1Loaded = true;
        windowRef.openMmiDoorOverlayState = doorState;
        windowRef.openMmiReverseOverlayState = reverseState;
      }
      return true;
    }

    function getSnapshot() {
      return Object.freeze({
        initialized,
        door: Object.freeze({ ...doorState }),
        reverse: Object.freeze({ ...reverseState }),
      });
    }

    return Object.freeze({
      getSnapshot,
      init,
      update,
      updateDoor,
      updateReverse,
    });
  }

  return Object.freeze({
    collectOpenDoors,
    createController,
    dismissDoorOverlay,
    dismissReverseOverlay,
    doorLabel,
    normaliseDoorKey,
    reduceDoorOverlay,
    reduceReverseOverlay,
    reverseSelected,
    truthyReverseValue,
    vehiclePageActive,
  });
});
