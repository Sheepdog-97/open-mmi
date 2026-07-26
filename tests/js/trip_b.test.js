"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const trip = require("../../ui/web_dashboard/static/trip-b.js");

function snapshot(overrides = {}) {
  return { configured: true, reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: 100000, ...(overrides.reset || {}) }, ...overrides };
}

test("Trip B is a separate long-term odometer counter", () => {
  const result = trip.evaluate(snapshot(), 101609.344);
  assert.equal(result.state, "ok");
  assert.equal(Math.round(result.tripKm), 1609);
  assert.equal(trip.formatDistanceWithUnit(result.tripKm, { speedUnit: "mph" }), "1,000 mi");
});

test("Trip B handles setup and odometer rollback", () => {
  assert.equal(trip.evaluate({ configured: false, reset: {} }, 100).state, "setup");
  assert.equal(trip.evaluate(snapshot(), 99990).state, "invalid-odometer");
});
