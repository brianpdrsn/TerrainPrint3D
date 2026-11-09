#!/bin/sh
# Runs the headless QGIS test using the QGIS.app bundle's Python.
Q=/Applications/QGIS.app/Contents
export PYTHONPATH="$Q/Resources/python:$Q/Resources/python/plugins:$PYTHONPATH"
export QGIS_PREFIX_PATH="$Q/MacOS"
export QT_QPA_PLATFORM=offscreen
exec "$Q/MacOS/bin/python3" "$(dirname "$0")/test_core.py"
