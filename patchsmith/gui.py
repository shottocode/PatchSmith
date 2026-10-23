"""Interfaccia grafica (Tkinter, inclusa in Python): scegli la cartella del gioco e premi Traduci.

Ogni operazione finisce con una finestra scelta dal suo esito (esito.py: tradotto, in parte, non supportato, gioco
aperto, traduttore fermo...) con i pulsanti per il passo successivo. Il registro di ogni operazione si salva anche su
disco, in registri/ tra i dati dell'utente (gli ultimi MAX_REGISTRI), con i dettagli tecnici degli errori; "Crea
rapporto" li mette in uno zip sul Desktop (rapporto.py).
All'apertura, i giochi con segnali nuovi dopo l'ultima partita (crash, traduttore fermo, file rimessi dal gioco...) si
controllano da soli (controllo.py): il risultato e' nella riga sotto la cartella, con "Dettagli" (e "Ritraduci" per
le frasi tradotte mentre si giocava da rifare).
"Correggi traduzioni…" apre la finestra con l'elenco delle traduzioni del gioco e i loro problemi (gui_correzioni.py):
le correzioni salvate li' si scrivono nel gioco da sole ("Riapplica"); da li' partono anche la ritraduzione dei testi
scelti, "Ritraduci tutto con Qualità (12B)" e "Ricomincia da zero".
"""
import datetime
import glob
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, ttk

from . import __version__, automatico, controllo, esito as esiti, icona, interruzione, lingua, rapporto, servizio_xunity
from . import interfaccia, musica, progetto, suoni, tema
from .backend import verifica_traduttore
from .esito import Esito
from .progetto import cartella_dati
from .testo import DETTAGLIO, leggibile, slug

IMPOSTAZIONI = None  # None = settings.json tra i dati dell'utente (progetto.cartella_dati); le prove la cambiano
VERSIONE_IMPOSTAZIONI = 2
# Traduttore usato dall'interfaccia. Il traduttore di prova "finto" resta solo da riga di comando
# (--backend finto) e per i collaudi automatici (App.backend_forzato).
BACKEND_PREDEFINITO = "locale"  # ripiego: il predefinito vero e' backend.predefinito()
TRADUTTORI = {"locale": "Traduttore locale (sul tuo PC)", "google": "Google gratuito (non ufficiale)",
              "deepl": "DeepL (con la tua chiave gratuita)", "mymemory": "MyMemory (gratuito)"}
AVVISO_GOOGLE = ("Google gratuito usa l'indirizzo del traduttore web di Google, che non è fatto per i programmi: Google "
                 "può rifiutare le richieste dopo poche frasi (da alcuni PC già dalla prima) e bloccarle per ore.\n\n"
                 "Quando succede PatchSmith si ferma da solo, mette in pausa Google e salva quello che ha già "
                 "tradotto; accanto al traduttore compare fino a quando dura la pausa. Se poi scegli un altro "
                 "traduttore, \"Analizza e traduci\" riparte da dove eri arrivato: traduce solo le frasi che mancano.\n\n"
                 "Per tradurre un gioco intero conviene il traduttore locale (sul tuo PC, nessun limite) oppure "
                 "DeepL con la tua chiave gratuita.")
MODELLI_LOCALI = {"4b": "Veloce (4B)", "12b": "Qualità (12B, più lento)"}
# i controlli prima di partire (automatico.controlli_preliminari): titolo e tipo della finestra
FINESTRE_PRELIMINARI = {"GIOCO_APERTO": ("Il gioco è aperto", "avviso"), "LINGUA": ("Lingua da controllare", "avviso"),
                        "SOLA_LETTURA": ("Cartella del gioco protetta", "errore"),
                        "SPAZIO": ("Poco spazio libero", "avviso"), "TRADUTTORE_IN_PAUSA": ("Traduttore in pausa", "avviso"),
                        "LOCALE_ASSENTE": ("Traduttore locale da installare", "domanda"),
                        "STESSO_GIOCO": ("Stesso gioco già tradotto?", "domanda")}
_FERMATO = object()  # risultato di un lavoro fermato con il tasto "Ferma"
MAX_REGISTRI = 30  # registri delle operazioni tenuti su disco (i piu' vecchi si cancellano)


def _impostazioni():
    return IMPOSTAZIONI or cartella_dati("settings.json")


def _blocco_note():
    """Il Blocco note di Windows, con il percorso intero (mai un notepad.exe della cartella corrente)."""
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "notepad.exe")


def _apri(percorso):
    """Apre il file con il programma predefinito; se Windows non ne ha uno per quel tipo di file (es. .json su un PC
    nuovo), con il Blocco note. OSError se non riesce nessuno dei due."""
    if sys.platform.startswith("win"):
        try:
            os.startfile(percorso)  # noqa: S606 - apre il file con il programma predefinito
        except OSError:
            subprocess.Popen([_blocco_note(), percorso])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", percorso])


