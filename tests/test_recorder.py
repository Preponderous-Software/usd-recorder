"""Recorder behaviour: lifecycle, typed values, events, validation, options."""

from __future__ import annotations

import pytest
from pxr import Sdf, Usd, UsdGeom

from usd_recorder import Recorder, TypeChangeError, default_entity_name


def vis(stage, path, t):
    return UsdGeom.Imageable(stage.GetPrimAtPath(path)).ComputeVisibility(Usd.TimeCode(t))


_OPEN = []  # keep stages alive while prims fetched from them are used


def open_stage(path):
    stage = Usd.Stage.Open(str(path))
    _OPEN.append(stage)
    return stage


def test_stage_metadata(tmp_path):
    with Recorder(tmp_path / "r.usda", 12, up_axis="Y", meters_per_unit=0.01, metadata={"sim:seed": 7}) as rec:
        rec.record(3, {1: {"position": (0, 0, 0)}})
        rec.record(9, {1: {"position": (1, 0, 0)}})
    stage = open_stage(tmp_path / "r.usda")
    assert UsdGeom.GetStageUpAxis(stage) == "Y"
    assert UsdGeom.GetStageMetersPerUnit(stage) == 0.01
    assert stage.GetTimeCodesPerSecond() == 12 and stage.GetFramesPerSecond() == 12
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (3, 9)
    assert stage.GetDefaultPrim().GetPath() == "/World"
    data = stage.GetRootLayer().customLayerData
    assert data["sim:seed"] == 7 and data["usdRecorder:version"] == "0.1.0"


def test_entity_appears_disappears_and_returns(tmp_path):
    with Recorder(tmp_path / "r.usda", 10) as rec:
        rec.record(0, {"a": {"position": (0, 0, 0)}})
        rec.record(1, {"a": {"position": (1, 0, 0)}, "b": {"position": (5, 5, 0)}})
        rec.record(2, {"b": {"position": (5, 6, 0)}})
        rec.record(3, {"b": {}})
        rec.record(4, {"a": {"position": (9, 0, 0)}, "b": {}})
    stage = open_stage(tmp_path / "r.usda")
    a, b = "/World/Entities/a", "/World/Entities/b"
    assert [vis(stage, a, t) for t in range(5)] == ["inherited", "inherited", "invisible", "invisible", "inherited"]
    assert [vis(stage, b, t) for t in range(5)] == ["invisible", "inherited", "inherited", "inherited", "inherited"]
    translate = stage.GetPrimAtPath(a).GetAttribute("xformOp:translate")
    assert translate.Get(3) == (1, 0, 0)  # held while hidden, not interpolating towards (9, 0, 0)
    assert translate.Get(4) == (9, 0, 0)
    assert stage.GetPrimAtPath(b).GetAttribute("xformOp:translate").Get(3) == (5, 6, 0)  # omitted: kept
    assert stage.GetPrimAtPath(a).GetAttribute("recorder:id").Get() == "a"


def test_typed_values_hold_and_type_changes_are_rejected_without_writing(tmp_path):
    rec = Recorder(tmp_path / "r.usda", 10)
    rec.record(0, {1: {"hp": 10, "name": "ann", "alive": True, "speed": 1.5, "dir": (1, 0, 0)}})
    rec.record(1, {1: {"hp": 9}})
    with pytest.raises(TypeChangeError):
        rec.record(2, {1: {"hp": 8.5}, 2: {"position": (0, 0, 0)}})
    assert rec.last_tick == 1 and rec.entity_ids() == [1]  # the failed tick changed nothing
    rec.record(2, {1: {"hp": 8}})
    rec.close()
    prim = open_stage(tmp_path / "r.usda").GetPrimAtPath("/World/Entities/e_1")
    assert [prim.GetAttribute("extras:hp").Get(t) for t in range(3)] == [10, 9, 8]
    assert prim.GetAttribute("extras:name").Get(2) == "ann"
    assert prim.GetAttribute("extras:alive").Get(2) is True
    assert prim.GetAttribute("extras:speed").GetTypeName() == Sdf.ValueTypeNames.Double
    assert prim.GetAttribute("extras:dir").GetTypeName() == Sdf.ValueTypeNames.Double3


