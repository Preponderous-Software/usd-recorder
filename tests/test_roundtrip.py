"""Randomized record -> read-back equivalence.

Random recordings (entities appearing late, disappearing and coming back, moving, changing
colour, typed values of every type given on some ticks only, stage-level extras, event channels)
are recorded unchunked and with several chunk sizes, text and binary, and read back through
USD (value clips resolved) and through the flattened layer. At every recorded tick the stage
must resolve to what a reference model of the recording rules says.
"""

from __future__ import annotations

import random

import pytest
from pxr import Usd, UsdGeom

from usd_recorder import Recorder, flatten
from usd_recorder.recorder import DEFAULT_COLOR

TYPES = ("bool", "int", "double", "string", "double3")


def _value(rng: random.Random, kind: str):
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.randrange(-5, 5)
    if kind == "double":
        return rng.choice([0.0, 0.5, rng.uniform(-100, 100)])
    if kind == "string":
        return rng.choice(["", "idle", "hunting", "line one\nline two", "ünïcode"])
    return (rng.uniform(-1, 1), 0.0, rng.choice([0.0, 2.5]))


def scenario(seed: int, ticks: int = 60):
    """A list of ``(tick, entities, extras, events)`` with random gaps between ticks."""
    rng = random.Random(seed)
    ids = [1, 2, 3, "scout", "a-b c", 7, 8]
    first = {e: rng.choice([0, 0, rng.randrange(ticks)]) for e in ids}
    keys = {f"k{i}": rng.choice(TYPES) for i in range(5)}
    frames, t = [], rng.randrange(0, 5)
    for i in range(ticks):
        entities = {}
        for e in ids:
            if i < first[e] or rng.random() < 0.15:
                continue
            values = {}
            if rng.random() < 0.7:
                values["position"] = (rng.randrange(10), rng.randrange(10), rng.choice([0.0, 0.5]))
            if rng.random() < 0.2:
                values["color"] = (rng.random(), rng.random(), rng.random())
            for key, kind in keys.items():
                if rng.random() < 0.3:
                    values[key] = _value(rng, kind)
            entities[e] = values
        extras = {}
        if rng.random() < 0.5:
            extras["clock"] = float(t) * 0.25
        if rng.random() < 0.2:
            extras["phase"] = rng.choice(["day", "night"])
        events = {}
        if rng.random() < 0.2:
            events["log"] = rng.choice(["spawn", ["a", "b"]])
        if i > ticks // 2 and rng.random() < 0.3:
            events["alarm"] = "!"
        frames.append((t, entities, extras, events))
        t += rng.choice([1, 1, 1, 2, 5])
    return frames


def expected(frames):
    """The reference model: per tick, what every query must resolve to."""
    state: dict = {}
    extras: dict = {}
    out = []
    for t, entities, ex, ev in frames:
        for e, values in entities.items():
            s = state.setdefault(e, {})
            for key, value in values.items():
                s[key] = tuple(float(c) for c in value) if isinstance(value, (tuple, list)) else value
        extras.update(ex)
        out.append(
            (
                t,
                {e: dict(state[e]) for e in entities},
                set(state) - set(entities),
                dict(extras),
                {c: ("\n".join(v) if isinstance(v, list) else v) for c, v in ev.items()},
            )
        )
    return out


def record(path, frames, chunk_size, binary):
    with Recorder(path, 10, chunk_size=chunk_size, binary=binary) as rec:
        for t, entities, extras, events in frames:
            rec.record(t, entities, extras, events)
        prims = {e: rec.entity_path(e) for e in rec.entity_ids()}
    return prims


def close(a, b, tol=1e-9):
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, abs=tol)
    if hasattr(a, "__len__") and not isinstance(a, str):
        return len(a) == len(b) and all(close(x, y, tol) for x, y in zip(a, b, strict=True))
    return a == b


def check(stage: Usd.Stage, prims, frames):
    world = stage.GetPrimAtPath("/World")
    channels = {c for _, _, _, ev in frames for c in ev}
    for t, present, absent, extras, events in expected(frames):
        tc = Usd.TimeCode(t)
        for e, values in present.items():
            prim = stage.GetPrimAtPath(prims[e])
            assert UsdGeom.Imageable(prim).ComputeVisibility(tc) == UsdGeom.Tokens.inherited, (t, e)
            if "position" in values:
                assert close(tuple(prim.GetAttribute("xformOp:translate").Get(tc)), values["position"]), (t, e)
            color = prim.GetChild("geom").GetAttribute("primvars:displayColor").Get(tc)[0]
            # displayColor is float32
            assert close(tuple(color), values.get("color", DEFAULT_COLOR), 1e-6), (t, e, tuple(color))
            for key, value in values.items():
                if key in ("position", "color"):
                    continue
                got = prim.GetAttribute(f"extras:{key}").Get(tc)
                got = tuple(got) if hasattr(got, "__len__") and not isinstance(got, str) else got
                assert close(got, value), (t, e, key, got, value)
        for e in absent:
            prim = stage.GetPrimAtPath(prims[e])
            assert UsdGeom.Imageable(prim).ComputeVisibility(tc) == UsdGeom.Tokens.invisible, (t, e)
        for e in set(prims) - set(present) - absent:  # not seen yet: hidden before it exists
            prim = stage.GetPrimAtPath(prims[e])
            assert UsdGeom.Imageable(prim).ComputeVisibility(tc) == UsdGeom.Tokens.invisible, (t, e, "late")
        for key, value in extras.items():
            assert close(world.GetAttribute(f"extras:{key}").Get(tc), value), (t, key)
        for channel in channels:
            assert world.GetAttribute(f"events:{channel}").Get(tc) == events.get(channel, ""), (t, channel)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("chunk_size", [None, 1, 4, 13, 1000])
def test_record_read_back(tmp_path, seed, chunk_size):
    frames = scenario(seed)
    binary = seed % 2 == 1
    name = "run.usdc" if binary and chunk_size is None else "run.usda"
    prims = record(tmp_path / name, frames, chunk_size, binary)
    stage = Usd.Stage.Open(str(tmp_path / name))
    assert stage.GetCompositionErrors() == []
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (frames[0][0], frames[-1][0])
    check(stage, prims, frames)
    flat = flatten(tmp_path / name, tmp_path / "flat.usda")
    check(Usd.Stage.Open(str(flat)), prims, frames)


def test_chunked_and_unchunked_resolve_identically(tmp_path):
    frames = scenario(42, ticks=80)
    prims = record(tmp_path / "one.usda", frames, None, False)
    record(tmp_path / "many.usda", frames, 6, False)
    a, b = Usd.Stage.Open(str(tmp_path / "one.usda")), Usd.Stage.Open(str(tmp_path / "many.usda"))
    for t, _, _, _ in frames:
        for path in prims.values():
            pa, pb = a.GetPrimAtPath(path), b.GetPrimAtPath(path)
            va = UsdGeom.Imageable(pa).ComputeVisibility(Usd.TimeCode(t))
            assert va == UsdGeom.Imageable(pb).ComputeVisibility(Usd.TimeCode(t)), (t, path)
            if va == UsdGeom.Tokens.inherited:
                for attr in pa.GetAttributes():
                    # A value is defined from the first tick it is given (ARCHITECTURE.md).
                    if attr.GetNumTimeSamples() and attr.GetTimeSamples()[0] <= t:
                        other = pb.GetAttribute(attr.GetName()).Get(Usd.TimeCode(t))
                        assert close(attr.Get(Usd.TimeCode(t)), other, 1e-6), (t, path, attr.GetName())
