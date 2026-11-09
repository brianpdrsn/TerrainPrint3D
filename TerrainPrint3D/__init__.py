# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Brian Pedersen
def classFactory(iface):
    from .plugin import TerrainPrint3DPlugin
    return TerrainPrint3DPlugin(iface)
