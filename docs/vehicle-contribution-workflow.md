# Vehicle signal contribution workflow

> **The registry is a continuity checkpoint, not a walled garden.**

Open MMI exists to turn vehicle-specific hexadecimal data into shared human meaning.
The project does not require every contributor to ask permission before researching a CAN
signal, and it does not reserve new vehicle concepts for maintainers. Anyone may discover,
document, test and propose support for a signal.

The registry checkpoint exists for one reason: two vehicles that express the same human
concept should use the same Open MMI name. Without that checkpoint, the project would
slowly develop hundreds of private dialects for common controls and states.

```text
Vehicle-specific hexadecimal data
        ↓ profile decoding
Shared human-readable meaning
        ↓ bindings and status consumers
Application behaviour
```

Open MMI is where hex meets human form. It must not become a place where hex is merely
renamed into another obscure machine vocabulary.

## What remains open during discovery

Raw CAN research is intentionally unrestricted. A contributor may record and share:

- CAN identifiers, bytes, masks and observed values;
- timestamps and the physical actions performed;
- manufacturer terminology such as PDC, BCM or MFSW;
- provisional names and uncertain interpretations;
- captures, replay files, spreadsheets and decoder notes; and
- signals that are not yet understood well enough to become a maintained mapping.

An observation does not need a finished canonical name before it can be discussed. Issue
and capture submissions may use labels such as `suspected PDC byte` or `unknown 0x431
change` while investigation continues. Uncertainty must be stated clearly rather than
hidden behind a confident-looking name.

The canonical requirement begins when a signal is proposed for a maintained or
distributable profile.

## The maintained-profile checkpoint

Before merge, each decoded signal must answer three questions:

1. **What does this mean to a person?**
2. **Is it a momentary event or persistent vehicle status?**
3. **Does the shared registry already describe that meaning?**

If an existing descriptor fits, reuse it and change only the vehicle-specific CAN mapping.
If none fits, propose a new universal descriptor in the same pull request as the profile.
No separate permission request is required.

The pull request is the review point where contributors and maintainers confirm that the
name is understandable, reusable and technically complete.

## Reuse: different CAN data, same human meaning

Seat may encode a steering-wheel mute request like this:

```json
{
  "id": "0x5C1",
  "byte": 0,
  "value": 43,
  "event": "mute_toggle"
}
```

Another vehicle may encode the same request like this:

```json
{
  "id": "0x431",
  "byte": 2,
  "value": 17,
  "event": "mute_toggle"
}
```

Only the CAN ID, byte and value change. The human meaning stays `mute_toggle`.
A contributor should be able to find the Seat example, recognise the same physical intent,
and substitute the CAN details discovered in their own vehicle.

The binding remains independent of both vehicles and of implementation details:

```json
{
  "mute_toggle": {
    "action": "media.mute.toggle"
  }
}
```

The profile says what happened in human terms. The binding selects a human-readable local
behavior. The action registry privately maps that behavior to the current implementation.

## Propose: genuinely new human meaning

A name such as `PDC_signal` is useful as a discovery note, but it is not yet a complete
canonical contract. It preserves an abbreviation and does not reveal whether the frame
represents a button press, a distance, an enabled state or a warning.

Further evidence might show that the signal is:

- an event such as `parking_assist_toggle`;
- a persistent distance such as `parking.distance.rear_left`;
- an active state such as `parking.assist.active`; or
- several separate signals rather than one concept.

The canonical status registry now records human-readable paths, value types, units,
nullability and lifecycle. An English-speaking reader should understand the human concept
without knowing the source CAN ID or manufacturer acronym.

A genuinely new event or status may be added to its registry in the same pull request as
its first vehicle mapping. That pull request should include:

- a clear title and plain-English description;
- event versus persistent-status classification;
- payload type, range and unit where applicable;
- delivery behaviour such as edge, repeatable or value;
- an explanation of why no existing descriptor fits;
- the vehicle-specific CAN rule;
- tests and regenerated documentation; and
- real-vehicle or replay evidence, with uncertainties stated.

## From event to action

Events and actions are deliberately separate vocabularies:

```text
mute_toggle          = what happened
media.mute.toggle     = what Open MMI should do
actions.audio...      = private implementation detail
```

A contributor or user can search actions using ordinary wording:

```bash
open-mmi-config vehicle-setup actions --search "audio mute"
open-mmi-config vehicle-setup actions --check media.mute.toggle
```

When no action fits, a contributor may propose a new universal behavior together with its
implementation, argument contract, payload contract, requirements, tests and generated
documentation in the same pull request. No separate permission request is required.
Maintained bindings may not invent module/function dialects; existing custom legacy bindings
continue to work while users migrate.

