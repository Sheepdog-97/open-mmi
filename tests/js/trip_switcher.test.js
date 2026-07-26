"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const switcher = require("../../ui/web_dashboard/static/trip-switcher.js");

function node() {
  return {
    textContent: "",
    hidden: false,
    attributes: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
  };
}

function card() {
  const label = node();
  const button = node();
  const tripA = node();
  const unitA = node();
  const tripB = node();
  const unitB = node();
  const map = {
    "[data-openmmi-trip-label]": label,
    "[data-openmmi-trip-next]": button,
  };
  return {
    dataset: {},
    label,
    button,
    tripA,
    unitA,
    tripB,
    unitB,
    querySelector(selector) { return map[selector] || null; },
    querySelectorAll(selector) {
      if (selector === "[data-openmmi-trip-a], [data-openmmi-trip-a-unit]") return [tripA, unitA];
      if (selector === "[data-openmmi-trip-b], [data-openmmi-trip-b-unit]") return [tripB, unitB];
      return [];
    },
  };
}

function harness(saved = null) {
  const cards = [card(), card()];
  const listeners = new Map();
  const writes = [];
  const document = {
    querySelectorAll(selector) { return selector === "[data-openmmi-trip-card]" ? cards : []; },
    addEventListener(type, callback) { listeners.set(type, callback); },
  };
  const window = { document, addEventListener() {} };
  const preferences = {
    readJson(_key, fallback) { return saved === null ? fallback : saved; },
    writeJson(key, value) { writes.push([key, value]); return true; },
  };
  const controller = switcher.install({ window, document, preferences });
  return { cards, controller, listeners, writes };
}

test("Trip A is the default and both cards switch together", () => {
  const { cards, controller, listeners, writes } = harness();
  for (const item of cards) {
    assert.equal(item.label.textContent, "Trip A");
    assert.equal(item.tripA.hidden, false);
    assert.equal(item.unitA.hidden, false);
    assert.equal(item.tripB.hidden, true);
    assert.equal(item.unitB.hidden, true);
    assert.equal(item.button.attributes["aria-label"], "Show Trip B");
  }

  let prevented = false;
  listeners.get("click")({
    target: { closest: (selector) => selector === "[data-openmmi-trip-next]" ? cards[0].button : null },
    preventDefault() { prevented = true; },
  });

  assert.equal(prevented, true);
  assert.equal(controller.activeTrip(), "b");
  assert.deepEqual(writes, [[switcher.STORAGE_KEY, "b"]]);
  for (const item of cards) {
    assert.equal(item.label.textContent, "Trip B");
    assert.equal(item.tripA.hidden, true);
    assert.equal(item.unitA.hidden, true);
    assert.equal(item.tripB.hidden, false);
    assert.equal(item.unitB.hidden, false);
    assert.equal(item.button.attributes.title, "Show Trip A");
  }
});

test("the saved Trip B choice is restored", () => {
  const { cards, controller } = harness("b");
  assert.equal(controller.activeTrip(), "b");
  assert.equal(cards[0].label.textContent, "Trip B");
  assert.equal(cards[0].tripB.hidden, false);
});

test("invalid saved values fall back to Trip A", () => {
  assert.equal(switcher.normaliseTrip("anything"), "a");
  assert.equal(switcher.normaliseTrip("B"), "b");
});
