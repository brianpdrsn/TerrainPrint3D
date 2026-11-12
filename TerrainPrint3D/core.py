# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
"""QGIS-side logic: sampling the DEM, reading tracks, assembling the model.

Kept free of widgets so it can be run headless (see tests/test_core.py).
"""
import math
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
from qgis.core import (
    QgsCoordinateTransform,
    QgsDistanceArea,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRasterLayer,
    QgsRectangle,
    QgsVectorLayer,
)

from . import mesh


@dataclass
class ModelParams:
    dem_layer: QgsRasterLayer
    extent: QgsRectangle            # in the DEM layer's CRS
    width_mm: float = 150.0
    exaggeration: float = 1.5
    base_mm: float = 3.0
    resolution_mm: float = 0.4
    fmt: str = "3mf"                # "3mf" or "stl"
    band: int = 1
    track_layer: Optional[QgsVectorLayer] = None
    track_mode: str = "raise"       # "raise" or "engrave"
    track_height_mm: float = 1.0
    track_width_mm: float = 1.2
    track_smooth_mm: float = 1.0     # Gaussian sigma along the path, 0 = off
    rotation_deg: float = 0.0        # model 'up' bearing, clockwise from north


@dataclass
class ModelGeometry:
    ground_w_m: float
    ground_h_m: float
    width_mm: float
    depth_mm: float
    rows: int
    cols: int
    scale: float                    # 1 : scale
    mm_per_m: float                 # horizontal model mm per ground metre
    triangles: int
    file_bytes: int


# --------------------------------------------------------------------------- #
def distance_area(crs):
    """Ellipsoidal distance calculator for a CRS (results in metres)."""
    da = QgsDistanceArea()
    da.setSourceCrs(crs, QgsProject.instance().transformContext())
    if not da.setEllipsoid(crs.ellipsoidAcronym() or "WGS84"):
        da.setEllipsoid("WGS84")
    return da


def ground_size_m(extent, crs):
    """Real-world width/height of the extent in metres (ellipsoidal)."""
    da = distance_area(crs)
    c = extent.center()
    w = da.measureLine(QgsPointXY(extent.xMinimum(), c.y()),
                       QgsPointXY(extent.xMaximum(), c.y()))
    h = da.measureLine(QgsPointXY(c.x(), extent.yMinimum()),
                       QgsPointXY(c.x(), extent.yMaximum()))
    return w, h


def square_extent(center, side_m, crs):
    """Rectangle in ``crs`` centred on ``center`` that is side_m x side_m on the ground.

    Measured the same way as :func:`ground_size_m`, so the model comes out
    exactly square. Works for any CRS (metres, feet, degrees, Web Mercator)
    by refining the half-spans until the measured sides match.
    """
    cx, cy = center.x(), center.y()
    if crs.isGeographic():
        hy = side_m / 2.0 / 111320.0
        hx = hy / max(math.cos(math.radians(cy)), 0.01)
    else:
        hx = hy = side_m / 2.0
    for _ in range(6):
        rect = QgsRectangle(cx - hx, cy - hy, cx + hx, cy + hy)
        w, h = ground_size_m(rect, crs)
        if not (w > 0 and h > 0):
            break
        hx *= side_m / w
        hy *= side_m / h
        if abs(w - side_m) < side_m * 1e-6 and abs(h - side_m) < side_m * 1e-6:
            break
    return QgsRectangle(cx - hx, cy - hy, cx + hx, cy + hy)


def check_dem(layer, band=1):
    """Raise a friendly error if the layer can't hold elevation values."""
    dt = int(layer.dataProvider().dataType(band))
    if dt in (12, 13):   # ARGB32 / ARGB32_Premultiplied: rendered colour images
        raise ValueError(
            '"%s" is a colour image (for example a basemap, WMS or hillshade), '
            "not elevation data. Choose a DEM layer whose pixels are heights."
            % layer.name())
    if dt not in _DTYPES:
        raise ValueError('"%s" has an unsupported data type for elevation.' % layer.name())


def model_geometry(p):
    check_dem(p.dem_layer, p.band)
    if p.extent is None or p.extent.isEmpty():
        raise ValueError("The extent is empty.")
    w_m, h_m = ground_size_m(p.extent, p.dem_layer.crs())
    if w_m <= 0 or h_m <= 0 or math.isnan(w_m) or math.isnan(h_m):
        raise ValueError("Could not measure the extent. Check the layer's CRS.")
    depth_mm = p.width_mm * h_m / w_m
    cols = max(2, int(round(p.width_mm / p.resolution_mm)) + 1)
    rows = max(2, int(round(depth_mm / p.resolution_mm)) + 1)
    mm_per_m = p.width_mm / w_m
    return ModelGeometry(
        ground_w_m=w_m, ground_h_m=h_m,
        width_mm=p.width_mm, depth_mm=depth_mm,
        rows=rows, cols=cols,
        scale=1000.0 / mm_per_m, mm_per_m=mm_per_m,
        triangles=mesh.count_triangles(rows, cols),
        file_bytes=mesh.estimate_file_bytes(rows, cols, p.fmt),
    )


