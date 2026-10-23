"""Safety test of the game's files (backups, updates, restore) on a COPY of a game.

Usage: python -I test/test_safety.py <game_copy_folder> <relative_json_file>
or (recommended) python -I test/run_all.py --sicurezza <cartella_copia_del_gioco> <file_json_relativo>
Prerequisite: `smith auto <copia> --backend finto` has already been run on the copy with the same data folder
(run_all.py does it by itself); if the game profile is not there, the test does it itself.
Game profile and translation memory go in the PATCHSMITH_DATI folder; without it, in "dati-prova-sicurezza" next to
the copy: never among PatchSmith's real data. Do not use on a real game.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import auto as automatico, project as progetto  # noqa: E402
from patchsmith.project import SUFFISSO_BACKUP, SUFFISSO_INFO, trova_info  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def impronta(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def silenzio(_):
    pass


def main(cartella, rel_json):
    cartella = os.path.abspath(cartella)
    dati = os.path.join(cartella, *rel_json.split("/"))
    backup = dati + SUFFISSO_BACKUP
    if not os.environ.get("PATCHSMITH_DATI"):
        progetto.RADICE_DATI = os.path.join(os.path.dirname(cartella), "dati-prova-sicurezza")
    if not automatico.trova_profilo(cartella):  # prerequisite not done with this data folder
        automatico.analizza_e_traduci(cartella, backend="finto", log=silenzio)
    originale = impronta(backup)
    profili_prima = len(list(automatico.elenco_profili()))

    # 1) same game chosen from the parent folder: must reuse the game profile
    superiore = os.path.dirname(cartella)
    controlla("cartella superiore -> stesso profilo", automatico.trova_profilo(superiore) == automatico.trova_profilo(cartella))
    automatico.analizza_e_traduci(superiore, backend="finto", log=silenzio)
    controlla("nessun profilo duplicato", len(list(automatico.elenco_profili())) == profili_prima)
    controlla("backup invariato dopo il secondo giro", impronta(backup) == originale)

    # 2) game update after the patch: the new file becomes the original
    with open(backup, encoding="utf-8") as f:
        aggiornato = json.load(f)
    # always different from the current original, even if the copy was already used by a previous test
    aggiornato["title"] = str(aggiornato.get("title", "")) + " (aggiornamento di prova)"
    nuovo_byte = json.dumps(aggiornato, indent=2).encode("utf-8")
    with open(dati, "wb") as f:
        f.write(nuovo_byte)
    automatico.riapplica(cartella, silenzio, prova=True)
    info = json.load(open(dati + SUFFISSO_INFO, encoding="utf-8"))
    controlla("aggiornamento: nuovo originale registrato", impronta(backup) == hashlib.sha256(nuovo_byte).hexdigest())
    controlla("aggiornamento: vecchio originale conservato", info["precedenti"] and
              os.path.exists(os.path.join(os.path.dirname(dati), info["precedenti"][0])))

    # 3) tampered backup: no writes, no destructive restore
    with open(backup, "rb") as f:
        buono = f.read()
    with open(backup, "wb") as f:
        f.write(buono[:20])
    prima = impronta(dati)
    try:
        automatico.riapplica(cartella, silenzio, prova=True)
        controlla("backup alterato: riapplica si ferma", False)
    except RuntimeError:
        controlla("backup alterato: riapplica si ferma", True)
    automatico.ripristina(cartella, silenzio)
    controlla("backup alterato: il file del gioco non viene toccato", impronta(dati) == prima)
    controlla("backup alterato: backup e info restano per il controllo manuale",
              os.path.exists(backup) and os.path.exists(dati + SUFFISSO_INFO))
    with open(backup, "wb") as f:
        f.write(buono)

    # 4) full restore: everything goes back to original, none of our files left
    automatico.ripristina(cartella, silenzio)
    controlla("ripristino: file dati = originale (aggiornato)", impronta(dati) == hashlib.sha256(nuovo_byte).hexdigest())
    rimasti = [os.path.join(r, f) for r, _, fs in os.walk(cartella) for f in fs if "patchsmith-" in f]
    controlla("ripristino: nessun file patchsmith-* rimasto", not rimasti and not trova_info(cartella))
    if rimasti:
        print("   rimasti:", rimasti)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.exit(0 if main(sys.argv[1], sys.argv[2]) else 1)
