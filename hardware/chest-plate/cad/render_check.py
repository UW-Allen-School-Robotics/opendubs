"""Render the chest plate assembly (from chest_plate_cad.py) in MuJoCo, as a visual check.

    ~/.venvs/mujoco/bin/python cad/render_check.py --parts cad/out --png cad/out/assembly.png

Reads cad/out/assembly/*.stl (already placed in the chest frame) and colours.json. Draws four views:
front, three-quarter, exploded three-quarter, and exploded from the side.
"""
import argparse
import json
import os

import mujoco
import numpy as np
from PIL import Image, ImageDraw

# Exploded view: how far each component moves out of the chest (mm), by name prefix.
EXPLODE = [("face", 60), ("gear", 75), ("magnet_face", 45), ("diffuser", 35), ("centre_mask", 25),
           ("magnet_box", 15), ("screw", 22), ("box", 0), ("ref_light", -25), ("insert", -45), ("ref_chest", -70)]


def _offset(name):
    for pre, dz in EXPLODE:
        if name.startswith(pre):
            return dz
    return 0


def build(asm_dir, explode):
    colours = json.load(open(os.path.join(asm_dir, "colours.json")))
    spec = mujoco.MjSpec()
    spec.meshdir = os.path.abspath(asm_dir)
    spec.visual.global_.offwidth, spec.visual.global_.offheight = 1200, 1000
    spec.visual.headlight.ambient = [0.35, 0.35, 0.35]
    spec.visual.headlight.diffuse = [0.55, 0.55, 0.55]
    w = spec.worldbody
    w.add_light(pos=[0.2, -0.4, 0.3], dir=[-0.4, 1, -0.6], diffuse=[0.6, 0.6, 0.6])
    w.add_light(pos=[-0.3, -0.3, 0.1], dir=[0.6, 1, -0.1], diffuse=[0.3, 0.3, 0.3])
    # chest frame (x right, y up, z out of the chest) -> world (x right, y into the chest, z up)
    q = np.zeros(4)
    mujoco.mju_mat2Quat(q, np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], float).flatten())
    root = w.add_body(name="chest", quat=q)
    for name, rgba in colours.items():
        if explode is None and name.startswith("ref_chest"):
            rgba = [rgba[0], rgba[1], rgba[2], 1.0]
        spec.add_mesh(name=name, file=f"{name}.stl", scale=[0.001] * 3)
        dz = _offset(name) * explode if explode else 0
        root.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_MESH, meshname=name, pos=[0, 0, dz / 1000], rgba=rgba)
    return spec.compile()


def view(model, az, el, dist, look=(0, 0, 0), w=1200, h=1000):
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    cam = mujoco.MjvCamera()
    cam.lookat[:] = look
    cam.azimuth, cam.elevation, cam.distance = az, el, dist
    with mujoco.Renderer(model, h, w) as r:
        r.update_scene(data, cam)
        return r.render()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parts", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
    ap.add_argument("--png", default=None)
    a = ap.parse_args()
    asm = os.path.join(a.parts, "assembly")
    m0, m1 = build(asm, None), build(asm, 1.0)
    shots = [("front", view(m0, 90, 0, 0.2)),
             ("three-quarter", view(m0, 125, 25, 0.22)),
             ("exploded", view(m1, 140, 20, 0.36, look=(0, 0.0, 0))),
             ("exploded, side", view(m1, 180, 5, 0.36, look=(0, 0.0, 0)))]
    tiles = []
    for title, img in shots:
        im = Image.fromarray(img).resize((600, 500))
        ImageDraw.Draw(im).text((10, 10), title, fill=(255, 255, 255))
        tiles.append(im)
    sheet = Image.new("RGB", (1200, 1000))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 2) * 600, (i // 2) * 500))
    out = a.png or os.path.join(a.parts, "assembly.png")
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
