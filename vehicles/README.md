# Maintained vehicle catalogue

Maintained profiles use a human-browsable hierarchy:

```text
vehicles/<brand>/<model>/<generation-platform>/
```

For example:

```text
vehicles/seat/leon/1p-pq35/
```

Folder names are lowercase and filesystem-safe. The folder answers **where the
vehicle belongs**; `metadata.id` is the stable machine identity used by Open MMI.
The exact mapping is declared in [`catalogue.v1.json`](catalogue.v1.json), which
also carries deprecated IDs used by existing installations.

Do not pre-create empty brand or model directories. Start a real integration with
the checked scaffold command:

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

Use `--dry-run` to review the derived stable ID, path and files without changing
the checkout. See the [scaffolding guide](../docs/vehicle-profile-scaffolding.md)
for options and safety guarantees. The placeholder values above are not support
claims. A new profile directory represents a genuine CAN/decoder boundary, not
merely a trim, engine,
steering side, or market badge.

Each maintained profile contains:

- `config.json` — identity, buses, canonical events and canonical statuses;
- `README.md` — human scope and contribution notes;
- `fixtures/mappings.v1.json` — deterministic CAN replay proof;
- `evidence/` — profile-local captures or qualification artefacts where suitable;
- `notes/` — reverse-engineering notes and provisional findings.

Run the catalogue and replay gates with:

```bash
open-mmi-config vehicle-setup conform --root .
open-mmi-config vehicle-setup replay --root . <profile-id>
```

Generated human-facing navigation and cross-vehicle coverage are published in
[`docs/vehicle-catalogue.md`](../docs/vehicle-catalogue.md) and
[`docs/vehicle-capability-matrix.md`](../docs/vehicle-capability-matrix.md). Regenerate
both from the checked catalogue and profile metadata with:

```bash
python tools/generate_vehicle_catalogue_docs.py
```

Raw discovery remains open. Use `open-mmi-config vehicle-setup capture` to normalize
and compare passive `candump` logs. Generated reports and candidate fixtures are refused
beneath this maintained tree until a contributor manually confirms meaning, canonical
vocabulary, deterministic expectations and evidence. See the
[capture analysis guide](../docs/vehicle-capture-analysis.md).

Maintained admission is the continuity checkpoint that turns vehicle-specific hexadecimal
data into shared human meaning.

## Qualification records

Each maintained profile has `evidence/qualification.v1.json`. A scaffold starts at `none` with an
unreviewed empty record. Replay and hardware claims must be made through the formal transition
workflow and must not be inferred from a directory name or the presence of captures alone. A
replay-qualified candidate may still reference controlled real-vehicle `capture` evidence; this makes
the mapping confidence visible without claiming the broader hardware-qualification boundary.
