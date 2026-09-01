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

Update continuity keeps three concepts separate:

1. **Release Trust Manifest** — supplied by the candidate release; describes proposed
   capabilities and their assurance level.
2. **Accepted owner trust state** — retained by the already-trusted installation; records
   what the owner has accepted.
3. **Transition history** — records explicit boundary transitions so lineage can later be
   checked independently.

Trust Manifest v1 establishes the release declaration, Accepted Owner Trust State v1
establishes the second local authority record, Trust Transition Gate v1 enforces the comparison
before a prepared candidate receives privileged execution, Trust Transition Lineage v1 records
accepted-state changes from a locally confirmed genesis baseline forward, and Installed
Release/File Integrity v1 binds active installed runtime bytes to the exact accepted Git candidate.
An independently pinned release-signer/external verification root remains a later anchor.

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

Generation 2 originally landed before Accepted Owner Trust State and Trust Transition Gate
v1 existed, so that historical generation-1 to generation-2 installation was not pre-screened
by the gate. Accepted Owner Trust State v1 can bootstrap the currently trusted installed
boundary, but it deliberately cannot retroactively prove that the historical installation was
gated. Future prepared candidates are now compared with accepted owner state before candidate
root execution. Actual telemetry sampling remains independently denied until the separate
local Telemetry Guard authorization boundary succeeds.

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

## Accepted Owner Trust State v1

Accepted Owner Trust State v1 is the local authority record that is deliberately separate
from a release-supplied Trust Manifest. Its production state is:

```text
/var/lib/open-mmi/trust/accepted-owner-trust.v1.json
```

The final directory is root-owned mode `0700`; the state file is root-owned mode `0600`,
written atomically and validated strictly. The record contains the exact normalized accepted
Trust Manifest, its deterministic SHA-256 digest and a local acceptance timestamp. It does
not contain a maintainer signature or claim independent release provenance.

The owner surface is intentionally narrow:

```text
sudo open-mmi-trust-state status
sudo open-mmi-trust-state accept-current
```

`accept-current` accepts no candidate path, repository, URL, command or non-interactive
confirmation flag. On a machine with no prior accepted state it can bootstrap only the exact
currently installed Trust Manifest after an interactive digest-bound confirmation. Once
state exists, the command may refresh it only when the installed boundary is equivalent or
narrower. The underlying state mutation primitive independently enforces the same monotonic
rule, so reusing it cannot broaden existing accepted authority or accept a policy-generation
regression. Trust Transition Gate v1 provides the separate old-trusted-side pre-install
acknowledgement path for an exact prepared expansion; `accept-current` remains unable to do so
after candidate installation.

The reusable comparison function applies these v1 rules capability by capability:

- a candidate policy may not rank above the accepted policy;
- for `declared-purposes-only`, candidate purposes must be a subset of accepted purposes;
- candidate assurance must be equal or stronger; weakening assurance is a trust expansion;
- a lower `policy_generation` is blocked as a generation regression until trusted downgrade
  lineage exists;
- equal or narrower authority is marked safe to proceed without new owner acknowledgement;
  an expansion is marked as requiring an old-side owner acknowledgement.

Trust Inspector v1 reads this state without mutation. Missing state remains `UNVERIFIED`; an
invalid state file or an installed manifest that exceeds/regresses the accepted ceiling is a
`FAIL`; an equal or narrower installed manifest is `PASS` for the accepted-state check. CI
also rejects normal Open MMI production code that mutates accepted state: the raw writer is
module-internal and the local owner CLI may call only the monotonic record primitive.

This is still software enforcement on a privileged machine. Arbitrary root software can
replace Open MMI or its state. Signed installed-file integrity, append-only transition
lineage and an independent checker are later layers needed to make that tampering externally
verifiable.

## Trust Transition Gate v1

The update rule is now enforced for prepared Nightly candidates:

> Software may surrender authority without special permission. It may not silently acquire
> authority that the already-trusted installation did not possess.

The gate runs in installed trusted code before candidate-controlled privileged deployment. A
prepared candidate remains data while the decision is made. The candidate Trust Manifest is
read from the exact candidate commit with trusted `git ls-tree` and `git cat-file` operations;
the gate does not import candidate Python, execute candidate shell, load candidate package
metadata, or invoke candidate hooks to determine the trust relation. The manifest must be a
non-executable regular Git blob, is size-bounded, decoded as strict UTF-8 JSON, and is validated
with the installed manifest schema.

