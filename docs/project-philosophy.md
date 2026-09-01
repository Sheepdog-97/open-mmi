# Project philosophy

`open-mmi` exists to make vehicle integration knowledge reusable.

Too much CAN-bus reverse-engineering is rediscovered privately, lost in forum threads, or locked inside one-off products. `open-mmi` aims to provide a shared Linux-based layer where vehicle profiles, decoded states, and dashboard/action integrations can be maintained openly.

## Goals

- keep vehicle integration local-first
- keep vehicle profiles reusable
- translate every vehicle into one shared canonical event and status vocabulary
- translate bindings into one shared action vocabulary rather than Python implementation names
- keep the core daemon small and boring
- keep vehicle-specific CAN knowledge out of core Python
- make it possible to add vehicle support without reinventing the whole project
- provide useful status/state output for multiple UI consumers
- support right-to-repair and owner-controlled vehicle integration

## Non-goals

`open-mmi` is not intended to become:

- a locked commercial head unit
- a proprietary vehicle-profile marketplace
- a cloud-required vehicle service
- a paywalled CAN database
- a safety-critical replacement for OEM warnings or diagnostics
- a drivetrain/body-control project

## Community model

The project should allow different levels of contribution:

- documentation fixes
- install notes
- screenshots and example output
- CAN captures
- vehicle decode notes
- vehicle profiles
- action modules
- dashboard/UI consumers
- tests and tooling

A contributor should not need to understand the whole daemon to help with one vehicle, one status mapping, or one UI consumer.

The canonical event, status and action registries are continuity checkpoints, not a walled
garden. Contributors may share raw evidence and provisional interpretations freely. A
confirmed signal only needs to cross into the shared human-readable vocabulary when it
becomes part of a maintained profile. A genuinely new event, status or local action may be
proposed with its mapping or implementation in that same pull request.

See [`vehicle-contribution-workflow.md`](vehicle-contribution-workflow.md).

## Core and edges

The core should be conservative.

The edges should be flexible.

```text
core daemon:
  small
  stable
  passive-first
  profile-driven
  boring

vehicle profiles:
  reusable
  reviewable
  manufacturer-specific at the CAN boundary
  canonical at the event/status boundary
  marked experimental/stable
  kept out of core Python

bindings/actions:
  human-readable at the binding boundary
  registry-backed rather than module/function-backed
  flexible and user-controlled
  replaceable behind stable action identifiers
  built on decoded events and state, not raw CAN
```

## Trust boundaries

Open MMI should make important trust boundaries visible in code and documentation rather than relying only on maintainer intent. Local-first, passive-first and owner-controlled behavior are therefore design constraints as well as project values.

A maintainer signature establishes provenance; it does not by itself authorize a silent expansion of an owner's established trust boundary. Boundaries may be redrawn when the project has a legitimate reason to do so, but material changes must be declared and, where the architecture can enforce it, require an owner-visible transition.

The project should prefer mechanisms that make trustworthy behavior independently reviewable and make undeclared capability expansion conspicuous. System integrators and downstream distributions may extend Open MMI, but additional behavior should be clearly distinguishable from the guarantees made by upstream Open MMI.

See [`trust-architecture.md`](trust-architecture.md).

## Evidence without gatekeeping

Open MMI does not require permission to investigate a vehicle. Maintained profiles do require explicit identity, maturity, test scope and evidence because users deserve to know what a compatibility claim means. The conformance report is an honesty and continuity checkpoint, not a walled garden.
