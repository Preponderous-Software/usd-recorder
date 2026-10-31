# Concept

## The problem

A simulation (an agent world, a grid game, a cognitive-architecture run, a cellular model) has a
state that changes every tick. To *look* at a run afterwards (scrub it, compare two runs, render
it, feed it to another tool) the run has to be stored as something more than logs. OpenUSD is a
good target: time-sampled attributes are first-class, the format is open and text-readable, and
usdview, Blender, Houdini, Omniverse and others open it.

Writing a correct time-sampled stage by hand is easy to get subtly wrong:

- Writing a sample every tick bloats the file; writing only on change makes values interpolate
  across unchanged runs. The fix is a hold key before every change.
- USD holds the first sample *backwards*: an entity that appears at tick 500 is visible from
  tick 0 unless an `invisible` sample precedes its first appearance.
- Strings and tokens never interpolate, they hold: a "what happened this tick" string shows on
  every later tick unless it is explicitly cleared.
- Long runs need chunking. Value clips solve it, but each chunk must author its own time range,
  and values a chunk has no samples for come from the clip *manifest's* defaults, not from the
  topology layer or the clip layers.
- A viewer following a live run must never open a half-written file.

Two projects solved these independently:
[viron-usd](https://github.com/Preponderous-Software/viron-usd) (an entity world recorded tick by
tick, chunked into value clips) and
[acsf-usd-env](https://github.com/Preponderous-Software/acsf-usd-env) (a cognitive-architecture run
exported cycle by cycle, followed live). `usd-recorder` is the shared library both patterns
come from, so the next simulation gets them for free.

## What it is

- A Python `Recorder` with one call per tick: `record(tick, entities, extras, events)`.
- The building blocks it is made of (sparse `Track`, typed values, clip stitching with manifest
  defaults, flatten, atomic export), public for consumers with their own stage layout.
- A replay writer and reader for the `usd-replay/1` contract ([docs/REPLAY.md](docs/REPLAY.md)):
  a compact, fixed layout for *sharing* a run, with a state hash per sample so the history can be
  checked by re-running the seed.
- A CLI to inspect and flatten recordings, and to validate, describe and convert replays.
- Header-only C++ writers for simulations written in C++ that want the same layouts (a recording
  or a replay) without linking OpenUSD, and a C++ replay reader (`cpp/reader`, an optional target
  that links OpenUSD) that reads replays the same way as the Python reader.

## What it is not

- Not a renderer or viewer. Use usdview, Blender, or
  [usd-web](https://github.com/Preponderous-Software/usd-web) for the browser.
- Not a simulation framework: it never drives the simulation, it only receives state.
- Not a general USD authoring toolkit: static scenes are authored by the consumer (the `scene`
  hook gives it the right layer to author into).

## Users

- **viron-usd** records Viron environments through the building blocks (its stage layout
  predates the library and is kept byte for byte, proven by golden-file tests).
- **New simulations** use `Recorder` directly.
- **C++ simulations** use the headers.
- **Replay players** (such as usd-web's) read replays through the contract without knowing the
  sim.

## Success criteria

1. Every recorded tick resolves to exactly the recorded values, chunked or not, text or binary,
   composed or flattened: a randomized record → read-back test checks it.
2. Both value-clip traps are encoded as tests that show the failure with raw USD and the fix.
3. A follower polling a live recording never fails to open it.
4. viron-usd's outputs are unchanged by moving onto the library.
