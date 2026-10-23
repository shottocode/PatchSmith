"""Percorsi completi di chi usa solo l'interfaccia (revisione finale della 0.16): giochi finti, traduttori finti,
nessuna rete, nessun gioco vero, nessun Ollama.

Uso: python -I test/prova_percorsi.py <cartella_temporanea_vuota>
Apre la finestra di PatchSmith (come test/prova_gui.py: le finestre di dialogo si registrano e si risponde da qui) e
fa quello che farebbe chi gioca:
- un gioco web nuovo: Analizza e traduci, controllo dopo aver giocato, Correggi traduzioni, Ripristina e Riapplica,
  Crea rapporto;
- un gioco GameMaker nuovo (file di lingua alla maniera di Another Door): traduzione, una traduzione che il font non sa
  disegnare (scartata e mostrata in "Correggi traduzioni");
- un gioco Unity Mono nuovo: BepInEx e XUnity (pacchetti finti), il traduttore delle frasi nuove di PatchSmith; dopo una
  partita (registri veri ridotti di test/dati/segnali) il controllo all'apertura, "Dettagli" e "Ritraduci";
  una correzione fatta con il gioco aperto scritta dal controllo dopo, Ripristina e Riapplica, il rapporto;
- il gioco aperto prima di partire e durante il lavoro, il traduttore online in pausa o bloccato a meta', il traduttore
  locale non installato, testi rimasti in originale.
Per ogni finestra si controlla che dica in italiano semplice il passo dopo (un pulsante o un'istruzione), senza nomi
tecnici (smith.py, file JSON, traceback).
I dati di PatchSmith vanno in PATCHSMITH_DATI (test/esegui_tutto.py ne crea una per la prova); senza, in una
cartella dentro quella della prova.
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

import gioco_web_finto  # noqa: E402
import prova_controllo as pc  # noqa: E402  (gioco Unity finto, pacchetti finti, registri veri ridotti)
import prova_formati as pf  # noqa: E402  (gioco GameMaker alla Another Door, traduttore finto)
import unity_finto  # noqa: E402
from patchsmith import (adapter_unity, automatico, backend, controllo, correzioni, esito, gui, limitatore,  # noqa: E402
                        ollama, progetto, rapporto, servizio_xunity, sistema, steam, tema)

ESITI = []
FINESTRE = []  # (tipo, titolo, testo, pulsanti)
RISPOSTE = {}  # titolo -> risposta (o lista di risposte): None = OK/Annulla/finestra chiusa
# parole che dicono a chi gioca cosa fare dopo (un pulsante o un'istruzione nel testo)
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
    """La finestra dice il passo dopo (un pulsante diverso da OK/Annulla, o un'istruzione nel testo) senza nomi
    tecnici."""
    _, titolo, testo, pulsanti = f
    altri = [p for p, v in pulsanti if v not in (None, False, True)]
    finito = re.search(r"nessun problema|Avvia il gioco", testo)  # niente da fare: OK basta
    return not TECNICI.search(titolo + " " + testo) and bool(altri or finito or PASSO.search(testo))


def finestre_da(n):
    return FINESTRE[n:]


def ultima(titolo=None, da=0):
    scelte = [f for f in FINESTRE[da:] if titolo is None or f[1] == titolo]
    return scelte[-1] if scelte else ("", "", "", [])


def pulsanti(f):
    return [v for _, v in f[3]]


def aspetta(app, radice, secondi=120):
    """Aspetta la fine del lavoro in corso e lascia arrivare gli ultimi messaggi (e la finestra di fine)."""
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
    """Traduttore finto "vero" (non il backend 'finto': memoria vera): parole al contrario, riconoscibili; quelle in
    STRANE tornano con un carattere che nessun font ha o uguali all'originale."""
    nome = "dizionario"
    STRANE = {}

    def __init__(self):
        self.ricevuti = []

    def traduci_righe(self, righe, origine, destinazione, *_a, **_k):
        from patchsmith import interruzione
        interruzione.controlla()
        self.ricevuti += list(righe)
        return [self.STRANE.get(r, pf.ITALIANO.get(r, pf.finta(r))) for r in righe]


class Bloccato(Traduttore):
    """Traduce il primo gruppo di testi, poi il servizio online blocca le richieste (come un 429 di Google)."""
    max_testi = 3

    def traduci_righe(self, righe, origine, destinazione, *_a, **_k):
        if self.ricevuti:
            limitatore.Limitatore("google", log=None).bloccato(429, "prova")
            raise backend.Bloccato("Google ha rifiutato le richieste (troppe richieste): riprovo piu' tardi.",
                                   "TRADUTTORE_BLOCCATO")
        return super().traduci_righe(righe, origine, destinazione)


# --- preparazione ------------------------------------------------------------------------------------------

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
        if not os.environ.get("PATCHSMITH_DATI"):  # (con esegui_tutto sono gia' cartelle della prova)
            os.environ[v] = os.path.join(base, rel)
        os.makedirs(os.environ[v], exist_ok=True)
    steam.cartella_steam = lambda: None
    steam.gioco_installato = lambda cartella, steam=None: None
    steam.riconosci_da_eseguibili = lambda cartella: None
    controllo.eventi_crash = lambda dal: []  # mai il registro vero di Windows
    # il traduttore locale "installato" (stato simulato) e il traduttore finto al posto di tutti
    STATO["locale"] = "pronto"
    ollama.stato = lambda: STATO["locale"]
    ollama.installato = lambda: STATO["locale"] == "pronto"
    ollama.modello_presente = lambda chiave=None: STATO["locale"] == "pronto"
    automatico.crea_backend = controllo.crea_backend = crea_backend
    STATO["traduttore"] = Traduttore
    # Unity: pacchetti di BepInEx e XUnity gia' "scaricati", plugin del traduttore senza compilatore
    pc.prepara_componenti(os.path.join(base, "componenti"))
    adapter_unity._compila_plugin = pc._plugin_finto


STATO = {}


def crea_backend(nome, log=print):
    """Come backend.crea_backend, ma con il traduttore finto: il traduttore locale non installato non parte, quello
    online in pausa lo dice (pausa(), come GoogleGratis)."""
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


# --- 1. gioco web ------------------------------------------------------------------------------------------

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

    # controllo dopo aver giocato dal pulsante: niente da fare
    n = len(FINESTRE)
    app._controlla()
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("web: controllo dopo aver giocato -> nessun problema, con il passo dopo",
              fine[1] == "Controllo dopo aver giocato" and chiara(fine)
              and "nessun problema" in app.stato_controllo.get())

    # Correggi traduzioni: una riga corretta e salvata, poi Riapplica da sola
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

    # Ripristina (conferma) e poi Riapplica
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

    # Crea rapporto: lo zip con i registri, il profilo e i testi con problemi, senza file del gioco
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


# --- 2. gioco aperto, traduttore in pausa o bloccato, locale non installato, testi rimasti ---------------------

def percorso_imprevisti(app, radice, base):
    gioco = os.path.join(base, "giochi", "Ember Road")
    gioco_web_finto.crea(gioco)
    app.cartella.set(gioco)
    vero_aperto = sistema.gioco_aperto
    # gioco aperto prima di partire: niente parte, "Riprova" a gioco chiuso parte
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

    # traduttore online in pausa prima di partire: "Continua comunque" -> solo le traduzioni salvate
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
    # "Usa il traduttore locale" dalla finestra di fine: lo sceglie e traduce
    n = len(FINESTRE)
    app._azione("usa_locale", app.ultimo_esito)
    aspetta(app, radice)
    fine = ultima(da=n)
    controlla("... 'Usa il traduttore locale': scelto e tradotto ('%s')" % fine[1],
              app._scelto == "locale" and fine[1] == "Traduzione installata")

    # Google bloccato a meta' lavoro: "Traduzione interrotta", le traduzioni fatte restano
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

    # traduttore locale non installato: "Continua comunque" -> "Traduttore non pronto" con "Installa"; installato
    # (finto) -> la traduzione riparte da sola
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
        aspetta(app, radice)  # l'installazione
        aspetta(app, radice)  # e poi la traduzione che aspettava
    finally:
        ollama.descrizione_download, ollama.installa, backend.prova_locale = veri
        STATO["locale"] = "pronto"
    fine = ultima(da=n)
    controlla("'Installa ora' prima di partire: installa (finto), frase di prova, poi traduce da solo ('%s')"
              % fine[1], fine[1] == "Traduzione installata")

    # un testo rimasto in originale (il traduttore non lo traduce mai): "Mostra i rimasti" e "Correggi traduzioni"
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


# --- 3. gioco GameMaker ------------------------------------------------------------------------------------

def percorso_gamemaker(app, radice, base):
    gioco = os.path.join(base, "giochi", "Porta finta")
    pf.gioco_porta(gioco)
    originali = impronte(gioco)
    app.cartella.set(gioco)
    # il gioco aperto durante il lavoro (aperto dopo i controlli prima di partire): le traduzioni restano salvate e
    # non si scrive nulla; "Riprova" a gioco chiuso le scrive. Una traduzione ha un carattere che nessun font del gioco
    # ha (una stella): il gioco non la saprebbe mostrare
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
        app._azione("chiudi_gioco", app.ultimo_esito)  # "Riprova", a gioco chiuso
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
    # il controllo dopo aver giocato: niente da fare
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


# --- 4. gioco Unity Mono -----------------------------------------------------------------------------------

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

    # una correzione fatta con il gioco aperto: resta in attesa ("Riprova" non premuto, si gioca)
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

    # dopo la partita: i registri veri ridotti (Google fermo nel Player-prev.log, lettere che il font non ha nel
    # Player.log, crash all'avvio) e il diario delle frasi nuove
    time.sleep(1.2)
    dopo = time.time() - 0.5  # (dopo l'ultima scrittura di PatchSmith nel gioco, prima del controllo)
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
    """Alla prossima apertura di PatchSmith (finestra nuova): il controllo dopo aver giocato parte da solo, senza
    finestre; la riga sotto la cartella, "Dettagli", "Ritraduci"; poi Ripristina, Riapplica e il rapporto."""
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

    # Ripristina e Riapplica: tutto via e tutto di nuovo, con le lettere adattate
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

    # il rapporto: i registri di Unity e di BepInEx, la configurazione, il controllo, le lettere dei font
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
        radice.destroy()  # chiusa PatchSmith, si gioca; poi si riapre
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
