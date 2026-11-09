# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
"""Pure-numpy mesh building and file writing (no QGIS imports, so it is testable).

Coordinate convention: model units are millimetres. The height grid ``z`` has
shape (rows, cols); row 0 is the north edge. Node (r, c) sits at
x = c * dx, y = (rows - 1 - r) * dy. The model's bottom is at z = 0.
"""
import io
import zipfile

import numpy as np


# --------------------------------------------------------------------------- #
# Size estimates
# --------------------------------------------------------------------------- #
def count_triangles(rows, cols):
    """Triangles in the closed mesh: top surface + walls + bottom fan."""
    perimeter = 2 * (rows - 1) + 2 * (cols - 1)
    return 2 * (rows - 1) * (cols - 1) + 3 * perimeter


def count_vertices(rows, cols):
    perimeter = 2 * (rows - 1) + 2 * (cols - 1)
    return rows * cols + perimeter + 1


def estimate_file_bytes(rows, cols, fmt="stl"):
    tris = count_triangles(rows, cols)
    if fmt == "stl":
        return 84 + 50 * tris
    # 3MF: indexed XML, deflated. Ratio calibrated on terrain test meshes.
    verts = count_vertices(rows, cols)
    raw = verts * 48 + tris * 42
    return int(raw * 0.16)


# --------------------------------------------------------------------------- #
# Track rasterisation
# --------------------------------------------------------------------------- #
def smooth_line(pts, sigma_mm, step_mm):
    """Gaussian-smooth a polyline by arc length (sigma in mm).

    The line is first resampled every ``step_mm`` so the result does not
    depend on how densely the GPS logged points. Ends stay in place.
    """
    pts = np.asarray(pts, dtype=np.float64)
    if sigma_mm <= 0 or len(pts) < 3:
        return pts
    seg = np.hypot(*np.diff(pts, axis=0).T)
    pts = pts[np.concatenate([[True], seg > 0])]          # drop duplicate points
    if len(pts) < 3:
        return pts
    s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    n = max(int(np.ceil(s[-1] / step_mm)), 2) + 1
    t = np.linspace(0.0, s[-1], n)
    x = np.interp(t, s, pts[:, 0])
    y = np.interp(t, s, pts[:, 1])

    sig = sigma_mm / (s[-1] / (n - 1))                     # sigma in samples
    r = max(1, int(np.ceil(3 * sig)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sig) ** 2)
    k /= k.sum()
    xs = np.convolve(np.pad(x, r, mode="edge"), k, mode="valid")
    ys = np.convolve(np.pad(y, r, mode="edge"), k, mode="valid")
    xs[0], ys[0], xs[-1], ys[-1] = x[0], y[0], x[-1], y[-1]
    return np.column_stack([xs, ys])


def track_weights(rows, cols, dx, dy, lines, width_mm):
    """Per-node track strength in 0..1 (1 = full track height).

    Nodes within width/2 of the line get ~1, falling off linearly over one
    grid cell at the edges. That anti-aliasing turns the grid "staircase"
    into a smooth slope. ``lines`` are (N, 2) arrays in model mm.
    """
    cell = max(dx, dy)
    hw = max(width_mm / 2.0, 0.5 * cell)
    reach = hw + cell / 2.0
    dist = np.full((rows, cols), np.inf)
    _track_distance(dist, dx, dy, lines, reach)
    return np.clip((reach - dist) / cell, 0.0, 1.0)


def track_mask(rows, cols, dx, dy, lines, width_mm):
    """Boolean version of :func:`track_weights` (nodes at least half on the track)."""
    return track_weights(rows, cols, dx, dy, lines, width_mm) >= 0.5


def _track_distance(dist, dx, dy, lines, hw):
    """Fill ``dist`` (in place) with distance to the nearest line, within ``hw``."""
    rows, cols = dist.shape
    xs = np.arange(cols) * dx
    ys = (rows - 1 - np.arange(rows)) * dy

    for line in lines:
        line = np.asarray(line, dtype=np.float64)
        if len(line) == 1:
            line = np.vstack([line, line])
        for (x0, y0), (x1, y1) in zip(line[:-1], line[1:]):
            c0 = max(0, int(np.floor((min(x0, x1) - hw) / dx)))
            c1 = min(cols - 1, int(np.ceil((max(x0, x1) + hw) / dx)))
            r0 = max(0, int(np.floor(rows - 1 - (max(y0, y1) + hw) / dy)))
            r1 = min(rows - 1, int(np.ceil(rows - 1 - (min(y0, y1) - hw) / dy)))
            if c0 > c1 or r0 > r1:
                continue
            X = xs[c0:c1 + 1][None, :]
            Y = ys[r0:r1 + 1][:, None]
            vx, vy = x1 - x0, y1 - y0
            l2 = vx * vx + vy * vy
            if l2 == 0:
                t = 0.0
            else:
                t = np.clip(((X - x0) * vx + (Y - y0) * vy) / l2, 0.0, 1.0)
            d = np.sqrt((X - x0 - t * vx) ** 2 + (Y - y0 - t * vy) ** 2)
            box = dist[r0:r1 + 1, c0:c1 + 1]
            np.minimum(box, d, out=box)


