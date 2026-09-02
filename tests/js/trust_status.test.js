"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");

const trust = require("../../ui/web_dashboard/static/trust-status.js");

test("known backend trust statuses are preserved", () => {
  for (const status of ["PASS", "FAIL", "UNVERIFIED"]) {
    assert.equal(trust.normalizeStatus(status), status);
  }
});

test("unknown trust status becomes UNVERIFIED", () => {
  assert.equal(trust.normalizeStatus("GREEN"), "UNVERIFIED");
  assert.equal(trust.normalizeStatus(null), "UNVERIFIED");
});

test("model exposes manifest owner and telemetry evidence", () => {
  const model = trust.buildModel({
    status: "PASS",
    report: {
      manifest: {
        available: true,
        policy_generation: 6,
        digest: "sha256:manifest",
        capabilities: {
          "vehicle.can.transmit": {
            policy: "prohibited",
            assurance: "os-enforced",
          },
          "network.external-egress": {
            policy: "declared-purposes-only",
            assurance: "os-enforced",
            purposes: ["media.internet-radio", "updates.release-fetch"],
          },
        },
      },
      telemetry_authorization: {
        authorized: false,
      },
      checks: [
        {
          id: "owner.accepted-release-state",
          status: "PASS",
          summary: "Accepted owner state is valid.",
          evidence: {
            established: true,
            accepted_generation: 6,
            accepted_manifest_digest: "sha256:accepted",
            current_relation: "equivalent",
          },
        },
      ],
    },
    error: null,
  });

  assert.equal(model.status, "PASS");
  assert.equal(model.manifest.generation, 6);
  assert.equal(model.manifest.digest, "sha256:manifest");
  assert.equal(model.ownerTrust.established, true);
  assert.equal(model.ownerTrust.generation, 6);
  assert.equal(model.ownerTrust.currentRelation, "equivalent");
  assert.equal(model.telemetry.authorized, false);

  assert.deepEqual(
    model.manifest.capabilities.map((capability) => capability.id),
    ["network.external-egress", "vehicle.can.transmit"],
  );
});

test("missing report remains visibly UNVERIFIED", () => {
  const html = trust.renderPayload({
    status: "UNVERIFIED",
    report: null,
    error: "Trust inspection evidence is unavailable.",
  });

  assert.match(html, /Overall/);
  assert.match(html, /UNVERIFIED/);
  assert.match(html, /Trust inspection evidence is unavailable/);
});

test("rendered trust surface contains no mutation controls", () => {
  const html = trust.renderPayload({
    status: "FAIL",
    report: {
      manifest: {
        available: false,
        capabilities: {},
      },
      telemetry_authorization: {},
      checks: [],
    },
  });

  assert.match(html, /FAIL/);
  assert.doesNotMatch(html, /<button/i);
  assert.doesNotMatch(html, /acknowledge/i);
  assert.doesNotMatch(html, /approve/i);
});

test("frontend trust module uses GET-only local endpoint", () => {
  const source = fs.readFileSync(
    "ui/web_dashboard/static/trust-status.js",
    "utf8",
  );

  assert.match(source, /const ENDPOINT = "\/api\/trust\/status"/);
  assert.match(source, /api\.getJson\(ENDPOINT\)/);

  for (const forbidden of [
    "postJson",
    "localStorage",
    "sessionStorage",
    "http://",
    "https://",
    "accepted_state",
    "transition_gate",
    "activate_acknowledged_expansion",
  ]) {
    assert.equal(
      source.includes(forbidden),
      false,
      `unexpected trust UI authority/dependency: ${forbidden}`,
    );
  }
});
