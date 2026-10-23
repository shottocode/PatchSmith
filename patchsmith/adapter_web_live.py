"""Electron games whose game files are unreadable (e.g. encrypted with GemShell) but with resources/app/preload.js
in clear text: the texts are translated while the game runs, in its page, as XUnity does with Unity.

PatchSmith neither opens nor changes the encrypted file: it appends to preload.js (with a verified backup, which
"Restore originals" puts back) the injector_live.js script, which
- translates the page's texts with patchsmith-dictionary.json (the translations already made, from the game's
  translation memory);
- asks PatchSmith's translator for new sentences (xunity_service: the same service as for Unity games, on
  127.0.0.1, started by preload.js with the command in patchsmith-alive.json and closed with the game), which saves
  them to the translation memory.

It needs a preload.js that runs in the page with Node: in Electron's main.js contextIsolation:false and
sandbox:false (as in games made with GemShell). Otherwise the game cannot be translated this way.
"""
import hashlib
import json
import os
import re
import sys

from .adapter_webjson import write_atomic

# marker of our script inside the game's preload.js (first comment of injector_live.js): never change it, or the
# games translated before would no longer be recognized
MARKER = "Patchsmith - traduzione dal vivo"
DICTIONARY_NAME = "patchsmith-dizionario.json"
CONFIG_NAME = "patchsmith-vivo.json"
_NO_ISOLATION = re.compile(r"contextIsolation\s*:\s*false")
_SANDBOX = re.compile(r"sandbox\s*:\s*false")


def find_preload(folder, depth=4):
    """The game's resources/app/preload.js (also in a subfolder, e.g. game/), or None."""
    folder = os.path.abspath(folder)
    for root, dirs, files in os.walk(folder):
        if os.path.relpath(root, folder).count(os.sep) >= depth:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", "locales", "steam_settings")]
        if os.path.basename(root).lower() == "app" and os.path.basename(os.path.dirname(root)).lower() == "resources" \
                and "preload.js" in files and "main.js" in files:
            return os.path.join(root, "preload.js")
    return None


def suitable(folder):
    """preload.js if the game can be translated live (the preload runs in the page and has Node), otherwise None."""
    preload = find_preload(folder)
    if not preload:
        return None
    try:
        with open(os.path.join(os.path.dirname(preload), "main.js"), encoding="utf-8", errors="replace") as f:
            main_src = f.read()
    except OSError:
        return None
    return preload if _NO_ISOLATION.search(main_src) and _SANDBOX.search(main_src) else None


def installed(folder):
    preload = find_preload(folder)
    if not preload:
        return False
    with open(preload, "rb") as f:
        return MARKER.encode("utf-8") in f.read()


def _command(profile_path, profile_name):
    from . import adapter_unity, xunity_service
    from .project import PROJECT_FOLDER
    port = xunity_service.port_for(profile_name)
    return port, {"program": adapter_unity._windowless_python(), "folder": PROJECT_FOLDER,
                   "arguments": ["-I", os.path.join(PROJECT_FOLDER, "smith.py"), "xunity-service", "--profile",
                                 os.path.abspath(profile_path), "--port", str(port)]}


def install(folder, profile_path, profile_name, src_lang, a, translations, log=print, active=True):
    """Adds (or updates) the script in preload.js and writes the dictionary and the translator's command.
    attivo=False: no translator while playing (only the dictionary's translations). Returns the number of
    translations."""
    from . import project
    preload = suitable(folder)
    if not preload:
        raise ValueError("this game does not support live translation (preload.js is isolated or absent)")
    app = os.path.dirname(preload)
    with open(preload, "rb") as f:
        current = f.read()
    info = project.read_info(preload)
    backup = preload + project.BACKUP_SUFFIX
    if MARKER.encode("utf-8") in current and info and os.path.exists(backup):
        with open(backup, "rb") as f:  # already ours: start again from the saved original
            original = f.read()
    else:  # first time, or the game put back its own preload.js (an update): the backup is of this one
        project.create_backup(preload, extra={"created": [DICTIONARY_NAME, CONFIG_NAME], "web_live": True})
        original = current
    port, command = _command(profile_path, profile_name)
    config = {"dictionary": DICTIONARY_NAME, "port": port if active else 0, "from": src_lang, "to": a,
              "command": command if active else None}
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "injector_live.js"), encoding="utf-8") as f:
        script = f.read().replace("/*__CONFIG__*/{}", json.dumps(config, ensure_ascii=True))
    dictionary = {k: v for k, v in translations.items() if isinstance(k, str) and isinstance(v, str) and v and v != k}
    write_atomic(os.path.join(app, DICTIONARY_NAME),
                   json.dumps(dictionary, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"))
    write_atomic(os.path.join(app, CONFIG_NAME), json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8"))
    new = original.rstrip(b"\r\n") + b"\n" + script.encode("utf-8")
    if new != current:
        write_atomic(preload, new)
    info = project.read_info(preload) or {}
    fingerprint = hashlib.sha256(new).hexdigest()
    if fingerprint not in info.get("written_hashes", []):
        info["written_hashes"] = (info.get("written_hashes") or [])[-(project.MAX_FINGERPRINTS - 1):] + [fingerprint]
        project.save_info(preload, info)
    log("  Live translation installed in %s (with backup): %d translations ready%s" % (
        os.path.relpath(preload, folder), len(dictionary), ", the others arrive as you play" if active else
        " (translator while playing: off)"))
    if not os.path.exists(command["program"]) and sys.platform.startswith("win"):
        log("  ! I can't find Python (%s): the new phrases will stay untranslated" % command["program"])
    return len(dictionary)