# --------------------------------------------------------------------------- #
# Mesh
# --------------------------------------------------------------------------- #
def build_mesh(z, dx, dy):
    """Closed, outward-oriented indexed mesh from a height grid.

    Returns (vertices float64 (V,3), faces int64 (F,3)).
    """
    z = np.asarray(z, dtype=np.float64)
    rows, cols = z.shape
    if rows < 2 or cols < 2:
        raise ValueError("Grid must be at least 2 x 2")
    if np.any(z <= 0):
        raise ValueError("All heights must be above the model bottom (z > 0)")

    xs = np.arange(cols) * dx
    ys = (rows - 1 - np.arange(rows)) * dy
    X, Y = np.meshgrid(xs, ys)
    top = np.column_stack([X.ravel(), Y.ravel(), z.ravel()])

    I = np.arange(rows * cols).reshape(rows, cols)
    a, b = I[:-1, :-1], I[:-1, 1:]
    c, d = I[1:, :-1], I[1:, 1:]
    top_faces = np.concatenate([
        np.stack([c, d, b], -1).reshape(-1, 3),
        np.stack([c, b, a], -1).reshape(-1, 3),
    ])

    # Perimeter loop, counter-clockwise seen from above, starting at SW corner.
    P = np.concatenate([
        I[-1, :-1],        # south edge, west -> east
        I[::-1, -1][:-1],  # east edge, south -> north
        I[0, ::-1][:-1],   # north edge, east -> west
        I[:, 0][:-1],      # west edge, north -> south
    ])
    n = len(P)
    bottom = top[P].copy()
    bottom[:, 2] = 0.0
    centre = np.array([[xs[-1] / 2.0, ys[0] / 2.0, 0.0]])

    q0 = rows * cols
    Q = q0 + np.arange(n)
    C = q0 + n
    P1 = np.roll(P, -1)
    Q1 = np.roll(Q, -1)

    wall_faces = np.concatenate([
        np.column_stack([Q, Q1, P1]),
        np.column_stack([Q, P1, P]),
    ])
    bottom_faces = np.column_stack([np.full(n, C), Q1, Q])

    vertices = np.vstack([top, bottom, centre])
    faces = np.vstack([top_faces, wall_faces, bottom_faces]).astype(np.int64)
    return vertices, faces


# --------------------------------------------------------------------------- #
# Writers
# --------------------------------------------------------------------------- #
def write_stl(path, vertices, faces, name="TerrainPrint3D"):
    tri = vertices[faces].astype(np.float32)            # (F, 3, 3)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals),
                        where=lengths > 0)

    rec = np.zeros(len(faces), dtype=np.dtype([
        ("normal", "<f4", (3,)), ("verts", "<f4", (3, 3)), ("attr", "<u2"),
    ]))
    rec["normal"] = normals
    rec["verts"] = tri
    with open(path, "wb") as f:
        f.write(name.encode("ascii", "replace")[:80].ljust(80, b" "))
        f.write(np.uint32(len(faces)).tobytes())
        f.write(rec.tobytes())


_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""


def write_3mf(path, vertices, faces, name="TerrainPrint3D"):
    buf = io.StringIO()
    buf.write('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<model unit="millimeter" xml:lang="en-US" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
              '<metadata name="Title">%s</metadata>\n'
              '<resources><object id="1" type="model"><mesh><vertices>\n'
              % _xml_escape(name))
    np.savetxt(buf, vertices, fmt='<vertex x="%.3f" y="%.3f" z="%.3f"/>')
    buf.write("</vertices><triangles>\n")
    np.savetxt(buf, faces, fmt='<triangle v1="%d" v2="%d" v3="%d"/>')
    buf.write('</triangles></mesh></object></resources>\n'
              '<build><item objectid="1"/></build></model>\n')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("[Content_Types].xml", _CONTENT_TYPES)
        zf.writestr("_rels/.rels", _RELS)
        zf.writestr("3D/3dmodel.model", buf.getvalue())


def _xml_escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))
