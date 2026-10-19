"""The ``usd-replay/1`` contract: write, read and validate shareable simulation replays.

See docs/REPLAY.md for the layout. In short: one layer, ``/Replay`` as the default prim, a state
hash at every sample tick, one ``Points`` prim per kind of entity with ``ids``/``points`` sampled
at every sample tick, optional colour grids under ``Fields`` and static ``Points`` under ``Scene``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from .atomic import atomic_export, layer_format

SCHEMA = "usd-replay/1"
ROOT = "/Replay"
ENTITIES = ROOT + "/Entities"
FIELDS = ROOT + "/Fields"
SCENE = ROOT + "/Scene"
HASH_ATTR = "replay:stateHash"
STAT_PREFIX = "replay:stat:"
LABEL_ATTR = "replay:label"
SHAPE_ATTR = "replay:shape"
FIELD_SIZE = "replay:field:size"
FIELD_ORIGIN = "replay:field:origin"
FIELD_CELL = "replay:field:cellSize"
SHAPES = ("sphere", "cube")
REQUIRED_META = ("schema", "sim", "simVersion", "seed", "tickRate", "stateHashAlgorithm")
HASH_RE = re.compile(r"^[0-9a-f]{8,64}$")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# ------------------------------------------------------------------------------------- colours --


def srgb_to_linear(rgb: Any) -> np.ndarray:
    """sRGB colours (``"#rrggbb"``, 0-255 integers or 0-1 floats; one or many) to linear floats."""
    if isinstance(rgb, str):
        rgb = [hex_rgb(rgb)]
    a = np.asarray(rgb)
    if a.dtype.kind in "OU":
        a = np.array([hex_rgb(c) for c in a.ravel()], dtype=np.uint8)
    if a.dtype.kind in "iu":
        a = a.astype(np.float64) / 255.0
    else:
        a = a.astype(np.float64)
    if a.shape[-1] != 3:
        raise ValueError(f"colours must have 3 channels, got shape {a.shape}")
    if np.any(a < 0) or np.any(a > 1) or not np.all(np.isfinite(a)):
        raise ValueError("colour channels must be in [0, 1] (floats) or 0-255 (integers)")
    return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)


def linear_to_srgb8(lin: Any) -> np.ndarray:
    """Linear colours back to sRGB bytes (what a player displays)."""
    a = np.clip(np.asarray(lin, dtype=np.float64), 0.0, 1.0)
    s = np.where(a <= 0.0031308, a * 12.92, 1.055 * a ** (1 / 2.4) - 0.055)
    return np.round(s * 255.0).astype(np.uint8)


def hex_rgb(s: str) -> tuple[int, int, int]:
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        raise ValueError(f"not a #rrggbb colour: {s!r}")
    return int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)


def _vec3f(a: np.ndarray) -> Vt.Vec3fArray:
    a = np.ascontiguousarray(a, dtype=np.float32).reshape(-1, 3)
    return Vt.Vec3fArray.FromNumpy(a)


# -------------------------------------------------------------------------------------- writer --


@dataclass
class _Set:
    name: str
    vertex_color: bool = False
    vertex_width: bool = False


@dataclass
class _Field:
    name: str
    cols: int
    rows: int
    last: np.ndarray | None = None


class ReplayWriter:
    """Write a ``usd-replay/1`` layer, one sample at a time; the file appears atomically on
    :meth:`close` (``.usdc`` or ``.usda`` by the path's extension).

    Declare entity sets, fields and scene points first, then call :meth:`sample` with strictly
    increasing integer ticks. A field whose colours did not change since its last sample is not
    resampled (players hold it).
    """

    def __init__(
        self,
        path: str | Path,
        *,
        sim: str,
        sim_version: str,
        seed: int | str,
        tick_rate: float,
        state_hash_algorithm: str,
        playback_rate: float = 30.0,
        meters_per_unit: float = 1.0,
        title: str | None = None,
        description: str | None = None,
        source: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> None:
        self.path = Path(path)
        layer_format(self.path)  # validates the extension
        if not sim or not isinstance(sim, str):
            raise ValueError("sim must be a non-empty string")
        seed_s = str(seed)
        if not seed_s.isdigit():
            raise ValueError(f"seed must be a non-negative integer, got {seed!r}")
        if not (tick_rate > 0 and math.isfinite(tick_rate)):
            raise ValueError("tick_rate must be a positive number")
        if not (playback_rate > 0 and math.isfinite(playback_rate)):
            raise ValueError("playback_rate must be a positive number")
        self.stage = Usd.Stage.CreateInMemory()
        self.layer = self.stage.GetRootLayer()
        UsdGeom.SetStageUpAxis(self.stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(self.stage, meters_per_unit)
        self.stage.SetTimeCodesPerSecond(playback_rate)
        self.stage.SetFramesPerSecond(playback_rate)
        meta: dict[str, Any] = {
            "schema": SCHEMA,
            "sim": sim,
            "simVersion": str(sim_version),
            "seed": seed_s,
            "tickRate": float(tick_rate),
            "stateHashAlgorithm": str(state_hash_algorithm),
        }
        for key, value in (("title", title), ("description", description), ("source", source)):
            if value:
                meta[key] = str(value)
        if params:
            meta["params"] = dict(params)
        self.layer.customLayerData = {"usdReplay": meta}
        root = UsdGeom.Xform.Define(self.stage, ROOT)
        self.stage.SetDefaultPrim(root.GetPrim())
        UsdGeom.Scope.Define(self.stage, ENTITIES)
        self._hash = root.GetPrim().CreateAttribute(HASH_ATTR, Sdf.ValueTypeNames.String)
        self._sets: dict[str, _Set] = {}
        self._fields: dict[str, _Field] = {}
        self._ticks: list[int] = []
        self._closed = False

    # ---- declarations ----
    def _name(self, name: str, taken: Iterable[str]) -> None:
        if not NAME_RE.match(name):
            raise ValueError(f"{name!r} is not a valid prim name")
        if name in taken:
            raise ValueError(f"{name!r} is already declared")
        if self._ticks:
            raise RuntimeError("declare sets, fields and scene points before the first sample")

    def entity_set(
        self,
        name: str,
        *,
        label: str | None = None,
        color: Any = "#9aa6b2",
        width: float | None = 1.0,
        shape: str = "sphere",
    ) -> None:
        """Declare a set. ``color``/``width`` given here are constant; pass ``None`` to give
        per-point values in every sample (``colors``/``widths``) instead."""
        self._name(name, self._sets)
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}")
        pts = UsdGeom.Points.Define(self.stage, f"{ENTITIES}/{name}")
        prim = pts.GetPrim()
        prim.CreateAttribute(LABEL_ATTR, Sdf.ValueTypeNames.String).Set(label or name)
        prim.CreateAttribute(SHAPE_ATTR, Sdf.ValueTypeNames.Token).Set(shape)
        s = _Set(name)
        color_pv = UsdGeom.PrimvarsAPI(prim).CreatePrimvar(
            "displayColor",
            Sdf.ValueTypeNames.Color3fArray,
            UsdGeom.Tokens.constant if color is not None else UsdGeom.Tokens.vertex,
        )
        if color is not None:
            color_pv.Set(_vec3f(srgb_to_linear(color)))
        else:
            s.vertex_color = True
        if width is not None:
            pts.CreateWidthsAttr().Set(Vt.FloatArray([float(width)]))
            pts.SetWidthsInterpolation(UsdGeom.Tokens.constant)
        else:
            pts.CreateWidthsAttr()
            pts.SetWidthsInterpolation(UsdGeom.Tokens.vertex)
            s.vertex_width = True
        pts.CreateIdsAttr()
        pts.CreatePointsAttr()
        self._sets[name] = s

    def field(
        self,
        name: str,
        *,
        cols: int,
        rows: int,
        origin: Sequence[float] = (0.0, 0.0),
        cell_size: float = 1.0,
        z: float = 0.0,
        label: str | None = None,
    ) -> None:
        """Declare a ``cols x rows`` colour grid whose minimum corner is ``origin``."""
        self._name(name, self._fields)
        if cols < 1 or rows < 1:
            raise ValueError("a field needs at least one column and one row")
        if not cell_size > 0:
            raise ValueError("cell_size must be positive")
        UsdGeom.Scope.Define(self.stage, FIELDS)
        mesh = UsdGeom.Mesh.Define(self.stage, f"{FIELDS}/{name}")
        ox, oy = float(origin[0]), float(origin[1])
        xs = ox + np.arange(cols + 1) * cell_size
        ys = oy + np.arange(rows + 1) * cell_size
        gx, gy = np.meshgrid(xs, ys)
        pts = np.stack([gx.ravel(), gy.ravel(), np.full(gx.size, z)], axis=1)
        mesh.CreatePointsAttr().Set(_vec3f(pts))
        mesh.CreateFaceVertexCountsAttr().Set(Vt.IntArray([4] * (cols * rows)))
        j, i = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
        a = (j * (cols + 1) + i).ravel()
        idx = np.stack([a, a + 1, a + cols + 2, a + cols + 1], axis=1).ravel()
        mesh.CreateFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(idx.astype(np.int32)))
        mesh.CreateSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
        mesh.CreateDoubleSidedAttr().Set(True)
        mesh.CreateExtentAttr().Set(Vt.Vec3fArray([Gf.Vec3f(xs[0], ys[0], z), Gf.Vec3f(xs[-1], ys[-1], z)]))
        prim = mesh.GetPrim()
        prim.CreateAttribute(FIELD_SIZE, Sdf.ValueTypeNames.Int2).Set(Gf.Vec2i(cols, rows))
        prim.CreateAttribute(FIELD_ORIGIN, Sdf.ValueTypeNames.Double2).Set(Gf.Vec2d(ox, oy))
        prim.CreateAttribute(FIELD_CELL, Sdf.ValueTypeNames.Double).Set(float(cell_size))
        prim.CreateAttribute(LABEL_ATTR, Sdf.ValueTypeNames.String).Set(label or name)
        UsdGeom.PrimvarsAPI(prim).CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.uniform)
        self._fields[name] = _Field(name, cols, rows)

    def scene_points(
        self,
        name: str,
        points: Any,
        *,
        widths: Any = 1.0,
        color: Any = "#808080",
        label: str | None = None,
        shape: str = "sphere",
    ) -> None:
        """Static points (obstacles, landmarks). ``widths``/``color`` may be one value or one per point."""
        if not NAME_RE.match(name):
            raise ValueError(f"{name!r} is not a valid prim name")
        if self._ticks:
            raise RuntimeError("declare scene points before the first sample")
        if shape not in SHAPES:
            raise ValueError(f"shape must be one of {SHAPES}")
        path = f"{SCENE}/{name}"
        if self.stage.GetPrimAtPath(path):
            raise ValueError(f"{name!r} is already declared")
        UsdGeom.Scope.Define(self.stage, SCENE)
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        pts = UsdGeom.Points.Define(self.stage, path)
        pts.CreatePointsAttr().Set(_vec3f(p))
        w = np.atleast_1d(np.asarray(widths, dtype=np.float32))
        pts.CreateWidthsAttr().Set(Vt.FloatArray.FromNumpy(w))
        pts.SetWidthsInterpolation(UsdGeom.Tokens.constant if w.size == 1 else UsdGeom.Tokens.vertex)
        c = srgb_to_linear(color).reshape(-1, 3)
        UsdGeom.PrimvarsAPI(pts.GetPrim()).CreatePrimvar(
            "displayColor",
            Sdf.ValueTypeNames.Color3fArray,
            UsdGeom.Tokens.constant if len(c) == 1 else UsdGeom.Tokens.vertex,
        ).Set(_vec3f(c))
        pts.GetPrim().CreateAttribute(LABEL_ATTR, Sdf.ValueTypeNames.String).Set(label or name)
        pts.GetPrim().CreateAttribute(SHAPE_ATTR, Sdf.ValueTypeNames.Token).Set(shape)

    # ---- samples ----
    def sample(
        self,
        tick: int,
        *,
        state_hash: str,
        entities: Mapping[str, Mapping[str, Any]] | None = None,
        fields: Mapping[str, Any] | None = None,
        stats: Mapping[str, float] | None = None,
    ) -> None:
        """Record the state after ``tick``. Every declared set must be given (``ids`` and
        ``points``, plus ``colors``/``widths`` for per-point sets); fields are optional after
        the first sample (omitted = unchanged)."""
        if self._closed:
            raise RuntimeError("the writer is closed")
        if isinstance(tick, bool) or int(tick) != tick:
            raise ValueError(f"ticks are integers, got {tick!r}")
        tick = int(tick)
        if self._ticks and tick <= self._ticks[-1]:
            raise ValueError(f"tick {tick} does not follow tick {self._ticks[-1]}")
        if not isinstance(state_hash, str) or not HASH_RE.match(state_hash):
            raise ValueError(f"state_hash must be 8-64 lowercase hex digits, got {state_hash!r}")
        entities = dict(entities or {})
        fields = dict(fields or {})
        unknown = (set(entities) - set(self._sets)) | (set(fields) - set(self._fields))
        if unknown:
            raise KeyError(f"not declared: {sorted(unknown)}")
        missing = set(self._sets) - set(entities)
        if missing:
            raise ValueError(f"every set needs a sample; missing {sorted(missing)}")
        if not self._ticks and set(fields) != set(self._fields):
            raise ValueError("the first sample must give every field")
        # Validate everything before writing anything.
        staged_sets = {}
        for name, data in entities.items():
            s = self._sets[name]
            ids = np.asarray(data.get("ids", []), dtype=np.int64).ravel()
            pts = np.asarray(data.get("points", np.zeros((0, 3))), dtype=np.float64).reshape(-1, 3)
            if len(ids) != len(pts):
                raise ValueError(f"{name}: {len(ids)} ids but {len(pts)} points")
            if len(np.unique(ids)) != len(ids):
                raise ValueError(f"{name}: ids must be unique within a sample")
            if not np.all(np.isfinite(pts)):
                raise ValueError(f"{name}: points must be finite")
            colors = widths = None
            if s.vertex_color:
                if "colors" not in data:
                    raise ValueError(f"{name}: per-point colours are needed in every sample")
                colors = srgb_to_linear(data["colors"]).reshape(-1, 3) if len(ids) else np.zeros((0, 3))
                if len(colors) != len(ids):
                    raise ValueError(f"{name}: {len(colors)} colours for {len(ids)} points")
            elif "colors" in data:
                raise ValueError(f"{name}: has a constant colour; do not pass colors")
            if s.vertex_width:
                if "widths" not in data:
                    raise ValueError(f"{name}: per-point widths are needed in every sample")
                widths = np.asarray(data["widths"], dtype=np.float32).ravel()
                if len(widths) != len(ids):
                    raise ValueError(f"{name}: {len(widths)} widths for {len(ids)} points")
            elif "widths" in data:
                raise ValueError(f"{name}: has a constant width; do not pass widths")
            staged_sets[name] = (ids, pts, colors, widths)
        staged_fields = {}
        for name, colors in fields.items():
            f = self._fields[name]
            lin = srgb_to_linear(colors).reshape(-1, 3).astype(np.float32)
            if len(lin) != f.cols * f.rows:
                raise ValueError(f"{name}: {len(lin)} colours for a {f.cols}x{f.rows} field")
            staged_fields[name] = lin
        for key, value in (stats or {}).items():
            if not NAME_RE.match(key):
                raise ValueError(f"stat name {key!r} is not valid")
            if not math.isfinite(float(value)):
                raise ValueError(f"stat {key} must be finite")

        t = Usd.TimeCode(tick)
        self._hash.Set(state_hash, t)
        for name, (ids, pts, colors, widths) in staged_sets.items():
            p = UsdGeom.Points.Get(self.stage, f"{ENTITIES}/{name}")
            p.GetIdsAttr().Set(Vt.Int64Array.FromNumpy(ids), t)
            p.GetPointsAttr().Set(_vec3f(pts), t)
            if colors is not None:
                UsdGeom.PrimvarsAPI(p.GetPrim()).GetPrimvar("displayColor").Set(_vec3f(colors), t)
            if widths is not None:
                p.GetWidthsAttr().Set(Vt.FloatArray.FromNumpy(widths), t)
        for name, lin in staged_fields.items():
            f = self._fields[name]
            if f.last is not None and np.array_equal(f.last, lin):
                continue
            f.last = lin
            prim = self.stage.GetPrimAtPath(f"{FIELDS}/{name}")
            UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor").Set(_vec3f(lin), t)
        root = self.stage.GetPrimAtPath(ROOT)
        for key, value in (stats or {}).items():
            attr = root.GetAttribute(STAT_PREFIX + key)
            if not attr:
                attr = root.CreateAttribute(STAT_PREFIX + key, Sdf.ValueTypeNames.Double)
            attr.Set(float(value), t)
        self._ticks.append(tick)

    def close(self) -> Path:
        """Write the file atomically and return its path."""
        if self._closed:
            return self.path
        if not self._ticks:
            raise RuntimeError("a replay needs at least one sample")
        self.stage.SetStartTimeCode(self._ticks[0])
        self.stage.SetEndTimeCode(self._ticks[-1])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_export(self.layer, self.path)
        self._closed = True
        return self.path

    def __enter__(self) -> ReplayWriter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.close()


# -------------------------------------------------------------------------------------- reader --


@dataclass
class EntitySet:
    name: str
    label: str
    shape: str
    ids: list[np.ndarray]  # per sample
    points: list[np.ndarray]  # per sample, (n, 3) float32
    colors: list[np.ndarray] | np.ndarray  # linear; (1, 3) constant or per sample (n, 3)
    widths: list[np.ndarray] | np.ndarray  # (1,) constant or per sample (n,)
    vertex_color: bool
    vertex_width: bool


@dataclass
class Field:
    name: str
    label: str
    cols: int
    rows: int
    origin: tuple[float, float]
    cell_size: float
    z: float
    ticks: list[int]  # the ticks it was sampled at (a subset of the replay's)
    colors: list[np.ndarray]  # linear (cols*rows, 3) per field sample


@dataclass
class ScenePoints:
    name: str
    label: str
    shape: str
    points: np.ndarray
    colors: np.ndarray
    widths: np.ndarray


@dataclass
class Replay:
    path: Path
    meta: dict[str, Any]
    playback_rate: float
    meters_per_unit: float
    ticks: list[int]
    hashes: list[str]
    sets: list[EntitySet] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    stats: dict[str, tuple[list[int], list[float]]] = field(default_factory=dict)
    scene: list[ScenePoints] = field(default_factory=list)


def _arr(value: Any, dtype: Any, width: int | None = None) -> np.ndarray:
    if value is None:
        return np.zeros((0, width) if width else (0,), dtype=dtype)
    a = np.asarray(value, dtype=dtype)
    return a.reshape(-1, width) if width else a.ravel()


def read_replay(path: str | Path, *, validate: bool = True) -> Replay:
    """Load a replay into numpy arrays. With ``validate`` (the default) a file that does not
    conform raises ``ValueError`` listing every problem."""
    if validate:
        problems = validate_replay(path)
        if problems:
            raise ValueError(f"{path} is not a valid {SCHEMA} replay:\n  " + "\n  ".join(problems))
    stage = Usd.Stage.Open(str(path))
    root = stage.GetPrimAtPath(ROOT)
    hash_attr = root.GetAttribute(HASH_ATTR)
    ticks = [int(t) for t in hash_attr.GetTimeSamples()]
    hashes = [hash_attr.Get(t) for t in ticks]
    rep = Replay(
        path=Path(path),
        meta=dict(stage.GetRootLayer().customLayerData.get("usdReplay", {})),
        playback_rate=stage.GetTimeCodesPerSecond(),
        meters_per_unit=UsdGeom.GetStageMetersPerUnit(stage),
        ticks=ticks,
        hashes=hashes,
    )
    ents = stage.GetPrimAtPath(ENTITIES)
    for prim in ents.GetChildren() if ents else []:
        pts = UsdGeom.Points(prim)
        color_pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
        vc = color_pv.GetInterpolation() == UsdGeom.Tokens.vertex
        vw = pts.GetWidthsInterpolation() == UsdGeom.Tokens.vertex
        s = EntitySet(
            name=prim.GetName(),
            label=_string(prim, LABEL_ATTR, prim.GetName()),
            shape=_string(prim, SHAPE_ATTR, "sphere"),
            ids=[_arr(pts.GetIdsAttr().Get(t), np.int64) for t in ticks],
            points=[_arr(pts.GetPointsAttr().Get(t), np.float32, 3) for t in ticks],
            colors=[_arr(color_pv.Get(t), np.float32, 3) for t in ticks] if vc else _arr(color_pv.Get(), np.float32, 3),
            widths=[_arr(pts.GetWidthsAttr().Get(t), np.float32) for t in ticks]
            if vw
            else _arr(pts.GetWidthsAttr().Get(), np.float32),
            vertex_color=vc,
            vertex_width=vw,
        )
        rep.sets.append(s)
    flds = stage.GetPrimAtPath(FIELDS)
    for prim in flds.GetChildren() if flds else []:
        cols, rows = prim.GetAttribute(FIELD_SIZE).Get()
        origin = prim.GetAttribute(FIELD_ORIGIN).Get()
        pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
        fticks = [int(t) for t in pv.GetAttr().GetTimeSamples()]
        mesh_pts = UsdGeom.Mesh(prim).GetPointsAttr().Get()
        rep.fields.append(
            Field(
                name=prim.GetName(),
                label=_string(prim, LABEL_ATTR, prim.GetName()),
                cols=int(cols),
                rows=int(rows),
                origin=(float(origin[0]), float(origin[1])),
                cell_size=float(prim.GetAttribute(FIELD_CELL).Get()),
                z=float(mesh_pts[0][2]) if mesh_pts else 0.0,
                ticks=fticks,
                colors=[_arr(pv.Get(t), np.float32, 3) for t in fticks],
            )
        )
    for attr in root.GetAttributes():
        name = attr.GetName()
        if name.startswith(STAT_PREFIX):
            ts = [int(t) for t in attr.GetTimeSamples()]
            rep.stats[name[len(STAT_PREFIX) :]] = (ts, [float(attr.Get(t)) for t in ts])
    scene = stage.GetPrimAtPath(SCENE)
    for prim in scene.GetChildren() if scene else []:
        pts = UsdGeom.Points(prim)
        rep.scene.append(
            ScenePoints(
                name=prim.GetName(),
                label=_string(prim, LABEL_ATTR, prim.GetName()),
                shape=_string(prim, SHAPE_ATTR, "sphere"),
                points=_arr(pts.GetPointsAttr().Get(), np.float32, 3),
                colors=_arr(UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor").Get(), np.float32, 3),
                widths=_arr(pts.GetWidthsAttr().Get(), np.float32),
            )
        )
    return rep


def _string(prim: Usd.Prim, name: str, default: str) -> str:
    attr = prim.GetAttribute(name)
    value = attr.Get() if attr else None
    return str(value) if value else default


# ----------------------------------------------------------------------------------- validator --


def validate_replay(path: str | Path) -> list[str]:
    """Every way ``path`` breaks the ``usd-replay/1`` contract, as ``"<where>: <problem>"``
    strings; ``[]`` when it conforms."""
    p: list[str] = []
    path = Path(path)
    if not path.is_file():
        return [f"{path}: no such file"]
    try:
        layer = Sdf.Layer.FindOrOpen(str(path))
    except Exception as exc:  # pxr raises Tf.ErrorException
        return [f"{path}: cannot open: {exc}"]
    if layer is None:
        return [f"{path}: cannot open as a USD layer"]

    # Layer shape: one self-contained layer.
    if layer.subLayerPaths:
        p.append("layer: has sublayers; a replay is one flattened layer")
    for spec_path in _all_prim_specs(layer):
        spec = layer.GetPrimAtPath(spec_path)
        if spec.referenceList.GetAddedOrExplicitItems() or spec.payloadList.GetAddedOrExplicitItems():
            p.append(f"{spec_path}: references or payloads are not allowed")
        if "clips" in spec.ListInfoKeys():
            p.append(f"{spec_path}: value clips are not allowed; flatten the recording first")

    meta = layer.customLayerData.get("usdReplay")
    if not isinstance(meta, dict):
        p.append("customLayerData: missing the usdReplay dictionary")
        meta = {}
    for key in REQUIRED_META:
        if key not in meta:
            p.append(f"usdReplay: missing {key!r}")
    if "schema" in meta and meta["schema"] != SCHEMA:
        p.append(f"usdReplay.schema: {meta['schema']!r} is not {SCHEMA!r}")
    for key in ("sim", "simVersion", "stateHashAlgorithm"):
        if key in meta and not (isinstance(meta[key], str) and meta[key]):
            p.append(f"usdReplay.{key}: must be a non-empty string")
    if "seed" in meta and not (isinstance(meta["seed"], str) and meta["seed"].isdigit()):
        p.append("usdReplay.seed: must be a string of decimal digits")
    if "tickRate" in meta and not (
        isinstance(meta["tickRate"], int | float) and not isinstance(meta["tickRate"], bool) and meta["tickRate"] > 0
    ):
        p.append("usdReplay.tickRate: must be a positive number")

    stage = Usd.Stage.Open(layer)
    if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z:
        p.append("stage: upAxis must be Z")
    root = stage.GetPrimAtPath(ROOT)
    if not root:
        p.append(f"{ROOT}: missing")
        return p
    if stage.GetDefaultPrim() != root:
        p.append(f"stage: defaultPrim must be {ROOT[1:]!r}")
    if root.GetTypeName() != "Xform":
        p.append(f"{ROOT}: must be an Xform, is {root.GetTypeName() or 'typeless'}")

    hash_attr = root.GetAttribute(HASH_ATTR)
    if not hash_attr or hash_attr.GetTypeName() != Sdf.ValueTypeNames.String:
        p.append(f"{ROOT}.{HASH_ATTR}: missing or not a string")
        return p
    times = list(hash_attr.GetTimeSamples())
    if not times:
        p.append(f"{ROOT}.{HASH_ATTR}: needs a time sample at every sample tick (has none)")
        return p
    if any(t != int(t) for t in times):
        p.append(f"{ROOT}.{HASH_ATTR}: sample ticks must be integers")
    for t in times:
        v = hash_attr.Get(t)
        if not (isinstance(v, str) and HASH_RE.match(v)):
            p.append(f"{ROOT}.{HASH_ATTR}@{t:g}: {v!r} is not 8-64 lowercase hex digits")
            break
    tickset = set(times)
    if stage.GetStartTimeCode() != times[0] or stage.GetEndTimeCode() != times[-1]:
        p.append(f"stage: start/endTimeCode must be the first and last sample ticks ({times[0]:g}, {times[-1]:g})")

    def subset(attr: Usd.Attribute, where: str) -> list[float]:
        ts = list(attr.GetTimeSamples())
        extra = [t for t in ts if t not in tickset]
        if extra:
            p.append(f"{where}: sampled at {extra[0]:g}, which is not a sample tick")
        return ts

    def exactly(attr: Usd.Attribute, where: str) -> bool:
        ts = list(attr.GetTimeSamples())
        if ts != times:
            missing = sorted(tickset - set(ts))
            p.append(
                f"{where}: must be sampled at every sample tick"
                + (f" (missing {missing[0]:g})" if missing else f" (extra {sorted(set(ts) - tickset)[0]:g})")
            )
            return False
        return True

    known_root = {HASH_ATTR}
    for attr in root.GetAttributes():
        name = attr.GetName()
        if name.startswith(STAT_PREFIX):
            known_root.add(name)
            if attr.GetTypeName() != Sdf.ValueTypeNames.Double:
                p.append(f"{ROOT}.{name}: stats must be double")
            subset(attr, f"{ROOT}.{name}")

    ents = stage.GetPrimAtPath(ENTITIES)
    if not ents:
        p.append(f"{ENTITIES}: missing (it may be empty)")
    else:
        if ents.GetTypeName() != "Scope":
            p.append(f"{ENTITIES}: must be a Scope")
        for prim in ents.GetChildren():
            _check_set(prim, times, exactly, p)

    flds = stage.GetPrimAtPath(FIELDS)
    if flds:
        if flds.GetTypeName() != "Scope":
            p.append(f"{FIELDS}: must be a Scope")
        for prim in flds.GetChildren():
            _check_field(prim, times, subset, p)

    scene = stage.GetPrimAtPath(SCENE)
    if scene:
        if scene.GetTypeName() != "Scope":
            p.append(f"{SCENE}: must be a Scope")
        for prim in Usd.PrimRange(scene):
            if prim != scene and prim.GetTypeName() != "Points":
                p.append(f"{prim.GetPath()}: scene prims must be Points")
            for attr in prim.GetAttributes():
                if attr.GetNumTimeSamples():
                    p.append(f"{attr.GetPath()}: the scene is static; no time samples allowed")
            if prim != scene and prim.GetTypeName() == "Points":
                _check_static_points(prim, p)

    allowed_roots = {ROOT}
    for prim in stage.GetPseudoRoot().GetChildren():
        if prim.GetPath().pathString not in allowed_roots:
            p.append(f"{prim.GetPath()}: only {ROOT} is allowed at the root")
    for attr in root.GetAttributes():
        if attr.GetNumTimeSamples() and attr.GetName() not in known_root:
            p.append(f"{ROOT}.{attr.GetName()}: unexpected time-sampled attribute")
    return p


def _all_prim_specs(layer: Sdf.Layer) -> list[Sdf.Path]:
    out: list[Sdf.Path] = []
    layer.Traverse(Sdf.Path.absoluteRootPath, lambda path: out.append(path) if path.IsPrimPath() else None)
    return out


def _check_set(prim: Usd.Prim, times: list[float], exactly, p: list[str]) -> None:
    where = str(prim.GetPath())
    if prim.GetTypeName() != "Points":
        p.append(f"{where}: entity sets must be Points, is {prim.GetTypeName() or 'typeless'}")
        return
    pts = UsdGeom.Points(prim)
    ids_attr, pts_attr = pts.GetIdsAttr(), pts.GetPointsAttr()
    if not ids_attr.HasAuthoredValueOpinion() or not pts_attr.HasAuthoredValueOpinion():
        p.append(f"{where}: needs ids and points")
        return
    ok = exactly(ids_attr, f"{where}.ids") & exactly(pts_attr, f"{where}.points")
    pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
    color_attr = pv.GetAttr() if pv else None
    widths_attr = pts.GetWidthsAttr()
    color_vertex = _interp_check(
        color_attr, pv.GetInterpolation() if pv else None, f"{where}.primvars:displayColor", exactly, p
    )
    width_vertex = _interp_check(
        widths_attr if widths_attr.HasAuthoredValueOpinion() else None,
        pts.GetWidthsInterpolation(),
        f"{where}.widths",
        exactly,
        p,
    )
    shape = prim.GetAttribute(SHAPE_ATTR)
    if shape and shape.Get() not in SHAPES:
        p.append(f"{where}.{SHAPE_ATTR}: must be one of {SHAPES}")
    if not ok:
        return
    for t in times:
        ids = ids_attr.Get(t)
        n = len(ids) if ids is not None else 0
        npts = len(pts_attr.Get(t) or [])
        if n != npts:
            p.append(f"{where}@{t:g}: {n} ids but {npts} points")
            return
        if n and len(set(ids)) != n:
            p.append(f"{where}.ids@{t:g}: ids must be unique within a sample")
            return
        if color_vertex and len(color_attr.Get(t) or []) != n:
            p.append(f"{where}.primvars:displayColor@{t:g}: needs one colour per point")
            return
        if width_vertex and len(widths_attr.Get(t) or []) != n:
            p.append(f"{where}.widths@{t:g}: needs one width per point")
            return


def _interp_check(attr, interp, where: str, exactly, p: list[str]) -> bool:
    """True when ``attr`` is per-point (vertex) and sampled correctly."""
    if attr is None or not attr.HasAuthoredValueOpinion():
        return False
    if interp == UsdGeom.Tokens.constant:
        if attr.GetNumTimeSamples():
            p.append(f"{where}: a constant value must not be time-sampled")
        elif len(attr.Get() or []) != 1:
            p.append(f"{where}: a constant value has exactly one element")
        return False
    if interp == UsdGeom.Tokens.vertex:
        if attr.Get() is not None and not attr.GetNumTimeSamples():
            p.append(f"{where}: per-point values must be time-sampled")
            return False
        return exactly(attr, where)
    p.append(f"{where}: interpolation must be constant or vertex, is {interp}")
    return False


def _check_field(prim: Usd.Prim, times: list[float], subset, p: list[str]) -> None:
    where = str(prim.GetPath())
    if prim.GetTypeName() != "Mesh":
        p.append(f"{where}: fields must be Mesh, is {prim.GetTypeName() or 'typeless'}")
        return
    size = prim.GetAttribute(FIELD_SIZE)
    origin = prim.GetAttribute(FIELD_ORIGIN)
    cell = prim.GetAttribute(FIELD_CELL)
    if not size or size.GetTypeName() != Sdf.ValueTypeNames.Int2:
        p.append(f"{where}.{FIELD_SIZE}: missing or not int2")
        return
    if not origin or origin.GetTypeName() != Sdf.ValueTypeNames.Double2:
        p.append(f"{where}.{FIELD_ORIGIN}: missing or not double2")
    if not cell or cell.GetTypeName() != Sdf.ValueTypeNames.Double or not (cell.Get() or 0) > 0:
        p.append(f"{where}.{FIELD_CELL}: missing, not double, or not positive")
    cols, rows = size.Get()
    if cols < 1 or rows < 1:
        p.append(f"{where}.{FIELD_SIZE}: must be at least 1 x 1")
        return
    mesh = UsdGeom.Mesh(prim)
    for attr in (mesh.GetPointsAttr(), mesh.GetFaceVertexCountsAttr(), mesh.GetFaceVertexIndicesAttr()):
        if attr.GetNumTimeSamples():
            p.append(f"{attr.GetPath()}: field topology is static")
    if len(mesh.GetPointsAttr().Get() or []) != (cols + 1) * (rows + 1):
        p.append(f"{where}.points: needs (cols+1)*(rows+1) = {(cols + 1) * (rows + 1)} points")
    if len(mesh.GetFaceVertexCountsAttr().Get() or []) != cols * rows:
        p.append(f"{where}.faceVertexCounts: needs cols*rows = {cols * rows} faces")
    pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
    if not pv or pv.GetInterpolation() != UsdGeom.Tokens.uniform:
        p.append(f"{where}.primvars:displayColor: missing or not uniform")
        return
    ts = subset(pv.GetAttr(), f"{where}.primvars:displayColor")
    if not ts or ts[0] != times[0]:
        p.append(f"{where}.primvars:displayColor: must be sampled at the first sample tick")
    for t in ts:
        if len(pv.Get(t) or []) != cols * rows:
            p.append(f"{where}.primvars:displayColor@{t:g}: needs cols*rows = {cols * rows} colours")
            return


def _check_static_points(prim: Usd.Prim, p: list[str]) -> None:
    where = str(prim.GetPath())
    pts = UsdGeom.Points(prim)
    n = len(pts.GetPointsAttr().Get() or [])
    pv = UsdGeom.PrimvarsAPI(prim).GetPrimvar("displayColor")
    for attr, interp, label in (
        (pv.GetAttr() if pv else None, pv.GetInterpolation() if pv else None, "primvars:displayColor"),
        (pts.GetWidthsAttr(), pts.GetWidthsInterpolation(), "widths"),
    ):
        if attr is None or not attr.HasAuthoredValueOpinion():
            continue
        k = len(attr.Get() or [])
        if interp == UsdGeom.Tokens.constant and k != 1:
            p.append(f"{where}.{label}: a constant value has exactly one element")
        elif interp == UsdGeom.Tokens.vertex and k != n:
            p.append(f"{where}.{label}: needs one value per point")


# --------------------------------------------------------------------------------------- info --


def replay_info(path: str | Path) -> dict[str, Any]:
    """A plain-data summary (for ``usd-recorder replay info``)."""
    r = read_replay(path)
    return {
        "path": str(r.path),
        "bytes": r.path.stat().st_size,
        "meta": r.meta,
        "playbackRate": r.playback_rate,
        "samples": len(r.ticks),
        "ticks": [r.ticks[0], r.ticks[-1]],
        "firstHash": r.hashes[0],
        "lastHash": r.hashes[-1],
        "sets": {s.name: {"label": s.label, "maxPoints": max((len(i) for i in s.ids), default=0)} for s in r.sets},
        "fields": {f.name: {"size": [f.cols, f.rows], "samples": len(f.ticks)} for f in r.fields},
        "stats": sorted(r.stats),
        "scene": {s.name: len(s.points) for s in r.scene},
    }


def convert(src: str | Path, dst: str | Path) -> Path:
    """Re-export a replay layer as ``.usda`` or ``.usdc`` (by ``dst``'s extension), atomically."""
    layer = Sdf.Layer.FindOrOpen(str(src))
    if layer is None:
        raise OSError(f"cannot open {src}")
    return atomic_export(layer, dst)


__all__ = [
    "SCHEMA",
    "EntitySet",
    "Field",
    "Replay",
    "ReplayWriter",
    "ScenePoints",
    "convert",
    "linear_to_srgb8",
    "read_replay",
    "replay_info",
    "srgb_to_linear",
    "validate_replay",
]
