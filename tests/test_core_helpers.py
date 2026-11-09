# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
import numpy as np
from osgeo import gdal, osr

GPX = """<?xml version="1.0"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>hike</name><trkseg>
<trkpt lat="59.97" lon="10.55"/><trkpt lat="59.98" lon="10.57"/><trkpt lat="59.99" lon="10.60"/>
</trkseg></trk></gpx>"""


def make_dem(path, nodata_corner=False):
    # 0.002 deg pixels around Oslo, EPSG:4326, a smooth hill
    rows, cols = 200, 300
    yy, xx = np.mgrid[0:rows, 0:cols]
    z = (400 * np.exp(-((xx - 150) ** 2 + (yy - 100) ** 2) / 3000.0)).astype(np.float32)
    if nodata_corner:
        z[:20, :20] = -9999
    ds = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform([10.4, 0.002, 0, 60.1, 0, -0.002])
    srs = osr.SpatialReference()
    srs.SetWellKnownGeogCS("WGS84")  # no proj.db lookup needed
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).SetNoDataValue(-9999)
    ds.GetRasterBand(1).WriteArray(z)
    ds = None




def make_plane_dem(path, a=0.02, b=0.005):
    """UTM 32N DEM, 10 m pixels, elevation = a*easting + b*northing (relative).

    A plane is reproduced exactly by bilinear sampling, so any rotation can
    be checked against the analytic value.
    """
    rows, cols, x0, y0, px = 400, 400, 592000.0, 6650000.0, 10.0
    E = x0 + (np.arange(cols) + 0.5) * px
    N = y0 - (np.arange(rows) + 0.5) * px
    z = (a * (E[None, :] - x0) + b * (N[:, None] - (y0 - rows * px))).astype(np.float64)
    ds = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Float64)
    ds.SetGeoTransform([x0, px, 0, y0, 0, -px])
    srs = osr.SpatialReference()
    srs.SetWellKnownGeogCS("WGS84")
    srs.SetUTM(32, True)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).WriteArray(z)
    ds = None
    return lambda X, Y: a * (X - x0) + b * (Y - (y0 - rows * px))
