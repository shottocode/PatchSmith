"""Cartella dei dati dell'utente (PATCHSMITH_DATI) separata dal programma.

Uso: python -I test/test_user_data.py <cartella_temporanea_vuota>
Copre:
- con PATCHSMITH_DATI profili, memoria, impostazioni, stato dei traduttori, registri delle lingue e del configuratore,
  salvataggi vanno li'; componenti, plugin, regole e smith.py restano accanto al programma;
- senza la variabile tutto resta come prima (accanto al programma), anche per chi cambia CARTELLA_PROGETTO;
- riga di comando: `smith auto`, `stato` e `ripristina` su un gioco web finto con i dati nella cartella di prova;
- il comando del traduttore per XUnity scritto nel gioco usa sempre smith.py e la cartella del programma;
- le impostazioni dell'interfaccia si salvano anche se la cartella dei dati non esiste ancora.
I processi figli calcolano solo percorsi, tranne la riga di comando, che parte solo se la variabile e' rispettata.
"""
import hashlib
import json
import os
import subprocess
import sys
import types

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import fake_web_game as gioco_web_finto  # noqa: E402
from patchsmith import adapter_unity, gui, project as progetto  # noqa: E402

ESITI = []
SMITH = os.path.join(progetto.CARTELLA_PROGETTO, "smith.py")

# stampa i percorsi che PatchSmith userebbe (nessuna scrittura)
PERCORSI = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from patchsmith import (adapter_unity, auto as automatico, backend, components as componenti,
                        configurator as configuratore, gui, limiter as limitatore,
                        official_language as lingua_ufficiale, ollama, project as progetto,
                        detection as rilevamento)
profilo = {"nome": "gioco", "lingua_destinazione": "it"}
print(json.dumps({
    "dati": {
        "profili": automatico.cartella_profili(),
        "memoria": progetto.percorso_memoria(profilo),
        "comune": progetto.percorso_comune("en", "it"),
        "memoria_unity": adapter_unity._cartella_memoria("gioco"),
        "impostazioni": gui._impostazioni(),
        "stato_servizi": limitatore.percorso_stato(),
        "configurazioni": configuratore._registro(sys.argv[1]),
        "lingue": lingua_ufficiale._registro(sys.argv[1]),
        "registri": progetto.cartella_dati("logs"),
    },
    "programma": {
        "componenti": componenti.cartella(),
        "plugin": adapter_unity.SORGENTE_SERVIZIO,
        "ollama": ollama.cartella(),
        "regole": rilevamento.CARTELLA_DATI,
    },
    # solo con i dati di prova: le credenziali vere dell'utente non si leggono
    "chiave_deepl": backend.credenziali()["chiave_deepl"] if progetto.RADICE_DATI else None,
}))
"""


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def dentro(percorso, cartella):
    percorso, cartella = os.path.normcase(os.path.abspath(percorso)), os.path.normcase(os.path.abspath(cartella))
    return percorso.startswith(cartella.rstrip("\\/") + os.sep)


def ambiente(base, dati=None):
    """Ambiente dei processi figli: cartelle dell'utente di prova, PATCHSMITH_DATI solo se indicata."""
    amb = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PATCHSMITH_", "OLLAMA_"))}
    for variabile, rel in (("LOCALAPPDATA", "Local"), ("APPDATA", "Roaming"), ("TEMP", "Temp"), ("TMP", "Temp"),
                           ("USERPROFILE", "utente")):
        amb[variabile] = os.path.join(base, "ambiente", rel)
        os.makedirs(amb[variabile], exist_ok=True)
    if dati:
        amb["PATCHSMITH_DATI"] = dati
    return amb


def percorsi(base, dati):
    r = subprocess.run([sys.executable, "-I", "-c", PERCORSI, progetto.CARTELLA_PROGETTO], env=ambiente(base, dati),
                       cwd=base, capture_output=True, timeout=120)
    if r.returncode:
        print(r.stderr.decode("utf-8", "replace")[-1500:])
        return None
    return json.loads(r.stdout.decode("utf-8"))


def smith(base, dati, *argomenti):
    r = subprocess.run([sys.executable, "-I", SMITH] + list(argomenti), env=ambiente(base, dati), cwd=base,
                       capture_output=True, timeout=600)
    return r.returncode, r.stdout.decode("utf-8", "replace")


def impronte(cartella):
    voci = {}
    for r, _, fs in os.walk(cartella):
        for f in fs:
            with open(os.path.join(r, f), "rb") as g:
                voci[os.path.relpath(os.path.join(r, f), cartella)] = hashlib.sha256(g.read()).hexdigest()
    return voci


