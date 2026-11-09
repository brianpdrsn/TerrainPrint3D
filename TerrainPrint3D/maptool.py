# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
"""Map tool: click the centre, drag outward, release -> a square area (in metres).

Plain drag: north-up square. Hold Shift while dragging: the square turns to
follow the mouse, and the cursor marks the middle of the model's top edge.
"""
import math

from qgis.core import (
    Qgis, QgsCoordinateTransform, QgsCsException, QgsPointXY, QgsProject, QgsWkbTypes,
)
from qgis.gui import QgsMapTool, QgsRubberBand
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QToolTip

from . import core

try:                                     # QGIS >= 3.30
    POLYGON = Qgis.GeometryType.Polygon
    LINE = Qgis.GeometryType.Line
except AttributeError:
    POLYGON = QgsWkbTypes.PolygonGeometry
    LINE = QgsWkbTypes.LineGeometry

MIN_DRAG_PX = 5
RED = QColor(232, 70, 60)


class Footprint:
    """Outline of the printed area plus a short line pointing to the model's top edge."""

    def __init__(self, canvas, color=RED, fill_alpha=30):
        self.canvas = canvas
        self.poly = QgsRubberBand(canvas, POLYGON)
        self.poly.setStrokeColor(color)
        fill = QColor(color)
        fill.setAlpha(fill_alpha)
        self.poly.setFillColor(fill)
        self.poly.setWidth(2)
        self.arrow = QgsRubberBand(canvas, LINE)
        self.arrow.setStrokeColor(color)
        self.arrow.setWidth(3)

    def show(self, frame):
        if frame is None:
            self.clear()
            return
        self.poly.setToGeometry(frame.polygon().densifyByCount(32), frame.crs)
        self.arrow.setToGeometry(frame.up_marker(), frame.crs)

    def clear(self):
        self.poly.reset(POLYGON)
        self.arrow.reset(LINE)

    def remove(self):
        for rb in (self.poly, self.arrow):
            self.canvas.scene().removeItem(rb)


class SquareMapTool(QgsMapTool):
    squareDrawn = pyqtSignal(object)     # (centre QgsPointXY in target CRS, side_m, rotation_deg)
    finished = pyqtSignal()

    def __init__(self, canvas, target_crs):
        """``target_crs`` is a callable returning the CRS the square is built in (the DEM's)."""
        super().__init__(canvas)
        self.canvas = canvas
        self.target_crs = target_crs
        self.center = None
        self.press_pos = None
        self.footprint = None
        self.setCursor(Qt.CrossCursor)

    # -------------------------------------------------------------- #
    def square_for(self, center, cursor, rotate):
        """Square from canvas-CRS ``center`` and ``cursor``.

        Returns (centre in target CRS, side in metres, rotation in degrees),
        or None. Without ``rotate`` the square is north-up and the cursor
        lies on its edge; with ``rotate`` the cursor marks the middle of the
        model's top edge.
        """
        tcrs = self.target_crs()
        xf = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), tcrs,
                                    QgsProject.instance())
        try:
            c = xf.transform(QgsPointXY(center))
            p = xf.transform(QgsPointXY(cursor))
        except QgsCsException:
            return None
        probe = core.square_extent(c, 1000.0, tcrs)      # local metres per CRS unit
        de = (p.x() - c.x()) * 1000.0 / probe.width()
        dn = (p.y() - c.y()) * 1000.0 / probe.height()
        if rotate:
            half = math.hypot(de, dn)
            rot = round(math.degrees(math.atan2(de, dn)), 1)
        else:
            half = max(abs(de), abs(dn))
            rot = 0.0
        if not half > 0:
            return None
        return c, 2.0 * half, rot

    def frame_for(self, square):
        c, side, rot = square
        tcrs = self.target_crs()
        return core.Frame(core.square_extent(c, side, tcrs), tcrs, 100.0, rot)

    # -------------------------------------------------------------- #
    def canvasPressEvent(self, e):
        if e.button() == Qt.RightButton:
            self.cancel()
            return
        if e.button() != Qt.LeftButton:
            return
        self.center = QgsPointXY(e.mapPoint())
        self.press_pos = e.pos()
        if self.footprint is None:
            self.footprint = Footprint(self.canvas)

    def canvasMoveEvent(self, e):
        if self.center is None:
            return
        rotate = bool(e.modifiers() & Qt.ShiftModifier)
        sq = self.square_for(self.center, e.mapPoint(), rotate)
        if sq is None:
            return
        self.footprint.show(self.frame_for(sq))
        km = sq[1] / 1000.0
        text = "%.2f × %.2f km" % (km, km) if km < 100 else "%.0f × %.0f km" % (km, km)
        text += "   %.1f°" % sq[2] if rotate else "   (Shift: rotate)"
        QToolTip.showText(self.canvas.mapToGlobal(e.pos()), text, self.canvas)

    def canvasReleaseEvent(self, e):
        if self.center is None or e.button() != Qt.LeftButton:
            return
        moved = (e.pos() - self.press_pos).manhattanLength() >= MIN_DRAG_PX
        sq = self.square_for(self.center, e.mapPoint(),
                             bool(e.modifiers() & Qt.ShiftModifier))
        self.center = None
        self._clear()
        if moved and sq is not None:
            self.squareDrawn.emit(sq)
        self.canvas.unsetMapTool(self)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.cancel()

    def cancel(self):
        self.center = None
        self._clear()
        self.canvas.unsetMapTool(self)

    def deactivate(self):
        self.center = None
        self._clear()
        QToolTip.hideText()
        super().deactivate()
        self.finished.emit()

    def _clear(self):
        if self.footprint is not None:
            self.footprint.remove()
            self.footprint = None
