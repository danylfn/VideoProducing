"""
Blender сцена: как работи разпределителен вал (overhead camshaft с чашкови повдигачи), 4 цилиндъра.
Пуска се от explainer/run.py:  Blender -b -P explainer/scenes/camshaft.py -- spec.json

Всичко е истинска геометрия и истинска кинематика, без AI:
  - гърбицата има хармоничен профил (база + плавно повдигане), изпъкнал за плосък повдигач;
  - повдигането на клапана във всеки кадър = колко дълбоко гърбицата натиска повдигача (опорна функция на
    профила при текущия ъгъл) -> повдигачът винаги допира гърбицата, без застъпване;
  - гърбиците на цилиндри 1-3-4-2 са на 90° една от друга (ред на запалване 1-3-4-2);
  - пружината се свива точно колкото се отваря клапанът.
spec.json: {"frames", "fps", "out", "engine", "samples", "cuts": [начален кадър на всеки план], "res": [w, h]}
"""
import json
import math
import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

SPEC = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text())
FRAMES, FPS = SPEC["frames"], SPEC["fps"]

# ---------------------------------------------------------------- размери (метри)
R0, LIFT, HALF = 0.018, 0.008, math.radians(75)   # база, макс. повдигане 8 mm, половин продължителност
LOBE_W, PITCH = 0.016, 0.11                       # широчина на гърбицата, разстояние между цилиндрите
LOBES_X = [-1.5 * PITCH, -0.5 * PITCH, 0.5 * PITCH, 1.5 * PITCH]
PHASES = [0, 270, 90, 180]                        # цилиндри 1..4 при ред на запалване 1-3-4-2
CAM_DEG_PER_SEC = 90.0                            # забавен каданс: 1 оборот за 4 s
TAPPET_R, TAPPET_H = 0.015, 0.022
SPRING_SEAT_Z, SPRING_R, SPRING_WIRE = -0.095, 0.0105, 0.0017
STEM_R, STEM_LEN, VALVE_R = 0.0035, 0.13, 0.016
DECK_Z = -0.17                                    # долната повърхност на главата


def lift_at(theta: float) -> float:
    a = (theta + math.pi) % (2 * math.pi) - math.pi
    return 0.5 * LIFT * (1 + math.cos(math.pi * a / HALF)) if abs(a) < HALF else 0.0


PROFILE = [(R0 + lift_at(2 * math.pi * i / 360)) for i in range(360)]
PROFILE_PTS = [(r * math.cos(2 * math.pi * i / 360), r * math.sin(2 * math.pi * i / 360)) for i, r in enumerate(PROFILE)]


def follower_depth(angle: float) -> float:
    """Колко надолу стига гърбицата при завъртане angle около X (Blender: z' = y sin + z cos)."""
    s, c = math.sin(angle), math.cos(angle)
    return -min(y * s + z * c for y, z in PROFILE_PTS)


# ---------------------------------------------------------------- помощни
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_obj(name, bm, mat, parent=None, bevel=0.0007, smooth=True):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    ob.data.materials.append(mat)
    if bevel:
        m = ob.modifiers.new("Bevel", "BEVEL")
        m.width, m.segments, m.limit_method, m.harden_normals = bevel, 3, "ANGLE", True
    if parent:
        ob.parent = parent
    return ob


def cylinder(r, depth, segs=96, axis="Z", r2=None):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segs, radius1=r,
                          radius2=r if r2 is None else r2, depth=depth)
    if axis == "X":
        bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=__import__("mathutils").Matrix.Rotation(math.pi / 2, 3, "Y"))
    return bm


