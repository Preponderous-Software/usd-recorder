"""Record a toy ecosystem as a usd-replay/1 replay: grazers eat a regrowing grass field and
breed or starve, and foxes hunt them.

    python examples/grazers_replay.py out/grazers.usdc --ticks 600 --every 4
    usd-recorder replay validate out/grazers.usdc
    usd-recorder replay info out/grazers.usdc

The state hash is a SHA-256 of the whole sim state, so ``--every 1`` and ``--every 10`` give the
same hash at every tick they share: recording never changes the run.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import random
import struct

import numpy as np

from usd_recorder.replay import ReplayWriter

SIZE = 32  # grass cells per side; one cell is one unit


class Sim:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.tick = 0
        self.grass = np.full((SIZE, SIZE), 0.6)
        self.next_id = 0
        self.grazers: dict[int, list[float]] = {}  # id -> [x, y, energy]
        self.foxes: dict[int, list[float]] = {}
        for _ in range(40):
            self.spawn(self.grazers, self.rng.uniform(0, SIZE), self.rng.uniform(0, SIZE), 50.0)
        for _ in range(3):
            self.spawn(self.foxes, self.rng.uniform(0, SIZE), self.rng.uniform(0, SIZE), 120.0)

    def spawn(self, group, x, y, energy) -> None:
        group[self.next_id] = [x, y, energy]
        self.next_id += 1

    def step(self) -> None:
        r = self.rng
        self.grass = np.minimum(1.0, self.grass + 0.004)
        for gid, g in list(self.grazers.items()):
            g[0] = min(SIZE - 1e-6, max(0.0, g[0] + r.uniform(-0.5, 0.5)))
            g[1] = min(SIZE - 1e-6, max(0.0, g[1] + r.uniform(-0.5, 0.5)))
            cell = (int(g[1]), int(g[0]))
            bite = min(self.grass[cell], 0.2)
            self.grass[cell] -= bite
            g[2] += bite * 20 - 1.0
            if g[2] <= 0:
                del self.grazers[gid]
            elif g[2] > 70:
                g[2] -= 35
                self.spawn(self.grazers, g[0], g[1], 30.0)
        if r.random() < 0.05:  # a grazer wanders in from the edge now and then
            self.spawn(self.grazers, 0.0, r.uniform(0, SIZE), 40.0)
        for fid, f in list(self.foxes.items()):
            prey = min(
                self.grazers.items(), key=lambda kv: (kv[1][0] - f[0]) ** 2 + (kv[1][1] - f[1]) ** 2, default=None
            )
            if prey is not None:
                dx, dy = prey[1][0] - f[0], prey[1][1] - f[1]
                d = math.hypot(dx, dy)
                if d < 0.6 and r.random() < 0.25:
                    del self.grazers[prey[0]]
                    f[2] += 60
                elif d > 0:
                    f[0] += 0.45 * dx / d + r.uniform(-0.2, 0.2)
                    f[1] += 0.45 * dy / d + r.uniform(-0.2, 0.2)
            f[2] -= 1.5
            if f[2] <= 0:
                del self.foxes[fid]
            elif f[2] > 240:
                f[2] -= 140
                self.spawn(self.foxes, f[0], f[1], 100.0)
        if not self.foxes and r.random() < 0.01:
            self.spawn(self.foxes, SIZE - 1e-6, r.uniform(0, SIZE), 120.0)
        self.tick += 1

    def state_hash(self) -> str:
        h = hashlib.sha256(struct.pack("<qq", self.tick, self.next_id))
        h.update(self.grass.astype("<f8").tobytes())
        for group in (self.grazers, self.foxes):
            for k in sorted(group):
                h.update(struct.pack("<q3d", k, *group[k]))
        h.update(repr(self.rng.getstate()).encode())
        return h.hexdigest()[:16]


def grass_colors(grass: np.ndarray) -> np.ndarray:
    soil, lush = np.array([122, 98, 64]), np.array([92, 168, 64])
    g = grass.reshape(-1, 1)
    return np.round(soil + (lush - soil) * g).astype(np.uint8)


def entities(group: dict[int, list[float]], z: float) -> dict:
    ids = sorted(group)
    return {"ids": ids, "points": [[group[i][0], group[i][1], z] for i in ids]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="replay file (.usdc or .usda)")
    parser.add_argument("--ticks", type=int, default=600)
    parser.add_argument("--every", type=int, default=4, help="record one sample every N ticks")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    sim = Sim(args.seed)
    with ReplayWriter(
        args.out,
        sim="grazers-example",
        sim_version="1",
        seed=args.seed,
        tick_rate=10,
        state_hash_algorithm="SHA-256/64 of tick, ids, grass, animals and RNG state",
        playback_rate=60,
        title="Grazers and foxes",
        source="usd-recorder examples/grazers_replay.py",
        params={"ticks": args.ticks, "every": args.every, "size": SIZE},
    ) as w:
        w.entity_set("grazers", label="Grazers", color="#f2efe6", width=0.6)
        w.entity_set("foxes", label="Foxes", color="#e0663a", width=1.0, shape="cube")
        w.field("grass", label="Grass", cols=SIZE, rows=SIZE, origin=(0, 0), cell_size=1.0)
        w.scene_points("stones", [[4, 4, 0.2], [27, 9, 0.2], [15, 25, 0.2]], widths=1.6, color="#8a8f96")
        for _ in range(args.ticks + 1):
            if sim.tick % args.every == 0 or sim.tick == args.ticks:
                w.sample(
                    sim.tick,
                    state_hash=sim.state_hash(),
                    entities={"grazers": entities(sim.grazers, 0.3), "foxes": entities(sim.foxes, 0.5)},
                    fields={"grass": grass_colors(sim.grass)},
                    stats={"grazers": len(sim.grazers), "foxes": len(sim.foxes), "grass": float(sim.grass.mean())},
                )
            if sim.tick == args.ticks:
                break
            sim.step()
    print(args.out)


if __name__ == "__main__":
    main()
