"""The C++ replay reader (cpp/reader) agrees with the Python read_replay, and refuses what it must.

These tests need a built ``replay-dump`` (cpp/reader/tools/replay_dump.cpp), which links OpenUSD's
C++ libraries; set ``USD_RECORDER_REPLAY_DUMP`` to its path. Without it they are skipped. CI builds
it against Strata's prebuilt OpenUSD v26.08 (.github/workflows/ci.yml, job "C++ reader").
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pxr import Sdf

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cpp" / "reader" / "tests"))

import replay_scenario  # noqa: E402
from conformance import compare  # noqa: E402

from usd_recorder.replay import ReplayWriter, convert, validate_replay  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DUMP = os.environ.get("USD_RECORDER_REPLAY_DUMP")
# The C++ writer scenario is compiled with g++ or clang++ where one is on PATH (not on Windows, where
# the reader is built with MSVC and the other fixtures cover it).
CXX = None if sys.platform == "win32" else shutil.which("g++") or shutil.which("clang++")
pytestmark = pytest.mark.skipif(not DUMP, reason="set USD_RECORDER_REPLAY_DUMP to a built replay-dump")


def _dump(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([DUMP, str(path)], capture_output=True, text=True, check=False)


def _edge_cases(path: Path) -> Path:
    """Empty sets, an id that leaves and comes back, vertex widths without vertex colours, a
    field sampled once, non-ASCII metadata, nested params of several types and no stats."""
    with ReplayWriter(
        path,
        sim="edge",
        sim_version="0.0.1+é",
        seed=0,
        tick_rate=0.5,
        state_hash_algorithm="none (test) – “quoted” 🦠",
        playback_rate=7.5,
        title="Edge\tcases\n",
        params={"n": 3, "ratio": 0.1, "on": True, "name": "x", "nested": {"deep": 1.5}},
    ) as w:
        w.entity_set("a", color="#ff8000", width=2.0, shape="cube")
        w.entity_set("b", color="#0080ff", width=None)
        w.field("f", cols=1, rows=1, origin=(0, 0), cell_size=1.0)
        for k, tick in enumerate([3, 4, 10, 1000]):
            ids_a = [] if k == 1 else [7, 9] if k != 2 else [9]
            w.sample(
                tick,
                state_hash=f"{tick:08x}",
                entities={
                    "a": {"ids": ids_a, "points": [[1e-7 * i, -1e6, 0.1] for i in ids_a]},
                    "b": {"ids": [k], "points": [[k, k, k]], "widths": [0.5 + k]},
                },
                fields={"f": [[255, 0, 0]]} if k == 0 else {},
            )
    return path


@pytest.fixture(scope="module")
def replays(tmp_path_factory) -> list[Path]:
    tmp = tmp_path_factory.mktemp("cpp_reader")
    replay_scenario.write(tmp / "py.usda")
    out = [tmp / "py.usda", convert(tmp / "py.usda", tmp / "py.usdc")]
    out.append(_edge_cases(tmp / "edge.usda"))
    out.append(convert(out[-1], tmp / "edge.usdc"))
    if CXX:
        exe = tmp / "replay_scenario"
        subprocess.run(
            [
                CXX,
                "-std=c++17",
                "-I",
                str(ROOT / "cpp" / "include"),
                str(ROOT / "cpp" / "tests" / "replay_scenario.cpp"),
                "-o",
                str(exe),
            ],
            check=True,
        )
        subprocess.run([str(exe), str(tmp / "cpp.usda")], check=True)
        out.append(tmp / "cpp.usda")
        out.append(convert(tmp / "cpp.usda", tmp / "cpp.usdc"))
    for p in out:
        assert validate_replay(p) == [], p
    return out


def test_cpp_and_python_readers_print_identical_json(replays):
    for path in replays:
        assert compare(DUMP, path) is None, path


def test_refuses_another_major_version(replays, tmp_path):
    layer = Sdf.Layer.FindOrOpen(str(replays[0]))
    copy = Sdf.Layer.CreateAnonymous(".usda")
    copy.TransferContent(layer)
    data = dict(copy.customLayerData)
    data["usdReplay"] = {**data["usdReplay"], "schema": "usd-replay/2"}
    copy.customLayerData = data
    path = tmp_path / "v2.usda"
    copy.Export(str(path))
    p = _dump(path)
    assert p.returncode == 1 and "usd-replay/2" in p.stderr and p.stdout == ""


def test_refuses_sublayers_and_a_missing_hash(tmp_path):
    sub = tmp_path / "sub.usda"
    sub.write_text("#usda 1.0\n")
    meta = (
        "(\n    customLayerData = {\n        dictionary usdReplay = {\n"
        '            string schema = "usd-replay/1"\n        }\n    }\n    defaultPrim = "Replay"\n'
    )
    (tmp_path / "layered.usda").write_text(
        "#usda 1.0\n" + meta + '    subLayers = [@./sub.usda@]\n)\ndef Xform "Replay" {}\n'
    )
    (tmp_path / "nohash.usda").write_text("#usda 1.0\n" + meta + ')\ndef Xform "Replay" {}\n')
    (tmp_path / "plain.usda").write_text('#usda 1.0\ndef Xform "Replay" {}\n')
    for name, needle in (("layered", "sublayers"), ("nohash", "stateHash"), ("plain", "customLayerData")):
        p = _dump(tmp_path / f"{name}.usda")
        assert p.returncode == 1 and needle in p.stderr, (name, p.stderr)


def test_refuses_mismatched_ids_and_points(replays, tmp_path):
    text = replays[0].read_text(encoding="utf-8")
    # Drop one point from the first sample of the first set: ids and points now differ in length.
    start = text.index("point3f[] points.timeSamples")
    first = text.index("0: [(", start)
    end = text.index("), (", first)
    path = tmp_path / "short.usda"
    path.write_text(text[: first + 4] + text[end + 3 :], encoding="utf-8")
    p = _dump(path)
    assert p.returncode == 1 and "ids but" in p.stderr, p.stderr
