"""Esegue tutte le prove (test/prova_*.py), ognuna con python -I, con i dati di PatchSmith in una cartella temporanea.

Uso: python -I test/esegui_tutto.py [--lavoro CARTELLA] [--sicurezza COPIA FILE_JSON] [--tieni] [prova ...]
- ogni prova riceve una cartella vuota tutta sua (prove/<nome>), con dentro i suoi dati di PatchSmith (profili,
  memoria, impostazioni, registri...: la cartella PATCHSMITH_DATI) e le sue %LOCALAPPDATA%, %APPDATA%, %TEMP% e
  cartella dell'utente: le opzioni dei giochi, i crash e i salvataggi veri non si toccano, e una prova non vede
  quello che ha lasciato un'altra;
- l'uscita di ogni prova va in registri/<nome>.txt; una prova che dura oltre MASSIMO_SECONDI si ferma, insieme ai
  processi che ha avviato;
- prova_gui si fa su un gioco web finto creato qui (si apre per qualche secondo la finestra di PatchSmith), con
  delle impostazioni gia' presenti che la prova deve rimettere com'erano;
- prova_sicurezza serve una COPIA di un gioco (--sicurezza <copia> <file.json relativo>): prima si esegue
  `smith.py auto <copia> --backend finto`; senza la copia la prova viene saltata;
- alla fine si controlla che i dati veri di PatchSmith (profili, memoria, impostazioni...) non siano cambiati;
- la cartella di lavoro si cancella se tutto va bene (con --tieni, o se l'hai scelta tu con --lavoro, resta).
Con dei nomi (es. prova_unity prova_xna) esegue solo quelle. Codice di uscita: 0 se tutte le prove eseguite passano
e i dati veri non sono cambiati. Le funzioni del runner le controlla test/prova_esegui_tutto.py.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import gioco_web_finto  # noqa: E402
from patchsmith import progetto  # noqa: E402

SENZA_ARGOMENTI = {"prova_cjk", "prova_segnaposto"}
MASSIMO_SECONDI = 900


def prove(scelte):
    nomi = sorted(f[:-3] for f in os.listdir(QUI) if f.startswith("prova_") and f.endswith(".py"))
    scelte = [os.path.basename(s)[:-3] if s.endswith(".py") else s for s in scelte]  # anche "prova_unity.py"
    sconosciute = [n for n in scelte if n not in nomi]
    if sconosciute:
        raise SystemExit("Prove sconosciute: %s" % ", ".join(sconosciute))
    return [n for n in nomi if not scelte or n in scelte]


def istantanea():
    """Dimensione e data di ogni file dei dati veri dell'utente (accanto al programma e, se c'e', in
    PATCHSMITH_DATI di chi lancia le prove)."""
    radici = {progetto.CARTELLA_PROGETTO}
    if progetto.RADICE_DATI:
        radici.add(progetto.RADICE_DATI)
    voci = {}
    for radice in radici:
        for nome in progetto.DATI_UTENTE + progetto.DATI_UTENTE_PRIMA:
            p = os.path.join(radice, nome)
            percorsi = [p] if os.path.isfile(p) else [os.path.join(r, f) for r, _, fs in os.walk(p) for f in fs]
            for q in percorsi:
                try:
                    s = os.stat(q)
                except OSError:
                    continue
                voci[q] = (s.st_size, s.st_mtime_ns)
    return voci


def ambiente(cartella):
    """Variabili d'ambiente di una prova: dati di PatchSmith e cartelle dell'utente di Windows tutte sue, dentro la
    sua cartella (senza le PATCHSMITH_* e OLLAMA_* di chi lancia le prove)."""
    amb = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PATCHSMITH_", "OLLAMA_"))}
    cartelle = {"PATCHSMITH_DATI": "dati", "LOCALAPPDATA": "utente/AppData/Local", "APPDATA": "utente/AppData/Roaming",
                "TEMP": "utente/Temp", "TMP": "utente/Temp", "USERPROFILE": "utente"}
    for variabile, rel in cartelle.items():
        amb[variabile] = os.path.join(cartella, *rel.split("/"))
        os.makedirs(amb[variabile], exist_ok=True)
    amb["PATCHSMITH_INTERFACCIA"] = "it"  # le prove controllano i testi italiani (quella in inglese la sceglie da se')
    return amb


def _ferma(processo):
    """Ferma una prova e i processi che ha avviato (un server di prova rimasto acceso non deve bloccare il resto)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(processo.pid), "/T", "/F"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    processo.kill()
    processo.wait()


def esegui(comando, amb, cwd, registro, massimo=None):
    """Esegue un comando con l'uscita scritta nel registro: un file, non una pipe, cosi' un processo avviato dalla
    prova e rimasto acceso non blocca la lettura. (codice, uscita, secondi); codice None = fermata perche' troppo
    lunga."""
    massimo = massimo or MASSIMO_SECONDI
    inizio = time.time()
    with open(registro, "ab") as f:
        f.write(("$ %s\n" % " ".join(comando)).encode("utf-8"))
        f.flush()
        da = f.tell()
        processo = subprocess.Popen(comando, cwd=cwd, env=amb, stdin=subprocess.DEVNULL, stdout=f,
                                    stderr=subprocess.STDOUT)
        try:
            codice = processo.wait(timeout=massimo)
        except subprocess.TimeoutExpired:
            _ferma(processo)
            codice = None
    with open(registro, "rb") as f:
        f.seek(da)
        uscita = f.read().decode("utf-8", "replace")
    if codice is None:
        nota = "\n(fermata: oltre %d s)\n" % massimo
        with open(registro, "a", encoding="utf-8") as f:
            f.write(nota)
        uscita += nota
    return codice, uscita, time.time() - inizio


