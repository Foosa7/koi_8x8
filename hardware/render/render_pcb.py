"""Blender script: import a pcb2blender .pcb3d file and render it with Cycles.

Run headless:

    blender -b -P render_pcb.py -- --pcb3d board.pcb3d --out out.png --view iso

See render.sh for the full pipeline (KiCad -> .pcb3d -> PNG).
"""

import argparse
import math
import sys
from pathlib import Path

import bpy
from mathutils import Vector

# Camera directions as (elevation, azimuth) in degrees. Elevation 90 = straight down.
VIEWS = {
    "top": (90.0, 0.0),
    "iso": (52.0, -38.0),
    "iso-low": (32.0, -38.0),
    "bottom": (-90.0, 0.0),
}


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--pcb3d", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--view", default="iso", choices=sorted(VIEWS))
    p.add_argument("--width", type=int, default=1600)
    p.add_argument("--height", type=int, default=1200)
    p.add_argument("--samples", type=int, default=256)
    p.add_argument("--margin", type=float, default=1.12, help="framing headroom, 1.0 = tight")
    p.add_argument("--texture-dpi", type=float, default=1524.0)
    p.add_argument("--transparent", action="store_true", help="alpha background, keep shadows")
    p.add_argument("--gpu", action="store_true", help="try OPTIX/CUDA (see enable_gpu caveat)")
    p.add_argument("--exposure", type=float, default=0.0, help="EV applied on top of the rig")
    return p.parse_args(argv)


ADDON = "bl_ext.user_default.pcb3d_importer"


def reset_scene() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    # factory settings drop enabled add-ons; re-enable ours for this session only
    bpy.ops.preferences.addon_enable(module=ADDON)


def import_pcb(pcb3d: Path, texture_dpi: float) -> list[bpy.types.Object]:
    bpy.ops.pcb2blender.import_pcb3d(
        filepath=str(pcb3d),
        pcb_material="RASTERIZED",
        texture_dpi=texture_dpi,
        add_solder_joints="SMART",
        center_boards=True,
        cut_boards=True,
        enhance_materials=True,
        merge_materials=True,
    )
    return [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]


def world_bbox(objects: list[bpy.types.Object]) -> tuple[Vector, Vector]:
    lo = Vector((float("inf"),) * 3)
    hi = Vector((float("-inf"),) * 3)
    for obj in objects:
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def add_backdrop(lo: Vector, hi: Vector, is_bottom: bool) -> bpy.types.Object:
    """Seamless studio floor. Doubles as the background in angled views."""
    span = max(hi.x - lo.x, hi.y - lo.y)
    z = (hi.z + 0.001) if is_bottom else (lo.z - 0.001)
    bpy.ops.mesh.primitive_plane_add(size=span * 14, location=(0, 0, z))
    plane = bpy.context.object
    plane.name = "backdrop"

    mat = bpy.data.materials.new("backdrop")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes["Principled BSDF"]
    bsdf.inputs["Base Color"].default_value = (0.55, 0.56, 0.60, 1.0)
    bsdf.inputs["Roughness"].default_value = 0.62
    bsdf.inputs["Specular IOR Level"].default_value = 0.25
    plane.data.materials.append(mat)
    return plane


def add_lights(lo: Vector, hi: Vector, is_bottom: bool) -> None:
    """Three-point studio rig, sized to the board so shadows stay soft at any scale."""
    span = max(hi.x - lo.x, hi.y - lo.y, 0.02)
    center = (lo + hi) / 2
    flip = -1.0 if is_bottom else 1.0

    def area(name, loc, size, energy, rot=(0, 0, 0)):
        light = bpy.data.lights.new(name, type="AREA")
        light.shape = "RECTANGLE"
        light.size = size
        light.size_y = size * 0.65
        light.energy = energy
        obj = bpy.data.objects.new(name, light)
        obj.location = loc
        obj.rotation_euler = rot
        bpy.context.scene.collection.objects.link(obj)
        return obj

    def aim(obj, target):
        direction = Vector(target) - obj.location
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Lights are placed at ~1.5*span, so watts must scale with span^2 to hold the
    # irradiance at the board constant regardless of board size.
    unit = span * span * 22.0

    key = area("key", (center.x - span * 1.1, center.y - span * 1.0, center.z + flip * span * 1.5),
               span * 2.2, unit * 1.6)
    fill = area("fill", (center.x + span * 1.3, center.y - span * 0.4, center.z + flip * span * 0.9),
                span * 2.8, unit * 0.45)
    rim = area("rim", (center.x + span * 0.3, center.y + span * 1.4, center.z + flip * span * 1.1),
               span * 1.8, unit * 0.8)
    for light in (key, fill, rim):
        aim(light, center)

    world = bpy.data.worlds.new("world")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Color"].default_value = (0.42, 0.44, 0.50, 1.0)
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.30
    bpy.context.scene.world = world


