(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(root);
  else root.openMmiTripDistance = factory(root);
})(typeof globalThis !== "undefined" ? globalThis : this, function createTripDistanceModule(root) {
  "use strict";

  const ENDPOINT = "/api/system/trip-distance";
  const OBSERVE_ENDPOINT = "/api/system/trip-distance/observe";
  const CHECKPOINT_INTERVAL_MS = 30 * 1000;
  const MAX_SAMPLE_GAP_MS = 5 * 1000;
  const MAX_SPEED_KMH = 350;

  function finite(value) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function distanceDeltaKm(previousSpeedKmh, currentSpeedKmh, elapsedMs) {
    const previous = finite(previousSpeedKmh);
    const current = finite(currentSpeedKmh);
    const elapsed = finite(elapsedMs);
    if (previous === null || current === null || elapsed === null || elapsed <= 0 || elapsed > MAX_SAMPLE_GAP_MS) return 0;
    if (previous < 0 || current < 0 || previous > MAX_SPEED_KMH || current > MAX_SPEED_KMH) return 0;
    return ((previous + current) / 2) * (elapsed / 3_600_000);
  }

  function install(options = {}) {
    const windowRef = options.window || root;
    const api = options.api || windowRef?.openMmiApi;
    const now = options.now || (() => Date.now());
    if (!api) {
      return Object.freeze({ update() {}, refresh: async () => null, checkpoint: async () => null, currentTotalKm: () => null, snapshot: () => null });
    }

    let snapshot = null;
    let pendingKm = 0;
    let pendingElapsedSeconds = 0;
    let inFlightKm = 0;
    let inFlightElapsedSeconds = 0;
    let currentOdometerKm = null;
    let lastSampleAt = null;
    let lastSpeedKmh = null;
    let lastCheckpointAt = 0;
    let checkpointing = false;

    function baseTotalKm() { return finite(snapshot?.total_km); }

    function currentTotalKm() {
      const base = baseTotalKm();
      return base === null ? null : base + pendingKm + inFlightKm;
    }

    async function refresh() {
      snapshot = await api.getJson(ENDPOINT, { usePayloadError: true });
      return snapshot;
    }

    async function checkpoint(force = false) {
      if (checkpointing || !snapshot || currentOdometerKm === null) return snapshot;
      const currentTime = now();
      const savedOdometerKm = finite(snapshot?.odometer_km);
      const needsAnchor = savedOdometerKm === null;
      const odometerChanged = savedOdometerKm !== null && Math.abs(currentOdometerKm - savedOdometerKm) >= 0.5;
      if (!force && !needsAnchor && !odometerChanged && currentTime - lastCheckpointAt < CHECKPOINT_INTERVAL_MS) return snapshot;
      if (!needsAnchor && !odometerChanged && pendingElapsedSeconds <= 0 && pendingKm <= 0) {
        lastCheckpointAt = currentTime;
        return snapshot;
      }

      const capturedKm = pendingKm;
      const capturedElapsed = pendingElapsedSeconds;
      pendingKm = 0;
      pendingElapsedSeconds = 0;
      inFlightKm = capturedKm;
      inFlightElapsedSeconds = capturedElapsed;
      checkpointing = true;
      lastCheckpointAt = currentTime;
      try {
        snapshot = await api.postJson(OBSERVE_ENDPOINT, {
          distance_delta_km: capturedKm,
          elapsed_seconds: capturedElapsed,
          odometer_km: currentOdometerKm,
        }, { usePayloadError: true });
        inFlightKm = 0;
        inFlightElapsedSeconds = 0;
        return snapshot;
      } catch (error) {
        pendingKm += inFlightKm;
        pendingElapsedSeconds += inFlightElapsedSeconds;
        inFlightKm = 0;
        inFlightElapsedSeconds = 0;
        throw error;
      } finally {
        checkpointing = false;
      }
    }

    function update(payload = {}) {
      const currentTime = now();
      const vehicle = payload?.state?.vehicle || {};
      const health = payload?.health || {};
      const healthAllowsMovement = health.stale !== true && !["error", "stale", "waiting"].includes(health.status);
      const present = vehicle.present === true && healthAllowsMovement;
      const speedKmh = finite(vehicle.speed_kmh);
      currentOdometerKm = finite(vehicle.odometer_km);

      const payloadTimestamp = finite(payload?.updated_at);
      const sampleAt = payloadTimestamp === null
        ? currentTime
        : payloadTimestamp > 1_000_000_000_000 ? payloadTimestamp : payloadTimestamp * 1000;

      if (lastSampleAt !== null && sampleAt > lastSampleAt && present) {
        const elapsedMs = sampleAt - lastSampleAt;
        const delta = distanceDeltaKm(lastSpeedKmh, speedKmh, elapsedMs);
        if (delta > 0) {
          pendingKm += delta;
          pendingElapsedSeconds += elapsedMs / 1000;
        } else if (elapsedMs <= MAX_SAMPLE_GAP_MS && speedKmh !== null && speedKmh === 0 && lastSpeedKmh === 0) {
          pendingElapsedSeconds += elapsedMs / 1000;
        }
      }

      if (lastSampleAt === null || sampleAt > lastSampleAt) {
        lastSampleAt = sampleAt;
        lastSpeedKmh = present ? speedKmh : null;
      }
      if (snapshot && currentOdometerKm !== null) void checkpoint(false).catch(() => {});
      return currentTotalKm();
    }

    void refresh().then(() => {
      if (currentOdometerKm !== null) void checkpoint(true).catch(() => {});
    }).catch(() => {});

    return Object.freeze({
      update,
      refresh,
      checkpoint,
      currentTotalKm,
      snapshot: () => snapshot,
      pendingKm: () => pendingKm + inFlightKm,
    });
  }

  return Object.freeze({
    ENDPOINT,
    OBSERVE_ENDPOINT,
    CHECKPOINT_INTERVAL_MS,
    MAX_SAMPLE_GAP_MS,
    MAX_SPEED_KMH,
    distanceDeltaKm,
    install,
  });
});
