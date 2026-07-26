"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class ClassList {
  constructor() { this.values = new Set(); }
  add(...names) { names.forEach((name) => this.values.add(name)); }
  remove(...names) { names.forEach((name) => this.values.delete(name)); }
  contains(name) { return this.values.has(name); }
  toggle(name, force) {
    const add = force === undefined ? !this.values.has(name) : Boolean(force);
    if (add) this.values.add(name); else this.values.delete(name);
    return add;
  }
}
class Style {
  setProperty(name, value) { this[name] = String(value); }
  removeProperty(name) { delete this[name]; }
}
class Element {
  constructor(tagName = "div", ownerDocument = null) {
    this.tagName = String(tagName).toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.parentNode = null;
    this.classList = new ClassList();
    this.style = new Style();
    this.dataset = {};
    this.attributes = {};
    this.textContent = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.paused = true;
    this.currentTime = 0;
    this.duration = NaN;
    this.readyState = 0;
    this.volume = 1;
    this.id = "";
  }
  set className(value) {
    this.classList = new ClassList();
    String(value || "").split(/\s+/).filter(Boolean).forEach((name) => this.classList.add(name));
  }
  get className() { return [...this.classList.values].join(" "); }
  set innerHTML(value) { this._innerHTML = String(value); }
  get innerHTML() { return this._innerHTML || ""; }
  appendChild(child) { child.parentNode = this; this.children.push(child); return child; }
  prepend(child) { child.parentNode = this; this.children.unshift(child); return child; }
  insertBefore(child, before) {
    child.parentNode = this;
    const index = before ? this.children.indexOf(before) : -1;
    if (index >= 0) this.children.splice(index, 0, child); else this.children.push(child);
    return child;
  }
  replaceChildren(...children) { this.children = []; children.forEach((child) => this.appendChild(child)); }
  remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((child) => child !== this); this.parentNode = null; }
  setAttribute(name, value) { this.attributes[name] = String(value); if (name === "id") this.id = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
  removeEventListener() {}
  dispatchEvent() { return true; }
  click() {}
  load() {}
  play() { this.paused = false; return Promise.resolve(); }
  pause() { this.paused = true; }
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, right: 800, bottom: 480, width: 800, height: 480 }; }
  getClientRects() { return [this.getBoundingClientRect()]; }
  closest(selector) {
    if (selector?.startsWith("#") && this.id === selector.slice(1)) return this;
    return this.parentNode?.closest?.(selector) || null;
  }
  matches(selector) {
    if (selector?.startsWith("#")) return this.id === selector.slice(1);
    if (selector?.startsWith(".")) return this.classList.contains(selector.slice(1));
    return false;
  }
  contains(node) { if (node === this) return true; return this.children.some((child) => child.contains?.(node)); }
  querySelector(selector) { return this.ownerDocument?._query(selector, this) || null; }
  querySelectorAll(selector) { return this.ownerDocument?._queryAll(selector, this) || []; }
}
class Document {
  constructor() {
    this.readyState = "loading";
    this.visibilityState = "visible";
    this.body = new Element("body", this);
    this.documentElement = new Element("html", this);
    this.documentElement.appendChild(this.body);
    this.listeners = new Map();
    this._all = [this.documentElement, this.body];
    this._seed();
  }
  _add(tag, id, className = "") {
    const element = new Element(tag, this); element.id = id || ""; element.className = className;
    this.body.appendChild(element); this._all.push(element); return element;
  }
  _seed() {
    this._add("main", "", "screen");
    this._add("section", "pageDrive", "page active");
    this._add("section", "pageClimate", "page");
    this._add("section", "pageVehicle", "page");
    this._add("section", "pageElectrical", "page");
    this._add("footer", "", "status-strip");
    this._add("h1", "pageTitle", "");
  }
  createElement(tag) { const e = new Element(tag, this); this._all.push(e); return e; }
  addEventListener(type, callback) { if (!this.listeners.has(type)) this.listeners.set(type, []); this.listeners.get(type).push(callback); }
  removeEventListener() {}
  dispatchEvent(event) { for (const cb of this.listeners.get(event.type) || []) cb(event); return true; }
  _descendants(root) { const out=[]; const visit=(node)=>{ for (const child of node.children || []) { out.push(child); visit(child); } }; visit(root); return out; }
  _queryAll(selector, root = this.documentElement) {
    const nodes = [root, ...this._descendants(root)];
    if (selector === ".page") return nodes.filter((node) => node.classList?.contains("page"));
    if (selector.includes(",")) {
      const out=[]; for (const part of selector.split(",")) for (const n of this._queryAll(part.trim(), root)) if (!out.includes(n)) out.push(n); return out;
    }
    if (selector.startsWith("#")) {
      const id = selector.slice(1).split(/[ .:#\[]/,1)[0];
      return nodes.filter((node) => node.id === id);
    }
    if (selector.startsWith(".")) return nodes.filter((node) => node.classList?.contains(selector.slice(1).split(/[ :#\[]/,1)[0]));
    return [];
  }
  _query(selector, root = this.documentElement) { return this._queryAll(selector, root)[0] || null; }
  querySelector(selector) { return this._query(selector); }
  querySelectorAll(selector) { return this._queryAll(selector); }
}

test("dashboard scripts initialise extracted Jellyfin and Bluetooth controllers", async () => {
  const document = new Document();
  const local = new Map();
  let intervalCount = 0;
  const windowListeners = new Map();
  const window = {
    document,
    innerHeight: 480,
    innerWidth: 800,
    devicePixelRatio: 1,
    location: { origin: "http://localhost", href: "http://localhost/" },
    navigator: { language: "en-GB" },
    localStorage: {
      getItem(key) { return local.has(key) ? local.get(key) : null; },
      setItem(key, value) { local.set(key, String(value)); },
      removeItem(key) { local.delete(key); },
    },
    performance: { now: () => 1000, getEntriesByType: () => [], mark() {}, measure() {}, clearMarks() {}, clearMeasures() {} },
    MutationObserver: class { observe() {} disconnect() {} },
    CustomEvent: class { constructor(type, init = {}) { this.type = type; this.detail = init.detail; } },
    Event: class { constructor(type) { this.type = type; } },
    FormData: class { constructor() {} get() { return null; } },
    confirm: () => true,
    getComputedStyle: () => ({ display: "block", visibility: "visible", getPropertyValue: () => "" }),
    addEventListener(name, callback) {
      if (!windowListeners.has(name)) windowListeners.set(name, []);
      windowListeners.get(name).push(callback);
    },
    removeEventListener(name, callback) {
      const listeners = windowListeners.get(name) || [];
      windowListeners.set(name, listeners.filter((entry) => entry !== callback));
    },
    dispatchEvent(event) {
      for (const callback of windowListeners.get(event.type) || []) callback(event);
      return true;
    },
    requestAnimationFrame() { return 1; },
    cancelAnimationFrame() {},
    setTimeout() { return 1; },
    clearTimeout() {},
    setInterval() { intervalCount += 1; return intervalCount; },
    clearInterval() {},
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ health: { status: "ok" }, state: {} }) }),
  };
  window.window = window;
  window.globalThis = window;
  window.self = window;
  const context = vm.createContext({
    ...window,
    window,
    self: window,
    globalThis: window,
    document,
    navigator: window.navigator,
    localStorage: window.localStorage,
    performance: window.performance,
    MutationObserver: window.MutationObserver,
    CustomEvent: window.CustomEvent,
    Event: window.Event,
    FormData: window.FormData,
    requestAnimationFrame: window.requestAnimationFrame,
    cancelAnimationFrame: window.cancelAnimationFrame,
    setTimeout: window.setTimeout,
    clearTimeout: window.clearTimeout,
    setInterval: window.setInterval,
    clearInterval: window.clearInterval,
    fetch: window.fetch,
    console,
    URL,
    URLSearchParams,
    Blob: class {},
    structuredClone: global.structuredClone,
  });

  const staticDir = path.resolve("ui/web_dashboard/static");
  const scripts = [
    "api.js", "dashboard-connection.js", "preferences.js", "system-settings.js", "vehicle-setup-settings.js", "runtime-diagnostics.js", "service-reminder.js", "trip-a.js", "trip-b.js", "trip-switcher.js", "clock.js", "status.js", "navigation.js", "overlays.js", "vehicle.js",
    "media.js", "jellyfin-reconnection.js", "media-jellyfin.js", "media-radio.js", "media-usb.js", "media-bluetooth.js", "app.js",
  ];
  for (const name of scripts) {
    try {
      vm.runInContext(fs.readFileSync(path.join(staticDir, name), "utf8"), context, { filename: name });
    } catch (error) {
      throw new Error(`Failed to execute ${name}: ${error.stack || error}`);
    }
  }
  assert.equal(window.openMmiDashboardConnection !== undefined, true);
  assert.equal(window.openMmiDashboardConnectionController !== undefined, true);
  assert.equal(window.openMmiClock !== undefined, true);
  assert.equal(window.openMmiTripA !== undefined, true);
  assert.equal(window.openMmiTripAController !== undefined, true);
  assert.equal(window.openMmiTripB !== undefined, true);
  assert.equal(window.openMmiTripBController !== undefined, true);
  assert.equal(window.openMmiTripSwitcher !== undefined, true);
  assert.equal(window.openMmiTripSwitcherController !== undefined, true);
  assert.equal(window.openMmiJellyfinReconnect !== undefined, true);
  assert.equal(window.openMmiJellyfinMedia !== undefined, true);
  assert.equal(window.openMmiJellyfinPlayer !== undefined, true);
  assert.equal(window.openMmiBluetoothMediaController !== undefined, true);
  assert.equal(window.openMmiBluetoothMedia !== undefined, true);
  assert.deepEqual(
    Object.keys(window.openMmiMediaAdapters?.adapters || {}).sort(),
    ["bluetooth", "jellyfin", "radio", "usb"],
  );
  assert.equal(typeof window.ommiMediaLoadLibrary, "function");
  assert.equal(window.openMmiJellyfinPlayer.state, window.openMmiMedia);
  assert.equal(intervalCount, 1, "only the shared status poller should retain a permanent interval");
  await new Promise((resolve) => setImmediate(resolve));
});
