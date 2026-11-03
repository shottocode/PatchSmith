"""Oodle decompression (RAD Game Tools, now Epic): Unreal 4.27+ and 5 compress many files of their paks with it, the
.locres files with the texts too.

Oodle is proprietary: PatchSmith neither downloads nor distributes it. It uses an Oodle library (oo2core_<n>_win64.dll)
that is already there: the game's own, if the game ships it as a separate file, or one that the user put in
components/oodle/, taken from a game they own. The newest one found is used (a newer Oodle reads the data of the
older ones). Writing never needs Oodle: the paks written by PatchSmith are not compressed.
"""
import ctypes
import os
import re
import sys

from .project import components_folder

PATTERN = re.compile(r"^oo2core_(\d+)_win64\.dll$", re.I)
_found = {}  # game folder -> library path (or None)
_libraries = {}  # path -> loaded library


class OodleError(Exception):
    pass


def user_folder():
    """Where the user can put an Oodle library taken from a game they own: components/oodle/."""
    return components_folder("oodle")


def _version(path):
    m = PATTERN.match(os.path.basename(path))
    return int(m.group(1)) if m else -1


def find(game_folder=None):
    """The Oodle library to use: the newest among the game's own (anywhere in its folder) and those in
    components/oodle/. None if there is none."""
    key = os.path.normcase(os.path.abspath(game_folder)) if game_folder else None
    if key in _found:
        return _found[key]
    candidates = []
    for base in ([game_folder] if game_folder else []) + [user_folder()]:
        if not os.path.isdir(base):
            continue
        for root, _dirs, files in os.walk(base):
            candidates += [os.path.join(root, f) for f in files if PATTERN.match(f)]
    best = max(candidates, key=_version) if candidates else None
    _found[key] = best
    return best


def _load(path):
    if path not in _libraries:
        if not sys.platform.startswith("win"):
            raise OodleError("the Oodle library only works on Windows")
        try:
            library = ctypes.WinDLL(path)
            function = library.OodleLZ_Decompress
        except (OSError, AttributeError) as e:
            raise OodleError("%s cannot be used (%s)" % (os.path.basename(path), e)) from e
        function.restype = ctypes.c_ssize_t
        function.argtypes = [ctypes.c_char_p, ctypes.c_ssize_t, ctypes.c_char_p, ctypes.c_ssize_t,
                             ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_ssize_t,
                             ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t, ctypes.c_int]
        _libraries[path] = function
    return _libraries[path]


def decompress(data, size, library):
    """The 'size' bytes decompressed from 'data' with the Oodle library at 'library' path. The checks of Oodle are on
    (damaged data is refused, not read beyond the buffers)."""
    function = _load(library)
    output = ctypes.create_string_buffer(size)
    # fuzz safe, no CRC check, silent, no dictionary or callback, Oodle's own memory, single-threaded
    n = function(data, len(data), output, size, 1, 0, 0, None, 0, None, None, None, 0, 3)
    if n != size:
        raise OodleError("%s could not decompress the data (version too old for this game?)"
                         % os.path.basename(library))
    return output.raw
