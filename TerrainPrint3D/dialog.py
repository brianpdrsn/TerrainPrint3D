import os
import traceback

from qgis.core import Qgis, QgsMapLayerProxyModel, QgsProject, QgsVectorLayer
from qgis.gui import QgsExtentGroupBox, QgsFileWidget, QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout,
)

from . import core
from .maptool import Footprint, SquareMapTool

try:                                    # QGIS >= 3.34
    RASTER_FILTER = Qgis.LayerFilter.RasterLayer
    LINE_FILTER = Qgis.LayerFilter.LineLayer
except AttributeError:
    RASTER_FILTER = QgsMapLayerProxyModel.RasterLayer
    LINE_FILTER = QgsMapLayerProxyModel.LineLayer

BIG_TRIANGLES = 5_000_000


def _human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def _spin(lo, hi, val, step, suffix="", decimals=2):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(val)
    s.setSuffix(suffix)
    return s


class TerrainPrintDialog(QDialog):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self._relief_cache = {}
        self._auto_path = True
        self.setWindowTitle("TerrainPrint3D")
        self.setMinimumWidth(460)

        self._estimate_timer = QTimer(self, singleShot=True, interval=250)
        self._estimate_timer.timeout.connect(self.update_estimate)

        layout = QVBoxLayout(self)

        # --- DEM + extent -------------------------------------------------- #
        form = QFormLayout()
        self.dem_combo = QgsMapLayerComboBox()
        self.dem_combo.setFilters(RASTER_FILTER)
        form.addRow("Elevation layer (DEM)", self.dem_combo)
        layout.addLayout(form)

        self.extent_box = QgsExtentGroupBox()
        self.extent_box.setCheckable(False)
        self.extent_box.setMapCanvas(self.canvas)
        layout.addWidget(self.extent_box)

        sq_row = QHBoxLayout()
        self.square_btn = QPushButton("Draw square on map…")
        self.square_btn.setToolTip(
            "Click the centre of the area on the map and drag outward.\n"
            "The area is square on the ground, so the model is square too.\n"
            "Hold Shift while dragging to rotate it: the cursor then marks\n"
            "the middle of the model's top edge. Esc or right-click cancels.")
        self.square_btn.clicked.connect(self.start_square)
        sq_row.addWidget(self.square_btn)
        sq_row.addStretch(1)
        sq_row.addWidget(QLabel("Rotation"))
        self.rot_spin = _spin(-180, 180, 0, 1, "°", 1)
        self.rot_spin.setWrapping(True)
        self.rot_spin.setToolTip(
            "Turns the area clockwise around its centre.\n"
            "This is the compass direction of the model's top edge:\n"
            "0° = north at the top, 90° = east at the top.\n"
            "The short line on the map outline points to the top edge.")
        sq_row.addWidget(self.rot_spin)
        layout.addLayout(sq_row)

        self.square_tool = SquareMapTool(self.canvas, self.dem_crs)
        self.square_tool.squareDrawn.connect(self._square_drawn)
        self.square_tool.finished.connect(self._square_finished)
        self._prev_tool = None
        # Outline of the area that will be printed
        self.preview = Footprint(self.canvas)

        # --- Model --------------------------------------------------------- #
        model_box = QGroupBox("Model")
        mf = QFormLayout(model_box)
        self.width_spin = _spin(10, 2000, 150, 5, " mm", 1)
        self.exag_spin = _spin(0.1, 50, 1.5, 0.1, " ×", 2)
        self.base_spin = _spin(0.4, 100, 3.0, 0.5, " mm", 1)
        self.res_spin = _spin(0.05, 10, 0.4, 0.05, " mm", 2)
        self.res_spin.setToolTip(
            "Distance between elevation samples on the model.\n"
            "Larger = less detail and a smaller file. Around your nozzle\n"
            "width (0.4 mm) is usually as fine as a printer can show.")
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItem("3MF: compressed, about 5× smaller", "3mf")
        self.fmt_combo.addItem("STL: works everywhere", "stl")
        mf.addRow("Width", self.width_spin)
        mf.addRow("Vertical exaggeration", self.exag_spin)
        mf.addRow("Base thickness", self.base_spin)
        mf.addRow("Detail (sample spacing)", self.res_spin)
        mf.addRow("File format", self.fmt_combo)
        layout.addWidget(model_box)

        # --- Track --------------------------------------------------------- #
        self.track_box = QGroupBox("GPX track")
        self.track_box.setCheckable(True)
        self.track_box.setChecked(False)
        tf = QFormLayout(self.track_box)
        row = QHBoxLayout()
        self.track_combo = QgsMapLayerComboBox()
        self.track_combo.setFilters(LINE_FILTER)
        load_btn = QPushButton("Load GPX…")
        load_btn.clicked.connect(self.load_gpx)
        row.addWidget(self.track_combo, 1)
        row.addWidget(load_btn)
        tf.addRow("Track layer", row)
        self.track_mode = QComboBox()
        self.track_mode.addItem("Raised above terrain", "raise")
        self.track_mode.addItem("Engraved into terrain", "engrave")
        self.track_h_spin = _spin(0.1, 20, 1.0, 0.1, " mm", 1)
        self.track_w_spin = _spin(0.2, 20, 1.2, 0.1, " mm", 1)
        self.track_smooth_spin = _spin(0, 20, 1.0, 0.5, " mm", 1)
        self.track_smooth_spin.setSpecialValueText("Off")
        self.track_smooth_spin.setToolTip(
            "How much to smooth the track's path, in model millimetres.\n"
            "Removes GPS wobble and zig-zags. 0 = follow the GPX exactly;\n"
            "1–2 mm suits most hikes; higher values round off tight corners.")
        tf.addRow("Style", self.track_mode)
        tf.addRow("Height / depth", self.track_h_spin)
        tf.addRow("Width", self.track_w_spin)
        tf.addRow("Smoothing", self.track_smooth_spin)
        layout.addWidget(self.track_box)

        # --- Info + output ------------------------------------------------- #
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        of = QFormLayout()
        self.file_widget = QgsFileWidget()
        self.file_widget.setStorageMode(QgsFileWidget.SaveFile)
        self.file_widget.fileChanged.connect(self._path_edited)
        of.addRow("Save to", self.file_widget)
        layout.addLayout(of)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        self.export_btn = buttons.addButton("Export", QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.export)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

        # --- Signals ------------------------------------------------------- #
        self.dem_combo.layerChanged.connect(self.dem_changed)
        self.extent_box.extentChanged.connect(self.schedule_estimate)
        self.extent_box.extentChanged.connect(self.update_preview)
        self.rot_spin.valueChanged.connect(self.update_preview)
        self.rot_spin.valueChanged.connect(self.schedule_estimate)
        for w in (self.width_spin, self.exag_spin, self.base_spin, self.res_spin,
                  self.track_h_spin):
            w.valueChanged.connect(self.schedule_estimate)
        self.fmt_combo.currentIndexChanged.connect(self.format_changed)
        self.track_box.toggled.connect(self.schedule_estimate)
        self.track_mode.currentIndexChanged.connect(self.schedule_estimate)

        self.format_changed()
        self.dem_changed(self.dem_combo.currentLayer())

    # ------------------------------------------------------------------ #
    def showEvent(self, event):
        super().showEvent(event)
        self.extent_box.setCurrentExtent(
            self.canvas.extent(), self.canvas.mapSettings().destinationCrs())
        self.update_preview()

    def closeEvent(self, event):
        self.preview.clear()
        if self.canvas.mapTool() is self.square_tool:
            self.canvas.unsetMapTool(self.square_tool)
        super().closeEvent(event)

    def cleanup(self):
        """Remove everything this dialog put on the map canvas (plugin unload)."""
        self.close()
        self.preview.remove()

    # --- square drawing ------------------------------------------------ #
    def dem_crs(self):
        layer = self.dem_combo.currentLayer()
        return layer.crs() if layer else self.canvas.mapSettings().destinationCrs()

    def start_square(self):
        prev = self.canvas.mapTool()
        self._prev_tool = prev if prev is not self.square_tool else None
        self.canvas.setMapTool(self.square_tool)
        self.hide()                       # get out of the way of the map

    def _square_drawn(self, square):
        center, side_m, rotation = square
        crs = self.dem_crs()
        self.rot_spin.blockSignals(True)
        self.rot_spin.setValue(rotation)
        self.rot_spin.blockSignals(False)
        self.extent_box.setOutputExtentFromUser(core.square_extent(center, side_m, crs), crs)
        self.update_preview()
        self.schedule_estimate()

    def _square_finished(self):
        # Runs when the tool is deactivated (finished, cancelled or replaced).
        def restore():
            if self.canvas.mapTool() is None and self._prev_tool is not None:
                self.canvas.setMapTool(self._prev_tool)
            self._prev_tool = None
            self.show()
            self.raise_()
            self.activateWindow()
        QTimer.singleShot(0, restore)

    def current_frame(self):
        layer = self.dem_combo.currentLayer()
        extent = self.extent_box.outputExtent()
        if layer is None or extent.isEmpty():
            return None
        try:
            return core.Frame(extent, layer.crs(), self.width_spin.value(), self.rot_spin.value())
        except Exception:
            return None

    def update_preview(self, *args):
        rotated = abs(self.rot_spin.value()) > 1e-9
        self.extent_box.setTitleBase("Extent (before rotation)" if rotated else "Extent")
        self.preview.show(self.current_frame())

    def schedule_estimate(self, *args):
        self._estimate_timer.start()

    def fmt(self):
        return self.fmt_combo.currentData()

    def format_changed(self, *args):
        ext = self.fmt()
        self.file_widget.setFilter("%s files (*.%s)" % (ext.upper(), ext))
        path = self.file_widget.filePath()
        if path:
            root, old = os.path.splitext(path)
            if old.lower() in (".stl", ".3mf"):
                self._set_path(root + "." + ext, auto=self._auto_path)
        self.schedule_estimate()

    def _set_path(self, path, auto):
        self.file_widget.blockSignals(True)
        self.file_widget.setFilePath(path)
        self.file_widget.blockSignals(False)
        self._auto_path = auto

    def _path_edited(self, *args):
        self._auto_path = False

    def dem_changed(self, layer):
        if layer is None:
            self.schedule_estimate()
            return
        self.extent_box.setOriginalExtent(layer.extent(), layer.crs())
        self.extent_box.setOutputCrs(layer.crs())
        if self.extent_box.outputExtent().isEmpty() or \
                not self.extent_box.outputExtent().intersects(layer.extent()):
            self.extent_box.setOutputExtentFromOriginal()
        self.update_preview()
        if self._auto_path:
            folder = os.path.expanduser("~/Documents")
            self._set_path(os.path.join(folder, "%s.%s" % (layer.name(), self.fmt())), True)
        self.schedule_estimate()

    def load_gpx(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open GPX file", "", "GPX files (*.gpx)")
        if not path:
            return
        name = os.path.splitext(os.path.basename(path))[0]
        for sub in ("tracks", "routes"):
            layer = QgsVectorLayer("%s|layername=%s" % (path, sub), name, "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                QgsProject.instance().addMapLayer(layer)
                self.track_combo.setLayer(layer)
                self.track_box.setChecked(True)
                return
        QMessageBox.warning(self, "TerrainPrint3D",
                            "No tracks or routes were found in this GPX file.")

    # ------------------------------------------------------------------ #
    def params(self):
        dem = self.dem_combo.currentLayer()
        if dem is None:
            raise ValueError("Choose an elevation layer first.")
        track = None
        if self.track_box.isChecked():
            track = self.track_combo.currentLayer()
            if track is None:
                raise ValueError("Choose a track layer, or turn off the GPX track option.")
        return core.ModelParams(
            dem_layer=dem,
            extent=self.extent_box.outputExtent(),
            width_mm=self.width_spin.value(),
            exaggeration=self.exag_spin.value(),
            base_mm=self.base_spin.value(),
            resolution_mm=self.res_spin.value(),
            fmt=self.fmt(),
            track_layer=track,
            track_mode=self.track_mode.currentData(),
            track_height_mm=self.track_h_spin.value(),
            track_width_mm=self.track_w_spin.value(),
            track_smooth_mm=self.track_smooth_spin.value(),
            rotation_deg=self.rot_spin.value(),
        )

    def _relief(self, p):
        key = (p.dem_layer.id(), p.extent.toString(6), round(p.rotation_deg, 3))
        if key not in self._relief_cache:
            self._relief_cache[key] = core.relief_range(p)
        return self._relief_cache[key]

    def update_estimate(self):
        try:
            p = self.params()
            g = core.model_geometry(p)
        except Exception as e:
            self.info.setText("<i>%s</i>" % e)
            self.export_btn.setEnabled(False)
            return
        self.export_btn.setEnabled(True)

        lines = [
            "Area: <b>%.2f × %.2f km</b> &nbsp; Scale: <b>1:%s</b>"
            % (g.ground_w_m / 1000, g.ground_h_m / 1000, format(int(round(g.scale)), ",")),
        ]
        size = "Model: <b>%.0f × %.0f mm</b>" % (g.width_mm, g.depth_mm)
        if abs(g.ground_w_m - g.ground_h_m) <= 0.002 * g.ground_w_m:
            size += " (square)"
        relief = self._relief(p)
        if relief:
            h = p.base_mm + (relief[1] - relief[0]) * g.mm_per_m * p.exaggeration
            if p.track_layer is not None and p.track_mode == "raise":
                h += p.track_height_mm
            size += " × ~%.1f mm tall &nbsp;(elevation %.0f–%.0f m)" % (h, relief[0], relief[1])
        lines.append(size)
        if not p.dem_layer.extent().contains(core.make_frame(p).bbox()):
            lines.append("<span style='color:#c0392b'>Part of the area is outside the DEM; "
                         "it will be flat at the lowest elevation.</span>")
        est = "%s triangles, %s%s" % (format(g.triangles, ","),
                                      "~" if p.fmt == "3mf" else "",
                                      _human(g.file_bytes))
        if g.triangles > BIG_TRIANGLES:
            est = "<span style='color:#c0392b'>%s: large. Try a bigger sample spacing.</span>" % est
        lines.append("File: <b>%s</b>" % est)
        self.info.setText("<br>".join(lines))

    # ------------------------------------------------------------------ #
    def export(self):
        try:
            p = self.params()
            g = core.model_geometry(p)
        except Exception as e:
            QMessageBox.warning(self, "TerrainPrint3D", str(e))
            return

        path = self.file_widget.filePath()
        if not path:
            path, _ = QFileDialog.getSaveFileName(
                self, "Save model", "", "%s files (*.%s)" % (p.fmt.upper(), p.fmt))
            if not path:
                return
        if not path.lower().endswith("." + p.fmt):
            path += "." + p.fmt
        if g.triangles > BIG_TRIANGLES:
            r = QMessageBox.question(
                self, "TerrainPrint3D",
                "This model has %s triangles (about %s). Continue?"
                % (format(g.triangles, ","), _human(g.file_bytes)))
            if r != QMessageBox.Yes:
                return

        self.progress.setVisible(True)
        self.export_btn.setEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)

        def progress(pct, msg):
            self.progress.setValue(pct)
            self.progress.setFormat("%s  %%p%%" % msg)
            QApplication.processEvents()

        try:
            g, info = core.export_model(p, path, progress)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            self.progress.setVisible(False)
            self.export_btn.setEnabled(True)
            QMessageBox.critical(self, "TerrainPrint3D",
                                 "Export failed:\n%s\n\n%s" % (e, traceback.format_exc()))
            return
        QApplication.restoreOverrideCursor()
        self.progress.setVisible(False)
        self.export_btn.setEnabled(True)

        bar = self.iface.messageBar()
        bar.pushMessage("TerrainPrint3D", "Saved %s (%s, %s triangles, %.1f mm tall)"
                        % (path, _human(info["file_bytes"]),
                           format(info["triangles"], ","), info["height_mm"]),
                        level=Qgis.Success, duration=10)
        if p.track_layer is not None and info["track_cells"] == 0:
            bar.pushMessage("TerrainPrint3D",
                            "The track does not cross the selected extent, so it was left out.",
                            level=Qgis.Warning, duration=10)
