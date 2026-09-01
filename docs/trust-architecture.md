# Trust architecture

Open MMI's trust architecture is intended to make changes to privacy, vehicle-control,
network, and persistence boundaries explicit and reviewable. It is not a claim that the
current Nightly already enforces every boundary at the operating-system or hardware layer.

The first implementation is **Trust Manifest v1**. It names the current boundaries in a
strict machine-readable form and records the strength of the current control separately
from the policy itself.

## Core rule

A release may declare what it wants to do. A release must not be able to authorize its own
expansion of an owner's previously accepted trust boundary.

Future update-continuity work will therefore keep three concepts separate:

1. **Release Trust Manifest** — supplied by the candidate release; describes proposed
   capabilities and their assurance level.
2. **Accepted owner trust state** — retained by the already-trusted installation; records
   what the owner has accepted.
3. **Transition history** — records explicit boundary transitions so lineage can later be
   checked independently.

Trust Manifest v1 establishes only the first of these. It does not yet implement automatic
update-transition enforcement or owner authorization storage.

## Current v1 capabilities

The checked manifest lives at:

```text
open_mmi_trust/data/trust-manifest.v1.json
```

It declares these stable capability identifiers:

- `vehicle.can.receive`
- `vehicle.can.transmit`
- `telemetry.collection`
- `vehicle.identity.remote-resolution`
- `network.external-egress`
- `vehicle-data.persistence`

Capability identifiers are compatibility contracts. A later schema must not silently reuse
an existing identifier with a materially different meaning.

## Policy and assurance are different

The manifest records both:

- **policy** — what the release says is permitted;
- **assurance** — how strongly the current release can demonstrate that policy.

Trust Manifest v1 defines these assurance levels:

```text
declared
ci-guarded
runtime-guarded
os-enforced
hardware-enforced
```

A stronger policy statement does not magically create stronger enforcement. Telemetry
Guard v1 now places Open MMI telemetry collection behind a runtime authorization check, so
`telemetry.collection` is `runtime-guarded`. This does not mean the operating system could
prevent deliberately modified privileged software from bypassing the library. CAN
transmission remains prohibited, and the current source plus CI tripwire contain no CAN
send path, so its v1 assurance is `ci-guarded`. A later phase can move these boundaries
toward OS/interface enforcement.

Removing an established enforcement layer is itself trust-relevant even if the headline
policy text does not change.

## Telemetry Guard v1

Trust policy generation 2 is the first declared capability expansion after the manifest
foundation. `telemetry.collection` changes from `prohibited` to `local-owner-opt-in`; the
release does not authorize collection merely by declaring that policy.

The accepted-owner trust state and trust-aware update gate are not implemented yet, so the
current updater does not pre-authorize or pre-reject generation 2 as a transition. This is
recorded as a limitation rather than hidden. The enforcement available in this generation is
post-install: actual telemetry sampling remains denied until the local owner authorization
boundary succeeds.

Telemetry Guard v1 is deliberately narrow:

- missing, unreadable, malformed, permission-weakened, wrong-VIN or wrong-scope
  authorization state denies collection;
- the supported owner mutation surface is an interactive local-terminal command; it has
  no HTTP endpoint, non-interactive confirmation flag or VIN command-line argument. The
  production state directory is root-owned mode `0700` and its state file is mode `0600`;
- the raw VIN is never written to authorization state. A random salt and PBKDF2-SHA256
  fingerprint bind authorization to the locally supplied 17-character VIN;
- authorization stores the exact normalized scope plus its SHA-256 digest. Any purpose or
  signal change produces a different digest and therefore requires new owner authorization;
- Telemetry Guard v1 accepts only `retention: session` and `destination: local-only`. It
  cannot authorize durable telemetry storage or remote submission;
- `collect_with_guard()` requires one sampler per declared signal, rejects missing or extra
  samplers, and evaluates authorization before invoking any sampler, so a denied request does
  not first collect data and discard it later.

The production state is `/var/lib/open-mmi/trust/telemetry-authorization.v1.json`. The local
owner CLI is `open-mmi-telemetry` and production mutation requires root. There is
intentionally no HTTP authorization endpoint and no remote VIN lookup. CI also rejects
production calls to the authorization mutation primitives outside the owner CLI.

A scope file is explicit data, for example:

```json
{
  "schema_version": 1,
  "purpose": "owner-diagnostics",
  "signals": ["vehicle.rpm", "vehicle.speed"],
  "retention": "session",
  "destination": "local-only"
}
```

The owner can review and authorize that exact file locally with:

```text
sudo open-mmi-telemetry authorize --scope ./telemetry-scope.json
```

Revocation is similarly local and interactive:

```text
sudo open-mmi-telemetry revoke
```

Generation 2 does not add a background telemetry collector or uploader. It establishes the
authorization boundary that any future built-in collector must pass before sampling.

The manifest's `runtime-guarded` assurance describes this Open MMI collection boundary, not
an OS sandbox against arbitrary root code. Future built-in collectors must use the guard; a
future OS-level egress/persistence architecture can make bypass materially harder.

## Operational processing is not telemetry

