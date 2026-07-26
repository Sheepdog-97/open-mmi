"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const trip = require("../../ui/web_dashboard/static/trip-a.js");

function snapshot(overrides = {}) {
  return {
    configured: true,
    reset: { reset_at: "2026-07-26T20:30:00+00:00", odometer_km: 100000, ...(overrides.reset || {}) },
    ...overrides,
  };
}

test("Trip A subtracts the saved odometer from the live odometer", () => {
  const result = trip.evaluate(snapshot(), 100123);
  assert.equal(result.state, "ok");
  assert.equal(result.tripKm, 123);
});

test("Trip A handles setup, waiting and odometer rollback states", () => {
  assert.equal(trip.evaluate({ configured: false, reset: {} }, 100).state, "setup");
  assert.equal(trip.evaluate(snapshot(), null).state, "waiting");
  assert.equal(trip.evaluate(snapshot(), 99990).state, "invalid-odometer");
});

test("Trip A display follows the existing speed unit", () => {
  assert.equal(trip.distanceForDisplay(1609.344, { speedUnit: "mph" }), 1000);
  assert.equal(trip.formatDistance(1609.344, { speedUnit: "mph" }), "1,000");
  assert.equal(trip.formatDistanceWithUnit(1609.344, { speedUnit: "mph" }), "1,000 mi");
  assert.equal(trip.formatDistanceWithUnit(1609.344, { speedUnit: "kmh" }), "1,609 km");
});

test("live odometer updates refresh Trip A without rebuilding the settings panel", async () => {
  let htmlWrites = 0;
  const dashboardValue = { textContent: "" };
  const dashboardUnit = { textContent: "" };
  const summaryLabel = { textContent: "" };
  const summaryDetail = { textContent: "" };
  const summaryNode = {
    className: "",
    querySelector(selector) {
      if (selector === "[data-openmmi-trip-a-summary-label]") return summaryLabel;
      if (selector === "[data-openmmi-trip-a-summary-detail]") return summaryDetail;
      return null;
    },
  };
  const currentOdometer = { textContent: "" };
  const resetButton = { disabled: true };
  const host = {
    _html: "",
    set innerHTML(value) { this._html = String(value); htmlWrites += 1; },
    get innerHTML() { return this._html; },
    querySelector(selector) {
      if (selector === "[data-openmmi-trip-a-summary]") return summaryNode;
      if (selector === "[data-openmmi-trip-a-current-odometer]") return currentOdometer;
      if (selector === "[data-openmmi-trip-a-reset]") return resetButton;
      return null;
    },
  };
  const documentRef = {
    querySelector(selector) {
      if (selector === '[data-openmmi-trip-a-panel="true"]') return host;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === "[data-openmmi-trip-a]") return [dashboardValue];
      if (selector === "[data-openmmi-trip-a-unit]") return [dashboardUnit];
      return [];
    },
    addEventListener() {},
  };
  const windowRef = {
    document: documentRef,
    addEventListener() {},
    confirm: () => true,
  };
  const controller = trip.install({
    window: windowRef,
    document: documentRef,
    api: {
      async getJson() { return snapshot(); },
      async postJson() { return snapshot({ reset: { odometer_km: 100123 } }); },
    },
    preferences: { readDashboardSettings: () => ({ speedUnit: "mph" }) },
  });

  await new Promise((resolve) => setImmediate(resolve));
  const writesAfterInitialRender = htmlWrites;
  controller.update({ state: { vehicle: { odometer_km: 100123 } } });

  assert.equal(htmlWrites, writesAfterInitialRender);
  assert.equal(dashboardValue.textContent, "76");
  assert.equal(dashboardUnit.textContent, "mi");
  assert.equal(currentOdometer.textContent, "62,214 mi");
  assert.equal(summaryLabel.textContent, "Trip A");
  assert.equal(summaryDetail.textContent, "76 mi");
  assert.equal(resetButton.disabled, false);
});