def test_paths(base):
    dati = os.path.join(base, "dati-variabile")
    os.makedirs(dati)
    with open(os.path.join(dati, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"chiave_deepl": "chiave-di-prova"}, f)
    con = percorsi(base, dati)
    rispettata = bool(con) and all(dentro(p, dati) for p in con["dati"].values())
    controlla("PATCHSMITH_DATI: tutti i dati dell'utente nella sua cartella", rispettata)
    if con and not rispettata:
        print("   fuori:", {k: p for k, p in con["dati"].items() if not dentro(p, dati)})
    controlla("PATCHSMITH_DATI: impostazioni (chiave DeepL) lette da li'",
              con and con["chiave_deepl"] == "chiave-di-prova")
    controlla("PATCHSMITH_DATI: componenti, plugin, traduttore locale e regole restano accanto al programma",
              con and all(dentro(p, progetto.CARTELLA_PROGETTO) for p in con["programma"].values()))
    senza = percorsi(base, None)
    controlla("senza PATCHSMITH_DATI: i dati restano accanto al programma, come prima",
              senza and all(dentro(p, progetto.CARTELLA_PROGETTO) for p in senza["dati"].values())
              and os.path.dirname(senza["dati"]["profili"]) == progetto.CARTELLA_PROGETTO)

    # chi sposta CARTELLA_PROGETTO (vecchi script di prova) sposta anche i dati, se la variabile non c'e'
    salvati = progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI
    try:
        progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = os.path.join(base, "altrove"), None
        controlla("RADICE_DATI vuota: i dati seguono CARTELLA_PROGETTO",
                  progetto.cartella_dati("memory") == os.path.join(base, "altrove", "memory"))
    finally:
        progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = salvati
    return rispettata


def prova_riga_di_comando(base):
    dati = os.path.join(base, "dati-cli")
    gioco = os.path.join(base, "giochi", "Lantern Keeper")
    rel = gioco_web_finto.crea(gioco)
    originali = impronte(gioco)
    codice, uscita = smith(base, dati, "auto", gioco, "--backend", "finto", "--a", "it")
    profili = os.listdir(os.path.join(dati, "profiles")) if os.path.isdir(os.path.join(dati, "profiles")) else []
    controlla("smith auto: tradotto, profilo e memoria nella cartella dei dati",
              codice == 0 and profili == ["lantern-keeper.json"]
              and os.path.exists(os.path.join(dati, "memory", "lantern-keeper.it.prova.json"))
              and os.path.exists(os.path.join(gioco, *rel.split("/")) + progetto.SUFFISSO_BACKUP))
    if codice:
        print("\n".join(uscita.strip().splitlines()[-10:]))
    codice, uscita = smith(base, dati, "stato", "lantern-keeper", "--esempi", "1")
    controlla("smith stato: il profilo si trova per nome nella cartella dei dati",
              codice == 0 and "Testi unici" in uscita)
    codice, _ = smith(base, dati, "ripristina", gioco)
    controlla("smith ripristina: gioco identico all'originale", codice == 0 and impronte(gioco) == originali)


def test_xunity_service(base):
    """Il comando scritto nel gioco: smith.py e cartella del programma, anche con i dati altrove."""
    gioco = os.path.join(base, "unity")
    os.makedirs(os.path.join(gioco, "BepInEx", "config"))
    profilo = os.path.join(base, "dati-servizio", "profiles", "gioco.json")
    compila = adapter_unity._compila_plugin
    salvata = progetto.RADICE_DATI
    try:
        adapter_unity._compila_plugin = lambda *_: None  # nessun compilatore: qui conta solo il comando
        progetto.RADICE_DATI = os.path.join(base, "dati-servizio")
        adapter_unity.attiva_servizio(gioco, {"dati": gioco}, profilo, "gioco")
    finally:
        adapter_unity._compila_plugin = compila
        progetto.RADICE_DATI = salvata
    with open(os.path.join(gioco, *adapter_unity.CONFIG_SERVIZIO.split("/")), encoding="utf-8") as f:
        righe = dict(r.split("=", 1) for r in f.read().splitlines() if "=" in r and not r.startswith("#"))
    controlla("servizio per XUnity: smith.py e cartella del programma, profilo con il suo percorso",
              ('"%s"' % SMITH) in righe.get("Argomenti", "") and ('--profilo "%s"' % profilo) in righe["Argomenti"]
              and righe.get("Cartella") == progetto.CARTELLA_PROGETTO)