# --------------------------------------------------------------------------- #
class Frame:
    """Maps model millimetres <-> DEM CRS coordinates.

    ``extent`` is the area *before* rotation; ``rotation_deg`` turns it
    clockwise around its centre, so it is the compass bearing of the
    model's top edge. Distances are true metres (local linearisation at the
    centre, the same measure used for the scale), so squares stay square.
    """

    def __init__(self, extent, crs, width_mm, rotation_deg=0.0):
        w_m, h_m = ground_size_m(extent, crs)
        c = extent.center()
        self.crs = crs
        self.cx, self.cy = c.x(), c.y()
        self.sx = w_m / extent.width()      # metres per CRS unit, east-west
        self.sy = h_m / extent.height()     # metres per CRS unit, north-south
        self.W = float(width_mm)
        self.D = width_mm * h_m / w_m
        self.mm_per_m = width_mm / w_m
        t = math.radians(rotation_deg)
        self.cos, self.sin = math.cos(t), math.sin(t)

    def to_crs(self, x_mm, y_mm):
        e0 = (np.asarray(x_mm, dtype=np.float64) - self.W / 2) / self.mm_per_m
        n0 = (np.asarray(y_mm, dtype=np.float64) - self.D / 2) / self.mm_per_m
        e = e0 * self.cos + n0 * self.sin
        n = -e0 * self.sin + n0 * self.cos
        return self.cx + e / self.sx, self.cy + n / self.sy

    def to_model(self, X, Y):
        e = (np.asarray(X, dtype=np.float64) - self.cx) * self.sx
        n = (np.asarray(Y, dtype=np.float64) - self.cy) * self.sy
        e0 = e * self.cos - n * self.sin
        n0 = e * self.sin + n * self.cos
        return e0 * self.mm_per_m + self.W / 2, n0 * self.mm_per_m + self.D / 2

    def grid(self, rows, cols):
        """CRS coordinates of every model grid node (row 0 = model top edge)."""
        X, Y = np.meshgrid(np.linspace(0, self.W, cols), np.linspace(self.D, 0, rows))
        return self.to_crs(X, Y)

    def corners(self):
        """CRS corner points: model bottom-left, bottom-right, top-right, top-left."""
        X, Y = self.to_crs([0, self.W, self.W, 0], [0, 0, self.D, self.D])
        return [QgsPointXY(x, y) for x, y in zip(X, Y)]

    def bbox(self):
        pts = self.corners()
        xs, ys = [p.x() for p in pts], [p.y() for p in pts]
        return QgsRectangle(min(xs), min(ys), max(xs), max(ys))

    def polygon(self):
        pts = self.corners()
        return QgsGeometry.fromPolygonXY([pts + [pts[0]]])

    def up_marker(self):
        """Line from the centre to just past the middle of the model's top edge."""
        X, Y = self.to_crs([self.W / 2, self.W / 2],
                           [self.D / 2, self.D + 0.08 * min(self.W, self.D)])
        return QgsGeometry.fromPolylineXY([QgsPointXY(X[0], Y[0]), QgsPointXY(X[1], Y[1])])


def make_frame(p):
    return Frame(p.extent, p.dem_layer.crs(), p.width_mm, p.rotation_deg)


def inside_dem(p):
    """True if the (rotated) area lies within the DEM's extent.

    Allows a tolerance of 1e-6 pixel: the frame's corners are recomputed in
    floating point, so an extent equal to the DEM's can come out a few ulps
    larger and would otherwise fail a strict containment test.
    """
    layer = p.dem_layer
    tol = 1e-6 * max(layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY(), 0.0)
    if not tol > 0:
        tol = 1e-9 * max(layer.extent().width(), layer.extent().height())
    bb = make_frame(p).bbox()
    ext = layer.extent()
    return (bb.xMinimum() >= ext.xMinimum() - tol and bb.xMaximum() <= ext.xMaximum() + tol
            and bb.yMinimum() >= ext.yMinimum() - tol and bb.yMaximum() <= ext.yMaximum() + tol)


