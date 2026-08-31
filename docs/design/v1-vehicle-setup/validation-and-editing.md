# Validation and custom editing

## Validation layers

Validation is shared by catalogue inspection, draft save, preview, coordinator apply,
CLI commands and tests.

It has four layers:

1. JSON and document shape;
2. profile or bindings semantic validation;
3. profile/bindings compatibility; and
4. runtime/provisioning validation.

The privileged coordinator reruns every validation required for application.

## Profile validation

At minimum validate:

- top-level object;
- `default_bus` string;
- `can_buses` object when present;
- default bus exists or receives the documented legacy fallback;
- each bus metadata value is an object;
- interface names have valid bounded Linux syntax;
- bitrate is a positive bounded integer when present;
- provisioning is from an allowlist;
- `bring_up` is boolean when present;
- rule, presence and status collections are arrays;
- CAN identifiers parse and are in the supported range;
- byte indexes and masks are valid for the supported frame type;
- rule bus references exist;
- event identifiers are canonical entries in the Open MMI vehicle-event registry;
- `value: "any"` rules match the registered payload contract and exact-value rules use no-payload events;
- presence transitions use registered no-payload events;
- status paths are non-empty bounded strings;
- supported status rule types contain their required fields; and
- aliases do not create ambiguous output without an explicit warning.

Unknown fields may be warnings during alpha compatibility, but fields used to generate
privileged configuration must be strict.

## Bindings validation

Maintained bindings identify stable canonical actions. The implemented action registry
owns the private module/function mapping and validates maintained and custom binding
action contracts. Legacy custom bindings using `module`, `func`, and `args` remain a
compatibility input and receive warnings.

The registry defines:

- stable action identifier;
- display name and description;
- implementation callable;
- allowed argument count and types;
- default arguments;
- whether extra CAN-derived arguments are accepted; and
- whether the action is exposed to custom bindings.

Bindings validation then checks:

- top-level event map;
- canonical registered vehicle-event keys;
- registered action identifier;
- bounded argument arrays;
- argument types and values; and
- no unsupported extra fields.

The vehicle-event registry is authoritative for binding keys and the vehicle-action
registry is authoritative for maintained action identifiers and argument contracts. The
runtime retains a compatibility reader for existing custom `module` and `func` fields,
but maintained bindings and newly created canonical content use action identifiers. A
future graphical bindings matrix is a UI enhancement, not a prerequisite for registry
safety.

## Compatibility validation

Compare events emitted by profile rules and presence transitions with binding keys.

Report:

- emitted and bound;
- emitted but unbound;
- bound but not emitted;
- duplicate event definitions;
- actions requiring dynamic arguments that the rule cannot provide; and
- profile entries referencing undeclared buses.

Unbound events are normally warnings. Invalid action definitions are errors.

## Runtime validation

Preview validates:

- selected active bus is declared or is a documented legacy default;
- exactly one bus is active in V1;
- selected interface name is valid;
- one interface is not assigned ambiguously;
- generated udev configuration uses parsed values rather than caller text; and
- the selected source revisions still match catalogue revisions.

Interface absence is a warning, not an error.

## Custom-copy provenance

Creating a custom copy uses the installed maintained template. It never uses a mutable
checkout unless explicit development mode is active.

Store provenance in a sidecar file which the daemon does not parse:

```json
{
  "schema_version": 1,
  "kind": "profile",
  "id": "my-seat",
  "display_name": "My Seat",
  "template": {
    "source": "maintained",
    "id": "seat-leon-1p-pq35",
    "open_mmi_version": "v1-foundation-alpha-…",
    "revision": "sha256:…"
  },
  "created_at": "2026-07-19T12:00:00+00:00"
}
```

The sidecar supports later comparison. It does not authorize automatic merging.

The implemented copy route stores these private sidecars under
`~/.config/open-mmi/.open-mmi-provenance/<kind>/<id>.json`. The daemon never scans this
hidden tree. Profile data remains under `vehicles/<id>/config.json`; bindings data
remains under `bindings/<id>.json`. Maintained files are never opened for writing.

## JSON import

The dashboard can import a local `.json` file only as a new `custom` identity. The browser
sends the selected kind, a validated identifier and bounded JSON text; it never sends a
filesystem path. The backend parses with duplicate-key and non-finite-number rejection,
runs the same full profile or bindings validator used by catalogue discovery, and uses
private no-overwrite creation. Invalid content, an existing identifier or an untrusted
custom directory leaves no imported item behind.

Imported content is selected as an unapplied draft. It does not restart `canbusd`, alter
maintained content, or replace any existing custom item. Import provenance records the
creation origin separately from maintained-template provenance.

## Draft loading and saving

Loading returns:

- kind and identifier;
- exact UTF-8 JSON content;
- content revision; and
- validation result.

Saving requires the revision returned by load. A changed revision produces a conflict
instead of overwriting another browser or terminal edit.

Save sequence:

1. validate identifier and fixed root;
2. reject symlinks and non-regular targets;
3. compare expected content revision;
4. parse and validate submitted JSON;
5. verify the target identity has not changed during the save;
6. write and flush a private temporary sibling file;
7. atomically replace the draft and flush its directory; and
8. return the new revision, validation result and `applied: false`.

The current editor rejects invalid JSON and semantic validation errors before writing.
An existing invalid custom item remains selectable for recovery: its exact JSON can be
loaded into the editor, and the catalogue shows the first validation code/path, but Review
and Apply remain blocked until validation passes. Saving never restarts `canbusd` and never
changes maintained content or provenance. For coordinator-managed exact document paths, the
running daemon pins its successfully parsed profile and bindings revisions until restart;
periodic and SIGHUP reloads cannot activate a saved draft. Activation remains a separate
reviewed operation. Last-known-good user revision archives remain a later slice.

## Custom lifecycle operations

Duplicate, rename and delete are available only for fixed `custom` identities and
require the exact current content revision. They never accept a path or document body.
All three operations acquire the shared lifecycle lock so they cannot race a reviewed
apply or managed update.

- Duplicate is allowed for active or inactive valid custom items because it leaves the
  source untouched and creates a new private destination. Invalid content must be repaired
  before duplication.
- Rename is allowed only for inactive custom items, including invalid items that need
  recovery or cleanup. It moves the exact profile directory or bindings file, moves and
  updates provenance, and never changes the content revision.
- Delete is allowed only for inactive custom items, including invalid items, and requires
  explicit browser confirmation. The backend hides the exact item under a private temporary
  name before removing its content and provenance.
- Existing destination identifiers, stale revisions, unsafe permissions, symlinks, hard
  links, unsupported profile-directory contents and active identities fail closed.
- Lifecycle operations return `applied: false`; selection and activation remain a
  separate reviewed workflow.

Maintained entries never expose Edit, Duplicate, Rename or Delete. Their only custom
entry point remains **Use maintained … as template**, which creates a separate copy.

## Editor delivery order

1. Advanced JSON text editing with syntax and semantic validation.
2. Structured profile overview and bus metadata.
3. Registry-backed event-to-action bindings matrix.
4. Structured presence and event rule tables.
5. Structured status-rule editors per rule type.
6. Optional recorded-frame assistance as a later research feature.

A full status-rule editor is not a prerequisite for maintained profile selection or
custom bindings selection.

## Sacred-file rules

- Updates never overwrite, refresh, migrate or delete custom profile/bindings content.
- Apply never copies maintained content over an existing custom identifier.
- Returning to maintained clears explicit custom selection but leaves custom files
  untouched.
- Template updates may produce a comparison notice only.
- Any future migration of custom content is an explicit, previewed user action.
