"""Safety test of the game's files (backups, updates, restore) on a COPY of a game.

Usage: python -I test/test_safety.py <game_copy_folder> <relative_json_file>
or (recommended) python -I test/run_all.py --safety <game_copy_folder> <relative_json_file>
Prerequisite: `smith auto <copy> --backend fake` has already been run on the copy with the same data folder
(run_all.py does it by itself); if the game profile is not there, the test does it itself.
Game profile and translation memory go in the PATCHSMITH_DATA folder; without it, in "dati-prova-sicurezza" next to
the copy: never among PatchSmith's real data. Do not use on a real game.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import auto as automatic, project  # noqa: E402
from patchsmith.project import BACKUP_SUFFIX, INFO_SUFFIX, find_info  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def silent(_):
    pass


def main(folder, rel_json):
    folder = os.path.abspath(folder)
    data = os.path.join(folder, *rel_json.split("/"))
    backup = data + BACKUP_SUFFIX
    if not os.environ.get("PATCHSMITH_DATA"):
        project.DATA_ROOT = os.path.join(os.path.dirname(folder), "dati-prova-sicurezza")
    if not automatic.find_profile(folder):  # prerequisite not done with this data folder
        automatic.analyze_and_translate(folder, backend="fake", log=silent)
    original = fingerprint(backup)
    profiles_before = len(list(automatic.list_profiles()))

    # 1) same game chosen from the parent folder: must reuse the game profile
    parent = os.path.dirname(folder)
    check("cartella superiore -> stesso profilo", automatic.find_profile(parent) == automatic.find_profile(folder))
    automatic.analyze_and_translate(parent, backend="fake", log=silent)
    check("nessun profilo duplicato", len(list(automatic.list_profiles())) == profiles_before)
    check("backup invariato dopo il secondo giro", fingerprint(backup) == original)

    # 2) game update after the patch: the new file becomes the original
    with open(backup, encoding="utf-8") as f:
        updated = json.load(f)
    # always different from the current original, even if the copy was already used by a previous test
    updated["title"] = str(updated.get("title", "")) + " (aggiornamento di prova)"
    new_bytes = json.dumps(updated, indent=2).encode("utf-8")
    with open(data, "wb") as f:
        f.write(new_bytes)
    automatic.reapply(folder, silent, test=True)
    info = json.load(open(data + INFO_SUFFIX, encoding="utf-8"))
    check("aggiornamento: nuovo originale registrato", fingerprint(backup) == hashlib.sha256(new_bytes).hexdigest())
    check("aggiornamento: vecchio originale conservato", info["prior"] and
              os.path.exists(os.path.join(os.path.dirname(data), info["prior"][0])))

    # 3) tampered backup: no writes, no destructive restore
    with open(backup, "rb") as f:
        good = f.read()
    with open(backup, "wb") as f:
        f.write(good[:20])
    before = fingerprint(data)
    try:
        automatic.reapply(folder, silent, test=True)
        check("backup alterato: riapplica si ferma", False)
    except RuntimeError:
        check("backup alterato: riapplica si ferma", True)
    automatic.restore(folder, silent)
    check("backup alterato: il file del gioco non viene toccato", fingerprint(data) == before)
    check("backup alterato: backup e info restano per il controllo manuale",
              os.path.exists(backup) and os.path.exists(data + INFO_SUFFIX))
    with open(backup, "wb") as f:
        f.write(good)

    # 4) full restore: everything goes back to original, none of our files left
    automatic.restore(folder, silent)
    check("ripristino: file dati = originale (aggiornato)", fingerprint(data) == hashlib.sha256(new_bytes).hexdigest())
    remaining = [os.path.join(r, f) for r, _, fs in os.walk(folder) for f in fs if "patchsmith-" in f]
    check("ripristino: nessun file patchsmith-* rimasto", not remaining and not find_info(folder))
    if remaining:
        print("   rimasti:", remaining)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1], sys.argv[2]) else 1)
