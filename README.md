# TerrainPrint3D

A QGIS plugin that turns a digital elevation model (DEM) into a watertight, print-ready 3D terrain model, with optional GPX tracks.

## Features

- **Exports 3MF or STL.** 3MF is compressed and usually about 5× smaller than STL. PrusaSlicer, Bambu Studio and Cura all open it.
- **Detail setting with live estimate.** Set the sample spacing in mm and see the triangle count and file size before you export.
- **True scale in any coordinate system.** Distances are measured on the ellipsoid, so lat/lon and Web Mercator DEMs aren't stretched.
- **Draw a square on the map.** Click the centre and drag. Hold **Shift** to rotate the square, or type an exact angle in the **Rotation** field. A red outline on the map shows the printed area, with a short line pointing to the model's top edge.
- **GPX tracks.** Load a GPX file or use any line layer, and raise or engrave it into the terrain. The **Smoothing** setting removes GPS wobble, and the track edges are sloped instead of stair-stepped.
- **Accurate sampling.** Elevation values come from the DEM's own pixel grid, with NoData handled and bilinear interpolation. Large DEMs are read in strips to limit memory use.
- **Clear warnings.** The dialog warns you if you pick a colour or basemap layer instead of a DEM, or if the area extends past the DEM.

Requires QGIS 3.22 or newer. Tested on QGIS 3.36.

## Installation

Copy (or symlink) the `TerrainPrint3D/` folder into your QGIS plugins folder:

| OS | Plugins folder |
|---|---|
| macOS | `~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/` |
| Windows | `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\` |
| Linux | `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/` |

Restart QGIS, then enable **TerrainPrint3D** under *Plugins → Manage and Install Plugins → Installed*. The plugin is marked experimental, so you may need to tick *Show also experimental plugins* in that dialog's Settings.

## Usage

1. Open *Raster → TerrainPrint3D*, or click the toolbar icon.
2. Choose an **elevation layer (DEM)**. This must be a layer whose pixels are heights, not a basemap or hillshade.
3. Choose the area: use the Extent box, or **Draw square on map…** (Shift-drag to rotate).
4. Set the model width, vertical exaggeration, base thickness and detail.
5. Optionally, tick **GPX track** and load a GPX file. Use its *tracks* layer rather than *routes*.
6. Choose a file name and click **Export**.

## Development

Run the tests with the Python bundled in QGIS (the paths are for macOS):

    /Applications/QGIS.app/Contents/MacOS/bin/python3 tests/test_mesh.py   # mesh: watertight, outward-facing, file sizes
    ./tests/run_core_test.sh                                                 # headless QGIS: export, square, rotation accuracy
    ./tests/run_dialog_test.sh                                               # offscreen dialog and map-tool interaction

| File | Contents |
|---|---|
| `TerrainPrint3D/mesh.py` | Mesh building, track rasterising and smoothing, STL/3MF writers (numpy only) |
| `TerrainPrint3D/core.py` | DEM sampling, rotation frame, scale, model assembly |
| `TerrainPrint3D/maptool.py` | Draw-square map tool and on-map outline |
| `TerrainPrint3D/dialog.py` | The plugin window |

For a detailed walkthrough of how everything works (architecture, the export pipeline, every function, and the algorithms), see [docs/DOCUMENTATION.md](docs/DOCUMENTATION.md).

## License

GNU General Public License v2.0 or later. See [LICENSE](LICENSE).