def test_stage_extras_and_string_channels_hold(tmp_path):
    with Recorder(tmp_path / "r.usda", 10) as rec:
        rec.record(0, extras={"phase": "day", "temp": 20.0})
        rec.record(1, extras={"temp": 21.0})
        rec.record(2, extras={"phase": "night"})
        rec.record(3)
    world = open_stage(tmp_path / "r.usda").GetPrimAtPath("/World")
    assert [world.GetAttribute("extras:phase").Get(t) for t in range(4)] == ["day", "day", "night", "night"]
    assert [world.GetAttribute("extras:temp").Get(t) for t in range(4)] == [20.0, 21.0, 21.0, 21.0]


def test_event_channels_read_empty_between_events(tmp_path):
    with Recorder(tmp_path / "r.usda", 10) as rec:
        for t in range(6):
            events = {}
            if t in (2, 3):
                events["log"] = f"event {t}"
            if t == 4:
                events["log"] = ["two", "lines"]
            rec.record(t, events=events)
    attr = open_stage(tmp_path / "r.usda").GetPrimAtPath("/World").GetAttribute("events:log")
    assert [attr.Get(t) for t in range(6)] == ["", "", "event 2", "event 3", "two\nlines", ""]


def test_event_channels_across_chunks(tmp_path):
    with Recorder(tmp_path / "r.usda", 10, chunk_size=3) as rec:
        for t in range(12):
            rec.record(t, events={"log": "x"} if t in (7, 8) else None)
    attr = open_stage(tmp_path / "r.usda").GetPrimAtPath("/World").GetAttribute("events:log")
    assert [attr.Get(t) for t in range(12)] == [""] * 7 + ["x", "x"] + [""] * 3


def test_colors_default_until_given_and_across_chunks(tmp_path):
    for chunk in (None, 2):
        out = tmp_path / f"r{chunk}.usda"
        with Recorder(out, 10, chunk_size=chunk, default_color=lambda e: (0.0, 0.0, float(e) / 10)) as rec:
            for t in range(6):
                values = {"position": (t, 0, 0)}
                if t == 3:
                    values["color"] = (1, 0, 0)
                rec.record(t, {5: values})
        color = open_stage(out).GetPrimAtPath("/World/Entities/e_5/geom").GetAttribute("primvars:displayColor")
        got = [tuple(round(c, 3) for c in color.Get(t)[0]) for t in range(6)]
        assert got == [(0, 0, 0.5)] * 3 + [(1, 0, 0)] * 3, chunk


def test_geometry_options(tmp_path):
    with Recorder(tmp_path / "s.usda", 10, geometry="sphere", entity_size=2.0) as rec:
        rec.record(0, {1: {}})
    geom = open_stage(tmp_path / "s.usda").GetPrimAtPath("/World/Entities/e_1/geom")
    assert geom.IsA(UsdGeom.Sphere) and geom.GetAttribute("radius").Get() == 1.0
    rec = Recorder(tmp_path / "n.usda", 10, geometry=None)
    rec.record(0, {1: {}})
    with pytest.raises(ValueError, match="colour needs geometry"):
        rec.record(1, {1: {"color": (1, 0, 0)}})
    rec.close()
    assert not open_stage(tmp_path / "n.usda").GetPrimAtPath("/World/Entities/e_1/geom").IsValid()


def test_scene_hook_runs_once_in_the_right_layer(tmp_path):
    def scene(stage):
        UsdGeom.Mesh.Define(stage, "/World/Ground")

    with Recorder(tmp_path / "a.usda", 10, scene=scene) as rec:
        rec.record(0, {})
    assert open_stage(tmp_path / "a.usda").GetPrimAtPath("/World/Ground").IsValid()
    with Recorder(tmp_path / "b.usda", 10, chunk_size=2, scene=scene) as rec:
        for t in range(5):
            rec.record(t, {1: {}})
        chunks = rec.chunk_paths
    assert open_stage(tmp_path / "b.usda").GetPrimAtPath("/World/Ground").IsValid()
    assert Sdf.Layer.FindOrOpen(str(tmp_path / "b.topology.usda")).GetPrimAtPath("/World/Ground") is not None
    for chunk in chunks:
        assert Sdf.Layer.FindOrOpen(str(chunk)).GetPrimAtPath("/World/Ground") is None


