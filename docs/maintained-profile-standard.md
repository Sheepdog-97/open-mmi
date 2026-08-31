# Maintained vehicle-profile standard

A maintained Open MMI vehicle profile is more than a decoder that happens to load. It is a
versioned, reviewable compatibility claim about a real vehicle family.

> **This is an admission and continuity checkpoint, not a walled garden.**
>
> Raw CAN discovery, notes, captures and custom profiles remain open. No contributor needs
> permission to investigate a vehicle. The extra requirements begin only when a profile is
> proposed for the maintained catalogue distributed by Open MMI.

The standard answers four user-facing questions:

1. **What vehicle is this profile for?**
2. **How mature is the integration?**
3. **What exactly has been tested?**
4. **Where is the reviewable evidence?**

The machine-readable envelope is described by
`canbusd/data/vehicle-profile.v1.schema.json`. Registry and decoder semantics remain enforced
by the normal profile validator.

## Required envelope

A maintained profile starts with:

```json
{
  "schema_version": 1,
  "metadata": {
    "id": "seat-leon-1p-pq35",
    "display_name": "SEAT Leon Mk2 / 1P (PQ35)",
    "manufacturer": "SEAT",
    "model": "Leon",
    "generation": "1P",
    "platform": "VAG PQ35",
    "market_aliases": [],
    "model_years": {
      "from": 2005,
      "to": 2012
    },
    "maturity": "qualified",
    "license": "GPL-3.0-only",
    "maintainers": [
      "Open MMI contributors"
    ],
    "qualification": {
      "level": "hardware",
      "last_tested": "2026-07-20",
      "scope": [
        "Passive infotainment CAN reception at 100 kbit/s"
      ],
      "evidence": [
        {
          "kind": "hardware",
          "path": "docs/design/v1-vehicle-setup/qualification.md",
          "description": "Maintainer hardware qualification record."
        }
      ]
    },
    "limitations": [
      "Only the infotainment CAN bus is qualified."
    ]
  },
  "default_bus": "infotainment",
  "can_buses": {},
  "rules": [],
  "presence": [],
  "status": []
}
```

`metadata.id` must match the stable identity declared in `vehicles/catalogue.v1.json`.
For user-facing consistency, `metadata.display_name` uses the form
`Manufacturer Model market-generation / type-code (platform-code)` when both a familiar
market generation and an OEM type/chassis code are known, for example
`Volkswagen Passat B6 / 3C (PQ46)`. The catalogue maps the stable ID to
`vehicles/<brand>/<model>/<generation-platform>/config.json` and may retain deprecated IDs
for installed compatibility. Optional `market_aliases` records compatible regional or
rebadged names without duplicating a profile solely for branding. Evidence paths are
repository-relative and must resolve to regular files in the same source tree.

## Maturity levels

| Maturity | Meaning | Minimum evidence |
| --- | --- | --- |
| `experimental` | Useful maintained work whose interpretation or coverage is still changing. | Qualification may be `none`; limitations must remain honest. |
| `candidate` | Canonical semantics and deterministic testing are ready for broader qualification. | Replay or hardware qualification and corresponding evidence. |
| `qualified` | The stated scope has passed real-vehicle hardware testing. | Hardware qualification, date, scope and at least one hardware evidence record. |
| `deprecated` | Retained for migration or historical compatibility. | Existing evidence remains visible; replacement guidance belongs in limitations/docs. |

A profile maturity label describes the overall integration. Individual status registry entries
may still be `experimental` or `diagnostic`, and those limitations must not be presented as
fully stable capabilities.

## Qualification levels

- `none` — no formal replay or hardware claim; `last_tested` is `null` and scope/evidence are empty.
- `replay` — deterministic fixture coverage is complete for the stated scope and the reviewed evidence is named.
- `hardware` — the stated compatibility scope was formally qualified against a real vehicle; the date and hardware evidence are named.

Evidence kinds are `research`, `capture`, `replay`, `hardware`, and `documentation`. A replay-qualified
candidate may also cite `capture` evidence from a real vehicle. That records where the mappings came
from without implying generation-wide hardware qualification; the formal level remains `replay` until
the separate hardware-qualification boundary is reviewed and promoted.