def sample_dem(layer, frame, rows, cols, band=1):
    """Elevations at the model grid nodes (row 0 = top edge). NoData -> minimum.

    Reads raw pixels over the (rotated) area's bounding box at no more than
    ~2x the model's sample density, fills NoData, then interpolates
    bilinearly in numpy. Provider-side resampling is avoided because it
    smears NoData values.
    """
    X, Y = frame.grid(rows, cols)
    spacing_m = frame.W / (cols - 1) / frame.mm_per_m
    want_x = spacing_m / 2 / frame.sx          # ~2 source pixels per model sample
    want_y = spacing_m / 2 / frame.sy

    provider = layer.dataProvider()
    if provider.xSize() > 0 and provider.ySize() > 0:
        # Work on the DEM's own pixel grid and keep the centre pixel of every
        # k x k group (k odd), so each value is exactly a native pixel centre.
        nat_x, nat_y = layer.rasterUnitsPerPixelX(), layer.rasterUnitsPerPixelY()
        ox, oy = layer.extent().xMinimum(), layer.extent().yMaximum()
        kx = _odd_factor(want_x / nat_x)
        ky = _odd_factor(want_y / nat_y)
        px_x, px_y = nat_x * kx, nat_y * ky
        bx0 = ox + math.floor((X.min() - ox) / px_x - 1) * px_x
        by1 = oy - math.floor((oy - Y.max()) / px_y - 1) * px_y
        src_cols = max(2, int(math.ceil((X.max() - bx0) / px_x)) + 1)
        src_rows = max(2, int(math.ceil((by1 - Y.min()) / px_y)) + 1)
        bx1 = bx0 + src_cols * px_x
        by0 = by1 - src_rows * px_y
        src, nodata = _read_decimated(provider, band, bx0, bx1, by1, nat_y,
                                      kx, ky, src_cols, src_rows)
    else:                                      # no fixed resolution (e.g. web services)
        px_x, px_y = want_x, want_y
        bx0, bx1 = X.min() - px_x, X.max() + px_x
        by0, by1 = Y.min() - px_y, Y.max() + px_y
        src_cols = max(2, int(math.ceil((bx1 - bx0) / px_x)))
        src_rows = max(2, int(math.ceil((by1 - by0) / px_y)))
        block = provider.block(band, QgsRectangle(bx0, by0, bx1, by1), src_cols, src_rows)
        if not block.isValid():
            raise ValueError("Could not read elevation data from the DEM.")
        src, nodata = _block_to_array(block, src_rows, src_cols)

    nodata |= ~np.isfinite(src)
    if nodata.all():
        raise ValueError("There is no elevation data in the selected extent.")
    src[nodata] = src[~nodata].min()

    fx = (X - bx0) / ((bx1 - bx0) / src_cols) - 0.5
    fy = (by1 - Y) / ((by1 - by0) / src_rows) - 0.5
    return _bilinear_at(src, fx, fy)


