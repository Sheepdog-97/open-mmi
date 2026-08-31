# Vehicle capture import and comparison

Open MMI includes bounded, research-only tooling for turning common `candump`
text logs into deterministic JSON and for comparing captures taken before and
after a physical action.

This tooling **does not claim vehicle support, infer human meaning, or edit a
maintained profile**. The maintained catalogue may contain profiles whose mappings were
originally derived from controlled captures, but generated reports and candidate fixtures
must stay outside `vehicles/` until a contributor has manually confirmed the signal meaning,
reviewed the mapping, and supplied the required replay/evidence record.

## Supported input

The importer accepts classic CAN frames with up to eight bytes in common
`candump` forms:

```text
(1700000000.000001) can0 123#0011AA
(1700000000.002001) can0 123 [3] 00 22 BB
can0 123#0011AA
can0 123 [3] 00 22 BB
```

Blank lines and lines beginning with `#` are ignored. CAN FD, remote-transmission
requests, malformed DLC values, oversized files, symlinks and non-UTF-8 input are
rejected. Capture files are bounded to 64 MiB and 500,000 frames.

Before sharing a capture, remove VINs, locations, personal data and unrelated
traffic that does not need review.

## Normalize a capture

```bash
open-mmi-config vehicle-setup capture normalize \
  captures/action.log \
  --bus can0 \
  --id 0x123 \
  --from-ms 1000 \
  --to-ms 5000 \
  --output tmp/action.normalized.json \
  --root .
```

Omit `--output` to print JSON to standard output. `--bus` and `--id` may be
repeated. Time filters are relative to the first timestamp in the capture and
require every selected line to include a timestamp.

The normalized document records source hashes, not absolute source paths.

## Compare before and after captures

Record a quiet baseline, perform one clearly documented physical action, and
record the after capture under the same bus and adapter conditions:

```bash
open-mmi-config vehicle-setup capture compare \
  captures/before.log \
  captures/after.log \
  --bus can0 \
  --minimum-score 0.25 \
  --limit 50 \
  --output tmp/action.comparison.json \
  --root .
```

The report groups frames by capture interface and CAN ID. For every changed byte
it lists observed value counts, dominant values, a distribution-change score,
and per-bit changes in the proportion of frames where that bit is set.

A high score is only a statistical difference. It is not proof that the changed
byte represents the action. Repeat the experiment, reverse the action, compare
multiple captures and account for counters, checksums and unrelated periodic
traffic.

## Export candidate replay cases

```bash
open-mmi-config vehicle-setup capture export \
  captures/before.log \
  captures/after.log \
  --profile-id example-profile \
  --fixture-bus comfort \
  --minimum-score 0.5 \
  --output tmp/action.candidate.json \
  --root .
```

The exported document uses the replay-fixture envelope but is marked:

```json
{
  "experimental": true,
  "review_required": true
}
```

Each case contains a representative after-state frame and deliberately empty
`events` and `statuses` expectations. A human must:

1. confirm what the signal means;
2. classify it as an event or persistent status;
3. search the canonical registries;
4. add or reuse a universal descriptor;
5. replace the empty expectations;
6. repeat the evidence and replay checks; and
7. only then move reviewed content into a profile fixture.

Generated output is refused beneath `vehicles/`. Existing output is never
replaced unless `--force` is explicit.

## Recommended experiment discipline

- Change one physical state at a time.
- Keep ignition, bus, capture point, adapter and bitrate constant.
- Record exact action timestamps and whether the action was held or toggled.
- Capture both directions, such as off → on and on → off.
- Repeat each experiment several times.
- Retain unknown and contradictory results in research notes.
- Never transmit CAN frames merely to make a candidate easier to identify.

The importer analyzes passive logs only. It does not open SocketCAN interfaces or
send vehicle traffic.