The coordinator performs a preflight check before it starts the root installer service, and the
installer independently repeats the check immediately before deployment. The resulting rules
are:

- missing or invalid Accepted Owner Trust State blocks installation;
- a policy-generation regression blocks installation;
- an equal or narrower candidate may proceed without new owner acknowledgement;
- an expansion remains prepared but blocked until the owner acknowledges that exact transition
  through installed trusted code.

The owner surface is deliberately fixed:

```text
sudo open-mmi-trust-transition status
sudo open-mmi-trust-transition acknowledge
```

It accepts no candidate path, repository, ref, URL, manifest path, command, `--yes`, or other
non-interactive bypass. Expansion acknowledgement requires a local interactive terminal and a
confirmation phrase bound to the candidate manifest digest and candidate commit. The resulting
root-private authorization record is additionally bound to the prepared transaction ID, exact
candidate commit, current accepted-state digest, accepted manifest digest, candidate manifest
digest, and candidate policy generation. A new preparation or a changed accepted state makes
the authorization stale.

For an acknowledged expansion, installed trusted code records the newly accepted capability
ceiling before candidate root code receives that expanded authority, then consumes the exact
transition authorization. For an equal or narrower transition, accepted owner state advances
only after deployment succeeds; this avoids a failed narrowing followed by rollback leaving the
restored older software outside the accepted ceiling.

After the gate succeeds, the existing deployment engine still executes the prepared candidate's
`scripts/manage.sh _deploy-prepared` as root. That execution is intentionally *after* the trust
decision and must never be moved before it. Trust Transition Gate v1 constrains official update
flow and owner acknowledgement; it is not an OS sandbox against arbitrary root software or a
proof that a candidate's manifest is truthful. Stronger runtime/OS enforcement and independent
verification remain later layers.

A maintainer signature proves provenance. It does not grant permission to silently redraw an
owner's established trust boundary. Trust Transition Lineage v1 records local accepted-state
changes, and Installed Release/File Integrity v1 binds current runtime bytes to the exact accepted
Git candidate, but their genesis baselines do not retroactively prove earlier history. Arbitrary
root can still replace installed code plus both local trust stores. An independently pinned release
signer and external verifier therefore remain necessary for external verification.

## Trust Transition Lineage v1

Trust Transition Lineage v1 records accepted-owner-trust state changes separately from the
mutable Accepted Owner Trust State itself. Production lineage is a root-owned mode `0700`
directory:

```text
/var/lib/open-mmi/trust/transition-lineage.v1.d
```

Each record is a mode `0600` regular file whose name contains its zero-padded sequence number
and the SHA-256 digest of its canonical record bytes. Official code appends by creating a new
record; it never rewrites, truncates, renumbers, or replaces an existing record. Every record
after the baseline contains the previous record digest, accepted-state before/after digests,
manifest before/after digests and generations, the full post-transition accepted-state snapshot,
the recomputed transition relation/changes, the decision source, candidate transaction/commit
when applicable, and whether local owner acknowledgement was required. Expansion records also
bind the transition-authorization digest.

The chain has two anchors. Record validation recomputes each record digest and the semantic
manifest comparison against the previous record, while the current Accepted Owner Trust State
must exactly match the final record's accepted-state snapshot and digest. Consequently editing
or reordering a record fails the hash chain, and deleting the newest authority-changing record
leaves a valid shorter chain whose head no longer matches current accepted authority and therefore
fails inspection and blocks future prepared updates. This remains local software evidence: an
arbitrary root attacker capable of replacing both stores still needs an independent external
integrity anchor to be detected.

Existing installations establish an explicit genesis baseline with:

```text
sudo open-mmi-trust-lineage status
sudo open-mmi-trust-lineage bootstrap
```

Bootstrap requires a local interactive, accepted-state-digest-bound confirmation and records
`history_before_baseline: unverified`. It does not claim that the historical generation-1 to
generation-2 transition or any earlier install was lineage-recorded. New accepted-state bootstrap
performed by `open-mmi-trust-state accept-current` creates the Lineage v1 baseline in the same
local owner flow.

