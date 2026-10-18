"""Chunk layers stitched with USD value clips, manifest defaults, and ``flatten``.

A long recording is written as fixed-size chunk layers, each holding only its own range of time
samples, then stitched with ``UsdUtils.StitchClips`` into a root layer plus a *topology* layer
(every prim and attribute declared in any chunk) and a *manifest* layer (every attribute that
has time samples). Two traps, both encoded as tests in ``tests/test_clip_traps.py``:

1. **Every chunk must author its own ``startTimeCode``/``endTimeCode``.** ``StitchClips``
   derives each clip's ``active`` and ``times`` metadata from them. Without them every clip is
   stitched as active at time 0 and the stage resolves to a single frame.
2. **Values a clip has no samples for come from the manifest's defaults.** An entity first seen
   in chunk 3 has no ``visibility`` samples in chunks 1-2; unless the *manifest* carries
   ``visibility = "invisible"`` as a default, it resolves to the schema fallback ``inherited``
   (visible, at the origin) before it existed. The same default authored in the topology layer,
   or in the clip layers, is ignored during clip value resolution.

See ARCHITECTURE.md § Chunked recordings.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from pxr import Sdf, Usd, UsdUtils

from .atomic import atomic_export, atomic_write_text, layer_format


def chunk_name(stem: str, index: int, binary: bool = False) -> str:
    """File name of chunk ``index`` of the recording whose root layer is ``<stem>.usda``."""
    return f"{stem}.chunk_{index:04d}.{'usdc' if binary else 'usda'}"


def topology_path(root_path: str | Path) -> Path:
    return Path(UsdUtils.GenerateClipTopologyName(str(root_path)))


def manifest_path(root_path: str | Path) -> Path:
    return Path(UsdUtils.GenerateClipManifestName(str(root_path)))


def stitch(
    root_path: str | Path,
    chunk_paths: Iterable[str | Path],
    clip_prim: str,
    *,
    topology: Callable[[Usd.Stage], None] | None = None,
    manifest: Callable[[Sdf.Layer], None] | None = None,
    root: Callable[[Usd.Stage], None] | None = None,
) -> Path:
    """Stitch ``chunk_paths`` into ``root_path`` (plus its topology and manifest layers).

    ``clip_prim`` is the prim the clips are anchored on (usually the default prim). The hooks
    run on the stitched layers before they are published: ``topology`` gets the topology layer
    as a stage (author the static scene there, not in every chunk), ``manifest`` gets the
    manifest layer (author clip-resolution defaults there, see :func:`set_manifest_defaults`),
    and ``root`` gets the composed root stage (author stage metadata there).

    The three layers are built under temporary names in the same directory, then each is moved
    over its final name (topology, manifest, root last), so a reader polling ``root_path`` never
    sees a stitched root whose topology or manifest is missing or half-written. Chunk asset
    paths are recorded relative to the root layer, so the directory stays relocatable.
    """
    root_path = Path(root_path)
    if layer_format(root_path) != "usda":
        raise ValueError("the stitched root layer must be a .usda file")
    chunks = [str(Path(p).resolve()) for p in chunk_paths]  # StitchClips must open them
    if not chunks:
        raise ValueError("nothing to stitch: no chunk layers")
    stem = root_path.name[: -len(root_path.suffix)]
    staging_stem = f".{stem}.staging-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    staging_root = root_path.with_name(f"{staging_stem}{root_path.suffix}")
    staged = [staging_root, topology_path(staging_root), manifest_path(staging_root)]
    try:
        layer = Sdf.Layer.CreateNew(str(staging_root))
        if not UsdUtils.StitchClips(layer, chunks, Sdf.Path(clip_prim)):
            raise OSError(f"UsdUtils.StitchClips failed for {root_path}")
        layer.Save()

        if topology is not None:
            topo = Usd.Stage.Open(str(staged[1]))
            topology(topo)
            topo.GetRootLayer().Save()
        if manifest is not None:
            man = Sdf.Layer.FindOrOpen(str(staged[2]))
            manifest(man)
            man.Save()
        if root is not None:
            stage = Usd.Stage.Open(str(staging_root))
            root(stage)
            stage.GetRootLayer().Save()

        finals = [topology_path(root_path), manifest_path(root_path), root_path]
        for src, dst in zip(staged[1:] + staged[:1], finals, strict=True):
            text = src.read_text(encoding="utf-8").replace(f"{staging_stem}.", f"{stem}.")
            atomic_write_text(dst, text)
    finally:
        for path in staged:
            path.unlink(missing_ok=True)
    return root_path


def set_manifest_defaults(manifest: Sdf.Layer, defaults: Mapping[str, Any]) -> int:
    """Author default values on manifest attributes: ``{"/World/a/b.visibility": value}``.

    An attribute missing from the manifest (it has no time samples in any chunk) is skipped.
    Returns how many defaults were authored.
    """
    count = 0
    for path, value in defaults.items():
        spec = manifest.GetAttributeAtPath(Sdf.Path(path))
        if spec is None:
            continue
        spec.default = value
        count += 1
    return count


def default_invisible(manifest: Sdf.Layer, parent: str) -> int:
    """``visibility = invisible`` as the manifest default of every child prim of ``parent``.

    This is the late-arrival fix: an entity whose chunk has no samples for it resolves to
    invisible instead of the schema fallback ``inherited``. The attribute spec is created if a
    child has none. Returns how many children were patched.
    """
    spec = manifest.GetPrimAtPath(parent)
    if spec is None:
        return 0
    count = 0
    for child in spec.nameChildren:
        attr = child.attributes.get("visibility")
        if attr is None:
            attr = Sdf.AttributeSpec(child, "visibility", Sdf.ValueTypeNames.Token, Sdf.VariabilityVarying)
        attr.default = "invisible"
        count += 1
    return count


# -- flatten -----------------------------------------------------------------------------------


def default_flat_path(src: str | Path, binary: bool = False) -> Path:
    src = Path(src)
    stem = src.name[: -len(src.suffix)] if src.suffix else src.name
    return src.parent / f"{stem}_flat.{'usdc' if binary else 'usda'}"


def flatten(src: str | Path, out: str | Path | None = None, *, binary: bool = False) -> Path:
    """Bake ``src`` (value clips and all) into one layer with every time sample resolved.

    Writes ``<stem>_flat.usda`` next to ``src`` (``.usdc`` with ``binary``) unless ``out`` is
    given; an ``out`` whose extension disagrees with ``binary`` raises ``ValueError``. The write
    is atomic.
    """
    src = Path(src)
    out = Path(out) if out is not None else default_flat_path(src, binary)
    if (layer_format(out) == "usdc") != binary:
        flag = "with" if binary else "without"
        raise ValueError(f"output {out.name!r} disagrees with --binary (given {flag} --binary)")
    if not src.is_file():
        raise ValueError(f"no such file: {src}")
    stage = Usd.Stage.Open(str(src))
    if stage is None:
        raise ValueError(f"cannot open {src}")
    return atomic_export(stage.Flatten(), out)


def clip_prims(layer: Sdf.Layer) -> list[Sdf.Path]:
    """Every prim spec in ``layer`` that carries value-clip metadata."""
    found: list[Sdf.Path] = []

    def visit(path: Sdf.Path) -> None:
        if path.IsPrimPath():
            spec = layer.GetPrimAtPath(path)
            if spec is not None and spec.HasInfo("clips"):
                found.append(path)

    layer.Traverse(Sdf.Path.absoluteRootPath, visit)
    return found


def has_clips(layer: Sdf.Layer) -> bool:
    """Whether any prim spec in ``layer`` carries value-clip metadata."""
    return bool(clip_prims(layer))


__all__ = [
    "chunk_name",
    "clip_prims",
    "default_flat_path",
    "default_invisible",
    "flatten",
    "has_clips",
    "manifest_path",
    "set_manifest_defaults",
    "stitch",
    "topology_path",
]
