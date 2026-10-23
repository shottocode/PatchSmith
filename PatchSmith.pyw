"""Starts the graphical interface with a double click (Windows: .pyw = no terminal window)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith.gui import main  # noqa: E402

main()
