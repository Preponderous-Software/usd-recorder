# Architecture

```text
src/usd_recorder/
  recorder.py   Recorder: per-tick validation, entity lifecycle, layers and chunk rotation
  track.py      Track: sparse sampling with hold keys for one attribute
  values.py     typed values: value_type, SDF_TYPES, colours, property names
  clips.py      stitch (atomic StitchClips + hooks), manifest defaults, flatten, has_clips
  atomic.py     atomic_export / atomic_write_bytes / atomic_write_text (temp file + os.replace)
  inspect.py    inspect_stage: a plain-data summary of a recording
  replay.py     the usd-replay/1 contract: ReplayWriter, read_replay, validate_replay, convert
  cli.py        usd-recorder flatten | inspect | replay {validate,info,convert}
cpp/include/usd_recorder/usda_writer.hpp    header-only C++17 .usda writer (Recorder layout)
cpp/include/usd_recorder/replay_writer.hpp  header-only C++17 .usda writer (replay contract)
```

## Time

One tick is one time code. Ticks are numbers (ints or floats) that strictly increase from call to
call; gaps are allowed. `time_codes_per_second` sets both `timeCodesPerSecond` and
`framesPerSecond`, so a USD player shows one tick per frame. The stage's `startTimeCode` and
`endTimeCode` are the first and last recorded ticks. This is acsf-usd-env's cycle-indexed time
(time code = cycle number) and viron-usd's tick-indexed time.

## Stage layout

```text
/World                         Xform, default prim (option `root`)
  extras:<key>                 stage-level typed values (option `namespace`)
  events:<channel>             string event channels (option `events_namespace`)
  Entities                     Scope (option `entities`)
    <name>                     Xform per entity; <name> = entity_name(id), default e_<id>
      recorder:id              the entity id (int or string), not time-sampled
      visibility               inherited while present, invisible otherwise
      xformOp:translate        double3, from the `position` key
      extras:<key>             typed values
      geom                     Cube (size) or Sphere (radius), or none (option `geometry`)
        primvars:displayColor  default colour, time-sampled once `color` is given
```

Stage metadata: `upAxis` (`Z` default, or `Y`), `metersPerUnit`, `defaultPrim`, and
`customLayerData` with `usdRecorder:version` plus anything passed as `metadata`.

## Sparse sampling