## Human-readable naming test

A canonical name should be understandable without knowledge of:

- the vehicle manufacturer or model;
- the original CAN identifier, byte or value;
- an ECU/module abbreviation;
- a Python module or function;
- the action chosen by one user; or
- the reverse-engineering history of the signal.

Good event names express universal intent:

```text
mute_toggle
volume_up
next_track
parking_assist_toggle
```

Good status paths express a human-readable subject and state using the shared registry:

```text
doors.front_right
parking.distance.rear_left
climate.outside_temp_regulation_c
vehicle.speed_kmh
```

Names that remain machine- or vendor-specific are not ready for the maintained boundary:

```text
PDC_signal
manufacturer_door_byte_3
seat_5c1_value_43
BCM_status_2
sound_module_off
```

This is a naming and continuity review, not a judgement on the value of the discovery.

## Event or status?

Use an **event** for a momentary request or transition that consumers react to:

```text
mute_toggle
volume_up
parking_assist_toggle
```

Use **status** for a value that remains meaningful between frames and can be read by a
dashboard or another consumer:

```text
vehicle speed
door position
parking distance
outside temperature
```

Do not force a persistent value into an event merely because the first proof of concept used
a button/action pipeline. Likewise, do not model a one-shot button request as persistent
state unless there is a clear state contract.

## Search, check, then map

Search the registry using ordinary human terms:

```bash
open-mmi-config vehicle-setup events --search mute
open-mmi-config vehicle-setup events --search "audio volume"
open-mmi-config vehicle-setup statuses --search "right door"
open-mmi-config vehicle-setup statuses --search pdc_signal
open-mmi-config vehicle-setup actions --search "audio mute"
```

Check a proposed identifier:

```bash
open-mmi-config vehicle-setup events --check mute_toggle
open-mmi-config vehicle-setup events --check pdc_signal
open-mmi-config vehicle-setup statuses --check doors.front_right
open-mmi-config vehicle-setup statuses --check pdc_signal
open-mmi-config vehicle-setup actions --check media.mute.toggle
```

The check command does not grant or deny permission. It explains whether to reuse an
existing event, migrate a deprecated alias, improve an invalid identifier, or propose a new
universal concept. Event checking also points toward status candidates when wording appears
to describe persistent vehicle state.

Inspect exact canonical definitions:

```bash
open-mmi-config vehicle-setup events mute_toggle
open-mmi-config vehicle-setup statuses parking.distance.rear_left
open-mmi-config vehicle-setup actions media.mute.toggle
```

## Compare captures without inventing meaning

Use the bounded research tooling to normalize common `candump` logs and compare a quiet
baseline with a capture taken during one documented physical action:

```bash
open-mmi-config vehicle-setup capture normalize captures/action.log
open-mmi-config vehicle-setup capture compare captures/before.log captures/after.log
```

The comparison highlights changed IDs, bytes and bits. It does not identify the human
meaning, distinguish causation from counters or unrelated traffic, or promote a mapping.
Candidate replay export deliberately leaves event and status expectations empty and refuses
to write under `vehicles/`. See [`vehicle-capture-analysis.md`](vehicle-capture-analysis.md).


## Preserve related-vehicle leads without claiming support

When a maintained sibling profile provides a likely mapping but the target vehicle has not
produced enough local truth, preserve the lead in
`notes/candidate_mappings.v1.json` instead of copying it into the active profile. The
structured candidate contains the exact canonical status rule, source profiles, confidence,
local evidence and the steps needed to verify it.

Candidate files explicitly have no runtime authority. They do not publish state, do not
count toward replay/capability totals and are shown separately in generated documentation.
After real verification, promote the rule into `config.json`, add a replay fixture and remove
the candidate entry. This is the preferred way to cross-pollinate related platforms quickly
without turning correlation into an implicit support claim.

## Contribution sequence

```text
Capture raw CAN evidence
        ↓
Record provisional observations openly
        ↓
Confirm the human meaning
        ↓
Classify event versus persistent status
        ↓
Search the event/status vocabulary
        ↓
Select or propose the canonical local action when a binding is needed
        ↓
Reuse an existing descriptor
        or propose a new universal descriptor
        ↓
Submit mapping, registry change if needed, tests and evidence together
```

The registry protects continuity across vehicles. It does not restrict who may expand the
project.

## Promotion is a reviewed transition

After capture analysis and deterministic fixtures are complete, use the formal
[`none → replay → hardware` qualification workflow](vehicle-qualification-workflow.md).
Promotion cannot skip replay, hardware claims must state tested equipment and variants, and stale
reviews produce warnings until maintainers renew or explicitly demote the claim.
