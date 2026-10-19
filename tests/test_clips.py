"""The clip building blocks used directly, without a Recorder: names, ``stitch`` and its hooks."""

from __future__ import annotations

from pathlib import Path

import pytest
from pxr import Sdf, Usd, UsdGeom

from usd_recorder import chunk_name, default_flat_path, set_manifest_defaults, stitch


def write_chunks(directory: Path) -> list[Path]:
    """Three five-tick chunks; ``e_1`` is in all of them, ``e_2`` only in the last."""
    chunks = [directory / chunk_name("run", i) for i in range(3)]
    for i, path in enumerate(chunks):
        ticks = range(i * 5, i * 5 + 5)
        stage = Usd.Stage.CreateNew(str(path))
        UsdGeom.Xform.Define(stage, "/World")
        UsdGeom.Scope.Define(stage, "/World/Entities")
        names = ["e_1", "e_2"] if i == 2 else ["e_1"]
        for name in names:
            xform = UsdGeom.Xform.Define(stage, f"/World/Entities/{name}")
            translate = xform.AddTranslateOp().GetAttr()
            for t in ticks:
                xform.CreateVisibilityAttr().Set(UsdGeom.Tokens.inherited, t)
                translate.Set((float(t), 0.0, 0.0), t)
        stage.SetStartTimeCode(ticks.start)
        stage.SetEndTimeCode(ticks.stop - 1)
        stage.GetRootLayer().Save()
    return chunks


def test_chunk_name():
    assert chunk_name("run", 0) == "run.chunk_0000.usda"
    assert chunk_name("run", 12, binary=True) == "run.chunk_0012.usdc"
    assert chunk_name("my.run", 12345) == "my.run.chunk_12345.usda"


def test_default_flat_path():
    assert default_flat_path("out/run.usda") == Path("out/run_flat.usda")
    assert default_flat_path("out/run.usdc", binary=True) == Path("out/run_flat.usdc")
    assert default_flat_path("out/my.run.usda") == Path("out/my.run_flat.usda")
    assert default_flat_path("out/run") == Path("out/run_flat.usda")


def test_stitch_rejects_a_binary_root_and_no_chunks(tmp_path):
    with pytest.raises(ValueError, match=r"\.usda"):
        stitch(tmp_path / "run.usdc", [tmp_path / "c.usda"], "/World")
    with pytest.raises(ValueError, match="no chunk layers"):
        stitch(tmp_path / "run.usda", [], "/World")
    assert list(tmp_path.iterdir()) == []


def test_stitch_runs_the_hooks_and_publishes_under_the_final_names(tmp_path):
    chunks = write_chunks(tmp_path)
    authored = []

    def topology(stage):
        UsdGeom.Cube.Define(stage, "/World/Ground")

    def manifest(layer):
        defaults = {
            "/World/Entities/e_2.visibility": UsdGeom.Tokens.invisible,
            "/World/Entities/missing.visibility": UsdGeom.Tokens.invisible,  # not in the manifest: skipped
        }
        authored.append(set_manifest_defaults(layer, defaults))

    def root(stage):
        stage.GetRootLayer().customLayerData = {"sim:name": "demo"}

    root_path = tmp_path / "run.usda"
    assert stitch(root_path, chunks, "/World", topology=topology, manifest=manifest, root=root) == root_path
    assert authored == [1]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [p.name for p in chunks] + ["run.usda", "run.topology.usda", "run.manifest.usda"]
    )
    for name in ("run.usda", "run.topology.usda", "run.manifest.usda"):
        assert "staging" not in (tmp_path / name).read_text(encoding="utf-8")

    stage = Usd.Stage.Open(str(root_path))
    assert stage.GetCompositionErrors() == []
    assert stage.GetPrimAtPath("/World/Ground").IsValid()  # authored once, on the topology layer
    assert stage.GetRootLayer().customLayerData["sim:name"] == "demo"
    translate = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [translate.Get(t)[0] for t in range(15)] == [float(t) for t in range(15)]
    late = UsdGeom.Imageable(stage.GetPrimAtPath("/World/Entities/e_2"))
    assert [late.ComputeVisibility(Usd.TimeCode(t)) for t in range(10)] == [UsdGeom.Tokens.invisible] * 10
    assert [late.ComputeVisibility(Usd.TimeCode(t)) for t in range(10, 15)] == [UsdGeom.Tokens.inherited] * 5


def test_stitch_publishes_nothing_when_a_hook_fails(tmp_path):
    chunks = write_chunks(tmp_path)

    def root(stage):
        raise RuntimeError("hook failed")

    with pytest.raises(RuntimeError, match="hook failed"):
        stitch(tmp_path / "run.usda", chunks, "/World", root=root)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(p.name for p in chunks)


def test_set_manifest_defaults_skips_attributes_the_manifest_lacks():
    manifest = Sdf.Layer.CreateAnonymous()
    prim = Sdf.CreatePrimInLayer(manifest, "/World/e_1")
    Sdf.AttributeSpec(prim, "visibility", Sdf.ValueTypeNames.Token, Sdf.VariabilityVarying)
    assert set_manifest_defaults(manifest, {"/World/e_2.visibility": "invisible"}) == 0
    assert set_manifest_defaults(manifest, {"/World/e_1.visibility": "invisible"}) == 1
    assert manifest.GetAttributeAtPath("/World/e_1.visibility").default == "invisible"
    assert manifest.GetPrimAtPath("/World/e_2") is None
