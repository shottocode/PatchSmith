"""Automatic test of the graphical interface.

Usage: python -I test/test_gui.py <test_game_folder>
Opens the window, sets the folder, presses "Analyze and translate" with the test backend (no network),
waits for the end, checks the log, restores the originals and closes. Do not use on a real game.
It also checks the end-of-operation dialogs (one for each outcome, outcome.py), the log saved to disk in
logs/ (also with an unexpected error and its traceback) and "Create report" (the zip goes to a test folder,
never to the real Desktop). From step 5: at the first start with no saved translator, the local translator "to
install" with "Install…" and the model suited to the (simulated) graphics card; the preliminary checks (game open and
"Try again", protected folder, language, Google paused, local translator to install: the operation does not start); a
copy of the game in another folder that reuses its translations; the "Re-translate" button after the check at
startup. From step 6: "Fix translations…" (a line fixed in the window and saved: valid translation memory, Reapply on
its own) and the "Fix translations" button in the end dialog (the rest in test/test_corrections.py).
Game profile, translation memory and settings go to the PATCHSMITH_DATI folder (test/run_all.py creates a temporary
one); without it, to "dati-prova-gui" next to the test game: never among PatchSmith's real data. The settings that
were already in that folder are put back as they were at the end (even if PATCHSMITH_DATI points to the real data).
"""
import glob
import json
import os
import shutil
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import fake_web_game as gioco_web_finto  # noqa: E402
from patchsmith import (auto as automatico, outcome as esito, gui, ollama, project as progetto, report as rapporto,
                        theme as tema)  # noqa: E402

# the expected title of the "Analyze and translate" end dialog for each outcome
TITOLI = {"OK": "Traduzione installata", "PARZIALE": "Traduzione installata in parte",
          "NULLA_DA_FARE": "Niente da fare", "UFFICIALE": "Il gioco ha già la tua lingua",
          "NON_SUPPORTATO": "Gioco non ancora supportato", "ANTICHEAT": "Gioco con anti-cheat",
          "GIOCO_APERTO": "Il gioco è aperto", "TRADUTTORE_BLOCCATO": "Traduzione interrotta",
          "TRADUTTORE_ASSENTE": "Traduttore non pronto", "GIA_ORIGINALE": "Gioco già originale",
          "PERMESSI": "File bloccati", "CARTELLA_SBAGLIATA": "Cartella da cambiare",
          "ERRORE": "Operazione non riuscita"}
FINESTRE = []  # (titolo, testo, pulsanti) of the choice dialogs shown by the interface
CONTROLLI = []


def controlla(nome, condizione):
    CONTROLLI.append(bool(condizione))
    print("%s: %s" % (nome, "OK" if condizione else "NO"))


def _scegli(_g, _c, titolo, testo, pulsanti, tipo="info"):
    FINESTRE.append((titolo, testo, list(pulsanti)))
    print("FINESTRA:", titolo, "|", " ".join(testo.split())[:160], "|", [p for p, _ in pulsanti])
    return None  # like closing with OK: no action


AVVISI = []  # titles of the warning dialogs


def _informa(_g, _c, titolo, testo):
    AVVISI.append(titolo)
    print("AVVISO:", titolo, "|", " ".join(testo.split())[:200])


def main(cartella):
    # the warning dialogs and the questions would wait for a click: they are printed, and questions are answered "no"
    tema.informa = _informa
    tema.avvisa = tema.errore = tema.informa
    tema.chiedi = lambda _g, _c, titolo, testo: print("DOMANDA (risposta: no):", titolo) or False
    tema.scegli = _scegli
    gui.mostra_file = lambda _p: None
    rapporti = os.path.join(os.path.dirname(os.path.abspath(cartella)), "rapporti-prova-gui")
    rapporto.cartella_desktop = lambda: rapporti  # never the real Desktop
    # the report must not copy the real logs of the live translator (components/ollama)
    ollama.cartella = lambda: os.path.join(os.path.dirname(os.path.abspath(cartella)), "ollama-prova-gui")
    automatico.SPAZIO_GIOCO = automatico.SPAZIO_DATI = 0  # the free space of the test disk does not count here
    # the graphics card (for the proposed model) is simulated: 8 GB -> Quality (12B)
    ollama.modello_consigliato = lambda gb=None: ("12b", "La tua scheda video ha 8 GB di memoria: ti propongo il "
                                                  "modello Qualità (12B).")
    if not os.environ.get("PATCHSMITH_DATI"):
        progetto.RADICE_DATI = os.path.join(os.path.dirname(os.path.abspath(cartella)), "dati-prova-gui")
    gui.IMPOSTAZIONI = None  # those of the test data folder
    impostazioni_prima = _leggi_byte(gui._impostazioni())  # the window rewrites them: they are put back at the end
    # the options of GameMaker games are in %LOCALAPPDATA%\<game name>: a test copy shares them with the real game,
    # so the test uses a folder of its own
    os.environ["LOCALAPPDATA"] = os.path.join(os.path.dirname(os.path.abspath(cartella)), "appdata-prova-gui")
    try:
        percorso, esito_ok = _collaudo(cartella, rapporti)
        _collaudo_lingue_salvate()
        _collaudo_diario_dal_vivo()
    finally:  # even if the test stops halfway
        if impostazioni_prima is None:
            if os.path.exists(gui._impostazioni()):
                os.remove(gui._impostazioni())
        else:
            with open(gui._impostazioni(), "wb") as f:
                f.write(impostazioni_prima)
    rimesse = _leggi_byte(gui._impostazioni()) == impostazioni_prima
    print("impostazioni com'erano prima:", "OK" if rimesse else "NO")
    esito_ok = esito_ok and rimesse and all(CONTROLLI)
    print("COLLAUDO GUI:", "OK" if esito_ok else "FALLITO")
    return percorso, esito_ok


