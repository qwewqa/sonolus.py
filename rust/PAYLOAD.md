# Engine Build Payload (version 1)

Specification of the single payload that `sonolus_backend.build_engine` (task T4.2)
receives from the Python frontend in **one FFI call** and turns into the six packaged
engine blobs (`EngineConfiguration`, `EnginePlayData`, `EngineWatchData`,
`EnginePreviewData`, `EngineTutorialData`, `EngineRom`). The Python half — tracing,
encoding, and payload assembly — is `sonolus/build/payload.py` (task T4.1); the wiring
of `package_engine` onto this path is task T4.3.

Unlike the CFG encoding ([ENCODING.md](ENCODING.md)), the payload is **not a serialized
byte format**: producer and consumer live in the same process, so the payload is a plain
Python object tree (dicts, lists, strs, ints, floats, bytes) crossing PyO3 directly.
This document is the contract for its shape and for the assembly semantics on both
sides. Producer and consumer ship in lockstep (same wheel), so there is no version
negotiation: the consumer rejects any `schema` other than the one it was built for.

The legacy reference for everything here is the frozen Python build path:
`sonolus/build/engine.py` (`package_engine`, `build_*_mode`, `package_rom`,
`package_data`) and `sonolus/build/compile.py` (`compile_mode`), executed on a
GIL-enabled interpreter (the `thread_pool is None` branch — see [§8](#8-determinism)).

## 1. Top-level payload

A Python `dict` with exactly these keys:

| Key             | Type          | Meaning |
|-----------------|---------------|---------|
| `schema`        | `int`         | Payload schema version. `1` for this document. Any other value is an error. |
| `level`         | `str`         | Optimization level for **all** work units: `"minimal"`, `"fast"`, or `"standard"` (the Rust pipeline prefixes; ARCHITECTURE.md §4). |
| `configuration` | `str`         | The complete `EngineConfiguration` JSON document, compact-serialized (`json.dumps(..., separators=(",", ":"))`) by Python from `build_engine_configuration(options, ui)`. Rust does **no** processing beyond UTF-8 encoding + gzip ([§7](#7-outputs-the-six-blobs)). |
| `rom`           | `list[float]` | ROM values as f64, in trace order. Never empty: the assembler applies the legacy `values or [0]` guard from `package_rom`, so the legacy-impossible empty case is already resolved to `[0.0]` here. |
| `modes`         | `dict`        | Exactly the four keys `"play"`, `"watch"`, `"preview"`, `"tutorial"`, in that insertion order. Always all four: mode selection (`BuildConfig.build_*`) is resolved by the assembler, which substitutes the legacy empty modes (`empty_play_mode()` etc.) exactly like `package_engine` does. |

Each value in `modes` is a `dict`:

| Key        | Type        | Meaning |
|------------|-------------|---------|
| `metadata` | `str`       | The mode-data JSON document, compact-serialized, in its **final shape minus per-node data** ([§3](#3-mode-metadata)). |
| `units`    | `list[dict]`| The mode's work units in canonical order ([§4](#4-work-units-and-canonical-node-ordering)). |

## 2. Work unit

Each element of `units` is a `dict`:

| Key         | Type          | Meaning |
|-------------|---------------|---------|
| `callback`  | `str`         | The runtime callback name (`CallbackInfo.name`, camelCase — e.g. `updateParallel`, `updateSpawn`). The same string legacy passes to `OptimizerConfig(callback=...)`. |
| `archetype` | `int \| None` | `None` for a mode-global callback. Otherwise the index into this mode's `metadata["archetypes"]` array of the **first** archetype entry whose base owns this callback (derived archetypes share their base's units; see [§3.2](#32-archetype-entries)). Informational + diagnostic; the metadata rewrite ([§5](#5-rust-side-assembly-t42)) does not depend on it beyond the `None`/not-`None` distinction. |
| `order`     | `int`         | The callback's `_callback_order_` (0 default). Always `0` for global callbacks. Duplicated in the metadata slot for archetype callbacks; carried here so a unit is self-describing. |
| `cfg`       | `bytes`       | The frontend CFG in the **v1 binary encoding** (ENCODING.md), produced by `sonolus.backend.encode.encode_cfg` from the traced, pre-optimization CFG. |

The unit's position in the `units` list is its **unit id**, referenced by the metadata
callback slots.

The optimizer context available to the Rust pipeline for a unit is `(mode, callback)` —
the mode from which `modes` key the unit came, and the `callback` string. This mirrors
legacy `OptimizerConfig(mode=..., callback=...)`; the legacy passes use it for
block-writability reasoning (`BlockData.writable`). The current Rust pipeline does not
consume it, but `build_engine` should thread it through so future passes can
(T4.2 note).

## 3. Mode metadata

`metadata` parses to a JSON object whose shape is **exactly** the legacy decompressed
mode data (what `unpackage_data(EnginePlayData)` etc. yields), with precisely two
substitutions:

1. Every **callback slot** holds a unit id instead of a node index:
   - a global callback slot (top-level key) is a bare integer unit id where legacy has
     a bare integer node index;
   - an archetype callback slot is `{"index": <unit id>, "order": <order>}` where
     legacy has `{"index": <node index>, "order": <order>}`.
2. The `"nodes"` key is present **with value `null`** in its legacy position (the node
   array is the per-node data T4.2 generates).

Everything else — key names, key order, nesting, value types, the int-vs-float
distinction of JSON numbers — is byte-for-byte the legacy JSON content. Key order is
**meaning-bearing**: Rust must parse and re-serialize with order preserved
(`serde_json` `preserve_order`, decision D5/D7 context).

The per-mode top-level key orders (produced by the legacy code paths, mirrored by the
assembler):

| Mode     | Top-level keys, in order |
|----------|--------------------------|
| play     | `archetypes`, `nodes`, `skin`, `effect`, `particle`, `buckets` |
| watch    | `updateSpawn`, `archetypes`, `nodes`, `skin`, `effect`, `particle`, `buckets` |
| preview  | `archetypes`, `nodes`, `skin` |
| tutorial | `preprocess`, `navigate`, `update`, `archetypes`, `nodes`, `skin`, `effect`, `particle`, `instruction` |

(Global callback keys come first because legacy `compile_mode` inserts them into
`results` before `"archetypes"`; the resource keys come last because the legacy
`build_*_mode` functions spread `compile_mode`'s dict first. `"archetypes"` is always
present — `package_engine` passes a list, `[]` for tutorial, to every mode.)

### 3.1 Global callback slots

One top-level key per mode-global callback: watch has `updateSpawn`; tutorial has
`preprocess`, `navigate`, `update` (in that order); play and preview have none. The set
of global-callback keys is **defined by the units list**: exactly the units with
`archetype is None`, whose `callback` strings name the keys (names are unique per mode).
This is how Rust locates them without a hardcoded key set.

### 3.2 Archetype entries

`metadata["archetypes"]` is an array with **one entry per archetype instance** in the
mode's archetype list (legacy: one per element of `archetypes`, spreading the shared
base entry). Entry key order:

```
name, hasInput, imports, [exports (play only)], <callback slots in callback order>
```

- `name`: the instance archetype's `name` (overrides the base's in place, keeping key
  position — legacy `{**base_entry, "name": a.name, "hasInput": a.is_scored}`).
- `hasInput`: the instance's `is_scored`.
- `imports`: `[{"name", "index"[, "def"]}, ...]` from the **base** archetype's
  `_imported_keys_` (the `def` key only when the import has a non-`None` default).
- `exports`: play mode only, the base's `[*_exported_keys_]`.
- Callback slots: one per supported, overridden (non-default) callback of the **base**
  archetype, in `_supported_callbacks_` declaration order, keyed by the camelCase
  callback name. Value: `{"index": <unit id>, "order": <order>}`.

Derived archetypes (`Archetype.derive`) share their base's compiled callbacks: multiple
entries reference the **same unit ids** (legacy: the same node indices). The contract
for identifying callback slots inside an entry: **every key other than `name`,
`hasInput`, `imports`, `exports` is a callback slot.** (Callback names are a closed,
schema-versioned set — `CallbackInfo.name` values — and can never collide with those
four.)

## 4. Work units and canonical node ordering

The `units` list order **is** the canonical node-construction order (the
"archetype order then callback order" contract from ARCHITECTURE.md §1 / the T4.2 task
row). For each mode it is:

1. For each **base** archetype, in first-encounter order of
   `getattr(a, "_derived_base_", a)` over the mode's archetype instance list:
   for each supported, overridden callback in `_supported_callbacks_` declaration
   order — one unit.
2. Then each mode-global callback, in the [§3.1](#31-global-callback-slots) order.

This equals the order in which the legacy GIL-build `compile_mode` calls
`OutputNodeGenerator.add` (archetype callbacks sequentially, then globals), so node
indices produced per [§5](#5-rust-side-assembly-t42) line up with a sequential legacy
build.

## 5. Rust-side assembly (T4.2)

`build_engine(payload)` is a **pure function** (invariant §3.8; D6): no state crosses
calls. Per mode:

1. **Compile** every unit at `level`: decode `cfg` (ENCODING.md), run the pipeline,
   emit the engine-node tree. Units are independent — rayon parallelism with the GIL
   released is licensed — but all output-ordering steps below use **unit list order**,
   never completion order (invariant §3.5).
2. **Dedup compilation** statelessly within the call (D6): two units (in any mode)
   whose `cfg` **bytes are identical** have identical compilation output; compile once.
   The dedup identity is the encoded byte string itself. An implementation may key by a
   hash of the bytes, but a bare hash without byte-equality confirmation is only
   acceptable if collision-resistant (≥128 bits); a 64-bit hash must fall back to byte
   comparison on hits. (The reference key exposed for tests is
   `sonolus.build.payload.dedup_key` = SHA-256 of the bytes.)
3. **Build the mode node array**: process units in list order; for each, add its node
   tree to the array with the legacy `OutputNodeGenerator` semantics — children before
   parents, structural dedup across the *entire mode array* (sub-trees shared between
   different callbacks of the same mode get one entry). `node_index(unit)` is the array
   index of the unit's root node. The faithful port already exists
   (`sonolus-backend-core::output::generate_output_nodes`, incl. the Python dict-key
   equality pitfalls: `5 == 5.0`, `0.0 == -0.0`, first-encountered representation
   wins, NaN constants impossible); T4.2 extends its dedup map and node vector to
   span all units of a mode instead of one tree.
4. **Rewrite the metadata** (parsed with key order preserved):
   - for every unit with `archetype is None`: `metadata[unit.callback]` holds the unit
     id (validate) and is replaced by `node_index(unit)`;
   - for every entry of `metadata["archetypes"]`, for every callback slot
     ([§3.2](#32-archetype-entries) rule): `slot["index"]` holds a unit id and is
     replaced by `node_index(unit)` (`order` untouched);
   - `metadata["nodes"]` (must be `null`) is replaced by the node array.
5. **Serialize** the rewritten document with the CPython-exact JSON serializer
   (`collection::pyjson`, compact separators), preserving key order, and gzip with
   `mtime=0`.

Plus, once per call:

6. **ROM**: pack `rom` as little-endian **f32** and gzip (`mtime=0`). T4.2 note: legacy
   `struct.pack("<f", v)` raises `OverflowError` for finite `|v| > f32::MAX`, while a
   bare Rust `as f32` cast would yield ±inf — `build_engine` must error (not saturate)
   for parity. NaN/±inf pack fine on both sides.
7. **Configuration**: UTF-8 encode the `configuration` string as-is and gzip
   (`mtime=0`).
8. Return the six blobs (`PackagedEngine` shape). *(T4.2 amendment, pinning the
   FFI shape the implementation chose:* `sonolus_backend.build_engine(payload)`
   *returns a `dict` with the legacy dataclass's field names —*
   `configuration`, `play_data`, `watch_data`, `preview_data`, `tutorial_data`,
   `rom` *— each a gzipped `bytes` value, so `PackagedEngine(**result)`
   constructs the legacy object directly. Error surface: `OverflowError` with
   the exact `struct.pack` message for ROM overflow ([§5.6](#5-rust-side-assembly-t42));
   `ValueError` for everything else, with unit compilation failures carrying
   the pipeline message verbatim — e.g. `Temporary memory limit exceeded` —
   for legacy parity.)*

Errors anywhere (decode failure, budget exhaustion, slot/unit-id validation,
ROM overflow) abort the whole call with an exception; there are no partial results.

## 6. Optimization level

`level` selects the pipeline prefix for every unit in the call: `"minimal"` (-O0),
`"fast"` (-O1), `"standard"` (-O2). The assembler maps the legacy
`BuildConfig.passes` values onto these names via
`sonolus.build.payload.level_from_passes` (identity with the three published pass
tuples); arbitrary custom pass sequences are not representable — T4.3 resolves what the
CLI/dev-server pass (they only ever use the three published levels).

## 7. Outputs (the six blobs)

gzip **bytes** are not part of the contract (ARCHITECTURE.md §7) — decompressed content
is. What T4.2's A/B against the legacy sequential build must show (its DoD):

| Blob | Required relationship to legacy |
|------|--------------------------------|
| `EngineConfiguration` | Decompressed bytes **identical** (the JSON string crosses pre-serialized). |
| `EngineRom` | Decompressed bytes **identical** (same trace ⇒ same values; same f32 packing). |
| `EnginePlayData` / `EngineWatchData` / `EnginePreviewData` / `EngineTutorialData` | Decompressed JSON **structurally identical except** the parts derived from compilation output: callback `index` values, global-callback integer values, and the `"nodes"` array contents (the Rust optimizer is redesigned, D2 — node trees legitimately differ). Key order, key sets, archetype entries, `order` values, imports/exports, skin/effect/particle/buckets/instruction must be equal. Behavior of each callback's node tree vs legacy: **per-callback differential interpretation**. |

## 8. Determinism

- The assembler traces **sequentially** in payload order: play → watch → preview →
  tutorial; within a mode, units in canonical order ([§4](#4-work-units-and-canonical-node-ordering)).
  The ROM (and the `BlockPlace` ROM indices baked into the encoded CFGs) is a function
  of this trace order, because all four modes share one `ProjectContextState`/
  `ReadOnlyMemory` — this is the one cross-mode coupling in the engine build. The
  sequential order matches the legacy GIL build exactly. (The legacy **free-threaded**
  path traces modes concurrently, making its ROM layout and node-array order
  timing-dependent; the payload path deliberately does not reproduce that
  nondeterminism. Parallelizing the *trace* is a possible T4.3+ follow-up and would
  need a deterministic ROM-merge design first.)
- Same engine + config + level ⇒ same payload, bit-for-bit (`metadata` strings, `cfg`
  bytes, `rom`), and `build_engine` must map the same payload to the same six blobs,
  including under rayon (invariant §3.5).
- Dict insertion order is meaning-bearing everywhere: payload key order, `modes` order,
  metadata JSON key order, unit list order.

## 9. Version history

- **v1** — initial schema (this document).
