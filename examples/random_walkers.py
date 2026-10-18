"""Record a toy simulation: walkers wander a 20x20 field, are born and die, and log events.

    python examples/random_walkers.py out/run.usda --ticks 300 --chunk-size 100
    usd-recorder inspect out/run.usda
    usd-recorder flatten out/run.usda

Open ``out/run.usda`` (or the flattened ``run_flat.usda``) in usdview, Blender, or any USD tool
and scrub the timeline.
"""

from __future__ import annotations

import argparse
import random

from pxr import Gf, UsdGeom, Vt

from usd_recorder import Recorder


def ground(stage) -> None:
    mesh = UsdGeom.Mesh.Define(stage, "/World/Ground")
    mesh.CreatePointsAttr(
        Vt.Vec3fArray([Gf.Vec3f(0, 0, 0), Gf.Vec3f(20, 0, 0), Gf.Vec3f(20, 20, 0), Gf.Vec3f(0, 20, 0)])
    )
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    mesh.CreateDisplayColorAttr(Vt.Vec3fArray([Gf.Vec3f(0.35, 0.5, 0.3)]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out")
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--chunk-size", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    walkers = {i: [rng.uniform(0, 20), rng.uniform(0, 20), 100] for i in range(8)}
    next_id = len(walkers)
    with Recorder(
        args.out, 24, chunk_size=args.chunk_size, entity_size=0.6, scene=ground, metadata={"example:seed": args.seed}
    ) as rec:
        for tick in range(args.ticks):
            events = []
            for state in walkers.values():
                state[0] = min(20, max(0, state[0] + rng.uniform(-0.4, 0.4)))
                state[1] = min(20, max(0, state[1] + rng.uniform(-0.4, 0.4)))
                state[2] -= rng.randrange(3)
            for wid in [w for w, s in walkers.items() if s[2] <= 0]:
                del walkers[wid]
                events.append(f"walker {wid} died")
            if rng.random() < 0.05:
                walkers[next_id] = [rng.uniform(0, 20), rng.uniform(0, 20), 100]
                events.append(f"walker {next_id} was born")
                next_id += 1
            entities = {
                wid: {"position": (x, y, 0.3), "energy": e, "color": (1 - e / 100, e / 100, 0.2)}
                for wid, (x, y, e) in walkers.items()
            }
            rec.record(tick, entities, extras={"population": len(walkers)}, events={"log": events})
    print(args.out)


if __name__ == "__main__":
    main()
