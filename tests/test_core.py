# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
"""Headless end-to-end test. Run with:  ./tests/run_core_test.sh"""
import os
import sys
import tempfile

from qgis.core import QgsApplication, QgsRasterLayer, QgsVectorLayer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

app = QgsApplication([], False)
app.initQgis()

from TerrainPrint3D import core  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from test_core_helpers import GPX, make_dem, make_plane_dem  # noqa: E402


with tempfile.TemporaryDirectory() as d:
    tif = os.path.join(d, "dem.tif")
    make_dem(tif, nodata_corner=True)
    dem = QgsRasterLayer(tif, "dem")
    assert dem.isValid()

    gpx = os.path.join(d, "track.gpx")
    open(gpx, "w").write(GPX)
    track = QgsVectorLayer(gpx + "|layername=tracks", "track", "ogr")
    assert track.isValid() and track.featureCount() == 1

    p = core.ModelParams(dem_layer=dem, extent=dem.extent(), width_mm=150,
                         resolution_mm=0.5, track_layer=track)
    g = core.model_geometry(p)
    print("ground %.1f x %.1f km, scale 1:%d, model %.0f x %.0f mm, grid %dx%d"
          % (g.ground_w_m / 1000, g.ground_h_m / 1000, g.scale,
             g.width_mm, g.depth_mm, g.cols, g.rows))
    # 0.6 deg lon at 59.9N ~ 33.5 km, 0.4 deg lat ~ 44.6 km
    assert 32500 < g.ground_w_m < 34500, g.ground_w_m
    assert 44000 < g.ground_h_m < 45000, g.ground_h_m

    for fmt in ("stl", "3mf"):
        p.fmt = fmt
        out = os.path.join(d, "out." + fmt)
        g, info = core.export_model(p, out, lambda pct, msg: None)
        print(fmt, "%.2f MB (est %.2f)" % (info["file_bytes"] / 1e6, g.file_bytes / 1e6),
              "triangles", info["triangles"], "track cells", info["track_cells"],
              "elev %.0f..%.0f m, height %.1f mm"
              % (info["elev_min"], info["elev_max"], info["height_mm"]))
        assert info["track_cells"] > 50
        assert info["elev_min"] > -1, "nodata leaked into elevations"

    print("relief", core.relief_range(core.ModelParams(dem_layer=dem, extent=dem.extent())))

    # The DEM's own extent counts as inside (no float-rounding false alarm),
    # while a slightly larger or shifted one does not.
    full = core.ModelParams(dem_layer=dem, extent=dem.extent())
    assert core.inside_dem(full), "full DEM extent reported as outside"
    from qgis.core import QgsRectangle as _R  # noqa: E402
    e = dem.extent()
    for bigger in (_R(e.xMinimum() - 0.001, e.yMinimum(), e.xMaximum(), e.yMaximum()),
                   _R(e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum() + 0.001)):
        assert not core.inside_dem(core.ModelParams(dem_layer=dem, extent=bigger))
    full.rotation_deg = 30
    assert not core.inside_dem(full), "rotated full extent pokes outside the DEM"
    print("inside_dem OK")
    print("ALL OK")


# A colour/ARGB layer must be rejected with a clear message.
from unittest import mock  # noqa: E402
with tempfile.TemporaryDirectory() as d:
    tif = os.path.join(d, "dem.tif")
    make_dem(tif)
    dem = QgsRasterLayer(tif, "basemap")
    with mock.patch.object(type(dem.dataProvider()), "dataType", return_value=12):
        try:
            core.model_geometry(core.ModelParams(dem_layer=dem, extent=dem.extent()))
            raise AssertionError("ARGB layer was accepted")
        except ValueError as e:
            assert "colour image" in str(e)
            print("rejects colour layer:", e)
print("COLOUR CHECK OK")

# square_extent must be square on the ground in any CRS
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY  # noqa: E402
for epsg, pt in (("EPSG:4326", (10.75, 59.91)), ("EPSG:25832", (597000, 6643000)),
                 ("EPSG:3857", (1196700, 8379700)), ("EPSG:2263", (980000, 200000))):
    crs = QgsCoordinateReferenceSystem(epsg)
    r = core.square_extent(QgsPointXY(*pt), 12000, crs)
    w, h = core.ground_size_m(r, crs)
    assert abs(w - 12000) < 1 and abs(h - 12000) < 1, (epsg, w, h)
    print("square %s: %.2f x %.2f m" % (epsg, w, h))
print("SQUARE OK")

# Rotation: sampling a plane must match the analytic plane for any angle,
# and 90 degrees must put the model's top edge on the east side.
import numpy as np  # noqa: E402
with tempfile.TemporaryDirectory() as d:
    tif = os.path.join(d, "plane.tif")
    truth = make_plane_dem(tif)
    dem = QgsRasterLayer(tif, "plane")
    assert dem.isValid() and dem.crs().isValid()
    sq = core.square_extent(dem.extent().center(), 2000, dem.crs())
    grids = {}
    for rot, res in ((0, 1.0), (30, 1.0), (90, 1.0), (-135, 1.0), (30, 3.0), (30, 4.0), (30, 0.3)):
        p = core.ModelParams(dem_layer=dem, extent=sq, width_mm=100,
                             resolution_mm=res, rotation_deg=rot)
        g = core.model_geometry(p)
        f = core.make_frame(p)
        z = core.sample_dem(dem, f, g.rows, g.cols)
        X, Y = f.grid(g.rows, g.cols)
        err = np.abs(z - truth(X, Y)).max()
        assert err < 1e-6, (rot, err)
        x_mm, y_mm = f.to_model(X, Y)                # round trip
        assert np.allclose(x_mm, np.linspace(0, f.W, g.cols)[None, :]), rot
        if res == 1.0:
            grids[rot] = z
        print("rotation %4d, detail %.1f mm: max error %.1e m" % (rot, res, err))
    assert np.allclose(grids[90], np.rot90(grids[0]), atol=1e-6), "90 deg should equal rot90"
    # Reading in many small strips must give the same result as one big read
    defaults = core._read_decimated.__defaults__
    core._read_decimated.__defaults__ = (3000,)
    try:
        p = core.ModelParams(dem_layer=dem, extent=sq, width_mm=100, resolution_mm=1.0, rotation_deg=30)
        g = core.model_geometry(p)
        z_strips = core.sample_dem(dem, core.make_frame(p), g.rows, g.cols)
    finally:
        core._read_decimated.__defaults__ = defaults
    assert np.allclose(z_strips, grids[30], atol=1e-9), "strip reading differs"
    print("strip reading matches")
    f = core.make_frame(core.ModelParams(dem_layer=dem, extent=sq, rotation_deg=90))
    top_left = f.corners()[3]
    c = sq.center()
    assert top_left.x() > c.x() and top_left.y() > c.y(), "up=east: top-left should be NE"
    # rotated square stays square on the ground
    bl, br, tr, tl = f.corners()
    da = core.distance_area(dem.crs())
    sides = [da.measureLine(a, b) for a, b in ((bl, br), (br, tr), (tr, tl), (tl, bl))]
    assert max(sides) - min(sides) < 1.0, sides
print("ROTATION OK")

app.exitQgis()
