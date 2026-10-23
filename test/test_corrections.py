"""Tests of "Fix translations" (item 6): the list of a game's translations with its filters, the window
(keyboard, long list in pages, checks while you type), saving (valid translation memory, machine translations that
stay the previous ones, shared translation memory, problemi.json) and writing into the game on its own ("Reapply"),
for a web game, a GameMaker game with the line space and a Unity game (XUnity cache line replaced, the others
identical; game open; line changed by hand after the fix). Then "Re-translate selected" (new requests, also for
lines fixed by hand), "Re-translate everything with Quality (12B)" (fixes made by hand stay), the Quality model
to install, "Start over" (backup copy, shared translation memory), "Open the file (advanced)" with no program
for .json files, a damaged translations file, and the "Fix translations" buttons in the end dialogs.

Usage: python -I test/test_corrections.py <empty_temp_folder>
PatchSmith's data go in <cartella>/dati; fake translators (no network, no Ollama), fake Unity components, no real
game. PatchSmith's window opens for a few seconds.
"""
import hashlib
import json
import os
import re
import struct
import sys
import time

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import tkinter as tk  # noqa: E402

import fake_web_game as gioco_web_finto  # noqa: E402
import test_gamemaker  # noqa: E402
import test_gamemaker_json  # noqa: E402
import test_unity  # noqa: E402
from patchsmith import (adapter_unity, auto as automatico, backend, corrections as correzioni, outcome as esito, gui,
                        gui_corrections as gui_correzioni)  # noqa: E402
from patchsmith import ollama, project as progetto, scan as scansione, steam, theme as tema  # noqa: E402

ESITI = []
FINESTRE = []  # (titolo, testo, pulsanti) of the choice dialogs and of the notices
RISPOSTE = []  # the answers to the next choice dialogs (None: OK / Cancel)


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def _scegli(_g, _c, titolo, testo, pulsanti, tipo="info"):
    FINESTRE.append((titolo, testo, list(pulsanti)))
    print("FINESTRA:", titolo, "|", " ".join(testo.split())[:150], "|", [p for p, _ in pulsanti])
    return RISPOSTE.pop(0) if RISPOSTE else None


def _avvisa(_g, _c, titolo, testo):
    FINESTRE.append((titolo, testo, []))
    print("AVVISO:", titolo, "|", " ".join(testo.split())[:150])


