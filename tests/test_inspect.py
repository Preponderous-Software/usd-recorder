import json
from pathlib import Path

import pytest
from pxr import Gf, Sdf, Usd, UsdGeom

from usd_recorder import Recorder, chunk_name, has_clips, inspect_stage
from usd_recorder.inspect import format_summary


def hand_built(path):
    """A stage whose counts are known by construction: 2 prims, 1 animated attribute, 3 samples."""
    stage = Usd.Stage.CreateNew(str(path))
    world = UsdGeom.Xform.Define(stage, "/World").GetPrim()
    child = stage.DefinePrim("/World/Thing")
    stage.SetDefaultPrim(world)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    stage.SetTimeCodesPerSecond(5)
    stage.SetStartTimeCode(2)
    stage.SetEndTimeCode(9)
    moving = child.CreateAttribute("moving", Sdf.ValueTypeNames.Double)
    for t, v in ((2, 0.0), (5, 1.0), (9, 2.0)):
        moving.Set(v, t)
    child.CreateAttribute("once", Sdf.ValueTypeNames.Double).Set(1.0, 4)  # one sample: not animated
    child.CreateAttribute("still", Sdf.ValueTypeNames.Double).Set(3.0)  # default only
    stage.GetRootLayer().customLayerData = {"sim:name": "demo", "nested": {"n": 3}, "v": Gf.Vec3d(1, 2, 3)}
    stage.Save()
    return path


def test_hand_built_stage(tmp_path):
    summary = inspect_stage(hand_built(tmp_path / "s.usda"))
    assert summary["path"] == str(tmp_path / "s.usda")
    assert summary["format"] == "usda"
    assert summary["defaultPrim"] == "/World"
    assert (summary["upAxis"], summary["metersPerUnit"]) == ("Y", 0.01)
    assert summary["timeCodesPerSecond"] == 5
    assert (summary["startTimeCode"], summary["endTimeCode"]) == (2, 9)
    assert summary["layers"] == ["s.usda"]
    assert summary["clips"] == []
    assert (summary["prims"], summary["animatedAttributes"], summary["timeSamples"]) == (2, 1, 3)
    assert summary["compositionErrors"] == []


def test_custom_layer_data_is_plain(tmp_path):
    summary = inspect_stage(hand_built(tmp_path / "s.usda"))
    data = summary["customLayerData"]
    assert data["sim:name"] == "demo"
    assert data["nested"] == {"n": 3}
    assert isinstance(data["v"], str)  # a Gf value becomes its string form
    json.dumps(summary)  # what `usd-recorder inspect --json` relies on


def test_no_default_prim(tmp_path):
    path = tmp_path / "bare.usda"
    stage = Usd.Stage.CreateNew(str(path))
    stage.DefinePrim("/A")
    stage.Save()
    assert inspect_stage(path)["defaultPrim"] is None


def test_binary_unchunked_recording(tmp_path):
    path = tmp_path / "run.usdc"
    with Recorder(path, 10, binary=True) as rec:
        for t in range(4):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    summary = inspect_stage(path)
    assert summary["format"] == "usdc"
    assert summary["layers"] == ["run.usdc"]
    assert summary["clips"] == []
    assert (summary["startTimeCode"], summary["endTimeCode"]) == (0, 3)
    assert summary["timeSamples"] >= 4  # xformOp:translate changes on every tick
    assert summary["customLayerData"]["usdRecorder:version"]


def test_chunked_recording_clips(tmp_path):
    root = tmp_path / "run.usda"
    with Recorder(root, 10, chunk_size=3) as rec:
        for t in range(7):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    assert has_clips(Sdf.Layer.FindOrOpen(str(root)))
    summary = inspect_stage(root)
    [clip] = summary["clips"]
    assert (clip["prim"], clip["count"]) == ("/World", 3)
    assert [Path(a).name for a in clip["assets"]] == [chunk_name("run", i) for i in range(3)]
    assert [index for _, index in clip["active"]] == [0.0, 1.0, 2.0]
    starts = [time for time, _ in clip["active"]]
    assert starts[0] == 0 and starts == sorted(starts)
    assert {"run.usda", "run.topology.usda"} <= set(summary["layers"])
    assert summary["compositionErrors"] == []


def test_format_summary_lines(tmp_path):
    text = format_summary(inspect_stage(hand_built(tmp_path / "s.usda")))
    lines = text.splitlines()
    assert lines[0] == f"{tmp_path / 's.usda'} (usda)"
    assert "  up axis          Y, 0.01 m per unit" in lines
    assert "  time codes       2 .. 9 at 5 per second" in lines
    assert "  animated attrs   1 (3 samples)" in lines
    assert "  sim:name         demo" in lines
    assert not any("value clips" in line or "ERROR" in line for line in lines)


def test_missing_file(tmp_path):
    with pytest.raises(ValueError, match="no such file"):
        inspect_stage(tmp_path / "missing.usda")
    with pytest.raises(ValueError, match="no such file"):
        inspect_stage(tmp_path)  # a directory is not a stage
