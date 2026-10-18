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