def mostra_file(percorso):
    """Apre la cartella con il file selezionato (Esplora file)."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(percorso)])
    else:
        _apri(os.path.dirname(percorso))


def cartella_registri():
    return cartella_dati("logs")


def impronta_codice():
    """(versione scritta in patchsmith/__init__.py, data piu' recente dei file del programma): se cambia mentre la
    finestra e' aperta, PatchSmith e' stato aggiornato e la finestra usa ancora il codice di prima."""
    cartella = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(cartella, "__init__.py"), encoding="utf-8") as f:
            m = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", f.read())
        versione = m.group(1) if m else None
    except OSError:
        versione = None
    date = []
    for p in glob.glob(os.path.join(glob.escape(cartella), "*.py")):
        try:
            date.append(os.path.getmtime(p))
        except OSError:
            pass
    return versione, max(date, default=0)


def _quanti(n, uno, molti):
    return "%d %s" % (n, uno if n == 1 else molti)


def _nota_musicale(master, colore, accesa):
    """Nota musicale 16x16 disegnata qui (come l'altoparlante): barrata se la musica e' spenta."""
    img = tk.PhotoImage(master=master, width=16, height=16)
    punti = [(x, y) for x in range(2, 8) for y in range(10, 15) if ((x - 4.5) / 2.6) ** 2 + ((y - 12) / 1.9) ** 2 <= 1]
    punti += [(x, y) for x in (7, 8) for y in range(2, 12)]  # l'asta
    punti += [(9, 3), (10, 3), (10, 4), (11, 4), (11, 5), (12, 6), (12, 7), (12, 8), (11, 9)]  # la bandierina
    if not accesa:
        punti += [(i, 15 - i) for i in range(1, 15)] + [(i, 14 - i) for i in range(1, 14)]
    for x, y in set(punti):
        img.put(colore, (x, y))
    return img


def _altoparlante(master, colore, acceso):
    """Altoparlante 16x16 disegnato qui (come le bandierine): con le onde se acceso, con la x se spento."""
    img = tk.PhotoImage(master=master, width=16, height=16)
    punti = [(x, y) for x in range(1, 4) for y in range(6, 10)]  # il corpo
    punti += [(x, y) for x in range(4, 8) for y in range(6 - (x - 3), 10 + (x - 3))]  # il cono
    if acceso:
        for raggio in (3.0, 5.6):  # due onde
            punti += [(x, y) for x in range(9, 16) for y in range(16)
                      if abs(((x - 7.5) ** 2 + (y - 7.5) ** 2) ** 0.5 - raggio) < 0.6 and abs(y - 7.5) < raggio * 0.8]
    else:
        punti += [(10 + i, 5 + i) for i in range(6)] + [(10 + i, 10 - i) for i in range(6)]
    for x, y in punti:
        img.put(colore, (x, y))
    return img


class App:
    def __init__(self, radice):
        self.radice = radice
        self.coda = queue.Queue()
        self._righe_registro = []  # le righe del registro della finestra come scritte (per cambiare lingua)
        self.lavoro = None
        self._registro = None  # file del registro dell'operazione in corso (logs/AAAAMMGG-HHMMSS-<gioco>.log)
        self._lucchetto = threading.Lock()
        self._operazione = None  # traduci / riapplica / ripristina / installa / rapporto
        self._ripeti = None  # per "Riprova": ripete l'ultima operazione sul gioco
        self._scelte_traduci = {"forza": False, "traduci_comunque": False}  # dell'ultimo Analizza e traduci
        self._in_errore = False
        self.ultimo_esito = None
        self.esiti_controllo = []  # esiti dell'ultimo controllo dopo aver giocato (uno per gioco), per "Dettagli"
        self._controllo_automatico = False  # il controllo in corso e' quello partito da solo all'apertura
        # il codice caricato: se i file del programma cambiano (aggiornamento) si avvisa di riaprire la finestra
        self._codice = impronta_codice()
        self._avvisato_codice = False
        # errori nei comandi della finestra (pythonw non ha una console): nel registro su disco e in una finestra
        radice.report_callback_exception = self._errore_interfaccia
        radice.title("PatchSmith")  # (la versione e' nei registri e nei rapporti)
        try:  # martello e cartella (icona.py) su questa finestra e su quelle che si aprono dopo
            icona.applica(radice)
        except tk.TclError:  # immagini rovinate: resta l'icona di Tk
            pass
        radice.minsize(720, 480)
        imp = self._leggi_impostazioni()
        # lingua dell'interfaccia (bandierina in alto a destra): i testi si traducono quando la finestra li mostra
        self.lingua_interfaccia = interfaccia.imposta(imp.get("interfaccia") or interfaccia.predefinita())
        interfaccia.aggancia_tk()
        _traduci_costanti()
        self.scelta_tema = imp.get("tema", "auto")  # auto (come Windows) / scuro / chiaro
        self.suoni_accesi = bool(imp.get("suoni", False))  # spenti di default (pulsante con l'altoparlante)
        self.musica_accesa = bool(imp.get("musica", False))  # spenta di default (pulsante con la nota)
        self._suono_atteso = False  # vero alla fine di un'operazione, fino alla prima finestra (_suono_finestra)
        self._suono_forzato = None  # il suono da usare per la prossima finestra al posto di quello del suo tipo
        tema.AL_MOSTRARE = self._suono_finestra
        # il clic di ogni pulsante, anche nelle altre finestre (finestre di dialogo, "Correggi traduzioni")
        radice.bind_class("TButton", "<ButtonPress-1>", self._clic_pulsante, add="+")
        if imp.get("versione", 1) < 2:  # dalla 0.2.1 la lingua del gioco si rileva da sola
            imp["da"] = lingua.AUTO
        self.colori = tema.applica(radice, self.scelta_tema)

        cornice = ttk.Frame(radice, padding=12)
        cornice.pack(fill="both", expand=True)
        cornice.columnconfigure(1, weight=1)

        ttk.Label(cornice, text="Cartella del gioco:").grid(row=0, column=0, sticky="w")
        self.cartella = tk.StringVar(value=imp.get("cartella", ""))
        # solo da "Sfoglia…": niente percorsi scritti a mano (sbagliati o a meta'); il testo si puo' copiare
        self.voce_cartella = ttk.Entry(cornice, textvariable=self.cartella, state="readonly")
        self.voce_cartella.grid(row=0, column=1, sticky="ew", padx=6)
        # largo quanto la colonna (cioe' quanto "Crea rapporto"): i bordi a destra sono tutti allineati; alto quanto
        # il campo della cartella
        self.btn_sfoglia = ttk.Button(cornice, text="Sfoglia…", style="Campo.TButton", command=self._sfoglia)
        self.btn_sfoglia.grid(row=0, column=2, sticky="nsew")
        # bandierina: lingua dell'interfaccia (le lingue sono i file in patchsmith/interfaccia/)
        self._bandiere = {c: interfaccia.bandiera(c, radice) for c in interfaccia.lingue()}
        # (una tendina disegnata qui: i menu di Windows hanno il bordo chiaro anche nel tema scuro)
        angolo = ttk.Frame(cornice)  # sotto "Sfoglia…": musica, suoni, tema chiaro/scuro e lingua
        angolo.grid(row=2, column=2, sticky="e", pady=(8, 0))
        self.btn_musica = ttk.Button(angolo, style="Bandiera.TButton", command=self._cambia_musica)
        self.btn_musica.pack(side="left", padx=(0, 4))
        self.btn_suoni = ttk.Button(angolo, style="Bandiera.TButton", command=self._cambia_suoni)
        self.btn_suoni.pack(side="left", padx=(0, 4))
        self.btn_tema = ttk.Button(angolo, style="Bandiera.TButton", command=self._cambia_tema)
        self.btn_tema.pack(side="left", padx=(0, 4))
        self.btn_lingua = ttk.Button(angolo, image=self._bandiere.get(self.lingua_interfaccia),
                                     style="Bandiera.TButton", command=self._menu_lingue)
        self.btn_lingua.pack(side="left")
        self._simbolo_tema()
        self._simbolo_suoni()
        self._simbolo_musica()
        self._tendina_lingue = None

        # riga in fondo, sotto il registro: a sinistra i pulsanti del controllo dopo aver giocato ("Dettagli ultima
        # partita", "Ritraduci N frasi"), che si vedono solo quando c'e' qualcosa da fare (il risultato e' gia' nel
        # registro), poi la riga di stato; "Crea rapporto" a destra. stato_controllo resta il riassunto del controllo
        # (per le prove e il registro).
        self.fondo = ttk.Frame(cornice)
        self.fondo.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        riga_controllo = self.riga_controllo = ttk.Frame(self.fondo)
        self.stato_controllo = tk.StringVar(value="")
        self.btn_dettagli = ttk.Button(riga_controllo, text="Dettagli ultima partita",
                                       command=self._dettagli_controllo)
        # frasi tradotte mentre giocavi da rifare: il controllo all'apertura non le ritraduce da solo (caricare il
        # traduttore locale o aspettare le pause di Google bloccherebbe la finestra), lo fa questo pulsante
        self.btn_ritraduci = ttk.Button(riga_controllo, text="Ritraduci", command=self._ritraduci)

        opzioni = ttk.Frame(cornice)
        opzioni.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(opzioni, text="Da:").pack(side="left")
        # lingue solo dall'elenco; una lingua salvata che non c'e' (vecchie impostazioni) torna alla predefinita
        elenco_da, elenco_a = lingua.etichette(con_auto=True), lingua.etichette()
        # (nei menu i nomi delle lingue sono nella lingua dell'interfaccia: da li' si torna al codice con _codice)
        self._etichette = {interfaccia.traduci(e): e for e in elenco_da}
        da = lingua.etichetta(imp.get("da") or lingua.AUTO)
        self.da = tk.StringVar(value=interfaccia.traduci(da if da in elenco_da else lingua.etichetta(lingua.AUTO)))
        ttk.Combobox(opzioni, textvariable=self.da, values=elenco_da, state="readonly",
                     width=22).pack(side="left", padx=(4, 12))
        ttk.Label(opzioni, text="A:").pack(side="left")
        a = lingua.etichetta(imp.get("a") or lingua.lingua_di_windows())
        self.a = tk.StringVar(value=interfaccia.traduci(a if a in elenco_a else
                                                        lingua.etichetta(lingua.lingua_di_windows())))
        ttk.Combobox(opzioni, textvariable=self.a, values=elenco_a, state="readonly",
                     width=20).pack(side="left", padx=(4, 12))
        # backend_forzato serve ai collaudi automatici ("finto")
        self.backend_forzato = None

        servizi = ttk.Frame(cornice)
        servizi.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(servizi, text="Traduttore:").pack(side="left")
        self.chiave_deepl = imp.get("chiave_deepl", "")
        self.email_mymemory = imp.get("email_mymemory", "")
        from . import ollama
        from .backend import predefinito as traduttore_predefinito
        predefinito = traduttore_predefinito()
        scelto = imp.get("backend") if imp.get("backend") in TRADUTTORI else predefinito
        self.traduttore = tk.StringVar(value=TRADUTTORI[scelto])
        self._scelto = scelto
        self.scelta_traduttore = ttk.Combobox(servizi, textvariable=self.traduttore, values=list(TRADUTTORI.values()),
                                              state="readonly", width=32)
        self.scelta_traduttore.pack(side="left", padx=(4, 8))
        self.scelta_traduttore.bind("<<ComboboxSelected>>", lambda _e: self._cambia_traduttore(scelto_ora=True))
        self.etichetta_credenziale = ttk.Label(servizi, text="")
        self.credenziale = tk.StringVar()
        self.voce_credenziale = ttk.Entry(servizi, textvariable=self.credenziale, width=30)
        # prova subito la chiave DeepL o l'email di MyMemory, senza aspettare "Analizza e traduci"
        self.btn_verifica = ttk.Button(servizi, text="Verifica", command=self._verifica_credenziale)
        self._avvisato_google = False
        self.stato_traduttore = ttk.Label(servizi, text="", style="Stato.TLabel")
        self.btn_riattiva = ttk.Button(servizi, text="Riattiva", command=self._riattiva)
        # il modello: quello salvato, se no il migliore gia' scaricato; senza nessuno dei due si propone quello adatto
        # alla scheda video (ollama.modello_consigliato, cercato in background: _proponi_modello)
        self._modello_scelto = imp.get("modello_locale") in MODELLI_LOCALI
        modello = imp.get("modello_locale") if self._modello_scelto else ollama.modello_predefinito()
        self._consiglio_modello = None  # (chiave, motivo) dalla scheda video
        self._prova_locale = None  # l'ultima prova del traduttore locale (backend.prova_locale)
        self._poi = None  # da rifare dopo l'installazione del traduttore locale (es. "Analizza e traduci")
        self.modello_locale = tk.StringVar(value=MODELLI_LOCALI[modello])
        self.scelta_modello = ttk.Combobox(servizi, textvariable=self.modello_locale,
                                           values=list(MODELLI_LOCALI.values()), state="readonly", width=22)
        self.scelta_modello.bind("<<ComboboxSelected>>", lambda _e: self._scegli_modello())
        self.btn_installa = ttk.Button(servizi, text="Installa…", command=self._installa_locale)
        self._cambia_traduttore(primo=True)
        self.radice.after(60_000, lambda: self._aggiorna_stato_servizio(ripeti=True))
        if not self._modello_scelto and not any(ollama.modello_presente(k) for k in MODELLI_LOCALI):
            threading.Thread(target=lambda: self.coda.put(("modello", ollama.modello_consigliato())),
                             daemon=True).start()

        pulsanti = ttk.Frame(cornice)
        pulsanti.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        self.btn_traduci = ttk.Button(pulsanti, text="Analizza e traduci", command=self._traduci)
        self.btn_traduci.pack(side="left")
        self.btn_correggi = ttk.Button(pulsanti, text="Correggi traduzioni…", command=self._correggi)
        self.btn_correggi.pack(side="left", padx=6)
        self.btn_ripristina = ttk.Button(pulsanti, text="Ripristina originali", command=self._ripristina)
        self.btn_ripristina.pack(side="right")
        # attivo solo mentre un'operazione e' in corso: si ferma al primo punto sicuro (traduzioni salvate)
        self.btn_ferma = ttk.Button(pulsanti, text="Ferma", command=self._ferma)
        self.btn_ferma.pack(side="right", padx=6)
        self.btn_ferma.state(["disabled"])
        # disattivati mentre un lavoro e' in corso: mai due lavori insieme (ne' cambi di traduttore a meta')
        self.pulsanti = [self.btn_traduci, self.btn_correggi, self.btn_ripristina,
                         self.btn_installa, self.btn_riattiva, self.scelta_traduttore, self.scelta_modello,
                         self.btn_ritraduci]

        # barra di avanzamento: visibile solo mentre un'operazione e' in corso
        self.barra = ttk.Progressbar(cornice, mode="indeterminate")
        self.barra.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.barra.grid_remove()

        registro = ttk.Frame(cornice)
        registro.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        cornice.rowconfigure(6, weight=1)
        self.testo = tk.Text(registro, wrap="word", height=18, font=("Consolas", 9), state="disabled")
        tema.colora_testo(self.testo, self.colori)
        scorri = ttk.Scrollbar(registro, command=self.testo.yview)
        self.testo.configure(yscrollcommand=scorri.set)
        self.testo.pack(side="left", fill="both", expand=True)
        scorri.pack(side="right", fill="y")

        self.stato = interfaccia.TestoVar(value="")
        self.etichetta_stato = ttk.Label(self.fondo, textvariable=self.stato, style="Stato.TLabel")
        self.etichetta_stato.pack(side="left")
        # mentre il gioco e' aperto con il suo traduttore acceso (vedi _sorveglia_dal_vivo)
        self.btn_ferma_vivo = ttk.Button(self.fondo, text="Ferma il traduttore del gioco",
                                         command=self._ferma_dal_vivo)
        self._vivo_acceso = None  # nome del gioco con il traduttore acceso, l'ultima volta che si e' guardato
        self._diario_letto = {}  # gioco -> fin dove il suo diario e' gia' stato riassunto nel registro
        # registri, impostazioni (senza chiave ne' email) e file utili in uno zip sul Desktop, da inviare a chi aiuta
        self.btn_rapporto = ttk.Button(cornice, text="Crea rapporto", command=self._crea_rapporto)
        self.btn_rapporto.grid(row=7, column=2, sticky="e", pady=(6, 0))
        self.pulsanti.append(self.btn_rapporto)

        self.radice.after(100, self._svuota_coda)
        self.radice.after(3000, self._sorveglia_dal_vivo)
        self.radice.protocol("WM_DELETE_WINDOW", self._chiudi)
        self.radice.after(500, self._controllo_iniziale)
        if self.musica_accesa:  # accesa l'ultima volta: riparte
            musica.avvia()

    def _chiudi(self):
        """Non chiudere a meta' di un'installazione o di una scrittura nei file del gioco."""
        if self.lavoro is not None and self.lavoro.is_alive():
            if not tema.chiedi(self.radice, self.colori, "Operazione in corso",
                               "PatchSmith sta ancora lavorando sui file del gioco.\n"
                               "Se chiudi adesso l'operazione resta a metà. Chiudere comunque?"):
                return
        self._salva_impostazioni()  # traduttore, chiave/email, cartella e lingue scelti: li ritrovi alla riapertura
        musica.ferma()
        self.radice.destroy()

    # --- impostazioni -------------------------------------------------------------------------

    def _leggi_impostazioni(self):
        try:
            with open(_impostazioni(), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _cambia_traduttore(self, primo=False, scelto_ora=False):
        """Mostra il campo giusto (chiave DeepL, email per MyMemory) e lo stato del traduttore scelto. scelto_ora: scelto
        adesso dal menu (per Google, una volta per sessione, la finestra che spiega i blocchi)."""
        if not primo:  # conserva quello scritto nel campo del traduttore di prima
            if self._scelto == "deepl":
                self.chiave_deepl = self.credenziale.get().strip()
            elif self._scelto == "mymemory":
                self.email_mymemory = self.credenziale.get().strip()
        self._scelto = next((k for k, v in TRADUTTORI.items() if v == self.traduttore.get()), BACKEND_PREDEFINITO)
        for w in (self.etichetta_credenziale, self.voce_credenziale, self.btn_verifica, self.stato_traduttore,
                  self.btn_riattiva, self.scelta_modello, self.btn_installa):
            w.pack_forget()
        if self._scelto == "deepl":
            self.etichetta_credenziale.configure(text="Chiave DeepL:")
            self.credenziale.set(self.chiave_deepl)
            self.voce_credenziale.configure(show="•")
        elif self._scelto == "mymemory":
            self.etichetta_credenziale.configure(text="Email (facoltativa):")
            self.credenziale.set(self.email_mymemory)
            self.voce_credenziale.configure(show="")
        if self._scelto in ("deepl", "mymemory"):
            self.etichetta_credenziale.pack(side="left")
            self.voce_credenziale.pack(side="left", padx=(4, 4))
            self.btn_verifica.pack(side="left", padx=(0, 8))
        if self._scelto == "locale":
            self.scelta_modello.pack(side="left", padx=(0, 8))
        self._aggiorna_stato_servizio()
        if scelto_ora:
            self._salva_impostazioni()
        if scelto_ora and self._scelto == "google" and not self._avvisato_google:
            self._avvisato_google = True
            tema.avvisa(self.radice, self.colori, "Google gratuito: può bloccarsi", AVVISO_GOOGLE)

    def _verifica_credenziale(self):
        """Pulsante "Verifica": la chiave DeepL o l'email di MyMemory, in background (una richiesta piccola)."""
        nome = self._scelto
        valore = "".join(self.credenziale.get().split()) if nome == "deepl" else self.credenziale.get().strip()
        if nome == "deepl":
            self.chiave_deepl = valore
            if not valore:
                tema.avvisa(self.radice, self.colori, "Chiave DeepL", "Incolla prima la tua chiave DeepL nel campo "
                            "accanto. Si crea gratis con un account \"DeepL API Free\" (500.000 caratteri al mese).")
                return
        elif nome == "mymemory":
            self.email_mymemory = valore
        self._salva_impostazioni()
        self.btn_verifica.state(["disabled"])
        self.btn_verifica.configure(text="Verifico…")

        def _lavoro():
            self.coda.put(("verifica", (nome, verifica_traduttore(nome, valore))))

        threading.Thread(target=_lavoro, daemon=True).start()

    def _fine_verifica(self, nome, risultato):
        self.btn_verifica.state(["!disabled"])
        self.btn_verifica.configure(text="Verifica")
        ok, messaggio = risultato
        titolo = {"deepl": "Chiave DeepL", "mymemory": "MyMemory"}.get(nome, "Verifica")
        if ok:
            tema.informa(self.radice, self.colori, titolo, messaggio)
        else:
            tema.errore(self.radice, self.colori, titolo, messaggio)

    def _chiave_modello(self):
        return next((k for k, v in MODELLI_LOCALI.items() if v == self.modello_locale.get()), "4b")

    def _scegli_modello(self):
        self._modello_scelto = True  # scelto dall'utente: la scheda video non lo cambia piu'
        self._aggiorna_stato_servizio()

    def _proponi_modello(self, consiglio, sincrono=False):
        """Il modello adatto alla scheda video (ollama.modello_consigliato: (chiave, motivo)), se l'utente non ne ha
        scelto uno e non ce n'e' nessuno scaricato: il 12B con almeno 8 GB di memoria video, se no il 4B."""
        from . import ollama
        self._consiglio_modello = consiglio
        if self._modello_scelto or (self._occupato() and not sincrono) or \
                any(ollama.modello_presente(k) for k in MODELLI_LOCALI):
            return
        chiave = consiglio[0] if consiglio and consiglio[0] in MODELLI_LOCALI else None
        if chiave and self.modello_locale.get() != MODELLI_LOCALI[chiave]:
            self.modello_locale.set(MODELLI_LOCALI[chiave])
            self._aggiorna_stato_servizio()

    def _occupato(self):
        return self.lavoro is not None and self.lavoro.is_alive()

    def _installa_locale(self, poi=None):
        """Installazione guidata del traduttore locale: propone il modello adatto alla scheda video, elenca cosa
        scarica e parte solo dopo il si'. Alla fine una frase di prova (_installa_e_prova) dice se funziona e quanto
        e' veloce; poi (se riesce) fa 'poi', per esempio l'"Analizza e traduci" che aspettava il traduttore."""
        from . import ollama
        if self._occupato():
            return
        if not self._modello_scelto and not any(ollama.modello_presente(k) for k in MODELLI_LOCALI):
            self._proponi_modello(self._consiglio_modello or ollama.modello_consigliato(), sincrono=True)
        scelti = [self._chiave_modello()]
        righe = ollama.descrizione_download(scelti)
        if not righe:
            self._aggiorna_stato_servizio()
            if poi:
                poi()
            return
        consiglio = ""
        if self._consiglio_modello and not ollama.modello_presente(scelti[0]):
            consiglio = "\n\n%s Ora è scelto \"%s\"; per l'altro sceglilo nel menu accanto e premi di nuovo " \
                        "\"Installa…\"." % (self._consiglio_modello[1], MODELLI_LOCALI[scelti[0]])
        try:
            lento = ollama.avviso_prestazioni()
        except Exception:  # noqa: BLE001 - solo un avviso: non deve impedire l'installazione
            lento = ""
        testo = ("Per il traduttore locale servono questi download, salvati in %s:\n\n- %s\n\nNessun limite e "
                 "nessun blocco: dopo l'installazione traduce anche senza internet.%s%s\n\nProcedo?"
                 % (ollama.cartella(), "\n- ".join(righe), consiglio, "\n\nAttenzione: " + lento if lento else ""))
        if tema.chiedi(self.radice, self.colori, "Installare il traduttore locale?", testo):
            self._poi = poi
            a = self._lingua_a()
            self._avvia("Installazione del traduttore locale", lambda _c: self._installa_e_prova(scelti, a),
                        fermabile=True, serve_cartella=False, operazione="installa")

    def _installa_e_prova(self, scelti, a="it"):
        """(nel lavoro in background) Installa e poi traduce una frase di prova, misurando i tempi: il risultato
        (backend.prova_locale) va nella riga di stato in basso."""
        from . import backend, ollama
        ollama.installa(scelti, self._log)
        self._log("Prova del traduttore locale: traduco \"Start game\" (la prima volta il modello si carica nella "
                  "scheda video: può volerci un minuto)…")
        risultato = backend.prova_locale(scelti[0], self._log, a=a)
        self._log(("  " if risultato["ok"] else "  ! ") + risultato["messaggio"])
        return risultato

    def _aggiorna_stato_servizio(self, ripeti=False):
        """Accanto al traduttore: se e' in pausa dopo un blocco (fino a quando) o disattivato (pulsante Riattiva);
        per il traduttore locale se e' pronto o da installare. Con ripeti=True si riaggiorna ogni minuto."""
        from .limitatore import Limitatore
        self.stato_traduttore.pack_forget()
        self.btn_riattiva.pack_forget()
        self.btn_installa.pack_forget()
        if self._scelto == "locale":
            from . import ollama
            pronto = ollama.installato() and ollama.modello_presente(self._chiave_modello())
            lento = pronto and (self._prova_locale or {}).get("scheda") == 0  # la prova: solo il processore
            # installato: niente scritta (solo l'avviso se usa il processore); se no "da installare" con "Installa…"
            testo = ("lento: senza scheda video" if lento else "") if pronto else "da installare"
            self.stato_traduttore.configure(text=testo)
            if testo:
                self.stato_traduttore.pack(side="left")
            if not pronto:
                self.btn_installa.pack(side="left", padx=(6, 0))
            if ripeti:
                self.radice.after(60_000, lambda: self._aggiorna_stato_servizio(ripeti=True))
            return
        breve = Limitatore(self._scelto, log=None).stato_breve()
        if breve:
            self.stato_traduttore.configure(text=breve)
            self.stato_traduttore.pack(side="left")
            if breve.startswith("disattivato"):
                self.btn_riattiva.pack(side="left", padx=(6, 0))
        if ripeti:
            self.radice.after(60_000, lambda: self._aggiorna_stato_servizio(ripeti=True))

    def _riattiva(self):
        from .limitatore import NOMI, Limitatore
        nome = NOMI.get(self._scelto, self._scelto)
        if tema.chiedi(self.radice, self.colori, "Riattivare %s?" % nome,
                       "%s ha rifiutato piu' volte le richieste. Se lo riattivi e continua a rifiutarle, insistere "
                       "puo' allungare il blocco. Riattivare?" % nome):
            Limitatore(self._scelto, log=None).azzera()
            self._aggiorna_stato_servizio()

    def _salva_impostazioni(self):
        if self._scelto == "deepl":  # niente spazi o a capo incollati per sbaglio dentro la chiave
            self.chiave_deepl = "".join(self.credenziale.get().split())
        elif self._scelto == "mymemory":
            self.email_mymemory = self.credenziale.get().strip()
        etichette = getattr(self, "_etichette", {})
        dati = {"cartella": self.cartella.get(), "da": lingua.codice(etichette.get(self.da.get(), self.da.get())),
                "a": self._lingua_a(),
                "backend": self._scelto, "versione": VERSIONE_IMPOSTAZIONI,
                "tema": self.scelta_tema, "suoni": getattr(self, "suoni_accesi", False),
                "musica": getattr(self, "musica_accesa", False),
                "chiave_deepl": self.chiave_deepl, "email_mymemory": self.email_mymemory,
                "modello_locale": self._chiave_modello(), "interfaccia": getattr(self, "lingua_interfaccia", interfaccia.attuale())}
        try:
            os.makedirs(os.path.dirname(_impostazioni()), exist_ok=True)
            with open(_impostazioni(), "w", encoding="utf-8") as f:
                json.dump(dati, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _codice_lingua(self, etichetta):
        """Il codice della lingua da un'etichetta del menu "Da" o "A" (anche tradotta nella lingua dell'interfaccia)."""
        return lingua.codice(self._etichette.get(etichetta, etichetta))

    def _simbolo_tema(self):
        """Sul pulsante del tema il simbolo del tema di adesso: luna = scuro, sole = chiaro."""
        self.btn_tema.configure(text="☾" if self.colori.get("nome") == "scuro" else "☀")

    def _cambia_tema(self):
        """Pulsante sole/luna: passa dal tema scuro a quello chiaro e viceversa, subito, e lo ricorda."""
        self.scelta_tema = "chiaro" if self.colori.get("nome") == "scuro" else "scuro"
        self.colori = tema.applica(self.radice, self.scelta_tema)
        tema.colora_testo(self.testo, self.colori)
        self._simbolo_tema()
        self._simbolo_suoni()  # (l'altoparlante e la nota sono disegnati con il colore del testo)
        self._simbolo_musica()
        self._chiudi_tendina()
        self._salva_impostazioni()

    def _simbolo_suoni(self):
        """Sul pulsante dei suoni l'altoparlante con le onde (accesi) o con la x (spenti), del colore del testo."""
        self._img_suoni = _altoparlante(self.radice, self.colori["testo"], self.suoni_accesi)
        self.btn_suoni.configure(image=self._img_suoni)

    def _cambia_suoni(self):
        """Pulsante con l'altoparlante: accende o spegne i suoni (accendendoli si sente il clic) e lo ricorda."""
        self.suoni_accesi = not self.suoni_accesi
        self._simbolo_suoni()
        self._chiudi_tendina()
        self._salva_impostazioni()
        if self.suoni_accesi:
            suoni.suona("clic")

    def _simbolo_musica(self):
        """Sul pulsante della musica la nota (accesa) o la nota barrata (spenta), del colore del testo."""
        self._img_musica = _nota_musicale(self.radice, self.colori["testo"], self.musica_accesa)
        self.btn_musica.configure(image=self._img_musica)

    def _cambia_musica(self):
        """Pulsante con la nota: accende o spegne la musica di sottofondo (musica.py) e lo ricorda."""
        self.musica_accesa = not self.musica_accesa
        self._simbolo_musica()
        self._chiudi_tendina()
        self._salva_impostazioni()
        if self.musica_accesa:
            musica.avvia()
        else:
            musica.ferma()

    def _clic_pulsante(self, evento):
        """Con i suoni accesi, il clic quando si preme un pulsante (non uno disattivato)."""
        try:
            attivo = not evento.widget.instate(["disabled"])
        except (AttributeError, tk.TclError):
            return
        if self.suoni_accesi and attivo:
            suoni.suona("clic")

    def _suono_finestra(self, tipo):
        """Con i suoni accesi suona ogni finestra, in qualunque momento: "errore" per gli errori, "domanda" per
        domande, avvisi e informazioni (anche l'avviso di Google quando lo scegli). La prima finestra alla fine di
        un'operazione, se e' un'informazione, suona invece "fatto"; anche una traduzione scritta nel gioco solo in parte
        (un avviso, ma il gioco e' tradotto: _mostra_finestra)."""
        forzato, self._suono_forzato = self._suono_forzato, None
        fine, self._suono_atteso = self._suono_atteso, False
        if not self.suoni_accesi:
            return
        if tipo == "errore":
            suoni.suona("errore")
        elif fine and (forzato or tipo == "info"):
            suoni.suona(forzato or "fatto")
        else:
            suoni.suona("domanda")

    def _menu_lingue(self):
        """La tendina delle lingue sotto la bandierina, con i colori del tema (bordo compreso)."""
        if self._tendina_lingue is not None:
            self._chiudi_tendina()
            return
        c = self.colori
        t = self._tendina_lingue = tk.Toplevel(self.radice)
        t.overrideredirect(True)
        t.configure(background=c["bordo"])
        dentro = tk.Frame(t, background=c["campo"])
        dentro.pack(padx=1, pady=1)
        self._voci_lingue = []
        for codice, info in interfaccia.lingue().items():
            voce = tk.Label(dentro, image=self._bandiere[codice], text=interfaccia.FISSO + " %s (%s)" % (
                info["nome"], codice), compound="left", anchor="w", padx=8, pady=3, background=c["campo"],
                foreground=c["testo"], cursor="hand2")
            voce.pack(fill="x")
            voce.bind("<Enter>", lambda _e, v=voce: v.configure(background=c["selezione"]))
            voce.bind("<Leave>", lambda _e, v=voce: v.configure(background=c["campo"]))
            voce.bind("<Button-1>", lambda _e, k=codice: (self._chiudi_tendina(), self._cambia_interfaccia(k)))
            self._voci_lingue.append((codice, voce))
        t.update_idletasks()
        b = self.btn_lingua
        x = b.winfo_rootx() + b.winfo_width() - t.winfo_reqwidth()  # allineata a destra con il pulsante
        t.geometry("+%d+%d" % (x, b.winfo_rooty() + b.winfo_height() + 2))
        t.bind("<Escape>", lambda _e: self._chiudi_tendina())
        t.bind("<FocusOut>", lambda _e: self.radice.after(150, self._chiudi_tendina))
        t.focus_force()

    def _chiudi_tendina(self):
        if self._tendina_lingue is not None:
            try:
                self._tendina_lingue.destroy()
            except tk.TclError:
                pass
            self._tendina_lingue = None

    def _cambia_interfaccia(self, codice):
        """La bandierina: la finestra passa subito alla lingua scelta (registro compreso), che resta nelle impostazioni.
        Senza riaprire PatchSmith: i testi originali sono ricordati nei widget (interfaccia.ritraduci)."""
        if codice == self.lingua_interfaccia:
            return
        if self._occupato():
            tema.avvisa(self.radice, self.colori, "Operazione in corso", "Aspetta che l'operazione finisca, poi "
                        "cambia la lingua.")
            return
        # le scelte dei menu a tendina come codici, prima che cambino le loro etichette
        da, a, modello = self._codice_lingua(self.da.get()), self._codice_lingua(self.a.get()), self._chiave_modello()
        self.lingua_interfaccia = interfaccia.imposta(codice)
        _traduci_costanti()
        interfaccia.ritraduci(self.radice)
        self._etichette = {interfaccia.traduci(e): e for e in lingua.etichette(con_auto=True)}
        self.da.set(interfaccia.traduci(lingua.etichetta(da)))
        self.a.set(interfaccia.traduci(lingua.etichetta(a)))
        # (i nomi dei traduttori e dei modelli sono arrivati ai menu gia' tradotti: si rimettono da qui)
        self.scelta_traduttore.configure(values=list(TRADUTTORI.values()))
        self.traduttore.set(TRADUTTORI[self._scelto])
        self.scelta_modello.configure(values=list(MODELLI_LOCALI.values()))
        self.modello_locale.set(MODELLI_LOCALI[modello])
        self.btn_lingua.configure(image=self._bandiere.get(self.lingua_interfaccia))
        self.testo.configure(state="normal")
        self.testo.delete("1.0", "end")
        self.testo.insert("end", "".join(self._riga_mostrata(r) + "\n" for r in self._righe_registro))
        self.testo.see("end")
        self.testo.configure(state="disabled")
        self._salva_impostazioni()

    def _lingua_a(self):
        a = lingua.codice(getattr(self, "_etichette", {}).get(self.a.get(), self.a.get()))
        return lingua.lingua_di_windows() if a == lingua.AUTO else a

    def _backend(self):
        return self.backend_forzato or self._scelto

    # --- registro e lavori in background ----------------------------------------------------

    def _log(self, messaggio):
        originale = str(messaggio)
        riga = leggibile(originale)
        if DETTAGLIO in riga:  # dettaglio tecnico: solo nel registro su disco (e in "Crea rapporto")
            self._scrivi_registro(riga.replace(DETTAGLIO, ""))
            return
        self.coda.put(("log", originale))  # nella finestra nella lingua dell'interfaccia (_riga_mostrata)
        self._scrivi_registro(riga)  # su disco come in origine

    def _riga_mostrata(self, originale):
        """Una riga del registro come si vede nella finestra: tradotta, o in italiano con gli accenti."""
        if self.lingua_interfaccia != interfaccia.SORGENTE:
            return interfaccia.traduci(originale)
        return leggibile(originale)

    def _apri_registro(self, cartella, descrizione, nome=None):
        """Nuovo registro su disco per l'operazione: registri/AAAAMMGG-HHMMSS-<nome>.log, con nome quello del
        profilo del gioco (o della sua cartella); senza cartella 'interfaccia' (errori della finestra fuori da
        un'operazione) o il nome dato. I piu' vecchi oltre MAX_REGISTRI si cancellano. Senza registro si lavora lo
        stesso: qui non deve mai esserci un errore (i pulsanti sono gia' disattivati)."""
        self._chiudi_registro()
        if not nome:
            nome = slug(os.path.basename(os.path.normpath(cartella))) if cartella else "interfaccia"
            try:  # un profilo rovinato lo segnala poi l'operazione, con la sua finestra
                profilo = automatico.trova_profilo(cartella) if cartella and os.path.isdir(cartella) else None
                if profilo:
                    nome = os.path.splitext(os.path.basename(profilo))[0]
            except Exception:  # noqa: S110
                pass
        adesso = datetime.datetime.now()
        try:
            intestazione = "PatchSmith %s - %s - %s\nCartella: %s\nTraduttore: %s, lingue: %s -> %s\n" % (
                __version__, descrizione, adesso.strftime("%Y-%m-%d %H:%M:%S"), cartella or "-", self._backend(),
                self._codice_lingua(self.da.get()), self._lingua_a())
        except Exception:  # noqa: BLE001 - l'intestazione non deve impedire il registro
            intestazione = "PatchSmith %s - %s - %s\n" % (__version__, descrizione, adesso)
        try:
            os.makedirs(cartella_registri(), exist_ok=True)
            percorso = os.path.join(cartella_registri(), "%s-%s.log" % (adesso.strftime("%Y%m%d-%H%M%S"), nome))
            f = open(percorso, "a", encoding="utf-8")
            f.write(intestazione)
            f.flush()
        except OSError:
            return
        with self._lucchetto:
            self._registro = f
        for vecchio in sorted(glob.glob(os.path.join(glob.escape(cartella_registri()), "*.log")))[:-MAX_REGISTRI]:
            try:
                os.remove(vecchio)
            except OSError:
                pass

    def _scrivi_registro(self, testo):
        with self._lucchetto:
            if self._registro is None:
                return
            try:
                self._registro.write(testo + "\n")
                self._registro.flush()
            except (OSError, ValueError):
                pass

    def _chiudi_registro(self):
        with self._lucchetto:
            if self._registro is not None:
                try:
                    self._registro.close()
                except OSError:
                    pass
            self._registro = None

    def percorso_registro(self):
        """Il registro su disco dell'operazione in corso (o None)."""
        with self._lucchetto:
            return self._registro.name if self._registro is not None else None

    def _svuota_coda(self):
        try:
            while True:
                tipo, valore = self.coda.get_nowait()
                if tipo == "log":
                    self._righe_registro.append(valore)
                    self.testo.configure(state="normal")
                    self.testo.insert("end", self._riga_mostrata(valore) + "\n")
                    self.testo.see("end")
                    self.testo.configure(state="disabled")
                elif tipo == "fine":
                    self._fine(*valore)
                elif tipo == "controllo":
                    self._avvia_controllo_iniziale(valore)
                elif tipo == "modello":
                    self._proponi_modello(valore)
                elif tipo == "verifica":
                    self._fine_verifica(*valore)
        except queue.Empty:
            pass
        finally:  # anche se una finestra di fine operazione non riesce, la finestra continua a leggere la coda
            self.radice.after(100, self._svuota_coda)

    def _errore_interfaccia(self, tipo, valore, tb):
        """Errore in un comando della finestra: il dettaglio tecnico va nel registro su disco, all'utente una
        finestra con il tipo di errore e la proposta di creare un rapporto (mai "Riprova": ripeterebbe l'ultima
        operazione sul gioco, che con questo errore non c'entra)."""
        testo = "".join(traceback.format_exception(tipo, valore, tb))
        aperto = self._registro is not None
        if not aperto:
            self._apri_registro("", "Errore nell'interfaccia", "interfaccia")
        self._scrivi_registro("ERRORE IMPREVISTO NELL'INTERFACCIA (%s: %s)\n%s" % (tipo.__name__, valore, testo))
        if not aperto:
            self._chiudi_registro()
        if self._in_errore or not issubclass(tipo, Exception):
            return
        self._in_errore = True
        try:
            e = Esito("ERRORE", str(valore).strip(), azioni=["crea_rapporto"],
                      extra={"imprevisto": True, "tipo": tipo.__name__})
            self.ultimo_esito = e
            self.stato.set("Errore imprevisto: %s" % tipo.__name__)
            self._mostra_finestra(e, "interfaccia")
        except Exception:  # noqa: S110 - un errore anche qui: e' gia' nel registro, non si insiste
            pass
        finally:
            self._in_errore = False

    def _avvia(self, descrizione, funzione, fermabile=False, serve_cartella=True, operazione=None, registra=True,
               nome_registro=None):
        """Esegue funzione(cartella) in un thread. operazione: traduci/riapplica/ripristina/installa/rapporto/controlla
        (sceglie la finestra di fine); registra=False: senza registro su disco (es. la creazione del rapporto);
        nome_registro: la fine del nome del registro (predefinita: il gioco)."""
        cartella = self.cartella.get().strip().strip('"')
        if self._occupato():  # un lavoro alla volta: azzera() annullerebbe un "Ferma" ancora in corso
            return
        if serve_cartella and (not cartella or not os.path.isdir(cartella)):
            tema.avvisa(self.radice, self.colori, "Cartella mancante", "Scegli una cartella del gioco valida.")
            return
        self._controlla_codice()
        self._operazione = operazione
        self._salva_impostazioni()
        for b in self.pulsanti:
            b.state(["disabled"])
        interruzione.azzera()
        self.btn_ferma.state(["!disabled"] if fermabile else ["disabled"])  # solo dove si puo' fermare
        self.barra.grid()
        self.barra.start(12)
        self.stato.set(descrizione + "…")
        if registra:
            self._apri_registro(cartella if serve_cartella else "", descrizione,
                                nome_registro or ("traduttore-locale" if operazione == "installa" else None))
        self._log("\n" + "=" * 70 + "\n" + descrizione + ": " + cartella)

        def _esegui():
            try:
                risultato = funzione(cartella)
                self.coda.put(("fine", (descrizione, risultato, None)))
            except interruzione.Interrotto:
                self.coda.put(("fine", (descrizione, _FERMATO, None)))
            except Exception as e:  # l'errore va mostrato all'utente, non deve chiudere l'app
                # il dettaglio tecnico (traceback) solo nel registro su disco: serve a chi aiuta, non a chi gioca
                self._scrivi_registro("ERRORE (%s: %s)\n%s" % (type(e).__name__, e, traceback.format_exc()))
                self.coda.put(("fine", (descrizione, None, e)))

        self.lavoro = threading.Thread(target=_esegui, daemon=True)
        self.lavoro.start()

    def _ferma(self):
        interruzione.richiedi()
        self.btn_ferma.state(["disabled"])
        self.stato.set("Mi fermo appena possibile (le traduzioni fatte restano salvate)…")
        self._log("Richiesta di fermarsi: mi fermo al primo punto sicuro.")

    def _fine(self, descrizione, risultato, errore):
        # alla fine di un'operazione la prima finestra suona (se i suoni sono accesi; non il controllo all'apertura)
        self._suono_atteso = not (self._operazione == "controlla" and self._controllo_automatico)
        try:
            self._fine_operazione(descrizione, risultato, errore)
        finally:
            self._suono_atteso = False

    def _fine_operazione(self, descrizione, risultato, errore):
        self.barra.stop()
        self.barra.grid_remove()
        self.btn_ferma.state(["disabled"])
        self._aggiorna_stato_servizio()  # un blocco appena ricevuto si vede subito accanto al traduttore
        for b in self.pulsanti:
            b.state(["!disabled"])
        operazione = self._operazione
        poi = None  # l'operazione che aspettava l'installazione del traduttore locale (solo se e' riuscita)
        if operazione == "installa":
            poi, self._poi = self._poi, None
        if risultato is _FERMATO:
            if descrizione.startswith("Installazione"):
                self.stato.set(descrizione + ": fermata. 'Installa…' riprende lo scaricamento da dove era.")
            else:
                self.stato.set(descrizione + ": fermato. Le traduzioni fatte sono salvate: 'Analizza e traduci' "
                                             "riprende da li'.")
            self._log("Fermato.")
            self._chiudi_registro()
            return
        if errore:
            e = esiti.da_eccezione(errore)
            if getattr(errore, "da_zero", False):  # "Ricomincia da zero" non partito (vedi _traduci)
                e.extra["da_zero"] = True
            self._log("ERRORE imprevisto (%s): %s" % (type(errore).__name__, errore) if e.extra.get("imprevisto")
                      else "ERRORE: %s" % errore)
            if getattr(errore, "comando", None):
                self._scrivi_registro("(da riga di comando: %s)" % errore.comando)
            self._scrivi_registro(e.riassunto())
            self._chiudi_registro()
            self.ultimo_esito = e
            self.stato.set("%s: %s" % (descrizione, esiti.STATO[e.codice]))
            if operazione == "controlla" and self._controllo_automatico:  # all'apertura: niente finestre d'errore
                self.stato_controllo.set("Controllo dopo l'ultima partita non riuscito (i dettagli sono nel registro): "
                                         "puoi creare un rapporto.")
                return
            self._mostra_finestra(e, operazione)
            return
        if operazione == "controlla" and isinstance(risultato, list):
            for e in risultato:
                self._scrivi_registro(e.riassunto())
            self._chiudi_registro()
            self.esiti_controllo = risultato
            self.ultimo_esito = risultato[-1] if risultato else None
            self._aggiorna_riga_controllo()
            self.stato.set("")  # il risultato e' nel registro (e nei pulsanti in fondo, se c'e' qualcosa da fare)
            if not self._controllo_automatico:
                self._mostra_controllo()
            return
        if operazione == "installa" and isinstance(risultato, dict):  # installato, con la frase di prova
            self._chiudi_registro()
            self._prova_locale = risultato
            self._aggiorna_stato_servizio()
            self.stato.set("Traduttore locale installato: %s" % risultato["messaggio"])
            if poi and risultato.get("ok"):  # di nuovo l'operazione che aspettava il traduttore locale
                self.radice.after(100, poi)
            elif not risultato.get("ok"):  # dopo scaricamenti di qualche GB non basta la riga in basso
                testo = "Il traduttore locale è installato, ma %s" % risultato["messaggio"]
                if poi:
                    testo += ("\n\nPer questo l'operazione che aspettava il traduttore non è partita: premi di nuovo "
                              "il suo pulsante per riprovare.")
                if "rapporto" not in testo:
                    testo += "\n\nSe succede ancora, crea un rapporto (pulsante in basso a destra)."
                tema.avvisa(self.radice, self.colori, "Prova del traduttore locale non riuscita", testo)
            return
        if operazione == "rapporto" and risultato:
            self.stato.set("Rapporto creato: %s" % risultato)
            scelta = tema.scegli(self.radice, self.colori, "Rapporto creato",
                                 "Ho creato il rapporto:\n%s\n\nContiene i registri e le impostazioni utili per "
                                 "capire cosa è successo, senza la chiave DeepL e senza l'email. Puoi inviarlo a chi "
                                 "ti aiuta con PatchSmith." % risultato,
                                 [("OK", None), ("Apri la cartella", "apri")])
            if scelta == "apri":
                mostra_file(risultato)
            return
        if isinstance(risultato, Esito):
            self._scrivi_registro(risultato.riassunto())
            self._chiudi_registro()
            self._mostra_esito(descrizione, risultato)
            return
        self._chiudi_registro()
        self.stato.set(descrizione + ": completato.")

    def _locale_pronto(self):
        from . import ollama
        try:
            return ollama.stato() == "pronto"
        except OSError:
            return False

    def _mostra_finestra(self, e, operazione):
        """La finestra di fine operazione per l'esito (testo e pulsanti da esito.finestra) e l'azione scelta."""
        dati = esiti.finestra(e, operazione or "traduci", locale_pronto=self._locale_pronto(),
                              attuale=self._backend())
        if e.codice == "PARZIALE" and e.scritto and operazione != "ripristina":  # tradotto, con qualche testo rimasto
            self._suono_forzato = "fatto"
        scelta = tema.scegli(self.radice, self.colori, dati["titolo"], dati["testo"], dati["pulsanti"], dati["tipo"])
        self._azione(scelta, e)
        return dati

    def _mostra_esito(self, descrizione, e):
        """Fine di Analizza e traduci / Riapplica / Ripristina: una finestra per ogni esito, con il passo dopo."""
        self.ultimo_esito = e
        x = e.extra
        if e.codice == "UFFICIALE" and x.get("lingua_ufficiale"):  # la lingua ufficiale c'e' (lingua_ufficiale.py)
            righe = x.get("righe") or [e.motivo]
            if x.get("serve_consenso"):
                self.stato.set("Il gioco ha gia' la lingua ufficiale: serve il tuo consenso per attivarla.")
                if tema.chiedi(self.radice, self.colori, "Attivare la lingua del gioco?",
                               "\n\n".join(righe) + "\n\nProcedo? (Te lo chiedo solo questa volta per "
                               "questo gioco.)"):
                    self._traduci(forza=x.get("forza", False), consenso=True)
                return
            if not x.get("supportato", True):
                self.stato.set("Lingua ufficiale attivata." if x.get("impostata") else
                               "Il gioco ha gia' la lingua ufficiale.")
                tema.informa(self.radice, self.colori, "Lingua ufficiale", "\n\n".join(righe))
                return
            self.stato.set("Il gioco ha gia' la lingua ufficiale.")
            if tema.chiedi(
                self.radice, self.colori, "Lingua ufficiale disponibile",
                "\n\n".join(righe) + "\n\nVuoi comunque una traduzione automatica?"
                " (di solito non serve: quella ufficiale è migliore)",
            ):
                self._traduci(forza=x.get("forza", False), traduci_comunque=True)
            return
        if e.codice == "NON_SUPPORTATO" and "sperimentale" in e.azioni:
            self.stato.set("%s: %s" % (descrizione, esiti.STATO[e.codice]))
            scelta = tema.scegli(
                self.radice, self.colori, "Motore non ancora supportato",
                "Il motore di questo gioco (%s) non è ancora supportato in automatico.\n\n"
                "Ho però trovato file con testo. Vuoi provare a tradurli in modo sperimentale?\n"
                "(Viene fatto comunque il backup degli originali.)" % x.get("motore_rilevato", "non riconosciuto"),
                [("Sì", "sperimentale"), ("No", None), ("Crea rapporto", "crea_rapporto")], "domanda")
            if scelta == "sperimentale":
                self._traduci(forza=True, traduci_comunque=x.get("traduci_comunque", False))
            else:
                self._azione(scelta, e)
            return
        self.stato.set("%s: %s" % (descrizione, esiti.STATO[e.codice]))
        if self._operazione == "traduci" and e.scritto and e.dal_vivo is not None and e.motore in ("unity", "web"):
            self._chiedi_dal_vivo(e)
        self._mostra_finestra(e, self._operazione)

    # --- traduzione mentre si gioca -------------------------------------------------------------------------------

    def _chiedi_dal_vivo(self, e):
        """Alla fine di "Analizza e traduci": tenere acceso il traduttore mentre si gioca? La scelta resta nel profilo
        del gioco (automatico.imposta_dal_vivo) e vale fino alla prossima "Analizza e traduci"."""
        cartella = self.cartella.get().strip().strip('"')
        try:
            _profilo, attiva = automatico.traduzione_dal_vivo(cartella)
        except Exception:  # noqa: BLE001 - solo una domanda in piu'
            attiva = None
        if attiva is None:
            return
        scelta = tema.scegli(
            self.radice, self.colori, "Traduzione mentre giochi",
            "Le frasi trovate adesso sono già tradotte. Mentre giochi, PatchSmith può tenere acceso il suo traduttore "
            "per tradurre anche le frasi nuove che compaiono (la prima volta in inglese per un attimo).\n\n"
            "Si avvia con il gioco e si chiude da solo quando chiudi il gioco. Usa la scheda video (circa 3 GB di "
            "memoria) solo per il tempo in cui giochi. Mentre è acceso, qui sotto vedi ogni tanto cosa ha tradotto e "
            "puoi fermarlo con un pulsante.\n\nLo tengo acceso?",
            [("Sì, tienilo acceso", True), ("No, solo le traduzioni salvate", False)], "domanda")
        if scelta is None:  # finestra chiusa: resta com'era
            return
        if scelta != attiva:
            try:
                automatico.imposta_dal_vivo(cartella, scelta, self._log)
            except Exception as ex:  # noqa: BLE001 - la traduzione e' gia' nel gioco: si dice e si va avanti
                self._log("! Non ho potuto %s la traduzione mentre giochi (%s): riprova con 'Analizza e traduci'."
                          % ("accendere" if scelta else "spegnere", ex))
                return
        if not scelta:
            e.dal_vivo = "nessuno"

    def _sorveglia_dal_vivo(self):
        """Ogni 5 secondi: se il gioco della cartella scelta e' aperto con il suo traduttore acceso, il pulsante per
        fermarlo e, nel registro, le frasi nuove tradotte dall'ultima volta."""
        try:
            self._controlla_dal_vivo()
        except Exception as e:  # noqa: BLE001 - solo un aiuto: non deve mai disturbare la finestra
            self._scrivi_registro("(sorveglianza del traduttore del gioco: %s: %s)" % (type(e).__name__, e))
        finally:
            self.radice.after(5000, self._sorveglia_dal_vivo)

    def _controlla_dal_vivo(self):
        cartella = self.cartella.get().strip().strip('"')
        profilo, attiva = automatico.traduzione_dal_vivo(cartella) if cartella and os.path.isdir(cartella) else \
            (None, None)
        nome = profilo.get("nome") if profilo and attiva else None
        if nome and nome not in self._diario_letto:  # la storia di prima non si ripete: solo le frasi da adesso
            try:
                self._diario_letto[nome] = os.path.getsize(servizio_xunity.percorso_diario(nome))
            except OSError:
                self._diario_letto[nome] = 0
        acceso = bool(nome) and servizio_xunity.acceso(nome, attesa=0.2)
        if acceso:
            if not self.btn_ferma_vivo.winfo_manager():
                self.btn_ferma_vivo.pack(side="left", padx=(0, 10), before=self.etichetta_stato)
            if self._vivo_acceso != nome:
                self._log("Il gioco è aperto con il traduttore di PatchSmith acceso: qui vedi ogni tanto le frasi "
                          "nuove che traduce.")
        else:
            self.btn_ferma_vivo.pack_forget()
        if nome:
            self._riassumi_diario(nome)
        if self._vivo_acceso and not acceso:
            self._log("Il traduttore del gioco si è chiuso (gioco chiuso o fermato).")
        self._vivo_acceso = nome if acceso else None

    def _riassumi_diario(self, nome):
        """Le frasi nuove del diario del traduttore dall'ultima lettura, in una riga del registro."""
        percorso = servizio_xunity.percorso_diario(nome)
        try:
            dimensione = os.path.getsize(percorso)
        except OSError:
            return
        letto = self._diario_letto.get(nome, 0)
        if dimensione < letto:  # diario ricominciato (troppo grande): da capo
            letto = 0
        if dimensione == letto:
            return
        with open(percorso, "rb") as f:
            f.seek(letto)
            dati = f.read(dimensione - letto)
        fine = dati.rfind(b"\n") + 1  # solo righe intere: il resto alla prossima lettura
        self._diario_letto[nome] = letto + fine
        nuove, rimaste = [], 0
        for riga in dati[:fine].decode("utf-8", "replace").splitlines():
            try:
                r = json.loads(riga)
            except ValueError:
                continue
            if r.get("esito") == "ok" and r.get("backend") not in ("memoria", "comune", "gia' tradotta", None):
                nuove.append((r.get("testo", ""), r.get("risposta", "")))
            elif r.get("esito") in ("rifiutata", "errore"):
                rimaste += 1
        if not nuove and not rimaste:
            return
        testo = "Mentre giochi: %s" % _quanti(len(nuove), "frase nuova tradotta", "frasi nuove tradotte")
        if nuove:
            esempio = nuove[-1]
            testo += " (es. \"%s\" → \"%s\")" % (" ".join(esempio[0].split())[:50], " ".join(esempio[1].split())[:50])
        if rimaste:
            testo += ", %s in originale" % _quanti(rimaste, "rimasta", "rimaste")
        self._log(testo)

    def _ferma_dal_vivo(self):
        cartella = self.cartella.get().strip().strip('"')
        profilo, _attiva = automatico.traduzione_dal_vivo(cartella)
        if profilo and servizio_xunity.ferma(profilo.get("nome", "")):
            self._log("Traduttore del gioco fermato: le frasi nuove restano in originale fino al prossimo avvio del "
                      "gioco (scheda video libera).")
        self.btn_ferma_vivo.pack_forget()
        self._vivo_acceso = None

    def _azione(self, azione, e):
        """Il pulsante scelto nella finestra di fine operazione."""
        if e is not None and e.extra.get("cartella") and azione in ("ripristina", "riapplica", "traduci",
                                                                     "traduci_comunque", "usa_locale",
                                                                     "crea_rapporto", "correggi"):
            # dal controllo dopo aver giocato di un altro gioco (all'apertura): l'azione (anche il rapporto) e' per
            # quel gioco
            self.cartella.set(os.path.normpath(e.extra["cartella"]))
        if azione == "mostra_rimasti":
            scelta = tema.scegli(self.radice, self.colori, "Testi rimasti in originale", esiti.testo_rimasti(e),
                                 [("OK", None), ("Correggi traduzioni", "correggi"),
                                  ("Crea rapporto", "crea_rapporto")])
            self._azione(scelta, e)
        elif azione == "correggi":  # la finestra con l'elenco giusto (rimasti in originale, non scritti nel gioco)
            self._correggi(esiti.filtro_correzioni(e))
        elif azione == "crea_rapporto":
            self._crea_rapporto(e)
        elif azione == "riapplica":
            self._riapplica()
        elif azione == "chiudi_gioco" and e is not None and "controllo" in e.extra and e.extra.get("profilo"):
            self._controlla([e.extra["profilo"]], all_apertura=False)  # di nuovo il controllo di quel gioco
        elif azione == "chiudi_gioco" and self._ripeti:
            self._ripeti()
        elif azione in ("usa_locale", "installa_locale"):
            self.traduttore.set(TRADUTTORI["locale"])
            self._cambia_traduttore()
            if azione == "installa_locale" or not self._locale_pronto():
                self._installa_locale()
            else:  # di nuovo Analizza e traduci con le stesse scelte (es. "Traduci comunque"), anche dopo un errore
                self._traduci(**self._scelte(e))
        elif azione == "traduci_comunque":
            self._traduci(forza=self._scelte(e)["forza"], traduci_comunque=True)
        elif azione == "ripristina":  # chiede conferma prima di rimettere gli originali
            self._ripristina()
        elif azione == "traduci":
            self._traduci()
        elif azione == "ritraduci" and e is not None and e.extra.get("profilo"):
            self._controlla([e.extra["profilo"]], all_apertura=False, ritraduci=True)

    def _scelte(self, e):
        """forza e traduci_comunque dell'ultimo Analizza e traduci (dall'esito, o ricordate: un esito nato da un
        errore non le ha); da_zero se l'esito e' di un "Ricomincia da zero" che non e' partito (traduttore non
        pronto: con quello nuovo si ricomincia da zero, non si traduce soltanto)."""
        scelte = dict(self._scelte_traduci)
        scelte.update({k: e.extra[k] for k in scelte if k in e.extra})
        if e.extra.get("da_zero"):
            scelte["da_zero"] = True
        return scelte

    # --- azioni -------------------------------------------------------------------------------

    def _sfoglia(self):
        scelta = filedialog.askdirectory(title="Cartella del gioco", initialdir=self.cartella.get() or None)
        if scelta:
            self.cartella.set(os.path.normpath(scelta))

    def _preliminari(self, operazione, ripeti, backend=None):
        """Prima di un'operazione lunga, i controlli veloci di automatico.controlli_preliminari (gioco aperto, lingue,
        cartella protetta, spazio, traduttore in pausa o da installare, stesso gioco gia' tradotto altrove), con una
        finestra per ogni problema trovato. Restituisce le scelte per partire ({'riusa': profilo o None}), oppure None
        se non si parte: annullato, o un'altra strada scelta ("Riprova" e "Usa il traduttore locale" rifanno 'ripeti',
        quindi di nuovo i controlli; "Installa" installa il traduttore locale e poi rifa' 'ripeti')."""
        cartella = self.cartella.get().strip().strip('"')
        if self._occupato():
            return None
        if not cartella or not os.path.isdir(cartella):
            return {"riusa": None}  # "Cartella mancante" lo dice _avvia
        try:
            trovati = automatico.controlli_preliminari(cartella, backend, operazione, self._codice_lingua(self.da.get()),
                                                       self._lingua_a())
        except Exception as e:  # noqa: BLE001 - un controllo che non riesce non deve impedire di lavorare
            self._log("  ! Controlli prima di partire non riusciti (%s: %s): parto lo stesso." % (type(e).__name__, e))
            return {"riusa": None}
        scelte = {"riusa": None}
        for p in trovati:
            titolo, tipo = FINESTRE_PRELIMINARI.get(p.codice, ("Prima di partire", "avviso"))
            pulsanti = {
                "riprova": [("Riprova", "riprova"), ("Annulla", None)],
                "continua": [("Annulla", None), ("Continua comunque", "continua")],
                "usa_locale": [("Usa il traduttore locale", "usa_locale"), ("Continua comunque", "continua"),
                               ("Annulla", None)],
                # (continuare senza: la lingua ufficiale non serve tradurla, e senza traduttore Analizza e traduci si
                # ferma prima di modificare il gioco)
                "installa_locale": [("Installa il traduttore locale" if p.codice != "LOCALE_ASSENTE" else "Installa ora",
                                     "installa_locale"), ("Continua comunque", "continua"), ("Annulla", None)],
                "riusa": [("Sì, riusale", "riusa"), ("No, traduci da capo", "da_capo"), ("Annulla", None)],
            }.get(p.azione, [("OK", None)])
            self.stato.set("%s: %s" % (titolo, p.messaggio.split("\n")[0][:120]))
            scelta = tema.scegli(self.radice, self.colori, titolo, p.messaggio, pulsanti, tipo)
            if scelta == "riprova":
                self.radice.after(50, ripeti)
                return None
            if scelta in ("usa_locale", "installa_locale"):
                self.traduttore.set(TRADUTTORI["locale"])
                self._cambia_traduttore()
                if scelta == "usa_locale" and self._locale_pronto():
                    self.radice.after(50, ripeti)
                else:
                    self._installa_locale(poi=ripeti)
                return None
            if scelta == "riusa":
                scelte["riusa"] = p.dati.get("profilo")
            elif scelta not in ("continua", "da_capo"):  # Annulla, OK di un problema che ferma, finestra chiusa
                self.stato.set("%s: non avviato." % titolo)
                return None
        return scelte

    def _traduci(self, forza=False, traduci_comunque=False, consenso=False, da_zero=False, rigenera=False):
        """Analizza e traduci. da_zero ("Ricomincia da zero" in Correggi traduzioni): prima le traduzioni salvate del
        gioco vanno da parte in una copia (automatico.ricomincia_da_zero) e il profilo si rifa' (rigenera). Messe da
        parte quelle, "Riprova" (es. il gioco aperto a meta' lavoro) continua la traduzione nuova con rigenera: non
        mette da parte di nuovo quella appena fatta."""
        from .backend import ErroreBackend
        da, a, backend = self._codice_lingua(self.da.get()), self._lingua_a(), self._backend()
        ripeti = lambda: self._traduci(forza=forza, traduci_comunque=traduci_comunque, consenso=consenso,  # noqa: E731
                                       da_zero=da_zero, rigenera=rigenera)
        # (il si' alla lingua ufficiale del gioco non traduce nulla: niente controlli del traduttore)
        scelte = self._preliminari("traduci", ripeti, None if consenso else backend)
        if scelte is None:
            return
        riusa = scelte.get("riusa")

        def _lavoro(cartella):
            """L'esito di Analizza e traduci (esito.Esito), con le scelte fatte per poterle ripetere."""
            if da_zero:  # (con un traduttore che non c'e' o in pausa si ferma prima di toccare qualcosa)
                try:
                    automatico.ricomincia_da_zero(cartella, self._log, prova=backend == "finto", backend=backend)
                except ErroreBackend as errore:
                    errore.da_zero = True  # niente toccato: "Usa il traduttore locale" ricomincia da zero (_scelte)
                    raise
                self._ripeti = lambda: self._traduci(forza=forza, traduci_comunque=traduci_comunque,
                                                     consenso=consenso, rigenera=True)
            percorso, completato, esito = automatico.analizza_e_traduci(
                cartella, da, a, backend, self._log, forza=forza, rigenera=da_zero or rigenera,
                traduci_comunque=traduci_comunque, consenso=consenso, riusa=riusa)
            e = esito.get("esito") or Esito("OK" if completato else "PARZIALE", scritto=bool(percorso))
            e.extra.update(forza=forza, traduci_comunque=traduci_comunque)
            return e

        self._ripeti = ripeti
        self._scelte_traduci = {"forza": forza, "traduci_comunque": traduci_comunque}
        self._avvia("Analisi e traduzione", _lavoro, fermabile=True, operazione="traduci")

    def _riapplica(self):
        prova = self._backend() == "finto"
        if self._preliminari("riapplica", self._riapplica) is None:
            return

        def _lavoro(cartella):
            esito = {}
            fatto = automatico.riapplica(cartella, self._log, prova=prova, esito=esito)
            e = esito.get("esito") or Esito("OK" if fatto else "PARZIALE")
            if esito.get("lingua_aggiunta"):
                e.extra["lingua_aggiunta"] = esito["lingua_aggiunta"]
            return e

        self._ripeti = self._riapplica
        self._avvia("Riapplicazione", _lavoro, operazione="riapplica")

    def _ripristina(self, chiedi=True):
        if chiedi and not tema.chiedi(self.radice, self.colori, "Ripristina", "Rimettere i file originali del "
                                      "gioco?\nLe traduzioni restano salvate: \"Analizza e traduci\" le rimette subito."):
            return
        if self._preliminari("ripristina", lambda: self._ripristina(chiedi=False)) is None:
            return

        def _lavoro(cartella):
            esito = {}
            fatto = automatico.ripristina(cartella, self._log, esito=esito)
            return esito.get("esito") or Esito("OK" if fatto else "PARZIALE")

        self._ripeti = lambda: self._ripristina(chiedi=False)
        self._avvia("Ripristino", _lavoro, operazione="ripristina")

    def _crea_rapporto(self, e=None):
        """Zip sul Desktop con registri, profilo, impostazioni senza chiave ne' email e file utili del gioco. Se sul
        Desktop non si puo' scrivere (es. "Accesso controllato alle cartelle" di Windows), nella cartella dei
        registri. e: l'esito della finestra da cui e' stato chiesto (predefinito: l'ultimo; dai "Dettagli" del
        controllo dopo aver giocato e' quello del gioco scelto, non dell'ultimo controllato)."""
        if self._occupato():
            return
        ultimo, scelto = e if e is not None else self.ultimo_esito, self._scelto

        def _lavoro(cartella):
            extra = ["Traduttore scelto: %s" % scelto]
            try:
                return rapporto.crea(cartella, rapporto.cartella_desktop(), esito=ultimo, extra=extra)
            except OSError as e:
                self._log("  ! Non riesco a scrivere il rapporto sul Desktop (%s): lo metto nella cartella dei "
                          "registri di PatchSmith." % e)
                return rapporto.crea(cartella, cartella_registri(), esito=ultimo, extra=extra)

        self._avvia("Creazione del rapporto", _lavoro, serve_cartella=False, operazione="rapporto", registra=False)

    # --- controllo dopo aver giocato -----------------------------------------------------------

    def _controllo_iniziale(self):
        """All'apertura: si cercano (in background, solo date di file) i giochi con segnali nuovi dopo l'ultima
        partita; se ce ne sono, il controllo parte da solo (vedi _avvia_controllo_iniziale)."""
        def cerca():
            try:
                trovati = controllo.da_controllare()
            except Exception as e:  # noqa: BLE001 - non deve mai impedire di usare PatchSmith
                self._log("  ! Controllo dopo l'ultima partita non riuscito (%s: %s)" % (type(e).__name__, e))
                trovati = []
            self.coda.put(("controllo", trovati))

        threading.Thread(target=cerca, daemon=True).start()

    def _avvia_controllo_iniziale(self, trovati, finestra_aperta=False):
        aperta = bool(trovati) and self.lavoro is None and bool(str(self.radice.tk.call("grab", "current")))
        if aperta or (trovati and self.lavoro is None and finestra_aperta):
            # una finestra aperta (es. i controlli prima di "Analizza e traduci" o "Installare il traduttore
            # locale?"): si aspetta che si chiuda, e ancora un attimo (la scelta fatta li', es. "Riprova", parte
            # subito dopo), altrimenti l'operazione scelta non partirebbe (un lavoro alla volta)
            self.radice.after(300, lambda: self._avvia_controllo_iniziale(trovati, finestra_aperta=aperta))
            return
        if not trovati:
            self._mostra_ultimo_controllo()
            return
        if self.lavoro is not None:  # nel frattempo hai gia' avviato un'operazione: il controllo parte dopo
            if not self.stato_controllo.get().startswith("Ci sono novità"):  # (una riga sola nel registro)
                self._log("Ci sono novità dopo l'ultima partita (%s): le controllo appena finisce l'operazione in "
                          "corso." % _quanti(len(trovati), "gioco", "giochi"))
            self.stato_controllo.set("Ci sono novità dopo l'ultima partita (%s): le controllo appena finisce "
                                     "l'operazione in corso." % _quanti(len(trovati), "gioco", "giochi"))
            if getattr(self, "_attesa_controllo", None):
                self.radice.after_cancel(self._attesa_controllo)
            self._attesa_controllo = self.radice.after(2000, lambda: self._avvia_controllo_iniziale(trovati))
            return
        self._attesa_controllo = None
        self._controlla(trovati)

    def _mostra_ultimo_controllo(self):
        """Senza novita': se all'ultimo controllo del gioco scelto era rimasto qualcosa da fare, lo ricorda."""
        cartella = self.cartella.get().strip().strip('"')
        try:
            percorso = automatico.trova_profilo(cartella) if cartella and os.path.isdir(cartella) else None
            e = controllo.esito_salvato(percorso) if percorso else None
        except Exception:  # noqa: BLE001 - solo un promemoria
            e = None
        if e is not None and e.extra.get("da_fare"):
            self.esiti_controllo = [e]
            self._aggiorna_riga_controllo(ultimo=True)

    def _controlla(self, profili=None, all_apertura=None, ritraduci=None):
        """Controllo dopo aver giocato: il gioco della cartella scelta (profili=None) o i giochi dati. all_apertura
        (predefinito: con i giochi dati) = il controllo partito da solo all'apertura: senza finestre alla fine, il
        risultato va nella riga sotto la cartella. ritraduci (predefinito: non all'apertura): ritradurre adesso le
        frasi tradotte mentre si giocava rimaste provvisorie; all'apertura no (caricare il traduttore locale o
        aspettare le pause di Google terrebbe la finestra ferma): restano da fare, con il pulsante "Ritraduci"."""
        traduttore = self._backend()
        all_apertura = profili is not None if all_apertura is None else all_apertura
        ritraduci = not all_apertura if ritraduci is None else ritraduci

        def _lavoro(cartella):
            if profili is None:
                percorso = automatico.trova_profilo(cartella)
                if not percorso:
                    self._log("Per questa cartella non c'e' nessuna traduzione di PatchSmith da controllare.")
                    return [Esito("NULLA_DA_FARE", "Per questa cartella non c'è nessuna traduzione di PatchSmith da "
                                  "controllare: usa prima \"Analizza e traduci\".")]
                elenco = [percorso]
            else:
                elenco = profili
            risultati = []
            for percorso in elenco:
                interruzione.controlla()
                try:
                    problemi = controllo.controlla(percorso, applica=True, log=self._log, traduttore=traduttore,
                                                   ritraduci=ritraduci)
                except interruzione.Interrotto:
                    raise
                except Exception as e:  # noqa: BLE001 - un gioco che non si controlla non ferma gli altri
                    self._scrivi_registro("ERRORE (%s: %s)\n%s" % (type(e).__name__, e, traceback.format_exc()))
                    problemi = [controllo.problema_errore(e)]
                risultati.append(controllo.come_esito(problemi, percorso))
            return risultati

        if self._occupato():
            return
        self._controllo_automatico = all_apertura
        self._ripeti = lambda: self._controlla(profili, all_apertura=False)  # "Riprova": con la finestra alla fine
        self._avvia("Controllo dopo aver giocato", _lavoro, fermabile=True, serve_cartella=profili is None,
                    operazione="controlla", nome_registro="controllo" if profili is not None else None)

    def _aggiorna_riga_controllo(self, ultimo=False):
        """La riga sotto la cartella: 'Dopo l'ultima partita a X: 2 problemi corretti, 1 da fare' e 'Dettagli'."""
        con_problemi = [e for e in self.esiti_controllo if e.extra.get("controllo")]
        self.btn_dettagli.pack_forget()
        self.btn_ritraduci.pack_forget()
        if not self.esiti_controllo or all(e.codice == "NULLA_DA_FARE" for e in self.esiti_controllo):
            self.stato_controllo.set("")
            self._mostra_riga_controllo()
            return
        corretti = sum(e.extra.get("corretti", 0) for e in self.esiti_controllo)
        da_fare = sum(e.extra.get("da_fare", 0) for e in self.esiti_controllo)
        if len(self.esiti_controllo) == 1:
            testo = "%s %s: " % ("All'ultimo controllo di" if ultimo else "Dopo l'ultima partita a",
                                 self.esiti_controllo[0].extra.get("gioco", "il gioco"))
        else:
            testo = "Dopo l'ultima partita (%s): " % _quanti(len(self.esiti_controllo), "gioco", "giochi")
        if con_problemi:
            self.btn_dettagli.pack(side="left")
        if corretti or da_fare:
            testo += "%s, %s" % (_quanti(corretti, "problema corretto", "problemi corretti"),
                                 _quanti(da_fare, "da fare", "da fare"))
        else:
            testo += "nessun problema"
        if any(e.codice == "GIOCO_APERTO" for e in self.esiti_controllo):
            testo += " (alcune correzioni appena il gioco è chiuso)"
        self.stato_controllo.set(testo)
        profili, frasi = self._da_ritradurre()
        if profili:
            self.btn_ritraduci.configure(text="Ritraduci %s" % _quanti(frasi, "frase", "frasi") if frasi else
                                         "Ritraduci le frasi")
            self.btn_ritraduci.pack(side="left", padx=(6, 0) if con_problemi else 0)
        self._mostra_riga_controllo()

    def _mostra_riga_controllo(self):
        """I pulsanti del controllo (in fondo, a sinistra) solo se ce n'e' uno da usare."""
        if self.btn_dettagli.winfo_manager() or self.btn_ritraduci.winfo_manager():
            if not self.riga_controllo.winfo_manager():
                self.riga_controllo.pack(side="left", padx=(0, 10), before=self.etichetta_stato)
        else:
            self.riga_controllo.pack_forget()

    def _da_ritradurre(self):
        """([profili], quante frasi) con frasi tradotte mentre si giocava da ritradurre (lasciate dal controllo
        all'apertura: problemi con l'azione 'ritraduci' ancora da fare)."""
        profili, frasi = [], 0
        for e in self.esiti_controllo:
            voci = [p for p in e.extra.get("controllo") or [] if p.get("azione") == "ritraduci"
                    and esiti.STATI_GRUPPO.get(p.get("stato")) == "da_fare"]
            if voci and e.extra.get("profilo"):
                profili.append(e.extra["profilo"])
                frasi += sum(int(p.get("frasi") or 0) for p in voci)
        return list(dict.fromkeys(profili)), frasi

    def _ritraduci(self):
        """Il pulsante "Ritraduci N frasi": il controllo dei giochi con frasi da ritradurre, questa volta con la
        ritraduzione (e le finestre alla fine)."""
        profili, _ = self._da_ritradurre()
        if profili:
            self._controlla(profili, all_apertura=False, ritraduci=True)

    def _dettagli_controllo(self):
        self._mostra_controllo(tutti=True)

    def _mostra_controllo(self, tutti=False):
        """Una finestra per gioco con i problemi (corretti, da fare, da sapere) e i pulsanti per quelli da fare; alla
        fine del controllo dal pulsante anche senza problemi. Un pulsante scelto chiude le altre finestre."""
        esiti_da_mostrare = [e for e in self.esiti_controllo if e.extra.get("controllo")] if tutti else \
            list(self.esiti_controllo)
        for e in esiti_da_mostrare:
            dati = esiti.finestra(e, "controlla", locale_pronto=self._locale_pronto(), attuale=self._backend())
            scelta = tema.scegli(self.radice, self.colori, dati["titolo"], dati["testo"], dati["pulsanti"],
                                 dati["tipo"])
            if scelta:
                self._azione(scelta, e)
                break

    def _controlla_codice(self):
        """PatchSmith aggiornato mentre la finestra era aperta: si avvisa (una volta) di chiuderla e riaprirla."""
        if self._avvisato_codice:
            return
        attuale = impronta_codice()
        if attuale != self._codice or (attuale[0] and attuale[0] != __version__):
            self._avvisato_codice = True
            tema.avvisa(self.radice, self.colori, "PatchSmith è stato aggiornato",
                        "PatchSmith è stato aggiornato mentre questa finestra era aperta: chiudi PatchSmith e riaprilo, "
                        "altrimenti continui a usare la versione di prima.")

    # --- correggi traduzioni (gui_correzioni.py) ------------------------------------------------------------

    def _correggi(self, filtro=None):
        """"Correggi traduzioni…": la finestra con le traduzioni del gioco della cartella scelta e i loro problemi;
        filtro: l'elenco da mostrare subito (vedi correzioni.FILTRI). Restituisce la finestra (o None)."""
        from . import correzioni, gui_correzioni
        if self._occupato():
            return None
        cartella = self.cartella.get().strip().strip('"')
        try:
            percorso = automatico.trova_profilo(cartella) if cartella and os.path.isdir(cartella) else None
        except Exception:  # noqa: BLE001 - un profilo rovinato: come senza traduzioni (lo dice Analizza e traduci)
            percorso = None
        if not percorso:
            tema.informa(self.radice, self.colori, "Nessuna traduzione", "Per questa cartella non ci sono ancora "
                         "traduzioni: usa prima \"Analizza e traduci\".")
            return None
        try:
            elenco = correzioni.carica(percorso, prova=self._backend() == "finto", log=self._log)
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            motivo = ("Il file delle traduzioni di questo gioco è rovinato (forse è stato modificato a mano con un "
                      "errore) e non si legge." if isinstance(e, RuntimeError) and "JSON" in str(e) else
                      "Non riesco a leggere le traduzioni di questo gioco (%s)." % e)
            # il dettaglio va nel registro dell'interfaccia (fuori da un'operazione non ce n'e' uno aperto) e
            # nell'esito: "Crea rapporto" qui sotto deve parlare di questo, non dell'ultima operazione
            aperto = self._registro is not None
            if not aperto:
                self._apri_registro("", "Correggi traduzioni", "interfaccia")
            self._scrivi_registro("Correggi traduzioni non aperta (%s: %s)" % (type(e).__name__, e))
            if not aperto:
                self._chiudi_registro()
            self.ultimo_esito = Esito("ERRORE", motivo, azioni=["crea_rapporto"])
            scelta = tema.scegli(self.radice, self.colori, "Traduzioni non leggibili", motivo + "\n\nPuoi aprire il "
                                 "file e correggerlo a mano (avanzato), oppure creare un rapporto da inviare a chi ti "
                                 "aiuta con PatchSmith.", [("OK", None), ("Apri il file (avanzato)", "apri"),
                                                            ("Crea rapporto", "crea_rapporto")], "errore")
            if scelta == "apri":
                self._apri_file_traduzioni()
            elif scelta:
                self._azione(scelta, None)
            return None
        return gui_correzioni.Finestra(self, elenco, filtro)

    def _apri_file_traduzioni(self):
        """"Apri il file (avanzato)": il file delle traduzioni nel programma predefinito (o nel Blocco note)."""
        cartella = self.cartella.get().strip().strip('"')
        try:
            percorso = automatico.file_memoria(cartella, self._lingua_a()) if cartella else None
        except Exception:  # noqa: BLE001 - come senza file
            percorso = None
        if not percorso or not os.path.exists(percorso):
            tema.informa(self.radice, self.colori, "Nessuna traduzione", "Per questa cartella non ci sono ancora "
                         "traduzioni.")
            return
        tema.informa(
            self.radice, self.colori, "Apri il file delle traduzioni",
            "Si apre il file delle traduzioni: a sinistra il testo originale, a destra la traduzione.\n"
            "Modifica solo il testo a destra, salva il file e poi premi \"Analizza e traduci\".\n\n"
            "Di solito è più comodo \"Correggi traduzioni…\", che controlla anche i segnaposto e le lunghezze.")
        try:
            _apri(percorso)
        except OSError as e:
            tema.avvisa(self.radice, self.colori, "File non aperto", "Non riesco ad aprire il file (%s). Lo trovi "
                        "qui:\n%s" % (e.strerror or e, percorso))
            mostra_file(percorso)

    def _dopo_correzioni(self, quante):
        """Correzioni salvate nella finestra "Correggi traduzioni": si scrivono nel gioco da sole, con "Riapplica"
        (prima i controlli: se il gioco e' aperto, "Riprova" quando l'hai chiuso)."""
        self._log("Correggi traduzioni: %s salvate, le scrivo nel gioco." % _quanti(quante, "correzione",
                                                                                    "correzioni"))
        self.stato.set("Correzioni salvate: le scrivo nel gioco…")
        self._riapplica()

    def _ritraduci_correzioni(self, testi=None, modello=None):
        """Da "Correggi traduzioni": ritraduce i testi scelti (testi=None: "Ritraduci tutto") con il traduttore scelto
        o con il traduttore locale e il modello dato, poi li scrive nel gioco (automatico.ritraduci)."""
        backend = self._backend()
        if modello and backend != "finto":  # un modello scelto e' quello del traduttore locale
            backend = "locale"
        prova = backend == "finto"
        if self._preliminari("riapplica", lambda: self._ritraduci_correzioni(testi, modello)) is None:
            return

        def _lavoro(cartella):
            esito = {}
            fatto = automatico.ritraduci(cartella, testi, backend, self._log, prova=prova, esito=esito, modello=modello)
            return esito.get("esito") or Esito("OK" if fatto else "PARZIALE")

        self._ripeti = self._riapplica  # (le traduzioni nuove sono gia' salvate: "Riprova" le scrive nel gioco)
        self._avvia("Ritraduzione", _lavoro, fermabile=True, operazione="ritraduci")

    def _installa_12b_e_ritraduci(self):
        """"Ritraduci tutto con Qualità (12B)" senza il modello: si sceglie il modello Qualita' del traduttore locale,
        si installa (con la solita domanda) e poi si ritraduce da solo."""
        self.traduttore.set(TRADUTTORI["locale"])
        self._cambia_traduttore()
        self.modello_locale.set(MODELLI_LOCALI["12b"])
        self._scegli_modello()
        self._installa_locale(poi=lambda: self._ritraduci_correzioni(None, "12b"))


_TRADUTTORI_IT, _MODELLI_IT = dict(TRADUTTORI), dict(MODELLI_LOCALI)


def _traduci_costanti():
    """I nomi dei traduttori e dei modelli nella lingua dell'interfaccia (i menu a tendina li confrontano)."""
    global TRADUTTORI, MODELLI_LOCALI
    TRADUTTORI = {k: interfaccia.traduci(v) for k, v in _TRADUTTORI_IT.items()}
    MODELLI_LOCALI = {k: interfaccia.traduci(v) for k, v in _MODELLI_IT.items()}


def main():
    progetto.migra_dati()  # i dati con i nomi italiani di prima (profili, memoria...) passano a quelli inglesi
    if sys.platform.startswith("win"):
        try:  # sulla barra delle applicazioni PatchSmith con la sua icona, non raggruppato sotto Python
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PatchSmith.PatchSmith")
        except (AttributeError, OSError):
            pass
        # PatchSmith.exe con l'icona accanto a PatchSmith.pyw (se manca o e' vecchio: compilarlo richiede un secondo)
        threading.Thread(target=icona.eseguibile, daemon=True).start()
    # la musica pronta in anticipo, anche se e' spenta: il pulsante con la nota la fa partire subito
    threading.Thread(target=musica.prepara, daemon=True).start()
    radice = tk.Tk()
    App(radice)  # il tema (chiaro/scuro come Windows) viene applicato dall'App
    radice.mainloop()
