"""The usd-replay/1 contract: writer, reader, validator (each rule broken once) and CLI."""

from __future__ import annotations

import json

import numpy as np
import pytest
from pxr import Sdf, Usd, UsdGeom, Vt

from usd_recorder.cli import main
from usd_recorder.replay import (
    SCHEMA,
    ReplayWriter,
    linear_to_srgb8,
    read_replay,
    replay_info,
    srgb_to_linear,
    validate_replay,
)


def make(path, ticks=range(0, 50, 10), **kw):
    rng = np.random.default_rng(0)
    with ReplayWriter(
        path, sim="demo", sim_version="1.0", seed=7, tick_rate=10, state_hash_algorithm="test", **kw
    ) as w:
        w.entity_set("prey", label="Prey", color="#5a9e4b", width=0.5)
        w.entity_set("hunters", color=None, width=None, shape="cube")
        w.field("food", label="Food", cols=4, rows=3, origin=(-2, -1.5), cell_size=1.0)
        w.scene_points("rocks", [[1, 1, 0], [2, 2, 0]], widths=[1, 2], color="#777777")
        for t in ticks:
            n = t // 10
            w.sample(
                t,
                state_hash=f"{t * 7919:016x}",
                entities={
                    "prey": {"ids": list(range(n)), "points": rng.random((n, 3))},
                    "hunters": {"ids": [42], "points": [[t, 0, 0]], "colors": [[200, 30, 30]], "widths": [1.5]},
                },
                fields={"food": np.full((12, 3), 100 if t < 30 else 200, dtype=np.uint8)},
                stats={"population": n + 1},
            )
    return path


def test_round_trip(tmp_path):
    path = make(tmp_path / "r.usdc")
    assert validate_replay(path) == []
    r = read_replay(path)
    assert r.meta == {
        "schema": SCHEMA,
        "sim": "demo",
        "simVersion": "1.0",
        "seed": "7",
        "tickRate": 10.0,
        "stateHashAlgorithm": "test",
    }
    assert r.ticks == [0, 10, 20, 30, 40] and r.hashes[2] == f"{20 * 7919:016x}"
    prey, hunters = r.sets
    assert [len(i) for i in prey.ids] == [0, 1, 2, 3, 4] and prey.label == "Prey" and not prey.vertex_color
    assert hunters.shape == "cube" and hunters.vertex_color and hunters.vertex_width
    np.testing.assert_array_equal(linear_to_srgb8(hunters.colors[0]), [[200, 30, 30]])
    np.testing.assert_array_equal(linear_to_srgb8(prey.colors), [[0x5A, 0x9E, 0x4B]])
    (food,) = r.fields
    assert food.ticks == [0, 30] and (food.cols, food.rows, food.origin) == (4, 3, (-2.0, -1.5))
    assert r.stats["population"] == ([0, 10, 20, 30, 40], [1.0, 2.0, 3.0, 4.0, 5.0])
    assert len(r.scene[0].points) == 2
    stage = Usd.Stage.Open(str(path))
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode(), UsdGeom.GetStageUpAxis(stage)) == (0, 40, "Z")