## One admission command

Check the complete maintained catalogue from a source checkout:

```bash
open-mmi-config vehicle-setup conform --root .
```

Check one profile:

```bash
open-mmi-config vehicle-setup conform --root . seat-leon-1p-pq35
```

The command verifies:

- the versioned metadata envelope;
- recursive catalogue path, stable profile identity, and legacy-alias agreement;
- maturity/qualification consistency;
- evidence paths and files;
- CAN bus metadata and decoder structure;
- canonical event and status contracts;
- deterministic `fixtures/mappings.v1.json` replay with complete event/status coverage;
- optional structured non-runtime candidates in `notes/candidate_mappings.v1.json`;
- a capability inventory derived from the profile rather than handwritten claims.

CI runs the same complete-catalogue command. A failed report blocks admission to the maintained
catalogue; it does not block discovery notes or local custom-profile use.

## Catalogue layout and replay proof

The maintained tree is organised for humans:

```text
vehicles/<brand>/<model>/<generation-platform>/
├── config.json
├── README.md
├── fixtures/mappings.v1.json
├── evidence/
└── notes/
    └── candidate_mappings.v1.json   # optional, non-runtime research queue
```

The folder explains where a vehicle belongs. The profile ID remains the stable
machine contract and therefore does not need to mirror every path component.
Existing IDs can be retained as deprecated aliases while the maintained tree is
reorganised.


### Structured candidate mappings

Related vehicles often provide enough evidence to make a mapping worth preserving before it
is safe to claim as a runtime capability. An optional
`notes/candidate_mappings.v1.json` file records those leads without adding them to
`config.json`. The envelope must set `runtime_authority` to `false`; conformance validates
that each candidate is a canonical, technically valid status rule and that none of its
outputs are already active capabilities.

Candidate confidence (`weak`, `moderate`, or `strong`) describes the quality of the lead,
not support status. Generated catalogue/matrix documentation lists candidates separately.
`canbusd`, replay coverage totals, dashboard state and canonical capability counts ignore
these files entirely.

Promotion is explicit: verify the candidate on the target vehicle, move or merge the rule
into `config.json`, add deterministic replay coverage, remove the candidate entry, and
update the capture/evidence note. This makes cross-platform verification fast without
allowing likely-but-unverified mappings to acquire runtime authority by accident.

Replay one profile, using either its canonical ID or a legacy alias:

```bash
open-mmi-config vehicle-setup replay --root . seat-leon-1p-pq35
open-mmi-config vehicle-setup replay --root . seat_1p
```

Fixture cases contain exact CAN bytes and expected canonical outputs. A mapping
change that alters an event, scale, enum, bitfield, timeout, or status path must
update the fixture deliberately and pass review.

## Contribution path

Create the source layout and catalogue identity with a non-claiming scaffold:

```bash
open-mmi-config vehicle-setup scaffold \
  --root . \
  --brand "Brand" \
  --model "Model" \
  --generation "Generation" \
  --platform "Platform" \
  --year-from 2000 \
  --year-to 2005
```

Use `--dry-run` to inspect the plan. The
[scaffolding guide](vehicle-profile-scaffolding.md) describes every option and
safety check. The command creates only an experimental identity envelope and
contribution directories. It does not invent CAN mappings,
evidence, replay coverage, compatibility or hardware qualification.

```text
Raw CAN discovery
        ↓
Custom/provisional decoder
        ↓
Canonical events and statuses
        ↓
Replay or hardware evidence
        ↓
Maintained-profile conformance report
        ↓
Catalogue review
```

A contributor may add the metadata, evidence and first profile implementation in the same pull
request. There is no separate permission request or private allow-list.

## Formal qualification record

Every maintained profile carries `evidence/qualification.v1.json`, described by
`canbusd/data/vehicle-qualification.v1.schema.json`. It records reviewer approval, the next
recheck date, tested equipment and vehicle variants, and transition history. The record must
agree with `metadata.qualification`; replay and hardware claims require complete passing fixture
coverage. See [the qualification workflow](vehicle-qualification-workflow.md).
