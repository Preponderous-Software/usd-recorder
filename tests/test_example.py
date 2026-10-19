import subprocess
import sys
from pathlib import Path

from pxr import Usd

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "random_walkers.py"


def test_example_records_a_chunked_run(tmp_path):
    out = tmp_path / "run.usda"
    subprocess.run([sys.executable, str(EXAMPLE), str(out), "--ticks", "60", "--chunk-size", "25"], check=True)
    stage = Usd.Stage.Open(str(out))
    assert stage.GetCompositionErrors() == []
    assert (stage.GetStartTimeCode(), stage.GetEndTimeCode()) == (0, 59)
    assert stage.GetPrimAtPath("/World/Ground").IsValid()
    assert stage.GetPrimAtPath("/World").GetAttribute("extras:population").Get(0) == 8


REPLAY_EXAMPLE = EXAMPLE.parent / "grazers_replay.py"


def test_replay_example_validates_and_recording_rate_does_not_change_the_run(tmp_path):
    from usd_recorder.replay import read_replay, validate_replay

    runs = {}
    for every in (1, 7):
        out = tmp_path / f"every{every}.usdc"
        subprocess.run(
            [sys.executable, str(REPLAY_EXAMPLE), str(out), "--ticks", "120", "--every", str(every)], check=True
        )
        assert validate_replay(out) == []
        r = read_replay(out)
        runs[every] = dict(zip(r.ticks, r.hashes, strict=True))
    assert len(runs[1]) == 121 and len(runs[7]) == 19  # 0, 7, ..., 119 and the last tick
    assert all(runs[1][t] == h for t, h in runs[7].items())
