# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
"""Offscreen GUI smoke test: build the dialog, load a DEM + GPX, export."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from qgis.core import QgsApplication, QgsProject, QgsRasterLayer, QgsVectorLayer
app = QgsApplication([], True); app.initQgis()
from qgis.gui import QgsMapCanvas, QgsMessageBar


class FakeIface:
    """Minimal iface. (qgis.testing.mocked.get_iface breaks layer CRS loading.)"""
    def __init__(self):
        self._canvas = QgsMapCanvas()
        self._bar = QgsMessageBar()
    def mapCanvas(self): return self._canvas
    def messageBar(self): return self._bar
    def mainWindow(self): return None
    def __getattr__(self, name):  # toolbar/menu registration: no-ops
        return lambda *a, **k: None
sys.path.insert(0, os.path.dirname(__file__))
from test_core_helpers import make_dem, GPX  # noqa
from TerrainPrint3D.dialog import TerrainPrintDialog
from TerrainPrint3D.plugin import TerrainPrint3DPlugin

iface = FakeIface()

# A modal popup would block forever offscreen: turn popups into failures instead.
from qgis.PyQt.QtWidgets import QMessageBox  # noqa: E402
def _popup(*a, **k):
    raise AssertionError("Unexpected popup: %s" % (a[2] if len(a) > 2 else a,))
QMessageBox.critical = QMessageBox.warning = QMessageBox.question = staticmethod(_popup)
with tempfile.TemporaryDirectory() as d:
    tif = os.path.join(d, "dem.tif"); make_dem(tif)
    gpx = os.path.join(d, "t.gpx"); open(gpx, "w").write(GPX)
    dem = QgsRasterLayer(tif, "dem"); trk = QgsVectorLayer(gpx + "|layername=tracks", "trk", "ogr")
    QgsProject.instance().addMapLayers([dem, trk])

    assert dem.crs().isValid() and dem.crs().isGeographic()
    plugin = TerrainPrint3DPlugin(iface); plugin.initGui()
    dlg = TerrainPrintDialog(iface)
    dlg.dem_combo.setLayer(dem)
    dlg.track_combo.setLayer(trk); dlg.track_box.setChecked(True)
    dlg.track_smooth_spin.setValue(2.0)
    dlg.res_spin.setValue(0.8)
    dlg.update_estimate()
    print(dlg.info.text().replace("<br>", "\n"))
    assert dlg.export_btn.isEnabled()
    assert "outside the DEM" not in dlg.info.text(), "false outside-DEM warning on full extent"
    for i in range(dlg.fmt_combo.count()):
        dlg.fmt_combo.setCurrentIndex(i)
        out = os.path.join(d, "model." + dlg.fmt()); dlg._set_path(out, False)
        dlg.export()
        assert os.path.exists(out), out
        print("exported", out, os.path.getsize(out))

    # --- Draw square tool: press at centre, drag, release ---------------- #
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle
    from qgis.gui import QgsMapMouseEvent
    from qgis.PyQt.QtCore import QEvent, QPoint, Qt
    canvas = iface.mapCanvas()
    canvas.resize(800, 600)
    canvas.show()
    app.processEvents()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:25832"))
    canvas.setExtent(QgsRectangle(570000, 6620000, 630000, 6680000))
    canvas.refresh()
    dlg.show()
    dlg.start_square()
    assert canvas.mapTool() is dlg.square_tool and not dlg.isVisible()
    tool = dlg.square_tool
    def ev(kind, x, y, mods=Qt.NoModifier):
        return QgsMapMouseEvent(canvas, kind, QPoint(x, y), Qt.LeftButton, Qt.LeftButton, mods)
    tool.canvasPressEvent(ev(QEvent.MouseButtonPress, 400, 300))
    tool.canvasMoveEvent(ev(QEvent.MouseMove, 450, 320))
    tool.canvasReleaseEvent(ev(QEvent.MouseButtonRelease, 480, 310))  # wider than tall drag
    app.processEvents()
    assert dlg.isVisible(), "dialog should come back after drawing"
    dlg.update_estimate()
    info = dlg.info.text()
    print(info.replace("<br>", "\n"))
    assert "(square)" in info, info
    assert not dlg.preview.poly.asGeometry().isEmpty(), "preview outline missing"
    assert dlg.rot_spin.value() == 0
    out = os.path.join(d, "square.3mf"); dlg.fmt_combo.setCurrentIndex(0); dlg._set_path(out, False)
    dlg.export(); assert os.path.exists(out)
    print("square export", os.path.getsize(out))

    # Shift-drag up and to the right (north-east on the north-up canvas) rotates
    dlg.start_square()
    tool.canvasPressEvent(ev(QEvent.MouseButtonPress, 400, 300))
    tool.canvasMoveEvent(ev(QEvent.MouseMove, 460, 240, Qt.ShiftModifier))
    tool.canvasReleaseEvent(ev(QEvent.MouseButtonRelease, 480, 220, Qt.ShiftModifier))
    app.processEvents()
    rot = dlg.rot_spin.value()
    print("shift-drag rotation:", rot)
    # 45 deg on the UTM canvas; the DEM is lat/lon, so grid convergence shifts it a little
    assert 40 < rot < 50, rot
    dlg.update_estimate()
    assert "(square)" in dlg.info.text(), dlg.info.text()
    assert dlg.extent_box.title().startswith("Extent (before rotation)"), dlg.extent_box.title()
    arrow = dlg.preview.arrow.asGeometry().asPolyline()   # canvas coordinates
    assert arrow[-1].x() > arrow[0].x() and arrow[-1].y() > arrow[0].y(), "top edge should face NE"
    out = os.path.join(d, "rotated.3mf"); dlg._set_path(out, False)
    dlg.export(); assert os.path.exists(out)
    print("rotated export", os.path.getsize(out))
    dlg.rot_spin.setValue(0)
    assert dlg.extent_box.title().startswith("Extent") and "rotation" not in dlg.extent_box.title()

    # A click without dragging cancels and changes nothing
    before = dlg.extent_box.outputExtent()
    dlg.start_square()
    tool.canvasPressEvent(ev(QEvent.MouseButtonPress, 100, 100))
    tool.canvasReleaseEvent(ev(QEvent.MouseButtonRelease, 101, 101))
    app.processEvents()
    assert dlg.extent_box.outputExtent() == before and dlg.isVisible()
    dlg.close()
    plugin.unload()
print("DIALOG OK")
app.exitQgis()

