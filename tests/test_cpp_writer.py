"""The header-only C++ writer produces stages equivalent to the Python Recorder's."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pxr import Usd, UsdGeom

sys.path.insert(0, str(Path(__file__).parent))

import cpp_scenario  # noqa: E402

from usd_recorder import Recorder  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CXX = shutil.which("g++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


@pytest.fixture(scope="module")
def stages(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cpp")
    exe = tmp / "scenario"
    subprocess.run(
        [
            CXX,
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(ROOT / "cpp" / "include"),
            str(ROOT / "cpp" / "tests" / "scenario.cpp"),
            "-o",
            str(exe),
        ],
        check=True,
    )
    subprocess.run([str(exe), str(tmp / "cpp.usda")], check=True)
    frames = cpp_scenario.frames()
    with Recorder(tmp / "py.usda", 10) as rec:
        for tick, entities, extras, events in frames:
            rec.record(tick, entities, extras, events)
    cpp, py = Usd.Stage.Open(str(tmp / "cpp.usda")), Usd.Stage.Open(str(tmp / "py.usda"))
    return cpp, py, frames, tmp


def test_cpp_layer_opens_cleanly(stages):
    cpp, py, frames, tmp = stages
    assert cpp.GetCompositionErrors() == []
    assert (cpp.GetStartTimeCode(), cpp.GetEndTimeCode()) == (frames[0][0], frames[-1][0])
    assert cpp.GetTimeCodesPerSecond() == 10 and UsdGeom.GetStageUpAxis(cpp) == "Z"
    assert not list(tmp.glob("*.tmp"))


def test_same_values_at_every_tick(stages):
    cpp, py, frames, _ = stages
    compared = 0
    for prim in py.Traverse():
        other = cpp.GetPrimAtPath(prim.GetPath())
        assert other.IsValid(), prim.GetPath()
        for attr in prim.GetAttributes():
            if not attr.GetNumTimeSamples():
                continue
            twin = other.GetAttribute(attr.GetName())
            assert twin.IsValid() and twin.GetTypeName() == attr.GetTypeName(), attr.GetPath()
            assert twin.GetTimeSamples() == attr.GetTimeSamples(), attr.GetPath()  # same sparse samples
            for tick, *_ in frames:
                assert twin.Get(tick) == attr.Get(tick), (attr.GetPath(), tick)
                compared += 1
    assert compared > 1000


def test_lifecycle_in_the_cpp_layer(stages):
    cpp, _, frames, _ = stages
    late = UsdGeom.Imageable(cpp.GetPrimAtPath("/World/Entities/e_5"))
    for tick, entities, _, _ in frames:
        want = UsdGeom.Tokens.inherited if 5 in entities else UsdGeom.Tokens.invisible
        assert late.ComputeVisibility(tick) == want, tick
    log = cpp.GetPrimAtPath("/World").GetAttribute("events:log")
    for tick, _, _, events in frames:
        assert log.Get(tick) == "\n".join(events.get("log", [])), tick
