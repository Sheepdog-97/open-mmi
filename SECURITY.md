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

See `docs/trust-architecture.md` and `open_mmi_trust/data/trust-manifest.v1.json`.

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
