"""Render a recording to an animated GIF with headless Blender (the README animation).

    python examples/random_walkers.py out/run.usda --ticks 300 --chunk-size 100
    usd-recorder flatten out/run.usda
    python docs/render_replay.py out/run_flat.usda docs/images/replay.gif --blender /path/to/blender

Two halves in one file:

* **Outside Blender** (any Python with ``usd-core`` and Pillow): runs Blender in the background
  on this file, then stamps the tick, the ``/World`` ``extras:population`` value and the
  ``events:log`` text of each tick on its frame and assembles the GIF.
* **Inside Blender** (``blender -b -P render_replay.py -- --inner params.json``): opens the stage
  with Blender's bundled ``pxr`` and keyframes, per recorded tick, each entity's
  ``xformOp:translate``, its ``visibility`` and its ``primvars:displayColor``, then renders one PNG
  per tick with Cycles on the CPU. Blender's USD importer does not carry animated visibility or
  colour, which this recording relies on (walkers are born and die, and fade from green to red
  as their energy drops), so the samples are read directly instead.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STEP = 2  # render every second tick


def _inner(params_path: str) -> None:
    import bpy  # type: ignore[import-not-found]
    from pxr import Usd, UsdGeom  # Blender's bundled USD bindings

    p = json.loads(Path(params_path).read_text())
    stage = Usd.Stage.Open(p["stage"])
    start, end = int(stage.GetStartTimeCode()), int(stage.GetEndTimeCode())
    ticks = list(range(start, end + 1, STEP))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    def material(name, rgb):
        mat = bpy.data.materials.new(name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (*rgb, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.6
        return mat, bsdf

    # ground: the recorded /World/Ground mesh
    g = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Ground"))
    pts = [tuple(v) for v in g.GetPointsAttr().Get()]
    mesh = bpy.data.meshes.new("Ground")
    mesh.from_pydata(pts, [], [list(g.GetFaceVertexIndicesAttr().Get())])
    ground = bpy.data.objects.new("Ground", mesh)
    scene.collection.objects.link(ground)
    ground.data.materials.append(material("ground", tuple(g.GetDisplayColorAttr().Get()[0]))[0])

    entities = stage.GetPrimAtPath("/World/Entities")
    for prim in entities.GetChildren():
        xf = UsdGeom.Xformable(prim)
        translate = prim.GetAttribute("xformOp:translate")
        vis = UsdGeom.Imageable(prim).GetVisibilityAttr()
        geom = prim.GetChild("geom")
        size = UsdGeom.Cube(geom).GetSizeAttr().Get() if geom.IsA(UsdGeom.Cube) else 1.0
        color = UsdGeom.Gprim(geom).GetDisplayColorAttr()
        bpy.ops.mesh.primitive_cube_add(size=size)
        obj = bpy.context.active_object
        obj.name = prim.GetName()
        mat, bsdf = material(prim.GetName(), (1, 1, 1))
        obj.data.materials.append(mat)
        for t in ticks:
            frame = t - start + 1
            pos, c = translate.Get(t), color.Get(t)
            # before an entity's first tick nothing is authored yet; it is invisible there
            hidden = pos is None or vis.Get(t) == UsdGeom.Tokens.invisible
            if pos is not None:
                obj.location = tuple(pos)
            obj.keyframe_insert("location", frame=frame)
            obj.hide_render = hidden
            obj.keyframe_insert("hide_render", frame=frame)
            if c:
                bsdf.inputs["Base Color"].default_value = (*tuple(c[0]), 1.0)
            bsdf.inputs["Base Color"].keyframe_insert("default_value", frame=frame)
        for fc in (obj.animation_data.action.fcurves if hasattr(obj.animation_data.action, "fcurves") else []):
            for k in fc.keyframe_points:
                k.interpolation = "CONSTANT"

    # camera aimed at the middle of the ground, a sun, and a transparent film
    bpy.ops.object.empty_add(location=(sum(x for x, _, _ in pts) / len(pts), sum(y for _, y, _ in pts) / len(pts), 0))
    target = bpy.context.active_object
    bpy.ops.object.camera_add(location=(target.location.x, target.location.y - 17, 19))
    cam = bpy.context.active_object
    cam.data.lens = 30
    track = cam.constraints.new("TRACK_TO")
    track.target = target
    scene.camera = cam
    bpy.ops.object.light_add(type="SUN", location=(0, 0, 30))
    bpy.context.active_object.data.energy = 4.0
    bpy.context.active_object.rotation_euler = (0.6, 0.2, 0.5)
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.35
    scene.world = world
    scene.render.film_transparent = True
    scene.view_settings.view_transform = "Standard"

    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = p.get("samples", 16)
    scene.cycles.use_denoising = False
    scene.render.resolution_x, scene.render.resolution_y = p["size"], p["size"]
    scene.render.image_settings.file_format = "PNG"
    for i, t in enumerate(ticks):
        if p.get("limit") and i >= p["limit"]:
            break
        scene.frame_set(t - start + 1)
        scene.render.filepath = str(Path(p["frames"]) / f"t{t:05d}.png")
        bpy.ops.render.render(write_still=True)


def _outer(args) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from pxr import Usd

    frames = Path(tempfile.mkdtemp(prefix="replay-"))
    params = frames / "params.json"
    params.write_text(json.dumps({"stage": str(Path(args.stage).resolve()), "frames": str(frames),
                                  "size": args.size, "samples": args.samples, "limit": args.limit}))
    subprocess.run([args.blender, "-b", "--python-exit-code", "1", "--threads", str(args.threads), "-P", __file__, "--",
                    "--inner", str(params)], check=True, stdout=subprocess.DEVNULL)

    stage = Usd.Stage.Open(args.stage)
    world = stage.GetPrimAtPath("/World")
    pop, log = world.GetAttribute("extras:population"), world.GetAttribute("events:log")
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 15)
    last_event, out = "", []
    for png in sorted(frames.glob("t*.png")):
        t = int(png.stem[1:])
        # the event channel reads "" on ticks without an event; keep the latest one on screen
        for tt in range(max(0, t - STEP + 1), t + 1):
            last_event = log.Get(tt) or last_event
        rgba = Image.open(png).convert("RGBA")
        im = Image.new("RGB", rgba.size, (244, 245, 247))
        im.paste(rgba, mask=rgba)
        d = ImageDraw.Draw(im)
        d.rectangle((8, 8, 8 + 9 * 26, 52), fill=(30, 34, 40))
        d.text((16, 13), f"tick {t:3d}  population {pop.Get(t)}", font=font, fill=(235, 238, 242))
        d.text((16, 31), last_event[:26], font=font, fill=(150, 220, 160))
        out.append(im.quantize(colors=128))
    out[0].save(args.out, save_all=True, append_images=out[1:], duration=args.ms, loop=0, optimize=True)
    shutil.rmtree(frames)
    print(f"wrote {args.out} ({len(out)} frames)")


if __name__ == "__main__":
    if "--inner" in sys.argv:
        _inner(sys.argv[sys.argv.index("--inner") + 1])
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("stage")
        ap.add_argument("out")
        ap.add_argument("--blender", required=True)
        ap.add_argument("--size", type=int, default=480)
        ap.add_argument("--samples", type=int, default=16)
        ap.add_argument("--threads", type=int, default=4)
        ap.add_argument("--ms", type=int, default=80, help="milliseconds per frame")
        ap.add_argument("--limit", type=int, default=0, help="render only the first N frames")
        _outer(ap.parse_args())
