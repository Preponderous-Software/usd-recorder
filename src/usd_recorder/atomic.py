"""Atomic file writes: a reader never sees a half-written layer.

Every write goes to a temporary file in the destination's own directory (same filesystem) and
is moved over the destination with ``os.replace``, which is atomic on POSIX and Windows. A
follower that opens the path sees either the previous complete file or the new complete file.
See ARCHITECTURE.md § Live recordings.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pxr import Sdf

TEXT, BINARY = ".usda", ".usdc"


def _temp_next_to(path: Path, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=suffix)
    os.close(fd)
    return Path(name)


def reload_if_open(path: str | Path) -> None:
    """Make a layer this process already has open at ``path`` re-read the file just replaced
    (USD's layer registry would otherwise keep serving the old content)."""
    layer = Sdf.Layer.Find(str(path))
    if layer is not None:
        layer.Reload(force=True)


def atomic_write_bytes(path: str | Path, data: bytes) -> Path:
    """Write ``data`` to ``path`` atomically."""
    path = Path(path)
    tmp = _temp_next_to(path, ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    reload_if_open(path)
    return path


def atomic_write_text(path: str | Path, text: str) -> Path:
    return atomic_write_bytes(path, text.encode("utf-8"))


def layer_format(path: str | Path) -> str:
    """``usda`` or ``usdc`` from a path's extension (``ValueError`` for anything else)."""
    ext = Path(path).suffix.lower()
    if ext == TEXT:
        return "usda"
    if ext == BINARY:
        return "usdc"
    raise ValueError(f"a layer path must end in .usda or .usdc, got {Path(path).name!r}")


def atomic_export(layer: Sdf.Layer, path: str | Path) -> Path:
    """Export ``layer`` to ``path`` atomically, as text or binary by the path's extension.

    Relative asset paths in ``layer`` are written as they are, so the temporary file being in
    the same directory as ``path`` keeps them valid.
    """
    path = Path(path)
    fmt = layer_format(path)
    tmp = _temp_next_to(path, f".{fmt}")
    try:
        if not layer.Export(str(tmp), "", {"format": fmt}):
            raise OSError(f"failed to write {path}")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if Sdf.Layer.Find(str(path)) != layer:
        reload_if_open(path)
    return path


__all__ = ["atomic_export", "atomic_write_bytes", "atomic_write_text", "layer_format", "reload_if_open"]