def _read_decimated(provider, band, bx0, bx1, by1, nat_y, kx, ky, cols, rows,
                    max_pixels=16_000_000):
    """Read an exactly grid-aligned area at native resolution, in horizontal
    strips to bound memory, keeping the centre pixel of each kx x ky group."""
    nat_cols = cols * kx
    strip = max(1, max_pixels // (nat_cols * ky))      # output rows per strip
    out = np.empty((rows, cols))
    mask = np.empty((rows, cols), dtype=bool)
    for r0 in range(0, rows, strip):
        r1 = min(rows, r0 + strip)
        top = by1 - r0 * ky * nat_y
        bottom = by1 - r1 * ky * nat_y
        n_rows = (r1 - r0) * ky
        block = provider.block(band, QgsRectangle(bx0, bottom, bx1, top), nat_cols, n_rows)
        if not block.isValid():
            raise ValueError("Could not read elevation data from the DEM.")
        arr, m = _block_to_array(block, n_rows, nat_cols)
        out[r0:r1] = arr[ky // 2::ky, kx // 2::kx]
        mask[r0:r1] = m[ky // 2::ky, kx // 2::kx]
    return out, mask


def _odd_factor(ratio):
    """Largest odd integer <= ratio (at least 1)."""
    k = max(1, int(math.floor(ratio)))
    return k if k % 2 else k - 1


def _bilinear_at(src, fx, fy):
    """Bilinear sample of ``src`` at fractional pixel coords (pixel centres at .0)."""
    sr, sc = src.shape
    fx = np.clip(fx, 0, sc - 1)
    fy = np.clip(fy, 0, sr - 1)
    x0 = np.floor(fx).astype(np.intp)
    y0 = np.floor(fy).astype(np.intp)
    x1 = np.minimum(x0 + 1, sc - 1)
    y1 = np.minimum(y0 + 1, sr - 1)
    wx = fx - x0
    wy = fy - y0
    top = src[y0, x0] * (1 - wx) + src[y0, x1] * wx
    bot = src[y1, x0] * (1 - wx) + src[y1, x1] * wx
    return top * (1 - wy) + bot * wy


_DTYPES = {1: np.uint8, 2: np.uint16, 3: np.int16, 4: np.uint32,
           5: np.int32, 6: np.float32, 7: np.float64, 14: np.int8}


def _block_to_array(block, rows, cols):
    if hasattr(block, "as_numpy"):                       # QGIS >= 3.34
        ma = block.as_numpy(use_masking=True)
        arr = np.ma.getdata(ma).astype(np.float64).reshape(rows, cols)
        mask = np.ma.getmaskarray(ma).reshape(rows, cols).copy()
    else:
        dt = _DTYPES.get(int(block.dataType()))
        if dt is None:
            raise ValueError("Unsupported raster data type.")
        arr = np.frombuffer(bytes(block.data()), dtype=dt).astype(np.float64)
        arr = arr.reshape(rows, cols)
        mask = np.zeros_like(arr, dtype=bool)
        if block.hasNoDataValue():
            mask |= arr == block.noDataValue()
    return arr, mask


def relief_range(p):
    """Quick min/max elevation over the (rotated) area from a coarse sample."""
    try:
        z = sample_dem(p.dem_layer, make_frame(p), 64, 64, p.band)
        return float(z.min()), float(z.max())
    except Exception:
        return None


# --------------------------------------------------------------------------- #
def track_lines_mm(track_layer, frame):
    """Track polylines converted to model millimetre coordinates."""
    xform = QgsCoordinateTransform(track_layer.crs(), frame.crs, QgsProject.instance())
    lines = []
    for feat in track_layer.getFeatures():
        geom = QgsGeometry(feat.geometry())
        if geom.isEmpty():
            continue
        geom.transform(xform)
        for part in geom.parts():
            pts = np.array([(v.x(), v.y()) for v in part.vertices()], dtype=np.float64)
            if len(pts):
                x, y = frame.to_model(pts[:, 0], pts[:, 1])
                lines.append(np.column_stack([x, y]))
    return lines


# --------------------------------------------------------------------------- #
def build_model(p, progress=None):
    """Return (vertices, faces, ModelGeometry, info dict)."""
    def step(pct, msg):
        if progress:
            progress(pct, msg)

    g = model_geometry(p)
    frame = make_frame(p)
    dx = g.width_mm / (g.cols - 1)
    dy = g.depth_mm / (g.rows - 1)

    step(5, "Reading elevation data…")
    elev = sample_dem(p.dem_layer, frame, g.rows, g.cols, p.band)
    zmin, zmax = float(elev.min()), float(elev.max())
    z = p.base_mm + (elev - zmin) * g.mm_per_m * p.exaggeration

    track_cells = 0
    if p.track_layer is not None:
        step(35, "Adding track…")
        lines = track_lines_mm(p.track_layer, frame)
        if p.track_smooth_mm > 0:
            spacing = min(p.track_smooth_mm / 3.0, min(dx, dy) / 2.0)
            lines = [mesh.smooth_line(ln, p.track_smooth_mm, spacing) for ln in lines]
        w = mesh.track_weights(g.rows, g.cols, dx, dy, lines, p.track_width_mm)
        track_cells = int((w > 0).sum())
        if p.track_mode == "engrave":
            z -= w * p.track_height_mm
            z = np.maximum(z, p.base_mm * 0.5)
        else:
            z += w * p.track_height_mm

    step(55, "Building mesh…")
    vertices, faces = mesh.build_mesh(z, dx, dy)
    info = dict(elev_min=zmin, elev_max=zmax, height_mm=float(z.max()),
                track_cells=track_cells)
    return vertices, faces, g, info


def export_model(p, path, progress=None):
    vertices, faces, g, info = build_model(p, progress)
    if progress:
        progress(75, "Writing file…")
    name = "TerrainPrint3D - %s" % p.dem_layer.name()
    if p.fmt == "stl":
        mesh.write_stl(path, vertices, faces, name)
    else:
        mesh.write_3mf(path, vertices, faces, name)
    info["file_bytes"] = os.path.getsize(path)
    info["triangles"] = len(faces)
    if progress:
        progress(100, "Done")
    return g, info
