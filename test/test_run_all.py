"""Controlli sul runner delle prove (test/run_all.py), senza lanciare le prove vere.

Uso: python -I test/test_run_all.py <cartella_temporanea_vuota>
Copre:
- ogni prova ha i suoi dati (PATCHSMITH_DATI) e le sue cartelle dell'utente, dentro la sua cartella, senza le
  variabili PATCHSMITH_* e OLLAMA_* di chi lancia le prove: una prova non vede quello che ha lasciato un'altra;
- l'uscita va nel registro e il codice di uscita si conserva;
- una prova troppo lunga si ferma insieme ai processi che ha avviato, e il runner non resta bloccato;
- l'istantanea dei dati veri si accorge di un file cambiato, aggiunto o tolto;
- i nomi delle prove (anche con .py) e l'elenco delle prove senza argomenti, che deve restare giusto.
"""
import os
import re
import sys
import time

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import run_all as esegui_tutto  # noqa: E402
from patchsmith import project as progetto, xunity_service as servizio_xunity  # noqa: E402

ESITI = []

# prova finta che avvia un processo figlio (eredita l'uscita, come un server di prova) e poi non finisce
APPESA = r"""
import subprocess, sys, time
figlio = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"])
with open(sys.argv[1], "w") as f:
    f.write(str(figlio.pid))
print("avviato", flush=True)
time.sleep(120)
"""


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def dentro(percorso, cartella):
    percorso, cartella = os.path.normcase(os.path.abspath(percorso)), os.path.normcase(os.path.abspath(cartella))
    return percorso.startswith(cartella.rstrip("\\/") + os.sep)


def prova_ambiente(base):
    os.environ["PATCHSMITH_DATI"] = os.path.join(base, "dati-di-chi-lancia")
    os.environ["OLLAMA_HOST"] = "127.0.0.1:1"
    uno = esegui_tutto.ambiente(os.path.join(base, "prove", "prova_uno"))
    due = esegui_tutto.ambiente(os.path.join(base, "prove", "prova_due"))
    variabili = ("PATCHSMITH_DATI", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "USERPROFILE")
    controlla("ambiente: dati e cartelle dell'utente dentro la cartella della prova, gia' create",
              all(dentro(uno[v], os.path.join(base, "prove", "prova_uno")) and os.path.isdir(uno[v])
                  for v in variabili))
    controlla("ambiente: due prove non condividono dati ne' cartelle dell'utente",
              all(os.path.normcase(uno[v]) != os.path.normcase(due[v]) for v in variabili))
    controlla("ambiente: niente PATCHSMITH_* e OLLAMA_* di chi lancia le prove",
              "OLLAMA_HOST" not in uno and not dentro(uno["PATCHSMITH_DATI"], os.environ["PATCHSMITH_DATI"])
              and not [k for k in uno if k.upper().startswith("PATCHSMITH_")
                       and k not in ("PATCHSMITH_DATI", "PATCHSMITH_INTERFACCIA")]
              and uno.get("PATCHSMITH_INTERFACCIA") == "it")
    del os.environ["PATCHSMITH_DATI"], os.environ["OLLAMA_HOST"]


def prova_esegui(base):
    cartella = os.path.join(base, "esegui")
    os.makedirs(cartella)
    amb = esegui_tutto.ambiente(cartella)
    registro = os.path.join(cartella, "registro.txt")
    codice, uscita, _ = esegui_tutto.esegui(
        [sys.executable, "-I", "-c", "import os, sys; print('dati=' + os.environ['PATCHSMITH_DATI']); sys.exit(3)"],
        amb, cartella, registro)
    with open(registro, encoding="utf-8") as f:
        scritto = f.read()
    controlla("esegui: codice di uscita, uscita e registro", codice == 3 and "dati=" + amb["PATCHSMITH_DATI"] in uscita
              and "dati=" in scritto and scritto.startswith("$ "))

    pid_file = os.path.join(cartella, "figlio.pid")
    script = os.path.join(cartella, "appesa.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(APPESA)
    inizio = time.time()
    codice, uscita, _ = esegui_tutto.esegui([sys.executable, "-I", script, pid_file], amb, cartella, registro,
                                            massimo=3)
    durata = time.time() - inizio
    controlla("prova troppo lunga: fermata, il runner non resta bloccato (%.0f s)" % durata,
              codice is None and durata < 60 and "avviato" in uscita and "(fermata: oltre 3 s)" in uscita)
    try:
        with open(pid_file, encoding="utf-8") as f:
            pid = int(f.read())
    except (OSError, ValueError):
        pid = None
    for _ in range(50):
        if pid is None or not servizio_xunity.processo_vivo(pid):
            break
        time.sleep(0.1)
    controlla("prova troppo lunga: fermati anche i processi che aveva avviato",
              pid is not None and not servizio_xunity.processo_vivo(pid))


def prova_istantanea(base):
    salvati = progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI
    radice = os.path.join(base, "programma")
    for rel, testo in (("profiles/gioco.json", "{}"), ("memory/gioco.it.json", "{}"), ("settings.json", "{}"),
                       ("components/altro.txt", "x")):
        p = os.path.join(radice, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(testo)
    try:
        progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = radice, None
        prima = esegui_tutto.istantanea()
        controlla("istantanea: dati dell'utente si', componenti no",
                  os.path.join(radice, "profiles", "gioco.json") in prima
                  and os.path.join(radice, "settings.json") in prima
                  and not any("components" in p for p in prima))
        with open(os.path.join(radice, "memory", "gioco.it.json"), "w", encoding="utf-8") as f:
            f.write('{"a": "b"}')
        with open(os.path.join(radice, "memory", "nuovo.it.json"), "w", encoding="utf-8") as f:
            f.write("{}")
        os.remove(os.path.join(radice, "settings.json"))
        dopo = esegui_tutto.istantanea()
        cambiati = {p for p in set(prima) | set(dopo) if prima.get(p) != dopo.get(p)}
        controlla("istantanea: si accorge di un file cambiato, aggiunto e tolto", cambiati == {
            os.path.join(radice, "memory", "gioco.it.json"), os.path.join(radice, "memory", "nuovo.it.json"),
            os.path.join(radice, "settings.json")})
    finally:
        progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = salvati


def prova_nomi():
    tutte = esegui_tutto.prove([])
    controlla("nomi: anche con .py", esegui_tutto.prove(["test_unity.py"]) == ["test_unity"])
    try:
        esegui_tutto.prove(["prova_che_non_esiste"])
        sconosciuta = False
    except SystemExit:
        sconosciuta = True
    controlla("nomi: una prova sconosciuta si segnala", sconosciuta)
    senza = set()  # le prove che non leggono argomenti dalla riga di comando
    for nome in tutte:
        with open(os.path.join(QUI, nome + ".py"), encoding="utf-8") as f:
            if not re.search(r"sys\.argv\[", f.read()):
                senza.add(nome)
    controlla("prove senza argomenti: l'elenco del runner e' giusto (%s)" % ", ".join(sorted(senza)),
              senza == esegui_tutto.SENZA_ARGOMENTI)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")  # per sicurezza: queste prove non scrivono dati
    prova_ambiente(base)
    prova_esegui(base)
    prova_istantanea(base)
    prova_nomi()
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