Open MMI must process local vehicle and host state to provide requested functionality. That
includes values such as speed, RPM, temperatures, door state, media state, trip counters,
and service-reminder state.

For this architecture, **telemetry collection** means collecting or retaining observations
for analytics, profiling, product metrics, fleet statistics, usage measurement, diagnostic
reporting, remote submission, or similar secondary measurement purposes.

Using a value transiently or persisting a narrowly declared local operational state for the
feature the owner requested is not automatically telemetry. Changing the purpose of that
collection is trust-relevant.

## Networking

Open MMI is local-first, not network-free. The current manifest declares external egress
only for named purposes that exist in the current tree:

- an owner-configured dashboard URL (loopback by default);
- Internet Radio;
- Jellyfin;
- release/update retrieval.

Normal dashboard rendering must not introduce a third-party network dependency. Bootstrap
5.3.8 is therefore vendored as a local static asset and verified against Bootstrap's
published SHA-384 Subresource Integrity digest. The previously loaded Bootstrap Icons
stylesheet was unnecessary because Open MMI's media controls already use inline SVG icons,
so that dependency was removed rather than copied locally.

This removes the former `frontend.bootstrap-cdn` and `frontend.bootstrap-icons-cdn` purposes
from `network.external-egress`. It is the first recorded example of the trust boundary
becoming narrower after Trust Manifest v1: the dashboard keeps the same declared feature
set while surrendering two unrelated third-party egress paths. Network assurance remains
`declared` for now; later architecture should narrow actual process network authority so
undeclared egress becomes technically harder rather than only review-visible.

## Vehicle CAN

The current project posture remains passive observation first. Production CAN runtime code
has no transmit call and Trust Manifest v1 declares `vehicle.can.transmit` prohibited.

The initial CI check is deliberately only a tripwire. It is not presented as proof that a
process with a SocketCAN socket could never be modified to transmit. Later work should add
stronger process, SocketCAN, adapter, and hardware controls where platforms support them.

## Future transition rule

The intended update rule is monotonic:

> Software may surrender authority without special permission. It may not silently acquire
> authority that the already-trusted installation did not possess.

A future trusted updater should compare the currently accepted boundary against the
candidate manifest before candidate code receives privileged execution. Equivalent or
narrower transitions may proceed under policy. Boundary expansions must stop for a local,
owner-visible acknowledgement performed by the already-trusted side of the transition.

A maintainer signature proves provenance. It does not grant permission to silently redraw an
owner's established trust boundary.

## SI and downstream distributions

Open MMI does not attempt to prevent a system integrator or owner with control of a machine
from running different software. The goal is narrower: a modified installation should not
silently inherit Open MMI's trust reputation after removing or weakening the controls that
support that reputation.

Future Trust Inspection and independent-verifier work should therefore evaluate demonstrated
capabilities and lineage rather than simply checking whether an installation has an official
Open MMI file hash.

## Trust Inspector v1

Trust Inspector v1 is a read-only local evidence surface. It does not authorize a
capability, modify telemetry consent, contact a network service, or change Trust Manifest
policy generation 2. Run it with:

```text
open-mmi-trust-inspect
open-mmi-trust-inspect --json
```

The machine-readable report contract is checked in at:

```text
open_mmi_trust/data/trust-inspection.v1.schema.json
```

Inspection results use three deliberately different states:

- `PASS` — one concrete local check succeeded;
- `FAIL` — observed local state contradicts the inspected trust contract;
- `UNVERIFIED` — the current architecture cannot prove the claim.

`UNVERIFIED` must never be presented as equivalent to `PASS`. The default human command
therefore exits successfully when no contradiction was found, while `--require-pass` can be
used by a stricter local checker that wants any remaining unverified evidence to be
non-zero. A `FAIL` is always non-zero.

V1 reports the strict Trust Manifest parse, policy generation and deterministic manifest
self-digest; every declared capability and assurance level; redacted Telemetry Guard
authorization state and exact authorized scope when readable; a no-authorization runtime
probe that verifies telemetry sampling does not begin before the guard; the installed-source
tripwire preventing normal Open MMI production code from calling telemetry authorization
mutators; the current CAN no-send source tripwire; and the dashboard's local Bootstrap /
no-remote-render-dependency contract. VIN salt and fingerprint bytes are intentionally not
part of the inspection report schema.

The inspector also states what it cannot currently prove. In generation 2, generic network
egress enforcement, generic vehicle-data persistence enforcement and remote VIN-resolution
enforcement remain declaration-level. Signed installed-file integrity, accepted owner release
trust state, append-only transition lineage and the trusted updater's pre-installation
capability-expansion gate are not implemented yet. Those checks therefore report
`UNVERIFIED`, and a normal current installation has an overall `UNVERIFIED` result even when
all available concrete checks pass.

That limitation is intentional. The built-in inspector is evidence produced by the installed
software itself; without an independent integrity/lineage root, a sufficiently privileged
modified installation can modify the inspector too. A later independent Trust Checker should
consume the same kinds of evidence from outside the inspected installation and turn more of
these `UNVERIFIED` results into independently grounded conclusions.