def _aspetta(app, radice, secondi=120):
    """Waits for the end of the running job and lets the last messages (and the end dialog) arrive."""
    inizio = time.time()
    while (app.lavoro is None or app.lavoro.is_alive()) and time.time() - inizio < secondi:
        radice.update()
        time.sleep(0.05)
    for _ in range(20):
        radice.update()
        time.sleep(0.05)


def _registri():
    return sorted(glob.glob(os.path.join(glob.escape(gui.cartella_registri()), "*.log")), key=os.path.getmtime)


def _leggi_testo(percorso):
    with open(percorso, encoding="utf-8") as f:
        return f.read()


def _collaudo_lingue_salvate():
    """Folder, From and To can only be chosen (no hand-typed text); invalid saved languages (old settings) go back
    to the defaults."""
    with open(gui._impostazioni(), "w", encoding="utf-8") as f:
        json.dump({"da": "Klingon", "a": "xx-zz", "cartella": ""}, f)
    radice = tk.Tk()
    try:
        app = gui.App(radice)
        sola_scelta = [w for w in radice.winfo_children()[0].winfo_children() if isinstance(w, ttk.Entry)]
        controlla("cartella, Da e A solo da scegliere; lingue salvate non valide -> quelle predefinite",
                  app.da.get() == gui.lingua.etichetta(gui.lingua.AUTO)
                  and app.a.get() == gui.lingua.etichetta(gui.lingua.lingua_di_windows())
                  and sola_scelta and all(str(w.cget("state")) == "readonly" for w in sola_scelta))
    finally:
        radice.destroy()
    # translator and key chosen and the window closed without doing anything else: on reopening they are still there
    radice = tk.Tk()
    app = gui.App(radice)
    app.traduttore.set(gui.TRADUTTORI["deepl"])
    app._cambia_traduttore(scelto_ora=True)
    app.credenziale.set(" chiave-di-prova:fx ")
    app._chiudi()
    radice = tk.Tk()
    try:
        app = gui.App(radice)
        controlla("traduttore DeepL e chiave scelti, finestra chiusa: alla riapertura sono ancora scelti",
                  app._scelto == "deepl" and app.credenziale.get() == "chiave-di-prova:fx")
    finally:
        radice.destroy()


def _collaudo_diario_dal_vivo():
    """During play: the new lines of the translator's journal summarized in the log (only those from now on)."""
    from patchsmith import xunity_service as servizio_xunity
    nome = "gioco-dal-vivo-prova"
    diario = servizio_xunity.percorso_diario(nome)
    os.makedirs(os.path.dirname(diario), exist_ok=True)
    righe = [{"testo": "Old line", "esito": "ok", "backend": "locale", "risposta": "Riga vecchia"}]
    with open(diario, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in righe))
    radice = tk.Tk()
    try:
        app = gui.App(radice)
        app._diario_letto[nome] = os.path.getsize(diario)  # as at the first look: the earlier history does not count
        nuove = [{"testo": "Upgrades", "esito": "ok", "backend": "locale", "risposta": "Potenziamenti"},
                 {"testo": "Settings", "esito": "ok", "backend": "memoria", "risposta": "Impostazioni"},
                 {"testo": "Hire {{A}} warriors", "esito": "ok", "backend": "locale", "risposta": "Assumi {{A}} guerrieri"},
                 {"testo": "Strange text", "esito": "rifiutata", "backend": "locale", "risposta": "Strange text"}]
        with open(diario, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in nuove))
        app._riassumi_diario(nome)
        app._riassumi_diario(nome)  # nothing new: no extra line
        radice.update()
        app._svuota_coda()
        testo = app.testo.get("1.0", "end")
        controlla("mentre giochi: frasi nuove tradotte riassunte nel registro (non quelle dalla memoria ne' la storia "
                  "di prima), una volta sola",
                  testo.count("Mentre giochi: 2 frasi nuove tradotte") == 1 and "1 rimasta in originale" in testo
                  and "Riga vecchia" not in testo)
    finally:
        radice.destroy()
        os.remove(diario)


