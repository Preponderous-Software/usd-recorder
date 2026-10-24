"""The flight recorder: one ``record(tick, ...)`` call per simulation tick.

A recording is either one layer (``chunk_size=None``) or fixed-size chunk layers stitched with
value clips into a ``.usda`` root on :meth:`Recorder.close`. Either way the composed stage
resolves to the same values at every recorded tick (``tests/test_roundtrip.py`` checks this for
random recordings). See ARCHITECTURE.md for the stage layout and the sampling rules.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pxr import Gf, Sdf, Usd, UsdGeom, Vt

from . import __version__
from .atomic import atomic_export, layer_format
from .clips import chunk_name, default_invisible, set_manifest_defaults, stitch
from .track import Track
from .values import (
    SDF_TYPES,
    TypeChangeError,
    is_finite_vec3,
    is_valid_property_name,
    normalize_color,
    to_color_array,
    to_vec3d,
    value_type,
)

EntityId = int | str
Color = tuple[float, float, float]

POSITION, COLOR = "position", "color"
RESERVED = frozenset({POSITION, COLOR})
VISIBLE, INVISIBLE = UsdGeom.Tokens.inherited, UsdGeom.Tokens.invisible
DEFAULT_COLOR: Color = (0.6, 0.6, 0.6)
GEOMETRY = ("cube", "sphere", None)
_INVALID = re.compile(r"[^A-Za-z0-9_]")


def default_entity_name(entity_id: EntityId) -> str:
    """Prim name of an entity: ``e_<id>`` for an int (``e_n<id>`` when negative); for a string,
    the string with characters outside ``[A-Za-z0-9_]`` replaced by ``_`` (and a leading ``_``
    if it starts with a digit or is empty)."""
    if isinstance(entity_id, bool) or not isinstance(entity_id, (int, str)):
        raise TypeError(f"entity ids must be int or str, got {entity_id!r}")
    if isinstance(entity_id, int):
        return f"e_n{-entity_id}" if entity_id < 0 else f"e_{entity_id}"
    name = _INVALID.sub("_", entity_id)
    return name if name and not name[0].isdigit() else f"_{name}"


def _join_events(value: str | Iterable[str]) -> str:
    if isinstance(value, str):
        return value
    items = list(value)
    if not all(isinstance(v, str) for v in items):
        raise TypeError(f"an event must be a string or a list of strings, got {value!r}")
    return "\n".join(items)


@dataclass
class _Known:
    """What the recorder knows about an entity across every chunk."""

    name: str
    types: dict[str, str] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)
    color: Color | None = None
    position: tuple[float, float, float] | None = None


@dataclass
class _Live:
    """An entity's prim and tracks in the layer currently being written."""

    prim: Usd.Prim
    visibility: Track
    position: Track | None = None
    color: Track | None = None
    attrs: dict[str, Track] = field(default_factory=dict)

    def tracks(self) -> Iterable[Track]:
        yield self.visibility
        if self.position is not None:
            yield self.position
        if self.color is not None:
            yield self.color
        yield from self.attrs.values()


class _Layer:
    """One layer being written: the whole recording, or one chunk."""

    def __init__(self, path: Path):
        self.path = path
        self.stage = Usd.Stage.CreateInMemory()
        self.first: float | None = None
        self.last: float | None = None
        self.entities: dict[EntityId, _Live] = {}
        self.extras: dict[str, Track] = {}
        self.events: dict[str, Track] = {}

    def tracks(self) -> Iterable[Track]:
        for live in self.entities.values():
            yield from live.tracks()
        yield from self.extras.values()
        yield from self.events.values()


