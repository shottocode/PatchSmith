"""Games with encrypted files (GemShell) and Steam emulator folders: PatchSmith does not touch them.

Usage: python -I test/test_encrypted.py <test_folder_that_does_not_exist>
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (auto as automatic, project, detection,  # noqa: E402
                        scan)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fingerprints(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for n in files:
            p = os.path.join(root, n)
            with open(p, "rb") as f:
                output[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return output


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    project.DATA_ROOT = os.path.join(base, "dati")
    automatic.PROFILES_FOLDER = os.path.join(project.DATA_ROOT, "profiles")
    achievements = json.dumps([{"name": "a1", "displayName": "Defeat the dragon",
                             "description": "Beat the first dragon of the game"}]).encode()

    # Electron game with files encrypted by GemShell (like "Now That's a Big Dragon")
    game = os.path.join(base, "Drago", "game")
    for name, data in {"chrome_100_percent.pak": b"pak", "v8_context_snapshot.bin": b"v8", "drago.exe": b"MZ",
                       "resources/app/main.js": b"require('./gemshell-native.node')",
                       "resources/app/package.json": b'{"main": "main.js"}',
                       "resources/app/gemshell-assets": b"GS02\x16\x00drago" + os.urandom(64),
                       "steam_settings/achievements.json": achievements}.items():
        write(os.path.join(game, *name.split("/")), data)
    outcome = detection.detect(game)
    check("Electron riconosciuto anche senza LICENSE.electron.txt; GemShell come archivio cifrato",
              outcome["motore_principale"] == "Electron" and detection.encrypted_container(outcome) == "GemShell")
    before = fingerprints(game)
    lines = []
    path, outcome = automatic.prepare(game, "auto", "it", "finto", lines.append)
    e = outcome.get("esito")
    check("file cifrati: non supportato, spiegato (nessuna traduzione sperimentale), nessun profilo",
              path is None and e is not None and e.code == "NON_SUPPORTATO" and "cifrato" in e.reason
              and "sperimentale" not in (e.actions or []))
    check("file cifrati: nessun file del gioco cambiato (neanche quello dell'emulatore di Steam)",
              fingerprints(game) == before)

    # ordinary web game with a Steam emulator folder next to it: only the game is translated
    web = os.path.join(base, "Web")
    write(os.path.join(web, "data", "testi.json"), json.dumps({"titolo": "Start the adventure now",
                                                                "esci": "Quit to the main menu"}).encode())
    write(os.path.join(web, "steam_settings", "achievements.json"), achievements)
    candidates, others = scan.scan_folder(web, lambda _m: None)
    paths = [c["percorso"] for c in candidates] + [a if isinstance(a, str) else a.get("percorso", "") for a in others]
    check("cartella di un emulatore di Steam (steam_settings) mai scansionata",
              not any("steam_settings" in p for p in paths) and any("testi.json" in p for p in paths))

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