def _collaudo(cartella, rapporti):
    radice = tk.Tk()
    app = gui.App(radice)
    inizio = time.time()
    while app.modello_locale.get() != gui.MODELLI_LOCALI["12b"] and time.time() - inizio < 5:
        radice.update()  # (the model suited to the graphics card comes from a thread)
        time.sleep(0.02)
    controlla("primo avvio senza traduttore salvato: 'Traduttore locale' da installare con 'Installa…' e il modello "
              "adatto alla scheda video (12B con 8 GB)", app.traduttore.get() == gui.TRADUTTORI["locale"]
              and app._scelto == "locale" and app.stato_traduttore.cget("text") == "da installare"
              and app.btn_installa.winfo_manager() == "pack" and app.modello_locale.get() == gui.MODELLI_LOCALI["12b"])
    app.cartella.set(cartella)
    app.backend_forzato = "finto"  # test translator, not in the menu
    app._traduci()
    # while it works: no second job, no change of translator or installation halfway
    primo = app.lavoro
    app._avvia("Secondo lavoro", lambda _c: None)
    un_lavoro = app.lavoro is primo and all(w.instate(["disabled"]) for w in (
        app.btn_installa, app.scelta_traduttore, app.scelta_modello, app.btn_traduci, app.btn_rapporto))
    print("un lavoro alla volta, comandi disattivati:", "OK" if un_lavoro else "NO")
    _aspetta(app, radice)
    registro = app.testo.get("1.0", "end")
    stato = app.stato.get()
    esito_ok = "Fatto. Avvia il gioco" in registro and "ERRORE" not in registro and un_lavoro \
        and app.scelta_traduttore.instate(["!disabled", "readonly"])
    print("stato:", stato)
    print("\n".join(registro.strip().splitlines()[-6:]))

    # end dialog and log on disk
    controlla("finestra di fine 'Traduzione installata' anche per un gioco web, con il riepilogo",
              FINESTRE and FINESTRE[-1][0] == "Traduzione installata" and "Testi tradotti:" in FINESTRE[-1][1])
    salvati = _registri()
    testo = _leggi_testo(salvati[-1]) if salvati else ""
    controlla("registro salvato su disco (logs/AAAAMMGG-HHMMSS-<gioco>.log) con l'operazione e l'esito",
              salvati and "Analisi e traduzione" in testo and "Fatto. Avvia il gioco" in testo
              and "Esito: OK" in testo and os.path.basename(salvati[-1])[:8].isdigit())

    # a dialog for each outcome, with its title
    app._operazione = "traduci"
    sbagliati = []
    for codice, atteso in TITOLI.items():
        prima = len(FINESTRE)
        app._mostra_esito("Analisi e traduzione", esito.Esito(codice, "Motivo di prova.", motore="gamemaker",
                                                              scritto=codice in ("OK", "PARZIALE"), tradotti=3,
                                                              rimasti=1 if codice == "PARZIALE" else 0,
                                                              esempi_rimasti=["Quit"]))
        if len(FINESTRE) != prima + 1 or FINESTRE[-1][0] != atteso or "smith.py" in FINESTRE[-1][1]:
            sbagliati.append(codice)
    controlla("titolo della finestra per ogni esito%s" % (": sbagliati " + ", ".join(sbagliati) if sbagliati else ""),
              not sbagliati)
    finestre = len(FINESTRE)
    app._mostra_esito("Analisi e traduzione", esito.Esito("NON_SUPPORTATO", azioni=["sperimentale"],
                                                         extra={"motore_rilevato": "RenPy"}))
    controlla("motore non supportato con file di testo: domanda sulla traduzione sperimentale",
              len(FINESTRE) == finestre + 1 and FINESTRE[-1][0] == "Motore non ancora supportato"
              and "RenPy" in FINESTRE[-1][1] and ("Sì", "sperimentale") in FINESTRE[-1][2])

    # unexpected error during the job: dialog with the type, traceback in the log on disk
    vero = automatico.analizza_e_traduci

    def guasto(*_a, **_k):
        return {}["cartella_gioco"]

    automatico.analizza_e_traduci = guasto
    try:
        app._traduci()
        _aspetta(app, radice)
    finally:
        automatico.analizza_e_traduci = vero
    testo = _leggi_testo(_registri()[-1])
    controlla("errore imprevisto: finestra 'Errore imprevisto' con il tipo e 'Crea rapporto'",
              FINESTRE[-1][0] == "Errore imprevisto" and "KeyError" in FINESTRE[-1][1]
              and ("Crea rapporto", "crea_rapporto") in FINESTRE[-1][2])
    controlla("errore imprevisto: nel registro su disco con il traceback",
              "Traceback (most recent call last)" in testo and "KeyError: 'cartella_gioco'" in testo
              and "in guasto" in testo)

    # error in a window command (report_callback_exception): log and dialog
    finestre = len(FINESTRE)
    radice.after(0, lambda: 1 / 0)
    for _ in range(10):
        radice.update()
        time.sleep(0.02)
    testo = _leggi_testo(_registri()[-1])
    controlla("errore in un comando della finestra: nel registro su disco e in una finestra",
              "ZeroDivisionError" in testo and "Traceback" in testo and len(FINESTRE) == finestre + 1
              and FINESTRE[-1][0] == "Errore imprevisto")

    # after an operation without a folder (installation of the local translator) a window error still goes to the
    # 'interfaccia' log; a permission error in a command does not offer "Try again" (it would repeat the last
    # operation on the game) nor advice about the game
    app._avvia("Installazione del traduttore locale", lambda _c: True, serve_cartella=False, operazione="installa")
    _aspetta(app, radice)
    finestre = len(FINESTRE)

    def permessi():
        raise PermissionError(13, "Accesso negato", "C:/prova/bloccato.json")

    radice.after(0, permessi)
    for _ in range(10):
        radice.update()
        time.sleep(0.02)
    nomi = [os.path.basename(p) for p in _registri()]
    controlla("errore della finestra dopo l'installazione del traduttore locale: registro 'interfaccia', finestra "
              "senza 'Riprova'", nomi[-1].endswith("-interfaccia.log") and nomi[-2].endswith("-traduttore-locale.log")
              and len(FINESTRE) == finestre + 1 and FINESTRE[-1][0] == "Errore imprevisto"
              and not any(a == "chiudi_gioco" for _, a in FINESTRE[-1][2]) and "Programmi" not in FINESTRE[-1][1])

    # an error while looking for the game profile for the log name does not leave the buttons disabled
    vero = automatico.trova_profilo

    def profilo_rovinato(_c):
        raise AttributeError("'list' object has no attribute 'get'")

    automatico.trova_profilo = profilo_rovinato
    try:
        app._avvia("Prova del registro", lambda _c: True)
        _aspetta(app, radice)
    finally:
        automatico.trova_profilo = vero
    controlla("registro: un profilo rovinato non blocca l'operazione (pulsanti di nuovo attivi, registro aperto)",
              app.btn_traduci.instate(["!disabled"]) and "Prova del registro" in _leggi_testo(_registri()[-1]))

    # "Use the local translator" after an error (which has no choices in the outcome): Analyze and translate again
    # with the same choices as last time (e.g. "Translate anyway")
    chiamate, scelto = [], app.traduttore.get()
    app._traduci = lambda **kw: chiamate.append(kw)
    app._locale_pronto = lambda: True
    app._cambia_traduttore = lambda primo=False: None
    app._scelte_traduci = {"forza": True, "traduci_comunque": True}
    try:
        app._azione("usa_locale", esito.Esito("TRADUTTORE_ASSENTE", "Prova", azioni=["installa_locale"]))
    finally:
        del app._traduci, app._locale_pronto, app._cambia_traduttore
        app._scelte_traduci = {"forza": False, "traduci_comunque": False}
        app.traduttore.set(scelto)
    controlla("'Usa il traduttore locale' dopo un errore: stesse scelte dell'ultimo Analizza e traduci",
              chiamate == [{"forza": True, "traduci_comunque": True}])

    _collaudo_controllo(app, radice, cartella)
    _collaudo_ritraduci(app, radice)
    _collaudo_preliminari(app, radice, cartella)
    _collaudo_installazione(app, radice)
    _collaudo_correzioni(app, radice, cartella)

    # restore from the window: outcome OK, 'Originals restored'
    app._ripristina(chiedi=False)
    _aspetta(app, radice)
    controlla("ripristino: finestra 'Originali ripristinati'", FINESTRE[-1][0] == "Originali ripristinati")

    # Create report: zip in the test folder (instead of the Desktop) with the operation's log
    gia_presenti = set(glob.glob(os.path.join(glob.escape(rapporti), "*.zip")))  # from a previous run
    app._crea_rapporto()
    _aspetta(app, radice)
    creati = sorted(set(glob.glob(os.path.join(glob.escape(rapporti), "*.zip"))) - gia_presenti)
    nomi = zipfile.ZipFile(creati[0]).namelist() if creati else []
    controlla("Crea rapporto: finestra 'Rapporto creato' e zip con registri (anche dell'errore nella finestra), "
              "profilo e impostazioni", FINESTRE[-1][0] == "Rapporto creato" and len(creati) == 1
              and any(n.startswith("registri/") and n.endswith("-lantern-keeper.log") for n in nomi)
              and any(n.endswith("-interfaccia.log") for n in nomi)
              and "settings.json" in nomi and any(n.startswith("profilo/") for n in nomi))

    # Desktop that cannot be written to (e.g. controlled folder access): the report goes to the logs
    bloccato = os.path.join(rapporti, "non-una-cartella")
    open(bloccato, "w").close()  # a file instead of the folder: nothing can be written inside
    gia_presenti = set(glob.glob(os.path.join(glob.escape(gui.cartella_registri()), "*.zip")))
    rapporto.cartella_desktop = lambda: bloccato
    try:
        app._crea_rapporto()
        _aspetta(app, radice)
    finally:
        rapporto.cartella_desktop = lambda: rapporti
        os.remove(bloccato)
    ripiego = sorted(set(glob.glob(os.path.join(glob.escape(gui.cartella_registri()), "*.zip"))) - gia_presenti)
    controlla("Crea rapporto con il Desktop bloccato: rapporto nella cartella dei registri, finestra 'Rapporto creato'",
              len(ripiego) == 1 and FINESTRE[-1][0] == "Rapporto creato" and ripiego[0] in FINESTRE[-1][1])
    for p in ripiego:  # it does not stay among the test data
        os.remove(p)

    automatico.ripristina(cartella, print)
    percorso = automatico.trova_profilo(cartella)
    radice.destroy()
    return percorso, esito_ok


