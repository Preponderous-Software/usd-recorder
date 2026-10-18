"""A plain-data summary of a recorded stage (what ``usd-recorder inspect`` prints)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pxr import Usd, UsdGeom

from .clips import clip_prims


def _plain(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def inspect_stage(path: str | Path) -> dict[str, Any]:
    """Time range, rate, up axis, clip layout, prim and sample counts of a stage.

    ``prims`` counts every prim; ``animated`` counts attributes with more than one time sample
    and ``samples`` their samples (resolved through value clips).
    """
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"no such file: {path}")
    stage = Usd.Stage.Open(str(path))
    if stage is None:
        raise ValueError(f"cannot open {path}")
    root = stage.GetRootLayer()
    clips: list[dict[str, Any]] = []
    for prim_path in clip_prims(root):
        info = root.GetPrimAtPath(prim_path).GetInfo("clips")
        for clip_set, data in dict(info).items():
            assets = [a.path for a in data.get("assetPaths", [])]
            clips.append(
                {
                    "prim": str(prim_path),
                    "set": clip_set,
                    "count": len(assets),
                    "assets": assets,
                    "active": [list(map(float, a)) for a in data.get("active", [])],
                }
            )
    prims = animated = samples = 0
    for prim in stage.TraverseAll():
        prims += 1
        for attr in prim.GetAttributes():
            n = attr.GetNumTimeSamples()
            if n > 1:
                animated += 1
                samples += n
    default = stage.GetDefaultPrim()
    return {
        "path": str(path),
        "format": "usdc" if path.read_bytes()[:8] == b"PXR-USDC" else "usda",
        "defaultPrim": str(default.GetPath()) if default else None,
        "upAxis": str(UsdGeom.GetStageUpAxis(stage)),
        "metersPerUnit": UsdGeom.GetStageMetersPerUnit(stage),
        "timeCodesPerSecond": stage.GetTimeCodesPerSecond(),
        "startTimeCode": stage.GetStartTimeCode(),
        "endTimeCode": stage.GetEndTimeCode(),
        "layers": [Path(layer.identifier).name for layer in stage.GetUsedLayers() if not layer.anonymous],
        "clips": clips,
        "prims": prims,
        "animatedAttributes": animated,
        "timeSamples": samples,
        "customLayerData": _plain(dict(root.customLayerData)),
        "compositionErrors": [str(e) for e in stage.GetCompositionErrors()],
    }


def format_summary(summary: dict[str, Any]) -> str:
    lines = [
        f"{summary['path']} ({summary['format']})",
        f"  default prim     {summary['defaultPrim']}",
        f"  up axis          {summary['upAxis']}, {summary['metersPerUnit']:g} m per unit",
        f"  time codes       {summary['startTimeCode']:g} .. {summary['endTimeCode']:g}"
        f" at {summary['timeCodesPerSecond']:g} per second",
        f"  prims            {summary['prims']}",
        f"  animated attrs   {summary['animatedAttributes']} ({summary['timeSamples']} samples)",
    ]
    for clip in summary["clips"]:
        lines.append(f"  value clips      {clip['count']} on {clip['prim']} ({clip['set']})")
    for key, value in sorted(summary["customLayerData"].items()):
        lines.append(f"  {key:<16} {value}")
    for error in summary["compositionErrors"]:
        lines.append(f"  ERROR            {error}")
    return "\n".join(lines)


__all__ = ["format_summary", "inspect_stage"]
