"""Avvio dell'interfaccia grafica con doppio clic (Windows: .pyw = senza finestra del terminale)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith.gui import main  # noqa: E402

main()
