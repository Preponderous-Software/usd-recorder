"""Print a replay as read by the Python ``read_replay``, in the canonical JSON of replay-dump.

    python cpp/reader/tests/dump_python.py REPLAY.usd[a|c]

The format is cpp/reader/tools/replay_dump.cpp's: keys sorted, no whitespace, integers as
integers, every other number as ``"%.17g"`` (exact for float32 and float64 alike), non-finite
numbers as null, strings as Python's json module escapes them. conformance.py compares the two.
"""

from __future__ import annotations

import json
import math
import sys
from typing import Any

import numpy as np

from usd_recorder.replay import Replay, read_replay


def _num(v: float) -> str:
    return f"{v:.17g}" if math.isfinite(v) else "null"


def _canon(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool | np.bool_):
        return "true" if v else "false"
    if isinstance(v, int | np.integer):
        return str(int(v))
    if isinstance(v, float | np.floating):
        return _num(float(v))
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, np.ndarray):
        return _canon(v.tolist())
    if isinstance(v, list | tuple):
        return "[" + ",".join(_canon(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join(json.dumps(k) + ":" + _canon(v[k]) for k in sorted(v)) + "}"
    raise TypeError(f"no canonical form for {type(v).__name__}")


def _meta(v: Any) -> Any:
    """Metadata values the C++ reader keeps (strings, numbers, booleans, dictionaries); any other
    USD type becomes None, as it does there."""
    if isinstance(v, dict):
        return {str(k): _meta(x) for k, x in v.items()}
    if isinstance(v, bool | int | float | str):
        return v
    return None


def canonical(r: Replay) -> str:
    doc = {
        "fields": [
            {
                "cellSize": float(f.cell_size),
                "colors": f.colors,
                "cols": f.cols,
                "label": f.label,
                "name": f.name,
                "origin": [float(f.origin[0]), float(f.origin[1])],
                "rows": f.rows,
                "ticks": f.ticks,
                "z": float(f.z),
            }
            for f in r.fields
        ],
        "hashes": [str(h) for h in r.hashes],
        "meta": _meta(r.meta),
        "metersPerUnit": float(r.meters_per_unit),
        "playbackRate": float(r.playback_rate),
        "scene": [
            {
                "colors": s.colors,
                "label": s.label,
                "name": s.name,
                "points": s.points,
                "shape": s.shape,
                "widths": s.widths,
            }
            for s in r.scene
        ],
        "sets": [
            {
                "colors": s.colors,
                "ids": s.ids,
                "label": s.label,
                "name": s.name,
                "points": s.points,
                "shape": s.shape,
                "vertexColor": s.vertex_color,
                "vertexWidth": s.vertex_width,
                "widths": s.widths,
            }
            for s in r.sets
        ],
        "stats": {name: {"ticks": ts, "values": [float(v) for v in vs]} for name, (ts, vs) in r.stats.items()},
        "ticks": r.ticks,
    }
    return _canon(doc) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: dump_python.py REPLAY.usd[a|c]", file=sys.stderr)
        return 2
    sys.stdout.write(canonical(read_replay(argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
