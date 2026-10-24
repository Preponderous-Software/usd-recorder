// usd_recorder/replay_reader.hpp -- read a usd-replay/1 replay (docs/REPLAY.md) from C++.
//
// The C++ counterpart of the Python `usd_recorder.replay.read_replay`: the same fields, in plain
// structs, so a C++ consumer (a player, a game) can load any conforming replay. Unlike the
// header-only writers next to it, the reader links OpenUSD (Sdf, Usd, UsdGeom), so it is a
// separate, optional CMake target: `usd_recorder::replay_reader` (cpp/CMakeLists.txt,
// -DUSD_RECORDER_BUILD_READER=ON). A producer that only writes never needs OpenUSD.
//
// What it checks: the `usdReplay.schema` major version (it refuses anything but usd-replay/1, as
// REPLAY.md § Versioning requires), one layer without sublayers, `defaultPrim = Replay`, the
// sample ticks from `replay:stateHash`, and that every array a consumer indexes has the length it
// must have (ids and points, vertex colours and widths, field colours). Everything else is the
// validator's job: run `usd-recorder replay validate` for the full rule check.
//
// Replays are files from other people, so the reader also caps the memory its arrays may take
// (ReadOptions::max_bytes) and refuses a file that would need more.
//
// Copyright (c) 2026 Daniel McCoy Stephenson. MIT licence; see LICENSE.
#ifndef USD_RECORDER_REPLAY_READER_HPP
#define USD_RECORDER_REPLAY_READER_HPP

#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace usd_recorder {

using Vec3f = std::array<float, 3>;

/// The major version of the replay contract this reader understands.
constexpr int kReplaySchemaMajor = 1;

/// A value in `customLayerData["usdReplay"]`: strings, numbers, booleans and (for `params`)
/// nested dictionaries. Values of any other USD type are kept as `Null`.
struct MetaValue {
    enum class Kind { Null, Bool, Int, UInt, Double, String, Dict };
    Kind kind = Kind::Null;
    bool b = false;
    int64_t i = 0;
    uint64_t u = 0;  // UInt: an unsigned value above INT64_MAX
    double d = 0.0;
    std::string s;
    std::vector<std::pair<std::string, MetaValue>> dict;  // sorted by key
};

/// One kind of entity (`/Replay/Entities/<name>`).
struct EntitySet {
    std::string name;
    std::string label;  // `replay:label`, or the name
    std::string shape;  // `replay:shape` as authored, or "sphere"
    bool vertex_color = false;
    bool vertex_width = false;
    std::vector<std::vector<int64_t>> ids;  // per sample
    std::vector<std::vector<Vec3f>> points;  // per sample, same length as ids
    // Linear colours. Constant (vertex_color false): one entry, the authored value (possibly
    // empty). Vertex: one entry per sample, one colour per point.
    std::vector<std::vector<Vec3f>> colors;
    // Widths, the same way: one entry when constant, one per sample when vertex.
    std::vector<std::vector<float>> widths;
};

/// One colour grid (`/Replay/Fields/<name>`). Cell (i, j) is index j * cols + i.
struct Field {
    std::string name;
    std::string label;
    int cols = 0;
    int rows = 0;
    std::array<double, 2> origin{};  // x, y of the grid's minimum corner
    double cell_size = 0.0;
    double z = 0.0;                         // height of the mesh
    std::vector<int64_t> ticks;             // the ticks it was sampled at (a subset of the replay's)
    std::vector<std::vector<Vec3f>> colors;  // linear, cols * rows per field sample
};

/// Static points (`/Replay/Scene/<name>`).
struct ScenePoints {
    std::string name;
    std::string label;
    std::string shape;
    std::vector<Vec3f> points;
    std::vector<Vec3f> colors;  // linear; one value (constant) or one per point
    std::vector<float> widths;  // one value (constant) or one per point
};

/// One `replay:stat:<name>` series, held between its samples.
struct Stat {
    std::string name;
    std::vector<int64_t> ticks;
    std::vector<double> values;
};

/// A whole replay in memory, mirroring the Python `Replay` dataclass.
struct Replay {
    std::string path;
    std::vector<std::pair<std::string, MetaValue>> meta;  // customLayerData["usdReplay"], sorted by key
    double playback_rate = 0.0;                           // timeCodesPerSecond: suggested ticks per second
    double meters_per_unit = 1.0;
    std::vector<int64_t> ticks;       // the sample ticks, strictly increasing
    std::vector<std::string> hashes;  // the state hash at each sample tick
    std::vector<EntitySet> sets;      // in the stage's child order
    std::vector<Field> fields;
    std::vector<Stat> stats;  // sorted by name
    std::vector<ScenePoints> scene;
};

struct ReadOptions {
    /// The most bytes the replay's arrays may take in memory; a larger replay is refused with
    /// ReplayError rather than allocated. The default fits every replay published so far many
    /// times over.
    uint64_t max_bytes = uint64_t(2) << 30;
};

/// Thrown for a file that cannot be opened, is not a usd-replay/1 replay, or breaks one of the
/// rules the reader checks.
class ReplayError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// Loads the replay at `path` (`.usda` or `.usdc`). Throws ReplayError.
Replay read_replay(const std::string& path, const ReadOptions& options = {});

}  // namespace usd_recorder

#endif  // USD_RECORDER_REPLAY_READER_HPP
