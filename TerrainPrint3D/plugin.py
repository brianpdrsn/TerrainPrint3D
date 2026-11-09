# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

MENU = "&TerrainPrint3D"


class TerrainPrint3DPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dialog = None

    def initGui(self):
        icon = QIcon(os.path.join(os.path.dirname(__file__), "icon.svg"))
        self.action = QAction(icon, "TerrainPrint3D: terrain to 3D print…",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addRasterToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU, self.action)

    def unload(self):
        self.iface.removeRasterToolBarIcon(self.action)
        self.iface.removePluginRasterMenu(MENU, self.action)
        if self.dialog is not None:
            self.dialog.cleanup()
            self.dialog.deleteLater()
            self.dialog = None

    def run(self):
        from .dialog import TerrainPrintDialog
        if self.dialog is None:
            self.dialog = TerrainPrintDialog(self.iface, self.iface.mainWindow())
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()
