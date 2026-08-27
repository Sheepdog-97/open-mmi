# Vehicle profile scaffolding

Open MMI has a maintained catalogue with profiles at different confidence levels. The
scaffold command prepares a contribution workspace for real future research; creating a
scaffold is not a statement that the vehicle is supported or qualified.

## Review the plan first

Run from a source checkout:

```bash
open-mmi-config vehicle-setup scaffold \
  --root . \
  --brand "Brand" \
  --model "Model" \
  --generation "Generation" \
  --platform "Platform" \
  --year-from 2000 \
  --year-to 2005 \
  --dry-run
```

The placeholder values above are intentionally generic. Replace them with the
identity of the real vehicle being investigated. The JSON response reports the
derived stable profile ID, maintained path, planned files and next checks.

Remove `--dry-run` only after reviewing that plan.

## What is created

The command derives lowercase filesystem-safe components and creates:

```text
vehicles/<brand>/<model>/<generation-platform>/
├── config.json
├── README.md
├── fixtures/README.md
├── evidence/README.md
└── notes/README.md
```

It also registers the stable profile ID and exact `config.json` path in
`vehicles/catalogue.v1.json`.

The generated profile is deliberately conservative:

- maturity is `experimental`;
- qualification is `none`;
- event, presence and status mappings are empty;
- the limitations state that no confirmed CAN mapping or hardware support is claimed;
- bus capture details remain explicit TODOs unless supplied by the contributor.

## Required identity

The command requires brand, model, generation, platform and an inclusive model
year range. It derives the path and default stable ID from those values. Use
`--id` only when a different stable machine identity is genuinely required.

Optional inputs include:

```text
--display-name
--maintainer              repeatable
--market-alias            repeatable
--default-bus
--interface
--bitrate
```

A market alias is a compatible human-facing name, not a deprecated profile ID.
Legacy profile IDs are maintained directly in the checked catalogue only when a
real compatibility migration exists.

## Safety rules

Scaffolding fails without changing the checkout when:

- an identity is already registered as a canonical ID or legacy alias;
- the destination already exists;
- a generated catalogue path collides with another profile;
- an input contains path syntax or cannot become a safe component;
- a destination parent is a symlink;
- the existing maintained catalogue is already inconsistent;
- the generated metadata or profile envelope fails validation.

Writes are staged beneath `vehicles/`, the destination and catalogue are
rechecked immediately before replacement, and the previous catalogue is restored
if final tree verification fails.

## Continue with evidence, not placeholders

After creation:

1. document the real capture point, adapter, interface and bitrate;
2. keep provisional observations in `notes/`;
3. search the canonical event and status registries by human meaning;
4. add only mappings supported by captures or hardware observations;
5. add deterministic replay fixtures before candidate maturity;
6. record bounded, reviewable evidence without VINs or personal data;
7. run maintained conformance and regenerate catalogue documentation.

```bash
open-mmi-config vehicle-setup conform --root .
python tools/generate_vehicle_catalogue_docs.py
python tools/generate_vehicle_catalogue_docs.py --check
```