def add_camera(
    lo: Vector, hi: Vector, view: str, margin: float, width: int, height: int
) -> bpy.types.Object:
    elevation, azimuth = VIEWS[view]
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)

    cam_data = bpy.data.cameras.new("camera")
    cam = bpy.data.objects.new("camera", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    bpy.context.scene.camera = cam

    el, az = math.radians(elevation), math.radians(azimuth)
    back = Vector((
        math.cos(el) * math.sin(az),
        -math.cos(el) * math.cos(az),
        math.sin(el),
    ))
    cam.rotation_euler = (-back).to_track_quat("-Z", "Y").to_euler()
    right, up = cam.matrix_world.to_3x3().col[0], cam.matrix_world.to_3x3().col[1]

    # The eight bbox corners in camera axes; frame against those rather than a
    # bounding sphere, which would leave a wide board swimming in empty frame.
    corners = [
        Vector((x, y, z)) - center
        for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)
    ]

    if view in ("top", "bottom"):
        cam_data.type = "ORTHO"
        # ortho_scale spans the *longer* image axis, so the shorter one has to be
        # scaled up into it or a near-square board gets cropped.
        aspect = max(width / height, height / width)
        extent_long, extent_short = (
            (max(abs(c.dot(right)) for c in corners), max(abs(c.dot(up)) for c in corners))
            if width >= height
            else (max(abs(c.dot(up)) for c in corners), max(abs(c.dot(right)) for c in corners))
        )
        cam_data.ortho_scale = 2 * max(extent_long, extent_short * aspect) * margin
        cam.location = center + back * radius * 6
    else:
        cam_data.type = "PERSP"
        cam_data.lens = 85.0  # long lens: product-shot look, little perspective distortion
        tan_long = math.tan(cam_data.angle / 2)
        tan_x, tan_y = (
            (tan_long, tan_long * height / width)
            if width >= height
            else (tan_long * width / height, tan_long)
        )
        distance = max(
            c.dot(back) + max(abs(c.dot(right)) / tan_x, abs(c.dot(up)) / tan_y) for c in corners
        )
        cam.location = center + back * distance * margin

    cam_data.clip_start = radius * 0.01
    cam_data.clip_end = radius * 100
    return cam


def configure_render(args: argparse.Namespace, backdrop: bpy.types.Object) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.render.resolution_x = args.width
    scene.render.resolution_y = args.height
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.filepath = args.out

    scene.cycles.samples = args.samples
    scene.cycles.use_denoising = True
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.01
    scene.cycles.max_bounces = 8
    scene.cycles.transmission_bounces = 4
    scene.cycles.caustics_reflective = False
    scene.cycles.caustics_refractive = False

    if args.transparent:
        scene.render.film_transparent = True
        backdrop.is_shadow_catcher = True

    scene.view_settings.exposure = args.exposure
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"

    if args.gpu:
        enable_gpu(scene)
    else:
        scene.cycles.device = "CPU"


def enable_gpu(scene: bpy.types.Scene) -> None:
    """Opt-in: Cycles' device enumeration walks *every* backend, and the oneAPI
    level-zero loader segfaults Blender on this machine. CPU is the safe default."""
    prefs = bpy.context.preferences.addons["cycles"].preferences
    for backend in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        devices = [d for d in prefs.devices if d.type == backend]
        if devices:
            for device in prefs.devices:
                device.use = device.type in (backend, "CPU")
            scene.cycles.device = "GPU"
            print(f"cycles: {backend} on {[d.name for d in devices]}")
            return
    scene.cycles.device = "CPU"
    print("cycles: CPU")


def main() -> None:
    args = parse_args()
    reset_scene()

    objects = import_pcb(Path(args.pcb3d), args.texture_dpi)
    if not objects:
        raise SystemExit(f"no mesh objects imported from {args.pcb3d}")
    lo, hi = world_bbox(objects)
    print(f"board bbox {tuple(round(v, 4) for v in lo)} .. {tuple(round(v, 4) for v in hi)}")

    is_bottom = args.view == "bottom"
    backdrop = add_backdrop(lo, hi, is_bottom)
    add_lights(lo, hi, is_bottom)
    add_camera(lo, hi, args.view, args.margin, args.width, args.height)
    configure_render(args, backdrop)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.render.render(write_still=True)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
