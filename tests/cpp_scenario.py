"""The Python twin of cpp/tests/scenario.cpp: the same draws in the same order."""

from __future__ import annotations

MASK = (1 << 64) - 1


class Rng:
    def __init__(self, seed: int = 42):
        self.state = seed

    def __call__(self) -> int:
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) & MASK
        return self.state >> 33


def frames():
    r = Rng()
    first = [0, 0, 0, 7, 20, 33]
    moods = ["calm", 'says "hi"', "two\nlines"]
    out = []
    for t in range(50):
        tick = t + (t // 10) * 3
        entities, extras, events = {}, {}, {}
        for eid in range(1, 6):
            if t < first[eid] or r() % 10 < 2:
                continue
            values = {}
            if r() % 3:
                values["position"] = (float(r() % 10), float(r() % 10), 0.5)
            if r() % 4 == 0:
                values["hp"] = r() % 100
            if eid == 2 and r() % 5 == 0:
                values["mood"] = moods[r() % 3]
            if eid == 3:
                values["speed"] = (r() % 1000) / 8.0
            if r() % 7 == 0:
                values["alive"] = bool(r() % 2)
            if eid == 4 and r() % 3 == 0:
                values["dir"] = (float(r() % 5) - 2.0, 0.25, 1e-3)
            entities[eid] = values
        if r() % 2:
            extras["clock"] = tick * 0.1
        if r() % 6 == 0:
            log = [f"e{t}"]
            if r() % 2:
                log.append("again")
            events["log"] = log
        out.append((tick, entities, extras, events))
    return out
