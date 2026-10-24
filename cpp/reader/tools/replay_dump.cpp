// replay-dump: read a usd-replay/1 replay with the C++ reader and print it as canonical JSON.
//
//   replay-dump REPLAY.usd[a|c]          the canonical JSON on stdout; exit 1 if it cannot be read
//
// The format is the one cpp/reader/tests/dump_python.py prints from the Python read_replay, so
// conformance.py can compare the two byte for byte: keys sorted, no whitespace, integers as
// integers, every other number printed with "%.17g" (exact for both float and double), non-finite
// numbers as null, strings escaped to ASCII as Python's json module escapes them.
//
// Copyright (c) 2026 Daniel McCoy Stephenson. MIT licence; see LICENSE.
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "usd_recorder/replay_reader.hpp"

#if defined(_WIN32)
#include <fcntl.h>
#include <io.h>
#endif

namespace {

using usd_recorder::MetaValue;

class Json {
public:
    std::string out;

    void raw(const char* s) { out += s; }
    void num(double v) {
        if (!std::isfinite(v)) {
            out += "null";
            return;
        }
        char buf[40];
        std::snprintf(buf, sizeof buf, "%.17g", v);
        out += buf;
    }
    void integer(int64_t v) {
        char buf[32];
        std::snprintf(buf, sizeof buf, "%" PRId64, v);
        out += buf;
    }
    void uinteger(uint64_t v) {
        char buf[32];
        std::snprintf(buf, sizeof buf, "%" PRIu64, v);
        out += buf;
    }
    void boolean(bool v) { out += v ? "true" : "false"; }
    void str(const std::string& s) {
        out += '"';
        for (size_t i = 0; i < s.size();) {
            const unsigned char c = static_cast<unsigned char>(s[i]);
            if (c < 0x80) {
                switch (c) {
                    case '"': out += "\\\""; break;
                    case '\\': out += "\\\\"; break;
                    case '\b': out += "\\b"; break;
                    case '\f': out += "\\f"; break;
                    case '\n': out += "\\n"; break;
                    case '\r': out += "\\r"; break;
                    case '\t': out += "\\t"; break;
                    default:
                        if (c < 0x20) u16(c);
                        else out += static_cast<char>(c);
                }
                ++i;
                continue;
            }
            // UTF-8 to a code point; an invalid sequence becomes U+FFFD.
            const size_t len = c >= 0xf0 ? 4 : c >= 0xe0 ? 3 : c >= 0xc0 ? 2 : 0;
            uint32_t cp = len == 4 ? (c & 0x07u) : len == 3 ? (c & 0x0fu) : (c & 0x1fu);
            bool ok = len > 0 && i + len <= s.size();
            for (size_t k = 1; ok && k < len; ++k) {
                const unsigned char cc = static_cast<unsigned char>(s[i + k]);
                if ((cc & 0xc0) != 0x80) ok = false;
                cp = (cp << 6) | (cc & 0x3fu);
            }
            if (!ok) {
                u16(0xfffd);
                ++i;
                continue;
            }
            if (cp >= 0x10000) {
                cp -= 0x10000;
                u16(0xd800 + (cp >> 10));
                u16(0xdc00 + (cp & 0x3ff));
            } else {
                u16(cp);
            }
            i += len;
        }
        out += '"';
    }

private:
    void u16(uint32_t unit) {
        char buf[16];
        std::snprintf(buf, sizeof buf, "\\u%04x", unit);
        out += buf;
    }
};

// Keys are written in sorted order by the callers (every object below lists them alphabetically).
void meta(Json& j, const MetaValue& v) {
    switch (v.kind) {
        case MetaValue::Kind::Null: j.raw("null"); break;
        case MetaValue::Kind::Bool: j.boolean(v.b); break;
        case MetaValue::Kind::Int: j.integer(v.i); break;
        case MetaValue::Kind::UInt: j.uinteger(v.u); break;
        case MetaValue::Kind::Double: j.num(v.d); break;
        case MetaValue::Kind::String: j.str(v.s); break;
        case MetaValue::Kind::Dict: {
            j.raw("{");
            for (size_t k = 0; k < v.dict.size(); ++k) {
                if (k) j.raw(",");
                j.str(v.dict[k].first);
                j.raw(":");
                meta(j, v.dict[k].second);
            }
            j.raw("}");
            break;
        }
    }
}

void vec3s(Json& j, const std::vector<usd_recorder::Vec3f>& a) {
    j.raw("[");
    for (size_t i = 0; i < a.size(); ++i) {
        if (i) j.raw(",");
        j.raw("[");
        j.num(a[i][0]);
        j.raw(",");
        j.num(a[i][1]);
        j.raw(",");
        j.num(a[i][2]);
        j.raw("]");
    }
    j.raw("]");
}

void floats(Json& j, const std::vector<float>& a) {
    j.raw("[");
    for (size_t i = 0; i < a.size(); ++i) {
        if (i) j.raw(",");
        j.num(a[i]);
    }
    j.raw("]");
}

void doubles(Json& j, const std::vector<double>& a) {
    j.raw("[");
    for (size_t i = 0; i < a.size(); ++i) {
        if (i) j.raw(",");
        j.num(a[i]);
    }
    j.raw("]");
}

void ints(Json& j, const std::vector<int64_t>& a) {
    j.raw("[");
    for (size_t i = 0; i < a.size(); ++i) {
        if (i) j.raw(",");
        j.integer(a[i]);
    }
    j.raw("]");
}

template <class T, class F>
void list(Json& j, const std::vector<T>& a, F each) {
    j.raw("[");
    for (size_t i = 0; i < a.size(); ++i) {
        if (i) j.raw(",");
        each(a[i]);
    }
    j.raw("]");
}

std::string dump(const usd_recorder::Replay& r) {
    Json j;
    j.raw("{\"fields\":");
    list(j, r.fields, [&](const usd_recorder::Field& f) {
        j.raw("{\"cellSize\":");
        j.num(f.cell_size);
        j.raw(",\"colors\":");
        list(j, f.colors, [&](const std::vector<usd_recorder::Vec3f>& c) { vec3s(j, c); });
        j.raw(",\"cols\":");
        j.integer(f.cols);
        j.raw(",\"label\":");
        j.str(f.label);
        j.raw(",\"name\":");
        j.str(f.name);
        j.raw(",\"origin\":[");
        j.num(f.origin[0]);
        j.raw(",");
        j.num(f.origin[1]);
        j.raw("],\"rows\":");
        j.integer(f.rows);
        j.raw(",\"ticks\":");
        ints(j, f.ticks);
        j.raw(",\"z\":");
        j.num(f.z);
        j.raw("}");
    });
    j.raw(",\"hashes\":");
    list(j, r.hashes, [&](const std::string& h) { j.str(h); });
    j.raw(",\"meta\":");
    MetaValue m;
    m.kind = MetaValue::Kind::Dict;
    m.dict = r.meta;
    meta(j, m);
    j.raw(",\"metersPerUnit\":");
    j.num(r.meters_per_unit);
    j.raw(",\"playbackRate\":");
    j.num(r.playback_rate);
    j.raw(",\"scene\":");
    list(j, r.scene, [&](const usd_recorder::ScenePoints& s) {
        j.raw("{\"colors\":");
        vec3s(j, s.colors);
        j.raw(",\"label\":");
        j.str(s.label);
        j.raw(",\"name\":");
        j.str(s.name);
        j.raw(",\"points\":");
        vec3s(j, s.points);
        j.raw(",\"shape\":");
        j.str(s.shape);
        j.raw(",\"widths\":");
        floats(j, s.widths);
        j.raw("}");
    });
    j.raw(",\"sets\":");
    list(j, r.sets, [&](const usd_recorder::EntitySet& s) {
        j.raw("{\"colors\":");
        if (s.vertex_color) list(j, s.colors, [&](const std::vector<usd_recorder::Vec3f>& c) { vec3s(j, c); });
        else vec3s(j, s.colors.at(0));
        j.raw(",\"ids\":");
        list(j, s.ids, [&](const std::vector<int64_t>& a) { ints(j, a); });
        j.raw(",\"label\":");
        j.str(s.label);
        j.raw(",\"name\":");
        j.str(s.name);
        j.raw(",\"points\":");
        list(j, s.points, [&](const std::vector<usd_recorder::Vec3f>& p) { vec3s(j, p); });
        j.raw(",\"shape\":");
        j.str(s.shape);
        j.raw(",\"vertexColor\":");
        j.boolean(s.vertex_color);
        j.raw(",\"vertexWidth\":");
        j.boolean(s.vertex_width);
        j.raw(",\"widths\":");
        if (s.vertex_width) list(j, s.widths, [&](const std::vector<float>& w) { floats(j, w); });
        else floats(j, s.widths.at(0));
        j.raw("}");
    });
    j.raw(",\"stats\":{");
    for (size_t k = 0; k < r.stats.size(); ++k) {
        if (k) j.raw(",");
        j.str(r.stats[k].name);
        j.raw(":{\"ticks\":");
        ints(j, r.stats[k].ticks);
        j.raw(",\"values\":");
        doubles(j, r.stats[k].values);
        j.raw("}");
    }
    j.raw("},\"ticks\":");
    ints(j, r.ticks);
    j.raw("}\n");
    return j.out;
}

}  // namespace

// Leaves without running static destructors. After OpenUSD's text parser has failed on a damaged
// .usda, process teardown sometimes aborts ("pure virtual method called") in OpenUSD's own
// shutdown, after main has returned; fuzz.py found it, and a ten-line program that only calls
// SdfLayer::FindOrOpen shows it too. Everything this tool owes is written by then.
[[noreturn]] void finish(int code) {
    std::fflush(stdout);
    std::fflush(stderr);
    std::_Exit(code);
}

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: replay-dump REPLAY.usd[a|c]\n");
        finish(2);
    }
#if defined(_WIN32)
    _setmode(_fileno(stdout), _O_BINARY);  // "\n", never "\r\n": the JSON is compared byte for byte
#endif
    try {
        const std::string json = dump(usd_recorder::read_replay(argv[1]));
        std::fwrite(json.data(), 1, json.size(), stdout);
        finish(0);
    } catch (const usd_recorder::ReplayError& e) {
        std::fprintf(stderr, "replay-dump: %s\n", e.what());
        finish(1);
    }
}
