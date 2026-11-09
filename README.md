# TerrainPrint3D

QGIS plugin that turns a DEM into a watertight, print-ready terrain model.

- **Detail control with live estimate**: set the sample spacing (mm) and see triangles and file size before exporting.
- **3MF export**: compressed and indexed, about 5× smaller than STL. Binary STL is also available.
- **GPX tracks**: load a GPX file or use any line layer, and raise or engrave it into the terrain.
- True scale from ellipsoidal distances, so geographic (lat/lon) DEMs come out undistorted.
- NoData-safe sampling with bilinear interpolation.

## Development

The plugin package `TerrainPrint3D/` is symlinked into the QGIS profile:

    ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/TerrainPrint3D

After editing code, reload it with the *Plugin Reloader* plugin, or restart QGIS.

## Tests

    /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/test_mesh.py   # mesh: watertight, outward, sizes
    ./tests/run_core_test.sh                                                 # headless QGIS end-to-end
    ./tests/run_dialog_test.sh                                               # offscreen dialog smoke test