def impronta(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def leggi(percorso):
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def aspetta(app, radice, secondi=120):
    inizio = time.time()
    while (app.lavoro is None or app.lavoro.is_alive()) and time.time() - inizio < secondi:
        radice.update()
        time.sleep(0.03)
    for _ in range(15):
        radice.update()
        time.sleep(0.03)


def scrivi(f, testo):
    """Like typing in the translation box (also Tk's <<Modified>> event)."""
    f.testo_traduzione.delete("1.0", "end")
    f.testo_traduzione.insert("1.0", testo)
    for _ in range(3):
        f.top.update()


def voce(elenco, testo):
    return next(x for x in elenco.voci if x.testo == testo)


class Traduce(backend.Backend):
    """Fake but "real" translator (not the test translation memory): a small dictionary, the rest an Italian sentence
    with the same codes; the bridge is not translated (left untranslated)."""
    nome = "prova-correzioni"
    VOCI = {"Start a new game": "Inizia una nuova partita", "Lantern Keeper": "Il guardiano delle lanterne antiche",
            "Delete": "Eliminare", "Quit to the desktop": "Esci al desktop",
            "Continue your journey": "Continua il tuo viaggio", "Options and settings": "Opzioni e impostazioni",
            "Welcome back, keeper. The lanterns are fading again.":
                "Bentornato, guardiano. Le lanterne si stanno spegnendo di nuovo."}

    def __init__(self, voci=None, rotti=("bridge",)):
        self.voci = dict(self.VOCI, **(voci or {}))
        self.rotti = rotti
        self.chieste = []

    def traduci_righe(self, righe, origine, destinazione):
        self.chieste += righe
        uscita = []
        for r in righe:
            if any(x in r for x in self.rotti):
                uscita.append("")
            else:
                uscita.append(self.voci.get(r) or ("Testo tradotto in italiano " + " ".join(
                    re.findall(r"\[\[\d+\]\]", r))).strip())
        return uscita


class ConSemi(Traduce):
    """Like the local translator: receives the round for each line (new seeds from round 1)."""
    semi_variabili = True

    def __init__(self, voci=None, rotti=()):
        super().__init__(voci, rotti)
        self.giri = []

    def traduci_righe(self, righe, origine, destinazione, giri=None):
        self.giri.append(giri)
        return super().traduci_righe(righe, origine, destinazione)


# --- functions without a window -----------------------------------------------------------------------------

def prova_funzioni(base):
    a = correzioni.avvisi
    controlla("controlli mentre scrivi: segnaposto mancante o in piu', numeri, righe, vuota",
              a("Collect {0} coins", "Raccogli monete")[0].startswith("manca {0}")
              and a("Collect {0} coins", "Raccogli {0} {1} monete") == ["in più: {1}"]
              and a("Level 2", "Livello 3") == ["numeri diversi dall'originale (manca 2; in più 3)"]
              and a("Line one\nLine two", "Riga uno riga due") == ["righe: 1 invece di 2"]
              and a("Quit", "  ") == ["la traduzione è vuota"] and a("Collect {0} coins", "Raccogli {0} monete") == []
              and a("max H", "massimo H", ("H",)) == [] and a("max H", "massimo", ("H",))[0].startswith("manca H"))
    x = correzioni.Voce("iron*ore")
    x.spazio = ("iron*ore", "*", "U", 11, 2, 0)
    controlla("lunghezza come la scrive il gioco (a capo ridistribuiti nello spazio delle righe)",
              correzioni.misura(x, "minerale di ferro") == (11, 11)
              and correzioni.troppo_lunga(*correzioni.misura(x, "minerale di ferro grezzo e pesante antico"))
              and not correzioni.troppo_lunga(12, 11) and correzioni.misura(correzioni.Voce("a"), "b") is None)

    def problemi(testo, tradotto, dest="it", problema=None, automatiche=None, cache=None):
        v = correzioni.Voce(testo)
        v.memoria = tradotto
        v.cache = cache
        correzioni._problemi_di(v, problema or {}, dest, None, (), (), automatiche or {}, {}, {})
        return v.problemi

    controlla("sospetti: verbo all'infinito in un pulsante (con la forma giusta), una parola diventata frase",
              "\"Esporta\", non \"Esportare\"" in problemi("Export", "Esportare").get("sospetti", "")
              and "Chiudi" in problemi("Close", "Chiudere").get("sospetti", "")
              and "l'imperativo (\"Rifare\"" in problemi("Redo", "Rifare").get("sospetti", "")
              and "sospetti" not in problemi("None", "— Nessuno —")
              and "sospetti" not in problemi("Chest", "Forziere") and "sospetti" not in problemi("Save", "Salva")
              and "una frase" in problemi("Inventory", "Il tuo inventario personale").get("sospetti", "")
              and "sospetti" not in problemi("Export", "Esportare", dest="fr"))
    controlla("filtri dai problemi annotati: rimasto (senza traduzione), non scritto nel gioco, sospetto; corretta a "
              "mano; un problema di una traduzione di prima non conta",
              "rimasti" in problemi("The bridge is broken", None, problema={"dove": "traduzione", "motivo": "x"})
              and problemi("Sword", "Spada→", problema={"dove": "gioco", "valore": "Spada→", "motivo": "font"})["gioco"]
              == "font"
              and "gioco" not in problemi("Sword", "Spada", problema={"dove": "gioco", "valore": "Spada→"})
              and "sospetti" in problemi("Wood", "Legna", problema={"dove": "memoria", "valore": "Legna"})
              and problemi("Wood", "Legno", automatiche={"Wood": "Legna"}).get("mano") == "corretta a mano"
              and "mano" not in problemi("Wood", "Legno", automatiche={"Wood": "Legno"}))
    controlla("filtri dai controlli: uguale all'originale, un'altra lingua, numeri e segnaposto (anche solo da "
              "guardare), etichetta corta diventata molto piu' lunga",
              problemi("Open the door to the castle", "Open the door to the castle").get("rimasti")
              == "uguale all'originale"
              and "lingua" in problemi("Open the door", "Abre la puerta de la casa y el castillo")
              and problemi("Collect {0} coins", "Raccogli monete")["segnaposto"].startswith("manca {0}")
              and problemi("Collect {0} coins", "Raccogli {0} monete")["segnaposto"] == ""
              and "lunghi" in problemi("Credits", "Ringraziamenti finali")
              and "lunghi" not in problemi("Save", "Salva"))
    profilo = {"nome": "funzioni", "lingua_destinazione": "it"}
    correzioni.segna_da_scrivere(profilo, {"Start": ("Inizia", "Nuova partita")})
    correzioni.segna_da_scrivere(profilo, {"Start": ("Nuova partita", "Gioca"), "Quit": (None, "Esci")})
    pendenti = correzioni.da_scrivere(profilo)
    correzioni.scritte(profilo, ["Start"])
    controlla("righe della cache da scrivere: una seconda correzione tiene la riga di prima; tolte quelle scritte",
              pendenti["Start"]["prima"] == "Inizia" and pendenti["Start"]["valore"] == "Gioca"
              and pendenti["Quit"]["prima"] is None and list(correzioni.da_scrivere(profilo)) == ["Quit"]
              and correzioni.percorso_da_scrivere(profilo).endswith("funzioni.it.da_scrivere.json"))
    os.remove(correzioni.percorso_da_scrivere(profilo))
    # "Open the file (advanced)" on a PC with no program for .json files: Notepad, with the full path
    lanciati, vero_avvio, vero_popen = [], getattr(os, "startfile", None), gui.subprocess.Popen

    def senza_programma(_p):
        raise OSError(1155, "Nessuna applicazione associata")

    os.startfile = senza_programma
    gui.subprocess.Popen = lambda comando, *a, **k: lanciati.append(comando)
    try:
        gui._apri(os.path.join(base, "memoria.json"))
    finally:
        os.startfile, gui.subprocess.Popen = vero_avvio, vero_popen
    controlla("'Apri il file (avanzato)' senza programma per i .json: il Blocco note di Windows, mai dalla cartella "
              "corrente", len(lanciati) == 1 and os.path.isabs(lanciati[0][0])
              and lanciati[0][0].lower().endswith(os.path.join("system32", "notepad.exe")))
    f = esito.finestra(esito.Esito("PARZIALE", scritto=True, tradotti=5, rimasti=2, esempi_rimasti=["Quit"],
                                   azioni=["usa_locale"]), "traduci")
    ok = esito.finestra(esito.Esito("OK", scritto=True, tradotti=5), "traduci")
    scartate = esito.Esito("PARZIALE", scritto=True, tradotti=5, extra={"corrette_a_mano_scartate": 1})
    controlla("fine operazione: 'Correggi traduzioni' con testi rimasti o non scritti nel gioco (con l'elenco giusto), "
              "non se e' tutto tradotto ne' per un errore imprevisto",
              ("Correggi traduzioni", "correggi") in f["pulsanti"] and "Correggi traduzioni" not in str(ok["pulsanti"])
              and ("Correggi traduzioni", "correggi") in esito.finestra(scartate, "riapplica")["pulsanti"]
              and esito.filtro_correzioni(scartate) == "gioco"
              and esito.filtro_correzioni(esito.Esito("PARZIALE", rimasti=1)) == "rimasti"
              and not esito.da_correggere(esito.Esito("ERRORE", rimasti=1, extra={"imprevisto": True}))
              and "elenco \"Non scritte nel gioco\"" in esito.finestra(scartate, "riapplica")["testo"]
              and "Ho scritto nel gioco la tua correzione." in esito.finestra(esito.Esito(
                  "OK", scritto=True, tradotti=5, extra={"correzioni_scritte": 1}), "riapplica")["testo"])


# --- web game -------------------------------------------------------------------------------------------------

def prova_web(base, radice, app):
    gioco = os.path.join(base, "Lantern Keeper")
    rel = gioco_web_finto.crea(gioco)
    file_testi = os.path.join(gioco, *rel.split("/"))
    automatico.crea_backend = lambda nome, log=print: Traduce()
    percorso, _, _ = automatico.analizza_e_traduci(gioco, "auto", "it", "locale", silenzio)
    profilo = leggi(percorso)
    memoria = progetto.percorso_memoria(profilo)
    auto_file = memoria[:-5] + ".automatiche.json"
    elenco = correzioni.carica(percorso)
    conti = elenco.conteggi()
    controlla("elenco: tutti i testi del gioco (anche quello rimasto in originale), con i filtri",
              conti["tutte"] == 19 and conti["rimasti"] == 1 and conti["mano"] == 0
              and voce(elenco, "The bridge is broken: you need a rope and two planks.").problemi.get("rimasti", "")
              .startswith("senza traduzione")
              and "lunghi" in voce(elenco, "Lantern Keeper").problemi
              and "\"Elimina\"" in voce(elenco, "Delete").problemi.get("sospetti", ""))
    trovati = [x.testo for x in elenco.filtra("tutte", "LANTERN")]
    controlla("ricerca senza maiuscole nell'originale e nella traduzione",
              "Lantern Keeper" in trovati and "Press any key to light the lantern near the gate." in trovati
              and [x.testo for x in elenco.filtra("tutte", "guardiano")] == ["Lantern Keeper",
                                                                            "Welcome back, keeper. The lanterns are "
                                                                            "fading again."])

    # the window: "Left untranslated" list, translation written by hand, checks while you type
    app.cartella.set(gioco)
    f = app._correggi()
    controlla("finestra: titolo con il gioco, menu 'Mostra' con i conteggi, colonne Originale/Traduzione/Problema "
              "(Limite solo se il gioco fa conoscere lo spazio dei testi)", f is not None
              and f.top.title() == "Correggi traduzioni - lantern-keeper"
              and "Rimasti in originale (1)" in f.scelta_filtro.cget("values")
              and len(f.albero.get_children()) == 19
              and tuple(f.albero.cget("displaycolumns")) == ("originale", "traduzione", "problema"))
    f.scegli_filtro("rimasti")
    righe = f.albero.get_children()
    f._scegli_riga(0)
    scrivi(f, "Il ponte è rotto: ti servono una corda e due assi.")
    controlla("riga rimasta in originale: si scrive la traduzione, la riga diventa 'modificata, da salvare'",
              len(righe) == 1 and f.modifiche == {"The bridge is broken: you need a rope and two planks.":
                                                  "Il ponte è rotto: ti servono una corda e due assi."}
              and "modificata" in f.albero.item(righe[0], "tags")
              and f.albero.item(righe[0], "values")[3] == "modificata, da salvare" and f.top.title().endswith("*"))
    f.scegli_filtro("tutte")
    f._vai_a("Quit to the desktop")
    scrivi(f, "Esci {0} al desktop")
    avviso = f.avviso.get()
    f._annulla_riga()
    controlla("controlli mentre scrivi (segnaposto in più) ed Esc che rimette la traduzione di prima",
              "in più: {0}" in avviso and "Quit to the desktop" not in f.modifiche
              and f.testo_traduzione.get("1.0", "end-1c") == "Esci al desktop")
    f._vai_a("Start a new game")
    scrivi(f, "Nuova partita")
    lunghezza = f.lunghezza.get()
    riga = f.albero.selection()
    f.cerca.set("new game")
    for _ in range(12):  # the search starts on its own after a moment
        radice.update()
        time.sleep(0.03)
    cercata = (f.albero.selection(), f.testo_traduzione.get("1.0", "end-1c"), len(f.albero.get_children()))
    f.cerca.set("")
    f.aggiorna_elenco()
    controlla("ricerca mentre correggi una riga: la riga resta scelta, con quello che hai scritto",
              cercata == (riga, "Nuova partita", 1) and f.albero.selection() == riga)
    prima = f.albero.selection()
    f._conferma_e_avanti()
    dopo = f.albero.selection()
    tasti = all(f.albero.bind(t) for t in ("<Return>", "<F2>", "<Escape>")) and \
        f.testo_traduzione.bind("<Control-Return>") and f.top.bind("<Control-f>") and f.top.bind("<Control-s>") \
        and f.top.bind("<Alt-Right>")
    f._vai_alla_ricerca()
    radice.update()
    controlla("tastiera: Invio/F2, Ctrl+Invio passa alla riga dopo, Ctrl+F cerca, Ctrl+S salva, Alt+frecce pagine; "
              "lunghezza mentre scrivi", tasti and prima != dopo and len(dopo) == 1
              and "Lunghezza: 13 caratteri (originale: 16)" == lunghezza)
    f._vai_a("Delete")
    controlla("il problema della riga scelta per intero sotto la traduzione (es. 'Elimina', non 'Eliminare')",
              f.problema.get().startswith("Problema: verbo all'infinito") and "\"Elimina\"" in f.problema.get())
    # empty text or text with a wrong placeholder: it is not saved without saying so
    f._vai_a("Continue your journey")
    scrivi(f, "")
    f.salva()
    vuota = FINESTRE[-1][0]
    scrivi(f, "Continua {0} il tuo viaggio")
    RISPOSTE[:] = [None]  # "Go back to editing"
    f.salva()
    controlla("salvataggio con una traduzione vuota o un segnaposto in piu': prima lo dice, la finestra resta aperta",
              vuota == "Traduzione vuota" and FINESTRE[-1][0] == "Controlla le correzioni"
              and ("Salva lo stesso", "salva") in FINESTRE[-1][2] and f.top.winfo_exists()
              and leggi(memoria)["Start a new game"] == "Inizia una nuova partita")
    f._annulla_riga()
    # save: valid translation memory with the new value, machine translations as they were, shared translation
    # memory, then Reapply on its own
    prima_gioco = impronta(file_testi)
    automatiche_prima = leggi(auto_file)
    f.salva()
    aspetta(app, radice)
    mem = leggi(memoria)
    auto = leggi(auto_file)
    comune = leggi(progetto.percorso_comune("en", "it"))
    origine = leggi(progetto.percorso_origine_comune("en", "it"))
    problemi = leggi(progetto.percorso_problemi(profilo))
    testi = leggi(file_testi)
    controlla("salvato: memoria valida con le correzioni, nelle traduzioni automatiche resta quella di prima (cosi' "
              "e' una correzione a mano; per il testo rimasto in originale una voce vuota)",
              mem["Start a new game"] == "Nuova partita"
              and mem["The bridge is broken: you need a rope and two planks."].startswith("Il ponte")
              and auto["Start a new game"] == automatiche_prima["Start a new game"] == "Inizia una nuova partita"
              and "The bridge is broken: you need a rope and two planks." not in automatiche_prima
              and auto.get("The bridge is broken: you need a rope and two planks.") == "")
    controlla("memoria comune: la correzione di un testo breve vale per tutti i giochi (corretta a mano)",
              comune.get("Start a new game") == "Nuova partita" and origine["Start a new game"].get("mano") is True
              and origine["Start a new game"].get("gioco") == "lantern-keeper")
    controlla("il testo tradotto a mano esce dai rimasti (problemi.json)",
              "The bridge is broken: you need a rope and two planks." not in problemi)
    controlla("Riapplica da sola dopo il salvataggio: il file del gioco e' cambiato, con le correzioni",
              impronta(file_testi) != prima_gioco and testi["menu"]["start"] == "Nuova partita"
              and testi["dialoghi"][2]["testo"].startswith("Il ponte") and FINESTRE[-1][0] == "Traduzione riapplicata"
              and not f.top.winfo_exists())
    elenco = correzioni.carica(percorso)
    controlla("riaperta: 'Corrette a mano' (2), niente piu' rimasti", elenco.conteggi()["mano"] == 2
              and elenco.conteggi()["rimasti"] == 0)

    # closing with unsaved fixes: "Don't save" exits without saving, "Cancel" stays
    f = app._correggi()
    f._vai_a("Quit to the desktop")
    scrivi(f, "Torna al desktop")
    RISPOSTE[:] = [None]
    f.chiudi()
    aperta = f.top.winfo_exists()
    RISPOSTE[:] = ["esci"]
    f.chiudi()
    controlla("chiudere con correzioni non salvate: lo chiede ('Annulla' resta aperta, 'Non salvare' esce senza "
              "salvare)", aperta and not f.top.winfo_exists() and FINESTRE[-1][0] == "Salvare le correzioni?"
              and leggi(memoria)["Quit to the desktop"] == "Esci al desktop")

    # Re-translate selected: new requests (round 1) also for the line fixed by hand, then into the game
    nuovo = ConSemi({"Start a new game": "Comincia una partita nuova", "Continue your journey": "Prosegui il viaggio"})
    vero_crea = correzioni.crea_traduttore
    chiesti = []
    correzioni.crea_traduttore = lambda nome, modello=None, log=print: chiesti.append((nome, modello)) or nuovo
    vero_stato, vero_presente = ollama.stato, ollama.modello_presente
    try:
        f = app._correggi()
        f._vai_a("Start a new game")
        f.albero.selection_add(str(f.indice["Continue your journey"]))
        f.ritraduci_selezionati()  # local translator chosen but not installed: it says so, the window stays
        senza_locale = (FINESTRE[-1][0], f.top.winfo_exists())
        ollama.stato, ollama.modello_presente = (lambda: "pronto"), (lambda chiave: chiave == "4b")
        RISPOSTE[:] = ["4b"]
        f.ritraduci_selezionati()
        modelli = FINESTRE[-1][2]
        aspetta(app, radice)
    finally:
        correzioni.crea_traduttore = vero_crea
        ollama.stato, ollama.modello_presente = vero_stato, vero_presente
    controlla("Ritraduci selezionati: senza traduttore locale lo dice; con il traduttore locale si sceglie il modello "
              "(solo quelli scaricati)", senza_locale == ("Traduttore locale da installare", True)
              and modelli == [("Traduttore locale: Veloce (4B)", "4b"), ("Annulla", None)])
    mem, auto, testi = leggi(memoria), leggi(auto_file), leggi(file_testi)
    controlla("Ritraduci selezionati: anche la riga corretta a mano, con domande nuove (giro 1), poi scritte nel gioco",
              mem["Start a new game"] == "Comincia una partita nuova" and mem["Continue your journey"] ==
              "Prosegui il viaggio" and auto["Start a new game"] == "Comincia una partita nuova"
              and nuovo.giri and all(g and min(g) >= 1 for g in nuovo.giri)
              and testi["menu"]["start"] == "Comincia una partita nuova" and chiesti == [("locale", "4b")])
    controlla("Ritraduci: la finestra di fine 'Traduzioni rifatte' con quante", FINESTRE[-1][0] == "Traduzioni rifatte"
              and "Ho ritradotto 2 testi su 2." in FINESTRE[-1][1])
    # pressing "Re-translate selected" again on the same line the requests are new again (random round, never 0):
    # with a fixed round the local translator would give the previous answer
    sequenza, vero_giro = iter([5, 9]), correzioni._giro_nuovo
    correzioni._giro_nuovo = lambda: next(sequenza)
    due = []
    try:
        for i in range(2):
            t = ConSemi({"Start a new game": "Avvia la partita %d" % i})
            correzioni.ritraduci(percorso, ["Start a new game"], t, silenzio)
            due.append(t.giri)
    finally:
        correzioni._giro_nuovo = vero_giro
    giri_caso = {correzioni._giro_nuovo() for _ in range(20)}
    controlla("Ritraduci selezionati di nuovo sulla stessa riga: altre domande (giro a caso, mai 0)",
              due == [[[5]], [[9]]] and min(giri_caso) >= 1 and len(giri_caso) > 1
              and leggi(memoria)["Start a new game"] == "Avvia la partita 1")

    # Re-translate everything with Quality (12B): fixes made by hand stay
    correzioni.salva(percorso, {"Options and settings": "Opzioni varie"})
    vero_stato, vero_presente = ollama.stato, ollama.modello_presente
    ollama.stato, ollama.modello_presente = (lambda: "pronto"), (lambda chiave: True)
    qualita = ConSemi({"Options and settings": "Opzioni (rifatte)", "Quit to the desktop": "Torna al desktop",
                       "Lantern Keeper": "Guardiano"})
    correzioni.crea_traduttore = lambda nome, modello=None, log=print: chiesti.append((nome, modello)) or qualita
    try:
        f = app._correggi()
        RISPOSTE[:] = ["si"]
        f.ritraduci_tutto()
        domanda = FINESTRE[-1]
        aspetta(app, radice)
    finally:
        correzioni.crea_traduttore = vero_crea
        ollama.stato, ollama.modello_presente = vero_stato, vero_presente
    mem = leggi(memoria)
    controlla("Ritraduci tutto con Qualita' (12B): il modello Qualita', le correzioni a mano restano (anche nella "
              "domanda), le traduzioni automatiche rifatte con le domande normali",
              chiesti[-1] == ("locale", "12b") and "Le tue correzioni a mano (2) restano" in domanda[1]
              and mem["Options and settings"] == "Opzioni varie" and mem["Quit to the desktop"] == "Torna al desktop"
              and mem["Lantern Keeper"] == "Guardiano" and "Options and settings" not in qualita.chieste
              and all(not g or not any(g) for g in qualita.giri))
    # ... without the Quality model: offer to install it, then it re-translates on its own
    installa = []
    vero_installa = app._installa_12b_e_ritraduci
    app._installa_12b_e_ritraduci = lambda: installa.append(True)
    try:
        f = app._correggi()
        RISPOSTE[:] = ["installa"]
        f.ritraduci_tutto()
    finally:
        app._installa_12b_e_ritraduci = vero_installa
    controlla("Ritraduci tutto senza il modello Qualita': 'Modello Qualità da installare', poi installazione e "
              "ritraduzione", installa == [True] and FINESTRE[-1][0] == "Modello Qualità da installare"
              and not f.top.winfo_exists())
    # and the model choice in the interface: Quality chosen, then the installation with the re-translation after
    poi, vero_installa_locale = [], app._installa_locale
    app._installa_locale = lambda poi=None: installa.append(poi)
    try:
        app._installa_12b_e_ritraduci()
    finally:
        app._installa_locale = vero_installa_locale
    controlla("... il traduttore locale con il modello Qualita' scelto, e la ritraduzione dopo l'installazione",
              app._scelto == "locale" and app._chiave_modello() == "12b" and callable(installa[-1]))

    # from the end dialogs: "Fix translations" opens the right list; "Show remaining" offers it
    app._azione("correggi", esito.Esito("PARZIALE", scritto=True, extra={"corrette_a_mano_scartate": 1}))
    aperte = [w for w in radice.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    f_gioco = getattr(app, "_ultima_correzioni", None)
    RISPOSTE[:] = [None]
    app._azione("mostra_rimasti", esito.Esito("PARZIALE", rimasti=1, esempi_rimasti=["Quit"]))
    controlla("'Correggi traduzioni' da una finestra di fine: la finestra con l'elenco 'Non scritte nel gioco'; "
              "'Mostra i rimasti' propone 'Correggi traduzioni'",
              len(aperte) == 1 and f_gioco is not None and f_gioco.filtro == "gioco"
              and ("Correggi traduzioni", "correggi") in FINESTRE[-1][2])
    f_gioco.chiudi()

    # damaged translations file (edited by hand with an error): it says so and offers to open it
    with open(memoria, "rb") as fm:
        buono = fm.read()
    with open(memoria, "w", encoding="utf-8") as fm:
        fm.write('{"Start a new game": "Nuova partita",}')
    try:
        aperta = app._correggi()
    finally:
        with open(memoria, "wb") as fm:
            fm.write(buono)
    controlla("file delle traduzioni rovinato: 'Traduzioni non leggibili' con 'Apri il file (avanzato)', in parole "
              "semplici", aperta is None and FINESTRE[-1][0] == "Traduzioni non leggibili"
              and ("Apri il file (avanzato)", "apri") in FINESTRE[-1][2] and "JSON" not in FINESTRE[-1][1])

    # Start over: original game, backup copy, shared translation memory without the game's machine translations
    comune_prima = leggi(progetto.percorso_comune("en", "it"))
    origine = leggi(progetto.percorso_origine_comune("en", "it"))
    automatiche_gioco = {t for t, o in origine.items() if o.get("gioco") == "lantern-keeper" and not o.get("mano")}
    a_mano = {t for t, o in origine.items() if o.get("mano")}
    originale = json.dumps(gioco_web_finto.TESTI, ensure_ascii=False, indent=2).encode("utf-8")
    # without a ready translator (local not installed, or paused) nothing is touched: the game would be left without
    tradotto = impronta(file_testi)
    fermati = []

    class InPausa(Traduce):
        def pausa(self):
            return "Google e' in pausa fino a domani."

    for crea in (backend.crea_backend, lambda nome, log=print: InPausa()):
        automatico.crea_backend = crea
        try:
            automatico.ricomincia_da_zero(gioco, silenzio, backend="locale")
        except backend.ErroreBackend as errore:
            fermati.append(esito.da_eccezione(errore).codice)
    controlla("Ricomincia da zero senza un traduttore pronto: si ferma prima di toccare qualcosa",
              fermati == ["TRADUTTORE_ASSENTE", "TRADUTTORE_BLOCCATO"] and impronta(file_testi) == tradotto
              and os.path.exists(memoria))
    automatico.crea_backend = lambda nome, log=print: Traduce()
    copia = automatico.ricomincia_da_zero(gioco, silenzio, backend="locale")
    comune = leggi(progetto.percorso_comune("en", "it"))
    tolte = leggi(os.path.join(copia, "comune.en-it.tolte.json"))
    with open(file_testi, "rb") as ft:
        rimesso = ft.read()
    controlla("Ricomincia da zero: il gioco torna originale; memoria, traduzioni automatiche e problemi messi da parte "
              "in memory/copies", rimesso == originale and copia
              and os.path.dirname(copia) == progetto.cartella_dati("memory", "copies")
              and not os.path.exists(memoria) and not os.path.exists(auto_file)
              and leggi(os.path.join(copia, os.path.basename(memoria)))["Options and settings"] == "Opzioni varie")
    controlla("Ricomincia da zero: dalla memoria comune via le traduzioni automatiche del gioco (conservate nella "
              "copia), le correzioni a mano restano", automatiche_gioco and set(tolte) == automatiche_gioco
              and not automatiche_gioco & set(comune) and a_mano == {"Options and settings"}
              and all(comune[t] == comune_prima[t] for t in a_mano))
    automatico.crea_backend = lambda nome, log=print: Traduce()
    automatico.analizza_e_traduci(gioco, "auto", "it", "locale", silenzio, rigenera=True)
    mem = leggi(memoria)
    controlla("... e la traduzione riparte da capo, con tutti i file del gioco (le correzioni a mano dalla memoria "
              "comune)", mem.get("Continue your journey") == "Continua il tuo viaggio"
              and mem.get("Options and settings") == "Opzioni varie" and len(mem) >= 18)
    # from the window, with the test translator (its own separate translation memory): "Start over" and Analyze and
    # translate
    app.backend_forzato = "finto"
    chiamate, vero_ricomincia = [], automatico.ricomincia_da_zero

    def in_pausa(*_a, **_k):
        raise backend.ErroreBackend("Google e' in pausa fino a domani.", "TRADUTTORE_BLOCCATO")

    try:
        app._traduci()
        aspetta(app, radice)
        f = app._correggi()
        RISPOSTE[:] = ["si"]
        f.ricomincia()
        aspetta(app, radice)
        titoli = [t for t, _, _ in FINESTRE]
        # "Try again" afterwards (e.g. game opened halfway through): continues the new translation, does not set it
        # aside again
        automatico.ricomincia_da_zero = lambda *a, **k: chiamate.append(1) or vero_ricomincia(*a, **k)
        app._ripeti()
        aspetta(app, radice)
        riprova = (list(chiamate), FINESTRE[-1][0])
        # a "Start over" that did not start (translator paused): "Use the local translator" starts over
        automatico.ricomincia_da_zero = in_pausa
        app._traduci(da_zero=True)
        aspetta(app, radice)
        non_partito = (app.ultimo_esito.codice, app._scelte(app.ultimo_esito).get("da_zero"))
    finally:
        automatico.ricomincia_da_zero = vero_ricomincia
        app.backend_forzato = None
    copie = sorted(os.listdir(progetto.cartella_dati("memory", "copies")), key=lambda n: os.path.getmtime(
        progetto.cartella_dati("memory", "copies", n)))
    controlla("dopo 'Ricomincia da zero', 'Riprova' continua la traduzione nuova senza rimetterla da parte; se non era "
              "partito (traduttore in pausa), 'Usa il traduttore locale' ricomincia da zero",
              riprova[0] == [] and riprova[1].startswith("Traduzione installata") and len(copie) == 2
              and non_partito == ("TRADUTTORE_BLOCCATO", True))
    controlla("'Ricomincia da zero…' dalla finestra: chiede conferma, mette da parte la memoria e traduce di nuovo",
              "Ricominciare da zero?" in titoli and titoli[-1].startswith("Traduzione installata") and len(copie) == 2
              and any(n.endswith(".prova.json") for n in os.listdir(progetto.cartella_dati("memory", "copies",
                                                                                          copie[-1])))
              and os.path.exists(progetto.percorso_memoria(profilo, prova=True)))
    automatico.ripristina(gioco, silenzio)


# --- long list: in pages --------------------------------------------------------------------------------------

def prova_pagine(app, radice):
    voci = []
    for i in range(750):
        x = correzioni.Voce("Text number %d of the game" % i)
        x.memoria = "Testo numero %d del gioco" % i
        if i % 100 == 0:
            x.problemi["sospetti"] = "da guardare"
        voci.append(x)
    elenco = correzioni.Elenco("profilo-finto.json", {"nome": "lungo", "lingua_destinazione": "it"}, voci, False, "en",
                               (), True)
    inizio = time.perf_counter()
    f = gui_correzioni.Finestra(app, elenco)
    secondi = time.perf_counter() - inizio
    pagina1 = (len(f.albero.get_children()), f.posizione.get())
    f.cambia_pagina(1)
    f.cambia_pagina(1)
    pagina3 = (len(f.albero.get_children()), f.posizione.get(), f.btn_dopo.instate(["disabled"]))
    f.cerca.set("number 7")
    f._vai_all_elenco()
    cercati = len(f.albero.get_children())  # 7, 70-79, 700-749
    f.cerca.set("")
    f.scegli_filtro("sospetti")
    sospetti = len(f.albero.get_children())
    pagine_con_pochi = f.riga_pagine.winfo_manager()  # (8 lines: a single page, no page buttons)
    f.chiudi()
    controlla("elenco lungo a pagine (%.2f s): %d righe alla volta, Pagina dopo fino all'ultima, ricerca e filtro; con "
              "una pagina sola la riga delle pagine non c'e'" % (secondi, gui_correzioni.PAGINA),
              pagina1 == (300, "Pagina 1 di 3 (righe 1–300 di 750)")
              and pagina3 == (150, "Pagina 3 di 3 (righe 601–750 di 750)", True)
              and cercati == 61 and sospetti == 8 and pagine_con_pochi == "" and secondi < 5)


# --- GameMaker: the line space and the translations the game cannot display ---------------------------------

def prova_spazio_gamemaker(base, radice, app):
    gioco = os.path.join(base, "Forager finto")
    local = os.path.join(gioco, "local")
    os.makedirs(local)
    for nome, dati in (("english", test_gamemaker_json.EN), ("french", test_gamemaker_json.FR),
                       ("spanish", test_gamemaker_json.ES), ("german", test_gamemaker_json.DE)):
        with open(os.path.join(local, nome + ".json"), "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)
    latino = set(chr(c) for c in range(32, 127)) | set("àèéìíòóùÀÈÉÌÒÙçâêôûœëïüö")
    with open(os.path.join(gioco, "data.win"), "wb") as f:
        f.write(test_gamemaker.data_win([("fntText", latino), ("fntChat", set(chr(c) for c in range(32, 127)))]))
    open(os.path.join(gioco, "Forager.exe"), "wb").write(b"MZ")
    voci = dict(test_gamemaker_json.ITALIANO, **{"royal steel": "acciaio della corona reale antica"})
    automatico.crea_backend = lambda nome, log=print: Traduce(voci, rotti=())
    percorso, _, _ = automatico.analizza_e_traduci(gioco, "auto", "it", "locale", silenzio)
    elenco = correzioni.carica(percorso)
    acciaio = voce(elenco, "royal steel")
    controlla("GameMaker: 'Troppo lunghi' con lo spazio che il testo ha nel gioco (riga piu' lunga delle sue "
              "versioni), colonna Limite", acciaio.spazio and acciaio.spazio[3] == 11 and "lunghi" in acciaio.problemi
              and acciaio.problemi["lunghi"].startswith("riga più lunga:")
              and "ce ne stanno circa 11" in acciaio.problemi["lunghi"]
              and "lunghi" not in voce(elenco, "wood").problemi)
    app.cartella.set(gioco)
    f = app._correggi("lunghi")
    filtro = f.filtro
    con_limite = "limite" in f.albero.cget("displaycolumns") or f.albero.cget("displaycolumns") == ("#all",)
    f._vai_a("royal steel")
    valori = f.albero.item(f.albero.selection()[0], "values")
    scrivi(f, "acciaio reale")
    corta = f.lunghezza.get()
    scrivi(f, "acciaio della corona reale antica e lucente")
    lunga = f.lunghezza.get()
    f._annulla_riga()
    f.chiudi()
    controlla("GameMaker: la finestra aperta su 'Troppo lunghi', il limite in colonna e mentre scrivi",
              filtro == "lunghi" and valori[0] == "royal steel" and re.fullmatch(r"\d+/11", valori[2]) and con_limite
              and corta.startswith("Riga più lunga: 7 caratteri (nel gioco ce ne stanno circa 11)")
              and lunga.endswith("troppo lunga"))
    # a fix with a character the game's fonts lack: it is not written, it goes in "Not written to the game"; a
    # suspicious translation fixed by hand leaves the suspicious ones at once (even without Reapply, as for Unity)
    file_problemi = progetto.percorso_problemi(leggi(percorso))
    problemi = leggi(file_problemi) if os.path.exists(file_problemi) else {}
    problemi["wood"] = {"motivo": "prova", "dove": "memoria", "valore": "legno", "tentativi": 6}
    with open(file_problemi, "w", encoding="utf-8") as f:
        json.dump(problemi, f)
    correzioni.salva(percorso, {"coal": "carbone → nero", "wood": "legname"})
    controlla("una traduzione sospetta corretta a mano esce subito da problemi.json",
              "wood" not in leggi(file_problemi) and leggi(progetto.percorso_memoria(leggi(percorso)))["wood"]
              == "legname")
    e = {}
    automatico.riapplica(gioco, silenzio, esito=e)
    elenco = correzioni.carica(percorso)
    carbone = voce(elenco, "coal")
    controlla("GameMaker: correzione con un carattere che il gioco non sa disegnare -> 'Non scritte nel gioco' con il "
              "motivo, e la finestra di fine propone 'Correggi traduzioni' su quell'elenco",
              "→" in carbone.problemi.get("gioco", "") and e["esito"].extra.get("corrette_a_mano_scartate") == 1
              and esito.filtro_correzioni(e["esito"]) == "gioco"
              and ("Correggi traduzioni", "correggi") in esito.finestra(e["esito"], "riapplica")["pulsanti"])
    automatico.ripristina(gioco, silenzio)


# --- Unity: the XUnity cache ---------------------------------------------------------------------------------

def prova_cache_unity(base, radice, app):
    from patchsmith import xunity_service as servizio_xunity
    test_unity.prepara_componenti(os.path.join(base, "componenti"))
    adapter_unity._compila_plugin = test_unity._plugin_finto
    vero_stato, vero_esecuzione = ollama.stato, adapter_unity.in_esecuzione
    ollama.stato = lambda: "pronto"
    adapter_unity.in_esecuzione = lambda *_a: False
    nome = "unity-correzioni"
    gioco = os.path.join(base, "Unity", nome)
    test_unity.crea_gioco(gioco)
    with open(os.path.join(gioco, "Gioco_Data", "resources.assets"), "wb") as f:  # asset texts (pre-translation)
        f.write(b"\x07\x00" + b"".join(struct.pack("<i", len(t.encode("utf-8"))) + t.encode("utf-8") + b"\x00" * (
            (4 - len(t.encode("utf-8")) % 4) % 4) for t in ("Start a new game", "Settings and options",
                                                           "Back to the main menu")))
    percorso = os.path.join(automatico.cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, {"nome": nome, "cartella_gioco": gioco.replace(os.sep, "/"), "motore": "unity",
                                    "lingua_origine": "en", "lingua_destinazione": "it", "backend": "google",
                                    "file": []})
    automatico.crea_backend = lambda nome_b, log=print: Traduce({"Settings and options": "Impostazioni e opzioni"})
    try:
        automatico.unity_completo(percorso, silenzio, backend="google")
        cache = adapter_unity._percorso_cache(gioco, "it")
        with open(cache, "a", encoding="utf-8") as f:
            f.write("Open the door to the castle=Open the door to the castle\nTalk to the guard=Parla con la guardia\n"
                    "Hold {{A}} to leap=Tieni {{A}} per saltare\n")
        with open(servizio_xunity.percorso_diario(nome), "w", encoding="utf-8") as f:
            f.write(json.dumps({"testo": "Hold {{A}} to leap", "esito": "rilassata",
                                "risposta": "Tieni {{A}} per saltare"}) + "\n")
        elenco = correzioni.carica(percorso)
        porta = voce(elenco, "Open the door to the castle")
        controlla("Unity: nell'elenco anche le righe della cache di XUnity (quello che il gioco mostra): una rimasta "
                  "uguale all'originale, una tradotta mentre giocavi senza tutti i controlli",
                  porta.cache == porta.testo and porta.memoria is None and "rimasti" in porta.problemi
                  and "mentre giocavi" in voce(elenco, "Hold {{A}} to leap").problemi.get("sospetti", "")
                  and voce(elenco, "Start a new game").cache == "Inizia una nuova partita"
                  and "English" not in [x.testo for x in elenco.voci])
        with open(cache, encoding="utf-8-sig") as f:
            righe_prima = f.read().splitlines()
        memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
        auto_prima = leggi(memoria[:-5] + ".automatiche.json")
        app.cartella.set(gioco)
        f = app._correggi()
        f._vai_a("Open the door to the castle")
        scrivi(f, "Apri la porta del castello")
        f._vai_a("Start a new game")
        scrivi(f, "Nuova partita")
        f.salva()
        aspetta(app, radice)
        with open(cache, encoding="utf-8-sig") as fc:
            righe_dopo = fc.read().splitlines()
        cambiate = [(a, b) for a, b in zip(righe_prima, righe_dopo) if a != b]
        mem = leggi(memoria)
        controlla("Unity: le righe corrette sostituite nella cache di XUnity, tutte le altre identiche",
                  len(righe_prima) == len(righe_dopo) and sorted(b for _, b in cambiate) ==
                  ["Open the door to the castle=Apri la porta del castello", "Start a new game=Nuova partita"])
        controlla("Unity: memoria con le correzioni (anche la riga che era solo nella cache), automatiche com'erano "
                  "(la riga solo nella cache annotata vuota: corretta a mano), niente piu' da scrivere",
                  mem["Start a new game"] == "Nuova partita"
                  and mem["Open the door to the castle"] == "Apri la porta del castello"
                  and leggi(memoria[:-5] + ".automatiche.json") == dict(auto_prima, **{"Open the door to the castle":
                                                                                       ""})
                  and voce(correzioni.carica(percorso), "Open the door to the castle").a_mano
                  and not correzioni.da_scrivere({"nome": nome, "lingua_destinazione": "it"}))
        controlla("Unity: la finestra di fine dice quante correzioni ha scritto nel gioco",
                  "Ho scritto nel gioco 2 tue correzioni." in FINESTRE[-1][1])
        # game open: the fix waits (saved), and is written at the next Reapply
        profilo = leggi(percorso)
        correzioni.salva(percorso, {"Talk to the guard": "Parla con la sentinella"}, {"Talk to the guard":
                                                                                    "Parla con la guardia"})
        adapter_unity.in_esecuzione = lambda *_a: True
        try:
            automatico.riapplica(gioco, silenzio)
            codice = None
        except adapter_unity.ErroreUnity as errore:  # (the interface shows it as "The game is open")
            codice = esito.da_eccezione(errore).codice
        aspettava = (adapter_unity.voci_cache_xunity(gioco, "it")["Talk to the guard"],
                     list(correzioni.da_scrivere(profilo)), codice)
        adapter_unity.in_esecuzione = lambda *_a: False
        automatico.riapplica(gioco, silenzio)
        controlla("Unity con il gioco aperto: la correzione resta da scrivere; a gioco chiuso Riapplica la scrive",
                  aspettava == ("Parla con la guardia", ["Talk to the guard"], "GIOCO_APERTO")
                  and adapter_unity.voci_cache_xunity(gioco, "it")["Talk to the guard"] == "Parla con la sentinella")
        # a line changed by hand in the file after the fix stays as it is
        correzioni.salva(percorso, {"Talk to the guard": "Parla col guardiano"}, {"Talk to the guard":
                                                                                "Parla con la sentinella"})
        with open(cache, encoding="utf-8-sig") as fc:
            testo = fc.read()
        with open(cache, "w", encoding="utf-8") as fc:
            fc.write(testo.replace("Talk to the guard=Parla con la sentinella", "Talk to the guard=Parla (a mano)"))
        automatico.riapplica(gioco, silenzio)
        controlla("Unity: una riga cambiata a mano nel file dopo la correzione non si sovrascrive (e non resta in "
                  "attesa)", adapter_unity.voci_cache_xunity(gioco, "it")["Talk to the guard"] == "Parla (a mano)"
                  and not correzioni.da_scrivere(profilo))
        # Re-translate a cache line: into the translation memory and into the cache
        vero_crea = correzioni.crea_traduttore
        correzioni.crea_traduttore = lambda nome_b, modello=None, log=print: ConSemi(
            {"Hold [[0]] to leap": "Tieni premuto [[0]] per saltare"})
        try:
            automatico.ritraduci(gioco, ["Hold {{A}} to leap"], "google", silenzio)
        finally:
            correzioni.crea_traduttore = vero_crea
        controlla("Unity: Ritraduci una riga provvisoria della cache -> memoria e cache di XUnity",
                  adapter_unity.voci_cache_xunity(gioco, "it")["Hold {{A}} to leap"] ==
                  "Tieni premuto {{A}} per saltare" and leggi(memoria)["Hold {{A}} to leap"] ==
                  "Tieni premuto {{A}} per saltare")
        # Start over: XUnity removed from the game and its cache set aside too (with the game open: nothing)
        adapter_unity.in_esecuzione = lambda *_a: True
        vero_aperto = automatico.sistema.gioco_aperto
        automatico.sistema.gioco_aperto = lambda _c: True
        try:
            automatico.ricomincia_da_zero(gioco, silenzio)
            aperto_ok = False
        except automatico.ErroreRicomincia as errore:
            aperto_ok = errore.codice == "GIOCO_APERTO" and os.path.exists(cache) and os.path.exists(memoria) \
                and esito.da_eccezione(errore).codice == "GIOCO_APERTO"
        finally:
            automatico.sistema.gioco_aperto = vero_aperto
            adapter_unity.in_esecuzione = lambda *_a: False
        copia = automatico.ricomincia_da_zero(gioco, silenzio)
        controlla("Unity: Ricomincia da zero toglie XUnity dal gioco e mette da parte anche la cache salvata; con il "
                  "gioco aperto non tocca nulla ('Il gioco è aperto')", aperto_ok and not os.path.exists(cache)
                  and not adapter_unity.manifesto_presente(gioco) and not os.path.exists(memoria)
                  and os.path.exists(os.path.join(copia, nome + ".unity", "Translation", "it", "Text",
                                                  "_AutoGeneratedTranslations.txt"))
                  and not os.path.exists(adapter_unity._cartella_memoria(nome)))
    finally:
        ollama.stato, adapter_unity.in_esecuzione = vero_stato, vero_esecuzione


# --- translation memory of a version that did not record machine translations -------------------------------

def prova_memoria_vecchia(base):
    """A game translated by a version without .automatiche.json, to which a few recorded translations are then added
    (e.g. the live translator, or a game update): entries without a record are machine translations (as for the
    shared translation memory and the translations to redo), not fixes made by hand. Separate data: new shared
    translation memory."""
    vera_radice = progetto.RADICE_DATI
    progetto.RADICE_DATI = os.path.join(base, "dati-memoria-vecchia")
    try:
        gioco = os.path.join(base, "Gioco vecchio")
        gioco_web_finto.crea(gioco)
        automatico.crea_backend = lambda nome, log=print: Traduce()
        percorso, _, _ = automatico.analizza_e_traduci(gioco, "auto", "it", "locale", silenzio)
        memoria = progetto.percorso_memoria(leggi(percorso))
        auto_file = memoria[:-5] + ".automatiche.json"
        mem = leggi(memoria)
        with open(auto_file, "w", encoding="utf-8") as f:  # only two recorded entries, one fixed by hand
            json.dump({"Start a new game": mem["Start a new game"], "Delete": "Elimina"}, f)
        elenco = correzioni.carica(percorso)
        testi, a_mano = correzioni.testi_da_ritradurre(elenco)
        controlla("memoria di una versione vecchia con poche voci annotate: 'Corrette a mano' solo quelle vere, le "
                  "altre si ritraducono con 'Ritraduci tutto' (che lo dice)",
                  elenco.conteggi()["mano"] == 1 and voce(elenco, "Delete").a_mano and a_mano == 1
                  and "Delete" not in testi and "Quit to the desktop" in testi and "Start a new game" in testi
                  and elenco.automatiche_note and elenco.non_annotate == len(mem) - 2)
        comune_prima = leggi(progetto.percorso_comune("en", "it"))
        correzioni.salva(percorso, {"Quit to the desktop": "Torna a Windows",
                                    "Options and settings": "Options and settings"})
        auto, comune = leggi(auto_file), leggi(progetto.percorso_comune("en", "it"))
        elenco = correzioni.carica(percorso)
        controlla("correzione di una voce senza annotazione: annotata con la traduzione di prima, quindi corretta a "
                  "mano (Ritraduci tutto non la tocca); lasciata uguale all'originale non va nella memoria comune",
                  auto.get("Quit to the desktop") == mem["Quit to the desktop"]
                  and auto.get("Options and settings") == mem["Options and settings"]
                  and voce(elenco, "Quit to the desktop").a_mano and voce(elenco, "Options and settings").a_mano
                  and "Quit to the desktop" not in correzioni.testi_da_ritradurre(elenco)[0]
                  and comune.get("Quit to the desktop") == "Torna a Windows"
                  and comune.get("Options and settings") == comune_prima.get("Options and settings")
                  != "Options and settings")
        # the next translation too (which rereads the fixes made by hand for the shared translation memory) does not
        # put it there
        p = progetto.Progetto(percorso, silenzio)
        p._memoria_comune("en", "it")
        controlla("... e nemmeno la memoria comune riletta dalla traduzione successiva",
                  leggi(progetto.percorso_comune("en", "it")).get("Options and settings") != "Options and settings")
        automatico.ripristina(gioco, silenzio)
    finally:
        progetto.RADICE_DATI = vera_radice


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    if not os.environ.get("PATCHSMITH_DATI"):
        progetto.RADICE_DATI = os.path.join(base, "dati")
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    automatico.CARTELLA_PROFILI = None
    automatico.SPAZIO_GIOCO = automatico.SPAZIO_DATI = 0
    steam.cartella_steam = lambda: None
    ollama.cartella = lambda: os.path.join(base, "ollama")  # local translator not installed (unless the test says so)
    ollama.modello_consigliato = lambda gb=None: ("4b", "prova")
    tema.scegli = _scegli
    tema.avvisa = tema.informa = tema.errore = _avvisa
    tema.chiedi = lambda _g, _c, titolo, testo: FINESTRE.append((titolo, testo, [])) or False
    gui.IMPOSTAZIONI = None
    gui.App._controllo_iniziale = lambda self: None  # no after-play check at startup
    vero_correggi = gui.App._correggi

    def correggi(self, filtro=None):
        self._ultima_correzioni = vero_correggi(self, filtro)
        return self._ultima_correzioni

    gui.App._correggi = correggi
    prova_funzioni(base)
    radice = tk.Tk()
    app = gui.App(radice)
    try:
        prova_web(base, radice, app)
        prova_pagine(app, radice)
        prova_spazio_gamemaker(base, radice, app)
        prova_cache_unity(base, radice, app)
        prova_memoria_vecchia(base)
    finally:
        radice.destroy()
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
