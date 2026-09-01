# Security Policy

## Current security status

`open-mmi` is currently alpha/backend software.

Security and safety reports should target the current active branch, tagged checkpoint, or commit where the issue was found.

Earlier tags such as:

```text
v1.0.0-backend
```

are historical backend checkpoints. They do not represent a final Open MMI v1 product release.

Please include the affected branch, tag, or commit when reporting an issue.

## Reporting a security or safety issue

If you believe you have found a security or safety issue, please do not publish exploit details publicly before the maintainer has had time to review it.

Use GitHub private vulnerability reporting if available, or contact the maintainer through GitHub.

Useful details include:

- affected version, branch, tag, or commit
- operating system
- install method
- CAN adapter used, if relevant
- vehicle platform/profile used, if relevant
- whether the issue requires vehicle CAN access
- whether the issue affects local Linux actions, status reporting, install/update, service permissions, or vehicle profile handling
- steps to reproduce, where safe to share
- logs with sensitive information removed

## Vehicle safety scope

Open MMI interfaces with vehicle CAN-bus data.

Incorrect configuration or unsafe features may:

- trigger unexpected local Linux actions
- misrepresent vehicle state
- distract the driver
- create unsafe behaviour if connected to critical systems

Open MMI currently focuses on:

- passive CAN receive
- local Linux actions
- status decoding
- dashboard/UI consumers

Decoded status is informational. It must not be treated as a replacement for OEM warnings, diagnostics, safety systems, or driver judgement.

## CAN transmit/control

`open-mmi` currently focuses on passive CAN receive and local Linux actions.

Vehicle CAN transmit/control behaviour is out of scope for the current alpha/backend project.

Do not add vehicle CAN transmit/control behaviour without:

- a separate design discussion
- explicit allowlists
- clear user-facing warnings
- maintainer review
- extensive off-car testing
- controlled real-vehicle testing
- documentation explaining the risk

The default project posture should remain passive observation first.

## Trust-boundary declarations

Open MMI maintains a strict machine-readable Trust Manifest describing security- and privacy-relevant capability boundaries. The manifest is release-supplied evidence, not owner authorization: future candidate software must not be able to grant itself a broader trust boundary merely by changing its own manifest.

Current Nightly assurance levels distinguish declaration from enforcement. A capability marked `declared` is a reviewable project policy but is not represented as an OS-level sandbox. `ci-guarded` means checked source/CI tripwires support the claim. Later phases may add runtime, OS and hardware-backed enforcement.

For trust purposes, ordinary local processing needed to provide a requested feature is distinct from telemetry. Telemetry means collection or retention for analytics, profiling, product metrics, fleet statistics, usage measurement, reporting, remote submission, or similar secondary measurement purposes. Changing data purpose or weakening an established enforcement layer is a trust-relevant change.

Telemetry Guard v1 keeps telemetry default-deny until a local owner authorizes one exact scope for one VIN through the supported interactive terminal flow. Production authorization state uses a root-owned mode `0700` directory and mode `0600` file; it stores a salted PBKDF2 VIN fingerprint rather than the raw VIN. Scope changes invalidate authorization. V1 scopes are session-only and local-only, so the guard cannot authorize telemetry persistence or remote upload. Treat bypass of the guard, weakening of its state-file checks, collection before authorization, or reuse of an authorization for a changed scope as a security/trust-boundary defect.

Accepted Owner Trust State v1 records the locally accepted Trust Manifest separately from release-supplied declarations. Production state is root-owned and private, and normal production code must not call its private mutation primitives. The supported `accept-current` owner flow can bootstrap the exact installed manifest or record an equivalent/narrower current boundary; it deliberately refuses to broaden an existing accepted boundary or accept a generation regression. Treat silent accepted-state mutation, accepting added capability/purpose authority, accepting weaker assurance without old-side owner acknowledgement, or presenting an installed expansion as accepted as a security/trust-boundary defect.

Trust Transition Gate v1 evaluates each prepared update candidate with already-installed trusted code before candidate-controlled privileged deployment. Candidate Trust Manifest data is read from the exact Git commit object rather than executed or imported from the staged worktree. Equal/narrower candidates may proceed; generation regressions are blocked; expansions require a local interactive acknowledgement bound to the exact prepared transaction, commit, accepted-state digest and candidate manifest digest. The coordinator gates before starting the root installer and the installer rechecks immediately before deployment. Treat any candidate code execution before that decision, caller-selected candidate/path arguments on the acknowledgement surface, stale authorization reuse, bypass of the installer recheck, or an unacknowledged expansion reaching candidate root execution as a security/trust-boundary defect.

Trust Transition Lineage v1 records every Accepted Owner Trust State change after a locally confirmed genesis baseline. Records are root-private immutable-by-interface files named by their canonical SHA-256 digest and chained to the previous record; validation recomputes the manifest relation and changes, and the current Accepted Owner Trust State must match the lineage head before any prepared update may proceed. The baseline explicitly does not claim history before Lineage v1. Treat record replacement/reordering, weakening record or directory permissions, accepting a lineage head that does not anchor current accepted state, silently recreating missing history, or allowing an update while lineage is absent/divergent as a security/trust-boundary defect. Arbitrary root can still replace both the lineage and its local anchor, so external signed integrity remains a later layer.

