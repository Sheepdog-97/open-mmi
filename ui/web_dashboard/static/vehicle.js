(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory(root);
  } else {
    root.openMmiVehicle = factory(root);
  }
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  "use strict";

  const DOORS = Object.freeze(["front_left", "front_right", "rear_left", "rear_right", "boot", "bonnet"]);
  const DEFAULT_SETTINGS = Object.freeze({ speedUnit: "mph", tempUnit: "c" });

  function formatNumber(value, decimals = 0) {
    if (value === null || value === undefined || value === "-") return "--";
    const number = Number(value);
    if (!Number.isFinite(number)) return String(value);
    return number.toLocaleString(undefined, {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    });
  }

  function normaliseSettings(settings = {}) {
    return Object.assign({}, DEFAULT_SETTINGS, settings || {});
  }

  function speedUnitLabel(settings = DEFAULT_SETTINGS) {
    return normaliseSettings(settings).speedUnit === "kmh" ? "km/h" : "mph";
  }

  function distanceUnitLabel(settings = DEFAULT_SETTINGS) {
    return normaliseSettings(settings).speedUnit === "kmh" ? "km" : "mi";
  }

  function tempUnitLabel(settings = DEFAULT_SETTINGS) {
    return normaliseSettings(settings).tempUnit === "f" ? "°F" : "°C";
  }

  function formatSpeedFromKmh(kilometres, decimals = 0, settings = DEFAULT_SETTINGS) {
    const number = Number(kilometres);
    if (!Number.isFinite(number)) return "--";
    return normaliseSettings(settings).speedUnit === "kmh"
      ? formatNumber(number, decimals)
      : formatNumber(number * 0.621371192, decimals);
  }

  function formatDistanceFromKm(kilometres, decimals = 0, settings = DEFAULT_SETTINGS) {
    return formatSpeedFromKmh(kilometres, decimals, settings);
  }

  function formatTempFromC(celsius, decimals = 0, settings = DEFAULT_SETTINGS) {
    const number = Number(celsius);
    if (!Number.isFinite(number)) return "--";
    const value = normaliseSettings(settings).tempUnit === "f" ? ((number * 9) / 5) + 32 : number;
    return formatNumber(value, decimals);
  }

  function booleanText(value) {
    if (value === true) return "ON";
    if (value === false) return "OFF";
    return "--";
  }

  function booleanYesNoText(value) {
    if (value === true) return "Yes";
    if (value === false) return "No";
    return "--";
  }

  function indicatorLabel(lighting = {}) {
    if (lighting.hazards === true) return "Hazards";
    if (lighting.left_indicator === true && lighting.right_indicator === true) return "Both";
    if (lighting.left_indicator === true) return "Left";
    if (lighting.right_indicator === true) return "Right";
    if (lighting.left_indicator === false || lighting.right_indicator === false) return "Off";
    return "--";
  }

  function lightsLabel(value) {
    return booleanText(value);
  }

  function buildViewModel(payload = {}, settings = DEFAULT_SETTINGS) {
    const resolvedSettings = normaliseSettings(settings);
    const health = payload.health || {};
    const state = payload.state || {};
    const vehicle = state.vehicle || {};
    const engine = state.engine || {};
    const electrical = state.electrical || {};
    const fuel = state.fuel || {};
    const climate = state.climate || {};
    const lighting = state.lighting || {};
    const doors = state.doors || {};

    return {
      health: {
        status: health.status || "waiting",
        ageText: typeof health.age_seconds === "number" ? `${health.age_seconds.toFixed(1)}s ago` : "--",
      },
      fields: {
        speed_mph: formatSpeedFromKmh(vehicle.speed_kmh, 0, resolvedSettings),
        rpm: formatNumber(engine.speed_rpm, 0),
        odo_mi: formatDistanceFromKm(vehicle.odometer_km, 0, resolvedSettings),
        fuel_l: formatNumber(fuel.level_l, 0),
        coolant_c: formatTempFromC(engine.coolant_temp_c, 0, resolvedSettings),
        outside_reg_c: formatTempFromC(climate.outside_temp_regulation_c, 1, resolvedSettings),
        outside_unfiltered_c: formatTempFromC(climate.outside_temp_unfiltered_c, 1, resolvedSettings),
        voltage_v: formatNumber(electrical.supply_voltage_v ?? electrical.terminal30_voltage_v, 1),
        blower_pct: formatNumber(climate.blower_load_percent, 1),
        dimmer_pct: formatNumber(lighting.dimmer_percent ?? lighting.dimmer_percent_mirror, 0),
        lighting_mode: lighting.mode || "--",
        lights_on: lightsLabel(lighting.lights_on),
        indicators: indicatorLabel(lighting),
      },
      booleans: {
        handbrake: vehicle.handbrake,
        reverse: vehicle.reverse,
        rear_heater: climate.rear_window_heater_requested,
        recirculation: climate.recirculation_active ?? climate.front_demist_air_request,
        compressor: climate.compressor_active,
        hazards: lighting.hazards,
        bulb_out: lighting.bulb_out,
      },
      doors: Object.fromEntries(DOORS.map((name) => [name, doors[name]])),
      anyDoorOpen: doors.any_open === true,
      tachRpm: engine.speed_rpm,
      fuelLowWarning: fuel.low_level_warning,
      units: {
        speed_mph: speedUnitLabel(resolvedSettings),
        odo_mi: distanceUnitLabel(resolvedSettings),
        fuel_l: "L",
        coolant_c: tempUnitLabel(resolvedSettings),
        outside_reg_c: tempUnitLabel(resolvedSettings),
        outside_unfiltered_c: tempUnitLabel(resolvedSettings),
      },
    };
  }

  function createRenderer(options = {}) {
    const documentRef = options.document || (root && root.document);
    const preferences = options.preferences || (root && root.openMmiPreferences);
    const enhancementsEnabled = options.enhancements !== false;
    const enhancedFields = new Set(["indicators", "voltage_v"]);
    const enhancedBooleans = new Set(["handbrake", "hazards", "bulb_out"]);
    let lastRenderSignature = null;
    let lastTachSignature = null;
    const metrics = { render_calls: 0, vehicle_renders: 0, unchanged_renders_skipped: 0, dom_writes: 0 };

    function requireDocument() {
      if (!documentRef) throw new Error("Vehicle renderer requires a document");
      return documentRef;
    }

    function query(selector) {
      return requireDocument().querySelector(selector);
    }

    function queryAll(selector) {
      return Array.from(requireDocument().querySelectorAll(selector));
    }

    function readSettings() {
      if (!preferences || typeof preferences.readDashboardSettings !== "function") return normaliseSettings();
      return normaliseSettings(preferences.readDashboardSettings(DEFAULT_SETTINGS));
    }

    function setText(node, value) {
      const next = String(value);
      if (node.textContent === next) return false;
      node.textContent = next;
      metrics.dom_writes += 1;
      return true;
    }

    function setHtml(node, html, signature = html) {
      const nextSignature = String(signature);
      if (node.dataset?.openMmiRenderSignature === nextSignature) return false;
      node.innerHTML = html;
      if (node.dataset) node.dataset.openMmiRenderSignature = nextSignature;
      metrics.dom_writes += 1;
      return true;
    }

    function setAttribute(node, name, value) {
      const next = String(value);
      if (node.getAttribute?.(name) === next) return false;
      node.setAttribute?.(name, next);
      metrics.dom_writes += 1;
      return true;
    }

    function setField(name, value) {
      queryAll(`[data-field="${name}"]`).forEach((node) => { setText(node, value); });
    }

    function setBoolean(name, value) {
      queryAll(`[data-bool="${name}"]`).forEach((node) => { setText(node, booleanText(value)); });
    }

    function setBooleanYesNo(name, value) {
      queryAll(`[data-bool-no="${name}"]`).forEach((node) => { setText(node, booleanYesNoText(value)); });
    }

    function updateFuelWarning(value) {
      const warningActive = value === true;
      queryAll('[data-field="fuel_l"]').forEach((node) => {
        const valueNode = node.closest ? node.closest(".value") : null;
        (valueNode || node).classList.toggle("openmmi-fuel-low-warning", warningActive);
      });
    }

    function updateDoor(name, value) {
      const isOpen = value === true;
      const text = value === true ? "Open" : value === false ? "Closed" : "--";
      queryAll(`[data-door-text="${name}"]`).forEach((node) => { setText(node, text); });
      queryAll(`[data-door-row="${name}"]`).forEach((node) => { node.classList.toggle("open", isOpen); });
      queryAll(`[data-door-mark="${name}"]`).forEach((node) => { node.classList.toggle("open", isOpen); });
    }

    function updateHealth(payload = {}) {
      const view = buildViewModel(payload, readSettings()).health;
      const dot = query("#healthDot");
      const text = query("#healthText");
      const age = query("#ageText");
      if (dot) {
        const className = `health-dot ${view.status}`;
        if (dot.className !== className) { dot.className = className; metrics.dom_writes += 1; }
      }
      if (text) setText(text, view.status);
      if (age) setText(age, view.ageText);
      return view;
    }

    function applyUnitLabels(units) {
      Object.entries(units).forEach(([field, unit]) => {
        queryAll(`[data-field="${field}"]`).forEach((node) => {
          const value = node.closest ? node.closest(".value") : null;
          const small = value ? value.querySelector("small") : node.parentElement?.querySelector?.("small");
          if (small) setText(small, unit);
        });
      });
    }

    function updateTach(rpm) {
      const number = Number(rpm);
      const known = Number.isFinite(number);
      const clamped = known ? Math.max(0, Math.min(6000, number)) : 0;
      const progress = clamped / 6000;
      let state = "rpm-unknown";
      if (known) {
        if (clamped < 900) state = "rpm-idle";
        else if (clamped < 4200) state = "rpm-normal";
        else if (clamped < 5200) state = "rpm-high";
        else state = "rpm-redline";
      }
      const signature = `${progress.toFixed(4)}:${state}`;
      if (signature === lastTachSignature) return;
      lastTachSignature = signature;
      const rootNode = requireDocument().documentElement;
      rootNode.style.setProperty("--rpm-scale", progress.toFixed(4));
      rootNode.style.setProperty("--rpm-fill", `${(progress * 100).toFixed(2)}%`);
      rootNode.classList.remove("rpm-unknown", "rpm-idle", "rpm-normal", "rpm-high", "rpm-redline");
      rootNode.classList.add(state);
      metrics.dom_writes += 3;
    }

    function applyDriverFacingCleanup() {
      const removeContainer = (node) => {
        if (!node) return;
        const container = node.closest?.("article, .tile, .openmmi-home-tile, .openmmi-home-metric, .openmmi-home-status, .metric, .status-chip, .summary-tile, .summary-card");
        if (container && !container.closest?.(".openmmi-settings-shell")) container.remove();
      };

      const home = query("#pageHome");
      if (home) {
        home.querySelectorAll('[data-field="fuel_l"], [data-field="rpm"], [data-field="lighting_mode"], [data-field="lights_on"]').forEach(removeContainer);
        home.querySelectorAll("article, .tile, .openmmi-home-tile, .openmmi-home-metric, .openmmi-home-status, .summary-tile, .summary-card").forEach((node) => {
          const label = (node.querySelector(".label, .openmmi-label, .metric-label")?.textContent || "").trim().toLowerCase();
          if (["lights", "light state", "lighting", "fuel remaining", "rpm"].includes(label)) node.remove();
        });
      }
      queryAll('#pageClimate [data-field="outside_unfiltered_c"]').forEach(removeContainer);
    }

    const applyInlineDataTelltales = (() => {
  const ICONS = Object.freeze({
    "leftTurn": "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+CjwhLS0gQ3JlYXRlZCB3aXRoIElua3NjYXBlIChodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy8pIC0tPgoKPHN2ZwogICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iCiAgIHhtbG5zOmNjPSJodHRwOi8vY3JlYXRpdmVjb21tb25zLm9yZy9ucyMiCiAgIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyIKICAgeG1sbnM6c3ZnPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIgogICB4bWxuczpzb2RpcG9kaT0iaHR0cDovL3NvZGlwb2RpLnNvdXJjZWZvcmdlLm5ldC9EVEQvc29kaXBvZGktMC5kdGQiCiAgIHhtbG5zOmlua3NjYXBlPSJodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy9uYW1lc3BhY2VzL2lua3NjYXBlIgogICB3aWR0aD0iMTg1LjEyMzc1IgogICBoZWlnaHQ9IjIwNy45OTk5NSIKICAgaWQ9InN2ZzQzMjkiCiAgIHZlcnNpb249IjEuMSIKICAgaW5rc2NhcGU6dmVyc2lvbj0iMC40OC40IHI5OTM5IgogICBzb2RpcG9kaTpkb2NuYW1lPSJBMTZMLnN2ZyI+CiAgPGRlZnMKICAgICBpZD0iZGVmczQzMzEiIC8+CiAgPHNvZGlwb2RpOm5hbWVkdmlldwogICAgIGlkPSJiYXNlIgogICAgIHBhZ2Vjb2xvcj0iI2ZmZmZmZiIKICAgICBib3JkZXJjb2xvcj0iIzY2NjY2NiIKICAgICBib3JkZXJvcGFjaXR5PSIxLjAiCiAgICAgaW5rc2NhcGU6cGFnZW9wYWNpdHk9IjAuMCIKICAgICBpbmtzY2FwZTpwYWdlc2hhZG93PSIyIgogICAgIGlua3NjYXBlOnpvb209IjIuOCIKICAgICBpbmtzY2FwZTpjeD0iNjIuMTA3MzAxIgogICAgIGlua3NjYXBlOmN5PSI3Ni4yODI0ODMiCiAgICAgaW5rc2NhcGU6ZG9jdW1lbnQtdW5pdHM9InB4IgogICAgIGlua3NjYXBlOmN1cnJlbnQtbGF5ZXI9ImxheWVyMSIKICAgICBzaG93Z3JpZD0iZmFsc2UiCiAgICAgZml0LW1hcmdpbi10b3A9IjQiCiAgICAgZml0LW1hcmdpbi1sZWZ0PSI0IgogICAgIGZpdC1tYXJnaW4tcmlnaHQ9IjQiCiAgICAgZml0LW1hcmdpbi1ib3R0b209IjQiCiAgICAgaW5rc2NhcGU6d2luZG93LXdpZHRoPSIxOTIwIgogICAgIGlua3NjYXBlOndpbmRvdy1oZWlnaHQ9IjExNTMiCiAgICAgaW5rc2NhcGU6d2luZG93LXg9IjEyNzYiCiAgICAgaW5rc2NhcGU6d2luZG93LXk9Ii00IgogICAgIGlua3NjYXBlOndpbmRvdy1tYXhpbWl6ZWQ9IjEiIC8+CiAgPG1ldGFkYXRhCiAgICAgaWQ9Im1ldGFkYXRhNDMzNCI+CiAgICA8cmRmOlJERj4KICAgICAgPGNjOldvcmsKICAgICAgICAgcmRmOmFib3V0PSIiPgogICAgICAgIDxkYzpmb3JtYXQ+aW1hZ2Uvc3ZnK3htbDwvZGM6Zm9ybWF0PgogICAgICAgIDxkYzp0eXBlCiAgICAgICAgICAgcmRmOnJlc291cmNlPSJodHRwOi8vcHVybC5vcmcvZGMvZGNtaXR5cGUvU3RpbGxJbWFnZSIgLz4KICAgICAgICA8ZGM6dGl0bGU+PC9kYzp0aXRsZT4KICAgICAgPC9jYzpXb3JrPgogICAgPC9yZGY6UkRGPgogIDwvbWV0YWRhdGE+CiAgPGcKICAgICBpbmtzY2FwZTpsYWJlbD0iTGF5ZXIgMSIKICAgICBpbmtzY2FwZTpncm91cG1vZGU9ImxheWVyIgogICAgIGlkPSJsYXllcjEiCiAgICAgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTExNS42NTY0OCwtMzczLjA5NDQ1KSI+CiAgICA8ZwogICAgICAgaWQ9Imc1Mjk1IgogICAgICAgdHJhbnNmb3JtPSJtYXRyaXgoMS45NDE3NDcxLDAsMCwxLjk0MTc0NzEsLTE2Mi42MjUsLTQ1NS40Mjk2MikiPgogICAgICA8cmVjdAogICAgICAgICB5PSI0MzAuMTMxMDQiCiAgICAgICAgIHg9IjE0Ni43NDcwMiIKICAgICAgICAgaGVpZ2h0PSIxMDAuMjY0OTUiCiAgICAgICAgIHdpZHRoPSI4OC40NzkyMSIKICAgICAgICAgaWQ9InJlY3Q1MjkzIgogICAgICAgICBzdHlsZT0iZmlsbDojMDBhMDAwO2ZpbGwtb3BhY2l0eToxO2ZpbGwtcnVsZTpldmVub2RkO3N0cm9rZTojMDAwMDAwO3N0cm9rZS13aWR0aDoyLjczNTA3MjE0O3N0cm9rZS1taXRlcmxpbWl0OjQ7c3Ryb2tlLW9wYWNpdHk6MTtzdHJva2UtZGFzaGFycmF5Om5vbmUiIC8+CiAgICAgIDxwYXRoCiAgICAgICAgIGlua3NjYXBlOmNvbm5lY3Rvci1jdXJ2YXR1cmU9IjAiCiAgICAgICAgIGlkPSJwYXRoMzM1MSIKICAgICAgICAgZD0ibSAxNDcuNjYzODksNTA1LjkxMTY4IDAsLTIzLjg1MTkgMTUuNzY5MTUsMTUuNjAxOSBjIDguNjczMDMsOC41ODEgMTkuNDczMDMsMTkuMTM5NiAyNCwyMy40NjM2IDQuNTI2OTcsNC4zMjM5IDguMjMwODUsOC4wMzY0IDguMjMwODUsOC4yNSAwLDAuMjEzNSAtMTAuOCwwLjM4ODIgLTI0LDAuMzg4MiBsIC0yNCwwIDAsLTIzLjg1MTggeiBtIDUxLjgzNDA3LDEwLjYwMzIgYyAwLjE4Mzc0LC03LjI4ODMgMC40MTUzLC0xMy4zMzMgMC41MTQ1OCwtMTMuNDMyNiAwLjA5OTMsLTAuMSA3LjkzNzA0LC0wLjMyNDcgMTcuNDE3MjQsLTAuNSBsIDE3LjIzNjczLC0wLjMxODggLTEwZS00LDEzLjc1IC0wLjAwMSwxMy43NSAtMTcuNzUsMCAtMTcuNzUsMCAwLjMzNDA3LC0xMy4yNTE0IHogbSAtMjUuMzM5OTMsLTI0Ljc1NzMgLTEwLjk3MTc1LC0xMS4wMDU5IDExLjczODgxLC0xMS43Mzg4IDExLjczODgsLTExLjczODggMCw1LjE3MzMgYyAwLDguMTU2OSAwLjM3NDc4LDguMzE2MSAxOS41NzE0Myw4LjMxNjEgbCAxNi40Mjg1NywwIDAsMTAgMCwxMCAtMTYuNDI4NTcsMCBjIC0xOC44ODE2MywwIC0xOS41NzE0MywwLjI2NjkgLTE5LjU3MTQzLDcuNTcxNCAwLDIuNDM1NyAtMC4zNDUxNyw0LjQyODYgLTAuNzY3MDUsNC40Mjg2IC0wLjQyMTg4LDAgLTUuNzA0MzUsLTQuOTUyNiAtMTEuNzM4ODEsLTExLjAwNTkgeiBtIC0yNi40OTQxNCwtMzYuNTE3NiAwLC0yNC40NzY1IDI0LjQ3NjM5LDAgMjQuNDc2MzgsMCAtMTYuNDY5NCwxNi4xMzYzIGMgLTkuMDU4MTgsOC44NzQ5IC0yMC4wNzI1NSwxOS44ODk0IC0yNC40NzYzOSwyNC40NzY1IGwgLTguMDA2OTgsOC4zNDAzIDAsLTI0LjQ3NjYgeiBtIDY4Ljc1LDMuNzc1MiAtMTYuNzUsLTAuMzAwMSAwLC0xMi4zOTQgYyAwLC02LjgxNjcgLTAuMjczMTUsLTEzLjEwNTggLTAuNjA2OTksLTEzLjk3NTggLTAuNTM5NzIsLTEuNDA2NSAxLjM5OTY0LC0xLjU4MTggMTcuNSwtMS41ODE4IGwgMTguMTA2OTksMCAwLDE0LjUgYyAwLDcuOTc1IC0wLjMzNzUsMTQuMzk5MSAtMC43NSwxNC4yNzU5IC0wLjQxMjUsLTAuMTIzMyAtOC4yODc1LC0wLjM1OTIgLTE3LjUsLTAuNTI0MiB6IgogICAgICAgICBzdHlsZT0iZmlsbDojMDAwMDAwIiAvPgogICAgPC9nPgogIDwvZz4KPC9zdmc+Cg==",
    "rightTurn": "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+CjwhLS0gQ3JlYXRlZCB3aXRoIElua3NjYXBlIChodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy8pIC0tPgoKPHN2ZwogICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iCiAgIHhtbG5zOmNjPSJodHRwOi8vY3JlYXRpdmVjb21tb25zLm9yZy9ucyMiCiAgIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyIKICAgeG1sbnM6c3ZnPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIgogICB4bWxuczpzb2RpcG9kaT0iaHR0cDovL3NvZGlwb2RpLnNvdXJjZWZvcmdlLm5ldC9EVEQvc29kaXBvZGktMC5kdGQiCiAgIHhtbG5zOmlua3NjYXBlPSJodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy9uYW1lc3BhY2VzL2lua3NjYXBlIgogICB3aWR0aD0iMTgzLjk2MDg5IgogICBoZWlnaHQ9IjIwNy45ODI4NSIKICAgaWQ9InN2ZzQzMjkiCiAgIHZlcnNpb249IjEuMSIKICAgaW5rc2NhcGU6dmVyc2lvbj0iMC40OC40IHI5OTM5IgogICBzb2RpcG9kaTpkb2NuYW1lPSJBMTZSLnN2ZyI+CiAgPGRlZnMKICAgICBpZD0iZGVmczQzMzEiIC8+CiAgPHNvZGlwb2RpOm5hbWVkdmlldwogICAgIGlkPSJiYXNlIgogICAgIHBhZ2Vjb2xvcj0iI2ZmZmZmZiIKICAgICBib3JkZXJjb2xvcj0iIzY2NjY2NiIKICAgICBib3JkZXJvcGFjaXR5PSIxLjAiCiAgICAgaW5rc2NhcGU6cGFnZW9wYWNpdHk9IjAuMCIKICAgICBpbmtzY2FwZTpwYWdlc2hhZG93PSIyIgogICAgIGlua3NjYXBlOnpvb209IjIuOCIKICAgICBpbmtzY2FwZTpjeD0iNjEuNTQ0OTAzIgogICAgIGlua3NjYXBlOmN5PSI3NS43MDk4NDkiCiAgICAgaW5rc2NhcGU6ZG9jdW1lbnQtdW5pdHM9InB4IgogICAgIGlua3NjYXBlOmN1cnJlbnQtbGF5ZXI9ImxheWVyMSIKICAgICBzaG93Z3JpZD0iZmFsc2UiCiAgICAgZml0LW1hcmdpbi10b3A9IjQiCiAgICAgZml0LW1hcmdpbi1sZWZ0PSI0IgogICAgIGZpdC1tYXJnaW4tcmlnaHQ9IjQiCiAgICAgZml0LW1hcmdpbi1ib3R0b209IjQiCiAgICAgaW5rc2NhcGU6d2luZG93LXdpZHRoPSIxOTIwIgogICAgIGlua3NjYXBlOndpbmRvdy1oZWlnaHQ9IjExNTMiCiAgICAgaW5rc2NhcGU6d2luZG93LXg9IjEyNzYiCiAgICAgaW5rc2NhcGU6d2luZG93LXk9Ii00IgogICAgIGlua3NjYXBlOndpbmRvdy1tYXhpbWl6ZWQ9IjEiIC8+CiAgPG1ldGFkYXRhCiAgICAgaWQ9Im1ldGFkYXRhNDMzNCI+CiAgICA8cmRmOlJERj4KICAgICAgPGNjOldvcmsKICAgICAgICAgcmRmOmFib3V0PSIiPgogICAgICAgIDxkYzpmb3JtYXQ+aW1hZ2Uvc3ZnK3htbDwvZGM6Zm9ybWF0PgogICAgICAgIDxkYzp0eXBlCiAgICAgICAgICAgcmRmOnJlc291cmNlPSJodHRwOi8vcHVybC5vcmcvZGMvZGNtaXR5cGUvU3RpbGxJbWFnZSIgLz4KICAgICAgICA8ZGM6dGl0bGU+PC9kYzp0aXRsZT4KICAgICAgPC9jYzpXb3JrPgogICAgPC9yZGY6UkRGPgogIDwvbWV0YWRhdGE+CiAgPGcKICAgICBpbmtzY2FwZTpsYWJlbD0iTGF5ZXIgMSIKICAgICBpbmtzY2FwZTpncm91cG1vZGU9ImxheWVyIgogICAgIGlkPSJsYXllcjEiCiAgICAgdHJhbnNmb3JtPSJ0cmFuc2xhdGUoLTExNi4yMTg4OCwtMzcyLjUzODkyKSI+CiAgICA8ZwogICAgICAgaWQ9Imc1MzE5IgogICAgICAgdHJhbnNmb3JtPSJtYXRyaXgoMS45MjE3NTcxLDAsMCwxLjkyMTc1NzEsLTE0NS44MjQzNywtNDQ2Ljk5NDA1KSI+CiAgICAgIDxyZWN0CiAgICAgICAgIHk9IjQyOS44NzMwMiIKICAgICAgICAgeD0iMTM5Ljc5MjI4IgogICAgICAgICBoZWlnaHQ9IjEwMS4zNzEyNyIKICAgICAgICAgd2lkdGg9Ijg4Ljg3MTI2OSIKICAgICAgICAgaWQ9InJlY3Q1MzE3IgogICAgICAgICBzdHlsZT0iZmlsbDojMDBhMDAwO2ZpbGwtb3BhY2l0eToxO2ZpbGwtcnVsZTpldmVub2RkO3N0cm9rZTojMDAwMDAwO3N0cm9rZS13aWR0aDoyLjcwMDE1NzE3O3N0cm9rZS1taXRlcmxpbWl0OjQ7c3Ryb2tlLW9wYWNpdHk6MTtzdHJva2UtZGFzaGFycmF5Om5vbmUiIC8+CiAgICAgIDxwYXRoCiAgICAgICAgIGlua3NjYXBlOmNvbm5lY3Rvci1jdXJ2YXR1cmU9IjAiCiAgICAgICAgIGlkPSJwYXRoMzM0OSIKICAgICAgICAgZD0ibSAxNDAuNzI3OTEsNTE1Ljg4MDEyIDAsLTE0LjUgMTYuOCwwIGMgMTEuNzMzMzMsMCAxNy4xNjE5LDAuMzYxOSAxOCwxLjIgMC44MTMwMywwLjgxMyAxLjIsNS40ODg5IDEuMiwxNC41IGwgMCwxMy4zIC0xOCwwIC0xOCwwIDAsLTE0LjUgeiBtIDM3LjEwMDkxLDE0LjI1IGMgMC4yMzg4MywtMC4xMzc1IDExLjU2MzgzLC0xMS4yNjI4IDI1LjE2NjY2LC0yNC43MjI5IGwgMjQuNzMyNDMsLTI0LjQ3MjkgMCwyNC43MjI5IDAsMjQuNzIyOSAtMjUuMTY2NjcsMCBjIC0xMy44NDE2NiwwIC0yNC45NzEyNSwtMC4xMTI1IC0yNC43MzI0MiwtMC4yNSB6IG0gMTAuNTIwMTcsLTI5LjI5NTEgYyAtMC4zNDE1OSwtMC44OTAxIC0wLjYyMTA4LC0zLjM4MTcgLTAuNjIxMDgsLTUuNTM2NyBsIDAsLTMuOTE4MiAtMTYuOCwwIGMgLTExLjczMzMzLDAgLTE3LjE2MTkxLC0wLjM2MTkgLTE4LC0xLjIgLTEuNjIwNjksLTEuNjIwNyAtMS42MjA2OSwtMTcuOTc5MyAwLC0xOS42IDAuODM4MDksLTAuODM4MSA2LjI2NjY3LC0xLjIgMTgsLTEuMiBsIDE2LjgsMCAwLC00Ljk0MSBjIDAsLTIuNzE3NSAwLjM5MDU4LC01LjE4MjQgMC44Njc5NiwtNS40Nzc0IDAuNDc3MzgsLTAuMjk1IDUuOTUyNyw0LjMzMDIgMTIuMTY3MzcsMTAuMjc4MyBsIDExLjI5OTQxLDEwLjgxNDYgLTUuOTE3MzcsNS44MDQxIGMgLTE1LjkyNjE2LDE1LjYyMTQgLTE3LjE1MjY4LDE2LjY1MzYgLTE3Ljc5NjI5LDE0Ljk3NjMgeiBtIDMyLjg1OTEyLC0yNy43NzM1IGMgLTMuMzEwODksLTMuMzk5OCAtMTQuMzg1ODUsLTE0LjM5MzkgLTI0LjYxMTAzLC0yNC40MzE0IGwgLTE4LjU5MTIzLC0xOC4yNSAyNC44NjEwMywwIDI0Ljg2MTAzLDAgMCwyNC41IGMgMCwxMy40NzUgLTAuMTEyNSwyNC40NjkxIC0wLjI1LDI0LjQzMTQgLTAuMTM3NSwtMC4wMzggLTIuOTU4OTEsLTIuODUwMiAtNi4yNjk4LC02LjI1IHogbSAtODAuNDgwMiwtMjguMTgxNCAwLC0xNC41IDE4LDAgMTgsMCAwLDEzLjMgYyAwLDkuMDExMSAtMC4zODY5NywxMy42ODcgLTEuMiwxNC41IC0wLjgzODEsMC44MzgxIC02LjI2NjY3LDEuMiAtMTgsMS4yIGwgLTE2LjgsMCAwLC0xNC41IHoiCiAgICAgICAgIHN0eWxlPSJmaWxsOiMwMDAwMDAiIC8+CiAgICA8L2c+CiAgPC9nPgo8L3N2Zz4K",
    "bulbFailure": "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+CjwhLS0gQ3JlYXRlZCB3aXRoIElua3NjYXBlIChodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy8pIC0tPgoKPHN2ZwogICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iCiAgIHhtbG5zOmNjPSJodHRwOi8vY3JlYXRpdmVjb21tb25zLm9yZy9ucyMiCiAgIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyIKICAgeG1sbnM6c3ZnPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIgogICB4bWxuczpzb2RpcG9kaT0iaHR0cDovL3NvZGlwb2RpLnNvdXJjZWZvcmdlLm5ldC9EVEQvc29kaXBvZGktMC5kdGQiCiAgIHhtbG5zOmlua3NjYXBlPSJodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy9uYW1lc3BhY2VzL2lua3NjYXBlIgogICB3aWR0aD0iMjA4IgogICBoZWlnaHQ9IjE5Mi43OTM2NCIKICAgaWQ9InN2ZzQzMjkiCiAgIHZlcnNpb249IjEuMSIKICAgaW5rc2NhcGU6dmVyc2lvbj0iMC40OC40IHI5OTM5IgogICBzb2RpcG9kaTpkb2NuYW1lPSJBMTQuc3ZnIj4KICA8ZGVmcwogICAgIGlkPSJkZWZzNDMzMSIgLz4KICA8c29kaXBvZGk6bmFtZWR2aWV3CiAgICAgaWQ9ImJhc2UiCiAgICAgcGFnZWNvbG9yPSIjZmZmZmZmIgogICAgIGJvcmRlcmNvbG9yPSIjNjY2NjY2IgogICAgIGJvcmRlcm9wYWNpdHk9IjEuMCIKICAgICBpbmtzY2FwZTpwYWdlb3BhY2l0eT0iMC4wIgogICAgIGlua3NjYXBlOnBhZ2VzaGFkb3c9IjIiCiAgICAgaW5rc2NhcGU6em9vbT0iMi44IgogICAgIGlua3NjYXBlOmN4PSI3NC41MjU3NzUiCiAgICAgaW5rc2NhcGU6Y3k9Ijg2LjU5ODc3MiIKICAgICBpbmtzY2FwZTpkb2N1bWVudC11bml0cz0icHgiCiAgICAgaW5rc2NhcGU6Y3VycmVudC1sYXllcj0ibGF5ZXIxIgogICAgIHNob3dncmlkPSJmYWxzZSIKICAgICBmaXQtbWFyZ2luLXRvcD0iNCIKICAgICBmaXQtbWFyZ2luLWxlZnQ9IjQiCiAgICAgZml0LW1hcmdpbi1yaWdodD0iNCIKICAgICBmaXQtbWFyZ2luLWJvdHRvbT0iNCIKICAgICBpbmtzY2FwZTp3aW5kb3ctd2lkdGg9IjE5MjAiCiAgICAgaW5rc2NhcGU6d2luZG93LWhlaWdodD0iMTE1MyIKICAgICBpbmtzY2FwZTp3aW5kb3cteD0iMTI3NiIKICAgICBpbmtzY2FwZTp3aW5kb3cteT0iLTQiCiAgICAgaW5rc2NhcGU6d2luZG93LW1heGltaXplZD0iMSIgLz4KICA8bWV0YWRhdGEKICAgICBpZD0ibWV0YWRhdGE0MzM0Ij4KICAgIDxyZGY6UkRGPgogICAgICA8Y2M6V29yawogICAgICAgICByZGY6YWJvdXQ9IiI+CiAgICAgICAgPGRjOmZvcm1hdD5pbWFnZS9zdmcreG1sPC9kYzpmb3JtYXQ+CiAgICAgICAgPGRjOnR5cGUKICAgICAgICAgICByZGY6cmVzb3VyY2U9Imh0dHA6Ly9wdXJsLm9yZy9kYy9kY21pdHlwZS9TdGlsbEltYWdlIiAvPgogICAgICAgIDxkYzp0aXRsZT48L2RjOnRpdGxlPgogICAgICA8L2NjOldvcms+CiAgICA8L3JkZjpSREY+CiAgPC9tZXRhZGF0YT4KICA8ZwogICAgIGlua3NjYXBlOmxhYmVsPSJMYXllciAxIgogICAgIGlua3NjYXBlOmdyb3VwbW9kZT0ibGF5ZXIiCiAgICAgaWQ9ImxheWVyMSIKICAgICB0cmFuc2Zvcm09InRyYW5zbGF0ZSgtMTAzLjIzODAxLC0zOTguNjE3MDUpIj4KICAgIDxnCiAgICAgICBpZD0iZzUyMjMiCiAgICAgICB0cmFuc2Zvcm09Im1hdHJpeCgxLjAxMzc1ODIsMCwwLDEuMDEzNzU4MiwtMS40NzUzOTc4LC04LjA4MTY5MDkpIj4KICAgICAgPHJlY3QKICAgICAgICAgeT0iNDA2LjQ5MiIKICAgICAgICAgeD0iMTA4LjYwNTA2IgogICAgICAgICBoZWlnaHQ9IjE3OS41NTE2MSIKICAgICAgICAgd2lkdGg9IjE5NC41NTE2MSIKICAgICAgICAgaWQ9InJlY3Q1MjIxIgogICAgICAgICBzdHlsZT0iZmlsbDojZTBiMzAwO2ZpbGwtb3BhY2l0eToxO2ZpbGwtcnVsZTpldmVub2RkO3N0cm9rZTojMDAwMDAwO3N0cm9rZS13aWR0aDoyLjczNDEwNjA2O3N0cm9rZS1taXRlcmxpbWl0OjQ7c3Ryb2tlLW9wYWNpdHk6MTtzdHJva2UtZGFzaGFycmF5Om5vbmUiIC8+CiAgICAgIDxwYXRoCiAgICAgICAgIGlua3NjYXBlOmNvbm5lY3Rvci1jdXJ2YXR1cmU9IjAiCiAgICAgICAgIGlkPSJwYXRoMzI5OSIKICAgICAgICAgZD0ibSAxMDkuMzgwODgsNTQwLjE1MTA3IDAsLTQ1LjQ3NCAxNS4yNSwtMC4yNzYwNCAxNS4yNSwtMC4yNzYwNSAwLC01IDAsLTUgLTE1LjI1LC0wLjI3NjA1IC0xNS4yNSwtMC4yNzYwNCAwLC0zNy45NzM5NiAwLC0zNy45NzM5NSAzNSwwIDM1LDAgMCwxNy4wMDY2MSAwLDE3LjAwNjYyIC0yLjc1LDIuMjI3OTUgYyAtMS41MTI1LDEuMjI1MzggLTUuNDc1MzUsNC45MzU0MyAtOC44MDYzNSw4LjI0NDU3IC0xMC45MzAxNiwxMC44NTg0MyAtMTYuNDk0LDI2LjMwMzAzIC0xNS4wNTQ2NCw0MS43ODk5NyAyLjAxNjY2LDIxLjY5ODUzIDE1LjU2NjU1LDM4Ljk2MTExIDM2LjMzNDg1LDQ2LjI5MDY3IDUuNDk2NTYsMS45Mzk4IDguNzU4OTIsMi4zOTE0IDE3LjI3NjE0LDIuMzkxNCA4LjU5MTM2LDAgMTEuNzY0MjgsLTAuNDQ2MSAxNy40NTUxNywtMi40NTQgMjYuNTkzMTQsLTkuMzgyODMgNDEuMjk5MzcsLTM3LjE5NzAzIDM0LjM2OTUzLC02NS4wMDM3OSAtMS4zMTM3NCwtNS4yNzE1NiAtNi4yNzQ5OSwtMTUuMDE4MTMgLTEwLjEwODE4LC0xOS44NTc5MyAtMS42ODcwOSwtMi4xMzAxMiAtNS4zNTA5OCwtNS42MjQ3IC04LjE0MTk4LC03Ljc2NTczIC0yLjc5MSwtMi4xNDEwMyAtNS42MzcwNCwtNC4zNTY4MyAtNi4zMjQ1NCwtNC45MjM5OSAtMC45MTAzMywtMC43NTA5OSAtMS4yNSwtNS42Mzk5NiAtMS4yNSwtMTcuOTkxNzggbCAwLC0xNi45NjA1NyAzNSwwIDM1LDAgMCwzNy45NzMyNyAwLDM3Ljk3MzI2IC0xNC43NSwwLjI3Njc0IC0xNC43NSwwLjI3NjczIDAsNSAwLDUgMTQuNzUsMC4yNzY3MyAxNC43NSwwLjI3Njc0IDAsNDUuNDczMjIgMCw0NS40NzMzIC00NS40NzMyNywwIC00NS40NzMyNiwwIC0wLjI3Njc0LC0xNC43NSAtMC4yNzY3MywtMTQuNzUgLTUsMCAtNSwwIC0wLjI3NjczLDE0Ljc1IC0wLjI3Njc0LDE0Ljc1IC00NS40NzMyNiwwIC00NS40NzMyNywwIDAsLTQ1LjQ3MzkgeiBtIDQzLjk4MTc3LDkuOTkyMiAxMC40NDYwMywtMTAuNDgxNyAtMy40MTAwNywtMy41MTgzIGMgLTEuODc1NTMsLTEuOTM1MSAtMy44OTIzMywtMy41MTgyOSAtNC40ODE3NiwtMy41MTgyOSAtMC41ODk0NCwwIC01Ljc3MjQyLDQuNzE2NzkgLTExLjUxNzc0LDEwLjQ4MTY5IGwgLTEwLjQ0NjAzLDEwLjQ4MTcgMy40MTAwNywzLjUxODMgYyAxLjg3NTUzLDEuOTM1MSAzLjg5MjMzLDMuNTE4MyA0LjQ4MTc2LDMuNTE4MyAwLjU4OTQ0LDAgNS43NzI0MiwtNC43MTY4IDExLjUxNzc0LC0xMC40ODE3IHogbSAxMjEuMjEzNTEsNy43OTQ2IGMgMS41NDI2LC0xLjQ3NzkgMi44MDQ3MiwtMy4yNTI5IDIuODA0NzIsLTMuOTQ0NCAwLC0xLjI5ODIgLTE5LjYyNTE4LC0yMS4zNjg0OSAtMjAuODk0NjQsLTIxLjM2ODQ5IC0wLjM5MjE3LDAgLTIuMjQ3NTYsMS41ODMxOSAtNC4xMjMwOSwzLjUxODI5IGwgLTMuNDEwMDcsMy41MTgzIDEwLjQ0NjAzLDEwLjQ4MTcgYyA1Ljc0NTMyLDUuNzY0OSAxMC44Nzk0NSwxMC40ODE3IDExLjQwOTE4LDEwLjQ4MTcgMC41Mjk3NCwwIDIuMjI1MjgsLTEuMjA5MiAzLjc2Nzg3LC0yLjY4NzEgeiBtIC0xMTUsLTExNC45OTk5OCBjIDEuNTQyNiwtMS40Nzc5IDIuODA0NzIsLTMuMjU2MzIgMi44MDQ3MiwtMy45NTIwNCAwLC0xLjYwMzk0IC0xOS4wNTQ5OCwtMjAuMzYwODcgLTIwLjY4NDQsLTIwLjM2MDg3IC0xLjcwNTExLDAgLTYuMzE1Niw0LjQ1MDc1IC02LjMxNTYsNi4wOTY3OCAwLDEuNTM2NzkgMTguOTI0NzgsMjAuOTAzMjIgMjAuNDI2NTIsMjAuOTAzMjIgMC41MzAyMiwwIDIuMjI2MTcsLTEuMjA5MTkgMy43Njg3NiwtMi42ODcwOSB6IG0gMTA4LjAzNjgzLC03LjA0NDE1IGMgNS4zNzIzNCwtNS4zNTIxOCA5Ljc2Nzg5LC0xMC4yMTM5IDkuNzY3ODksLTEwLjgwMzgzIDAsLTAuNTg5OTMgLTEuNTg1OTcsLTIuNjA5NzggLTMuNTI0MzgsLTQuNDg4NTcgbCAtMy41MjQzOSwtMy40MTU5NiAtMTAuNjk1NDIsMTAuNjk1NDEgLTEwLjY5NTQxLDEwLjY5NTQyIDMuNDE1OTcsMy41MjQzOCBjIDEuODc4NzgsMS45Mzg0MiAzLjg4MjE0LDMuNTI0MzkgNC40NTE5MSwzLjUyNDM5IDAuNTY5NzYsMCA1LjQzMTQ5LC00LjM3OTA2IDEwLjgwMzgzLC05LjczMTI0IHogbSAtNzIuNDg2MTksOTUuNzA3NDkgYyAtMTAuNzcwMDIsLTIuNzE1OTUgLTIyLjg3NzQzLC0xMi42NTkxOCAtMjcuOTc3MTIsLTIyLjk3NjI1IC0xLjQ5NTI1LC0zLjAyNSAtMy4yNzA4MSwtOC43NTgzNyAtMy45NDU2OSwtMTIuNzQwODEgLTAuOTczNjksLTUuNzQ1NzEgLTAuOTcwMDEsLTguNTMzMjQgMC4wMTc4LC0xMy41IDEuNzM0MzIsLTguNzIwMDggNi42NDYzMywtMTguMzQxNyAxMi4xOTc5NywtMjMuODkzMzQgNy43MDE2OSwtNy43MDE2OSAxOS44NjgwNiwtMTIuODI4OTQgMzAuNTAwOTksLTEyLjg1Mzk4IDYuNTI0OSwtMC4wMTU0IDE1LjUzMzQ3LDIuMzU4OTcgMjEuNzE0NzEsNS43MjMyMiA1LjY4NjQ3LDMuMDk0OTcgMTQuODUwMzgsMTIuNTkwMjIgMTcuODc3NTQsMTguNTIzOTUgMS4xNTEyNCwyLjI1NjYgMi42OTk2Myw3LjU3NjkxIDMuNDQwODgsMTEuODIyOTEgMC45OTU5Myw1LjcwNDgxIDEuMDg5ODQsOS4zNzczIDAuMzU5NzYsMTQuMDY5MDIgLTIuMTg5MjMsMTQuMDY4NzMgLTExLjg4NjM0LDI3LjEyNDYzIC0yNC41NDY5LDMzLjA0OTIyIC01Ljc1Mjg4LDIuNjkyMSAtNy45NzMxNywzLjE1ODcxIC0xNi4yNzM4MywzLjQyMDA5IC01LjI4NjYzLDAuMTY2NDcgLTExLjMwMTM5LC0wLjEyMzM0IC0xMy4zNjYxMywtMC42NDQwMyB6IG0gMTYuNTA0MDgsLTEwLjEyOTY4IGMgMC45NjI1LC0xLjEzNzQ3IDEuNzUsLTMuMTYzNzQgMS43NSwtNC41MDI4MSAwLC0zLjE4MTgxIC00LjIyNDczLC03LjM0Mzc2IC03LjQ1NDU1LC03LjM0Mzc2IC0zLjYyMzQ5LDAgLTYuNTQ1NDUsMy4zMTU2NyAtNi41NDU0NSw3LjQyNzQyIDAsMi41MzQ2NSAwLjcwNTA0LDMuOTgyMDEgMi43MDY3Miw1LjU1NjUyIDMuMTY2MDksMi40OTA0NiA2Ljg0NDQ4LDIuMDUyMDcgOS41NDMyOCwtMS4xMzczNyB6IG0gLTAuNzA0NTUsLTE4LjMwMTEyIDIuNDU0NTUsLTIuNDU0NTQgMCwtMjEuOTM0NjcgYyAwLC0xOS44MjY4OCAtMC4xNzcyMSwtMjIuMTUzNTIgLTEuODQ0MTcsLTI0LjIxMjEyIC0yLjUwNzI3LC0zLjA5NjM2IC03LjgwNDM5LC0zLjA5NjM2IC0xMC4zMTE2NiwwIC0xLjY2OTEsMi4wNjEyNSAtMS44NDQxNyw0LjM5NiAtMS44NDQxNywyNC41OTQwOCBsIDAsMjIuMzE2NjQgMi42MzQ4NiwyLjA3MjU4IGMgMS40NDkxNywxLjEzOTkyIDMuNDk0NjMsMi4wNzI1OCA0LjU0NTQ1LDIuMDcyNTggMS4wNTA4MywwIDMuMDE1MTQsLTEuMTA0NTUgNC4zNjUxNCwtMi40NTQ1NSB6IG0gNC45NTQ1NSwtNjYuNzM4IGMgLTIuMjI1MTEsLTAuNTQ1NjEgLTguNzg2NzksLTAuNzExNjEgLTE0Ljc4Nzc5LC0wLjM3NDA5IGwgLTEwLjc4Nzc5LDAuNjA2NzMgMC4yODc3OSwtOS4yNzAwNCAwLjI4Nzc5LC05LjI3MDA1IDE1LjUsMCAxNS41LDAgMCw5LjQxNjY3IGMgMCw1LjQ1OTQ2IC0wLjQyMDIzLDkuNTEyMjYgLTEsOS42NDQxNCAtMC41NSwwLjEyNTExIC0yLjgsLTAuMjEzOSAtNSwtMC43NTMzNiB6IgogICAgICAgICBzdHlsZT0iZmlsbDojMDAwMDAwIiAvPgogICAgPC9nPgogIDwvZz4KPC9zdmc+Cg==",
    "hazard": "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+CjwhLS0gQ3JlYXRlZCB3aXRoIElua3NjYXBlIChodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy8pIC0tPgoKPHN2ZwogICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iCiAgIHhtbG5zOmNjPSJodHRwOi8vY3JlYXRpdmVjb21tb25zLm9yZy9ucyMiCiAgIHhtbG5zOnJkZj0iaHR0cDovL3d3dy53My5vcmcvMTk5OS8wMi8yMi1yZGYtc3ludGF4LW5zIyIKICAgeG1sbnM6c3ZnPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIgogICB4bWxuczpzb2RpcG9kaT0iaHR0cDovL3NvZGlwb2RpLnNvdXJjZWZvcmdlLm5ldC9EVEQvc29kaXBvZGktMC5kdGQiCiAgIHhtbG5zOmlua3NjYXBlPSJodHRwOi8vd3d3Lmlua3NjYXBlLm9yZy9uYW1lc3BhY2VzL2lua3NjYXBlIgogICB3aWR0aD0iMjA4IgogICBoZWlnaHQ9IjE3Ny43MDk3IgogICBpZD0ic3ZnNDMyOSIKICAgdmVyc2lvbj0iMS4xIgogICBpbmtzY2FwZTp2ZXJzaW9uPSIwLjQ4LjQgcjk5MzkiCiAgIHNvZGlwb2RpOmRvY25hbWU9IkExOS5zdmciPgogIDxkZWZzCiAgICAgaWQ9ImRlZnM0MzMxIiAvPgogIDxzb2RpcG9kaTpuYW1lZHZpZXcKICAgICBpZD0iYmFzZSIKICAgICBwYWdlY29sb3I9IiNmZmZmZmYiCiAgICAgYm9yZGVyY29sb3I9IiM2NjY2NjYiCiAgICAgYm9yZGVyb3BhY2l0eT0iMS4wIgogICAgIGlua3NjYXBlOnBhZ2VvcGFjaXR5PSIwLjAiCiAgICAgaW5rc2NhcGU6cGFnZXNoYWRvdz0iMiIKICAgICBpbmtzY2FwZTp6b29tPSIyLjgiCiAgICAgaW5rc2NhcGU6Y3g9IjU4LjExNjM0MiIKICAgICBpbmtzY2FwZTpjeT0iNDUuNDk1NTM3IgogICAgIGlua3NjYXBlOmRvY3VtZW50LXVuaXRzPSJweCIKICAgICBpbmtzY2FwZTpjdXJyZW50LWxheWVyPSJsYXllcjEiCiAgICAgc2hvd2dyaWQ9ImZhbHNlIgogICAgIGZpdC1tYXJnaW4tdG9wPSI0IgogICAgIGZpdC1tYXJnaW4tbGVmdD0iNCIKICAgICBmaXQtbWFyZ2luLXJpZ2h0PSI0IgogICAgIGZpdC1tYXJnaW4tYm90dG9tPSI0IgogICAgIGlua3NjYXBlOndpbmRvdy13aWR0aD0iMTkyMCIKICAgICBpbmtzY2FwZTp3aW5kb3ctaGVpZ2h0PSIxMTUzIgogICAgIGlua3NjYXBlOndpbmRvdy14PSIxMjc2IgogICAgIGlua3NjYXBlOndpbmRvdy15PSItNCIKICAgICBpbmtzY2FwZTp3aW5kb3ctbWF4aW1pemVkPSIxIiAvPgogIDxtZXRhZGF0YQogICAgIGlkPSJtZXRhZGF0YTQzMzQiPgogICAgPHJkZjpSREY+CiAgICAgIDxjYzpXb3JrCiAgICAgICAgIHJkZjphYm91dD0iIj4KICAgICAgICA8ZGM6Zm9ybWF0PmltYWdlL3N2Zyt4bWw8L2RjOmZvcm1hdD4KICAgICAgICA8ZGM6dHlwZQogICAgICAgICAgIHJkZjpyZXNvdXJjZT0iaHR0cDovL3B1cmwub3JnL2RjL2RjbWl0eXBlL1N0aWxsSW1hZ2UiIC8+CiAgICAgICAgPGRjOnRpdGxlPjwvZGM6dGl0bGU+CiAgICAgIDwvY2M6V29yaz4KICAgIDwvcmRmOlJERj4KICA8L21ldGFkYXRhPgogIDxnCiAgICAgaW5rc2NhcGU6bGFiZWw9IkxheWVyIDEiCiAgICAgaW5rc2NhcGU6Z3JvdXBtb2RlPSJsYXllciIKICAgICBpZD0ibGF5ZXIxIgogICAgIHRyYW5zZm9ybT0idHJhbnNsYXRlKC0xMTkuNjQ3NDQsLTM3Mi41OTc3NSkiPgogICAgPGcKICAgICAgIGlkPSJnNTM1NCIKICAgICAgIHRyYW5zZm9ybT0ibWF0cml4KDEuMTc3OTU1NSwwLDAsMS4xNzc5NTU1LC0yMi4wMDM3NCwtOTcuMjE4NDA2KSI+CiAgICAgIDxyZWN0CiAgICAgICAgIHk9IjQwMy42MzM4MiIKICAgICAgICAgeD0iMTI1LjA0NTIyIgogICAgICAgICBoZWlnaHQ9IjE0MS4yNzU4NSIKICAgICAgICAgd2lkdGg9IjE2Ni45OTAxNCIKICAgICAgICAgaWQ9InJlY3Q1MzQxIgogICAgICAgICBzdHlsZT0iZmlsbDojY2YwMDAwO2ZpbGwtb3BhY2l0eToxO2ZpbGwtcnVsZTpldmVub2RkO3N0cm9rZTojMDAwMDAwO3N0cm9rZS13aWR0aDoyLjc5NTU2MDM2O3N0cm9rZS1taXRlcmxpbWl0OjQ7c3Ryb2tlLW9wYWNpdHk6MTtzdHJva2UtZGFzaGFycmF5Om5vbmUiIC8+CiAgICAgIDxwYXRoCiAgICAgICAgIGlua3NjYXBlOmNvbm5lY3Rvci1jdXJ2YXR1cmU9IjAiCiAgICAgICAgIGlkPSJwYXRoMzM3NyIKICAgICAgICAgZD0ibSAyODguNDkxOTksNTQxLjc1NzUyIGMgLTEuMDkzMSwtMS40NDE3NiAtMTAuMzU0MSwtMTcuMDc5MjYgLTIwLjU4LC0zNC43NSAtMTAuMjI1OSwtMTcuNjcwNzMgLTIwLjYyNTEsLTM1LjUwMzYxIC0yMy4xMDkyLC0zOS42Mjg2MSAtMi40ODQyLC00LjEyNSAtMTEuNjEzMSwtMTkuNzgwNjYgLTIwLjI4NjQsLTM0Ljc5MDM1IC04LjY3MzMsLTE1LjAwOTY5IC0xNi4yMjY5LC0yNy4xMzc5NSAtMTYuNzg1NywtMjYuOTUxNjkgLTAuNTU4OCwwLjE4NjI2IC04LjgyNzgsMTMuODE2OTIgLTE4LjM3NTcsMzAuMjkwMzUgLTkuNTQ3OCwxNi40NzM0MyAtMTkuMzIzLDMzLjEwMTY5IC0yMS43MjI3LDM2Ljk1MTY5IC0yLjM5OTYsMy44NSAtMTIuMTM2NCwyMC41IC0yMS42MzczLDM3IC05LjUwMDgsMTYuNSAtMTguMTEwNCwzMC44MDUwMyAtMTkuMTMyMywzMS43ODg5NiAtMS44MTAxLDEuNzQyNzcgLTEuODU4MSwtMC4wMzE5IC0xLjg1ODEsLTY4Ljc1MDAxIGwgMCwtNzAuNTM4OTUgNDEsMCBjIDI2LjY2NjcsMCA0MSwwLjM0OTU5IDQxLDEgMCwwLjU1IDAuNDc2NiwxIDEuMDU5LDEgMC41ODI1LDAgMC43ODA5LC0wLjQ1IDAuNDQxLC0xIC0wLjQwODIsLTAuNjYwNTQgMTMuNDg5NiwtMSA0MC45NDEsLTEgbCA0MS41NTksMCAwLDcxIGMgMCwzOS4wNSAtMC4xMTgyLDcxIC0wLjI2MjYsNzEgLTAuMTQ0NSwwIC0xLjE1NywtMS4xNzk2MiAtMi4yNSwtMi42MjEzOSB6IG0gLTE0NS45NjkyLC02LjM0OTI0IGMgLTAuMzMsLTAuNTMzODUgMC4wNDksLTIuMjIxMzUgMC44NDIyLC0zLjc1IDEuODM2MSwtMy41Mzg1OSAzMC42MjEyLC01Mi44NjU1OSAzNy44MDI0LC02NC43NzkzNyAyLjk4MzcsLTQuOTUgOS44ODc2LC0xNi43NjI1IDE1LjM0MiwtMjYuMjUgNS40NTQ0LC05LjQ4NzUgMTAuNDkwOCwtMTcuMjUgMTEuMTkyLC0xNy4yNSAxLjU2OTIsMCAzLjM1MDcsMi43ODY0NiAxNi4zMDMyLDI1LjUgNS42NDU1LDkuOSAxMS40MTUyLDE5LjggMTIuODIxNiwyMiA3LjczNTUsMTIuMTAwODYgMzYuMTc4NCw2Mi4xODc3NSAzNi4xNzg0LDYzLjcwODkgMCwxLjcwMzA4IC0zLjE5MTQsMS43OTExIC02NC45NDEsMS43OTExIC00MC45MDU0LDAgLTY1LjE2MywtMC4zNTkyNSAtNjUuNTQwOCwtMC45NzA2MyB6IG0gMTEwLjQ4MTgsLTExLjk2NDg5IGMgMCwtMS4wNDcwMyAtMjQuNzc2MSwtNDQuMzIwODcgLTI4Ljk1MDMsLTUwLjU2NDQ4IC0zLjYyMDEsLTUuNDE0ODEgLTEzLjgxMTQsLTIyLjcxNTQ4IC0xNC42MTU3LC0yNC44MTE0MiAtMC4zNTY0LC0wLjkyODcyIC0xLjAwNSwtMS42ODg1OCAtMS40NDEzLC0xLjY4ODU4IC0wLjQzNjQsMCAtMy44Mzk4LDUuMjg3NSAtNy41NjMxLDExLjc1IC0zLjcyMzQsNi40NjI1IC03LjY5NTEsMTMuMDg3NTQgLTguODI2MSwxNC43MjIzMiAtNC4zODk1LDYuMzQ0ODcgLTI5LjMyNyw1MC40NzA4OSAtMjguODEzNCw1MC45ODQ0MyAwLjk4MjYsMC45ODI2NCA5MC4yMDk5LDAuNTk0NjUgOTAuMjA5OSwtMC4zOTIyNyB6IG0gLTc0LjUzLC05LjExMjk5IGMgLTEuMDk1NCwtMS43NzIzOSAyNy4yMzcsLTQ5Ljk1MTQ5IDI5LjM3NDYsLTQ5Ljk1MTQ5IDEuMjM3OSwwIDQuMjkwNCw0Ljc4NDk3IDE4LjE2MDYsMjguNDY3ODQgNi42MzcyLDExLjMzMjg1IDExLjc5OTcsMjEuMDM4NzYgMTEuNDcyMiwyMS41Njg2OCAtMC44NDc3LDEuMzcxNjcgLTU4LjE1ODEsMS4yODkxIC01OS4wMDc0LC0wLjA4NSB6IgogICAgICAgICBzdHlsZT0iZmlsbDojMDAwMDAwIiAvPgogICAgPC9nPgogIDwvZz4KPC9zdmc+Cg==",
    "parkingBrake": "data:image/svg+xml;base64,PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0iVVRGLTgiIHN0YW5kYWxvbmU9Im5vIj8+CjxzdmcKICAgeG1sbnM6ZGM9Imh0dHA6Ly9wdXJsLm9yZy9kYy9lbGVtZW50cy8xLjEvIgogICB4bWxuczpjYz0iaHR0cDovL2NyZWF0aXZlY29tbW9ucy5vcmcvbnMjIgogICB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRheC1ucyMiCiAgIHhtbG5zOnN2Zz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciCiAgIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyIKICAgeG1sbnM6c29kaXBvZGk9Imh0dHA6Ly9zb2RpcG9kaS5zb3VyY2Vmb3JnZS5uZXQvRFREL3NvZGlwb2RpLTAuZHRkIgogICB4bWxuczppbmtzY2FwZT0iaHR0cDovL3d3dy5pbmtzY2FwZS5vcmcvbmFtZXNwYWNlcy9pbmtzY2FwZSIKICAgdmVyc2lvbj0iMS4wIgogICB3aWR0aD0iMjA4IgogICBoZWlnaHQ9IjE2My4xMTI1MiIKICAgdmlld0JveD0iMCAwIDE2Ni40IDEzMC40OTAwMSIKICAgcHJlc2VydmVBc3BlY3RSYXRpbz0ieE1pZFlNaWQgbWVldCIKICAgaWQ9InN2ZzIiCiAgIGlua3NjYXBlOnZlcnNpb249IjAuNDguNCByOTkzOSIKICAgc29kaXBvZGk6ZG9jbmFtZT0iQjAyLnN2ZyI+CiAgPGRlZnMKICAgICBpZD0iZGVmczE4IiAvPgogIDxzb2RpcG9kaTpuYW1lZHZpZXcKICAgICBwYWdlY29sb3I9IiNmZmZmZmYiCiAgICAgYm9yZGVyY29sb3I9IiM2NjY2NjYiCiAgICAgYm9yZGVyb3BhY2l0eT0iMSIKICAgICBvYmplY3R0b2xlcmFuY2U9IjEwIgogICAgIGdyaWR0b2xlcmFuY2U9IjEwIgogICAgIGd1aWRldG9sZXJhbmNlPSIxMCIKICAgICBpbmtzY2FwZTpwYWdlb3BhY2l0eT0iMCIKICAgICBpbmtzY2FwZTpwYWdlc2hhZG93PSIyIgogICAgIGlua3NjYXBlOndpbmRvdy13aWR0aD0iMTkyMCIKICAgICBpbmtzY2FwZTp3aW5kb3ctaGVpZ2h0PSIxMTUzIgogICAgIGlkPSJuYW1lZHZpZXcxNiIKICAgICBzaG93Z3JpZD0iZmFsc2UiCiAgICAgZml0LW1hcmdpbi10b3A9IjQiCiAgICAgZml0LW1hcmdpbi1sZWZ0PSI0IgogICAgIGZpdC1tYXJnaW4tcmlnaHQ9IjQiCiAgICAgZml0LW1hcmdpbi1ib3R0b209IjQiCiAgICAgaW5rc2NhcGU6em9vbT0iMy4zMzU3OTQ1IgogICAgIGlua3NjYXBlOmN4PSIxMjMuNDY1NjQiCiAgICAgaW5rc2NhcGU6Y3k9IjEwNy4xNjE4MiIKICAgICBpbmtzY2FwZTp3aW5kb3cteD0iMTI3NiIKICAgICBpbmtzY2FwZTp3aW5kb3cteT0iLTQiCiAgICAgaW5rc2NhcGU6d2luZG93LW1heGltaXplZD0iMSIKICAgICBpbmtzY2FwZTpjdXJyZW50LWxheWVyPSJzdmcyIiAvPgogIDxtZXRhZGF0YQogICAgIGlkPSJtZXRhZGF0YTQiPgpDcmVhdGVkIGJ5IHBvdHJhY2UgMS4xMSwgd3JpdHRlbiBieSBQZXRlciBTZWxpbmdlciAyMDAxLTIwMTMKPHJkZjpSREY+CiAgPGNjOldvcmsKICAgICByZGY6YWJvdXQ9IiI+CiAgICA8ZGM6Zm9ybWF0PmltYWdlL3N2Zyt4bWw8L2RjOmZvcm1hdD4KICAgIDxkYzp0eXBlCiAgICAgICByZGY6cmVzb3VyY2U9Imh0dHA6Ly9wdXJsLm9yZy9kYy9kY21pdHlwZS9TdGlsbEltYWdlIiAvPgogICAgPGRjOnRpdGxlPjwvZGM6dGl0bGU+CiAgPC9jYzpXb3JrPgo8L3JkZjpSREY+CjwvbWV0YWRhdGE+CiAgPHJlY3QKICAgICBzdHlsZT0iZmlsbDojY2YwMDAwO2ZpbGwtb3BhY2l0eToxO2ZpbGwtcnVsZTpldmVub2RkO3N0cm9rZTojMDAwMDAwO3N0cm9rZS13aWR0aDoxLjg1MDU4NzAxO3N0cm9rZS1saW5lam9pbjptaXRlcjtzdHJva2UtbWl0ZXJsaW1pdDo0O3N0cm9rZS1vcGFjaXR5OjE7c3Ryb2tlLWRhc2hhcnJheTpub25lIgogICAgIGlkPSJyZWN0Mjk5NSIKICAgICB3aWR0aD0iMTU4LjE0OTQxIgogICAgIGhlaWdodD0iMTIyLjIzOTQzIgogICAgIHg9IjQuMTI1MjkzNyIKICAgICB5PSI0LjEyNTI5MTMiIC8+CiAgPGcKICAgICB0cmFuc2Zvcm09Im1hdHJpeCgwLjA4MTM3Nzk5LDAsMCwtMC4wODEzNzc5OSw0LjE1ODk1ODQsMTI2LjMzMTA0KSIKICAgICBpZD0iZzYiCiAgICAgc3R5bGU9ImZpbGw6IzAwMDAwMDtzdHJva2U6bm9uZSI+CiAgICA8cGF0aAogICAgICAgZD0iTSAxLDExNzMgQyAyLDk5MiA1LDg1OSA3LDg3NSBjIDI0LDE5MyAxMTksMzk3IDI1Miw1MzggbCAzNSwzNyAzOCwtMzcgYyAyMSwtMjAgMzgsLTQwIDM4LC00NCAwLC01IC0xNiwtMjUgLTM2LC00NiBDIDExMiwxMDg2IDUzLDcwNSAxOTEsNDAyIDIyNywzMjEgMjc0LDI1MCAzMzUsMTgyIEwgMzgxLDEzMSAzNDksOTAgQyAzMzEsNjggMzEzLDUwIDMwOSw1MCAyOTIsNTAgMTgyLDE4NSAxMzMsMjY2IDc4LDM1OCAyNiw1MDQgMTEsNjA1IDUsNjQ3IDIsNTYzIDEsMzMzIEwgMCwwIDQ0MywxIGMgMjU3LDEgNDIyLDUgMzk1LDkgLTIyNCwzOCAtNDI3LDE4OCAtNTM2LDM5NSAtNjMsMTE5IC03NywxODQgLTc3LDM1MCAxLDEzNSAzLDE1OCAyNywyMjggMzcsMTExIDEwMCwyMTEgMTg3LDI5OCAxMTEsMTExIDIzNSwxNzggMzg2LDIwOSAyMyw0IC0xNDAsOCAtMzkyLDkgbCAtNDMzLDEgMSwtMzI3IHoiCiAgICAgICBpZD0icGF0aDgiCiAgICAgICBpbmtzY2FwZTpjb25uZWN0b3ItY3VydmF0dXJlPSIwIiAvPgogICAgPHBhdGgKICAgICAgIGQ9Im0gMTEwMCwxNDkzIGMgNDQsLTcgMTUzLC00NSAyMDUsLTcxIDE3OSwtODggMzE3LC0yNTAgMzgyLC00NDcgMjMsLTcxIDI2LC05NiAyNywtMjIwIDAsLTExOSAtMywtMTUxIC0yMywtMjE1IEMgMTYwNywyNjcgMTM3Myw1NiAxMTAyLDEwIDEwNzUsNiAxMjQwLDIgMTQ5OCwxIGwgNDQyLC0xIC0xLDM1MyBjIC0xLDIyMyAtNCwzMjggLTksMjg2IEMgMTkwNyw0NDIgMTgxNiwyNDUgMTY4MSw5OSBsIC00OSwtNTQgLTI3LDI1IGMgLTUyLDQ4IC01MSw1NCAxNCwxMzIgMjczLDMyNCAyNjcsODAzIC0xMywxMTE1IGwgLTQ1LDUwIDQzLDQyIDQzLDQyIDQzLC00OCBjIDEyOSwtMTQ1IDIxNywtMzQyIDI0MCwtNTMyIDUsLTQyIDgsNjAgOSwyODIgbCAxLDM0NyAtNDMyLC0xIGMgLTIzOCwtMSAtNDIyLC00IC00MDgsLTYgeiIKICAgICAgIGlkPSJwYXRoMTAiCiAgICAgICBpbmtzY2FwZTpjb25uZWN0b3ItY3VydmF0dXJlPSIwIiAvPgogICAgPHBhdGgKICAgICAgIGQ9Ik0gODEwLDEzNzQgQyA1ODAsMTMxNSAzOTIsMTEyMCAzNDAsODkwIDMyNSw4MjIgMzI4LDY2MiAzNDUsNTk1IDM5NCw0MDQgNTQ1LDIyOSA3MjYsMTU2IGMgMTg1LC03NSA0MTUsLTU2IDU4MSw0OSAxMjUsNzkgMjI3LDIwOSAyNzQsMzUwIDIxLDYwIDI0LDg4IDIzLDIwMCAtMSwxMTQgLTQsMTM5IC0yNywyMDQgLTc0LDIwNyAtMjQyLDM2MSAtNDUyLDQxNiAtNzQsMTkgLTI0MSwxOSAtMzE1LC0xIHogbSAzMDUsLTE5NiBjIDgxLC0xNiAxNDMsLTYwIDE3OCwtMTI4IDI3LC01MSAyOSwtNjIgMjUsLTE0MCAtNSwtMTE0IC00NCwtMTc3IC0xNDMsLTIyOCAtNDUsLTI0IC02MywtMjYgLTE5MiwtMzAgbCAtMTQzLC00IDAsLTE2NCAwLC0xNjQgLTU1LDAgLTU1LDAgMCw0MzUgMCw0MzUgMTYzLDAgYyA5MCwwIDE5MCwtNSAyMjIsLTEyIHoiCiAgICAgICBpZD0icGF0aDEyIgogICAgICAgaW5rc2NhcGU6Y29ubmVjdG9yLWN1cnZhdHVyZT0iMCIgLz4KICAgIDxwYXRoCiAgICAgICBkPSJtIDg0MCw5MjAgMCwtMTYwIDEyMCwwIGMgMTM5LDAgMTY0LDkgMjEwLDcyIDU3LDc4IDMzLDE4OSAtNTAsMjMwIC0yOCwxNCAtNjEsMTggLTE1NywxOCBsIC0xMjMsMCAwLC0xNjAgeiIKICAgICAgIGlkPSJwYXRoMTQiCiAgICAgICBpbmtzY2FwZTpjb25uZWN0b3ItY3VydmF0dXJlPSIwIiAvPgogIDwvZz4KPC9zdmc+Cg=="
});

  function qsa(selector) {
    return Array.from(documentRef.querySelectorAll(selector));
  }

  function stateClass(value) {
    if (value === true) return "is-active";
    if (value === false) return "is-inactive";
    return "is-unknown";
  }

  function stateText(value) {
    if (value === true) return "on";
    if (value === false) return "off";
    return "unknown";
  }

  function sr(text) {
    return `<span class="openmmi-telltale-sr">${text}</span>`;
  }

  function icon(kind, value, label, src) {
    const status = stateText(value);
    const title = `${label}: ${status}`;
    return `<span class="openmmi-telltale openmmi-telltale-${kind} ${stateClass(value)}" role="img" aria-label="${title}" title="${title}">` +
      `<img src="${src}" alt="" aria-hidden="true" loading="eager" decoding="async" draggable="false" onerror="this.closest('.openmmi-telltale').classList.add('icon-load-failed')">` +
      sr(title) +
      `</span>`;
  }

  function markHost(node, kind, label) {
    node.classList.add("openmmi-telltale-value", `openmmi-telltale-value-${kind}`);
    node.setAttribute("aria-label", label);
    const host = node.closest(".tile, .footer-item");
    if (host) {
      host.classList.add("openmmi-telltale-host", `openmmi-telltale-host-${kind}`);
      host.setAttribute("title", label);
    }
  }

  function setNodeIcon(selector, kind, value, label, src) {
    qsa(selector).forEach((node) => {
      const signature = `${kind}:${stateText(value)}`;
      if (setHtml(node, icon(kind, value, label, src), signature)) {
        markHost(node, kind, `${label}: ${stateText(value)}`);
      }
    });
  }

  function indicatorLabel(lighting) {
    if (lighting.hazards === true) return "Hazard warning lights";
    if (lighting.left_indicator === true && lighting.right_indicator === true) return "Both direction indicators";
    if (lighting.left_indicator === true) return "Left direction indicator";
    if (lighting.right_indicator === true) return "Right direction indicator";
    if (lighting.left_indicator === false || lighting.right_indicator === false) return "Direction indicators off";
    return "Direction indicators unknown";
  }

  function setIndicators(lighting) {
    const hazards = lighting.hazards === true;
    const left = hazards ? true : lighting.left_indicator;
    const right = hazards ? true : lighting.right_indicator;
    const label = indicatorLabel(lighting);

    qsa('[data-field="indicators"]').forEach((node) => {
      const html =
        `<span class="openmmi-indicator-pair ${hazards ? "is-hazard" : ""}">` +
          icon("left-turn", left, "Left direction indicator", ICONS.leftTurn) +
          icon("right-turn", right, "Right direction indicator", ICONS.rightTurn) +
        `</span>` + sr(label);
      if (setHtml(node, html, `${stateText(left)}:${stateText(right)}:${hazards}`)) {
        markHost(node, "indicators", label);
      }
    });
  }

  function applyInlineDataTelltales(payload) {
    const state = (payload && payload.state) || {};
    const vehicle = state.vehicle || {};
    const lighting = state.lighting || {};

    setIndicators(lighting);
    setNodeIcon('[data-bool="handbrake"]', "parking-brake", vehicle.handbrake, "Parking brake", ICONS.parkingBrake);
    setNodeIcon('[data-bool="hazards"]', "hazard", lighting.hazards, "Hazard warning", ICONS.hazard);
    setNodeIcon('[data-bool-no="bulb_out"]', "bulb-failure", lighting.bulb_out, "Exterior bulb failure", ICONS.bulbFailure);
  }
      return applyInlineDataTelltales;
    })();

    const applyCoolantAndVoltageFixes = (() => {
  function qsa(selector) {
    return Array.from(documentRef.querySelectorAll(selector));
  }

  function asNumber(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function voltageState(voltage) {
    if (voltage === null) return "unknown";
    if (voltage < 11.8 || voltage > 15.2) return "danger";
    if (voltage < 12.2 || voltage > 14.9) return "warn";
    return "normal";
  }

  function coolantState(tempC) {
    if (tempC === null) return "unknown";
    if (tempC >= 112) return "hot";
    if (tempC <= 60) return "cold";
    return "normal";
  }

  function batterySvg() {
    return '' +
      '<svg class="openmmi-voltage-icon" viewBox="0 0 96 64" role="img" aria-hidden="true" focusable="false">' +
        '<path d="M18 18H78c4.4 0 8 3.6 8 8v12c4.4 0 8 3.6 8 8s-3.6 8-8 8v-2c0 4.4-3.6 8-8 8H18c-4.4 0-8-3.6-8-8V26c0-4.4 3.6-8 8-8Z" fill="none" stroke="currentColor" stroke-width="7" stroke-linejoin="round"/>' +
        '<path d="M31 32v14M24 39h14M61 39h14" fill="none" stroke="currentColor" stroke-width="7" stroke-linecap="round"/>' +
      '</svg>';
  }

  function updateVoltageTellTale(payload) {
    const state = (payload && payload.state) || {};
    const electrical = state.electrical || {};
    const voltage = asNumber(electrical.supply_voltage_v ?? electrical.terminal30_voltage_v);
    const status = voltageState(voltage);

    const text = voltage === null ? "--" : formatNumber(voltage, 1);
    qsa('[data-field="voltage_v"]').forEach((node) => {
      node.classList.add("openmmi-voltage-field");
      setHtml(
        node,
        '<span class="openmmi-voltage-readout is-' + status + '" title="Supply voltage: ' + text + ' V">' +
          batterySvg() +
          '<span class="openmmi-voltage-number">' + text + '</span>' +
        '</span>',
        `voltage:${status}:${text}`,
      );

      const tile = node.closest('.tile');
      if (tile) {
        tile.classList.toggle('openmmi-voltage-warn', status === 'warn');
        tile.classList.toggle('openmmi-voltage-danger', status === 'danger');
      }
    });
  }

  function updateCoolantGauge(payload) {
    const state = (payload && payload.state) || {};
    const engine = state.engine || {};
    const tempC = asNumber(engine.coolant_temp_c);
    const status = coolantState(tempC);

    qsa('.temp-bar').forEach((bar) => {
      bar.classList.add('openmmi-coolant-gauge');
      bar.classList.remove('is-cold', 'is-normal', 'is-hot', 'is-unknown');
      bar.classList.add('is-' + status);

      if (tempC === null) {
        if (bar.style.getPropertyValue?.('--coolant-pos')) { bar.style.removeProperty('--coolant-pos'); metrics.dom_writes += 1; }
        setAttribute(bar, 'title', 'Coolant temperature: unknown');
        return;
      }

      // The printed scale is 50 / 90 / 130 °C, so map the marker to that range.
      const percent = clamp(((tempC - 50) / 80) * 100, 0, 100);
      const position = percent.toFixed(1) + '%';
      if (bar.style.getPropertyValue?.('--coolant-pos') !== position) { bar.style.setProperty('--coolant-pos', position); metrics.dom_writes += 1; }
      setAttribute(bar, 'title', 'Coolant temperature: ' + formatTempFromC(tempC, 0, readSettings()) + ' ' + tempUnitLabel(readSettings()));
    });
  }

  function applyCoolantAndVoltageFixes(payload) {
    updateVoltageTellTale(payload || {});
    updateCoolantGauge(payload || {});
  }
      return applyCoolantAndVoltageFixes;
    })();

    function render(payload = {}) {
      metrics.render_calls += 1;
      const settings = readSettings();
      const view = buildViewModel(payload, settings);
      updateHealth(payload);
      const state = payload.state || {};
      const renderSignature = JSON.stringify({
        fields: view.fields,
        booleans: view.booleans,
        doors: view.doors,
        anyDoorOpen: view.anyDoorOpen,
        tachRpm: view.tachRpm,
        fuelLowWarning: view.fuelLowWarning,
        units: view.units,
        lighting: state.lighting || {},
        coolantTempC: state.engine?.coolant_temp_c ?? null,
        supplyVoltageV: state.electrical?.supply_voltage_v ?? state.electrical?.terminal30_voltage_v ?? null,
      });
      if (renderSignature === lastRenderSignature) {
        metrics.unchanged_renders_skipped += 1;
        return view;
      }
      lastRenderSignature = renderSignature;
      metrics.vehicle_renders += 1;

      Object.entries(view.fields).forEach(([name, value]) => {
        if (!enhancementsEnabled || !enhancedFields.has(name)) setField(name, value);
      });
      updateFuelWarning(view.fuelLowWarning);
      applyUnitLabels(view.units);
      const tellTaleTest = root && root.openMmiApplyTellTaleTest;
      if (typeof tellTaleTest === "function") tellTaleTest();

      if (!enhancementsEnabled || !enhancedBooleans.has("handbrake")) setBoolean("handbrake", view.booleans.handbrake);
      setBoolean("reverse", view.booleans.reverse);
      setBoolean("rear_heater", view.booleans.rear_heater);
      setBoolean("recirculation", view.booleans.recirculation);
      setBoolean("compressor", view.booleans.compressor);
      if (!enhancementsEnabled || !enhancedBooleans.has("hazards")) setBoolean("hazards", view.booleans.hazards);
      if (!enhancementsEnabled || !enhancedBooleans.has("bulb_out")) setBooleanYesNo("bulb_out", view.booleans.bulb_out);

      DOORS.forEach((name) => updateDoor(name, view.doors[name]));
      queryAll(".car-shell").forEach((node) => { node.classList.toggle("any-open", view.anyDoorOpen); });
      updateTach(view.tachRpm);

      if (enhancementsEnabled) {
        applyInlineDataTelltales(payload);
        applyCoolantAndVoltageFixes(payload);
      }
      return view;
    }

    function scheduleDriverFacingCleanup() {
      const callback = () => { try { applyDriverFacingCleanup(); } catch (_) {} };
      if (typeof root?.requestAnimationFrame === "function") root.requestAnimationFrame(callback);
      else callback();
    }

    documentRef?.addEventListener?.("DOMContentLoaded", scheduleDriverFacingCleanup);
    root?.addEventListener?.("openmmi:settingsrender", scheduleDriverFacingCleanup);
    scheduleDriverFacingCleanup();

    function getMetrics() {
      return Object.freeze({ ...metrics });
    }

    return Object.freeze({
      getMetrics,
      render,
      updateHealth,
      updateTach,
      applyInlineDataTelltales,
      applyCoolantAndVoltageFixes,
      readSettings,
    });
  }

  return Object.freeze({
    DOORS,
    DEFAULT_SETTINGS,
    formatNumber,
    formatSpeedFromKmh,
    formatDistanceFromKm,
    formatTempFromC,
    speedUnitLabel,
    distanceUnitLabel,
    tempUnitLabel,
    booleanText,
    booleanYesNoText,
    indicatorLabel,
    buildViewModel,
    createRenderer,
  });
});