def test_colour_conversion_round_trips_every_byte():
    b = np.arange(256, dtype=np.uint8)
    rgb = np.stack([b, b[::-1], b], axis=1)
    np.testing.assert_array_equal(linear_to_srgb8(srgb_to_linear(rgb).astype(np.float32)), rgb)
    np.testing.assert_allclose(srgb_to_linear("#ffffff"), [[1, 1, 1]])
    with pytest.raises(ValueError):
        srgb_to_linear("#12345")


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda w: w.sample(5, state_hash="XYZ", entities={"a": {"ids": [], "points": []}}), "hex"),
        (lambda w: w.sample(5.5, state_hash="0" * 16, entities={"a": {"ids": [], "points": []}}), "integers"),
        (lambda w: w.sample(5, state_hash="0" * 16, entities={}), "missing"),
        (
            lambda w: w.sample(5, state_hash="0" * 16, entities={"a": {"ids": [1, 1], "points": [[0, 0, 0]] * 2}}),
            "unique",
        ),
        (lambda w: w.sample(5, state_hash="0" * 16, entities={"a": {"ids": [1], "points": []}}), "1 ids but 0 points"),
        (
            lambda w: w.sample(5, state_hash="0" * 16, entities={"a": {"ids": [], "points": [], "colors": []}}),
            "constant colour",
        ),
        (
            lambda w: w.sample(5, state_hash="0" * 16, entities={"a": {"ids": [], "points": []}, "zz": {}}),
            "not declared",
        ),
    ],
)
def test_writer_rejects_bad_samples(tmp_path, call, match):
    w = ReplayWriter(tmp_path / "x.usda", sim="s", sim_version="1", seed=1, tick_rate=1, state_hash_algorithm="t")
    w.entity_set("a")
    with pytest.raises((ValueError, KeyError), match=match):
        call(w)


def test_writer_rejects_bad_setup(tmp_path):
    with pytest.raises(ValueError):
        ReplayWriter(tmp_path / "x.txt", sim="s", sim_version="1", seed=1, tick_rate=1, state_hash_algorithm="t")
    with pytest.raises(ValueError, match="seed"):
        ReplayWriter(tmp_path / "x.usda", sim="s", sim_version="1", seed=-1, tick_rate=1, state_hash_algorithm="t")
    w = ReplayWriter(tmp_path / "x.usda", sim="s", sim_version="1", seed=1, tick_rate=1, state_hash_algorithm="t")
    w.entity_set("a")
    with pytest.raises(ValueError, match="already"):
        w.entity_set("a")
    w.field("f", cols=1, rows=1)
    with pytest.raises(ValueError, match="every field"):
        w.sample(0, state_hash="0" * 8, entities={"a": {"ids": [], "points": []}})
    w.sample(0, state_hash="0" * 8, entities={"a": {"ids": [], "points": []}}, fields={"f": [[1, 2, 3]]})
    with pytest.raises(ValueError, match="does not follow"):
        w.sample(0, state_hash="0" * 8, entities={"a": {"ids": [], "points": []}})
    with pytest.raises(RuntimeError, match="before the first sample"):
        w.entity_set("b")
    w.close()
    assert validate_replay(tmp_path / "x.usda") == []


def _break(src, dst, edit):
    stage = Usd.Stage.Open(str(src))
    edit(stage)
    stage.GetRootLayer().Export(str(dst))
    return dst


def _set_attr(path, value, time=None):
    def edit(stage):
        attr = stage.GetAttributeAtPath(path)
        attr.Set(value, time) if time is not None else attr.Set(value)

    return edit


def _meta(**changes):
    def edit(stage):
        layer = stage.GetRootLayer()
        d = dict(layer.customLayerData)
        meta = dict(d["usdReplay"])
        for k, v in changes.items():
            if v is None:
                meta.pop(k, None)
            else:
                meta[k] = v
        d["usdReplay"] = meta
        layer.customLayerData = d

    return edit


