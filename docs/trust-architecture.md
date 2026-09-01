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

A stronger policy statement does not magically create stronger enforcement. For example,
Nightly currently declares telemetry collection prohibited, but the generic Telemetry Guard
has not been implemented yet, so that capability remains `declared`. CAN transmission is
also prohibited, and the current source plus CI tripwire contain no CAN send path, so its
v1 assurance is `ci-guarded`. A later phase can move it toward OS/interface enforcement.

Removing an established enforcement layer is itself trust-relevant even if the headline
policy text does not change.

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