def main(argv=None):
    ap = argparse.ArgumentParser(description="Esegue tutte le prove di PatchSmith senza toccare i dati veri.")
    ap.add_argument("prove", nargs="*", help="solo queste (es. prova_unity)")
    ap.add_argument("--lavoro", help="cartella di lavoro (vuota o da creare); predefinita: una cartella temporanea")
    ap.add_argument("--sicurezza", nargs=2, metavar=("COPIA", "FILE_JSON"),
                    help="copia di un gioco e file JSON relativo per prova_sicurezza (la copia viene modificata)")
    ap.add_argument("--tieni", action="store_true", help="non cancellare la cartella di lavoro alla fine")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    elenco = prove(a.prove)
    if a.lavoro:
        lavoro = os.path.abspath(a.lavoro)
        if os.path.exists(lavoro) and os.listdir(lavoro):
            raise SystemExit("La cartella di lavoro deve essere vuota: %s" % lavoro)
        os.makedirs(lavoro, exist_ok=True)
    else:
        lavoro = tempfile.mkdtemp(prefix="patchsmith-prove-")
    registri = os.path.join(lavoro, "registri")
    os.makedirs(registri)
    print("Cartella di lavoro: %s (ogni prova in prove\\<nome>, con i suoi dati di prova in prove\\<nome>\\dati)"
          % lavoro, flush=True)
    prima = istantanea()

    esiti = {}  # nome -> (stato, secondi, uscita); stato: "OK", "ERRORE", "SALTATA"
    for nome in elenco:
        registro = os.path.join(registri, nome + ".txt")
        cartella = os.path.join(lavoro, "prove", nome)
        script = os.path.join(QUI, nome + ".py")
        if nome == "prova_sicurezza" and not a.sicurezza:
            esiti[nome] = ("SALTATA", 0, "serve la copia di un gioco: --sicurezza <copia> <file.json>")
            print("SALTATA  %-28s serve la copia di un gioco: --sicurezza <copia> <file.json>" % nome, flush=True)
            continue
        os.makedirs(cartella)
        amb = ambiente(cartella)
        if nome == "prova_gui":
            gioco = os.path.join(cartella, "Lantern Keeper")
            gioco_web_finto.crea(gioco)
            # impostazioni gia' presenti, come per chi usa gia' PatchSmith: la prova deve rimetterle com'erano
            with open(os.path.join(amb["PATCHSMITH_DATI"], "settings.json"), "w", encoding="utf-8") as f:
                json.dump({"versione": 2, "nota": "impostazioni di prima della prova"}, f)
            argomenti = [gioco]
        elif nome == "prova_sicurezza":
            copia = os.path.abspath(a.sicurezza[0])
            codice, uscita, _ = esegui([sys.executable, "-I", os.path.join(os.path.dirname(QUI), "smith.py"), "auto",
                                        copia, "--backend", "finto"], amb, cartella, registro)
            if codice != 0:
                esiti[nome] = ("ERRORE", 0, uscita)
                print("ERRORE   %-28s prerequisito `smith auto` non riuscito (codice %s)" % (nome, codice), flush=True)
                continue
            argomenti = [copia, a.sicurezza[1]]
        elif nome in SENZA_ARGOMENTI:
            argomenti = []
        else:
            argomenti = [os.path.join(cartella, "base")]
        codice, uscita, secondi = esegui([sys.executable, "-I", script] + argomenti, amb, cartella, registro)
        stato = "OK" if codice == 0 else "ERRORE"
        esiti[nome] = (stato, secondi, uscita)
        nota = "" if codice == 0 else "  (fermata: oltre %d s)" % MASSIMO_SECONDI if codice is None else \
            "  (codice %s)" % codice
        print("%-8s %-28s %5.1f s%s" % (stato, nome, secondi, nota), flush=True)

    dopo = istantanea()
    cambiati = sorted((set(prima) ^ set(dopo)) | {p for p in set(prima) & set(dopo) if prima[p] != dopo[p]})
    falliti = [n for n, (stato, _, _) in esiti.items() if stato == "ERRORE"]
    for nome in falliti:
        righe = esiti[nome][2].strip().splitlines()
        print("\n--- %s (ultime righe; tutto in %s)" % (nome, os.path.join(registri, nome + ".txt")))
        print("\n".join(righe[-15:]))
    if cambiati:
        print("\nATTENZIONE: durante le prove sono cambiati dei dati veri di PatchSmith (se nel frattempo stavi usando "
              "PatchSmith o giocando con la traduzione dal vivo, puo' essere stato quello):")
        print("\n".join("  " + p for p in cambiati[:20]))
    eseguite = [n for n, (stato, _, _) in esiti.items() if stato != "SALTATA"]
    saltate = [n for n, (stato, _, _) in esiti.items() if stato == "SALTATA"]
    print("\nRISULTATO: %d prove superate su %d%s; dati veri %s" % (
        len(eseguite) - len(falliti), len(eseguite), " (saltate: %s)" % ", ".join(saltate) if saltate else "",
        "CAMBIATI" if cambiati else "invariati"))
    ok = not falliti and not cambiati
    if ok and not a.tieni and not a.lavoro:
        shutil.rmtree(lavoro, ignore_errors=True)
    else:
        print("Cartella di lavoro conservata: %s" % lavoro)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