def prism(points_yz, width):
    """Многоъгълник в равнината YZ, изтеглен по X (центриран)."""
    bm = bmesh.new()
    vs = [bm.verts.new((-width / 2, y, z)) for y, z in points_yz]
    face = bm.faces.new(vs)
    ext = bmesh.ops.extrude_face_region(bm, geom=[face])
    moved = [e for e in ext["geom"] if isinstance(e, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=moved, vec=(width, 0, 0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return bm


def empty(name, loc=(0, 0, 0), parent=None):
    e = bpy.data.objects.new(name, None)
    e.location = loc
    bpy.context.scene.collection.objects.link(e)
    if parent:
        e.parent = parent
    return e


def material(name, color, metallic=1.0, rough=0.3, aniso=0.0, coat=0.0, bump=0.0, bump_scale=400.0, mottle=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    vals = {"Base Color": (*color, 1), "Metallic": metallic, "Roughness": rough, "Anisotropic": aniso,
            "Coat Weight": coat, "Coat Roughness": 0.05}
    for k, v in vals.items():
        if k in bsdf.inputs:
            bsdf.inputs[k].default_value = v
    if bump:  # фина текстура (следи от обработка / отливка), за да не изглежда „CG гладко“
        tex = nt.nodes.new("ShaderNodeTexNoise")
        tex.inputs["Scale"].default_value = bump_scale
        tex.inputs["Detail"].default_value = 8
        b = nt.nodes.new("ShaderNodeBump")
        b.inputs["Strength"].default_value = bump
        b.inputs["Distance"].default_value = 0.0002 if bump_scale > 500 else 0.0005
        nt.links.new(tex.outputs["Fac"], b.inputs["Height"])
        nt.links.new(b.outputs["Normal"], bsdf.inputs["Normal"])
        rmix = nt.nodes.new("ShaderNodeMapRange")
        rmix.inputs["To Min"].default_value = rough * 0.8
        rmix.inputs["To Max"].default_value = rough * 1.25
        nt.links.new(tex.outputs["Fac"], rmix.inputs["Value"])
        nt.links.new(rmix.outputs["Result"], bsdf.inputs["Roughness"])
    if mottle:  # петна в цвета (отливка, окисляване) – без тях металът изглежда като пластмаса
        big = nt.nodes.new("ShaderNodeTexNoise")
        big.inputs["Scale"].default_value = 45
        big.inputs["Detail"].default_value = 4
        rng = nt.nodes.new("ShaderNodeMapRange")
        rng.inputs["To Min"].default_value = 1 - mottle
        rng.inputs["To Max"].default_value = 1 + mottle
        hsv = nt.nodes.new("ShaderNodeHueSaturation")
        hsv.inputs["Color"].default_value = (*color, 1)
        nt.links.new(big.outputs["Fac"], rng.inputs["Value"])
        nt.links.new(rng.outputs["Result"], hsv.inputs["Value"])
        nt.links.new(hsv.outputs["Color"], bsdf.inputs["Base Color"])
    return m


# ---------------------------------------------------------------- сцена
reset()
scene = bpy.context.scene
scene.render.fps = FPS
scene.frame_start, scene.frame_end = 1, FRAMES

M_CAM = material("Cam steel", (0.62, 0.62, 0.64), rough=0.16, aniso=0.6, coat=0.35, bump=0.04)
M_JOURNAL = material("Polished journal", (0.7, 0.7, 0.72), rough=0.07, coat=0.5)
M_TAPPET = material("Tappet", (0.5, 0.5, 0.52), rough=0.22, coat=0.3, bump=0.03)
M_SPRING = material("Spring black oxide", (0.035, 0.035, 0.04), rough=0.35, coat=0.25)
M_VALVE = material("Valve stainless", (0.58, 0.57, 0.55), rough=0.28, bump=0.03)
M_HEAD = material("Cast aluminium", (0.42, 0.42, 0.41), rough=0.55, bump=0.18, bump_scale=700, mottle=0.18)
M_CUT = material("Machined bore", (0.56, 0.56, 0.55), rough=0.22, aniso=0.3, bump=0.05, bump_scale=1500)
M_SPROCKET = material("Sprocket", (0.25, 0.25, 0.27), rough=0.3, coat=0.2, bump=0.05, mottle=0.15)
M_BACKDROP = material("Backdrop", (0.03, 0.03, 0.032), metallic=0.0, rough=0.8)

# --- разпределителен вал (въртящ се)
cam = empty("Camshaft")
mesh_obj("Shaft", cylinder(0.011, 0.56, axis="X"), M_CAM, cam).location.x = 0.01
for i, x in enumerate([-2 * PITCH, -PITCH, 0, PITCH, 2 * PITCH]):
    mesh_obj(f"Journal{i}", cylinder(0.0165, 0.02, axis="X"), M_JOURNAL, cam).location.x = x
for i, (x, ph) in enumerate(zip(LOBES_X, PHASES)):
    rot = math.radians(ph)
    pts = [(y * math.cos(rot) - z * math.sin(rot), y * math.sin(rot) + z * math.cos(rot)) for y, z in PROFILE_PTS]
    mesh_obj(f"Lobe{i + 1}", prism(pts, LOBE_W), M_CAM, cam).location.x = x
# зъбно колело (задвижва се от коляновия вал с ремък/верига, 2:1)
teeth, r_out, r_in = 36, 0.055, 0.05
gear = []
for k in range(teeth * 4):
    a = 2 * math.pi * k / (teeth * 4)
    r = r_out if k % 4 in (1, 2) else r_in
    gear.append((r * math.cos(a), r * math.sin(a)))
mesh_obj("Sprocket", prism(gear, 0.012), M_SPROCKET, cam).location.x = 0.285
mesh_obj("Hub", cylinder(0.02, 0.03, axis="X"), M_SPROCKET, cam).location.x = 0.27

# --- клапанни групи (неподвижна база + подвижна част)
valves, springs = [], []
for i, x in enumerate(LOBES_X):
    mov = empty(f"Valve{i + 1}", (x, 0, 0))
    mesh_obj("Tappet", cylinder(TAPPET_R, TAPPET_H), M_TAPPET, mov).location.z = -R0 - TAPPET_H / 2
    z_tb = -R0 - TAPPET_H
    mesh_obj("Retainer", cylinder(0.0125, 0.003, r2=0.009), M_TAPPET, mov).location.z = z_tb - 0.0015
    mesh_obj("Stem", cylinder(STEM_R, STEM_LEN), M_VALVE, mov).location.z = z_tb - STEM_LEN / 2
    # тарелка: широка отдолу, стеснява се нагоре към стеблото
    mesh_obj("Valve head", cylinder(VALVE_R, 0.008, r2=STEM_R * 1.4), M_VALVE, mov).location.z = z_tb - STEM_LEN + 0.004
    valves.append(mov)
    # пружина: спирала от седлото до шайбата; свива се по Z колкото се отваря клапанът
    length = z_tb - 0.003 - SPRING_SEAT_Z
    cu = bpy.data.curves.new(f"Spring{i + 1}", "CURVE")
    cu.dimensions, cu.bevel_depth, cu.bevel_resolution = "3D", SPRING_WIRE, 4
    sp = cu.splines.new("POLY")
    coils, n = 6.5, 260
    sp.points.add(n - 1)
    for k in range(n):
        t = k / (n - 1)
        a = 2 * math.pi * coils * t
        sp.points[k].co = (SPRING_R * math.cos(a), SPRING_R * math.sin(a), t * length, 1)
    sob = bpy.data.objects.new(f"Spring{i + 1}", cu)
    sob.data.materials.append(M_SPRING)
    scene.collection.objects.link(sob)
    sob.location = (x, 0, SPRING_SEAT_Z)
    springs.append((sob, length))
    mesh_obj(f"Seat{i + 1}", cylinder(0.013, 0.003), M_TAPPET).location = (x, 0, SPRING_SEAT_Z - 0.0015)

# --- глава на двигателя, разрязана по оста на клапаните (виждаме сечението)
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
head = mesh_obj("Cylinder head", bm, M_HEAD, bevel=0.0015, smooth=False)
HEAD_TOP = -0.005  # главата обгръща долната половина на вала (легла на лагерите)
head.scale = (0.56, 0.075, HEAD_TOP - DECK_Z)
head.location = (0.0, 0.0375 - 0.002, (DECK_Z + HEAD_TOP) / 2)
head.data.materials.append(M_CUT)
for i, x in enumerate(LOBES_X):
    for r, z0, z1 in [(TAPPET_R + 0.0008, -0.07, 0.005), (0.0145, SPRING_SEAT_Z, -0.065), (STEM_R + 0.0008, DECK_Z - 0.01, SPRING_SEAT_Z),
                      (VALVE_R + 0.001, DECK_Z - 0.01, DECK_Z + 0.008)]:
        cut = mesh_obj(f"Bore{i}_{z0:.2f}", cylinder(r, z1 - z0), M_CUT, bevel=0)
        cut.location = (x, 0, (z0 + z1) / 2)
        cut.display_type, cut.hide_render = "WIRE", True
        mod = head.modifiers.new(f"bore{i}{z0:.2f}", "BOOLEAN")
        mod.object, mod.operation, mod.solver, mod.material_mode = cut, "DIFFERENCE", "EXACT", "TRANSFER"
    cut = mesh_obj(f"Pocket{i}", cylinder(0.030, LOBE_W + 0.01, axis="X"), M_CUT, bevel=0)  # място за гърбицата
    cut.location, cut.hide_render, cut.display_type = (x, 0, 0), True, "WIRE"
    mod = head.modifiers.new(f"pocket{i}", "BOOLEAN")
    mod.object, mod.operation, mod.solver, mod.material_mode = cut, "DIFFERENCE", "EXACT", "TRANSFER"
for x in [-2 * PITCH, -PITCH, 0, PITCH, 2 * PITCH]:  # легла на лагерите
    cut = mesh_obj(f"Bearing{x:.2f}", cylinder(0.0168, 0.024, axis="X"), M_CUT, bevel=0)
    cut.location, cut.hide_render, cut.display_type = (x, 0, 0), True, "WIRE"
    mod = head.modifiers.new(f"bearing{x:.2f}", "BOOLEAN")
    mod.object, mod.operation, mod.solver, mod.material_mode = cut, "DIFFERENCE", "EXACT", "TRANSFER"

# главата е неподвижна -> изрязванията се смятат веднъж, не за всеки кадър (и подкадър при motion blur)
dg = bpy.context.evaluated_depsgraph_get()
baked = bpy.data.meshes.new_from_object(head.evaluated_get(dg))
head.modifiers.clear()
head.data = baked
for ob in [o for o in bpy.data.objects if o.name.startswith(("Bore", "Pocket", "Bearing"))]:
    bpy.data.objects.remove(ob)

# студиен фон: улавя светлината и дава плавен преход зад механизма (вместо „празно“ черно)
bm = bmesh.new()
bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=1.5)
bd = mesh_obj("Backdrop", bm, M_BACKDROP, bevel=0, smooth=False)
bd.location, bd.rotation_euler.x = (0, 0.45, -0.1), math.pi / 2

# ---------------------------------------------------------------- анимация (истинска кинематика)
for f in range(1, FRAMES + 1):
    ang = math.radians(CAM_DEG_PER_SEC) * (f - 1) / FPS
    cam.rotation_euler.x = ang
    cam.keyframe_insert("rotation_euler", index=0, frame=f)
    for i, (mov, (sob, length)) in enumerate(zip(valves, springs)):
        lift = follower_depth(ang + math.radians(PHASES[i])) - R0
        mov.location.z = -lift
        mov.keyframe_insert("location", index=2, frame=f)
        sob.scale.z = (length - lift) / length
        sob.keyframe_insert("scale", index=2, frame=f)
for ob in bpy.data.objects:
    if ob.animation_data and ob.animation_data.action:
        for fc in getattr(ob.animation_data.action, "fcurves", []):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"

# ---------------------------------------------------------------- светлина и свят
world = bpy.data.worlds.new("Studio")
scene.world = world
world.use_nodes = True
wn = world.node_tree
bg = wn.nodes["Background"]
hdri = Path(bpy.utils.system_resource("DATAFILES", path="studiolights/world")) / "studio.exr"
if not hdri.exists():
    hdri = next(Path(bpy.utils.system_resource("DATAFILES", path="studiolights/world")).glob("*.exr"))
env = wn.nodes.new("ShaderNodeTexEnvironment")
env.image = bpy.data.images.load(str(hdri))
bg.inputs["Strength"].default_value = 0.3
wn.links.new(env.outputs["Color"], bg.inputs["Color"])
# камерата вижда тъмен фон, а металът отразява студиото
dark = wn.nodes.new("ShaderNodeBackground")
dark.inputs["Color"].default_value = (0.006, 0.0065, 0.008, 1)
lp = wn.nodes.new("ShaderNodeLightPath")
mix = wn.nodes.new("ShaderNodeMixShader")
wn.links.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
wn.links.new(bg.outputs[0], mix.inputs[1])
wn.links.new(dark.outputs[0], mix.inputs[2])
wn.links.new(mix.outputs[0], wn.nodes["World Output"].inputs[0])


def area(name, loc, size, power, color, target=(0, 0, -0.05)):
    ld = bpy.data.lights.new(name, "AREA")
    ld.size, ld.energy, ld.color = size, power, color
    ob = bpy.data.objects.new(name, ld)
    ob.location = loc
    scene.collection.objects.link(ob)
    c = ob.constraints.new("TRACK_TO")
    c.target = empty(name + "_t", target)
    return ob


area("Key", (-0.45, -0.55, 0.55), 0.6, 18, (1.0, 0.96, 0.9))
area("Rim", (0.35, 0.6, 0.35), 0.5, 20, (0.75, 0.85, 1.0))
area("Fill", (0.5, -0.6, -0.1), 0.8, 4, (1.0, 1.0, 1.0))
area("Top", (0.0, -0.05, 0.6), 0.4, 8, (1.0, 0.98, 0.95), target=(0, 0, 0))

# ---------------------------------------------------------------- камери (план = cut на ново изречение)
SHOTS = [  # (име, начало -> край на движението, цел, обектив, бленда)
    ("Hero", (-0.40, -0.36, 0.20), (-0.33, -0.30, 0.16), (0.03, 0.0, -0.06), 45, 4.0),
    # 3/4 откъм края на вала -> вижда се яйцевидният профил на гърбицата, повдигачът, пружината и клапанът
    ("Macro", (-0.37, -0.26, -0.02), (-0.345, -0.235, -0.03), (-0.165, 0.0, -0.07), 55, 4.0),
    # почти по оста на вала -> четирите гърбици под различен ъгъл (90° една от друга)
    ("Lobes", (-0.46, -0.13, 0.075), (-0.43, -0.115, 0.065), (-0.11, 0.0, -0.01), 70, 11.0),
    ("Wide", (0.48, -0.52, 0.30), (0.56, -0.64, 0.36), (0.02, 0.0, -0.05), 38, 5.6),
]
cuts = SPEC["cuts"] + [FRAMES + 1]
for k, (name, a, b, tgt, lens, fstop) in enumerate(SHOTS):
    cd = bpy.data.cameras.new(name)
    cd.lens, cd.sensor_fit, cd.sensor_width = lens, "AUTO", 36
    co = bpy.data.objects.new(name, cd)
    scene.collection.objects.link(co)
    t = empty(name + "_target", tgt)
    con = co.constraints.new("TRACK_TO")
    con.target = t
    cd.dof.use_dof, cd.dof.focus_object, cd.dof.aperture_fstop = True, t, fstop
    f0, f1 = cuts[k], cuts[k + 1] - 1
    co.location = a
    co.keyframe_insert("location", frame=f0)
    co.location = b
    co.keyframe_insert("location", frame=max(f1, f0 + 1))
    mk = scene.timeline_markers.new(name, frame=f0)
    mk.camera = co
    if k == 0:
        scene.camera = co

# ---------------------------------------------------------------- рендер
r = scene.render
r.resolution_x, r.resolution_y = SPEC["res"]
r.resolution_percentage = 100
r.image_settings.file_format = "PNG"
r.image_settings.color_depth = "8"
r.filepath = str(Path(SPEC["out"]) / "f_")
r.use_overwrite, r.use_placeholder = False, True   # прекъснат рендер продължава от последния кадър
r.use_motion_blur = True
r.use_persistent_data = True  # сцената остава в паметта между кадрите
r.motion_blur_shutter = 0.5
scene.view_settings.view_transform = "AgX"
try:
    scene.view_settings.look = "AgX - Medium High Contrast"
except TypeError:
    pass
if SPEC["engine"] == "cycles":
    r.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    if hasattr(prefs, "kernel_optimization_level"):  # фоновото компилиране на Metal kernel-и крашва Blender 5.2
        prefs.kernel_optimization_level = "OFF"
    prefs.get_devices()
    for d in prefs.devices:
        d.use = True
    scene.cycles.device = "GPU"
    scene.cycles.samples = SPEC["samples"]
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.015
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 8
else:
    r.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items.keys() else "BLENDER_EEVEE"
if SPEC.get("blend"):
    bpy.ops.wm.save_as_mainfile(filepath=SPEC["blend"])
if SPEC.get("still"):  # само тестови кадри
    for f in SPEC["still"]:
        scene.frame_set(f)
        r.filepath = str(Path(SPEC["out"]) / f"still_{f:04d}.png")
        bpy.ops.render.render(write_still=True)
else:
    bpy.ops.render.render(animation=True)