def _collaudo_controllo(app, radice, cartella):
    """After-play check: check of the chosen game, at startup (without dialogs), line under the folder,
    "Last session details" with the buttons for the right game, notice that PatchSmith was updated while the window
    is open."""
    from patchsmith import checks as controllo
    # from the button, on the web game just translated: nothing to do, end dialog and line
    finestre = len(FINESTRE)
    app._controlla()
    _aspetta(app, radice)
    controlla("controllo del gioco scelto: finestra di fine e riga 'Dopo l'ultima partita a ...'",
              len(FINESTRE) == finestre + 1 and FINESTRE[-1][0] == "Controllo dopo aver giocato"
              and app.stato_controllo.get() == "Dopo l'ultima partita a %s: nessun problema"
              % os.path.basename(os.path.normpath(cartella)))
    controlla("controllo: registro su disco con il risultato", "Controllo dopo aver giocato" in _leggi_testo(
        _registri()[-1]) and "Esito: OK" in _leggi_testo(_registri()[-1]))
    # at startup, with a problem to do in another game: no dialog, the line with 'Last session details'
    altro = os.path.join(os.path.dirname(os.path.abspath(cartella)), "Altro Gioco")
    vero = controllo.controlla

    def finto(percorso, applica=True, log=print, traduttore=None, eventi=None, **_k):
        log("Controllo dopo aver giocato: prova")
        return [controllo.Problema("CRASH", "errore", "Il gioco si e' chiuso all'improvviso.", azione="ripristina",
                                   stato="da_fare"),
                controllo.Problema("TEMPORANEI", "info", "Tolti 2 file temporanei.", stato="corretto")]

    vero_come = controllo.come_esito
    controllo.controlla = finto
    controllo.come_esito = lambda problemi, percorso=None, titolo=None: vero_come(problemi, None, "Altro Gioco")
    try:
        finestre = len(FINESTRE)
        app._controlla(["profilo-finto"])
        _aspetta(app, radice)
    finally:
        controllo.controlla, controllo.come_esito = vero, vero_come
    controlla("controllo all'apertura: nessuna finestra, riga '1 problema corretto, 1 da fare' e 'Dettagli'",
              len(FINESTRE) == finestre and app.stato_controllo.get() == "Dopo l'ultima partita a Altro Gioco: "
              "1 problema corretto, 1 da fare" and app.btn_dettagli.winfo_manager() == "pack")
    nomi = [os.path.basename(p) for p in _registri()]
    controlla("controllo all'apertura: registro 'controllo'", nomi[-1].endswith("-controllo.log"))
    # Last session details: the dialog with "To do" and "Restore originals"; once chosen, it applies to that game
    app.esiti_controllo[0].extra["cartella"] = altro
    scelte, vero_ripristina, vera_scegli = [], app._ripristina, tema.scegli

    def scegli(_g, _c, titolo, testo, pulsanti, tipo="info"):
        _scegli(_g, _c, titolo, testo, pulsanti, tipo)
        return "ripristina"

    app._ripristina = lambda chiedi=True: scelte.append(app.cartella.get())
    tema.scegli = scegli
    try:
        app._dettagli_controllo()
    finally:
        app._ripristina, tema.scegli = vero_ripristina, vera_scegli
        app.cartella.set(cartella)
    controlla("Dettagli: 'Qualcosa da fare' con 'Ripristina originali', per la cartella di quel gioco",
              FINESTRE[-1][0] == "Qualcosa da fare" and "Da fare:" in FINESTRE[-1][1]
              and ("Ripristina originali", "ripristina") in FINESTRE[-1][2] and scelte == [os.path.normpath(altro)])
    # a game that cannot be checked (unexpected error) does not stop the others: "to do" with the report
    def rotto(percorso, applica=True, log=print, traduttore=None, eventi=None, **_k):
        if percorso == "rotto":
            raise KeyError("cartella_gioco")
        return []

    controllo.controlla = rotto
    try:
        app._controlla(["rotto", "buono"])
        _aspetta(app, radice)
    finally:
        controllo.controlla = vero
    errori = [p for e in app.esiti_controllo for p in e.extra.get("controllo", []) if p["codice"] == "ERRORE"]
    controlla("controllo all'apertura: un gioco con un errore non ferma gli altri (errore da fare, con il rapporto)",
              len(app.esiti_controllo) == 2 and len(errori) == 1 and errori[0]["azione"] == "crea_rapporto"
              and "KeyError" in errori[0]["messaggio"] and "1 da fare" in app.stato_controllo.get())
    # "Try again" in the dialog of a check (game open): the check of that game again, not the last operation done
    # (e.g. a translation)
    rifatti, vero_controlla = [], app._controlla
    app._controlla = lambda profili=None, all_apertura=None: rifatti.append((profili, all_apertura))
    try:
        app._azione("chiudi_gioco", esito.Esito("GIOCO_APERTO", azioni=["chiudi_gioco"],
                                                extra={"controllo": [], "profilo": "profilo-x"}))
    finally:
        app._controlla = vero_controlla
    controlla("'Riprova' dopo un controllo con il gioco aperto: rifa' il controllo di quel gioco, con la finestra",
              rifatti == [(["profilo-x"], False)])
    # at startup, but you have already started something: it does not start right away, the line says so; it starts
    # by itself as soon as that finishes
    lavoro_prima = app.lavoro
    app.lavoro = object()
    app._controlla = lambda profili=None, all_apertura=None: rifatti.append((profili, all_apertura))
    try:
        rifatti.clear()
        app._avvia_controllo_iniziale(["profilo-finto"])
        riga = app.stato_controllo.get()
        for _ in range(10):
            radice.update()
            time.sleep(0.02)
        durante = list(rifatti)
        app.lavoro = None
        inizio = time.time()
        while not rifatti and time.time() - inizio < 4:
            radice.update()
            time.sleep(0.02)
    finally:
        app._controlla = vero_controlla
        app.lavoro = lavoro_prima
    del app._controlla
    controlla("controllo all'apertura durante un'altra operazione: la riga lo dice e parte da solo appena finisce "
              "(%s)" % rifatti, "appena finisce" in riga and durante == []
              and rifatti == [(["profilo-finto"], None)])
    # "Create report" from the Last session details of another game: the report is for that game, with its outcome
    rapporti_chiesti, vera_crea = [], rapporto.crea
    rapporto.crea = lambda c, destinazione=None, esito=None, **_k: rapporti_chiesti.append((c, esito)) or \
        os.path.join(destinazione or "", "rapporto-finto.zip")
    e_altro = esito.Esito("PARZIALE", azioni=["crea_rapporto"], extra={"controllo": [], "cartella": altro})
    try:
        app._azione("crea_rapporto", e_altro)
        _aspetta(app, radice)
    finally:
        rapporto.crea = vera_crea
        app.cartella.set(cartella)
    controlla("'Crea rapporto' dai Dettagli di un altro gioco: rapporto di quel gioco con il suo esito",
              rapporti_chiesti == [(os.path.normpath(altro), e_altro)])
    # PatchSmith updated while the window is open: notice, only once
    vera_impronta = gui.impronta_codice
    gui.impronta_codice = lambda: ("9.9.9", 0)
    try:
        prima = len(AVVISI)
        app._avvia("Prova", lambda _c: None, serve_cartella=False, registra=False)
        _aspetta(app, radice)
        app._avvia("Prova", lambda _c: None, serve_cartella=False, registra=False)
        _aspetta(app, radice)
    finally:
        gui.impronta_codice = vera_impronta
    controlla("PatchSmith aggiornato con la finestra aperta: avviso di riaprirla, una volta sola",
              AVVISI[prima:] == ["PatchSmith è stato aggiornato"])


