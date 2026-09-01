# Open MMI documentation

This index separates normal vehicle-owner instructions from administrator,
contributor, developer, generated-reference, and historical design material.
Start with the shortest path that matches your role.

## Vehicle owners and first-time users

- [Getting started](getting-started.md) — install, open the desktop application,
  select a maintained vehicle, and understand the normal update path.
- [Web dashboard](dashboard.md) — pages, Settings, diagnostics, recovery, and UI behavior.
- [Media sources](media-sources.md) — Jellyfin, Internet Radio, USB, and Bluetooth.
- [Vehicle setup](vehicle-setup.md) — configured, draft, and loaded state;
  profile/bindings selection; review; Apply; custom copies; adapter health.
- [Troubleshooting](troubleshooting.md) — launcher, authorization, dashboard,
  Vehicle Setup, CAN reception, updates, and logs.
- [Demo mode](demo-mode.md) — run the dashboard without a vehicle.
- [Vehicle tablet installation and cooling](vehicle-tablet-installation.md) —
  physical installation and thermal guidance.

## Advanced operators and recovery

- [Manual administration](manual-administration.md) — terminal equivalents,
  managed source updates, channel policy, service controls, manual profile
  application, terminal UI, logs, uninstall, and important file locations.
- [Desktop shell](desktop-shell.md) — launcher and managed browser behavior.
- [Runtime hardening](runtime-hardening.md) — cache, service, interface, thermal,
  and runtime-recovery behavior.
- [Profile and bindings ownership](profile-ownership.md) — maintained/custom
  ownership, canonical selection, sacred files, and advanced explicit overrides.
- [Status snapshot](status-snapshot.md) — the persistent status interface.

## Vehicle integration contributors

- [Vehicle contribution workflow](vehicle-contribution-workflow.md) — the full
  path from passive captures to maintained-profile review.
- [Vehicle profile scaffolding](vehicle-profile-scaffolding.md)
- [Vehicle capture analysis](vehicle-capture-analysis.md)
- [Maintained profile standard](maintained-profile-standard.md)
- [Vehicle qualification workflow](vehicle-qualification-workflow.md)
- [Vehicle profiles](vehicle-profiles.md)
- [Vehicle integration standard](vehicle-integration-standard.md)
- [CAN bus model](can-bus-model.md)
- [Compatibility testing](compatibility-testing.md)

## Generated reference

These files are generated from checked machine-readable registries or catalogue
metadata. Do not edit them by hand.

- [Maintained vehicle catalogue](vehicle-catalogue.md)
- [Vehicle capability matrix](vehicle-capability-matrix.md)
- [Vehicle event registry](vehicle-event-registry.md)
- [Vehicle status registry](vehicle-status-registry.md)
- [Vehicle action registry](vehicle-action-registry.md)

Regenerate or verify them with:

```bash
python tools/generate_vehicle_action_docs.py --check
python tools/generate_vehicle_event_docs.py --check
python tools/generate_vehicle_status_docs.py --check
python tools/generate_vehicle_catalogue_docs.py --check
```

## Developers and maintainers

- [Contributing](../CONTRIBUTING.md)
- [Branch workflow](branch-workflow.md) — Nightly, Beta, Main, promotions, and hotfix propagation.
- [Project philosophy](project-philosophy.md)
- [Trust architecture](trust-architecture.md) — machine-readable trust boundaries, assurance levels, and the future update-continuity model.
- [Performance testing](performance-testing.md)
- [Release checklist](release-checklist.md)
- [Versioning](versioning.md)
- [V1 foundation migration](v1-foundation-migration.md)
- [V1 roadmap](V1_ROADMAP.md) — planning record; current behavior belongs in
  the user/operator documents above.

## Historical design records

The [design index](design/README.md) contains milestone records explaining why
features were planned and how they were qualified. Design records are not the
primary place for current user instructions. Each implemented design set should
point to the permanent document that owns its current behavior.
