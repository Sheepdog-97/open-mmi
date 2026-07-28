"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const trip = require("../../ui/web_dashboard/static/trip-b.js");

function snapshot(overrides = {}) {
  return { ...overrides, configured: overrides.configured ?? true, reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: 100000, ...(overrides.reset || {}) } };
}

test("Trip B is a separate long-term odometer counter", () => {
  const result = trip.evaluate(snapshot(), 101609.344);
  assert.equal(result.state, "ok");
  assert.equal(Math.round(result.tripKm), 1609);
  assert.equal(trip.formatTripDistanceWithUnit(result.tripKm, { speedUnit: "mph" }), "1,000.0 mi");
});

test("Trip B prefers the high-resolution distance accumulator", () => {
  const result = trip.evaluate(snapshot({ reset: { distance_total_km: 200 } }), 100000, 200.1609344);
  assert.equal(result.precise, true);
  assert.equal(trip.formatTripDistanceWithUnit(result.tripKm, { speedUnit: "mph" }), "0.1 mi");
});

test("Trip B handles setup and odometer rollback", () => {
  assert.equal(trip.evaluate({ configured: false, reset: {} }, 100).state, "setup");
  assert.equal(trip.evaluate(snapshot(), 99990).state, "invalid-odometer");
});
