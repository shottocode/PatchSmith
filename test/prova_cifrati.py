"""Giochi con i file cifrati (GemShell) e cartelle degli emulatori di Steam: PatchSmith non li tocca.

Uso: python -I test/prova_cifrati.py <cartella_di_prova_che_non_esiste>
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import automatico, progetto, rilevamento, scansione  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def scrivi(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)


def impronte(cartella):
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for n in files:
            p = os.path.join(radice, n)
            with open(p, "rb") as f:
                uscita[os.path.relpath(p, cartella)] = hashlib.sha256(f.read()).hexdigest()
    return uscita


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    automatico.CARTELLA_PROFILI = os.path.join(progetto.RADICE_DATI, "profiles")
    obiettivi = json.dumps([{"name": "a1", "displayName": "Defeat the dragon",
                             "description": "Beat the first dragon of the game"}]).encode()

    # gioco Electron con i file cifrati da GemShell (come "Now That's a Big Dragon")
    gioco = os.path.join(base, "Drago", "game")
    for nome, dati in {"chrome_100_percent.pak": b"pak", "v8_context_snapshot.bin": b"v8", "drago.exe": b"MZ",
                       "resources/app/main.js": b"require('./gemshell-native.node')",
                       "resources/app/package.json": b'{"main": "main.js"}',
                       "resources/app/gemshell-assets": b"GS02\x16\x00drago" + os.urandom(64),
                       "steam_settings/achievements.json": obiettivi}.items():
        scrivi(os.path.join(gioco, *nome.split("/")), dati)
    esito = rilevamento.rileva(gioco)
    controlla("Electron riconosciuto anche senza LICENSE.electron.txt; GemShell come archivio cifrato",
              esito["motore_principale"] == "Electron" and rilevamento.contenitore_cifrato(esito) == "GemShell")
    prima = impronte(gioco)
    righe = []
    percorso, esito = automatico.prepara(gioco, "auto", "it", "finto", righe.append)
    e = esito.get("esito")
    controlla("file cifrati: non supportato, spiegato (nessuna traduzione sperimentale), nessun profilo",
              percorso is None and e is not None and e.codice == "NON_SUPPORTATO" and "cifrato" in e.motivo
              and "sperimentale" not in (e.azioni or []))
    controlla("file cifrati: nessun file del gioco cambiato (neanche quello dell'emulatore di Steam)",
              impronte(gioco) == prima)

    # gioco web normale con la cartella di un emulatore di Steam accanto: si traduce solo il gioco
    web = os.path.join(base, "Web")
    scrivi(os.path.join(web, "data", "testi.json"), json.dumps({"titolo": "Start the adventure now",
                                                                "esci": "Quit to the main menu"}).encode())
    scrivi(os.path.join(web, "steam_settings", "achievements.json"), obiettivi)
    candidati, altri = scansione.scansiona(web, lambda _m: None)
    percorsi = [c["percorso"] for c in candidati] + [a if isinstance(a, str) else a.get("percorso", "") for a in altri]
    controlla("cartella di un emulatore di Steam (steam_settings) mai scansionata",
              not any("steam_settings" in p for p in percorsi) and any("testi.json" in p for p in percorsi))

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