def test_binary_and_paths(tmp_path):
    with Recorder(tmp_path / "r.usdc", 10, binary=True) as rec:
        rec.record(0, {1: {}})
    assert (tmp_path / "r.usdc").read_bytes()[:8] == b"PXR-USDC"
    with Recorder(tmp_path / "c.usda", 10, chunk_size=1, binary=True) as rec:
        rec.record(0, {1: {}})
        rec.record(1, {1: {}})
    assert [p.name for p in rec.chunk_paths] == ["c.chunk_0000.usdc", "c.chunk_0001.usdc"]
    assert (tmp_path / "c.usda").read_text().startswith("#usda")
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.usda", 10, binary=True)
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.usdc", 10)
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.usdc", 10, chunk_size=5, binary=True)
    with pytest.raises(ValueError):
        Recorder(tmp_path / "x.txt", 10)


def test_chunks_anchor_to_the_first_tick_and_skip_empty_ranges(tmp_path):
    with Recorder(tmp_path / "r.usda", 10, chunk_size=10) as rec:
        for t in (5, 6, 40, 41):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    assert len(rec.chunk_paths) == 2
    stage = open_stage(tmp_path / "r.usda")
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (5, 41)
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in (5, 6, 40, 41)] == [5, 6, 40, 41]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"time_codes_per_second": 0},
        {"time_codes_per_second": float("nan")},
        {"chunk_size": 0},
        {"up_axis": "X"},
        {"geometry": "cone"},
        {"namespace": "bad name"},
        {"checkpoint_every": 0},
        {"root": "World"},
    ],
)
def test_bad_options(tmp_path, kwargs):
    kwargs.setdefault("time_codes_per_second", 10)
    with pytest.raises(ValueError):
        Recorder(tmp_path / "r.usda", **kwargs)


def test_bad_records(tmp_path):
    rec = Recorder(tmp_path / "r.usda", 10)
    rec.record(1, {})
    for args in [
        (1, {}),  # not after the previous tick
        (0.5, {}),
        (float("inf"), {}),
        (2, {1: {"position": (0, 0)}}),
        (2, {1: {"position": (0, 0, float("nan"))}}),
        (2, {1: {"color": (2, 0, 0)}}),
        (2, {1: {"bad key": 1}}),
        (2, {1: {"k": None}}),
        (2, {}, {"x": object()}),
        (2, {}, None, {"bad name": "x"}),
        (2, {1: [1, 2]}),
    ]:
        with pytest.raises((ValueError, TypeError)):
            rec.record(*args)
    with pytest.raises(TypeError):
        rec.record(2, {}, None, {"log": [1]})
    with pytest.raises(TypeError):
        rec.record(2, {1.5: {}})
    assert rec.last_tick == 1 and rec.ticks == 1
    rec.close()
    with pytest.raises(RuntimeError):
        rec.record(5, {})


def test_prim_name_collisions_are_errors(tmp_path):
    rec = Recorder(tmp_path / "r.usda", 10)
    rec.record(0, {"a b": {}})
    with pytest.raises(ValueError, match="both map"):
        rec.record(1, {"a_b": {}})
    with pytest.raises(ValueError, match="both map"):
        rec.record(1, {"x-y": {}, "x y": {}})
    rec.close()


def test_default_entity_names():
    assert default_entity_name(4) == "e_4"
    assert default_entity_name(-4) == "e_n4"
    assert default_entity_name("wolf-1") == "wolf_1"
    assert default_entity_name("9lives") == "_9lives"
    assert default_entity_name("") == "_"
    with pytest.raises(TypeError):
        default_entity_name(True)


def test_close_without_ticks_writes_nothing(tmp_path):
    rec = Recorder(tmp_path / "r.usda", 10)
    assert rec.close() is None
    assert rec.close() is None
    assert not (tmp_path / "r.usda").exists()
    with pytest.raises(RuntimeError):
        Recorder(tmp_path / "q.usda", 10).checkpoint()


def test_float_time_codes(tmp_path):
    with Recorder(tmp_path / "r.usda", 10, chunk_size=1.5) as rec:
        for t in (0.0, 0.5, 1.0, 2.25, 3.0):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    attr = open_stage(tmp_path / "r.usda").GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in (0.0, 0.5, 1.0, 2.25, 3.0)] == [0.0, 0.5, 1.0, 2.25, 3.0]
