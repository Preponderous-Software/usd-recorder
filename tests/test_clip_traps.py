"""The two value-clip traps, each shown happening with raw USD and then shown fixed.

Trap 1: a chunk layer without its own startTimeCode/endTimeCode is stitched as active at time 0
and the stage resolves to one frame. Trap 2: an entity first seen in a later chunk resolves to
``visibility = inherited`` (visible) in earlier chunks unless the *manifest* carries the
``invisible`` default; the same default in the topology layer does not help.
"""

from __future__ import annotations

from pathlib import Path

from pxr import Sdf, Usd, UsdGeom, UsdUtils

from usd_recorder import Recorder, default_invisible
from usd_recorder.clips import manifest_path, topology_path


def write_chunk(path: Path, ticks: range, value_of, with_range: bool, entity: str = "e_1") -> None:
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.Xform.Define(stage, "/World")
    xform = UsdGeom.Xform.Define(stage, f"/World/Entities/{entity}")
    attr = xform.AddTranslateOp().GetAttr()
    for t in ticks:
        attr.Set((value_of(t), 0.0, 0.0), t)
    if with_range:
        stage.SetStartTimeCode(ticks.start)
        stage.SetEndTimeCode(ticks.stop - 1)
    stage.GetRootLayer().Save()


def stitch_raw(root: Path, chunks: list[Path]) -> Usd.Stage:
    layer = Sdf.Layer.CreateNew(str(root))
    UsdUtils.StitchClips(layer, [str(p.resolve()) for p in chunks], Sdf.Path("/World"))
    layer.Save()
    return Usd.Stage.Open(str(root))


# -- trap 1: per-chunk start/end time codes ------------------------------------------------------


def test_trap1_without_chunk_ranges_the_stage_collapses(tmp_path):
    chunks = [tmp_path / "c0.usda", tmp_path / "c1.usda"]
    write_chunk(chunks[0], range(0, 5), float, with_range=False)
    write_chunk(chunks[1], range(5, 10), float, with_range=False)
    stage = stitch_raw(tmp_path / "run.usda", chunks)
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    # USD warns "Clip 1 cannot be active at time 0.000 ..." and the clips do not resolve.
    resolved = [None if attr.Get(t) is None else attr.Get(t)[0] for t in range(10)]
    assert resolved != [float(t) for t in range(10)]


def test_trap1_with_chunk_ranges_every_tick_resolves(tmp_path):
    chunks = [tmp_path / "c0.usda", tmp_path / "c1.usda"]
    write_chunk(chunks[0], range(0, 5), float, with_range=True)
    write_chunk(chunks[1], range(5, 10), float, with_range=True)
    stage = stitch_raw(tmp_path / "run.usda", chunks)
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in range(10)] == [float(t) for t in range(10)]


def test_trap1_recorder_authors_every_chunk_range(tmp_path):
    with Recorder(tmp_path / "run.usda", 10, chunk_size=4) as rec:
        for t in range(3, 14):
            rec.record(t, {1: {"position": (t, 0, 0)}})
        chunks = list(rec.chunk_paths)
    ranges = [(Sdf.Layer.FindOrOpen(str(p)).startTimeCode, Sdf.Layer.FindOrOpen(str(p)).endTimeCode) for p in chunks]
    assert ranges == [(3, 6), (7, 10), (11, 13)]
    stage = Usd.Stage.Open(str(tmp_path / "run.usda"))
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in range(3, 14)] == [float(t) for t in range(3, 14)]


# -- trap 2: late entities need a manifest-level invisible default --------------------------------


def _late_entity_stage(tmp_path: Path) -> tuple[Path, list[Path]]:
    chunks = [tmp_path / f"c{i}.usda" for i in range(3)]
    for i, path in enumerate(chunks):
        ticks = range(i * 5, i * 5 + 5)
        stage = Usd.Stage.CreateNew(str(path))
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Scope.Define(stage, "/World/Entities")
        early = UsdGeom.Xform.Define(stage, "/World/Entities/early")
        for t in ticks:
            early.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited, t)
        if i == 2:  # "late" exists only from the third chunk
            late = UsdGeom.Xform.Define(stage, "/World/Entities/late")
            for t in ticks:
                late.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited, t)
        stage.SetStartTimeCode(ticks.start)
        stage.SetEndTimeCode(ticks.stop - 1)
        stage.GetRootLayer().Save()
    return tmp_path / "run.usda", chunks


def _late_visibility(stage: Usd.Stage, t: int) -> str:
    return UsdGeom.Imageable(stage.GetPrimAtPath("/World/Entities/late")).ComputeVisibility(Usd.TimeCode(t))


def test_trap2_late_entity_is_visible_before_it_exists_without_the_fix(tmp_path):
    root, chunks = _late_entity_stage(tmp_path)
    stage = stitch_raw(root, chunks)
    assert _late_visibility(stage, 2) == UsdGeom.Tokens.inherited  # the bug: visible at tick 2


def test_trap2_default_in_topology_layer_does_not_help(tmp_path):
    root, chunks = _late_entity_stage(tmp_path)
    stitch_raw(root, chunks)
    topo = Sdf.Layer.FindOrOpen(str(topology_path(root)))
    spec = topo.GetPrimAtPath("/World/Entities/late")
    attr = spec.attributes.get("visibility") or Sdf.AttributeSpec(
        spec, "visibility", Sdf.ValueTypeNames.Token, Sdf.VariabilityVarying
    )
    attr.default = "invisible"
    topo.Save()
    stage = Usd.Stage.Open(str(root))
    assert _late_visibility(stage, 2) == UsdGeom.Tokens.inherited


def test_trap2_manifest_default_fixes_it(tmp_path):
    root, chunks = _late_entity_stage(tmp_path)
    stitch_raw(root, chunks)
    manifest = Sdf.Layer.FindOrOpen(str(manifest_path(root)))
    assert default_invisible(manifest, "/World/Entities") == 2
    manifest.Save()
    stage = Usd.Stage.Open(str(root))
    assert [_late_visibility(stage, t) for t in (0, 4, 9)] == [UsdGeom.Tokens.invisible] * 3
    assert _late_visibility(stage, 10) == UsdGeom.Tokens.inherited


def test_trap2_recorder_hides_late_entities_in_every_earlier_chunk(tmp_path):
    with Recorder(tmp_path / "run.usda", 10, chunk_size=5) as rec:
        for t in range(20):
            entities = {"early": {"position": (t, 0, 0)}}
            if t >= 12:
                entities["late"] = {"position": (0, t, 0)}
            rec.record(t, entities)
    stage = Usd.Stage.Open(str(tmp_path / "run.usda"))
    late = UsdGeom.Imageable(stage.GetPrimAtPath("/World/Entities/late"))
    assert [late.ComputeVisibility(Usd.TimeCode(t)) for t in range(12)] == [UsdGeom.Tokens.invisible] * 12
    assert all(late.ComputeVisibility(Usd.TimeCode(t)) == UsdGeom.Tokens.inherited for t in range(12, 20))
    manifest = Sdf.Layer.FindOrOpen(str(manifest_path(tmp_path / "run.usda")))
    assert manifest.GetAttributeAtPath("/World/Entities/late.visibility").default == "invisible"
