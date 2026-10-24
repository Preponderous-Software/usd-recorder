"""Feed the C++ reader damaged replays: every one must be read or refused, never crash.

    python cpp/reader/tests/fuzz.py --dump build/reader/replay-dump --runs 100 a.usda b.usdc ...

For each replay, ``--runs`` copies are truncated at a random length and ``--runs`` have random
bytes flipped (a fixed ``--seed``, so a failure reproduces). ``replay-dump`` must exit 0 (it read
the file) or 1 (it refused it with ReplayError), within ``--timeout`` seconds and under a memory
limit; a signal, an abort or a timeout fails the run and the damaged file is kept in ``--keep``.
"""

from __future__ import annotations

import argparse
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def _limit_memory(limit: int):
    def apply() -> None:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ImportError, ValueError, OSError):
            pass

    return apply


def mutate(data: bytes, rng: random.Random, kind: str) -> bytes:
    if kind == "truncate":
        return data[: rng.randrange(0, len(data))]
    b = bytearray(data)
    for _ in range(rng.randint(1, 8)):
        i = rng.randrange(len(b))
        b[i] ^= 1 << rng.randrange(8)
    return bytes(b)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", required=True, help="path to the replay-dump executable")
    ap.add_argument("--runs", type=int, default=50, help="truncations and bit-flip runs per replay (each)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--memory", type=int, default=4 << 30, help="address-space limit in bytes (POSIX only)")
    ap.add_argument("--keep", type=Path, default=Path("fuzz-failures"), help="where failing inputs are kept")
    ap.add_argument("replays", nargs="+", type=Path)
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)
    failures = 0
    counts = {0: 0, 1: 0}
    with tempfile.TemporaryDirectory() as tmp:
        for src in args.replays:
            data = src.read_bytes()
            for kind in ("truncate", "flip"):
                for run in range(args.runs):
                    path = Path(tmp) / f"{src.stem}.{kind}{run}{src.suffix}"
                    path.write_bytes(mutate(data, rng, kind))
                    try:
                        p = subprocess.run(
                            [args.dump, str(path)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                            timeout=args.timeout,
                            preexec_fn=_limit_memory(args.memory) if sys.platform != "win32" else None,
                            check=False,
                        )
                        code, err = p.returncode, p.stderr.decode(errors="replace").strip().splitlines()
                    except subprocess.TimeoutExpired:
                        code, err = "timeout", []
                    if code in (0, 1):
                        counts[code] += 1
                        continue
                    failures += 1
                    args.keep.mkdir(parents=True, exist_ok=True)
                    shutil.copy(path, args.keep / path.name)
                    print(f"FAIL {path.name}: exit {code}; {err[-1] if err else ''}")
    total = counts[0] + counts[1] + failures
    print(f"{total} damaged replays: {counts[0]} read, {counts[1]} refused, {failures} crashed or hung")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
