"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const reminder = require("../../ui/web_dashboard/static/service-reminder.js");

function snapshot(overrides = {}) {
  return {
    configured: true,
    settings: {
      enabled: true,
      distance_interval_km: 16093.44,
      time_interval_months: 12,
      warning_distance_km: 1609.344,
      warning_days: 30,
      ...(overrides.settings || {}),
    },
    reset: { reset_date: "2026-01-01", odometer_km: 100000, ...(overrides.reset || {}) },
    next_due: { date: "2027-01-01", odometer_km: 116093.44, ...(overrides.next_due || {}) },
    ...overrides,
  };
}

test("service reminder is due when either time or distance reaches zero", () => {
  const byDistance = reminder.evaluate(snapshot(), 116094, new Date("2026-06-01T12:00:00"));
  assert.equal(byDistance.state, "due");
  assert.ok(byDistance.distanceRemainingKm < 0);

  const byTime = reminder.evaluate(snapshot(), 101000, new Date("2027-01-01T12:00:00"));
  assert.equal(byTime.state, "due");
  assert.equal(byTime.daysRemaining, 0);
});

test("service reminder enters due-soon state at either advance threshold", () => {
  const byDistance = reminder.evaluate(snapshot(), 114500, new Date("2026-06-01T12:00:00"));
  assert.equal(byDistance.state, "soon");

  const byTime = reminder.evaluate(snapshot(), 110000, new Date("2026-12-15T12:00:00"));
  assert.equal(byTime.state, "soon");
  assert.equal(byTime.daysRemaining, 17);
});

test("service reminder handles setup, disabled and odometer rollback states", () => {
  assert.equal(reminder.evaluate(snapshot(), null, new Date("2026-06-01T12:00:00")).state, "waiting");
  assert.equal(reminder.evaluate({ configured: false, settings: { enabled: true } }, 100, new Date()).state, "setup");
  assert.equal(reminder.evaluate(snapshot({ settings: { enabled: false } }), 100, new Date()).state, "disabled");
  assert.equal(reminder.evaluate(snapshot(), 99990, new Date("2026-06-01T12:00:00")).state, "invalid-odometer");
});

test("service interval distance conversion follows the existing speed unit", () => {
  assert.equal(reminder.distanceForDisplay(1609.344, { speedUnit: "mph" }), 1000);
  assert.equal(reminder.distanceFromDisplay(1000, { speedUnit: "mph" }), 1609.344);
  assert.equal(reminder.distanceForDisplay(1609.344, { speedUnit: "kmh" }), 1609.344);
  assert.equal(reminder.formatDistance(1609.344, { speedUnit: "mph" }), "1,000 mi");
});

test("service summary uses MIB-style remaining distance or time", () => {
  const evaluated = reminder.evaluate(snapshot(), 110000, new Date("2026-06-01T12:00:00"));
  assert.deepEqual(reminder.statusSummary(evaluated, { speedUnit: "mph" }), {
    label: "Next inspection",
    detail: "3,786 mi or 214 days",
  });
});

test("live odometer updates preserve the service form DOM and use standard settings buttons", async () => {
  let htmlWrites = 0;
  const summaryLabel = { textContent: "" };
  const summaryDetail = { textContent: "" };
  const summaryNode = {
    className: "",
    querySelector(selector) {
      if (selector === "[data-openmmi-service-summary-label]") return summaryLabel;
      if (selector === "[data-openmmi-service-summary-detail]") return summaryDetail;
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
      if (selector === "[data-openmmi-service-summary]") return summaryNode;
      if (selector === "[data-openmmi-service-current-odometer]") return currentOdometer;
      if (selector === "[data-openmmi-service-reset]") return resetButton;
      return null;
    },
  };
  const documentRef = {
    querySelector(selector) {
      if (selector === '[data-openmmi-service-reminder-panel="true"]') return host;
      return null;
    },
    addEventListener() {},
  };
  const windowRef = {
    document: documentRef,
    addEventListener() {},
    FormData: class {},
    confirm: () => true,
  };
  const controller = reminder.install({
    window: windowRef,
    document: documentRef,
    api: {
      async getJson() { return snapshot(); },
      async postJson() { return snapshot(); },
    },
    preferences: { readDashboardSettings: () => ({ speedUnit: "mph" }) },
  });

  await new Promise((resolve) => setImmediate(resolve));
  const writesAfterInitialRender = htmlWrites;
  assert.ok(writesAfterInitialRender >= 1);
  assert.match(host.innerHTML, /openmmi-setting-pill is-selected" data-openmmi-service-save/);
  assert.match(host.innerHTML, /openmmi-setting-pill" data-openmmi-service-reset/);

  controller.update({ state: { vehicle: { odometer_km: 110000 } } });

  assert.equal(htmlWrites, writesAfterInitialRender, "live status updates must not replace focused form controls");
  assert.equal(currentOdometer.textContent, "68,351 mi");
  assert.equal(resetButton.disabled, false);
  assert.equal(summaryLabel.textContent, "Next inspection");
});
