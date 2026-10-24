// The usd-replay/1 reader (see usd_recorder/replay_reader.hpp). It reads exactly what the Python
// read_replay in src/usd_recorder/replay.py reads, in the same order, so the two give the same
// result on every conforming file (cpp/reader/tests/conformance.py checks that).
//
// Copyright (c) 2026 Daniel McCoy Stephenson. MIT licence; see LICENSE.
#include "usd_recorder/replay_reader.hpp"

#include <algorithm>
#include <cstdlib>
#include <exception>
#include <limits>
#include <new>
#include <string>

#include <pxr/base/gf/vec2d.h>
#include <pxr/base/gf/vec2i.h>
#include <pxr/base/gf/vec3f.h>
#include <pxr/base/tf/token.h>
#include <pxr/base/vt/array.h>
#include <pxr/base/vt/dictionary.h>
#include <pxr/base/vt/types.h>
#include <pxr/base/vt/value.h>
#include <pxr/usd/sdf/layer.h>
#include <pxr/usd/usd/attribute.h>
#include <pxr/usd/usd/prim.h>
#include <pxr/usd/usd/stage.h>
#include <pxr/usd/usdGeom/mesh.h>
#include <pxr/usd/usdGeom/metrics.h>
#include <pxr/usd/usdGeom/points.h>
#include <pxr/usd/usdGeom/primvar.h>
#include <pxr/usd/usdGeom/primvarsAPI.h>
#include <pxr/usd/usdGeom/tokens.h>

PXR_NAMESPACE_USING_DIRECTIVE

