import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pxr import Sdf, Usd

from usd_recorder import Recorder, __version__, has_clips
from usd_recorder.cli import main


def recorded(tmp_path):
    with Recorder(tmp_path / "run.usda", 10, chunk_size=3, metadata={"sim:name": "demo"}) as rec:
        for t in range(7):
            rec.record(t, {1: {"position": (t, 0, 0)}})
    return tmp_path / "run.usda"


def test_flatten_default_output(tmp_path, capsys):
    run = recorded(tmp_path)
    assert main(["flatten", str(run)]) == 0
    out = tmp_path / "run_flat.usda"
    assert capsys.readouterr().out.strip() == str(out)
    layer = Sdf.Layer.FindOrOpen(str(out))
    assert not has_clips(layer) and not layer.subLayerPaths
    stage = Usd.Stage.Open(str(out))
    attr = stage.GetPrimAtPath("/World/Entities/e_1").GetAttribute("xformOp:translate")
    assert [attr.Get(t)[0] for t in range(7)] == list(range(7))


def test_flatten_binary_and_mismatch(tmp_path, capsys):
    run = recorded(tmp_path)
    assert main(["flatten", str(run), "--binary"]) == 0
    assert (tmp_path / "run_flat.usdc").read_bytes()[:8] == b"PXR-USDC"
    assert main(["flatten", str(run), "-o", str(tmp_path / "x.usdc")]) == 1
    assert "disagrees" in capsys.readouterr().err
    assert main(["flatten", str(run), "-o", str(tmp_path / "x.txt")]) == 1


def test_inspect(tmp_path, capsys):
    run = recorded(tmp_path)
    assert main(["inspect", str(run), "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["clips"][0]["count"] == 3
    assert (summary["startTimeCode"], summary["endTimeCode"]) == (0, 6)
    assert summary["customLayerData"]["sim:name"] == "demo"
    assert summary["compositionErrors"] == []
    assert main(["inspect", str(run)]) == 0
    text = capsys.readouterr().out
    assert "value clips      3 on /World" in text and "0 .. 6 at 10 per second" in text
    assert main(["inspect", str(tmp_path / "missing.usda")]) == 1


def test_help_names_every_command(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    out = " ".join(capsys.readouterr().out.split())
    assert "Inspect and flatten" in out and "usd-replay/1 replays" in out
    assert all(command in out for command in ("flatten", "inspect", "replay"))


def test_replay_actions_describe_their_arguments(capsys):
    expected = {
        "validate": ["every rule of usd-replay/1", "replay layer (.usda or .usdc)"],
        "info": ["as JSON", "replay layer (.usda or .usdc)"],
        "convert": ["an invalid replay is not converted", "replay layer to convert", ".usdc binary"],
    }
    for action, texts in expected.items():
        with pytest.raises(SystemExit) as exc:
            main(["replay", action, "--help"])
        assert exc.value.code == 0
        out = " ".join(capsys.readouterr().out.split())
        assert all(text in out for text in texts), (action, out)


def test_no_command_and_module_entry_point():
    assert main([]) == 2
    proc = subprocess.run([sys.executable, "-m", "usd_recorder.cli", "--version"], capture_output=True, text=True)
    assert proc.returncode == 0 and proc.stdout == f"usd-recorder {__version__}\n"


def test_pyproject_version_matches_package_version():
    # pyproject.toml declares the version statically (release tooling reads it without building), so it must
    # move with __version__.
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    declared = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
    assert declared is not None and declared.group(1) == __version__
