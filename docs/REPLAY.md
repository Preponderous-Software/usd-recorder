# Replay contract: `usd-replay/1`

A small, fixed OpenUSD layout for **sharing a simulation run**: what moved where, what colour
the world was, and a state hash per sample so anyone can check that re-running the same sim with
the same seed gives the same history. A producer (a sim, an exporter) writes it; a consumer (a
web player such as [usd-web](https://github.com/Preponderous-Software/usd-web), Blender,
usdview) reads it without knowing anything about the sim.

It is deliberately narrower than what `Recorder` can write: a `Recorder` stage has one prim per
entity and is good for scrubbing a few hundred agents in usdview; a replay keeps each *kind* of
entity in one `Points` prim, so thousands of entities over thousands of samples stay small and
cheap to decode.

```text
replay.usdc                     one layer, no sublayers, references or value clips
  customLayerData.usdReplay     schema, sim, simVersion, seed, tickRate, stateHashAlgorithm, ...
  /Replay                       Xform, defaultPrim
    replay:stateHash            string, sampled at EVERY sample tick (defines the sample ticks)
    replay:stat:<name>          double, optional, sampled at sample ticks (HUD numbers)
    Entities                    Scope
      <set>                     Points: one kind of entity
        ids                     int64[],   sampled at every sample tick
        points                  point3f[], sampled at every sample tick, same length as ids
        widths                  float[],   constant (1 value, unsampled) or vertex (sampled)
        primvars:displayColor   color3f[], constant (1 value, unsampled) or vertex (sampled)
        replay:label            string (legend text), optional
        replay:shape            token: sphere | cube (a hint for players), optional
    Fields                      Scope, optional
      <field>                   Mesh: a cols x rows grid of unit quads, static topology
        replay:field:size       int2   (cols, rows)
        replay:field:origin     double2 (x, y of the grid's minimum corner)
        replay:field:cellSize   double
        primvars:displayColor   color3f[cols*rows], uniform, sampled at a subset of sample ticks
        replay:label            string, optional
    Scene                       Scope, optional: static Points (obstacles, landmarks), never sampled
```

## Stage

- **One layer.** The root layer holds everything: no sublayers, references, payloads or value
  clips. A chunked `Recorder` run is flattened first; a consumer never has to compose.
  `.usdc` is the format to publish (arrays are compact and deduplicated); `.usda` is fine for
  tests and for writers without OpenUSD (the C++ header below writes `.usda`;
  `usd-recorder replay convert` turns it into `.usdc`).
- **`defaultPrim = "Replay"`**, an `Xform`.
- **`upAxis = "Z"`.** The ground is the XY plane; a 2D sim writes `z = 0` (or a small height
  to lift entities above a field). `metersPerUnit` is the producer's (default 1).
- **Time code = sim tick.** Sample ticks are integers and strictly increase; gaps are allowed
  and expected (record every N ticks). `startTimeCode`/`endTimeCode` are the first and last
  sample ticks. `timeCodesPerSecond` (and `framesPerSecond`) is a suggested *playback* rate in
  ticks per second, not the sim's own rate.

## Metadata: `customLayerData["usdReplay"]`

| Key | Type | Required | Meaning |
|---|---|---|---|
| `schema` | string | yes | `"usd-replay/1"` |
| `sim` | string | yes | the producer, e.g. `"microbiome"` |
| `simVersion` | string | yes | its version or commit, e.g. `"0.3.0+c428ff8"` |
| `seed` | string | yes | the seed in decimal (a string, so 64-bit unsigned seeds survive) |
| `tickRate` | double | yes | sim ticks per simulated second (the sim's clock, not playback) |
| `stateHashAlgorithm` | string | yes | what `replay:stateHash` fingerprints, e.g. `"strata.sim.StateHasher (FNV-1a 64)"` |
| `title` | string | no | a human title for players |
| `description` | string | no | one or two sentences |
| `source` | string | no | where the run came from (command line, repo URL at a commit) |
| `params` | dictionary | no | any other run parameters (grid size, tick count, ...) |

## The state hash

`/Replay.replay:stateHash` is a lowercase hex string (8 to 64 digits) authored at **every**
sample tick: the sim's own fingerprint of its complete state *after* that tick, by the
algorithm named in `stateHashAlgorithm`. Its time samples define the sample ticks: every other
time-sampled attribute in the replay is sampled only at those ticks.

It is the determinism proof: re-run `sim` at `simVersion` with `seed` to any sample tick, hash
the state the same way and compare. A producer must compute it from the sim's state, never from
what was recorded, and recording must not change it (each producer tests that its hash is the
same with and without recording). A producer whose sim has no state hash may hash its exported
frame instead, and must say so in `stateHashAlgorithm` (for example
`"export-frame SHA-256/64"`): that proves the export is reproducible, not the sim.

## Entities

Every child of `/Replay/Entities` is a `UsdGeomPoints` prim holding one kind of entity (a
species, settlers, pawns). At **every** sample tick it has a sample of `ids` and of `points`
with the same length (zero is fine: the set is empty at that tick). `ids` are unique within a
sample and stable across samples for the same entity, so a player can match an entity from one
sample to the next and interpolate its position; an id that disappears is gone (or hidden), one
that appears is new.

`widths` (diameter, in scene units) and `primvars:displayColor` are each either:

- **constant**: interpolation `constant`, one value, authored as a default (not sampled), for a
  set whose entities all look alike; or
- **vertex**: interpolation `vertex`, sampled at every sample tick with one value per point.

`replay:label` names the set in a legend; `replay:shape` (`sphere` or `cube`) hints how to draw
a point. Players draw points as instanced shapes of the given width.

## Fields

Every child of `/Replay/Fields` is a `UsdGeomMesh` covering a `cols x rows` grid: cell `(i, j)`
spans `origin + (i, j) * cellSize` to `origin + (i + 1, j + 1) * cellSize`, row-major from the
minimum corner (`j * cols + i`). The mesh is static (`(cols+1)*(rows+1)` points at a constant
`z`, `cols*rows` quads), so any USD tool draws it.

What changes is `primvars:displayColor` with `uniform` interpolation: one colour per cell,
`cols * rows` values. It is sampled at the first sample tick and then at **any subset** of the
sample ticks: a field that did not change since its last sample is simply not resampled.
Between samples a player **holds** the latest colour at or before the tick (it does not blend
colours). Fields are colours, not raw values: the producer chooses its colour map, and keeps
the grid coarse enough to publish (64 x 64 or less is typical).

## Stats

Optional `double` attributes `/Replay.replay:stat:<name>` (population, food, nations, ...)
sampled at some or all sample ticks, held between samples like fields. Players can show them
as a readout or sparkline.

## Scene

Optional static `UsdGeomPoints` under `/Replay/Scene` (grains, rocks, landmarks), with the same
`widths`/`displayColor` rules as entities but **no time samples at all**.

## Colours

`primvars:displayColor` is in linear Rec.709, as USD expects. Writers take the usual sRGB
colours (hex or 0-255) and convert; players convert back to sRGB for display.

## Validating

```bash
usd-recorder replay validate run.usdc        # exit 0 = conforms; prints problems otherwise
usd-recorder replay info run.usdc            # metadata, sample ticks, sets, fields, sizes
usd-recorder replay convert run.usda run.usdc
```

```python
from usd_recorder.replay import validate_replay, read_replay

problems = validate_replay("run.usdc")  # [] when it conforms
replay = read_replay("run.usdc")  # metadata, ticks, hashes and arrays per sample
```

C++ consumers use `usd_recorder::read_replay` from `cpp/reader` (it links OpenUSD; see the
README's "C++ reader"). It returns the same data as the Python `read_replay`, refuses any major
version but 1, and checks only what it needs to index the arrays; it is not a validator.

The validator checks every rule above (layer shape, metadata, sample ticks, per-set lengths
and id uniqueness, interpolation, field sizes and sample subsets, static scene) and reports
each problem with its prim path. `tests/test_replay.py` breaks a good replay one rule at a
time and checks that each is caught.

## Writing one

Python (needs `usd-core`):

```python
from usd_recorder.replay import ReplayWriter

with ReplayWriter(
    "run.usdc",
    sim="mysim",
    sim_version="1.2.0",
    seed=7,
    tick_rate=10,
    state_hash_algorithm="mysim fingerprint (FNV-1a 64)",
    playback_rate=60,
) as w:
    w.entity_set("prey", label="Prey", color="#5a9e4b", width=1.0)
    w.field("food", label="Food", cols=32, rows=32, origin=(0, 0), cell_size=4.0)
    for tick in range(0, 3000, 10):
        sim.run_until(tick)
        w.sample(
            tick,
            state_hash=f"{sim.hash():016x}",
            entities={"prey": {"ids": sim.ids, "points": sim.xyz}},
            fields={"food": sim.food_rgb},  # (cols*rows, 3) sRGB bytes
            stats={"population": len(sim.ids)},
        )
```

C++ (no dependencies, writes `.usda`): `cpp/include/usd_recorder/replay_writer.hpp`, the same
calls (`entity_set`, `field`, `scene_points`, `begin_sample`/`entities`/`field_colors`/`stat`/
`end_sample`, `write`). `tests/test_cpp_replay.py` compiles a scenario and checks that its
output validates and matches the Python writer's for the same input.

## Versioning

The schema string is `usd-replay/<major>`. Additive, optional keys keep the major version;
anything that changes what a valid `usd-replay/1` file means bumps it, and players refuse a
major version they do not know.
