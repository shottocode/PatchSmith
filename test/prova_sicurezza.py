"""Test di sicurezza dei file del gioco (backup, aggiornamenti, ripristino) su una COPIA di un gioco.

Uso: python -I test/prova_sicurezza.py <cartella_copia_del_gioco> <file_json_relativo>
oppure (consigliato) python -I test/esegui_tutto.py --sicurezza <cartella_copia_del_gioco> <file_json_relativo>
Prerequisito: sulla copia e' gia' stato eseguito `smith auto <copia> --backend finto` con la stessa cartella dei dati
(esegui_tutto.py lo fa da solo); se il profilo non c'e', la prova lo fa lei.
Profilo e memoria vanno nella cartella PATCHSMITH_DATI; senza, in "dati-prova-sicurezza" accanto alla copia: mai tra i
dati veri di PatchSmith. Non usare su un gioco vero.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import automatico, progetto  # noqa: E402
from patchsmith.progetto import SUFFISSO_BACKUP, SUFFISSO_INFO, trova_info  # noqa: E402

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
    if not automatico.trova_profilo(cartella):  # prerequisito non fatto con questa cartella dei dati
        automatico.analizza_e_traduci(cartella, backend="finto", log=silenzio)
    originale = impronta(backup)
    profili_prima = len(list(automatico.elenco_profili()))

    # 1) stessa partita scelta dalla cartella superiore: deve riusare il profilo
    superiore = os.path.dirname(cartella)
    controlla("cartella superiore -> stesso profilo", automatico.trova_profilo(superiore) == automatico.trova_profilo(cartella))
    automatico.analizza_e_traduci(superiore, backend="finto", log=silenzio)
    controlla("nessun profilo duplicato", len(list(automatico.elenco_profili())) == profili_prima)
    controlla("backup invariato dopo il secondo giro", impronta(backup) == originale)

    # 2) aggiornamento del gioco dopo la patch: il file nuovo diventa l'originale
    with open(backup, encoding="utf-8") as f:
        aggiornato = json.load(f)
    # sempre diverso dall'originale attuale, anche se la copia e' gia' stata usata da un test precedente
    aggiornato["title"] = str(aggiornato.get("title", "")) + " (aggiornamento di prova)"
    nuovo_byte = json.dumps(aggiornato, indent=2).encode("utf-8")
    with open(dati, "wb") as f:
        f.write(nuovo_byte)
    automatico.riapplica(cartella, silenzio, prova=True)
    info = json.load(open(dati + SUFFISSO_INFO, encoding="utf-8"))
    controlla("aggiornamento: nuovo originale registrato", impronta(backup) == hashlib.sha256(nuovo_byte).hexdigest())
    controlla("aggiornamento: vecchio originale conservato", info["precedenti"] and
              os.path.exists(os.path.join(os.path.dirname(dati), info["precedenti"][0])))

    # 3) backup alterato: niente scritture, niente ripristino distruttivo
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

    # 4) ripristino completo: tutto torna originale, niente file nostri rimasti
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
