// A fixed replay scenario for tests/test_cpp_replay.py: the same samples are produced in Python
// (tests/replay_scenario.py), so the two writers' outputs can be compared value by value.
#include <cstdio>
#include <vector>

#include "usd_recorder/replay_writer.hpp"

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: replay_scenario OUT.usda\n");
        return 2;
    }
    usd_recorder::ReplayMeta meta;
    meta.sim = "scenario";
    meta.sim_version = "1.0.0";
    meta.seed = "18446744073709551615";
    meta.tick_rate = 20;
    meta.playback_rate = 12;
    meta.state_hash_algorithm = "scenario (tick * 2654435761)";
    meta.title = "C++ \"scenario\"";
    meta.number_params["cells"] = 6;
    meta.string_params["mode"] = "test";
    usd_recorder::ReplayWriter w(meta);
    w.entity_set("walkers", "Walkers", usd_recorder::Rgb8{90, 158, 75}, 0.75f);
    w.entity_set("glowers", "", std::nullopt, std::nullopt, "cube");
    w.field("heat", "Heat", 3, 2, -1.5, 2.0, 0.5, -0.25);
    w.scene_points("rocks", "Rocks", {{{1.f, 2.f, 0.f}}, {{3.5f, -1.25f, 0.f}}}, {1.5f, 2.f}, {usd_recorder::Rgb8{128, 128, 128}});
    for (int64_t tick = 0; tick <= 60; tick += 5) {
        char hash[17];
        std::snprintf(hash, sizeof hash, "%016llx", static_cast<unsigned long long>(tick) * 2654435761ULL);
        w.begin_sample(tick, hash);
        std::vector<int64_t> ids;
        std::vector<float> xyz;
        for (int64_t id = tick / 10; id < tick / 10 + 4; ++id) {
            ids.push_back(id);
            xyz.insert(xyz.end(), {id * 0.5f + tick * 0.25f, -id * 1.5f, 0.125f});
        }
        w.entities("walkers", ids.data(), xyz.data(), ids.size());
        std::vector<int64_t> gids;
        std::vector<float> gxyz, gw;
        std::vector<usd_recorder::Rgb8> gc;
        for (int64_t id = 100; id < 100 + (tick / 20); ++id) {
            gids.push_back(id);
            gxyz.insert(gxyz.end(), {static_cast<float>(id - 100), static_cast<float>(tick) / 4.f, 0.f});
            gw.push_back(0.25f * static_cast<float>(1 + (id % 3)));
            gc.push_back({static_cast<uint8_t>((id * 37 + tick) % 256), static_cast<uint8_t>((id * 11) % 256), 7});
        }
        w.entities("glowers", gids.data(), gxyz.data(), gids.size(), gc.data(), gw.data());
        std::vector<usd_recorder::Rgb8> heat(6);
        for (int k = 0; k < 6; ++k) heat[k] = {static_cast<uint8_t>((k * 40 + (tick / 20) * 9) % 256), 0, 255};
        w.field_colors("heat", heat.data());
        w.stat("population", static_cast<double>(ids.size() + gids.size()));
        if (tick % 10 == 0) w.stat("energy", tick * 1.5);
        w.end_sample();
    }
    w.write(argv[1]);
    return 0;
}