If accepted state was successfully written but the following lineage append could not complete
(for example because of a filesystem failure), the mismatch is deliberately fail-closed. The
updater will not treat the newer accepted state as sufficient authority while lineage is behind.
A local owner can inspect and append reconciliation evidence with:

```text
sudo open-mmi-trust-lineage reconcile-current
```

Reconciliation never rewrites history or changes accepted authority. It appends a record only
after local confirmation; if the current state is an expansion beyond the lineage head, matching
transition-authorization evidence is additionally required. There is intentionally no silent or
candidate-controlled repair path.

For an acknowledged prepared expansion, old trusted code changes Accepted Owner Trust State,
appends the exact expansion lineage record, and only then allows candidate-controlled deployment
to continue. The one-shot transition authorization is consumed only after both accepted state and
lineage have advanced. For a non-expanding candidate whose accepted manifest changes, accepted
state and lineage advance only after deployment succeeds. A candidate with an identical accepted
manifest produces no redundant authority-transition record.

## Installed Release/File Integrity v1

Installed Release/File Integrity v1 closes a different gap from the Trust Manifest and Transition
Gate: after an update decision, it answers whether the runtime bytes actually active on the system
still match the exact candidate Git tree that old trusted code accepted. It does **not** turn Git
ancestry into signer authentication and it does not give a candidate permission to expand trust.

Production integrity state is a root-owned mode `0600` regular file beneath the existing private
mode `0700` trust directory:

```text
/var/lib/open-mmi/trust/installed-release-integrity.v1.json
```

The state records the exact 40-hex candidate commit, embedded Trust Manifest and digest, a sorted
canonical runtime-file inventory and inventory digest, and the Accepted Owner Trust State plus
Lineage-head digests current when that integrity state was recorded. Inventory entries bind each
logical runtime path to its byte length and SHA-256 digest. Validation rejects duplicate JSON
fields, non-finite values, unknown fields, symlinked/weakened production state, duplicate paths,
non-canonical ordering and digest mismatches.

The expected inventory is derived with old trusted code from the exact Git commit objects using
`git ls-tree`/`git cat-file`; candidate Python is never imported or executed to describe itself.
Only explicitly supported runtime file kinds and Git modes are accepted. A candidate that adds an
unrecognized runtime artifact such as a native `.so`, symlink or submodule fails closed until the
trusted inventory policy is deliberately updated and reviewed. Build cache files such as
`__pycache__`/`.pyc` are ignored because they are derived runtime artifacts, and the dashboard's
source-only `ui/web_dashboard/README.md` is explicitly outside the executable/runtime inventory.

### The active runtime is intentionally verified in two physical places

Open MMI does not have one physical runtime root. User services run with
`WorkingDirectory=/opt/open-mmi`, so imports for `actions`, `bindings`, `canbusd`, `powerd`, `ui`
and `vehicles` are satisfied by the deployed source tree. Root/system update services and console
entry points execute the installed wheel from the venv, so `open_mmi_trust`,
`open_mmi_telemetry`, and wheel-installed copies of the runtime packages live in
`site-packages`. File Integrity v1 verifies both locations against the same logical Git-object
inventory. A one-root check would be incomplete and is treated as a trust defect.

### Establishing the first local baseline

An existing installation can establish File Integrity v1 locally with:

```text
sudo open-mmi-trust-integrity status
sudo open-mmi-trust-integrity bootstrap
```

Bootstrap requires root plus an interactive TTY, requires current Accepted Owner Trust State and
Lineage to be valid, rejects a generation regression or an installed manifest broader than the
accepted ceiling, and requires an exact confirmation phrase bound to the first 12 hex characters
of the candidate inventory digest. A managed checkout must identify one exact committed tree; a
dirty editable checkout is refused rather than silently attributing uncommitted bytes to a Git
commit. The bootstrap summary explicitly states `history_before_baseline: unverified`; the stored
state identifies its source as `baseline-existing-state`. Together those semantics establish
evidence from that exact point forward and do not retroactively attest earlier installations.

### Prepared-update ordering

For a managed prepared update, already-installed trusted code now enforces this ordering:

1. re-evaluate the Trust Transition Gate against current Accepted Owner Trust State and Lineage;
2. require the **currently installed** split runtime to match its existing integrity state;
3. derive the candidate Trust Manifest and runtime inventory from the exact candidate Git objects;
4. for an acknowledged expansion, advance the already-defined accepted-state/lineage authority
   transition only after those current checks succeed;
