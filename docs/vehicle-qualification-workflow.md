# Maintained vehicle qualification workflow

Open MMI separates reverse-engineering progress from a maintained compatibility claim.
A scaffold, capture comparison, provisional decoder, or candidate fixture does not by
itself mean that a vehicle is supported.

SEAT Leon Mk2 / 1P (PQ35) remains the only maintained profile backed by real reverse
engineering and hardware qualification in this repository.

## Lifecycle

Maintained profiles move through three qualification levels:

```text
none → replay → hardware
```

The corresponding profile maturity is:

| Qualification | Maturity | Meaning |
| --- | --- | --- |
| `none` | `experimental` | No formal mapping or hardware claim. |
| `replay` | `candidate` | Every declared canonical event and status is covered by passing deterministic fixtures. |
| `hardware` | `qualified` | Replay proof is complete and the stated scope was reviewed against real passive vehicle CAN traffic. |

Promotion must advance one stage at a time. A profile cannot move directly from `none`
to `hardware`. Demotion may move to any lower level when evidence is withdrawn, becomes
ambiguous, or needs retesting.

## Qualification record

Each maintained profile has:

```text
vehicles/<brand>/<model>/<generation-platform>/evidence/qualification.v1.json
```

The format is described by
`canbusd/data/vehicle-qualification.v1.schema.json`. The record contains:

- the current qualification level and tested date;
- the exact tested scope;
- tested equipment and vehicle-variant boundaries;
- reviewer approval and the next review date; and
- an append-only transition history with reasons and reviewers.

The current level, date, and scope must match `metadata.qualification` in the profile.
The record does not replace mapping evidence; it binds that evidence to an explicit
review decision.

## Admission rules

### Replay qualification

A replay promotion requires:

- complete passing `fixtures/mappings.v1.json` coverage;
- no missing or unexpected canonical events or statuses;
- at least one `replay` evidence record;
- a tested date and bounded scope;
- reviewer approval; and
- a future recheck date.

### Hardware qualification

Hardware promotion additionally requires:

- an existing replay-qualified state;
- at least one `hardware` evidence record;
- a named passive test setup or equipment boundary;
- a named vehicle variant/model-year boundary; and
- reviewed real-vehicle evidence for the stated scope.

Hardware qualification does not turn experimental or diagnostic individual signals
into stable claims. Those lifecycle limitations remain visible in the status registry
and profile limitations.

## Inspect qualification

Report all maintained profiles:

```bash
open-mmi-config vehicle-setup qualification report --root .
```

Report one profile at a specific review date:

```bash
open-mmi-config vehicle-setup qualification report \
  seat-leon-1p-pq35 \
  --root . \
  --as-of 2026-07-21
```

A review past `recheck_after` produces a `qualification-stale` warning. It does not
silently rewrite or demote the profile. Maintainers must review the evidence and either
renew the qualification or record an explicit demotion.

## Promote with a dry run

Evidence arguments use `KIND=PATH=DESCRIPTION` and paths remain repository-relative.
For example, a future evidence-backed profile could be promoted from `none` to `replay`
with:

```bash
open-mmi-config vehicle-setup qualification transition \
  example-profile \
  --root . \
  --to replay \
  --reason "Complete deterministic mapping replay reviewed." \
  --reviewer "Reviewer name" \
  --reviewed-on 2026-07-21 \
  --tested-on 2026-07-20 \
  --recheck-after 2027-01-20 \
  --scope "Canonical event and status mapping replay" \
  --evidence "replay=vehicles/example/fixtures/mappings.v1.json=Reviewed deterministic replay proof." \
  --dry-run
```

Remove `--dry-run` only after reviewing the machine-readable plan and staged diff.
The command atomically updates the profile and qualification record, then reruns
maintained-catalogue conformance. A failed final check rolls both files back.

## Hardware promotion

Hardware promotion adds equipment and variant boundaries:

```bash
open-mmi-config vehicle-setup qualification transition \
  example-profile \
  --root . \
  --to hardware \
  --reason "Passive real-vehicle CAN qualification reviewed." \
  --reviewer "Reviewer one" \
  --reviewer "Reviewer two" \
  --reviewed-on 2026-07-22 \
  --tested-on 2026-07-21 \
  --recheck-after 2027-07-21 \
  --scope "Passive receive on the documented CAN bus" \
  --equipment "Documented CAN adapter and capture point" \
  --variant "Exact tested model years and equipment variant" \
  --evidence "hardware=path/to/redacted-hardware-record.md=Reviewed passive hardware qualification." \
  --dry-run
```

Do not use placeholder evidence to promote a real profile.

## Demotion

Demotion requires a reviewer, review date, and plain-language reason. Demotion to
`none` clears current qualification scope and evidence from profile metadata while
retaining the transition history in `qualification.v1.json`.

```bash
open-mmi-config vehicle-setup qualification transition \
  example-profile \
  --root . \
  --to none \
  --reason "Compatibility claim withdrawn pending retest." \
  --reviewer "Reviewer name" \
  --reviewed-on 2026-07-23 \
  --dry-run
```

## Evidence safety

Qualification evidence must be reviewable without exposing VINs, registration data,
credentials, precise private locations, or unredacted personal captures. Open MMI is
passive receive-only in this workflow; qualification tooling does not transmit vehicle
CAN frames.
