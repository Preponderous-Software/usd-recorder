"""The replay scenario of cpp/tests/replay_scenario.cpp, through the Python ReplayWriter."""

from __future__ import annotations

import numpy as np

from usd_recorder.replay import ReplayWriter


def write(path) -> None:
    f32 = np.float32
    with ReplayWriter(
        path,
        sim="scenario",
        sim_version="1.0.0",
        seed="18446744073709551615",
        tick_rate=20,
        playback_rate=12,
        state_hash_algorithm="scenario (tick * 2654435761)",
        title='C++ "scenario"',
        params={"cells": 6.0, "mode": "test"},
    ) as w:
        w.entity_set("walkers", label="Walkers", color=(90, 158, 75), width=0.75)
        w.entity_set("glowers", color=None, width=None, shape="cube")
        w.field("heat", label="Heat", cols=3, rows=2, origin=(-1.5, 2.0), cell_size=0.5, z=-0.25)
        w.scene_points("rocks", [[1, 2, 0], [3.5, -1.25, 0]], widths=[1.5, 2.0], color=(128, 128, 128), label="Rocks")
        for tick in range(0, 61, 5):
            ids = list(range(tick // 10, tick // 10 + 4))
            pts = [[f32(i) * f32(0.5) + f32(tick) * f32(0.25), -f32(i) * f32(1.5), 0.125] for i in ids]
            gids = list(range(100, 100 + tick // 20))
            stats = {"population": float(len(ids) + len(gids))}
            if tick % 10 == 0:
                stats["energy"] = tick * 1.5
            w.sample(
                tick,
                state_hash=f"{(tick * 2654435761) & (2**64 - 1):016x}",
                entities={
                    "walkers": {"ids": ids, "points": pts},
                    "glowers": {
                        "ids": gids,
                        "points": [[i - 100, tick / 4, 0] for i in gids],
                        "widths": [0.25 * (1 + i % 3) for i in gids],
                        "colors": np.array(
                            [[(i * 37 + tick) % 256, (i * 11) % 256, 7] for i in gids], dtype=np.uint8
                        ).reshape(-1, 3),
                    },
                },
                fields={
                    "heat": np.array([[(k * 40 + (tick // 20) * 9) % 256, 0, 255] for k in range(6)], dtype=np.uint8)
                },
                stats=stats,
            )
