"""Shaded PNG renders from STL meshes, with no GPU or display.

A small orthographic z-buffer rasteriser in numpy: flat shading, per-part
colours, and dark feature edges found from depth and normal discontinuities.
Good enough for an agent (or a person on a phone) to see what it built.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

import numpy as np
from PIL import Image


def read_stl(path: Path) -> np.ndarray:
    data = Path(path).read_bytes()
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        verts = [list(map(float, ln.split()[1:4])) for ln in data.decode().splitlines()
                 if ln.strip().startswith("vertex")]
        return np.array(verts).reshape(-1, 3, 3)
    n = struct.unpack("<I", data[80:84])[0]
    rec = np.frombuffer(data[84:84 + 50 * n], dtype=np.dtype(
        [("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
    return rec["v"].astype(float)


def _view_basis(toward, up=(0, 0, 1)):
    t = np.array(toward, float)
    t /= np.linalg.norm(t)
    r = np.cross(np.array(up, float), t)
    r /= np.linalg.norm(r)
    u = np.cross(t, r)
    return r, u, t


def render(meshes, toward=(1.0, -1.3, 0.9), size=(1400, 1000), ss=2, margin=0.06,
           background=(247, 247, 245), light=(-0.4, -0.6, 1.0), edges=True) -> Image.Image:
    """meshes: list of (triangles[n,3,3], rgb)."""
    r, u, t = _view_basis(toward)
    W, H = size[0] * ss, size[1] * ss
    allv = np.concatenate([m.reshape(-1, 3) for m, _ in meshes])
    sx, sy = allv @ r, allv @ u
    span = max((sx.max() - sx.min()) / (W * (1 - 2 * margin)),
               (sy.max() - sy.min()) / (H * (1 - 2 * margin)))
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2

    depth = np.full((H, W), -np.inf)
    color = np.zeros((H, W, 3))
    color[:] = np.array(background) / 255.0
    nbuf = np.zeros((H, W, 3))
    idbuf = np.full((H, W), -1, dtype=np.int32)
    L = np.array(light, float)
    L /= np.linalg.norm(L)
    # Express the light in view space so shading is stable across views.
    Lw = L[0] * r + L[1] * u + L[2] * t

    for mid, (tris, rgb) in enumerate(meshes):
        base = np.array(rgb, float) / 255.0
        X = (tris @ r - cx) / span + W / 2
        Y = H / 2 - (tris @ u - cy) / span
        Z = tris @ t
        nrm = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
        ln = np.linalg.norm(nrm, axis=1)
        keep = ln > 1e-12
        nrm[keep] /= ln[keep, None]
        facing = nrm @ t
        nrm[facing < 0] *= -1  # thin sheets: shade both sides
        shade = 0.28 + 0.62 * np.clip(nrm @ Lw, 0, 1) + 0.10 * np.clip(nrm @ t, 0, 1)
        for i in np.nonzero(keep)[0]:
            x, y, z = X[i], Y[i], Z[i]
            x0, x1 = max(int(math.floor(x.min())), 0), min(int(math.ceil(x.max())), W - 1)
            y0, y1 = max(int(math.floor(y.min())), 0), min(int(math.ceil(y.max())), H - 1)
            if x0 > x1 or y0 > y1:
                continue
            px, py = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
            d = (y[1] - y[2]) * (x[0] - x[2]) + (x[2] - x[1]) * (y[0] - y[2])
            if abs(d) < 1e-12:
                continue
            w0 = ((y[1] - y[2]) * (px - x[2]) + (x[2] - x[1]) * (py - y[2])) / d
            w1 = ((y[2] - y[0]) * (px - x[2]) + (x[0] - x[2]) * (py - y[2])) / d
            w2 = 1 - w0 - w1
            inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
            if not inside.any():
                continue
            zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
            sub = depth[y0:y1 + 1, x0:x1 + 1]
            win = inside & (zz > sub)
            if not win.any():
                continue
            sub[win] = zz[win]
            color[y0:y1 + 1, x0:x1 + 1][win] = np.clip(base * shade[i], 0, 1)
            nbuf[y0:y1 + 1, x0:x1 + 1][win] = nrm[i]
            idbuf[y0:y1 + 1, x0:x1 + 1][win] = mid

    if edges:
        filled = np.isfinite(depth)
        dz = np.where(filled, depth, depth[filled].min() - 50 * span if filled.any() else 0)
        e = np.zeros((H, W), bool)
        for ax in (0, 1):
            ddepth = np.abs(np.diff(dz, axis=ax)) > 1.5 * span * 2.5
            dn = np.linalg.norm(np.diff(nbuf, axis=ax), axis=2) > 0.35
            did = np.diff(idbuf, axis=ax) != 0
            m = ddepth | dn | did
            if ax == 0:
                e[:-1] |= m
                e[1:] |= m
            else:
                e[:, :-1] |= m
                e[:, 1:] |= m
        color[e] *= 0.35

    img = Image.fromarray((np.clip(color, 0, 1) * 255).astype(np.uint8))
    return img.resize(size, Image.LANCZOS) if ss > 1 else img
