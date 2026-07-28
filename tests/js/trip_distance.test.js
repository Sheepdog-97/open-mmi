"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const tripDistance = require("../../ui/web_dashboard/static/trip-distance.js");

function close(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= epsilon, `${actual} was not within ${epsilon} of ${expected}`);
}

test("distance integration uses the trapezoidal speed average", () => {
  close(tripDistance.distanceDeltaKm(115.872768, 115.872768, 5_000), 0.1609344);
  assert.equal(tripDistance.distanceDeltaKm(60, 60, tripDistance.MAX_SAMPLE_GAP_MS + 1), 0);
  assert.equal(tripDistance.distanceDeltaKm(400, 400, 1000), 0);
});

test("controller exposes fractional movement before and after a checkpoint", async () => {
  let currentTime = 0;
  const posts = [];
  const controller = tripDistance.install({
    now: () => currentTime,
    api: {
      async getJson() {
        return { ok: true, total_km: 10, odometer_km: 1000, updated_at: "2026-07-27T10:00:00+00:00" };
      },
      async postJson(_path, payload) {
        posts.push(payload);
        return {
          ok: true,
          total_km: 10 + payload.distance_delta_km,
          odometer_km: payload.odometer_km,
          updated_at: "2026-07-27T10:00:30+00:00",
        };
      },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));

  controller.update({ state: { vehicle: { present: true, speed_kmh: 57.936384, odometer_km: 1000 } } });
  for (let second = 1; second <= 10; second += 1) {
    currentTime = second * 1000;
    controller.update({ state: { vehicle: { present: true, speed_kmh: 57.936384, odometer_km: 1000 } } });
  }
  close(controller.currentTotalKm(), 10.1609344);

  currentTime = 30_000;
  controller.update({ state: { vehicle: { present: true, speed_kmh: 0, odometer_km: 1000 } } });
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(posts.length, 1);
  close(posts[0].distance_delta_km, 0.1609344);
  close(controller.currentTotalKm(), 10.1609344);
});

test("duplicate or stale status snapshots do not add distance", async () => {
  let currentTime = 0;
  const controller = tripDistance.install({
    now: () => currentTime,
    api: {
      async getJson() { return { ok: true, total_km: 0, odometer_km: 1000, updated_at: "2026-07-27T10:00:00+00:00" }; },
      async postJson(_path, payload) { return { ok: true, total_km: payload.distance_delta_km, odometer_km: payload.odometer_km }; },
    },
  });
  await new Promise((resolve) => setImmediate(resolve));
  const live = (updatedAt) => ({ updated_at: updatedAt, health: { status: "live", stale: false }, state: { vehicle: { present: true, speed_kmh: 100, odometer_km: 1000 } } });
  controller.update(live(1000));
  currentTime = 1000;
  controller.update(live(1000));
  assert.equal(controller.currentTotalKm(), 0);
  currentTime = 2000;
  controller.update({ ...live(1001), health: { status: "stale", stale: true } });
  assert.equal(controller.currentTotalKm(), 0);
});
