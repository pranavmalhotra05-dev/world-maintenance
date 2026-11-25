"""Blender scene builder: data-faithful cinematic stills of one 3D block.

  "C:/Program Files/Blender Foundation/Blender 5.3/blender.exe" -b --factory-startup -P usa/render_blender.py -- minneapolis-mn
      [--views hero,top,street,night] [--samples 160] [--res 1600x900] [--out DIR] [--save-blend]

Reads usa/_cache/renders/<slug>/scene.npz + scene.json written by usa/render_export.py (run that first, in .venv).
Every object comes from the block's own data:
  terrain   1 m LiDAR ground mesh, textured with the newest NAIP aerial (public domain)
  buildings roof + wall surfaces fitted to the block's LiDAR (mesh.txt); roof colour = NAIP projected onto the roof, mixed
            with the roof's median NAIP colour; walls a light neutral (no facade data exists, so no invented detail)
  points    EVERY LiDAR return as a sphere (Geometry Nodes 'Mesh to Points', Cycles native point cloud), coloured from NAIP;
            vegetation returns larger so canopies read as solid, ground/roof returns small so the fitted models sit on top
  streets   streets.json (other lane) if present: asphalt road polygons, painted lines, crosswalks, signals, street lamps
  sun       Sun lamp + physical sky at the real solar position (NOAA) for the date/time in scene.json
Nothing else is added except a neutral dark plinth under the block edge (clearly not data) so the block does not float.
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "usa" / "_cache" / "renders"
WEB = ROOT / "web" / "usa" / "blocks"


def args():
    a = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    o = {"slug": None, "views": "hero,top,street,night", "samples": 384, "res": "1920x1080", "out": None, "save_blend": False}
    i = 0
    while i < len(a):
        if a[i] == "--views":
            o["views"] = a[i + 1]; i += 2
        elif a[i] == "--samples":
            o["samples"] = int(a[i + 1]); i += 2
        elif a[i] == "--res":
            o["res"] = a[i + 1]; i += 2
        elif a[i] == "--out":
            o["out"] = a[i + 1]; i += 2
        elif a[i] == "--save-blend":
            o["save_blend"] = True; i += 1
        else:
            o["slug"] = a[i]; i += 1
    return o


# ------------------------------------------------------------------------------------------------ helpers
def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def mesh_obj(name, verts, faces=None):
    me = bpy.data.meshes.new(name)
    verts = np.asarray(verts, np.float32)
    me.vertices.add(len(verts))
    me.vertices.foreach_set("co", verts.ravel())
    if faces is not None and len(faces):
        faces = np.asarray(faces, np.int32)
        nf = len(faces)
        me.loops.add(nf * 3)
        me.loops.foreach_set("vertex_index", faces.ravel())
        me.polygons.add(nf)
        me.polygons.foreach_set("loop_start", np.arange(0, nf * 3, 3, dtype=np.int32))
    me.update(calc_edges=True)
    me.validate(clean_customdata=False)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def node(nt, kind, loc=(0, 0), **props):
    n = nt.nodes.new(kind)
    n.location = loc
    for k, v in props.items():
        setattr(n, k, v)
    return n


def link(nt, a, b):
    nt.links.new(a, b)


def principled(nt):
    return nt.nodes.get("Principled BSDF") or node(nt, "ShaderNodeBsdfPrincipled")


def new_mat(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree


def img_coords(nt, half, loc=(-900, 0)):
    """Object-space x,y -> NAIP UV (image covers exactly [-half, half] in local metres, north up)."""
    tc = node(nt, "ShaderNodeTexCoord", (loc[0] - 400, loc[1]))
    mp = node(nt, "ShaderNodeMapping", loc)
    mp.inputs["Scale"].default_value = (1 / (2 * half), 1 / (2 * half), 0)
    mp.inputs["Location"].default_value = (0.5, 0.5, 0)
    link(nt, tc.outputs["Object"], mp.inputs["Vector"])
    return mp.outputs["Vector"]


def naip_tex(nt, image, half, loc=(-500, 0)):
    t = node(nt, "ShaderNodeTexImage", loc)
    t.image = image
    t.extension = "EXTEND"
    t.interpolation = "Cubic"
    link(nt, img_coords(nt, half, (loc[0] - 300, loc[1])), t.inputs["Vector"])
    # NAIP is muted and AgX desaturates further: a mild saturation lift keeps roofs/lawns their photographed hue
    hs = node(nt, "ShaderNodeHueSaturation", (loc[0] + 200, loc[1] - 150))
    hs.inputs["Saturation"].default_value = 1.25
    link(nt, t.outputs["Color"], hs.inputs["Color"])
    t.label = "naip"
    t["sat_out"] = hs.name
    return hs


def set_input(n, names, v):
    for nm in names if isinstance(names, (list, tuple)) else [names]:
        if nm in n.inputs:
            n.inputs[nm].default_value = v
            return True
    return False


def _noise(nt, vec, scale, loc, detail=6.0, rough=0.55):
    n = node(nt, "ShaderNodeTexNoise", loc)
    n.inputs["Scale"].default_value = scale
    set_input(n, "Detail", detail); set_input(n, "Roughness", rough)
    if vec is not None:
        link(nt, vec, n.inputs["Vector"])
    return n


def _maprange(nt, src, a, b, loc, fa=0.0, fb=1.0):
    m = node(nt, "ShaderNodeMapRange", loc)
    m.inputs["From Min"].default_value = fa; m.inputs["From Max"].default_value = fb
    m.inputs["To Min"].default_value = a; m.inputs["To Max"].default_value = b
    link(nt, src, m.inputs["Value"])
    return m.outputs["Result"]


def _math(nt, op, a, b, loc=(0, 0)):
    m = node(nt, "ShaderNodeMath", loc, operation=op)
    for i, v in enumerate((a, b)):
        if isinstance(v, (int, float)):
            m.inputs[i].default_value = v
        else:
            link(nt, v, m.inputs[i])
    return m.outputs[0]


def _mixc(nt, fac, a, b, loc=(0, 0), dtype="RGBA"):
    m = node(nt, "ShaderNodeMix", loc, data_type=dtype)
    for key, v in (("Factor", fac), ("A", a), ("B", b)):
        sock = m.inputs[key] if dtype != "VECTOR" or key == "Factor" else [s_ for s_ in m.inputs if s_.name == key and s_.type == "VECTOR"][0]
        if dtype == "RGBA" and key != "Factor":
            sock = [s_ for s_ in m.inputs if s_.name == key and s_.type == "RGBA"][0]
        if isinstance(v, (int, float)):
            sock.default_value = v
        elif isinstance(v, tuple):
            sock.default_value = v
        else:
            link(nt, v, sock)
    out = [o for o in m.outputs if o.name == "Result" and o.type == ("VECTOR" if dtype == "VECTOR" else "RGBA")][0]
    return out


def _bump(nt, height, strength, dist, loc, normal=None):
    b = node(nt, "ShaderNodeBump", loc)
    b.inputs["Strength"].default_value = strength; b.inputs["Distance"].default_value = dist
    link(nt, height, b.inputs["Height"])
    if normal is not None:
        link(nt, normal, b.inputs["Normal"])
    return b.outputs["Normal"]


# ------------------------------------------------------------------------------------------------ scene parts
def build_terrain(S, D, image):
    half = S["half_m"]
    tp, ti = D["terr_pos"], D["terr_idx"]
    ob = mesh_obj("terrain", tp, ti)
    ob.data.polygons.foreach_set("use_smooth", np.ones(len(ti), bool))
    m, nt = new_mat("ground_naip")
    b = principled(nt)
    t = naip_tex(nt, image, half, (-900, 300))
    tc = node(nt, "ShaderNodeTexCoord", (-1400, -300))
    # grass vs paved/bare straight from the photo: green excess G - max(R, B)
    sep = node(nt, "ShaderNodeSeparateColor", (-700, 0))
    link(nt, t.outputs["Color"], sep.inputs["Color"])
    mx = _math(nt, "MAXIMUM", sep.outputs[0], sep.outputs[2], (-550, 0))
    ge = _math(nt, "SUBTRACT", sep.outputs[1], mx, (-400, 0))
    grass = _maprange(nt, ge, 0.0, 1.0, (-250, 0), 0.005, 0.05)
    # micro-variation so close-ups are not a flat photo: grass clumps / blades, fine grit on paving
    n_blade = _noise(nt, tc.outputs["Object"], 260.0, (-700, -300), 8.0, 0.7)
    n_clump = _noise(nt, tc.outputs["Object"], 6.0, (-700, -500), 4.0, 0.6)
    n_grit = _noise(nt, tc.outputs["Object"], 900.0, (-700, -700), 2.0, 0.5)
    hg = _math(nt, "MULTIPLY", n_blade.outputs["Fac"], grass, (-450, -300))
    hp_ = _math(nt, "MULTIPLY", n_grit.outputs["Fac"], _math(nt, "SUBTRACT", 1.0, grass, (-500, -800)), (-450, -700))
    h = _math(nt, "ADD", hg, hp_, (-300, -400))
    nrm = _bump(nt, h, 0.35, 0.03, (-120, -400))
    shade = _maprange(nt, n_clump.outputs["Fac"], 0.86, 1.1, (-450, -500))
    shade_g = _mixc(nt, grass, (1.0, 1.0, 1.0, 1.0), None, (-250, -500)) if False else None
    col = node(nt, "ShaderNodeMix", (-120, 200), data_type="RGBA", blend_type="MULTIPLY")
    col.inputs["Factor"].default_value = 1.0
    link(nt, t.outputs["Color"], [s_ for s_ in col.inputs if s_.name == "A" and s_.type == "RGBA"][0])
    gm = _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", shade, 1.0, (-330, -560)), grass, (-250, -600))
    gm = _math(nt, "ADD", gm, 1.0, (-200, -600))
    cv = node(nt, "ShaderNodeCombineColor", (-150, -650))
    for i in range(3):
        link(nt, gm, cv.inputs[i])
    link(nt, cv.outputs[0], [s_ for s_ in col.inputs if s_.name == "B" and s_.type == "RGBA"][0])
    link(nt, [o for o in col.outputs if o.type == "RGBA"][0], b.inputs["Base Color"])
    link(nt, _maprange(nt, grass, 0.7, 0.95, (-250, 100)), b.inputs["Roughness"])
    set_input(b, ["Specular IOR Level", "Specular"], 0.35)
    link(nt, nrm, b.inputs["Normal"])
    ob.data.materials.append(m)

    # neutral plinth: skirt from the terrain edge down + a dark matte floor (not data; keeps the block from floating)
    tn = int(round(math.sqrt(len(tp))))
    G = tp.reshape(tn, tn, 3)
    edge = np.vstack([G[0, :], G[:, -1], G[-1, ::-1], G[::-1, 0]])
    zb = float(tp[:, 2].min()) - 2.5
    top = edge.copy(); bot = edge.copy(); bot[:, 2] = zb
    V = np.vstack([top, bot]); n = len(edge)
    F = []
    for i in range(n - 1):
        F += [[i, i + 1, n + i + 1], [i, n + i + 1, n + i]]
    sk = mesh_obj("plinth", V, np.array(F))
    pm, pnt = new_mat("plinth")
    pb = principled(pnt)
    pb.inputs["Base Color"].default_value = (0.02, 0.019, 0.018, 1)
    pb.inputs["Roughness"].default_value = 0.6
    sk.data.materials.append(pm)
    fl = mesh_obj("floor", [[-2000, -2000, zb], [2000, -2000, zb], [2000, 2000, zb], [-2000, 2000, zb]], [[0, 1, 2], [0, 2, 3]])
    fm, fnt = new_mat("floor")
    fb = principled(fnt)
    fb.inputs["Base Color"].default_value = (0.006, 0.006, 0.006, 1)
    fb.inputs["Roughness"].default_value = 0.95
    fl.data.materials.append(fm)
    return ob


def build_houses(S, D, image):
    half = S["half_m"]
    hp, hi = D["house_pos"], D["house_idx"]
    fk, fh = D["house_fkind"], D["house_fhid"]
    if not len(hi):
        return None
    ob = mesh_obj("buildings", hp, hi)
    me = ob.data
    me.polygons.foreach_set("material_index", (fk == 2).astype(np.int32))
    me.polygons.foreach_set("use_smooth", (fk == 1))
    alb = S.get("roof_albedo") or {}
    rgb = np.array([alb.get(str(int(h)), [0.45, 0.44, 0.42]) for h in fh], np.float32)
    rgb = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)  # NAIP median is sRGB; attributes are linear
    rng = np.random.default_rng(7)
    tint = rng.uniform(0.88, 1.06, 256).astype(np.float32)
    warm = rng.uniform(-0.025, 0.025, 256).astype(np.float32)
    wall = np.column_stack([0.42 * tint[fh % 256] + warm[fh % 256] * 0.5, 0.40 * tint[fh % 256], 0.37 * tint[fh % 256] - warm[fh % 256] * 0.5])
    wt = S.get("wall_types") or {}
    wtype = np.array([float(wt.get(str(int(h) % 256), 0)) for h in fh], np.float32)
    me.attributes.new("roof_albedo", "FLOAT_COLOR", "FACE").data.foreach_set("color", np.column_stack([rgb, np.ones(len(rgb))]).astype(np.float32).ravel())
    me.attributes.new("wall_tint", "FLOAT_COLOR", "FACE").data.foreach_set("color", np.column_stack([wall, np.ones(len(wall))]).astype(np.float32).ravel())
    me.attributes.new("wall_type", "FLOAT", "FACE").data.foreach_set("value", wtype)
    me.attributes.new("bid", "FLOAT", "FACE").data.foreach_set("value", (fh % 256).astype(np.float32))

    # roof: per-roof aerial colour + the aerial itself projected, granule bump, roughness varying across the roof
    m, nt = new_mat("roof")
    b = principled(nt)
    t = naip_tex(nt, image, half, (-900, 300))
    tc = node(nt, "ShaderNodeTexCoord", (-1300, -300))
    at = node(nt, "ShaderNodeAttribute", (-500, 450), attribute_name="roof_albedo")
    base = _mixc(nt, 0.45, at.outputs["Color"], t.outputs["Color"], (-250, 300))
    n_r = _noise(nt, tc.outputs["Object"], 1.2, (-700, -100), 5.0, 0.6)
    n_g = _noise(nt, tc.outputs["Object"], 380.0, (-700, -350), 2.0, 0.5)
    dirt = _maprange(nt, n_r.outputs["Fac"], 0.88, 1.06, (-450, -100), 0.3, 0.7)
    cv = node(nt, "ShaderNodeCombineColor", (-300, -100))
    for i in range(3):
        link(nt, dirt, cv.inputs[i])
    colm = node(nt, "ShaderNodeMix", (-80, 200), data_type="RGBA", blend_type="MULTIPLY")
    colm.inputs["Factor"].default_value = 1.0
    link(nt, base, [s_ for s_ in colm.inputs if s_.name == "A" and s_.type == "RGBA"][0])
    link(nt, cv.outputs[0], [s_ for s_ in colm.inputs if s_.name == "B" and s_.type == "RGBA"][0])
    link(nt, [o for o in colm.outputs if o.type == "RGBA"][0], b.inputs["Base Color"])
    link(nt, _maprange(nt, n_r.outputs["Fac"], 0.55, 0.88, (-450, -200), 0.3, 0.7), b.inputs["Roughness"])
    link(nt, _bump(nt, n_g.outputs["Fac"], 0.18, 0.01, (-300, -350)), b.inputs["Normal"])
    me.materials.append(m)

    # walls: material class from the assessor's exterior-wall field when the city publishes it (Minneapolis etc.);
    # unknown -> plain light render. No windows: we have no data on where they are.
    wm, nt = new_mat("wall")
    wb = principled(nt)
    tc = node(nt, "ShaderNodeTexCoord", (-1500, 0))
    sepv = node(nt, "ShaderNodeSeparateXYZ", (-1300, 0))
    link(nt, tc.outputs["Object"], sepv.inputs[0])
    uu = _math(nt, "ADD", sepv.outputs[0], sepv.outputs[1], (-1150, 50))
    cxyz = node(nt, "ShaderNodeCombineXYZ", (-1000, 0))
    link(nt, uu, cxyz.inputs[0]); link(nt, sepv.outputs[2], cxyz.inputs[1])
    uv = cxyz.outputs[0]
    wa = node(nt, "ShaderNodeAttribute", (-900, 400), attribute_name="wall_tint")
    wtp = node(nt, "ShaderNodeAttribute", (-900, 600), attribute_name="wall_type")
    # siding: 0.2 m lap boards
    wave = node(nt, "ShaderNodeTexWave", (-800, -200), wave_type="BANDS", bands_direction="Z")
    wave.inputs["Scale"].default_value = 1.57; set_input(wave, "Distortion", 0.0)
    wave.wave_profile = "SAW"
    link(nt, tc.outputs["Object"], wave.inputs["Vector"])
    # brick: 215 x 65 mm with 10 mm mortar
    br = node(nt, "ShaderNodeTexBrick", (-800, 200))
    br.inputs["Scale"].default_value = 1.0
    br.inputs["Brick Width"].default_value = 0.225; br.inputs["Row Height"].default_value = 0.075
    br.inputs["Mortar Size"].default_value = 0.01
    br.inputs["Color1"].default_value = (0.30, 0.11, 0.065, 1); br.inputs["Color2"].default_value = (0.22, 0.085, 0.05, 1)
    br.inputs["Mortar"].default_value = (0.42, 0.40, 0.37, 1)
    link(nt, uv, br.inputs["Vector"])
    n_st = _noise(nt, tc.outputs["Object"], 60.0, (-800, -450), 8.0, 0.7)
    vor = node(nt, "ShaderNodeTexVoronoi", (-800, -650), feature="DISTANCE_TO_EDGE")
    vor.inputs["Scale"].default_value = 3.5
    link(nt, uv, vor.inputs["Vector"])

    def is_t(v, loc):
        return _math(nt, "COMPARE", wtp.outputs["Fac"], float(v), loc) if False else _cmp(v, loc)

    def _cmp(v, loc):
        c = node(nt, "ShaderNodeMath", loc, operation="COMPARE")
        link(nt, wtp.outputs["Fac"], c.inputs[0]); c.inputs[1].default_value = float(v); c.inputs[2].default_value = 0.1
        return c.outputs[0]
    f_sid, f_br, f_stu, f_sto = _cmp(1, (-500, 800)), _cmp(2, (-500, 700)), _cmp(3, (-500, 600)), _cmp(4, (-500, 500))
    plain = wa.outputs["Color"]
    siding = _mixc(nt, 0.0, plain, plain, (-400, 300))
    stucco = _mixc(nt, 0.35, plain, (0.55, 0.52, 0.47, 1), (-400, 200))
    stone = _mixc(nt, _maprange(nt, vor.outputs["Distance"], 0.0, 1.0, (-600, -650), 0.0, 0.08), (0.12, 0.11, 0.10, 1), (0.34, 0.32, 0.29, 1), (-400, 100))
    c1 = _mixc(nt, f_sid, plain, siding, (-200, 400))
    c2 = _mixc(nt, f_br, c1, br.outputs["Color"], (-100, 400))
    c3 = _mixc(nt, f_stu, c2, stucco, (0, 400))
    c4 = _mixc(nt, f_sto, c3, stone, (100, 400))
    link(nt, c4, wb.inputs["Base Color"])
    # height field per class, one bump
    h_sid = _math(nt, "MULTIPLY", wave.outputs["Fac"], f_sid, (-300, -200))
    h_br = _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", 1.0, br.outputs["Fac"], (-450, 0)), f_br, (-300, -50))
    h_stu = _math(nt, "MULTIPLY", n_st.outputs["Fac"], _math(nt, "ADD", f_stu, 0.25, (-450, -450)), (-300, -450))
    h_sto = _math(nt, "MULTIPLY", _maprange(nt, vor.outputs["Distance"], 0.0, 1.0, (-600, -800), 0.0, 0.05), f_sto, (-300, -650))
    hsum = _math(nt, "ADD", _math(nt, "ADD", h_sid, h_br, (-150, -100)), _math(nt, "ADD", h_stu, h_sto, (-150, -500)), (0, -300))
    link(nt, _bump(nt, hsum, 0.35, 0.015, (150, -300)), wb.inputs["Normal"])
    wb.inputs["Roughness"].default_value = 0.82
    me.materials.append(wm)
    return ob


def build_points(S, D):
    P, k, col = D["pts"], D["pts_k"], D["pts_col"]
    r0 = float(S["point_radius"])
    ob = mesh_obj("lidar_points", P)
    me = ob.data
    hag = D.get("pts_hag", np.zeros(len(k), np.float32))
    r_g = 0.016
    # vegetation/other returns grow from ground-point size (near the ground: grass, kerbs, noise) to canopy size (above ~2 m)
    rad = np.where(k >= 2, r_g + (r0 * 1.35 - r_g) * np.clip((hag - 0.25) / 2.0, 0, 1), np.where(k == 1, 0.022, r_g)).astype(np.float32)
    me.attributes.new("radius_m", "FLOAT", "POINT").data.foreach_set("value", rad)
    me.attributes.new("kind", "FLOAT", "POINT").data.foreach_set("value", k.astype(np.float32))
    me.attributes.new("var", "FLOAT", "POINT").data.foreach_set("value", np.random.default_rng(3).random(len(k)).astype(np.float32))
    c = me.attributes.new("col", "FLOAT_COLOR", "POINT")
    lin = np.where(col <= 0.04045, col / 12.92, ((col + 0.055) / 1.055) ** 2.4)  # NAIP sRGB -> linear
    c.data.foreach_set("color", np.column_stack([lin, np.ones(len(lin))]).astype(np.float32).ravel())

    ng = bpy.data.node_groups.new("lidar_to_points", "GeometryNodeTree")
    ng.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    ng.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    gi = node(ng, "NodeGroupInput", (-600, 0)); go = node(ng, "NodeGroupOutput", (400, 0))
    na = node(ng, "GeometryNodeInputNamedAttribute", (-400, -200), data_type="FLOAT")
    na.inputs["Name"].default_value = "radius_m"
    mp = node(ng, "GeometryNodeMeshToPoints", (-150, 0))
    link(ng, gi.outputs[0], mp.inputs["Mesh"])
    link(ng, na.outputs["Attribute"], mp.inputs["Radius"])
    sm = node(ng, "GeometryNodeSetMaterial", (150, 0))
    link(ng, mp.outputs[0], sm.inputs["Geometry"])
    link(ng, sm.outputs[0], go.inputs[0])
    mod = ob.modifiers.new("points", "NODES"); mod.node_group = ng

    m, nt = new_mat("lidar")
    b = principled(nt)
    at = node(nt, "ShaderNodeAttribute", (-900, 200), attribute_name="col")
    kk = node(nt, "ShaderNodeAttribute", (-900, -200), attribute_name="kind")
    vv = node(nt, "ShaderNodeAttribute", (-900, 0), attribute_name="var")
    veg = _math(nt, "GREATER_THAN", kk.outputs["Fac"], 1.5, (-700, -200))
    # leaf-to-leaf variation: +-4 % hue, 0.82-1.12 value (vegetation only)
    hs = node(nt, "ShaderNodeHueSaturation", (-500, 200))
    link(nt, _maprange(nt, vv.outputs["Fac"], 0.48, 0.52, (-700, 100)), hs.inputs["Hue"])
    link(nt, _maprange(nt, vv.outputs["Fac"], 1.1, 1.3, (-700, 0)), hs.inputs["Saturation"])
    vval = _maprange(nt, vv.outputs["Fac"], 0.82, 1.12, (-700, -50))
    link(nt, _mixc(nt, veg, 1.0, 1.0, (-650, -100), "FLOAT") if False else _math(nt, "ADD", _math(nt, "MULTIPLY", _math(nt, "SUBTRACT", vval, 1.0, (-600, -80)), veg, (-550, -80)), 1.0, (-500, -80)), hs.inputs["Value"])
    link(nt, at.outputs["Color"], hs.inputs["Color"])
    # (canopy self-shadowing comes from Cycles' path tracing + the under-canopy darkening baked in render_export.py;
    #  an AO shader node over ~1M spheres cost 10x render time for little visible gain)
    link(nt, hs.outputs["Color"], b.inputs["Base Color"])
    link(nt, _maprange(nt, veg, 0.8, 0.6, (-500, -300)), b.inputs["Roughness"])
    tw = b.inputs.get("Transmission Weight")
    if tw is not None:
        link(nt, _math(nt, "MULTIPLY", veg, 0.1, (-300, -400)), tw)
    sm.inputs["Material"].default_value = m
    return ob


def _drape(D, xy, lift):
    tp = D["terr_pos"]
    tn = int(round(math.sqrt(len(tp))))
    G = tp.reshape(tn, tn, 3)
    x0, y0 = G[0, 0, 0], G[0, 0, 1]
    step = G[0, 1, 0] - G[0, 0, 0]
    i = np.clip((np.asarray(xy)[:, 0] - x0) / step, 0, tn - 1.001); j = np.clip((np.asarray(xy)[:, 1] - y0) / step, 0, tn - 1.001)
    i0, j0 = i.astype(int), j.astype(int); fi, fj = i - i0, j - j0
    Z = G[:, :, 2]
    z = (Z[j0, i0] * (1 - fi) * (1 - fj) + Z[j0, i0 + 1] * fi * (1 - fj) + Z[j0 + 1, i0] * (1 - fi) * fj + Z[j0 + 1, i0 + 1] * fi * fj)
    return np.column_stack([xy, z + lift])


def _ribbon(path, width, dashed=False, dash=3.0, gap=6.0):
    P = np.asarray(path, float)
    quads = []
    if len(P) < 2:
        return quads
    seg = np.diff(P, axis=0); L = np.hypot(*seg.T)
    s_acc = 0.0
    for a, b, l in zip(P[:-1], P[1:], L):
        if l < 1e-6:
            continue
        u = (b - a) / l; nrm = np.array([-u[1], u[0]]) * width / 2
        pieces = []
        if dashed:
            t = 0.0
            while t < l:
                ph = (s_acc + t) % (dash + gap)
                if ph < dash:
                    e = min(l, t + dash - ph); pieces.append((t, e)); t = e
                else:
                    t += dash + gap - ph
        else:
            pieces = [(0.0, l)]
        for t0, t1 in pieces:
            p0, p1 = a + u * t0, a + u * t1
            quads.append([p0 - nrm, p0 + nrm, p1 + nrm, p1 - nrm])
        s_acc += l
    return quads


def _surface_mat(name, image, half, dark, light, naip_mix, rough, noise_scale=3.0):
    """Paved surface: a plausible asphalt/concrete base tinted by the NAIP pixel underneath (keeps real patches/stains)."""
    m, nt = new_mat(name)
    b = principled(nt)
    tc = node(nt, "ShaderNodeTexCoord", (-1000, 0))
    nz = node(nt, "ShaderNodeTexNoise", (-800, 100)); nz.inputs["Scale"].default_value = noise_scale
    link(nt, tc.outputs["Object"], nz.inputs["Vector"])
    ramp = node(nt, "ShaderNodeValToRGB", (-600, 100))
    ramp.color_ramp.elements[0].color = dark; ramp.color_ramp.elements[1].color = light
    link(nt, nz.outputs["Fac"], ramp.inputs["Fac"])
    t = naip_tex(nt, image, half, (-600, -200))
    mix = node(nt, "ShaderNodeMix", (-300, 0), data_type="RGBA", blend_type="MULTIPLY")
    mix.inputs["Factor"].default_value = naip_mix
    link(nt, ramp.outputs["Color"], mix.inputs["A"])
    gam = node(nt, "ShaderNodeGamma", (-450, -200)); gam.inputs["Gamma"].default_value = 0.6
    link(nt, t.outputs["Color"], gam.inputs["Color"])
    link(nt, gam.outputs["Color"], mix.inputs["B"])
    link(nt, mix.outputs["Result"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    bump = node(nt, "ShaderNodeBump", (-200, -350)); bump.inputs["Strength"].default_value = 0.2
    n2 = node(nt, "ShaderNodeTexNoise", (-400, -350)); n2.inputs["Scale"].default_value = 160
    link(nt, tc.outputs["Object"], n2.inputs["Vector"]); link(nt, n2.outputs["Fac"], bump.inputs["Height"])
    link(nt, bump.outputs["Normal"], b.inputs["Normal"])
    return m


def _tri_obj(name, D, tri, lift, mat):
    if not tri:
        return None
    V = _drape(D, np.array(tri["xy"], float), lift)
    ob = mesh_obj(name, V, np.array(tri["idx"]))
    ob.data.materials.append(mat)
    return ob


def _quads_obj(name, D, quads, lift, mat):
    if not quads:
        return None
    Q = np.array(quads).reshape(-1, 2)
    V = _drape(D, Q, lift)
    F = np.arange(len(Q)).reshape(-1, 4)
    F = np.vstack([F[:, [0, 1, 2]], F[:, [0, 2, 3]]])
    ob = mesh_obj(name, V, F)
    ob.data.materials.append(mat)
    return ob


def build_streets(S, D, image):
    """streets.json surfaces draped on our LiDAR terrain (its own z is ignored so nothing floats or sinks here)."""
    st = S.get("streets")
    half = S["half_m"]
    if not st:
        return []
    asphalt = _surface_mat("asphalt", image, half, (0.1, 0.098, 0.095, 1), (0.16, 0.155, 0.15, 1), 0.45, 0.78)
    concrete = _surface_mat("concrete", image, half, (0.32, 0.31, 0.29, 1), (0.42, 0.41, 0.39, 1), 0.5, 0.85, 8.0)
    _tri_obj("roads", D, st.get("road_tris"), 0.035, asphalt)
    _tri_obj("roads_concrete", D, st.get("road_concrete_tris"), 0.035, concrete)
    _tri_obj("sidewalks", D, st.get("sidewalk_tris"), 0.03, concrete)
    _tri_obj("driveways", D, st.get("driveway_tris"), 0.03, concrete)
    paint = {"white": (0.75, 0.75, 0.72, 1), "yellow": (0.8, 0.55, 0.06, 1)}
    for colour in ("white", "yellow"):
        m, nt = new_mat(f"paint_{colour}")
        b = principled(nt); b.inputs["Base Color"].default_value = paint[colour]; b.inputs["Roughness"].default_value = 0.45
        quads = []
        for ln in st.get("lines", []):
            if ln["colour"] == colour:
                quads += _ribbon(ln["path"], ln["width"])
        _quads_obj(f"paint_{colour}", D, quads, 0.05, m)
        if colour == "white":
            _tri_obj("crosswalks", D, st.get("crosswalk_tris"), 0.05, m)
    quads = []
    for c in st.get("curbs", []):
        quads += _ribbon(c, 0.18)
    _quads_obj("curbs", D, quads, 0.06, concrete)
    return street_furniture(S, D)


def street_furniture(S, D):
    """Poles, stop signs, traffic-signal heads and street lamps exactly where streets.json places them."""
    st = S.get("streets") or {}
    sigs = st.get("signals", [])
    if not sigs:
        return []
    pole_m, nt = new_mat("pole")
    b = principled(nt); b.inputs["Base Color"].default_value = (0.09, 0.095, 0.1, 1); b.inputs["Metallic"].default_value = 0.7
    b.inputs["Roughness"].default_value = 0.4
    red_m, nt = new_mat("stop_red")
    b = principled(nt); b.inputs["Base Color"].default_value = (0.45, 0.01, 0.01, 1); b.inputs["Roughness"].default_value = 0.35
    box_m, nt = new_mat("signal_box")
    b = principled(nt); b.inputs["Base Color"].default_value = (0.02, 0.02, 0.018, 1); b.inputs["Roughness"].default_value = 0.5
    lamps = []
    for g in sigs:
        x, y, kind = g["x"], g["y"], g.get("kind") or ""
        z = float(_drape(D, np.array([[x, y]]), 0.0)[0, 2])
        hd = math.radians(g.get("heading") or 0.0)
        face = Vector((math.sin(hd), math.cos(hd), 0))
        rot = face.to_track_quat("Z", "Y").to_euler() if kind != "street_lamp" else None
        if kind == "stop_sign":
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.03, depth=2.1, location=(x, y, z + 1.05))
            bpy.context.active_object.data.materials.append(pole_m)
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.38, depth=0.02, location=Vector((x, y, z + 2.1)) + face * 0.04)
            o = bpy.context.active_object; o.rotation_euler = rot; o.rotation_euler.rotate_axis("Z", math.radians(22.5))
            o.data.materials.append(red_m)
        elif kind == "traffic_signal":
            bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=0.09, depth=4.2, location=(x, y, z + 2.1))
            bpy.context.active_object.data.materials.append(pole_m)
            arm = g.get("arm") or {"dir": [-face.y, face.x], "len": 3.0}
            av = Vector((arm["dir"][0], arm["dir"][1], 0)).normalized(); al = float(arm["len"])
            bpy.ops.mesh.primitive_cylinder_add(vertices=8, radius=0.06, depth=al, location=Vector((x, y, z + 5.6)) + av * (al / 2))
            o = bpy.context.active_object; o.rotation_euler = av.to_track_quat("Z", "Y").to_euler(); o.data.materials.append(pole_m)
            bpy.ops.mesh.primitive_cylinder_add(vertices=10, radius=0.09, depth=1.6, location=(x, y, z + 4.9))
            bpy.context.active_object.data.materials.append(pole_m)
            hc = Vector((x, y, z + 5.0)) + av * al
            bpy.ops.mesh.primitive_cube_add(size=1, location=hc)
            o = bpy.context.active_object; o.scale = (0.32, 0.32, 1.0); o.rotation_euler = rot
            o.data.materials.append(box_m)
            g["_head"] = hc + face * 0.18
            g["_face"] = face
            lamps.append(("signal", g))
        elif kind == "street_lamp":
            h = float(g.get("h") or 7.5)
            bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.08, depth=h, location=(x, y, z + h / 2))
            bpy.context.active_object.data.materials.append(pole_m)
            lamps.append(("lamp", {"x": x, "y": y, "z": z, "h": h}))
    return lamps


def sun_vec(el, az):
    e, a = math.radians(el), math.radians(az)
    return Vector((math.sin(a) * math.cos(e), math.cos(a) * math.cos(e), math.sin(e)))


def setup_world(sky_el, sky_az, strength, night=False):
    w = bpy.data.worlds.get("world") or bpy.data.worlds.new("world")
    bpy.context.scene.world = w
    w.use_nodes = True
    nt = w.node_tree
    nt.nodes.clear()
    out = node(nt, "ShaderNodeOutputWorld", (400, 0))
    bg = node(nt, "ShaderNodeBackground", (200, 0))
    sky = node(nt, "ShaderNodeTexSky", (0, 0))
    for t in ("MULTIPLE_SCATTERING", "SINGLE_SCATTERING", "NISHITA", "HOSEK_WILKIE"):
        try:
            sky.sky_type = t; break
        except TypeError:
            continue
    sky.sun_disc = False
    sky.sun_elevation = math.radians(sky_el if night else max(sky_el, 0.0))
    # verified by test renders in Blender 5.3: rotation 0 = sun due north (+Y), -90 deg = west, i.e. rotation = azimuth
    sky.sun_rotation = math.radians(sky_az)
    sky.altitude = 300.0
    if hasattr(sky, "aerosol_density"):
        sky.aerosol_density = 1.6
    link(nt, sky.outputs["Color"], bg.inputs["Color"])
    bg.inputs["Strength"].default_value = strength
    link(nt, bg.outputs["Background"], out.inputs["Surface"])
    return sky, bg


def add_sun(el, az, strength, color, angle=0.53):
    ld = bpy.data.lights.new("sun", "SUN")
    ld.energy = strength; ld.color = color; ld.angle = math.radians(angle)
    ob = bpy.data.objects.new("sun", ld)
    bpy.context.scene.collection.objects.link(ob)
    ob.rotation_euler = sun_vec(el, az).to_track_quat("Z", "Y").to_euler()
    return ob


def sun_colour(el):
    """Approximate direct-beam colour vs elevation (air-mass reddening)."""
    t = max(0.0, min(1.0, (el - 2) / 40))
    warm = Vector((1.0, 0.55, 0.28)); white = Vector((1.0, 0.97, 0.93))
    c = warm.lerp(white, t ** 0.6)
    return (c.x, c.y, c.z)


def add_camera(c, name="cam", ortho=None, dof=None):
    dof = c.get("fstop", dof) if dof is not False else None
    cd = bpy.data.cameras.new(name)
    ob = bpy.data.objects.new(name, cd)
    bpy.context.scene.collection.objects.link(ob)
    loc = Vector(c["loc"]); tgt = Vector(c["target"])
    ob.location = loc
    ob.rotation_euler = (tgt - loc).to_track_quat("-Z", "Y").to_euler()
    cd.lens = c.get("lens", 35)
    cd.clip_start = 0.1; cd.clip_end = 5000
    cd.sensor_width = 36
    if ortho:
        cd.type = "ORTHO"; cd.ortho_scale = ortho
    if dof:
        cd.dof.use_dof = True; cd.dof.focus_distance = (tgt - loc).length; cd.dof.aperture_fstop = dof
    bpy.context.scene.camera = ob
    return ob


def setup_render(res, samples):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    dev = "CPU"
    for t in ("OPTIX", "CUDA"):
        try:
            prefs.compute_device_type = t; prefs.get_devices()
            gpus = [d for d in prefs.devices if d.type == t]
            if gpus:
                for d in prefs.devices:
                    d.use = d.type == t
                dev = t; break
        except Exception:  # noqa: BLE001
            continue
    sc.cycles.device = "GPU" if dev != "CPU" else "CPU"
    sc.cycles.samples = samples
    sc.cycles.use_adaptive_sampling = True
    sc.cycles.adaptive_threshold = 0.008
    sc.cycles.use_denoising = True
    try:
        sc.cycles.denoiser = "OPTIX" if dev == "OPTIX" else "OPENIMAGEDENOISE"
    except TypeError:
        pass
    sc.cycles.max_bounces = 6; sc.cycles.diffuse_bounces = 3; sc.cycles.glossy_bounces = 2; sc.cycles.transmission_bounces = 4
    sc.cycles.caustics_reflective = False; sc.cycles.caustics_refractive = False
    sc.cycles.blur_glossy = 1.0
    w, h = [int(v) for v in res.lower().split("x")]
    sc.render.resolution_x = w; sc.render.resolution_y = h; sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "JPEG"
    sc.render.image_settings.quality = 88
    sc.view_settings.view_transform = "AgX"
    for look in ("AgX - Punchy", "Punchy", "AgX - Medium High Contrast", "Medium High Contrast"):
        try:
            sc.view_settings.look = look; break
        except TypeError:
            continue
    sc.render.film_transparent = False
    print("view transform", sc.view_settings.view_transform, "look", sc.view_settings.look)
    # slight bloom/glare in the compositor (Blender 5 node-group compositor)
    try:
        ng = bpy.data.node_groups.new("comp", "CompositorNodeTree")
        ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        gl = ng.nodes.new("CompositorNodeGlare")
        go = ng.nodes.new("NodeGroupOutput")
        gl.inputs["Type"].default_value = "Bloom"
        gl.inputs["Threshold"].default_value = 1.0
        gl.inputs["Strength"].default_value = 0.12
        gl.inputs["Size"].default_value = 0.6
        ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
        ng.links.new(gl.outputs[0], go.inputs[0])
        sc.compositing_node_group = ng
        sc.render.use_compositing = True
    except Exception as e:  # noqa: BLE001
        print("glare setup skipped:", e)
    return dev


# ------------------------------------------------------------------------------------------------ views
def view_setup(S, D, view, lamps, lamp_objs):
    sc = bpy.context.scene
    for ob in [o for o in sc.objects if o.type in ("LIGHT", "CAMERA") or o.get("night_only")]:
        bpy.data.objects.remove(ob, do_unlink=True)
    sun = S["sun"]; cams = S["cameras"]; half = S["half_m"]
    sc.view_settings.exposure = 0.0
    if view in ("hero", "street"):
        g = sun["golden"]
        setup_world(g["elevation"], g["azimuth"], 0.22)
        add_sun(g["elevation"], g["azimuth"], 8.0, sun_colour(g["elevation"]))
        sc.view_settings.exposure = 0.2 if view == "hero" else 0.4
        add_camera(cams[view], dof=8.0 if view == "hero" else 2.8)
        sc.view_settings.exposure = 0.65 if view == "hero" else 0.75
        return f"golden hour {g['local']} (sun {g['elevation']:.1f} deg up, azimuth {g['azimuth']:.0f} deg)"
    if view == "top":
        g = sun["noon"]
        setup_world(g["elevation"], g["azimuth"], 0.35)
        add_sun(g["elevation"], g["azimuth"], 4.5, sun_colour(g["elevation"]))
        sc.view_settings.exposure = -0.5
        add_camera(cams["top"], ortho=2 * half * 16 / 9 * 1.03, dof=False)
        return f"solar noon {g['local']} (sun {g['elevation']:.1f} deg up)"
    if view == "night":
        g = sun["night"]
        # deep blue hour: physical sky with the sun below the horizon; every warm light is a street lamp / signal from
        # streets.json (no invented window glow or extra fixtures)
        setup_world(g["elevation"], g["azimuth"], 1.0, night=True)
        nl = 0
        emis = {}

        def emissive(name, rgb, strength):
            if name not in emis:
                m, nt = new_mat(name)
                nt.nodes.clear()
                e = node(nt, "ShaderNodeEmission"); e.inputs["Color"].default_value = (*rgb, 1); e.inputs["Strength"].default_value = strength
                o_ = node(nt, "ShaderNodeOutputMaterial", (300, 0)); link(nt, e.outputs[0], o_.inputs[0])
                emis[name] = m
            return emis[name]
        for kind, L in lamps:
            if kind == "lamp":
                x, y, z, h = L["x"], L["y"], L["z"], L["h"]
                ld = bpy.data.lights.new("lamp", "SPOT")
                ld.energy = 1500; ld.color = (1.0, 0.74, 0.45); ld.spot_size = math.radians(130); ld.spot_blend = 0.7
                ld.shadow_soft_size = 0.2
                lo = bpy.data.objects.new("lamp", ld); sc.collection.objects.link(lo)
                lo.location = (x, y, z + h - 0.35)
                bpy.ops.mesh.primitive_uv_sphere_add(radius=0.2, location=(x, y, z + h - 0.15))
                bpy.context.active_object.data.materials.append(emissive("lamp_head", (1, 0.78, 0.5), 30))
                nl += 1
            else:
                hp_, f_ = L["_head"], L["_face"]
                for dz, rgb in ((0.3, (1, 0.05, 0.02)), (0.0, (0.02, 0.02, 0.02)), (-0.3, (0.02, 0.02, 0.02))):
                    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.09, location=hp_ + Vector((0, 0, dz)))
                    bpy.context.active_object.data.materials.append(emissive(f"sig_{dz}", rgb, 25 if dz > 0 else 0.1))
                ld = bpy.data.lights.new("sig", "POINT"); ld.energy = 15; ld.color = (1, 0.1, 0.05); ld.shadow_soft_size = 0.1
                lo = bpy.data.objects.new("sig", ld); sc.collection.objects.link(lo); lo.location = hp_ + f_ * 0.3 + Vector((0, 0, 0.3))
        sc.view_settings.exposure = 2.0 if nl else 4.6
        add_camera(cams["night"])
        return f"night {g['local']} (sun {g['elevation']:.0f} deg), {nl} street lamps from streets.json"
    raise ValueError(view)


def main():
    o = args()
    slug = o["slug"]
    S = json.loads((CACHE / slug / "scene.json").read_text())
    D = dict(np.load(CACHE / slug / "scene.npz"))
    clear()
    dev = setup_render(o["res"], o["samples"])
    image = bpy.data.images.load(S["image"])
    image.colorspace_settings.name = "sRGB"
    build_terrain(S, D, image)
    build_houses(S, D, image)
    build_points(S, D)
    lamps = build_streets(S, D, image)
    lamp_objs = []
    out = Path(o["out"]) if o["out"] else WEB / slug / "renders"
    out.mkdir(parents=True, exist_ok=True)
    log = {}
    for v in o["views"].split(","):
        v = v.strip()
        desc = view_setup(S, D, v, lamps, lamp_objs)
        f = out / f"{v}.jpg"
        bpy.context.scene.render.filepath = str(f)
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        log[v] = {"file": str(f), "seconds": round(time.time() - t0, 1), "lighting": desc, "device": dev, "blender": bpy.app.version_string}
        print(f"[{slug}] {v}: {f} in {time.time() - t0:.0f}s on {dev} - {desc}")
    if o["save_blend"]:
        bpy.ops.wm.save_as_mainfile(filepath=str(CACHE / slug / "scene.blend"))
    lf = CACHE / slug / "render_log.json"
    try:
        old = json.loads(lf.read_text()) if lf.exists() else {}
    except Exception:  # noqa: BLE001
        old = {}
    lf.write_text(json.dumps({**old, **log}, indent=1))  # a partial re-render keeps the other views' records


if __name__ == "__main__":
    main()