def _collaudo_ritraduci(app, radice):
    """The check at startup does not re-translate the journal's lines (the window would freeze): a
    "Re-translate N lines" button in the line, which (like "Re-translate the phrases" in the Last session details)
    redoes the check with the re-translation."""
    from patchsmith import checks as controllo
    ricevuti, vero, vero_come = [], controllo.controlla, controllo.come_esito

    def finto(percorso, applica=True, log=print, traduttore=None, eventi=None, ritraduci=True):
        ricevuti.append(ritraduci)
        return [controllo.Problema("FRASI_DA_RITRADURRE", "avviso", "Mentre giocavi, 3 frasi sono rimaste in "
                                   "originale.", azione="ritraduci", stato="da_fare", extra={"frasi": 3})]

    def come(problemi, percorso=None, titolo=None):
        e = vero_come(problemi, None, "Gioco Diario")
        e.extra["profilo"] = percorso
        return e

    controllo.controlla, controllo.come_esito = finto, come
    try:
        app._controlla(["profilo-diario"])
        _aspetta(app, radice)
    finally:
        controllo.controlla, controllo.come_esito = vero, vero_come
    controlla("controllo all'apertura: le frasi del diario non si ritraducono adesso, pulsante 'Ritraduci 3 frasi' "
              "nella riga", ricevuti == [False] and app.btn_ritraduci.winfo_manager() == "pack"
              and app.btn_ritraduci.cget("text") == "Ritraduci 3 frasi")
    rifatti = []
    app._controlla = lambda profili=None, all_apertura=None, ritraduci=None: rifatti.append(
        (profili, all_apertura, ritraduci))
    try:
        app._ritraduci()
        app._azione("ritraduci", app.esiti_controllo[0])
    finally:
        del app._controlla
    controlla("'Ritraduci' (riga e Dettagli): il controllo di quel gioco con la ritraduzione e le finestre",
              rifatti == [(["profilo-diario"], False, True)] * 2)
    app.esiti_controllo = []
    app._aggiorna_riga_controllo()