`Track` writes the first value; then nothing while the value is unchanged, but it remembers the
last unchanged time as the *pending hold*; on a change at `t` it writes the pending hold (old
value) and then the new value at `t`; `flush` writes any pending hold at the end of a layer. Every
observed time therefore resolves to its exact value, unchanged runs never interpolate, and a value
costs at most two samples per change. `hold(t)` marks a value unchanged without observing it (an
absent entity's values, an omitted key).

## Entity lifecycle

- **Present** at a tick: `visibility = inherited`; given keys are observed, omitted keys held.
- **Absent**: `visibility = invisible`; every value is held, so it neither interpolates while
  hidden nor towards wherever the entity reappears.
- **Late arrival** (first seen after the layer's first tick): an `invisible` sample is written at
  the layer's previous tick before the first `inherited` one. Without it the first sample would
  hold backwards and the entity would be visible before it existed.
- **Colour**: the geometry carries the default colour (option `default_color`, a colour or a
  function of the id) as a plain default. When `color` is first given after the entity's first
  tick in a layer, the default colour is sampled at the previous tick first, for the same reason.
- **Values defined from the first tick they are given.** Before that a key has no value
  (unchunked, USD holds its first sample backwards; chunked, earlier chunks have none). The
  round-trip test only compares a key from the tick it is first given.

## Typed values

`value_type` maps `bool`, `int` (signed 32-bit, else `ValueError`), `float` → `double`, `str` →
`string`, and three numbers → `double3`; anything else is a `TypeError`. A key keeps the type of
its first value per entity (and per stage for `extras`); a different type raises
`TypeChangeError`. Keys must be valid USD property names (`a`, `a:b_2`). `position` and `color`
are reserved entity keys.

`record` validates everything (tick order, positions, colours, types, names) before writing
anything, so a call that raises leaves the recording unchanged.

## Event channels

An event channel is a string attribute observed on *every* tick: the event text (several events
of one tick joined with `"\n"`) or `""`. Sampled through `Track`, that costs samples only around
events, yet no tick shows a stale string. A channel first seen after the layer's first tick gets
`""` at the previous tick; in chunked recordings the manifest carries `""` as the channel's
default for chunks before it existed. (acsf-usd-env reached the same semantics by writing every
channel densely on every cycle.)

A *string state* (a mood, a phase) is not an event: record it as a string extra and it holds
until it changes.

## Chunked recordings

With `chunk_size=N`, chunk *k* holds ticks `[first + k·N, first + (k+1)·N)` where *first* is the
first recorded tick; a range with no ticks produces no chunk. Chunks are written next to the root
as `<stem>.chunk_NNNN.usda` (`.usdc` with `binary`). On rotation the finished chunk is flushed and
exported atomically. A chunk declares only the entities present in it; when an entity is
declared in a later chunk, its last position, values and colour are re-authored at its first tick
there, so omitted keys keep their values across chunk boundaries. Stage extras are carried the
same way.

`close()` stitches the chunks into the `.usda` root with `UsdUtils.StitchClips`, which also
writes `<stem>.topology.usda` (every prim and attribute declared in any chunk) and
`<stem>.manifest.usda` (every attribute with time samples). The `scene` hook runs on the topology
layer, so static geometry is stored once. Chunk asset paths are recorded relative to the root, so
the directory is relocatable.

### The two value-clip traps

Found while spiking viron-usd on `usd-core` 26.8; `tests/test_clip_traps.py` reproduces each with
raw USD and checks the fix.

1. **Each chunk must author its own `startTimeCode`/`endTimeCode`.** `StitchClips` derives each
   clip's `active` and `times` from them. Without them every clip is stitched as active at time 0,
   USD warns `Clip 1 cannot be active at time 0.000 …` and the stage no longer resolves per tick.
   The recorder sets both on every chunk before exporting it.
2. **Values a clip has no samples for come from the manifest's defaults.** An entity first seen
   in chunk 3 has no `visibility` samples in chunks 1–2 and resolves to the schema fallback
   `inherited`: visible, at the origin, before it existed. Authoring `invisible` as the default
   **in the manifest** fixes it; the same default in the topology layer (or in the clip layers)
   is ignored during clip value resolution. The recorder authors `invisible` on every entity, the
   default colour on every animated `displayColor`, and `""` on every event channel, in the
   manifest.

### `stitch`

`stitch(root, chunks, clip_prim, topology=, manifest=, root=)` is the public form. It stitches
under temporary names in the same directory, runs the hooks, then moves the topology, the
manifest and finally the root over their final names, each with `os.replace`. A reader polling
the root never sees one whose topology or manifest is missing or stale. (The staging root's name
is rewritten to the final one in the references; the result is byte-identical to stitching in
place, which viron-usd's golden tests check.)

## Flatten

`flatten(src, out=None, binary=False)` opens the stage (clips and all), calls `Stage.Flatten()`
and exports one layer with every time sample resolved and no clip metadata, atomically. The
default output is `<stem>_flat.usda` (`.usdc` with `binary`); an output extension that disagrees
with `binary` is an error.

## Live recordings

`checkpoint()` (or `checkpoint_every=N`) publishes the recording so far: pending hold keys are
flushed (so values hold through the last recorded tick), the time range is set, and the layer is
exported atomically. In a chunked recording the open chunk is exported and the root re-stitched.
Every write goes to a temporary file in the destination directory and is moved over the
destination with `os.replace` (atomic on POSIX and Windows), so a follower opens either the
previous or the new complete file; `tests/test_live.py` follows a recording from another thread
while it checkpoints every tick. Because USD's layer registry caches open layers, a layer this
process already has open at a replaced path is reloaded.

This is acsf-usd-env's follow-mode guarantee, generalised: there the whole stage was rebuilt and
replaced per poll; here only the open layer (and, chunked, the small root/topology/manifest) is.

## Output formats

Time-sampled layers (the single layer, or the chunks) are `.usda` by default and `.usdc` with
`binary=True`. A chunked root, its topology and its manifest are always `.usda`: they are small
and hold the clip metadata a reader is most likely to inspect. An unchunked path's extension must
agree with `binary`.

## C++ writer

`cpp/include/usd_recorder/usda_writer.hpp` is a header-only C++17 writer (standard library only)
for the unchunked layout: `begin_tick` / `entity(id).position(...).set(key, value)` / `extra` /
`event` / `end_tick`, then `write(path)` (temporary file + `std::rename`). It implements the same
`Track` algorithm and lifecycle rules, so its time samples are identical to the Python
`Recorder`'s for the same input (`tests/test_cpp_writer.py`). Entity ids are 64-bit integers;
values are `bool`, `int32_t`, `double`, `std::string` and `Vec3`. The only options are
`time_codes_per_second` and `up_axis`; the rest of the layout is fixed at the `Recorder`'s
defaults (`/World`, `Entities`, `e_<id>` / `e_n<id>` names, `extras:` / `events:` namespaces,
`metersPerUnit = 1`, a unit `Cube` with the default colour). Two things differ: `recorder:id`
is `int64` (the `Recorder` writes `int`), and `customLayerData` carries
`usdRecorder:writer = "cpp"` instead of `usdRecorder:version`. Python-only: chunking, `binary`,
checkpoints, colours, the other geometry and naming options, string ids, `scene` and `metadata`.
A C++ run that needs chunks can be re-chunked in Python later.

## Dependencies

`usd-core` is pinned exactly (26.8): the value-clip behaviour the tests assert is usd-core's, and
the traps above were found on that version. Bumping it means re-running the trap tests, which is
what they are for. `numpy` (`>=1.26`) is a dependency of the replay contract only: `replay.py`
moves arrays through it and `read_replay` returns them.

## Replays

`replay.py` is a second, deliberately narrower layout for *sharing* a run: the
[`usd-replay/1` contract](docs/REPLAY.md). Where a `Recorder` stage has one Xform per entity and
sparse per-attribute samples (good for scrubbing a few hundred agents in usdview), a replay keeps
each kind of entity in one `UsdGeomPoints` prim sampled at every sample tick, so thousands of
entities over thousands of samples stay one compact `.usdc` that a player decodes without
composing anything. `ReplayWriter` builds it on an in-memory stage and exports it atomically on
`close()`; it validates a whole sample before writing any of it, and does not resample a field
whose colours did not change. `validate_replay` checks every rule of the contract and returns one
message per problem; `read_replay` loads a replay into numpy arrays (validating first).

Colours are converted from sRGB to linear on the way in (`srgb_to_linear`) and back with
`linear_to_srgb8`; the conversion round-trips every byte. The C++ `replay_writer.hpp` uses the
same formula (in double, then float), and `tests/test_cpp_replay.py` checks that its replay equals
the Python writer's sample for sample.

## Testing

- `test_roundtrip.py`: randomized record → read-back over six seeds × five chunk sizes (none,
  1, 4, 13, 1000), text and binary, composed and flattened; plus chunked vs unchunked equality.
- `test_clip_traps.py`: both traps, with raw USD and through the recorder.
- `test_clips.py`: the clip building blocks without a `Recorder`: chunk and flat names, `stitch`
  with all three hooks (and nothing published when a hook fails), `set_manifest_defaults`.
- `test_recorder.py`, `test_track.py`, `test_values.py`: behaviour and validation.
- `test_live.py`: follower thread, atomic failure modes, staging cleanup, relocation.
- `test_cli.py`: `flatten` (default output, `--binary`, extension mismatch), `inspect` (text,
  `--json`, a missing file), the replay actions' `--help`, no command, and
  `python -m usd_recorder.cli --version`.
- `test_example.py`: `random_walkers.py` records a chunked run; `grazers_replay.py` writes a
  valid replay whose sampling rate does not change the run.
- `test_cpp_writer.py`: compiles `cpp/tests/scenario.cpp` (skipped without a C++ compiler); its
  layer opens cleanly, resolves to the same values at every tick as the `Recorder`'s layer for
  the same scenario (`tests/cpp_scenario.py`), and keeps the entity lifecycle.
- `test_replay.py`: the replay writer and reader, colour round trip, the CLI, and the validator
  against a good replay broken one rule at a time (25 ways).
- `test_cpp_replay.py`: the C++ replay writer's output validates and equals the Python writer's.
