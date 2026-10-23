"""External components (loaders and plugins) downloaded from the official releases, at pinned versions.

Each component has its URL, size and SHA-256 fingerprint written here: the downloaded file is used
only if the fingerprint matches. The files stay in components/ (cache), so they are downloaded once.
"""
import hashlib
import os
import urllib.request
import zipfile

from . import interruption
from .project import components_folder

FOLDER = None  # None: components/ next to the program (project.components_folder); the tests change it


def folder():
    return FOLDER or components_folder()

# name -> {url, file, size, sha256, license, version}
# Fingerprints read from the official GitHub releases API ("digest" field) on 2026-10-13.
CATALOG = {
    "bepinex5_x64": {
        "url": "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x64_5.4.23.5.zip",
        "file": "BepInEx_win_x64_5.4.23.5.zip", "size": 639118, "version": "5.4.23.5",
        "sha256": "82f9878551030f54657792c0740d9d51a09500eeae1fba21106b0c441e6732c4", "license": "LGPL-2.1",
    },
    "bepinex5_x86": {
        "url": "https://github.com/BepInEx/BepInEx/releases/download/v5.4.23.5/BepInEx_win_x86_5.4.23.5.zip",
        "file": "BepInEx_win_x86_5.4.23.5.zip", "size": 638544, "version": "5.4.23.5",
        "sha256": "37651c79e40d6f909572a4f461ac25350bb3ef8fe7fbd29f1aa8791a33b84c82", "license": "LGPL-2.1",
    },
    "xunity_bepinex5": {
        "url": "https://github.com/bbepis/XUnity.AutoTranslator/releases/download/v5.6.3.1/"
               "XUnity.AutoTranslator-BepInEx-5.6.3.1.zip",
        "file": "XUnity.AutoTranslator-BepInEx-5.6.3.1.zip", "size": 759511, "version": "5.6.3.1",
        "sha256": "6c3ff03aafe9708f25c2115c0c04c255b6a9d88e716896ae7bacb590a8777609", "license": "MIT",
    },
}


class ComponentError(Exception):
    pass


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def path(name):
    return os.path.join(folder(), CATALOG[name]["file"])


def is_available(name):
    """True if the component is already in the cache and intact."""
    p = path(name)
    return os.path.exists(p) and _sha256(p) == CATALOG[name]["sha256"]


def obtain(name, log=print):
    """Path of the component's file, downloading it if needed. Always verifies the fingerprint."""
    entry = CATALOG[name]
    dest_lang = path(name)
    if is_available(name):
        return dest_lang
    os.makedirs(folder(), exist_ok=True)
    temp_file = dest_lang + ".scaricamento"
    log("  downloading %s (%d KB) from %s" % (entry["file"], entry["size"] // 1024, entry["url"]))
    req = urllib.request.Request(entry["url"], headers={"User-Agent": "Mozilla/5.0 (patchsmith)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response, open(temp_file, "wb") as f:
            read_items = 0
            for block in iter(lambda: response.read(1 << 16), b""):
                interruption.check()
                read_items += len(block)
                if read_items > entry["size"] * 2 + 1_000_000:
                    raise ComponentError("%s: The downloaded file is too large, download interrupted" % name)
                f.write(block)
    except BaseException as e:  # "Stop" too: no half-finished downloads
        if os.path.exists(temp_file):
            os.remove(temp_file)
        if isinstance(e, OSError):
            raise ComponentError("Download of %s failed: %s" % (name, e)) from e
        raise
    fingerprint = _sha256(temp_file)
    if fingerprint != entry["sha256"]:
        os.remove(temp_file)
        raise ComponentError("%s: SHA-256 hash different from the expected one (%s): file discarded for safety"
                               % (name, fingerprint))
    os.replace(temp_file, dest_lang)
    return dest_lang


def zip_entries(name):
    """Paths (with /) contained in the component's zip."""
    with zipfile.ZipFile(path(name)) as z:
        return [i.filename for i in z.infolist()]