class Recorder:
    """Record a simulation tick by tick as a time-sampled OpenUSD stage.

    ``path`` is the layer to write. Unchunked, it is ``.usda`` (or ``.usdc`` with ``binary``).
    With ``chunk_size``, it is the ``.usda`` root that the chunk layers
    (``<stem>.chunk_NNNN.usda``, ``.usdc`` with ``binary``, next to it) are stitched into.
    One tick is one time code; ``time_codes_per_second`` sets the playback rate.

    Per tick, :meth:`record` takes the entities present at that tick, stage-level ``extras`` and
    ``events``. An entity that is present is visible; one that is absent is invisible, before it
    first appears too (also across chunks). Its values hold while it is absent, so they do not
    interpolate towards wherever it reappears.

    Options: ``root`` (default prim path), ``entities`` (scope name under the root),
    ``up_axis`` (``"Z"`` or ``"Y"``), ``meters_per_unit``, ``geometry`` (``"cube"``,
    ``"sphere"`` or ``None``) and ``entity_size`` for the entity's ``geom`` child,
    ``default_color`` (a colour, or a function of the entity id), ``entity_name`` (entity id →
    prim name), ``namespace`` for typed values (``extras``), ``events_namespace`` for event
    channels (``events``), ``scene`` (a function that authors
    the static part of the scene; it runs once, in the topology layer when chunked),
    ``metadata`` (``customLayerData`` of the root layer) and ``checkpoint_every`` (publish the
    recording so far, atomically, every N ticks: see :meth:`checkpoint`).
    """

    def __init__(
        self,
        path: str | Path,
        time_codes_per_second: float = 24.0,
        chunk_size: int | None = None,
        binary: bool = False,
        *,
        root: str = "/World",
        entities: str = "Entities",
        up_axis: str = "Z",
        meters_per_unit: float = 1.0,
        geometry: str | None = "cube",
        entity_size: float = 1.0,
        default_color: Color | Callable[[EntityId], Color] = DEFAULT_COLOR,
        entity_name: Callable[[EntityId], str] = default_entity_name,
        namespace: str = "extras",
        events_namespace: str = "events",
        scene: Callable[[Usd.Stage], None] | None = None,
        metadata: Mapping[str, Any] | None = None,
        checkpoint_every: int | None = None,
    ):
        if not math.isfinite(time_codes_per_second) or time_codes_per_second <= 0:
            raise ValueError("time_codes_per_second must be a finite positive number")
        if chunk_size is not None and (isinstance(chunk_size, bool) or chunk_size <= 0):
            raise ValueError("chunk_size must be a positive number of ticks, or None")
        if checkpoint_every is not None and checkpoint_every <= 0:
            raise ValueError("checkpoint_every must be positive, or None")
        if up_axis not in ("Y", "Z"):
            raise ValueError("up_axis must be 'Y' or 'Z'")
        if geometry not in GEOMETRY:
            raise ValueError(f"geometry must be one of {GEOMETRY}")
        for ns in (namespace, events_namespace):
            if not is_valid_property_name(ns):
                raise ValueError(f"{ns!r} is not a valid property namespace")
        self.path = Path(path)
        fmt = layer_format(self.path)
        if chunk_size is None and (fmt == "usdc") != binary:
            raise ValueError(f"{self.path.name!r} disagrees with binary={binary}")
        if chunk_size is not None and fmt != "usda":
            raise ValueError("a chunked recording's root layer must be a .usda file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.time_codes_per_second = float(time_codes_per_second)
        self.chunk_size = chunk_size
        self.binary = binary
        self.root = Sdf.Path(root)
        if not self.root.IsAbsolutePath() or not self.root.IsPrimPath():
            raise ValueError(f"root must be an absolute prim path, got {root!r}")
        self.scope = self.root.AppendChild(entities)
        self.up_axis = up_axis
        self.meters_per_unit = float(meters_per_unit)
        self.geometry = geometry
        self.entity_size = float(entity_size)
        self._default_color = default_color
        self._entity_name = entity_name
        self.namespace = namespace
        self.events_namespace = events_namespace
        self.scene = scene
        self.metadata: dict[str, Any] = {"usdRecorder:version": __version__}
        self.metadata.update(metadata or {})
        self.checkpoint_every = checkpoint_every

        self.first_tick: float | None = None
        self.last_tick: float | None = None
        self.ticks = 0
        self.chunk_paths: list[Path] = []
        self.closed = False
        self._known: dict[EntityId, _Known] = {}
        self._names: dict[str, EntityId] = {}
        self._extra_types: dict[str, str] = {}
        self._extra_values: dict[str, Any] = {}
        self._channels: set[str] = set()
        self._layer: _Layer | None = None
        self._chunk_index: int | None = None

    # -- public ---------------------------------------------------------------------------

    @property
    def chunked(self) -> bool:
        return self.chunk_size is not None

    def entity_ids(self) -> list[EntityId]:
        return list(self._known)

    def entity_path(self, entity_id: EntityId) -> Sdf.Path:
        known = self._known.get(entity_id)
        return self.scope.AppendChild(known.name if known else self._entity_name(entity_id))

    def record(
        self,
        tick: float,
        entities: Mapping[EntityId, Mapping[str, Any]] | None = None,
        extras: Mapping[str, Any] | None = None,
        events: Mapping[str, str | Iterable[str]] | None = None,
    ) -> None:
        """Record one tick.

        ``entities`` maps each entity present at ``tick`` to its values: ``position`` (three
        numbers), ``color`` (three numbers in [0, 1], on the entity's geometry), and any other
        key as a typed value (bool, int, double, string or double3) under the namespace. A key
        not given keeps its last value. ``extras`` are typed values of the stage itself.
        ``events`` maps a channel name to what happened on it this tick (a string, or a list of
        strings joined with newlines); a channel reads ``""`` on every tick it has no event.

        Ticks must increase. Every input is validated before anything is written, so a call
        that raises leaves the recording unchanged.
        """
        if self.closed:
            raise RuntimeError("recorder is closed")
        if isinstance(tick, bool) or not isinstance(tick, (int, float)) or not math.isfinite(tick):
            raise ValueError(f"tick must be a finite number, got {tick!r}")
        if self.last_tick is not None and tick <= self.last_tick:
            raise ValueError(f"tick {tick} is not after the previous tick {self.last_tick}")
        entities = entities or {}
        extras = dict(extras or {})
        events = {k: _join_events(v) for k, v in (events or {}).items()}
        new_names = self._validate(entities, extras, events)

        for entity_id, name in new_names.items():
            self._known[entity_id] = _Known(name)
            self._names[name] = entity_id
        if self.first_tick is None:
            self.first_tick = tick
        layer = self._layer_for(tick)
        previous = layer.last
        if layer.first is None:
            layer.first = tick
        layer.last = tick
        self.last_tick = tick
        self.ticks += 1

        for entity_id, values in entities.items():
            self._record_present(layer, entity_id, values, tick, previous)
        for entity_id, live in layer.entities.items():
            if entity_id not in entities:
                live.visibility.observe(tick, INVISIBLE)
                for track in live.tracks():
                    if track is not live.visibility:
                        track.hold(tick)
        self._record_extras(layer, extras, tick)
        self._record_events(layer, events, tick, previous)

        if self.checkpoint_every and self.ticks % self.checkpoint_every == 0:
            self.checkpoint()

    def checkpoint(self) -> Path:
        """Publish everything recorded so far, atomically, so a follower (a viewer polling the
        file, ``usd-recorder inspect``) sees a complete stage. Pending hold keys are written
        first, so the published values hold through the last recorded tick."""
        if self.closed:
            raise RuntimeError("recorder is closed")
        if self._layer is None:
            raise RuntimeError("nothing recorded yet")
        self._publish_layer(self._layer)
        if self.chunked:
            self._stitch()
        return self.path

    def close(self) -> Path | None:
        """Finish the recording and return the root layer's path (``None`` if no tick was
        recorded: nothing is written)."""
        if self.closed:
            return self.path if self.ticks else None
        self.closed = True
        if self._layer is None:
            return None
        self._publish_layer(self._layer)
        if self.chunked:
            self._stitch()
        self._layer = None
        return self.path

    def __enter__(self) -> Recorder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- validation -----------------------------------------------------------------------

    def _validate(self, entities, extras, events) -> dict[EntityId, str]:
        new_names: dict[EntityId, str] = {}
        for entity_id, values in entities.items():
            known = self._known.get(entity_id)
            if known is None:
                name = self._entity_name(entity_id)
                if not isinstance(name, str) or not Sdf.Path.IsValidIdentifier(name):
                    raise ValueError(f"entity {entity_id!r}: {name!r} is not a valid prim name")
                other = self._names.get(name)
                if other is None:
                    other = next((e for e, n in new_names.items() if n == name), None)
                if other is not None and other != entity_id:
                    raise ValueError(f"entities {other!r} and {entity_id!r} both map to prim name {name!r}")
                new_names[entity_id] = name
            types = known.types if known else {}
            if not isinstance(values, Mapping):
                raise TypeError(f"entity {entity_id!r}: values must be a mapping, got {values!r}")
            for key, value in values.items():
                if key == POSITION:
                    if not is_finite_vec3(value):
                        raise ValueError(f"entity {entity_id!r}: position must be three finite numbers, got {value!r}")
                elif key == COLOR:
                    if self.geometry is None:
                        raise ValueError(f"entity {entity_id!r}: a colour needs geometry (geometry=None)")
                    normalize_color(value)
                else:
                    self._check_typed(f"entity {entity_id!r}", key, value, types)
        for key, value in extras.items():
            self._check_typed("extras", key, value, self._extra_types)
        for key in events:
            if not is_valid_property_name(key):
                raise ValueError(f"event channel {key!r} is not a valid USD property name")
        return new_names

    @staticmethod
    def _check_typed(owner: str, key: str, value: Any, types: Mapping[str, str]) -> None:
        if not is_valid_property_name(key):
            raise ValueError(f"{owner}: key {key!r} is not a valid USD property name")
        type_name = value_type(value)
        if key in types and types[key] != type_name:
            raise TypeChangeError(f"{owner}: key {key!r} changed type from {types[key]} to {type_name}")

    # -- layers -----------------------------------------------------------------------------

    def _layer_for(self, tick: float) -> _Layer:
        if not self.chunked:
            if self._layer is None:
                self._layer = self._new_layer(self.path, static_scene=True)
            return self._layer
        assert self.first_tick is not None and self.chunk_size is not None
        index = int((tick - self.first_tick) // self.chunk_size)
        if self._layer is not None and index == self._chunk_index:
            return self._layer
        if self._layer is not None:
            self._publish_layer(self._layer)
        path = self.path.with_name(chunk_name(self.path.stem, len(self.chunk_paths), self.binary))
        self.chunk_paths.append(path)
        self._layer = self._new_layer(path, static_scene=False)
        self._chunk_index = index
        return self._layer

    def _configure(self, stage: Usd.Stage) -> None:
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z if self.up_axis == "Z" else UsdGeom.Tokens.y)
        UsdGeom.SetStageMetersPerUnit(stage, self.meters_per_unit)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, self.root).GetPrim())
        stage.SetTimeCodesPerSecond(self.time_codes_per_second)
        stage.SetFramesPerSecond(self.time_codes_per_second)

    def _new_layer(self, path: Path, static_scene: bool) -> _Layer:
        layer = _Layer(path)
        self._configure(layer.stage)
        UsdGeom.Scope.Define(layer.stage, self.scope)
        if static_scene and self.scene is not None:
            self.scene(layer.stage)
        return layer

    def _publish_layer(self, layer: _Layer) -> None:
        for track in layer.tracks():
            track.flush()
        if layer.first is not None:
            layer.stage.SetStartTimeCode(layer.first)  # trap 1: every chunk needs its own range
            layer.stage.SetEndTimeCode(layer.last)
        if not self.chunked:
            root = layer.stage.GetRootLayer()
            root.customLayerData = {**dict(root.customLayerData), **self.metadata}
        atomic_export(layer.stage.GetRootLayer(), layer.path)

    def _stitch(self) -> None:
        def topology(stage: Usd.Stage) -> None:
            if self.scene is not None:
                self.scene(stage)

        def manifest(man: Sdf.Layer) -> None:
            default_invisible(man, str(self.scope))  # trap 2: late arrivals stay hidden
            defaults: dict[str, Any] = {}
            for entity_id, known in self._known.items():
                geom = self.scope.AppendChild(known.name).AppendChild("geom")
                defaults[f"{geom}.primvars:displayColor"] = to_color_array(self._color_of(entity_id))
            for channel in self._channels:
                defaults[f"{self.root}.{self.events_namespace}:{channel}"] = ""
            set_manifest_defaults(man, defaults)

        def root(stage: Usd.Stage) -> None:
            self._configure(stage)
            layer = stage.GetRootLayer()
            layer.customLayerData = {**dict(layer.customLayerData), **self.metadata}

        stitch(self.path, self.chunk_paths, str(self.root), topology=topology, manifest=manifest, root=root)

    # -- entities ---------------------------------------------------------------------------

    def _color_of(self, entity_id: EntityId) -> Color:
        c = self._default_color(entity_id) if callable(self._default_color) else self._default_color
        return normalize_color(c)

    def _define(self, layer: _Layer, entity_id: EntityId, tick: float, previous: float | None) -> _Live:
        known = self._known[entity_id]
        stage = layer.stage
        path = self.scope.AppendChild(known.name)
        xform = UsdGeom.Xform.Define(stage, path)
        prim = xform.GetPrim()
        id_type = Sdf.ValueTypeNames.Int if isinstance(entity_id, int) else Sdf.ValueTypeNames.String
        prim.CreateAttribute("recorder:id", id_type, custom=True).Set(entity_id)
        live = _Live(prim, Track(xform.CreateVisibilityAttr()))
        if previous is not None:
            # Not present at earlier ticks of this layer: hidden up to (and held before) now.
            live.visibility.observe(previous, INVISIBLE)
        if self.geometry is not None:
            schema = UsdGeom.Cube if self.geometry == "cube" else UsdGeom.Sphere
            gprim = schema.Define(stage, path.AppendChild("geom"))
            half = self.entity_size / 2
            if self.geometry == "cube":
                gprim.CreateSizeAttr(self.entity_size)
            else:
                gprim.CreateRadiusAttr(half)
            gprim.CreateExtentAttr(Vt.Vec3fArray([Gf.Vec3f(-half), Gf.Vec3f(half)]))
            gprim.CreateDisplayColorAttr(to_color_array(self._color_of(entity_id)))
        layer.entities[entity_id] = live
        # Carry state from earlier layers: values (and the driver colour) re-authored now.
        if known.position is not None:
            self._position_track(live).observe(tick, known.position)
        for key, value in known.values.items():
            self._attr_track(live, key, known.types[key]).observe(tick, value)
        if known.color is not None:
            self._color_track(layer, live, entity_id, None).observe(tick, known.color)
        return live

    def _position_track(self, live: _Live) -> Track:
        if live.position is None:
            op = UsdGeom.Xform(live.prim).AddTranslateOp()
            live.position = Track(op.GetAttr(), to_vec3d)
        return live.position

    def _attr_track(self, live: _Live, key: str, type_name: str) -> Track:
        track = live.attrs.get(key)
        if track is None:
            sdf_type, convert = SDF_TYPES[type_name]
            attr = live.prim.CreateAttribute(f"{self.namespace}:{key}", sdf_type, custom=True)
            track = live.attrs[key] = Track(attr, convert)
        return track

    def _color_track(self, layer: _Layer, live: _Live, entity_id: EntityId, previous: float | None) -> Track:
        if live.color is None:
            geom = UsdGeom.Gprim(layer.stage.GetPrimAtPath(live.prim.GetPath().AppendChild("geom")))
            live.color = Track(geom.GetDisplayColorAttr(), to_color_array)
            if previous is not None:
                # The default colour applies until the first given colour; without this sample
                # the first given colour would hold backwards to the start of the layer.
                live.color.observe(previous, self._color_of(entity_id))
        return live.color

    def _record_present(self, layer, entity_id, values, tick, previous) -> None:
        known = self._known[entity_id]
        live = layer.entities.get(entity_id)
        if live is None:
            live = self._define(layer, entity_id, tick, previous)
        live.visibility.observe(tick, VISIBLE)
        given: set[str] = set()
        for key, value in values.items():
            if key == POSITION:
                position = (float(value[0]), float(value[1]), float(value[2]))
                self._position_track(live).observe(tick, position)
                known.position = position
            elif key == COLOR:
                color = normalize_color(value)
                self._color_track(layer, live, entity_id, previous).observe(tick, color)
                known.color = color
            else:
                type_name = value_type(value)
                value = tuple(float(c) for c in value) if type_name == "double3" else value
                self._attr_track(live, key, type_name).observe(tick, value)
                known.types[key] = type_name
                known.values[key] = value
            given.add(key)
        if live.position is not None and POSITION not in given:
            live.position.hold(tick)
        if live.color is not None and COLOR not in given:
            live.color.hold(tick)
        for key, track in live.attrs.items():
            if key not in given:
                track.hold(tick)

    # -- stage-level extras and events ------------------------------------------------------------

    def _root_prim(self, layer: _Layer) -> Usd.Prim:
        return layer.stage.GetPrimAtPath(self.root)

    def _record_extras(self, layer: _Layer, extras: Mapping[str, Any], tick: float) -> None:
        for key, value in extras.items():
            type_name = value_type(value)
            self._extra_types[key] = type_name
            self._extra_values[key] = tuple(float(c) for c in value) if type_name == "double3" else value
        for key, type_name in self._extra_types.items():
            track = layer.extras.get(key)
            if track is None:
                sdf_type, convert = SDF_TYPES[type_name]
                attr = self._root_prim(layer).CreateAttribute(f"{self.namespace}:{key}", sdf_type, custom=True)
                track = layer.extras[key] = Track(attr, convert)
            if key in extras:
                track.observe(tick, self._extra_values[key])
            elif not track.has_value:
                track.observe(tick, self._extra_values[key])  # carried into a new chunk
            else:
                track.hold(tick)

    def _record_events(self, layer: _Layer, events: Mapping[str, str], tick: float, previous) -> None:
        self._channels.update(events)
        for channel in sorted(self._channels):
            track = layer.events.get(channel)
            if track is None:
                attr = self._root_prim(layer).CreateAttribute(
                    f"{self.events_namespace}:{channel}", Sdf.ValueTypeNames.String, custom=True
                )
                track = layer.events[channel] = Track(attr)
                if previous is not None:
                    track.observe(previous, "")  # a new channel reads "" before its first event
            track.observe(tick, events.get(channel, ""))


__all__ = ["COLOR", "DEFAULT_COLOR", "POSITION", "Recorder", "default_entity_name"]