Installed Release/File Integrity v1 records the exact committed runtime inventory accepted for the installed release in `/var/lib/open-mmi/trust/installed-release-integrity.v1.json`. The inventory is derived from the exact candidate Git objects, not a candidate worktree, and is checked against both active runtime locations: source packages executed from `/opt/open-mmi` and the venv `site-packages` used by installed console-entry/system services. Before a prepared update can expand authority or begin any candidate-controlled build/deployment step, already-installed trusted code requires the current integrity baseline to match and derives candidate identity from exact Git objects. After the trust/provenance decision (and acknowledged expansion activation where required), `pip wheel` may execute the candidate PEP 517 build backend; the resulting wheel is verified against the Git-object inventory, and candidate `manage.sh` receives only that exact transaction-bound wheel. After deployment, installed bytes must match the candidate inventory before a new integrity baseline is recorded. `release.file-integrity: PASS` proves local byte identity to the recorded accepted Git candidate; it does not by itself prove who signed or published that candidate. Treat integrity-state bypass or permission weakening, verification of only one active runtime root, moving candidate-controlled execution before old-trusted integrity/provenance/trust decisions, candidate selection/replacement of the prepared wheel, ignoring wheel/runtime inventory mismatches, or presenting ancestry/local byte identity as signer provenance as a trust-boundary defect.

Release Provenance / Pinned Signer Root v1 stores one create-once owner-pinned OpenPGP root in `/var/lib/open-mmi/trust/release-signer-root.v1.json`. Old trusted code verifies both the current integrity-bound commit and every prepared candidate commit offline with fixed root-controlled `/usr/bin/git` and `/usr/bin/gpg` paths in an isolated temporary GPG home containing only the pinned public key. GitHub signature badges, user keyrings, Web-of-Trust decisions and network/keyserver discovery are not trust roots. Bootstrap requires root, a local interactive TTY, a safe local public-key file, a signature on the current integrity-bound release, and full-fingerprint confirmation after independent owner/auditor verification. V1 intentionally has no signer-rotation/replacement command and records `history_before_baseline: unverified`; do not add silent root replacement, automatic key discovery, candidate-supplied root mutation, non-interactive bypass flags, or a claim that the release which first introduced Provenance v1 was pre-install provenance-gated. A later key lifecycle/rotation design must be an explicit trust transition. Arbitrary root can still replace local code plus local signer/integrity state, so an external verifier remains a later layer.

Trust Inspector v1 is read-only and does not authorize capabilities or change trust state. `PASS` means one concrete local check succeeded; `UNVERIFIED` is not a pass and means the current architecture cannot prove the claim; `FAIL` means observed state contradicts the inspected contract. Installed Release/File Integrity v1 plus Release Provenance v1 let the built-in inspector check recorded byte identity and the integrity-bound commit's signature against the locally pinned signer root, but the inspector is still not an independent attestation root: arbitrary privileged code can replace the inspector and its local anchors together. Treat removal of a previously available inspection check, secret VIN-binding material appearing in inspector output, a false `PASS` for known-unverified enforcement, or a provenance `PASS` without strict pinned-root signature verification as a trust-boundary defect.

See `docs/trust-architecture.md`, `open_mmi_trust/data/trust-manifest.v1.json`, and `open_mmi_trust/data/trust-inspection.v1.schema.json`.

## Local permissions model

`open-mmi` performs local Linux actions such as media key events, brightness changes, screen wake/sleep behaviour, and dashboard/status output.

Some installations may require additional local permissions.

Current examples include:

- access to `uinput` for virtual input events
- membership of the `input` group for input-related behaviour
- membership of the `video` group for display/backlight control
- udev rules for CAN, backlight, and input-related device access

These permissions are local security tradeoffs.

A system with these permissions should be treated as a trusted local vehicle computer, not as a general-purpose multi-user untrusted desktop.

Do not install unknown vehicle profiles, bindings, action modules, scripts, or udev rules from untrusted sources.

## Trusted configuration

Vehicle profiles and bindings are trusted local configuration.

Bindings can map decoded vehicle events to Python action modules. This is intentional, but it means bindings are not just passive data.

A malicious or careless binding may trigger unwanted local actions.

Only use profiles and bindings that you trust or have reviewed.

Vehicle-specific CAN knowledge should live in vehicle profiles, but action behaviour should still be reviewed before use.

## udev rules

The included udev rules are intended to make a dedicated vehicle Linux installation easier to use.

They may grant access to local devices such as CAN interfaces, backlight control, or virtual input.

Before installing open-mmi on a shared or security-sensitive system, review:

```text
udev/80-canbus.rules
```

In particular, broad access to `uinput` is convenient for virtual input actions, but it is also powerful. A process with uinput access can create synthetic input devices.

For a dedicated car PC or tablet this may be acceptable. For a general-purpose multi-user machine, it may not be.


## Dashboard network and media boundaries

The dashboard binds to loopback by default. Treat any deployment bound to a LAN or
other shared interface as an exposed web service and place it behind a host firewall
or authenticated reverse proxy.

Optional media integrations cross additional trust boundaries:

- Internet Radio catalogue entries are untrusted external input. Stream hosts and
  every redirect are resolved and checked against the public-address policy, and the
  connection is pinned to the validated address.
- USB media roots are trusted local mount points, but individual stream/artwork paths
  are opened descriptor-relatively without following symlinks.
- Jellyfin credentials remain server-side. JSON and image responses are bounded,
  image types are allowlisted, and assigned-user login tokens have a bounded cache
  lifetime with one authentication refresh after rejection.

The private-radio override and Jellyfin global-scope or insecure-TLS options weaken
these defaults. Enable them only for a deliberately trusted local deployment.

## Sensitive information

Please avoid posting:

- full VINs
- private locations
- personal information
- credentials
- SSH keys
- complete logs containing sensitive data
- unsafe exploit details

Redact sensitive data before sharing logs or CAN captures.

CAN logs may reveal details about your vehicle, installed modules, coding, usage patterns, or location-related behaviour when combined with other information.

## Responsible disclosure

The maintainer will aim to acknowledge valid reports, investigate the issue, and publish a fix or mitigation where practical.

Safety-impacting issues may be handled more conservatively than normal bugs.