def prova_impostazioni_gui(base):
    """_salva_impostazioni con la cartella dei dati che non esiste ancora (prima volta con PATCHSMITH_DATI)."""
    salvata = progetto.RADICE_DATI
    progetto.RADICE_DATI = os.path.join(base, "dati-gui", "nuova")
    try:
        finta = types.SimpleNamespace(
            _scelto="locale", cartella=types.SimpleNamespace(get=lambda: "C:/Giochi/Finto"),
            da=types.SimpleNamespace(get=lambda: "auto"), _lingua_a=lambda: "it", scelta_tema="auto",
            chiave_deepl="", email_mymemory="", _chiave_modello=lambda: "4b")
        gui.App._salva_impostazioni(finta)
        with open(os.path.join(progetto.RADICE_DATI, "settings.json"), encoding="utf-8") as f:
            salvate = json.load(f)
        controlla("interfaccia: impostazioni salvate nella cartella dei dati (creata se manca)",
                  salvate.get("cartella") == "C:/Giochi/Finto" and salvate.get("backend") == "locale")
    except OSError as e:
        controlla("interfaccia: impostazioni salvate nella cartella dei dati (creata se manca): %s" % e, False)
    finally:
        progetto.RADICE_DATI = salvata


def prova_nomi_inglesi(base):
    """I dati con i nomi italiani di prima della 0.1.0 passano a quelli inglesi (spostati, mai copiati ne' cancellati);
    finche' una cartella non e' spostata si usa quella vecchia; un gioco che chiede il profilo con il percorso vecchio
    lo ritrova."""
    from patchsmith import cli
    radice = os.path.join(base, "nomi-di-prima")
    vecchi = {"profili/gioco.json": "{}", "memoria/gioco.it.json": '{"a": "b"}', "memoria/copie/vecchia/x.json": "{}",
              "registri/op.log": "log", "impostazioni.json": '{"tema": "scuro"}', "stato_servizi.json": "{}",
              "componenti/ollama/ollama.exe": "exe", "componenti/plugin/Patchsmith.UIToolkit.dll": "dll",
              "components/plugin/PatchsmithServizio.cs": "cs"}  # (components/plugin c'e' gia': i sorgenti nuovi)
    for rel, testo in vecchi.items():
        p = os.path.join(radice, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(testo)
    salvati = progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI
    progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = radice, None
    try:
        prima = progetto.cartella_dati("profiles", "gioco.json")  # non ancora spostata: quella vecchia
        spostati = progetto.migra_dati()

        def leggi(rel):
            p = os.path.join(radice, *rel.split("/"))
            return open(p, encoding="utf-8").read() if os.path.isfile(p) else None
        nuovi = {"profiles/gioco.json": "{}", "memory/gioco.it.json": '{"a": "b"}', "memory/copies/vecchia/x.json": "{}",
                 "logs/op.log": "log", "settings.json": '{"tema": "scuro"}', "services.json": "{}",
                 "components/ollama/ollama.exe": "exe", "components/plugin/Patchsmith.UIToolkit.dll": "dll",
                 "components/plugin/PatchsmithServizio.cs": "cs"}
        vecchie = [n for n in ("profili", "memoria", "registri", "componenti", "impostazioni.json") if
                   os.path.exists(os.path.join(radice, n))]
        controlla("nomi inglesi: dati spostati (%s), nulla perso, componenti uniti, nessuna cartella vecchia (%s)"
                  % (sorted(spostati), vecchie), all(leggi(r) == t for r, t in nuovi.items()) and not vecchie
                  and prima == os.path.join(radice, "profili", "gioco.json"))
        di_nuovo = progetto.migra_dati()
        trovato = cli._profilo(os.path.join("C:\\", "vecchio", "PatchSmith", "profili", "gioco.json"))
        controlla("nomi inglesi: la seconda volta niente da fare; il profilo chiesto con il percorso di prima "
                  "(dal comando dentro un gioco gia' tradotto) si trova", di_nuovo == []
                  and trovato == os.path.join(radice, "profiles", "gioco.json"))
    finally:
        progetto.CARTELLA_PROGETTO, progetto.RADICE_DATI = salvati


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")  # anche questo processo: mai i dati veri
    if test_paths(base):
        prova_riga_di_comando(base)
    else:  # se la variabile non fosse rispettata, la riga di comando scriverebbe tra i dati veri
        controlla("riga di comando non provata: PATCHSMITH_DATI non rispettata", False)
    test_xunity_service(base)
    prova_impostazioni_gui(base)
    prova_nomi_inglesi(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
