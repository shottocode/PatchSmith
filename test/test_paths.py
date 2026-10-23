"""Complete paths of someone who only uses the interface (final review of 0.16): fake games, fake translators,
no network, no real game, no Ollama.

Usage: python -I test/test_paths.py <empty_temp_folder>
Opens PatchSmith's window (like test/test_gui.py: the dialogs are recorded and answered from here) and
does what a player would do:
- a new web game: Analyze and translate, after-play check, Fix translations, Restore and Reapply,
  Create report;
- a new GameMaker game (language files in the style of Another Door): translation, a translation the font cannot
  draw (rejected and shown in "Fix translations");
- a new Unity Mono game: BepInEx and XUnity (fake packages), PatchSmith's translator of new lines; after a
  session (real logs, reduced, from test/data/signals) the check on opening, "Details" and "Re-translate";
  a fix made with the game open, written by the check afterwards, Restore and Reapply, the report;
- the game open before starting and during the work, the online translator paused or blocked halfway, the local
  translator not installed, texts left untranslated.
For each dialog it checks that it tells the next step in plain Italian (a button or an instruction), without
technical names (smith.py, JSON file, traceback).
PatchSmith's data go to PATCHSMITH_DATI (test/run_all.py creates one for the test); without it, to a
folder inside the test's folder.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import time
import zipfile

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import tkinter as tk  # noqa: E402

import fake_web_game as gioco_web_finto  # noqa: E402
import test_checks as pc  # noqa: E402  (fake Unity game, fake packages, reduced real logs)
import test_formats as pf  # noqa: E402  (GameMaker game in the style of Another Door, fake translator)
import fake_unity as unity_finto  # noqa: E402
from patchsmith import (adapter_unity, auto as automatico, backend, checks as controllo, corrections as correzioni,
                        outcome as esito, gui, limiter as limitatore, ollama, project as progetto, report as rapporto,
                        xunity_service as servizio_xunity, system as sistema, steam, theme as tema)

ESITI = []
FINESTRE = []  # (type, title, text, buttons)
RISPOSTE = {}  # title -> answer (or list of answers): None = OK/Cancel/dialog closed
# words that tell the player what to do next (a button or an instruction in the text)
PASSO = re.compile(r"\b(premi|avvia|scegli|chiudi|riprova|installa|usa|apri|crea|correggi|lascia|gioca|controlla|"
                   r"riapplica|aspetta|puoi|serve|basta|sposta|incolla|riavvia|torna|resta|restano|dimmi|fai)\w*",
                   re.I)
TECNICI = re.compile(r"smith\.py|Traceback|\.json\b|\bNone\b|%[sd]\b|\{\}|Errno|Exception|KeyError|TypeError|"
                     r"\bprofilo\b|PATCHSMITH_", re.I)


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome, flush=True)


def silenzio(_):
    pass


def _risposta(titolo, predefinita=None):
    r = RISPOSTE.get(titolo, predefinita)
    if isinstance(r, list):
        r = r.pop(0) if r else predefinita
    return r


def _scegli(_g, _c, titolo, testo, pulsanti, tipo="info"):
    FINESTRE.append(("scegli", titolo, testo, list(pulsanti)))
    print("FINESTRA [%s] %s | %s | %s" % (tipo, titolo, " ".join(testo.split()), [p for p, _ in pulsanti]))
    return _risposta(titolo)


def _chiedi(_g, _c, titolo, testo):
    FINESTRE.append(("chiedi", titolo, testo, [("Sì", True), ("No", False)]))
    risposta = bool(_risposta(titolo, False))
    print("DOMANDA %s | %s | risposta: %s" % (titolo, " ".join(testo.split()), "sì" if risposta else "no"))
    return risposta


def _informa(_g, _c, titolo, testo):
    FINESTRE.append(("informa", titolo, testo, [("OK", None)]))
    print("AVVISO %s | %s" % (titolo, " ".join(testo.split())))


def chiara(f):
    """The dialog tells the next step (a button other than OK/Cancel, or an instruction in the text) without technical
    names."""
    _, titolo, testo, pulsanti = f
    altri = [p for p, v in pulsanti if v not in (None, False, True)]
    finito = re.search(r"nessun problema|Avvia il gioco", testo)  # nothing to do: OK is enough
    return not TECNICI.search(titolo + " " + testo) and bool(altri or finito or PASSO.search(testo))


def finestre_da(n):
    return FINESTRE[n:]


def ultima(titolo=None, da=0):
    scelte = [f for f in FINESTRE[da:] if titolo is None or f[1] == titolo]
    return scelte[-1] if scelte else ("", "", "", [])


def pulsanti(f):
    return [v for _, v in f[3]]


def aspetta(app, radice, secondi=120):
    """Waits for the end of the running work and lets the last messages (and the final dialog) arrive."""
    inizio = time.time()
    for _ in range(3):
        radice.update()
        time.sleep(0.02)
    while app.lavoro is not None and app.lavoro.is_alive() and time.time() - inizio < secondi:
        radice.update()
        time.sleep(0.03)
    for _ in range(25):
        radice.update()
        time.sleep(0.03)


def impronte(cartella):
    uscita = {}
    for r, _, fs in os.walk(cartella):
        for n in fs:
            p = os.path.join(r, n)
            with open(p, "rb") as f:
                uscita[os.path.relpath(p, cartella)] = hashlib.sha256(f.read()).hexdigest()
    return uscita


class Traduttore(backend.Backend):
    """A "real" fake translator (not the 'finto' backend: real translation memory): words reversed, recognizable;
    those in STRANE come back with a character no font has, or the same as the original."""
    nome = "dizionario"
    STRANE = {}

    def __init__(self):
        self.ricevuti = []

    def traduci_righe(self, righe, origine, destinazione, *_a, **_k):
        from patchsmith import interruption as interruzione
        interruzione.controlla()
        self.ricevuti += list(righe)
        return [self.STRANE.get(r, pf.ITALIANO.get(r, pf.finta(r))) for r in righe]


class Bloccato(Traduttore):
    """Translates the first group of texts, then the online service blocks the requests (like a Google 429)."""
    max_testi = 3

    def traduci_righe(self, righe, origine, destinazione, *_a, **_k):
        if self.ricevuti:
            limitatore.Limitatore("google", log=None).bloccato(429, "prova")
            raise backend.Bloccato("Google ha rifiutato le richieste (troppe richieste): riprovo piu' tardi.",
                                   "TRADUTTORE_BLOCCATO")
        return super().traduci_righe(righe, origine, destinazione)


# --- setup -------------------------------------------------------------------------------------------------

def prepara(base):
    tema.informa = tema.avvisa = tema.errore = _informa
    tema.chiedi = _chiedi
    tema.scegli = _scegli
    gui.mostra_file = lambda _p: None
    gui._apri = lambda _p: None
    rapporto.cartella_desktop = lambda: os.path.join(base, "Desktop")
    ollama.cartella = lambda: os.path.join(base, "ollama")
    automatico.SPAZIO_GIOCO = automatico.SPAZIO_DATI = 0
    ollama.modello_consigliato = lambda gb=None: ("4b", "La scheda video ha 4 GB: ti propongo il modello Veloce (4B).")
    if not os.environ.get("PATCHSMITH_DATI"):
        progetto.RADICE_DATI = os.path.join(base, "dati")
    gui.IMPOSTAZIONI = None
    for v, rel in (("LOCALAPPDATA", "appdata"), ("USERPROFILE", "utente"), ("TEMP", "temp")):
        if not os.environ.get("PATCHSMITH_DATI"):  # (with esegui_tutto they are already test folders)
            os.environ[v] = os.path.join(base, rel)
        os.makedirs(os.environ[v], exist_ok=True)
    steam.cartella_steam = lambda: None
    steam.gioco_installato = lambda cartella, steam=None: None
    steam.riconosci_da_eseguibili = lambda cartella: None
    controllo.eventi_crash = lambda dal: []  # never the real Windows log
    # the local translator "installed" (simulated state) and the fake translator in place of all of them
    STATO["locale"] = "pronto"
    ollama.stato = lambda: STATO["locale"]
    ollama.installato = lambda: STATO["locale"] == "pronto"
    ollama.modello_presente = lambda chiave=None: STATO["locale"] == "pronto"
    automatico.crea_backend = controllo.crea_backend = crea_backend
    STATO["traduttore"] = Traduttore
    # Unity: BepInEx and XUnity packages already "downloaded", the translator's plugin without a compiler
    pc.prepara_componenti(os.path.join(base, "componenti"))
    adapter_unity._compila_plugin = pc._plugin_finto


STATO = {}


def crea_backend(nome, log=print):
    """Like backend.crea_backend, but with the fake translator: the local translator not installed does not start,
    the online one, if paused, says so (pausa(), like GoogleGratis)."""
    if nome == "locale" and STATO["locale"] != "pronto":
        raise backend.ErroreBackend(backend.NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=backend.COMANDO_INSTALLA)
    t = STATO["traduttore"]()
    if nome in ("google", "deepl", "mymemory"):
        t.pausa = lambda: limitatore.Limitatore(nome, log=None).pausa()
    return t


def apri_finestra():
    radice = tk.Tk()
    app = gui.App(radice)
    for _ in range(20):
        radice.update()
        time.sleep(0.02)
    app.traduttore.set(gui.TRADUTTORI["locale"])
    app._cambia_traduttore()
    return radice, app


# --- 1. web game -------------------------------------------------------------------------------------------

def percorso_web(app, radice, base):
    gioco = os.path.join(base, "giochi", "Lantern Keeper")
    gioco_web_finto.crea(gioco)
    originali = impronte(gioco)
    app.cartella.set(gioco)
    n = len(FINESTRE)
    app._traduci()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("web: 'Analizza e traduci' con il traduttore locale -> 'Traduzione installata' con il riepilogo e il passo "
              "dopo (%s)" % fine[1], fine[1] == "Traduzione installata" and "Testi tradotti" in fine[2] and chiara(fine))
    file_testi = os.path.join(gioco, *gioco_web_finto.FILE_TESTI.split("/"))
    with open(file_testi, encoding="utf-8") as f:
        tradotto = f.read()
    controlla("web: il file del gioco e' tradotto", "Start a new game" not in tradotto)

    # after-play check from the button: nothing to do
    n = len(FINESTRE)
    app._controlla()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("web: controllo dopo aver giocato -> nessun problema, con il passo dopo",
              fine[1] == "Controllo dopo aver giocato" and chiara(fine)
              and "nessun problema" in app.stato_controllo.get())

    # Fix translations: one line fixed and saved, then Reapply on its own
    finestra = app._correggi()
    finestra._vai_a("Start a new game")
    finestra.testo_traduzione.delete("1.0", "end")
    finestra.testo_traduzione.insert("1.0", "Inizia una nuova partita")
    for _ in range(5):
        radice.update()
    n = len(FINESTRE)
    finestra.salva()
    aspetta(app, radice)
    fine = ultima(da=n)
    with open(file_testi, encoding="utf-8") as f:
        controlla("web: correzione salvata e scritta nel gioco da sola ('Traduzione riapplicata')",
                  "Inizia una nuova partita" in f.read() and fine[1] == "Traduzione riapplicata" and chiara(fine))

    # Restore (confirm) and then Reapply
    RISPOSTE["Ripristina"] = True
    n = len(FINESTRE)
    app._ripristina()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("web: 'Ripristina originali' -> 'Originali ripristinati', file identici all'originale",
              fine[1] == "Originali ripristinati" and impronte(gioco) == originali and chiara(fine))
    n = len(FINESTRE)
    app._riapplica()
    aspetta(app, radice)
    fine = ultima(da=n)
    with open(file_testi, encoding="utf-8") as f:
        controlla("web: 'Riapplica' dopo Ripristina -> di nuovo tradotto, con la correzione",
                  "Inizia una nuova partita" in f.read() and fine[1] == "Traduzione riapplicata" and chiara(fine))

    # Create report: the zip with the logs, the game profile and the texts with problems, without game files
    n = len(FINESTRE)
    app._crea_rapporto()
    aspetta(app, radice)
    fine = ultima(da=n)
    zips = glob.glob(os.path.join(glob.escape(rapporto.cartella_desktop()), "*.zip"))
    nomi = zipfile.ZipFile(zips[-1]).namelist() if zips else []
    controlla("web: 'Crea rapporto' -> 'Rapporto creato' sul Desktop con registri e profilo",
              fine[1] == "Rapporto creato" and any(x.startswith("registri/") for x in nomi)
              and any(x.startswith("profilo/") for x in nomi) and not any(x.endswith(".html") for x in nomi))
    return gioco


# --- 2. game open, translator paused or blocked, local one not installed, texts left untranslated -------------

def percorso_imprevisti(app, radice, base):
    gioco = os.path.join(base, "giochi", "Ember Road")
    gioco_web_finto.crea(gioco)
    app.cartella.set(gioco)
    vero_aperto = sistema.gioco_aperto
    # game open before starting: nothing starts, "Try again" with the game closed starts
    stato = {"aperto": True}
    sistema.gioco_aperto = lambda _c: stato["aperto"]
    RISPOSTE["Il gioco è aperto"] = ["riprova"]
    try:
        n = len(FINESTRE)
        app._traduci()
        domanda = ultima(da=n)
        stato["aperto"] = False
        aspetta(app, radice)
    finally:
        sistema.gioco_aperto = vero_aperto
    fine = ultima(da=n)
    controlla("gioco aperto prima di partire: 'Il gioco è aperto' con 'Riprova'; chiuso il gioco, 'Riprova' traduce",
              domanda[1] == "Il gioco è aperto" and "riprova" in pulsanti(domanda) and chiara(domanda)
              and fine[1] == "Traduzione installata")

    # online translator paused before starting: "Continue anyway" -> only the saved translations
    altro = os.path.join(base, "giochi", "Frost Hollow")
    gioco_web_finto.crea(altro)
    app.cartella.set(altro)
    app.traduttore.set(gui.TRADUTTORI["google"])
    app._cambia_traduttore()
    limitatore.Limitatore("google", log=None).bloccato(429, "prova")
    RISPOSTE["Traduttore in pausa"] = ["continua"]
    try:
        n = len(FINESTRE)
        app._traduci()
        aspetta(app, radice)
    finally:
        limitatore.Limitatore("google", log=None).azzera()
    pausa = ultima("Traduttore in pausa", da=n)
    fine = ultima(da=n)
    controlla("Google in pausa: finestra con 'Usa il traduttore locale'; 'Continua comunque' -> esito con il passo dopo "
              "(%s: %s)" % (fine[1], pulsanti(fine)), pausa[1] and "usa_locale" in pulsanti(pausa) and chiara(pausa)
              and fine[1] in ("Traduzione interrotta", "Traduzione installata in parte") and chiara(fine)
              and "usa_locale" in pulsanti(fine))
    # "Use the local translator" from the final dialog: selects it and translates
    n = len(FINESTRE)
    app._azione("usa_locale", app.ultimo_esito)
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("... 'Usa il traduttore locale': scelto e tradotto ('%s')" % fine[1],
              app._scelto == "locale" and fine[1] == "Traduzione installata")

    # Google blocked halfway through the work: "Translation interrupted", the translations done stay
    terzo = os.path.join(base, "giochi", "Moss Gate")
    gioco_web_finto.crea(terzo)
    app.cartella.set(terzo)
    app.traduttore.set(gui.TRADUTTORI["google"])
    app._cambia_traduttore()
    STATO["traduttore"] = Bloccato
    try:
        n = len(FINESTRE)
        app._traduci()
        aspetta(app, radice)
    finally:
        STATO["traduttore"] = Traduttore
        limitatore.Limitatore("google", log=None).azzera()
    fine = ultima(da=n)
    controlla("Google bloccato a meta': '%s' con il passo dopo e 'Usa il traduttore locale' (%s)"
              % (fine[1], pulsanti(fine)), fine[1] in ("Traduzione interrotta", "Traduzione installata in parte")
              and chiara(fine) and "usa_locale" in pulsanti(fine))

    # local translator not installed: "Continue anyway" -> "Translator not ready" with "Install"; once installed
    # (fake) -> the translation starts again on its own
    app.traduttore.set(gui.TRADUTTORI["locale"])
    app._cambia_traduttore()
    STATO["locale"] = "non installato"
    quarto = os.path.join(base, "giochi", "Salt Marsh")
    gioco_web_finto.crea(quarto)
    originali = impronte(quarto)
    app.cartella.set(quarto)
    RISPOSTE["Traduttore locale da installare"] = ["continua"]
    n = len(FINESTRE)
    app._traduci()
    aspetta(app, radice)
    assente = ultima("Traduttore locale da installare", da=n)
    fine = ultima(da=n)
    controlla("traduttore locale non installato: 'Installa ora' prima di partire; 'Continua comunque' -> '%s' con "
              "'Installa il traduttore locale', gioco non toccato" % fine[1],
              assente[1] and "installa_locale" in pulsanti(assente) and chiara(assente)
              and fine[1] == "Traduttore non pronto" and "installa_locale" in pulsanti(fine) and chiara(fine)
              and impronte(quarto) == originali)
    veri = ollama.descrizione_download, ollama.installa, backend.prova_locale

    def installa(scelti, log=print):
        STATO["locale"] = "pronto"
        return True

    ollama.descrizione_download = lambda scelti: ["Ollama (finto)", "modello Veloce (finto)"]
    ollama.installa = installa
    backend.prova_locale = lambda modello=None, log=print, **_k: {
        "ok": True, "messaggio": "pronto, sulla scheda video: \"Start game\" -> \"Inizia\".", "scheda": 1.0}
    RISPOSTE["Installare il traduttore locale?"] = True
    RISPOSTE["Traduttore locale da installare"] = ["installa_locale"]
    try:
        STATO["locale"] = "non installato"
        n = len(FINESTRE)
        app._traduci()
        aspetta(app, radice)  # the installation
        aspetta(app, radice)  # and then the translation that was waiting
    finally:
        ollama.descrizione_download, ollama.installa, backend.prova_locale = veri
        STATO["locale"] = "pronto"
    fine = ultima(da=n)
    controlla("'Installa ora' prima di partire: installa (finto), frase di prova, poi traduce da solo ('%s')"
              % fine[1], fine[1] == "Traduzione installata")

    # a text left untranslated (the translator never translates it): "Show remaining" and "Fix translations"
    quinto = os.path.join(base, "giochi", "Tide Bell")
    gioco_web_finto.crea(quinto)
    app.cartella.set(quinto)
    Traduttore.STRANE = {"Rest here to save your progress and recover your strength.": ""}
    try:
        n = len(FINESTRE)
        app._traduci()
        aspetta(app, radice)
    finally:
        Traduttore.STRANE = {}
    fine = ultima(da=n)
    controlla("testi rimasti in originale: '%s' con 'Mostra i rimasti' e 'Correggi traduzioni' (%s)"
              % (fine[1], pulsanti(fine)), fine[1] == "Traduzione installata in parte" and chiara(fine)
              and "correggi" in pulsanti(fine) and "Rest here" in fine[2] + json.dumps(app.ultimo_esito.esempi_rimasti))
    aperte = []
    app._correggi = lambda filtro=None: aperte.append(filtro)
    try:
        app._azione("correggi", app.ultimo_esito)
    finally:
        del app._correggi
    controlla("... 'Correggi traduzioni' apre l'elenco dei rimasti", aperte == ["rimasti"])
    finestra = app._correggi("rimasti")
    righe = len(finestra.albero.get_children()) if finestra else 0
    controlla("... la finestra mostra i testi rimasti (%d)" % righe, righe >= 1)
    if finestra:
        finestra.chiudi()


# --- 3. GameMaker game -------------------------------------------------------------------------------------

def percorso_gamemaker(app, radice, base):
    gioco = os.path.join(base, "giochi", "Porta finta")
    pf.gioco_porta(gioco)
    originali = impronte(gioco)
    app.cartella.set(gioco)
    # the game open during the work (opened after the checks before starting): the translations stay saved and
    # nothing is written; "Try again" with the game closed writes them. One translation has a character that no font
    # of the game has (a star): the game could not show it
    vero_aperto = progetto.gioco_aperto
    progetto.gioco_aperto = lambda _c: True
    Traduttore.STRANE = {"VULTURE TOKEN": "GETTONE ★ AVVOLTOIO"}
    try:
        n = len(FINESTRE)
        app._traduci()
        aspetta(app, radice)
    finally:
        progetto.gioco_aperto = vero_aperto
    fine = ultima(da=n)
    controlla("GameMaker aperto durante il lavoro: 'Il gioco è aperto' (traduzioni pronte, non scritte) con Riprova "
              "(%s: %s)" % (fine[1], pulsanti(fine)), fine[1] == "Il gioco è aperto" and "chiudi_gioco" in pulsanti(fine)
              and chiara(fine) and impronte(gioco) == originali)
    try:
        n = len(FINESTRE)
        app._azione("chiudi_gioco", app.ultimo_esito)  # "Try again", with the game closed
        aspetta(app, radice)
    finally:
        Traduttore.STRANE = {}
    fine = ultima(da=n)
    controlla("GameMaker: '%s' con il passo dopo; la traduzione che il font non sa disegnare porta all'elenco "
              "'Non scritte nel gioco' (%s)" % (fine[1], pulsanti(fine)),
              fine[1] == "Traduzione installata in parte" and chiara(fine) and "correggi" in pulsanti(fine)
              and esito.filtro_correzioni(app.ultimo_esito) == "gioco" and "\"Non scritte nel gioco\"" in fine[2]
              and "\"Rimasti in originale\"" not in fine[2])
    with open(os.path.join(gioco, "localization", "stringsEN.json"), encoding="utf-8") as f:
        tradotto = f.read()
    controlla("GameMaker: file inglese tradotto, la stella mai scritta nel gioco", "Gioca" in tradotto
              and "★" not in tradotto)
    # the after-play check: nothing to do
    n = len(FINESTRE)
    app._controlla()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("GameMaker: controllo dopo aver giocato con il passo dopo (%s)" % fine[1], chiara(fine))
    finestra = app._correggi("gioco")
    voci = [finestra.albero.item(i, "values")[0] for i in finestra.albero.get_children()] if finestra else []
    controlla("GameMaker: la traduzione con la stella e' in 'Non scritte nel gioco' (%s)" % voci[:3],
              any("VULTURE" in v for v in voci))
    if finestra:
        finestra.chiudi()
    RISPOSTE["Ripristina"] = True
    app._ripristina()
    aspetta(app, radice)
    controlla("GameMaker: Ripristina -> file identici all'originale", impronte(gioco) == originali)
    return gioco


# --- 4. Unity Mono game ------------------------------------------------------------------------------------

AZIENDA, PRODOTTO = "Studio Finto", "Lumen Mine"


def crea_unity(gioco):
    pc.crea_gioco_unity(gioco, PRODOTTO, "2022.3.10f1", AZIENDA, PRODOTTO)
    open(os.path.join(gioco, "UnityPlayer.dll"), "wb").write(b"finto")
    frasi = ["Start a new game", "Welcome to the old mine. Collect all the gems!", "Press any key to continue",
             "The door is locked. Find the key first.", "Settings and options", "Your lantern is running out of oil."]
    with open(os.path.join(gioco, PRODOTTO + "_Data", "Managed", "Assembly-CSharp.dll"), "wb") as f:
        f.write(unity_finto.dll(frasi))


ORIGINALI_UNITY = {}


def percorso_unity(app, radice, base):
    gioco = os.path.join(base, "giochi", PRODOTTO)
    crea_unity(gioco)
    originali = impronte(gioco)
    ORIGINALI_UNITY.update(originali)
    app.cartella.set(gioco)
    n = len(FINESTRE)
    Traduttore.STRANE = {"The door is locked. Find the key first.": "La porta è chiusa. Trova prima la chiave."}
    try:
        app._traduci()
        aspetta(app, radice)
    finally:
        Traduttore.STRANE = {}
    fine = ultima(da=n)
    controlla("Unity: 'Traduzione installata' con ALT+T e chi traduce le frasi nuove (%s)" % fine[1],
              fine[1] == "Traduzione installata" and "ALT+T" in fine[2] and "PatchSmith" in fine[2] and chiara(fine))
    ini = adapter_unity.leggi_ini(os.path.join(gioco, *adapter_unity.CONFIG_XUNITY.split("/")))
    controlla("Unity: frasi nuove al traduttore di PatchSmith (CustomTranslate), registro di Unity acceso",
              (ini.get("Service") or {}).get("Endpoint") == "CustomTranslate"
              and adapter_unity.servizio_attivo(gioco) and adapter_unity.registro_unity_attivo(gioco))
    percorso = automatico.trova_profilo(gioco)
    with open(percorso, encoding="utf-8") as f:
        profilo = json.load(f)
    nome = profilo["nome"]

    # a fix made with the game open: it stays waiting ("Try again" not pressed, the player plays)
    vero_aperto = sistema.gioco_aperto
    finestra = app._correggi()
    finestra._vai_a("Start a new game")
    finestra.testo_traduzione.delete("1.0", "end")
    finestra.testo_traduzione.insert("1.0", "Nuova partita")
    for _ in range(5):
        radice.update()
    sistema.gioco_aperto = lambda _c: True
    try:
        n = len(FINESTRE)
        finestra.salva()
        aspetta(app, radice)
    finally:
        sistema.gioco_aperto = vero_aperto
    domanda = ultima(da=n)
    controlla("Unity: correzione salvata con il gioco aperto: 'Il gioco è aperto' con 'Riprova' (%s)" % domanda[1],
              domanda[1] == "Il gioco è aperto" and chiara(domanda))
    controlla("Unity: la correzione aspetta di essere scritta", "Start a new game" in correzioni.da_scrivere(profilo))

    # after the session: the reduced real logs (Google stopped in Player-prev.log, letters the font lacks in
    # Player.log, crash on start) and the journal of new lines
    time.sleep(1.2)
    dopo = time.time() - 0.5  # (after PatchSmith's last write to the game, before the check)
    utente = os.environ["USERPROFILE"]
    locallow = os.path.join(utente, "AppData", "LocalLow", AZIENDA, PRODOTTO)
    errore_google = pc.con_percorso(pc.leggi(os.path.join(pc.SEGNALI, "unity-player-errore-google.log")),
                                    pc.COPIA_NUOVA, gioco)
    errore_google = errore_google.replace("One Million Coins_Data", PRODOTTO + "_Data")
    pc.scrivi(os.path.join(locallow, "Player-prev.log"), errore_google, dopo)
    lettere = ("Mono path[0] = '%s/%s_Data/Managed'\n" % (gioco.replace("\\", "/"), PRODOTTO) +
               "The character with Unicode value \\u00E8 was not found in the [LiberationSans SDF] font asset or any "
               "potential fallbacks. It was replaced by Unicode character \\u25A1 in text object [Testo].\n")
    pc.scrivi(os.path.join(locallow, "Player.log"), lettere, dopo)
    pc.scrivi(servizio_xunity.percorso_diario(nome), pc.leggi(os.path.join(pc.SEGNALI, "diario-richieste.jsonl")))
    os.utime(servizio_xunity.percorso_diario(nome), (dopo, dopo))
    return gioco, nome, percorso


def percorso_unity_dopo(app, radice, base, gioco, nome, percorso):
    """At the next opening of PatchSmith (new window): the after-play check starts on its own, without
    dialogs; the line under the folder, "Details", "Re-translate"; then Restore, Reapply and the report."""
    originali = ORIGINALI_UNITY
    n = len(FINESTRE)
    inizio = time.time()
    while (app.lavoro is None or app.lavoro.is_alive()) and time.time() - inizio < 60:
        radice.update()
        time.sleep(0.03)
    aspetta(app, radice)
    riga = app.stato_controllo.get()
    print("RIGA:", riga, "| Ritraduci:", app.btn_ritraduci.winfo_manager() and app.btn_ritraduci.cget("text"))
    controlla("Unity: controllo all'apertura senza finestre, la riga sotto la cartella con 'Dettagli' (%s)" % riga,
              len(FINESTRE) == n and riga.startswith("Dopo l'ultima partita") and
              app.btn_dettagli.winfo_manager() == "pack")
    with open(percorso, encoding="utf-8") as f:
        profilo = json.load(f)
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    controlla("Unity: lettere che il font non ha annotate e frasi gia' scritte adattate (è -> e')",
              "è" in (profilo.get("caratteri_mancanti") or "") and not any("è" in v for v in voci.values()))
    controlla("Unity: la correzione fatta a gioco aperto scritta nella cache dal controllo all'apertura (%s)"
              % voci.get("Start a new game"), voci.get("Start a new game") == "Nuova partita"
              and not correzioni.da_scrivere(profilo))
    n = len(FINESTRE)
    app._dettagli_controllo()
    dettagli = finestre_da(n)
    controlla("Unity: 'Dettagli' -> finestra con il passo dopo per ogni problema, anche la correzione scritta da sola "
              "(%s)" % [f[1] for f in dettagli], dettagli and all(chiara(f) for f in dettagli)
              and any("con il gioco aperto non era ancora nel gioco" in f[2] for f in dettagli))
    controlla("Unity: pulsante 'Ritraduci 5 frasi' nella riga (le frasi del diario)",
              app.btn_ritraduci.winfo_manager() == "pack" and app.btn_ritraduci.cget("text") == "Ritraduci 5 frasi")
    n = len(FINESTRE)
    app._ritraduci()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("Unity: 'Ritraduci 5 frasi' -> tradotte e messe nel gioco, finestra con il passo dopo (%s)" % fine[1],
              chiara(fine) and "5 frasi tradotte e messe nel gioco" in fine[2])

    # Restore and Reapply: everything removed and everything back, with the adapted letters
    RISPOSTE["Ripristina"] = True
    n = len(FINESTRE)
    app._ripristina()
    aspetta(app, radice)
    fine = ultima(da=n)
    rimasti = sorted(set(impronte(gioco)) - set(originali))
    controlla("Unity: Ripristina -> nessun file di PatchSmith, BepInEx o XUnity nel gioco (%s)" % rimasti[:5],
              fine[1] == "Originali ripristinati" and impronte(gioco) == originali)
    n = len(FINESTRE)
    app._riapplica()
    aspetta(app, radice)
    fine = ultima(da=n)
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    controlla("Unity: Riapplica -> di nuovo installato, correzione e lettere adattate (%s)" % fine[1],
              fine[1] == "Traduzione riapplicata" and voci.get("Start a new game") == "Nuova partita"
              and not any("è" in v for v in voci.values()) and adapter_unity.registro_unity_attivo(gioco))

    # the report: the Unity and BepInEx logs, the configuration, the check, the fonts' letters
    n = len(FINESTRE)
    app._crea_rapporto()
    aspetta(app, radice)
    zips = sorted(glob.glob(os.path.join(glob.escape(rapporto.cartella_desktop()), "*.zip")), key=os.path.getmtime)
    nomi = zipfile.ZipFile(zips[-1]).namelist() if zips else []
    print("RAPPORTO:", nomi)
    controlla("Unity: rapporto con Player.log, configurazione di XUnity e di BepInEx, controllo e diario",
              any(x.endswith("Player.log") for x in nomi) and any("AutoTranslatorConfig.ini" in x for x in nomi)
              and any("BepInEx.cfg" in x for x in nomi) and any(x.endswith(".controllo.json") for x in nomi)
              and any(x.endswith(".richieste.jsonl") for x in nomi))
    return gioco


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    prepara(base)
    radice, app = apri_finestra()
    try:
        percorso_web(app, radice, base)
        percorso_imprevisti(app, radice, base)
        percorso_gamemaker(app, radice, base)
        gioco, nome, percorso = percorso_unity(app, radice, base)
        radice.destroy()  # PatchSmith closed, the player plays; then it is reopened
        radice, app = apri_finestra()
        percorso_unity_dopo(app, radice, base, gioco, nome, percorso)
    finally:
        try:
            radice.destroy()
        except tk.TclError:
            pass
    oscure = [f for f in FINESTRE if not chiara(f) and f[1] not in ("Ripristina", "Installare il traduttore locale?")]
    for f in oscure:
        print("FINESTRA SENZA PASSO DOPO O CON NOMI TECNICI:", f[1], "|", " ".join(f[2].split()), f[3])
    controlla("ogni finestra mostrata dice il passo dopo, senza nomi tecnici (%d finestre)" % len(FINESTRE), not oscure)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
