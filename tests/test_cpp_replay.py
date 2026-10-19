"""The header-only C++ replay writer produces replays equal to the Python ReplayWriter's."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from pxr import Usd

sys.path.insert(0, str(Path(__file__).parent))

import replay_scenario  # noqa: E402

from usd_recorder.replay import convert, read_replay, validate_replay  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CXX = shutil.which("g++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(CXX is None, reason="no C++ compiler")


@pytest.fixture(scope="module")
def pair(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cpp_replay")
    exe = tmp / "replay_scenario"
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
            str(ROOT / "cpp" / "tests" / "replay_scenario.cpp"),
            "-o",
            str(exe),
        ],
        check=True,
    )
    subprocess.run([str(exe), str(tmp / "cpp.usda")], check=True)
    replay_scenario.write(tmp / "py.usda")
    return tmp / "cpp.usda", tmp / "py.usda"


def test_cpp_replay_validates_and_opens_cleanly(pair):
    cpp, _ = pair
    assert validate_replay(cpp) == []
    stage = Usd.Stage.Open(str(cpp))
    assert stage.GetCompositionErrors() == []
    assert not list(cpp.parent.glob("*.tmp"))


def test_cpp_and_python_replays_are_equal(pair):
    a, b = (read_replay(p) for p in pair)
    assert a.meta == b.meta
    assert a.meta["seed"] == "18446744073709551615" and a.meta["params"] == {"cells": 6.0, "mode": "test"}
    assert a.playback_rate == b.playback_rate == 12
    assert a.ticks == b.ticks == list(range(0, 61, 5))
    assert a.hashes == b.hashes
    assert a.stats == b.stats and sorted(a.stats) == ["energy", "population"]
    assert [s.name for s in a.sets] == [s.name for s in b.sets] == ["walkers", "glowers"]
    for sa, sb in zip(a.sets, b.sets, strict=True):
        assert (sa.label, sa.shape, sa.vertex_color, sa.vertex_width) == (
            sb.label,
            sb.shape,
            sb.vertex_color,
            sb.vertex_width,
        )
        for k in range(len(a.ticks)):
            np.testing.assert_array_equal(sa.ids[k], sb.ids[k])
            np.testing.assert_array_equal(sa.points[k], sb.points[k])
        if sa.vertex_color:
            for ca, cb in zip(sa.colors, sb.colors, strict=True):
                np.testing.assert_allclose(ca, cb, rtol=0, atol=1e-7)
            for wa, wb in zip(sa.widths, sb.widths, strict=True):
                np.testing.assert_array_equal(wa, wb)
        else:
            np.testing.assert_allclose(sa.colors, sb.colors, rtol=0, atol=1e-7)
            np.testing.assert_array_equal(sa.widths, sb.widths)
    (fa,), (fb,) = a.fields, b.fields
    assert (
        (fa.cols, fa.rows, fa.origin, fa.cell_size, fa.z)
        == (fb.cols, fb.rows, fb.origin, fb.cell_size, fb.z)
        == (3, 2, (-1.5, 2.0), 0.5, -0.25)
    )
    assert fa.ticks == fb.ticks == [0, 20, 40, 60]  # unchanged field colours are held, not resampled
    for ca, cb in zip(fa.colors, fb.colors, strict=True):
        np.testing.assert_allclose(ca, cb, rtol=0, atol=1e-7)
    (ra,), (rb,) = a.scene, b.scene
    np.testing.assert_array_equal(ra.points, rb.points)
    np.testing.assert_array_equal(ra.widths, rb.widths)
    np.testing.assert_allclose(ra.colors, rb.colors, rtol=0, atol=1e-7)


def test_cpp_replay_converts_to_usdc(pair, tmp_path):
    cpp, _ = pair
    out = convert(cpp, tmp_path / "run.usdc")
    assert validate_replay(out) == []
    assert read_replay(out).hashes == read_replay(cpp).hashes