def _collaudo_preliminari(app, radice, cartella):
    """The preliminary checks: with a problem the operation does not start (_avvia is not called) and the dialog
    offers the right step; then a copy of the game in another folder reuses its translations."""
    from patchsmith import limiter as limitatore, system as sistema
    avviati, risposte = [], []
    app._avvia = lambda descrizione, *_a, **_k: avviati.append(descrizione)
    vera_scegli = tema.scegli

    def scegli(_g, _c, titolo, testo, pulsanti, tipo="info"):
        _scegli(_g, _c, titolo, testo, pulsanti, tipo)
        return risposte.pop(0) if risposte else None  # no answer: Cancel (or OK)

    tema.scegli = scegli
    vero_aperto = sistema.gioco_aperto
    try:
        # game open: "Try again"; when cancelled nothing starts
        sistema.gioco_aperto = lambda _c: True
        try:
            app._traduci()
            app._riapplica()
            app._ripristina(chiedi=False)
        finally:
            sistema.gioco_aperto = vero_aperto
        controlla("gioco aperto prima di Analizza e traduci, Riapplica e Ripristina: 'Il gioco è aperto' con "
                  "'Riprova', nessuna operazione avviata", avviati == []
                  and [f[0] for f in FINESTRE[-3:]] == ["Il gioco è aperto"] * 3 and ("Riprova", "riprova")
                  in FINESTRE[-1][2])
        stato = {"aperto": True}
        sistema.gioco_aperto = lambda _c: stato["aperto"]
        risposte[:] = ["riprova"]
        try:
            app._traduci()
            stato["aperto"] = False  # game closed: "Try again" redoes the checks and starts
            for _ in range(15):
                radice.update()
                time.sleep(0.02)
        finally:
            sistema.gioco_aperto = vero_aperto
        controlla("'Riprova' a gioco chiuso: di nuovo i controlli, poi l'operazione parte",
                  avviati == ["Analisi e traduzione"])
        avviati.clear()
        # protected folder and wrong language: it stops, with the reason
        vera_prova = automatico.prova_scrittura
        automatico.prova_scrittura = lambda _c: PermissionError(13, "Accesso negato")
        try:
            app._traduci()
        finally:
            automatico.prova_scrittura = vera_prova
        controlla("cartella protetta: finestra con il motivo e solo OK, nessuna operazione",
                  avviati == [] and FINESTRE[-1][0] == "Cartella del gioco protetta" and FINESTRE[-1][2] == [("OK", None)]
                  and "amministratore" in FINESTRE[-1][1])
        a_prima = app.a.get()
        app.a.set("Italianoo")
        try:
            app._traduci()
        finally:
            app.a.set(a_prima)
        controlla("lingua non riconosciuta in A: 'Lingua da controllare', nessuna operazione",
                  avviati == [] and FINESTRE[-1][0] == "Lingua da controllare" and "Italianoo" in FINESTRE[-1][1])
        # Google paused: "Install the local translator" chooses it, installs it and then repeats Analyze and translate
        scelto, app.backend_forzato = app.traduttore.get(), None
        app.traduttore.set(gui.TRADUTTORI["google"])
        app._cambia_traduttore()
        limitatore.Limitatore("google", log=None).bloccato(429, "prova")
        installazioni = []
        app._installa_locale = lambda poi=None: installazioni.append(poi)
        try:
            app._traduci()
            pausa = FINESTRE[-1]
            risposte[:] = ["installa_locale"]
            app._traduci()
            dopo_pausa = app._scelto
            app._traduci()  # now the local translator is chosen, and it is not installed
            assente = FINESTRE[-1]
            # the yes to the official language (consenso) translates nothing: no questions about the translator
            finestre = len(FINESTRE)
            app._traduci(consenso=True)
            consenso = (list(avviati), [f[0] for f in FINESTRE[finestre:]])
            avviati.clear()
        finally:
            limitatore.Limitatore("google", log=None).azzera()
            del app._installa_locale
            app.traduttore.set(scelto)
            app._cambia_traduttore()
            app.backend_forzato = "finto"
        controlla("Google in pausa: 'Traduttore in pausa' con 'Installa il traduttore locale' e 'Continua comunque'; "
                  "scelto: traduttore locale e installazione, poi di nuovo Analizza e traduci",
                  avviati == [] and pausa[0] == "Traduttore in pausa" and ("Installa il traduttore locale",
                                                                          "installa_locale") in pausa[2]
                  and ("Continua comunque", "continua") in pausa[2] and dopo_pausa == "locale"
                  and len(installazioni) == 1 and callable(installazioni[0]))
        controlla("traduttore locale non installato: 'Installa ora' prima di partire (o 'Continua comunque': la lingua "
                  "ufficiale non serve tradurla), nessuna operazione",
                  avviati == [] and assente[0] == "Traduttore locale da installare"
                  and ("Installa ora", "installa_locale") in assente[2] and ("Continua comunque", "continua")
                  in assente[2])
        controlla("consenso alla lingua ufficiale con il traduttore locale da installare: parte senza domande sul "
                  "traduttore (non serve tradurre)", consenso == (["Analisi e traduzione"], []))
    finally:
        sistema.gioco_aperto = vero_aperto
        del app._avvia
        tema.scegli = vera_scegli

    # a copy of the game (same folder name, elsewhere): "Yes, use them again" -> the original's translation memory
    copia = os.path.join(os.path.dirname(os.path.abspath(cartella)), "copia-prova-gui",
                         os.path.basename(os.path.normpath(cartella)))
    shutil.rmtree(os.path.dirname(copia), ignore_errors=True)
    for p, prof in list(automatico.elenco_profili()):  # (from a previous run of the test launched on its own)
        if prof.get("cartella_gioco") and automatico._dentro(prof["cartella_gioco"], os.path.dirname(copia)):
            os.remove(p)
    gioco_web_finto.crea(copia)
    originale = json.load(open(automatico.trova_profilo(cartella), encoding="utf-8"))
    voci = json.load(open(progetto.percorso_memoria(originale, prova=True), encoding="utf-8"))
    tema.scegli = lambda _g, _c, titolo, testo, pulsanti, tipo="info": (
        _scegli(_g, _c, titolo, testo, pulsanti, tipo) or ("riusa" if titolo == "Stesso gioco già tradotto?" else None))
    try:
        app.cartella.set(copia)
        finestre = len(FINESTRE)
        app._traduci()
        _aspetta(app, radice)
    finally:
        tema.scegli = vera_scegli
        app.cartella.set(cartella)
    domanda = next((f for f in FINESTRE[finestre:] if f[0] == "Stesso gioco già tradotto?"), None)
    nuovo = automatico.trova_profilo(copia)
    profilo_copia = json.load(open(nuovo, encoding="utf-8")) if nuovo else {}
    memoria_copia = json.load(open(progetto.percorso_memoria(profilo_copia, prova=True), encoding="utf-8")) \
        if nuovo else {}
    registro = app.testo.get("1.0", "end")
    controlla("copia del gioco in un'altra cartella: 'Stesso gioco già tradotto?' con 'Sì, riusale'; riusate le sue "
              "traduzioni (profilo nuovo con la stessa memoria)", domanda and ("Sì, riusale", "riusa") in domanda[2]
              and nuovo and profilo_copia.get("nome") != originale["nome"]
              and all(memoria_copia.get(k) == v for k, v in voci.items()) and "Riuso le traduzioni" in registro
              and FINESTRE[-1][0] == "Traduzione installata")
    automatico.ripristina(copia, print)


