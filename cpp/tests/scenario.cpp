// A deterministic recording used by tests/test_cpp_writer.py: the same scenario is recorded by
// the Python Recorder (tests/cpp_scenario.py mirrors this file step by step) and both stages
// must resolve to the same values at every tick.
#include <cstdint>
#include <iostream>
#include <string>

#include "usd_recorder/usda_writer.hpp"

static uint64_t state = 42;
static uint64_t r() {
    state = state * 6364136223846793005ULL + 1442695040888963407ULL;
    return state >> 33;
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "usage: scenario OUT.usda\n";
        return 2;
    }
    const int first[6] = {0, 0, 0, 7, 20, 33};
    const char* moods[3] = {"calm", "says \"hi\"", "two\nlines"};
    usd_recorder::UsdaRecorder rec(10.0);
    for (int t = 0; t < 50; ++t) {
        double tick = t + (t / 10) * 3;
        rec.begin_tick(tick);
        for (int id = 1; id <= 5; ++id) {
            if (t < first[id] || r() % 10 < 2) continue;
            auto e = rec.entity(id);
            if (r() % 3) {
                double x = double(r() % 10);  // separate statements: argument order is unspecified
                double y = double(r() % 10);
                e.position(x, y, 0.5);
            }
            if (r() % 4 == 0) e.set("hp", int32_t(r() % 100));
            if (id == 2 && r() % 5 == 0) e.set("mood", moods[r() % 3]);
            if (id == 3) e.set("speed", double(r() % 1000) / 8.0);
            if (r() % 7 == 0) e.set("alive", bool(r() % 2));
            if (id == 4 && r() % 3 == 0) e.set("dir", usd_recorder::Vec3{double(r() % 5) - 2.0, 0.25, 1e-3});
        }
        if (r() % 2) rec.extra("clock", tick * 0.1);
        if (r() % 6 == 0) {
            rec.event("log", "e" + std::to_string(t));
            if (r() % 2) rec.event("log", "again");
        }
        rec.end_tick();
    }
    rec.write(argv[1]);
    return 0;
}
