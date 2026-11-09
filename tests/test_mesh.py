"""Run with QGIS's Python:  /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/test_mesh.py"""
import os
import struct
import sys
import tempfile
import zipfile
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "TerrainPrint3D"))
import mesh  # noqa: E402


def check_closed_and_oriented(v, f):
    edges = Counter()
    for tri in f:
        for i in range(3):
            edges[(tri[i], tri[(i + 1) % 3])] += 1
    for (a, b), n in edges.items():
        assert n == 1, "edge %s used %d times in same direction" % ((a, b), n)
        assert edges.get((b, a)) == 1, "edge %s has no opposite partner" % ((a, b),)
    # Positive signed volume => normals point outward.
    t = v[f]
    vol = np.einsum("ij,ij->i", t[:, 0], np.cross(t[:, 1], t[:, 2])).sum() / 6
    assert vol > 0, "mesh is inside-out (volume %f)" % vol
    return vol


def test_flat_box_volume():
    z = np.full((11, 21), 5.0)
    v, f = mesh.build_mesh(z, 1.0, 1.0)
    vol = check_closed_and_oriented(v, f)
    assert abs(vol - 20 * 10 * 5) < 1e-6, vol
    assert len(f) == mesh.count_triangles(11, 21)
    assert len(v) == mesh.count_vertices(11, 21)


def test_random_terrain():
    rng = np.random.default_rng(1)
    z = 3 + rng.random((37, 23)) * 10
    v, f = mesh.build_mesh(z, 0.4, 0.7)
    check_closed_and_oriented(v, f)


def test_track_mask():
    m = mesh.track_mask(50, 100, 1.0, 1.0, [np.array([[0, 25], [99, 25]])], 2.0)
    # Row for y=25 is rows-1-25 = 24
    assert m[24].all()
    assert not m[10].any()
    assert m.sum() < 100 * 4


def test_track_weights_are_antialiased():
    w = mesh.track_weights(50, 100, 1.0, 1.0, [np.array([[0, 25.3], [99, 25.3]])], 3.0)
    assert w.max() == 1.0 and w.min() == 0.0
    partial = ((w > 0) & (w < 1)).sum()
    assert partial > 0, "edges should slope, not step"


def test_smooth_line_removes_zigzag_keeps_ends():
    x = np.linspace(0, 100, 201)
    y = 50 + np.where(np.arange(201) % 2, 1.0, -1.0)   # +/-1 mm zig-zag
    pts = np.column_stack([x, y])
    sm = mesh.smooth_line(pts, 2.0, 0.25)
    inner = sm[len(sm) // 10: -len(sm) // 10]
    assert np.abs(inner[:, 1] - 50).max() < 0.1, np.abs(inner[:, 1] - 50).max()
    assert np.allclose(sm[0], pts[0]) and np.allclose(sm[-1], pts[-1])
    assert np.array_equal(mesh.smooth_line(pts, 0, 0.25), pts)


def test_writers_and_size_estimates():
    rng = np.random.default_rng(2)
    rows, cols = 300, 400
    # smooth-ish terrain, like a real DEM
    yy, xx = np.mgrid[0:rows, 0:cols]
    z = 5 + 10 * np.sin(xx / 37.0) * np.cos(yy / 23.0) + rng.random((rows, cols)) * 0.3 + 10
    v, f = mesh.build_mesh(z, 0.5, 0.5)
    with tempfile.TemporaryDirectory() as d:
        stl = os.path.join(d, "t.stl")
        mf = os.path.join(d, "t.3mf")
        mesh.write_stl(stl, v, f)
        mesh.write_3mf(mf, v, f)
        stl_size, mf_size = os.path.getsize(stl), os.path.getsize(mf)
        assert stl_size == mesh.estimate_file_bytes(rows, cols, "stl")
        with open(stl, "rb") as fh:
            fh.seek(80)
            assert struct.unpack("<I", fh.read(4))[0] == len(f)
        with zipfile.ZipFile(mf) as zf:
            assert "3D/3dmodel.model" in zf.namelist()
        est = mesh.estimate_file_bytes(rows, cols, "3mf")
        print("  STL %.1f MB, 3MF %.1f MB (estimate %.1f MB, %.1fx smaller)"
              % (stl_size / 1e6, mf_size / 1e6, est / 1e6, stl_size / mf_size))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