def _collaudo_installazione(app, radice):
    """The installation of the local translator requested before starting ("Install now"): after the successful test
    sentence the operation that was waiting restarts by itself; if the test fails a dialog says so and the operation
    does not start. Then: the check at startup waits for an open dialog to close (the operation chosen there would not
    start, one job at a time)."""
    from patchsmith import backend
    veri = ollama.descrizione_download, ollama.installa, backend.prova_locale, tema.chiedi
    rifatte, prove, esiti_prova = [], [], [
        {"ok": True, "messaggio": "pronto, sulla scheda video: \"Start game\" -> \"Inizia\".", "scheda": 1.0},
        {"ok": False, "messaggio": "la prova di traduzione non e' riuscita: il traduttore locale non risponde.",
         "scheda": None}]
    ollama.descrizione_download = lambda scelti: ["finto da scaricare"]
    ollama.installa = lambda scelti, log=print: True
    backend.prova_locale = lambda modello=None, log=print, **_k: prove.append(modello) or esiti_prova.pop(0)
    tema.chiedi = lambda *_a: True
    try:
        avvisi = len(AVVISI)
        app._installa_locale(poi=lambda: rifatte.append("di nuovo"))
        _aspetta(app, radice)
        riuscita = (list(rifatte), app.stato.get(), len(AVVISI) - avvisi)
        app._installa_locale(poi=lambda: rifatte.append("di nuovo"))
        _aspetta(app, radice)
    finally:
        ollama.descrizione_download, ollama.installa, backend.prova_locale, tema.chiedi = veri
    controlla("installazione chiesta prima di partire: frase di prova riuscita -> l'operazione riparte da sola",
              riuscita[0] == ["di nuovo"] and "Traduttore locale installato: pronto" in riuscita[1] and not riuscita[2]
              and len(prove) == 2)
    controlla("frase di prova non riuscita: finestra con il motivo, l'operazione che aspettava non parte",
              rifatte == ["di nuovo"] and AVVISI[-1] == "Prova del traduttore locale non riuscita"
              and "non risponde" in app.stato.get())
    # already installed: "Install now" runs the waiting operation right away; preliminary checks that fail
    # (unexpected error): it starts anyway, and the log says so
    ollama.descrizione_download = lambda scelti: []
    subito, vero_cp = [], automatico.controlli_preliminari
    automatico.controlli_preliminari = lambda *_a, **_k: 1 / 0
    try:
        app._installa_locale(poi=lambda: subito.append("subito"))
        scelte = app._preliminari("traduci", lambda: None, "finto")
        for _ in range(10):
            radice.update()
            time.sleep(0.02)
    finally:
        ollama.descrizione_download, automatico.controlli_preliminari = veri[0], vero_cp
    controlla("traduttore locale gia' installato: l'operazione che aspettava parte subito; controlli prima di partire "
              "non riusciti: si parte lo stesso, con una riga nel registro", subito == ["subito"]
              and scelte == {"riusa": None}
              and "Controlli prima di partire non riusciti" in app.testo.get("1.0", "end"))

    # the check at startup arrives while a dialog is open: it waits for it to close
    finestra = tk.Toplevel(radice)
    finestra.grab_set()
    controlli, lavoro = [], app.lavoro
    app.lavoro = None
    app._controlla = lambda profili=None, **_k: controlli.append(profili)
    try:
        app._avvia_controllo_iniziale(["profilo-in-attesa"])
        for _ in range(25):
            radice.update()
            time.sleep(0.02)
        durante = list(controlli)
        finestra.grab_release()
        finestra.destroy()
        radice.update()
        subito = list(controlli)  # (right after closing it still waits a moment: the choice made there starts first)
        inizio = time.time()
        while not controlli and time.time() - inizio < 3:
            radice.update()
            time.sleep(0.02)
    finally:
        del app._controlla
        app.lavoro = lavoro
    controlla("controllo all'apertura con una finestra aperta: parte solo dopo che si e' chiusa",
              durante == [] and subito == [] and controlli == [["profilo-in-attesa"]])
    # dialog closed with a choice that starts an operation right after (e.g. "Try again"): the check does not start
    # right away, it waits for the operation to finish
    finestra = tk.Toplevel(radice)
    finestra.grab_set()
    controlli[:] = []
    app.lavoro = None
    app._controlla = lambda profili=None, **_k: controlli.append(profili)
    try:
        app._avvia_controllo_iniziale(["profilo-in-attesa"])
        for _ in range(20):
            radice.update()
            time.sleep(0.02)
        finestra.grab_release()
        finestra.destroy()
        radice.after(250, lambda: setattr(app, "lavoro", lavoro))  # the operation chosen in the dialog (within 300 ms)
        inizio = time.time()
        while time.time() - inizio < 1.2:
            radice.update()
            time.sleep(0.02)
    finally:
        del app._controlla
        app.lavoro = lavoro
    controlla("finestra chiusa con un'operazione che parte subito dopo: il controllo all'apertura aspetta che finisca",
              controlli == [] and "appena finisce" in app.stato_controllo.get())
    if app._attesa_controllo:  # (in the test it must not restart later)
        radice.after_cancel(app._attesa_controllo)
        app._attesa_controllo = None
    app.stato_controllo.set("")