namespace usd_recorder {
namespace {

const char* const kRoot = "/Replay";
const char* const kEntities = "/Replay/Entities";
const char* const kFields = "/Replay/Fields";
const char* const kScene = "/Replay/Scene";
const char* const kHashAttr = "replay:stateHash";
const char* const kStatPrefix = "replay:stat:";
const char* const kLabelAttr = "replay:label";
const char* const kShapeAttr = "replay:shape";
const char* const kFieldSize = "replay:field:size";
const char* const kFieldOrigin = "replay:field:origin";
const char* const kFieldCell = "replay:field:cellSize";
const char* const kSchemaPrefix = "usd-replay/";

// The most cells one field may have: far beyond the "64 x 64 or less" REPLAY.md calls typical,
// small enough that cols * rows never overflows and a bit flip in the size cannot ask for
// gigabytes.
constexpr int64_t kMaxFieldCells = int64_t(1) << 24;

// Counts the bytes the replay's arrays take and refuses to go over the cap.
class Budget {
public:
    explicit Budget(uint64_t max) : max_(max) {}
    void take(uint64_t bytes, const std::string& where) {
        if (bytes > max_ - used_) {
            throw ReplayError(where + ": the replay needs more than " + std::to_string(max_) +
                              " bytes of arrays (ReadOptions::max_bytes)");
        }
        used_ += bytes;
    }

private:
    uint64_t max_;
    uint64_t used_ = 0;
};

MetaValue meta_value(const VtValue& v) {
    MetaValue m;
    if (v.IsHolding<std::string>()) {
        m.kind = MetaValue::Kind::String;
        m.s = v.UncheckedGet<std::string>();
    } else if (v.IsHolding<TfToken>()) {
        m.kind = MetaValue::Kind::String;
        m.s = v.UncheckedGet<TfToken>().GetString();
    } else if (v.IsHolding<bool>()) {
        m.kind = MetaValue::Kind::Bool;
        m.b = v.UncheckedGet<bool>();
    } else if (v.IsHolding<int>()) {
        m.kind = MetaValue::Kind::Int;
        m.i = v.UncheckedGet<int>();
    } else if (v.IsHolding<int64_t>()) {
        m.kind = MetaValue::Kind::Int;
        m.i = v.UncheckedGet<int64_t>();
    } else if (v.IsHolding<unsigned int>()) {
        m.kind = MetaValue::Kind::Int;
        m.i = v.UncheckedGet<unsigned int>();
    } else if (v.IsHolding<uint64_t>()) {
        const uint64_t u = v.UncheckedGet<uint64_t>();
        if (u <= uint64_t(std::numeric_limits<int64_t>::max())) {
            m.kind = MetaValue::Kind::Int;
            m.i = int64_t(u);
        } else {
            m.kind = MetaValue::Kind::UInt;
            m.u = u;
        }
    } else if (v.IsHolding<double>()) {
        m.kind = MetaValue::Kind::Double;
        m.d = v.UncheckedGet<double>();
    } else if (v.IsHolding<float>()) {
        m.kind = MetaValue::Kind::Double;
        m.d = v.UncheckedGet<float>();
    } else if (v.IsHolding<VtDictionary>()) {
        m.kind = MetaValue::Kind::Dict;
        for (const auto& kv : v.UncheckedGet<VtDictionary>()) m.dict.emplace_back(kv.first, meta_value(kv.second));
        std::sort(m.dict.begin(), m.dict.end(), [](const auto& a, const auto& b) { return a.first < b.first; });
    }
    return m;
}

// `replay:label` / `replay:shape`: the authored string, or `fallback` when it is missing or empty.
std::string string_attr(const UsdPrim& prim, const char* name, const std::string& fallback) {
    const UsdAttribute attr = prim.GetAttribute(TfToken(name));
    VtValue v;
    if (!attr || !attr.Get(&v)) return fallback;
    std::string s;
    if (v.IsHolding<std::string>()) s = v.UncheckedGet<std::string>();
    else if (v.IsHolding<TfToken>()) s = v.UncheckedGet<TfToken>().GetString();
    return s.empty() ? fallback : s;
}

std::vector<int64_t> int_ticks(const std::vector<double>& times) {
    std::vector<int64_t> out;
    out.reserve(times.size());
    for (double t : times) {
        if (!(t > -9.2e18 && t < 9.2e18)) throw ReplayError("a time sample is not a finite tick");
        out.push_back(static_cast<int64_t>(t));  // truncates, as Python's int() does
    }
    return out;
}

template <class T>
bool get_array(const UsdAttribute& attr, UsdTimeCode time, VtArray<T>* out) {
    out->clear();
    return attr && attr.Get(out, time);
}

std::vector<Vec3f> vec3s(const VtVec3fArray& a, Budget& budget, const std::string& where) {
    budget.take(uint64_t(a.size()) * sizeof(Vec3f), where);
    std::vector<Vec3f> out(a.size());
    for (size_t i = 0; i < a.size(); ++i) out[i] = {a[i][0], a[i][1], a[i][2]};
    return out;
}

std::vector<float> floats(const VtFloatArray& a, Budget& budget, const std::string& where) {
    budget.take(uint64_t(a.size()) * sizeof(float), where);
    return std::vector<float>(a.begin(), a.end());
}

std::vector<int64_t> int64s(const VtInt64Array& a, Budget& budget, const std::string& where) {
    budget.take(uint64_t(a.size()) * sizeof(int64_t), where);
    return std::vector<int64_t>(a.begin(), a.end());
}

std::string at(const UsdPrim& prim, int64_t tick) { return prim.GetPath().GetString() + "@" + std::to_string(tick); }

EntitySet read_set(const UsdPrim& prim, const std::vector<int64_t>& ticks, Budget& budget) {
    const UsdGeomPoints pts(prim);
    const UsdGeomPrimvar color_pv = UsdGeomPrimvarsAPI(prim).GetPrimvar(TfToken("displayColor"));
    EntitySet s;
    s.name = prim.GetName().GetString();
    s.label = string_attr(prim, kLabelAttr, s.name);
    s.shape = string_attr(prim, kShapeAttr, "sphere");
    s.vertex_color = color_pv.GetInterpolation() == UsdGeomTokens->vertex;
    s.vertex_width = pts.GetWidthsInterpolation() == UsdGeomTokens->vertex;
    const UsdAttribute ids_attr = pts.GetIdsAttr(), points_attr = pts.GetPointsAttr(), widths_attr = pts.GetWidthsAttr();
    VtInt64Array ids;
    VtVec3fArray xyz, rgb;
    VtFloatArray w;
    for (int64_t tick : ticks) {
        const UsdTimeCode t(static_cast<double>(tick));
        const std::string where = at(prim, tick);
        get_array(ids_attr, t, &ids);
        get_array(points_attr, t, &xyz);
        if (ids.size() != xyz.size()) {
            throw ReplayError(where + ": " + std::to_string(ids.size()) + " ids but " + std::to_string(xyz.size()) +
                              " points");
        }
        s.ids.push_back(int64s(ids, budget, where));
        s.points.push_back(vec3s(xyz, budget, where));
        if (s.vertex_color) {
            rgb.clear();
            if (color_pv) color_pv.Get(&rgb, t);
            if (rgb.size() != ids.size()) throw ReplayError(where + ": vertex displayColor needs one colour per point");
            s.colors.push_back(vec3s(rgb, budget, where));
        }
        if (s.vertex_width) {
            get_array(widths_attr, t, &w);
            if (w.size() != ids.size()) throw ReplayError(where + ": vertex widths need one width per point");
            s.widths.push_back(floats(w, budget, where));
        }
    }
    const std::string where = prim.GetPath().GetString();
    if (!s.vertex_color) {
        rgb.clear();
        if (color_pv) color_pv.Get(&rgb, UsdTimeCode::Default());
        s.colors.push_back(vec3s(rgb, budget, where));
    }
    if (!s.vertex_width) {
        get_array(widths_attr, UsdTimeCode::Default(), &w);
        s.widths.push_back(floats(w, budget, where));
    }
    return s;
}

Field read_field(const UsdPrim& prim, Budget& budget) {
    const std::string where = prim.GetPath().GetString();
    Field f;
    f.name = prim.GetName().GetString();
    f.label = string_attr(prim, kLabelAttr, f.name);
    GfVec2i size(0, 0);
    GfVec2d origin(0.0, 0.0);
    double cell = 0.0;
    const UsdAttribute size_attr = prim.GetAttribute(TfToken(kFieldSize));
    const UsdAttribute origin_attr = prim.GetAttribute(TfToken(kFieldOrigin));
    const UsdAttribute cell_attr = prim.GetAttribute(TfToken(kFieldCell));
    if (!size_attr || !size_attr.Get(&size)) throw ReplayError(where + ": missing int2 " + kFieldSize);
    if (!origin_attr || !origin_attr.Get(&origin)) throw ReplayError(where + ": missing double2 " + kFieldOrigin);
    if (!cell_attr || !cell_attr.Get(&cell)) throw ReplayError(where + ": missing double " + kFieldCell);
    if (size[0] < 1 || size[1] < 1 || int64_t(size[0]) * size[1] > kMaxFieldCells) {
        throw ReplayError(where + ": field size " + std::to_string(size[0]) + " x " + std::to_string(size[1]) +
                          " is out of range");
    }
    f.cols = size[0];
    f.rows = size[1];
    f.origin = {origin[0], origin[1]};
    f.cell_size = cell;
    VtVec3fArray mesh_pts;
    get_array(UsdGeomMesh(prim).GetPointsAttr(), UsdTimeCode::Default(), &mesh_pts);
    f.z = mesh_pts.empty() ? 0.0 : static_cast<double>(mesh_pts[0][2]);
    const UsdGeomPrimvar pv = UsdGeomPrimvarsAPI(prim).GetPrimvar(TfToken("displayColor"));
    std::vector<double> times;
    if (pv) pv.GetAttr().GetTimeSamples(&times);
    f.ticks = int_ticks(times);
    const size_t cells = size_t(f.cols) * size_t(f.rows);
    VtVec3fArray rgb;
    for (size_t k = 0; k < times.size(); ++k) {
        rgb.clear();
        pv.Get(&rgb, UsdTimeCode(times[k]));
        const std::string here = at(prim, f.ticks[k]);
        if (rgb.size() != cells) throw ReplayError(here + ": a field sample needs cols * rows colours");
        f.colors.push_back(vec3s(rgb, budget, here));
    }
    return f;
}

ScenePoints read_scene(const UsdPrim& prim, Budget& budget) {
    const std::string where = prim.GetPath().GetString();
    const UsdGeomPoints pts(prim);
    ScenePoints s;
    s.name = prim.GetName().GetString();
    s.label = string_attr(prim, kLabelAttr, s.name);
    s.shape = string_attr(prim, kShapeAttr, "sphere");
    VtVec3fArray xyz, rgb;
    VtFloatArray w;
    get_array(pts.GetPointsAttr(), UsdTimeCode::Default(), &xyz);
    s.points = vec3s(xyz, budget, where);
    const UsdGeomPrimvar pv = UsdGeomPrimvarsAPI(prim).GetPrimvar(TfToken("displayColor"));
    if (pv) pv.Get(&rgb, UsdTimeCode::Default());
    s.colors = vec3s(rgb, budget, where);
    get_array(pts.GetWidthsAttr(), UsdTimeCode::Default(), &w);
    s.widths = floats(w, budget, where);
    if (s.colors.size() > 1 && s.colors.size() != s.points.size()) {
        throw ReplayError(where + ": displayColor needs one value or one per point");
    }
    if (s.widths.size() > 1 && s.widths.size() != s.points.size()) {
        throw ReplayError(where + ": widths need one value or one per point");
    }
    return s;
}

std::vector<UsdPrim> children(const UsdStageRefPtr& stage, const char* path) {
    std::vector<UsdPrim> out;
    const UsdPrim parent = stage->GetPrimAtPath(SdfPath(path));
    if (parent) {
        for (const UsdPrim& child : parent.GetChildren()) out.push_back(child);
    }
    return out;
}

Replay read(const std::string& path, const ReadOptions& options) {
    const SdfLayerRefPtr layer = SdfLayer::FindOrOpen(path);
    if (!layer) throw ReplayError("cannot open " + path);
    const VtDictionary custom = layer->GetCustomLayerData();
    const auto it = custom.find("usdReplay");
    if (it == custom.end() || !it->second.IsHolding<VtDictionary>()) {
        throw ReplayError(path + " is not a usd-replay replay (no customLayerData.usdReplay)");
    }
    const VtDictionary& meta = it->second.UncheckedGet<VtDictionary>();
    const auto schema = meta.find("schema");
    if (schema == meta.end() || !schema->second.IsHolding<std::string>()) {
        throw ReplayError(path + ": customLayerData.usdReplay.schema is missing");
    }
    const std::string& name = schema->second.UncheckedGet<std::string>();
    const std::string major = name.rfind(kSchemaPrefix, 0) == 0 ? name.substr(std::string(kSchemaPrefix).size()) : "";
    if (major != std::to_string(kReplaySchemaMajor)) {
        throw ReplayError(path + ": schema \"" + name + "\" is not usd-replay/" + std::to_string(kReplaySchemaMajor) +
                          " (this reader refuses any other major version)");
    }
    if (!layer->GetSubLayerPaths().empty()) throw ReplayError(path + ": a replay is one layer, with no sublayers");
    if (layer->GetDefaultPrim() != TfToken("Replay")) throw ReplayError(path + ": defaultPrim must be \"Replay\"");

    const UsdStageRefPtr stage = UsdStage::Open(layer, UsdStage::LoadAll);
    if (!stage) throw ReplayError("cannot open the stage of " + path);
    const UsdPrim root = stage->GetPrimAtPath(SdfPath(kRoot));
    const UsdAttribute hash_attr = root ? root.GetAttribute(TfToken(kHashAttr)) : UsdAttribute();
    if (!hash_attr) throw ReplayError(path + ": missing /Replay." + kHashAttr);
    std::vector<double> times;
    hash_attr.GetTimeSamples(&times);
    if (times.empty()) throw ReplayError(path + ": " + kHashAttr + " has no time samples");

    Budget budget(options.max_bytes);
    Replay rep;
    rep.path = path;
    rep.meta = meta_value(VtValue(meta)).dict;
    rep.playback_rate = stage->GetTimeCodesPerSecond();
    rep.meters_per_unit = UsdGeomGetStageMetersPerUnit(stage);
    rep.ticks = int_ticks(times);
    for (int64_t tick : rep.ticks) {
        std::string h;
        VtValue v;
        if (hash_attr.Get(&v, UsdTimeCode(static_cast<double>(tick)))) {
            if (v.IsHolding<std::string>()) h = v.UncheckedGet<std::string>();
            else if (v.IsHolding<TfToken>()) h = v.UncheckedGet<TfToken>().GetString();
        }
        budget.take(h.size() + sizeof(std::string), path);
        rep.hashes.push_back(std::move(h));
    }
    for (const UsdPrim& prim : children(stage, kEntities)) rep.sets.push_back(read_set(prim, rep.ticks, budget));
    for (const UsdPrim& prim : children(stage, kFields)) rep.fields.push_back(read_field(prim, budget));
    for (const UsdAttribute& attr : root.GetAttributes()) {
        const std::string attr_name = attr.GetName().GetString();
        if (attr_name.rfind(kStatPrefix, 0) != 0) continue;
        Stat st;
        st.name = attr_name.substr(std::string(kStatPrefix).size());
        std::vector<double> ts;
        attr.GetTimeSamples(&ts);
        st.ticks = int_ticks(ts);
        budget.take(uint64_t(ts.size()) * 16, path);
        for (double t : ts) {
            VtValue v;
            double value = 0.0;
            if (attr.Get(&v, UsdTimeCode(t))) {
                if (v.IsHolding<double>()) value = v.UncheckedGet<double>();
                else if (v.IsHolding<float>()) value = v.UncheckedGet<float>();
                else if (v.IsHolding<int>()) value = v.UncheckedGet<int>();
                else throw ReplayError(path + ": " + attr_name + " is not a number");
            }
            st.values.push_back(value);
        }
        rep.stats.push_back(std::move(st));
    }
    std::sort(rep.stats.begin(), rep.stats.end(), [](const Stat& a, const Stat& b) { return a.name < b.name; });
    for (const UsdPrim& prim : children(stage, kScene)) rep.scene.push_back(read_scene(prim, budget));
    return rep;
}

}  // namespace

Replay read_replay(const std::string& path, const ReadOptions& options) {
    try {
        return read(path, options);
    } catch (const ReplayError&) {
        throw;
    } catch (const std::bad_alloc&) {
        throw ReplayError(path + ": out of memory while reading");
    } catch (const std::exception& e) {
        throw ReplayError(path + ": " + e.what());
    }
}

}  // namespace usd_recorder