BREAKS = {
    "schema": (_meta(schema="usd-replay/9"), "is not 'usd-replay/1'"),
    "no seed": (_meta(seed=None), "missing 'seed'"),
    "seed type": (_meta(seed="-3"), "decimal digits"),
    "tick rate": (_meta(tickRate=0.0), "positive"),
    "up axis": (lambda s: UsdGeom.SetStageUpAxis(s, UsdGeom.Tokens.y), "upAxis"),
    "default prim": (lambda s: s.GetRootLayer().ClearDefaultPrim(), "defaultPrim"),
    "extra root": (lambda s: s.DefinePrim("/Other"), "only /Replay"),
    "bad hash": (_set_attr("/Replay.replay:stateHash", "NOT-HEX", 20), "lowercase hex"),
    "fraction tick": (_set_attr("/Replay.replay:stateHash", "abcdef12", 25.5), "integers"),
    "time range": (lambda s: s.SetEndTimeCode(99), "start/endTimeCode"),
    "missing ids": (lambda s: s.GetAttributeAtPath("/Replay/Entities/prey.ids").ClearAtTime(20), "every sample tick"),
    "length": (_set_attr("/Replay/Entities/prey.ids", Vt.Int64Array([1, 2, 3]), 10), "ids but"),
    "dupe ids": (_set_attr("/Replay/Entities/prey.ids", Vt.Int64Array([5, 5]), 20), "unique"),
    "stray sample": (_set_attr("/Replay/Entities/prey.points", Vt.Vec3fArray(0), 15), "extra 15"),
    "vertex colour count": (
        _set_attr("/Replay/Entities/hunters.primvars:displayColor", Vt.Vec3fArray(2), 10),
        "one colour per point",
    ),
    "constant sampled": (
        _set_attr("/Replay/Entities/prey.primvars:displayColor", Vt.Vec3fArray(1), 10),
        "must not be time-sampled",
    ),
    "field size": (_set_attr("/Replay/Fields/food.primvars:displayColor", Vt.Vec3fArray(5), 0), "cols*rows"),
    "field off-tick": (
        _set_attr("/Replay/Fields/food.primvars:displayColor", Vt.Vec3fArray(12), 7),
        "not a sample tick",
    ),
    "field topology": (_set_attr("/Replay/Fields/food.points", Vt.Vec3fArray(3)), "(cols+1)*(rows+1)"),
    "stat type": (
        lambda s: s.GetPrimAtPath("/Replay").CreateAttribute("replay:stat:bad", Sdf.ValueTypeNames.Int).Set(3, 10),
        "double",
    ),
    "scene sampled": (_set_attr("/Replay/Scene/rocks.points", Vt.Vec3fArray(2), 10), "static"),
    "set type": (lambda s: s.DefinePrim("/Replay/Entities/blob", "Xform"), "must be Points"),
    "stray attribute": (
        lambda s: s.GetPrimAtPath("/Replay").CreateAttribute("other", Sdf.ValueTypeNames.Float).Set(1.0, 10),
        "unexpected",
    ),
    "sublayer": (lambda s: s.GetRootLayer().subLayerPaths.append("other.usda"), "sublayers"),
    "reference": (
        lambda s: s.GetPrimAtPath("/Replay/Entities/prey").GetReferences().AddReference("x.usda"),
        "references",
    ),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_validator_catches(tmp_path, name):
    edit, message = BREAKS[name]
    good = make(tmp_path / "good.usda")
    bad = _break(good, tmp_path / "bad.usda", edit)
    problems = validate_replay(bad)
    assert any(message in p for p in problems), problems


def test_validator_on_non_replays(tmp_path):
    assert "no such file" in validate_replay(tmp_path / "missing.usda")[0]
    (tmp_path / "junk.usda").write_text("not usd")
    assert validate_replay(tmp_path / "junk.usda")
    plain = Usd.Stage.CreateNew(str(tmp_path / "plain.usda"))
    plain.DefinePrim("/World")
    plain.Save()
    assert any("usdReplay" in p for p in validate_replay(tmp_path / "plain.usda"))
    with pytest.raises(ValueError, match="not a valid"):
        read_replay(tmp_path / "plain.usda")


def test_cli(tmp_path, capsys):
    path = make(tmp_path / "r.usda")
    assert main(["replay", "validate", str(path)]) == 0
    assert "conforms" in capsys.readouterr().out
    assert main(["replay", "info", str(path)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["samples"] == 5 and info["fields"]["food"]["samples"] == 2
    assert info == json.loads(json.dumps(replay_info(path)))
    out = tmp_path / "r.usdc"
    assert main(["replay", "convert", str(path), str(out)]) == 0
    assert validate_replay(out) == [] and read_replay(out).hashes == read_replay(path).hashes
    bad = _break(path, tmp_path / "bad.usda", BREAKS["schema"][0])
    capsys.readouterr()
    assert main(["replay", "validate", str(bad)]) == 1
    assert "usd-replay/9" in capsys.readouterr().out
