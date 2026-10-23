#!/usr/bin/env python3
"""PatchSmith: traduce i testi dei videogiochi direttamente nei file del gioco.

Uso:  python -I smith.py <comando> [opzioni]      (python -I smith.py -h per l'aiuto)
"""
import os
import sys

# Con -I la cartella dello script non e' nel path: la aggiungiamo esplicitamente.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
