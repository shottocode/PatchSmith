"""Runs all the tests (test/test_*.py), each with python -I, with PatchSmith's data in a temporary folder.

Usage: python -I test/run_all.py [--lavoro CARTELLA] [--sicurezza COPIA FILE_JSON] [--tieni] [prova ...]
- each test gets an empty folder of its own (prove/<nome>), holding its own PatchSmith data (game profiles,
  translation memory, settings, logs...: the PATCHSMITH_DATI folder) and its own %LOCALAPPDATA%, %APPDATA%, %TEMP%
  and user folder: the games' real options, crashes and saves are not touched, and a test does not see what
  another one left behind;
- each test's output goes to registri/<nome>.txt; a test that runs longer than MASSIMO_SECONDI is stopped, together
  with the processes it started;
- test_gui runs on a fake web game created here (PatchSmith's window opens for a few seconds), with some
  pre-existing settings that the test must put back as they were;
- test_safety needs a COPY of a game (--sicurezza <copia> <file.json relativo>): first
  `smith.py auto <copia> --backend finto` is run; without the copy the test is skipped;
- at the end it checks that PatchSmith's real data (game profiles, translation memory, settings...) has not changed;
- the work folder is deleted if everything goes well (with --tieni, or if you chose it yourself with --lavoro, it
  stays).
With some names (e.g. test_unity test_xna) it runs only those. Exit code: 0 if all the tests run pass and the real
data has not changed. The runner's functions are checked by test/test_run_all.py.
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

import fake_web_game as gioco_web_finto  # noqa: E402
from patchsmith import project as progetto  # noqa: E402

SENZA_ARGOMENTI = {"test_cjk", "test_placeholders"}
MASSIMO_SECONDI = 900


def prove(scelte):
    nomi = sorted(f[:-3] for f in os.listdir(QUI) if f.startswith("test_") and f.endswith(".py"))
    scelte = [os.path.basename(s)[:-3] if s.endswith(".py") else s for s in scelte]  # also "test_unity.py"
    sconosciute = [n for n in scelte if n not in nomi]
    if sconosciute:
        raise SystemExit("Prove sconosciute: %s" % ", ".join(sconosciute))
    return [n for n in nomi if not scelte or n in scelte]


def istantanea():
    """Size and date of every file of the user's real data (next to the program and, if set, in the
    PATCHSMITH_DATI of whoever runs the tests)."""
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
    """Environment variables of a test: PatchSmith data and Windows user folders all of its own, inside its folder
    (without the PATCHSMITH_* and OLLAMA_* of whoever runs the tests)."""
    amb = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PATCHSMITH_", "OLLAMA_"))}
    cartelle = {"PATCHSMITH_DATI": "dati", "LOCALAPPDATA": "utente/AppData/Local", "APPDATA": "utente/AppData/Roaming",
                "TEMP": "utente/Temp", "TMP": "utente/Temp", "USERPROFILE": "utente"}
    for variabile, rel in cartelle.items():
        amb[variabile] = os.path.join(cartella, *rel.split("/"))
        os.makedirs(amb[variabile], exist_ok=True)
    amb["PATCHSMITH_INTERFACCIA"] = "it"  # the tests check the Italian texts (the English one picks it by itself)
    return amb


def _ferma(processo):
    """Stops a test and the processes it started (a test server left running must not block the rest)."""
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(processo.pid), "/T", "/F"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    processo.kill()
    processo.wait()


def esegui(comando, amb, cwd, registro, massimo=None):
    """Runs a command with its output written to the log: a file, not a pipe, so a process started by the test and
    left running does not block the reading. (codice, uscita, secondi); codice None = stopped because it ran too
    long."""
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
    ap.add_argument("prove", nargs="*", help="solo queste (es. test_unity)")
    ap.add_argument("--work", "--lavoro", dest="lavoro", metavar="FOLDER",
                    help="cartella di lavoro (vuota o da creare); predefinita: una cartella temporanea")
    ap.add_argument("--safety", "--sicurezza", dest="sicurezza", nargs=2, metavar=("COPIA", "FILE_JSON"),
                    help="copia di un gioco e file JSON relativo per test_safety (la copia viene modificata)")
    ap.add_argument("--keep", "--tieni", dest="tieni", action="store_true",
                    help="non cancellare la cartella di lavoro alla fine")
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
        if nome == "test_safety" and not a.sicurezza:
            esiti[nome] = ("SALTATA", 0, "serve la copia di un gioco: --sicurezza <copia> <file.json>")
            print("SALTATA  %-28s serve la copia di un gioco: --sicurezza <copia> <file.json>" % nome, flush=True)
            continue
        os.makedirs(cartella)
        amb = ambiente(cartella)
        if nome == "test_gui":
            gioco = os.path.join(cartella, "Lantern Keeper")
            gioco_web_finto.crea(gioco)
            # pre-existing settings, as for someone who already uses PatchSmith: the test must put them back as
            # they were
            with open(os.path.join(amb["PATCHSMITH_DATI"], "settings.json"), "w", encoding="utf-8") as f:
                json.dump({"versione": 2, "nota": "impostazioni di prima della prova"}, f)
            argomenti = [gioco]
        elif nome == "test_safety":
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
