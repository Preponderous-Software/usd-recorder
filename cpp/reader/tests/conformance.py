"""The C++ reader and the Python reader agree: for every replay given, ``replay-dump`` and
``dump_python.py`` print byte-identical canonical JSON.

    python cpp/reader/tests/conformance.py --dump build/reader/replay-dump a.usda b.usdc ...

Exit 0 when every file agrees; otherwise the first difference of each file that does not is
printed and the exit status is 1.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from dump_python import canonical  # noqa: E402

from usd_recorder.replay import read_replay  # noqa: E402


def compare(dump: str, path: Path) -> str | None:
    """None when the two readers agree on ``path``, else a description of the first difference."""
    cpp = subprocess.run([dump, str(path)], capture_output=True, check=False)
    if cpp.returncode != 0:
        return f"replay-dump exited {cpp.returncode}: {cpp.stderr.decode(errors='replace').strip()}"
    a = cpp.stdout.decode()
    b = canonical(read_replay(path))
    if a == b:
        return None
    i = next((k for k in range(min(len(a), len(b))) if a[k] != b[k]), min(len(a), len(b)))
    lo, hi = max(0, i - 60), i + 60
    return f"differs at byte {i}:\n  C++:    ...{a[lo:hi]}...\n  Python: ...{b[lo:hi]}..."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", required=True, help="path to the replay-dump executable")
    ap.add_argument("replays", nargs="+", type=Path)
    args = ap.parse_args(argv)
    failed = 0
    for path in args.replays:
        problem = compare(args.dump, path)
        if problem is None:
            print(f"{path}: identical ({path.stat().st_size:,} bytes)")
        else:
            failed += 1
            print(f"{path}: {problem}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