def _collaudo_correzioni(app, radice, cartella):
    """"Fix translations…" (step 6): the window with the list, a translation changed in the line and "Save and apply to
    the game": the translation memory (the test translator's) stays valid JSON with the new value and Reapply starts on
    its own and changes the game's file. Then "Fix translations" in the end dialog with some texts left."""
    import hashlib
    app.cartella.set(cartella)
    file_testi = os.path.join(cartella, *gioco_web_finto.FILE_TESTI.split("/"))
    with open(file_testi, "rb") as f:
        prima = hashlib.sha256(f.read()).hexdigest()
    finestra = app._correggi()
    righe = len(finestra.albero.get_children()) if finestra else 0
    finestra._vai_a("Start a new game")
    finestra.testo_traduzione.delete("1.0", "end")
    finestra.testo_traduzione.insert("1.0", "Nuova partita (corretta)")
    for _ in range(5):
        radice.update()
    cella = finestra.albero.item(finestra.albero.selection()[0], "values")[1]
    finestra.salva()
    _aspetta(app, radice)
    profilo = json.load(open(automatico.trova_profilo(cartella), encoding="utf-8"))
    try:
        memoria = json.load(open(progetto.percorso_memoria(profilo, prova=True), encoding="utf-8"))
    except ValueError:
        memoria = {}
    with open(file_testi, "rb") as f:
        dopo = f.read()
    controlla("Correggi traduzioni: finestra con l'elenco, riga cambiata e salvata: memoria valida con il valore "
              "nuovo, Riapplica da sola che cambia il file del gioco", righe > 10 and cella == "Nuova partita (corretta)"
              and memoria.get("Start a new game") == "Nuova partita (corretta)"
              and hashlib.sha256(dopo).hexdigest() != prima and "Nuova partita (corretta)" in dopo.decode("utf-8")
              and FINESTRE[-1][0] == "Traduzione riapplicata" and not finestra.top.winfo_exists())
    finestre = len(FINESTRE)
    app._mostra_esito("Analisi e traduzione", esito.Esito("PARZIALE", scritto=True, tradotti=3, rimasti=1,
                                                         esempi_rimasti=["Quit"]))
    controlla("finestra di fine con testi rimasti: pulsante 'Correggi traduzioni'",
              len(FINESTRE) == finestre + 1 and ("Correggi traduzioni", "correggi") in FINESTRE[-1][2])


def _leggi_byte(percorso):
    try:
        with open(percorso, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    percorso, esito_ok = main(sys.argv[1])
    print("profilo usato:", percorso)
    sys.exit(0 if esito_ok else 1)
