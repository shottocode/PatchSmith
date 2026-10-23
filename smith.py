#!/usr/bin/env python3
"""PatchSmith: translates the texts of video games directly in the game's files.

Usage:  python -I smith.py <command> [options]      (python -I smith.py -h for help)
"""
import os
import sys

# With -I the script's folder is not on the path: we add it explicitly.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
