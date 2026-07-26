(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.openMmiTripSwitcher = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function createTripSwitcherModule(root) {
  "use strict";

  const STORAGE_KEY = "openmmi.trip.display.v1";

  function normaliseTrip(value) {
    return String(value || "").toLowerCase() === "b" ? "b" : "a";
  }

  function readActiveTrip(preferences) {
    if (!preferences || typeof preferences.readJson !== "function") return "a";
    return normaliseTrip(preferences.readJson(STORAGE_KEY, "a"));
  }

  function writeActiveTrip(preferences, value) {
    if (!preferences || typeof preferences.writeJson !== "function") return false;
    return preferences.writeJson(STORAGE_KEY, normaliseTrip(value));
  }

  function setHidden(nodes, hidden) {
    Array.from(nodes || []).forEach((node) => { node.hidden = hidden; });
  }

  function renderCard(card, activeTrip) {
    if (!card) return;
    const active = normaliseTrip(activeTrip);
    const next = active === "a" ? "b" : "a";
    const label = card.querySelector?.("[data-openmmi-trip-label]");
    const button = card.querySelector?.("[data-openmmi-trip-next]");

    if (card.dataset) card.dataset.openmmiTripActive = active;
    if (label) label.textContent = `Trip ${active.toUpperCase()}`;
    setHidden(card.querySelectorAll?.("[data-openmmi-trip-a], [data-openmmi-trip-a-unit]"), active !== "a");
    setHidden(card.querySelectorAll?.("[data-openmmi-trip-b], [data-openmmi-trip-b-unit]"), active !== "b");

    if (button) {
      const action = `Show Trip ${next.toUpperCase()}`;
      button.setAttribute?.("aria-label", action);
      button.setAttribute?.("title", action);
    }
  }

  function install(options = {}) {
    const windowRef = options.window || root;
    const documentRef = options.document || windowRef?.document;
    const preferences = options.preferences || windowRef?.openMmiPreferences;
    if (!documentRef) {
      return Object.freeze({
        activeTrip: () => "a",
        render() {},
        setActiveTrip() { return "a"; },
        toggle() { return "b"; },
      });
    }

    let active = readActiveTrip(preferences);

    function render() {
      Array.from(documentRef.querySelectorAll?.("[data-openmmi-trip-card]") || [])
        .forEach((card) => renderCard(card, active));
      return active;
    }

    function setActiveTrip(value, persist = true) {
      active = normaliseTrip(value);
      if (persist) writeActiveTrip(preferences, active);
      render();
      return active;
    }

    function toggle() {
      return setActiveTrip(active === "a" ? "b" : "a");
    }

    documentRef.addEventListener?.("click", (event) => {
      const button = event.target?.closest?.("[data-openmmi-trip-next]");
      if (!button) return;
      event.preventDefault?.();
      toggle();
    });

    windowRef?.addEventListener?.("storage", (event) => {
      if (event?.key !== STORAGE_KEY) return;
      let next = "a";
      try { next = event.newValue ? JSON.parse(event.newValue) : "a"; } catch (_) { next = "a"; }
      setActiveTrip(next, false);
    });

    render();

    return Object.freeze({
      activeTrip: () => active,
      render,
      setActiveTrip,
      toggle,
    });
  }

  return Object.freeze({
    STORAGE_KEY,
    install,
    normaliseTrip,
    readActiveTrip,
    renderCard,
    writeActiveTrip,
  });
});