5. only after the old-trusted trust decision (and any acknowledged expansion activation), invoke
   the candidate wheel build with the already-installed Python/pip into the root-owned transaction
   rollback tree. A PEP 517 build backend is candidate-controlled code, so this build is deliberately
   the first candidate-controlled execution point and must never move before steps 1–4;
6. verify the resulting wheel's complete logical runtime payload against the Git-object inventory;
7. pass `scripts/manage.sh _deploy-prepared` only that exact transaction-bound, root-owned wheel;
8. after deployment and service health checks, verify both active runtime locations against the
   exact candidate inventory;
9. finalize an equal/narrower accepted-state transition where needed, then atomically record the
   new integrity state.

The candidate therefore cannot silently choose a different Python package artifact after being
accepted. The wheel build itself may execute candidate-controlled PEP 517 backend code, but only
after old trusted code has completed the boundary decision and, for an acknowledged expansion,
advanced accepted authority plus lineage. The build output is not trusted merely because it built:
it must match the Git-object inventory before `manage.sh` receives it. Missing or mismatched current
integrity blocks later prepared updates; a post-deployment mismatch is a failed update and no new
integrity baseline is recorded. The current deployment engine still runs the accepted candidate's
`scripts/manage.sh _deploy-prepared` after the old-side gate and wheel verification, so this is not
yet a fully old-code-owned filesystem deployment engine. Also, a failure detected by the outer installer after candidate deployment returns may
leave candidate bytes present while the updater remains failed and integrity state is not
advanced; future work can move final file replacement/rollback entirely into the trusted
installer. Those limitations must not be described as atomic attestation.

### Integrity is not provenance

Trust Inspector exposes two separate conclusions:

- `release.file-integrity` can be `PASS` when strict local state is valid and all active bytes
  match the recorded accepted Git candidate;
- `release.provenance` remains `UNVERIFIED` because Open MMI does not yet pin an independent
  release-signing identity that installed trusted code or an external verifier can validate.

This separation is intentional. A Git commit ID and matching bytes identify content; forward
ancestry identifies history in the repository object graph. Neither statement by itself answers
which signer the owner independently trusts. Likewise, arbitrary root can replace both installed
code and this local integrity state. A later signer-root / independent Trust Checker layer should
consume these exact digests as evidence without upgrading local integrity into a provenance claim.

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
self-digest; every declared capability and assurance level; Accepted Owner Trust State and
the installed manifest's relation to that accepted ceiling; redacted Telemetry Guard
authorization state and exact authorized scope when readable; a no-authorization runtime
probe that verifies telemetry sampling does not begin before the guard; installed-source
tripwires preventing normal Open MMI production code from calling telemetry or accepted-state
mutation primitives; the current CAN no-send source tripwire; and the dashboard's local
Bootstrap / no-remote-render-dependency contract. VIN salt and fingerprint bytes are
intentionally not part of the inspection report schema.

The inspector also states what it cannot currently prove. In generation 2, generic network
egress enforcement, generic vehicle-data persistence enforcement and remote VIN-resolution
enforcement remain declaration-level. Accepted owner release trust state is inspectable once
locally bootstrapped, and Trust Inspector v1 now reproduces the source-level ordering of Trust
Transition Gate v1: coordinator preflight before installer launch, installer recheck before
candidate deployment, and Git-object candidate-manifest inspection. Trust Transition Lineage v1
is inspected as a hash-chained local record and becomes `PASS` once a locally confirmed baseline
exists and its head anchors current accepted state. Installed Release/File Integrity v1 can make
`release.file-integrity` `PASS` once its locally confirmed baseline exists and both active runtime
roots match it. `release.provenance` deliberately remains `UNVERIFIED` because no independently
pinned Open MMI release-signing identity exists yet. A normal current installation therefore still
has an overall `UNVERIFIED` result even when all available concrete checks pass.

That limitation is intentional. The built-in inspector is evidence produced by the installed
software itself; local File Integrity v1 makes silent byte drift detectable relative to its local
anchor, but sufficiently privileged modified software can replace both the inspector and that
local anchor. A later independent Trust Checker with a pinned signer/external integrity root should
consume the same kinds of evidence from outside the inspected installation and turn more of these
`UNVERIFIED` results into independently grounded conclusions.
