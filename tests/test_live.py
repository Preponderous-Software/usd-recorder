"""Live (follow-mode) recordings: checkpoints are atomic, so a follower never sees a torn file."""

from __future__ import annotations

import os
import threading

import pytest
from pxr import Sdf, Usd

from usd_recorder import Recorder, atomic_export, atomic_write_bytes
from usd_recorder import atomic as atomic_mod


def leftovers(directory):
    return sorted(p.name for p in directory.iterdir() if p.name.startswith("."))


@pytest.mark.parametrize("chunk_size", [None, 4])
def test_follower_always_opens_a_complete_stage(tmp_path, chunk_size):
    path = tmp_path / "live.usda"
    stop = threading.Event()
    seen, errors = [], []

    def follow():
        while not stop.is_set():
            if not path.exists():
                continue
            try:
                layer = Sdf.Layer.OpenAsAnonymous(str(path))
                if layer is None:
                    errors.append("could not open")
                    continue
                stage = Usd.Stage.Open(layer)
                seen.append(stage.GetEndTimeCode())
            except Exception as exc:  # noqa: BLE001 - any failure to read is the bug under test
                errors.append(repr(exc))

    thread = threading.Thread(target=follow)
    thread.start()
    try:
        with Recorder(path, 10, chunk_size=chunk_size, checkpoint_every=1) as rec:
            for t in range(40):
                rec.record(t, {1: {"position": (t, 0, 0)}, 2: {"hp": t}}, events={"log": str(t)})
    finally:
        stop.set()
        thread.join()
    assert errors == []
    assert seen, "the follower never saw the file"
    assert seen == sorted(seen)  # the published range only grows
    assert leftovers(tmp_path) == []
    stage = Usd.Stage.Open(str(path))
    assert stage.GetEndTimeCode() == 39


def test_checkpoint_publishes_values_so_far(tmp_path):
    rec = Recorder(tmp_path / "r.usda", 10, chunk_size=3)
    for t in range(5):
        rec.record(t, {1: {"position": (t, 0, 0)}})
    rec.checkpoint()
    stage = Usd.Stage.Open(str(tmp_path / "r.usda"))
    assert stage.GetEndTimeCode() == 4
    assert stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate").Get(4)[0] == 4
    for t in range(5, 8):
        rec.record(t, {1: {"position": (t, 0, 0)}})
    rec.close()
    stage = Usd.Stage.Open(str(tmp_path / "r.usda"))
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in range(8)] == list(range(8))


def test_failed_export_keeps_the_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "r.usda"
    good = Sdf.Layer.CreateAnonymous()
    good.comment = "previous"
    atomic_export(good, path)
    before = path.read_bytes()

    class Broken:
        def Export(self, filename, comment, args):  # noqa: N802 - mirrors Sdf.Layer
            with open(filename, "w") as fh:
                fh.write("#usda 1.0\n(half")
            raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        atomic_export(Broken(), path)
    assert path.read_bytes() == before
    assert leftovers(tmp_path) == []


def test_atomic_write_bytes_uses_replace(tmp_path, monkeypatch):
    calls = []
    real = os.replace
    monkeypatch.setattr(atomic_mod.os, "replace", lambda a, b: (calls.append((a, b)), real(a, b)))
    atomic_write_bytes(tmp_path / "x.bin", b"abc")
    assert (tmp_path / "x.bin").read_bytes() == b"abc"
    assert len(calls) == 1 and str(calls[0][1]).endswith("x.bin")


def test_stitch_leaves_no_staging_files_and_is_relocatable(tmp_path):
    with Recorder(tmp_path / "a" / "run.usda", 10, chunk_size=2) as rec:
        for t in range(5):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    assert leftovers(tmp_path / "a") == []
    (tmp_path / "a").rename(tmp_path / "b")
    stage = Usd.Stage.Open(str(tmp_path / "b" / "run.usda"))
    assert stage.GetCompositionErrors() == []
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in range(5)] == list(range(5))
