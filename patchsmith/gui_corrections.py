"""La finestra "Correggi traduzioni": le traduzioni del gioco (correzioni.carica) con la ricerca e gli elenchi dei
problemi (rimasti in originale, troppo lunghi, non scritti nel gioco, numeri e segnaposto, un'altra lingua, sospetti,
corretti a mano), e sotto la traduzione della riga scelta da correggere, con la lunghezza e i controlli mentre scrivi.

"Salva e applica al gioco" salva le correzioni in memoria (correzioni.salva: per Unity anche le righe da cambiare
nella cache di XUnity) e chiude la finestra; poi PatchSmith le scrive nel gioco da solo con "Riapplica" (con i
controlli prima di partire: a gioco chiuso). "Ritraduci selezionati…", "Ritraduci tutto con Qualità (12B)…" e
"Ricomincia da zero…" diventano operazioni della finestra principale (con "Ferma" e la finestra di fine).

Tastiera: Ctrl+F cerca; Invio (o F2) dall'elenco porta alla traduzione; Ctrl+Invio conferma e passa alla riga dopo;
Esc annulla la modifica della riga (dall'elenco chiude la finestra); Ctrl+S salva; Alt+freccia destra/sinistra (o
Ctrl+PagGiù/PagSu) cambiano pagina. L'elenco mostra PAGINA righe alla volta: anche i giochi con decine di migliaia di
testi restano veloci.
"""
import tkinter as tk
from tkinter import ttk

from . import corrections as correzioni, ui_language as interfaccia, theme as tema

PAGINA = 300  # righe dell'elenco mostrate insieme
LARGHEZZA_CELLA = 160  # caratteri mostrati in una cella (il testo intero e' sotto, nella riga scelta)
COLONNE = (("originale", "Originale", 280), ("traduzione", "Traduzione", 280), ("limite", "Limite", 70),
           ("problema", "Problema", 230))


def _in_cella(testo):
    t = " ⏎ ".join((testo or "").split("\n"))
    return t if len(t) <= LARGHEZZA_CELLA else t[:LARGHEZZA_CELLA - 1] + "…"


def _numero(n):
    return "{:,}".format(n).replace(",", ".")


def _quante(n, una, molte):
    """'1 riga', '2 righe'."""
    return "%d %s" % (n, una if n == 1 else molte)


class Finestra:
    """La finestra (modale) sopra quella principale. app: gui.App; elenco: correzioni.Elenco gia' caricato."""

    def __init__(self, app, elenco, filtro=None):
        self.app = app
        self.c = app.colori
        self.elenco = elenco
        self.modifiche = {}  # {testo: traduzione scritta qui}, finche' non si salva
        self.indice = {x.testo: i for i, x in enumerate(elenco.voci)}
        self.mostrate = []
        self.pagina = 0
        self.attuale = None  # la riga nella traduzione qui sotto
        self.chiusa = False
        self._ricerca_in_attesa = None
        self.chiavi_filtri = [k for k, _ in correzioni.FILTRI]

        top = self.top = tk.Toplevel(app.radice)
        top.withdraw()
        top.title(self._titolo())
        top.configure(background=self.c["sfondo"])
        top.transient(app.radice)
        top.minsize(780, 560)
        cornice = ttk.Frame(top, padding=10)
        cornice.pack(fill="both", expand=True)
        cornice.columnconfigure(0, weight=1)
        cornice.rowconfigure(2, weight=1)

        barra = ttk.Frame(cornice)
        barra.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        ttk.Label(barra, text="Cerca:").pack(side="left")
        self.cerca = tk.StringVar()
        self.voce_cerca = ttk.Entry(barra, textvariable=self.cerca, width=34)
        self.voce_cerca.pack(side="left", padx=(4, 14))
        self.cerca.trace_add("write", lambda *_a: self._cerca_dopo())
        ttk.Label(barra, text="Mostra:").pack(side="left")
        self.scelta_filtro = ttk.Combobox(barra, state="readonly", width=34, values=self._etichette_filtri())
        self.scelta_filtro.pack(side="left", padx=(4, 0))
        self.scelta_filtro.current(self.chiavi_filtri.index(filtro) if filtro in self.chiavi_filtri else 0)
        self.scelta_filtro.bind("<<ComboboxSelected>>", lambda _e: self.aggiorna_elenco())

        elenco_f = ttk.Frame(cornice)
        elenco_f.grid(row=2, column=0, sticky="nsew")
        elenco_f.columnconfigure(0, weight=1)
        elenco_f.rowconfigure(0, weight=1)
        self.albero = ttk.Treeview(elenco_f, columns=[k for k, _, _ in COLONNE], show="headings",
                                   selectmode="extended", height=14)
        for chiave, titolo, larghezza in COLONNE:
            self.albero.heading(chiave, text=titolo, anchor="w")
            self.albero.column(chiave, width=larghezza, minwidth=50, stretch=chiave != "limite",
                               anchor="center" if chiave == "limite" else "w")
        # "Limite" (riga piu' lunga/spazio nel gioco) solo se il gioco fa conoscere lo spazio dei suoi testi (GameMaker
        # con gli a capo misurati): negli altri giochi sarebbe vuota in ogni riga
        if not any(x.spazio for x in self.elenco.voci):
            self.albero.configure(displaycolumns=[k for k, _, _ in COLONNE if k != "limite"])
        self.albero.tag_configure("modificata", foreground=self.c["accento"])
        scorri = ttk.Scrollbar(elenco_f, command=self.albero.yview)
        self.albero.configure(yscrollcommand=scorri.set)
        self.albero.grid(row=0, column=0, sticky="nsew")
        scorri.grid(row=0, column=1, sticky="ns")
        self.albero.bind("<<TreeviewSelect>>", lambda _e: self._seleziona())
        for tasto in ("<Return>", "<F2>"):
            self.albero.bind(tasto, self._vai_alla_traduzione)
        self.albero.bind("<Escape>", lambda _e: self.chiudi())
        self.albero.bind("<Control-a>", self._tutte_le_righe)

        # pagine dell'elenco (PAGINA righe l'una): la riga si vede solo con piu' pagine, o per dire che non c'e'
        # nessuna riga
        pagine = self.riga_pagine = ttk.Frame(cornice)
        pagine.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        self.btn_prima = ttk.Button(pagine, text="◀ Pagina prima", command=lambda: self.cambia_pagina(-1))
        self.posizione = interfaccia.TestoVar()
        self.etichetta_posizione = ttk.Label(pagine, textvariable=self.posizione, style="Stato.TLabel")
        self.btn_dopo = ttk.Button(pagine, text="Pagina dopo ▶", command=lambda: self.cambia_pagina(1))

        editor = ttk.Frame(cornice)
        editor.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        editor.columnconfigure(1, weight=1)
        ttk.Label(editor, text="Originale:").grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.testo_originale = tk.Text(editor, height=3, wrap="word", font=("Segoe UI", 10), state="disabled",
                                       takefocus=False)
        tema.colora_testo(self.testo_originale, self.c)
        self.testo_originale.grid(row=0, column=1, sticky="ew")
        ttk.Label(editor, text="Traduzione:").grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(6, 0))
        self.testo_traduzione = tk.Text(editor, height=3, wrap="word", font=("Segoe UI", 10), undo=True)
        tema.colora_testo(self.testo_traduzione, self.c)
        self.testo_traduzione.grid(row=1, column=1, sticky="ew", pady=(6, 0))
        self.testo_traduzione.bind("<<Modified>>", lambda _e: self._scritto())
        self.testo_traduzione.bind("<Control-Return>", self._conferma_e_avanti)
        self.testo_traduzione.bind("<Escape>", self._annulla_riga)
        self.testo_traduzione.bind("<Tab>", lambda _e: self._prossimo_campo(_e.widget, 1))
        self.testo_traduzione.bind("<Shift-Tab>", lambda _e: self._prossimo_campo(_e.widget, -1))
        info = ttk.Frame(editor)
        info.grid(row=2, column=1, sticky="ew", pady=(4, 0))
        self.lunghezza = interfaccia.TestoVar()
        self.etichetta_lunghezza = ttk.Label(info, textvariable=self.lunghezza, style="Stato.TLabel")
        self.etichetta_lunghezza.pack(side="left")
        self.avviso = interfaccia.TestoVar()
        self.etichetta_avviso = ttk.Label(info, textvariable=self.avviso, foreground=tema.ICONE["avviso"][1])
        self.etichetta_avviso.pack(side="left", padx=(14, 0))
        # il problema della riga per intero (nella colonna dell'elenco si vede solo l'inizio)
        self.problema = interfaccia.TestoVar()
        ttk.Label(editor, textvariable=self.problema, style="Stato.TLabel", wraplength=860, justify="left").grid(
            row=3, column=1, sticky="w")

        pulsanti = ttk.Frame(cornice)
        pulsanti.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        self.btn_salva = ttk.Button(pulsanti, text="Salva e applica al gioco", underline=0, command=self.salva)
        self.btn_salva.pack(side="left")
        self.btn_ritraduci = ttk.Button(pulsanti, text="Ritraduci selezionati…", underline=0,
                                        command=self.ritraduci_selezionati)
        self.btn_ritraduci.pack(side="left", padx=6)
        ttk.Button(pulsanti, text="Chiudi", command=self.chiudi).pack(side="right")
        ttk.Button(pulsanti, text="Apri il file (avanzato)", command=self.apri_file).pack(side="right", padx=6)

        for tasto in ("<Control-f>", "<Control-F>"):
            top.bind(tasto, self._vai_alla_ricerca)
        for tasto in ("<Control-s>", "<Control-S>", "<Alt-s>", "<Alt-S>"):
            top.bind(tasto, lambda _e: self.salva() or "break")
        for tasto in ("<Alt-r>", "<Alt-R>"):
            top.bind(tasto, lambda _e: self.ritraduci_selezionati() or "break")
        for tasto, verso in (("<Alt-Right>", 1), ("<Alt-Left>", -1), ("<Control-Next>", 1), ("<Control-Prior>", -1)):
            top.bind(tasto, lambda _e, v=verso: self.cambia_pagina(v) or "break")
        self.voce_cerca.bind("<Return>", self._vai_all_elenco)
        self.voce_cerca.bind("<Down>", self._vai_all_elenco)
        self.voce_cerca.bind("<Escape>", self._esc_ricerca)
        top.protocol("WM_DELETE_WINDOW", self.chiudi)

        self.aggiorna_elenco()
        self._mostra_traduzione(None)
        top.update_idletasks()
        radice = app.radice
        x = radice.winfo_rootx() + max(0, (radice.winfo_width() - top.winfo_reqwidth()) // 2)
        y = radice.winfo_rooty() + max(0, (radice.winfo_height() - top.winfo_reqheight()) // 4)
        top.geometry("+%d+%d" % (max(0, x), max(0, y)))
        top.deiconify()
        tema._barra_titolo(top, self.c.get("nome") == "scuro")
        self._riprendi()
        if filtro and self.mostrate:
            self._scegli_riga(0)
            self.albero.focus_set()
        else:
            self.voce_cerca.focus_set()

    # --- elenco ------------------------------------------------------------------------------------------

    def _titolo(self):
        return "Correggi traduzioni - %s%s" % (self.elenco.nome, " *" if self.modifiche else "")

    def _etichette_filtri(self):
        conti = self.elenco.conteggi()
        return ["%s (%s)" % (etichetta, _numero(conti.get(k, 0))) for k, etichetta in correzioni.FILTRI]

    @property
    def filtro(self):
        i = self.scelta_filtro.current()
        return self.chiavi_filtri[i] if 0 <= i < len(self.chiavi_filtri) else "tutte"

    def scegli_filtro(self, filtro):
        self.scelta_filtro.current(self.chiavi_filtri.index(filtro))
        self.aggiorna_elenco()

    def _cerca_dopo(self):
        """La ricerca parte appena smetti di scrivere (non a ogni lettera)."""
        if self._ricerca_in_attesa is not None:
            self.top.after_cancel(self._ricerca_in_attesa)
        self._ricerca_in_attesa = self.top.after(250, self.aggiorna_elenco)

    def aggiorna_elenco(self):
        self._ricerca_in_attesa = None
        self.mostrate = self.elenco.filtra(self.filtro, self.cerca.get())
        self.pagina = 0
        self._mostra_pagina()

    def _pagine(self):
        return max(1, (len(self.mostrate) + PAGINA - 1) // PAGINA)

    def cambia_pagina(self, verso):
        nuova = min(max(0, self.pagina + verso), self._pagine() - 1)
        if nuova != self.pagina:
            self.pagina = nuova
            self._mostra_pagina()
            if self.mostrate:
                self._scegli_riga(0)

    def _mostra_pagina(self):
        """Le righe della pagina. La riga che stai correggendo, se c'e' ancora (es. dopo una ricerca), resta scelta."""
        attuale = self.attuale
        self.albero.delete(*self.albero.get_children())
        inizio = self.pagina * PAGINA
        for x in self.mostrate[inizio:inizio + PAGINA]:
            self.albero.insert("", "end", iid=str(self.indice[x.testo]), values=self._valori(x),
                               tags=("modificata",) if x.testo in self.modifiche else ())
        piu_pagine = self._pagine() > 1
        for w in (self.btn_prima, self.etichetta_posizione, self.btn_dopo):
            w.pack_forget()
        if piu_pagine:
            self.posizione.set("Pagina %d di %d (righe %s–%s di %s)" % (
                self.pagina + 1, self._pagine(), _numero(inizio + 1),
                _numero(min(inizio + PAGINA, len(self.mostrate))), _numero(len(self.mostrate))))
            self.btn_prima.pack(side="left")
            self.etichetta_posizione.pack(side="left", padx=10)
            self.btn_dopo.pack(side="left")
        elif not self.mostrate:
            self.posizione.set("Nessuna riga" + (" con questa ricerca" if self.cerca.get().strip() else ""))
            self.etichetta_posizione.pack(side="left")
        if piu_pagine or not self.mostrate:
            self.riga_pagine.grid()
        else:
            self.riga_pagine.grid_remove()
        self.btn_prima.state(["!disabled"] if self.pagina > 0 else ["disabled"])
        self.btn_dopo.state(["!disabled"] if self.pagina < self._pagine() - 1 else ["disabled"])
        iid = str(self.indice[attuale.testo]) if attuale is not None else None
        if iid is not None and self.albero.exists(iid):
            self.albero.selection_set(iid)
            self.albero.focus(iid)
            self.albero.see(iid)
        else:
            self._mostra_traduzione(None)

    def traduzione_di(self, x):
        return self.modifiche.get(x.testo, x.traduzione)

    def _valori(self, x):
        tradotto = self.traduzione_di(x)
        misurata = correzioni.misura(x, tradotto) if tradotto else None
        limite = "%d/%d" % misurata if misurata and misurata[0] is not None else ""
        problema = x.motivi()
        if x.testo in self.modifiche:
            problema = "; ".join(["modificata, da salvare"] + self._avvisi(x))
        return _in_cella(x.testo), _in_cella(tradotto), limite, problema

    def _avvisi(self, x):
        return correzioni.avvisi(x.testo, self.traduzione_di(x), self.elenco.protetti)

    def righe_scelte(self):
        return [self.elenco.voci[int(i)] for i in self.albero.selection()]

    def _scegli_riga(self, posizione):
        figli = self.albero.get_children()
        if not figli:
            return
        iid = figli[max(0, min(posizione, len(figli) - 1))]
        self.albero.selection_set(iid)
        self.albero.focus(iid)
        self.albero.see(iid)
        self._seleziona()

    def _seleziona(self):
        scelte = self.righe_scelte()
        if len(scelte) == 1 and scelte[0] is self.attuale:
            return  # la stessa riga (es. il segnale della scelta arrivato dopo): quello che stai scrivendo resta
        self._mostra_traduzione(scelte[0] if len(scelte) == 1 else None, len(scelte))

    def _tutte_le_righe(self, _evento=None):
        self.albero.selection_set(self.albero.get_children())
        return "break"

    # --- traduzione della riga scelta ---------------------------------------------------------------------

    def _imposta(self, widget, testo, attivo=True):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", testo)
        widget.edit_reset()
        widget.edit_modified(False)
        widget.configure(state="normal" if attivo else "disabled")

    def _mostra_traduzione(self, x, scelte=0):
        self.attuale = None
        if x is None:
            self._imposta(self.testo_originale, "", attivo=False)
            self._imposta(self.testo_traduzione, "", attivo=False)
            self.lunghezza.set("Hai scelto %d righe: puoi ritradurle con \"Ritraduci selezionati…\"." % scelte
                               if scelte > 1 else "Scegli una riga dell'elenco.")
            self.avviso.set("")
            self.problema.set("")
            return
        self._imposta(self.testo_originale, x.testo, attivo=False)
        self._imposta(self.testo_traduzione, self.traduzione_di(x))
        self.attuale = x
        self.problema.set("Problema: %s" % x.motivi() if x.motivi() else "")
        self._aggiorna_info()

    def _scritto(self):
        """La traduzione e' cambiata (anche incollando): la modifica resta in memoria finche' non salvi."""
        if not self.testo_traduzione.edit_modified():
            return
        self.testo_traduzione.edit_modified(False)
        x = self.attuale
        if x is None:
            return
        nuovo = self.testo_traduzione.get("1.0", "end-1c")
        if nuovo == x.traduzione:
            self.modifiche.pop(x.testo, None)
        else:
            self.modifiche[x.testo] = nuovo
        iid = str(self.indice[x.testo])
        if self.albero.exists(iid):
            self.albero.item(iid, values=self._valori(x), tags=("modificata",) if x.testo in self.modifiche else ())
        self.top.title(self._titolo())
        self._aggiorna_info()

    def _aggiorna_info(self):
        x = self.attuale
        if x is None:
            return
        tradotto = self.traduzione_di(x)
        misurata = correzioni.misura(x, tradotto) if tradotto.strip() else None
        if misurata and misurata[0] is not None:
            lunga, larghezza = misurata
            self.lunghezza.set("Riga più lunga: %d caratteri (nel gioco ce ne stanno circa %d)%s" % (
                lunga, larghezza, ": troppo lunga" if correzioni.troppo_lunga(lunga, larghezza) else ""))
        elif misurata:
            self.lunghezza.set("Non ha la forma dell'originale (paragrafi o a capo diversi)")
        else:
            self.lunghezza.set("Lunghezza: %d caratteri (originale: %d)" % (len(tradotto), len(x.testo)))
        avvisi = self._avvisi(x) if tradotto.strip() or x.testo in self.modifiche else []
        self.avviso.set(("Attenzione: " + "; ".join(avvisi)) if avvisi else "")

    def _vai_alla_traduzione(self, _evento=None):
        if self.attuale is not None:
            self.testo_traduzione.focus_set()
            self.testo_traduzione.mark_set("insert", "end-1c")
        return "break"

    def _conferma_e_avanti(self, _evento=None):
        """Ctrl+Invio: la modifica resta (si salva con le altre) e si passa alla riga dopo."""
        figli = self.albero.get_children()
        scelta = self.albero.selection()
        posizione = figli.index(scelta[0]) + 1 if scelta and scelta[0] in figli else 0
        if posizione >= len(figli) and self.pagina < self._pagine() - 1:
            self.cambia_pagina(1)
        else:
            self._scegli_riga(posizione)
        self._vai_alla_traduzione()
        return "break"

    def _annulla_riga(self, _evento=None):
        """Esc nella traduzione: torna quella di prima (salvata) e si torna all'elenco."""
        x = self.attuale
        if x is not None and x.testo in self.modifiche:
            del self.modifiche[x.testo]
            iid = str(self.indice[x.testo])
            if self.albero.exists(iid):
                self.albero.item(iid, values=self._valori(x), tags=())
            self._mostra_traduzione(x)
            self.top.title(self._titolo())
        self.albero.focus_set()
        return "break"

    @staticmethod
    def _prossimo_campo(widget, verso):
        (widget.tk_focusNext() if verso > 0 else widget.tk_focusPrev()).focus_set()
        return "break"

    def _vai_alla_ricerca(self, _evento=None):
        self.voce_cerca.focus_set()
        self.voce_cerca.select_range(0, "end")
        return "break"

    def _vai_all_elenco(self, _evento=None):
        if self._ricerca_in_attesa is not None:
            self.top.after_cancel(self._ricerca_in_attesa)
            self.aggiorna_elenco()
        self.albero.focus_set()
        self._scegli_riga(0)
        return "break"

    def _esc_ricerca(self, _evento=None):
        if self.cerca.get():
            self.cerca.set("")
        else:
            self.chiudi()
        return "break"

    # --- finestre di domanda (modali sopra questa) --------------------------------------------------------

    def _riprendi(self):
        """Dopo una finestra di domanda (che toglie la sua presa quando si chiude) questa torna l'unica attiva."""
        if self.chiusa:
            return
        try:
            self.top.grab_set()
            self.top.lift()
        except tk.TclError:
            pass

    def _scegli(self, titolo, testo, pulsanti, tipo="domanda"):
        try:
            return tema.scegli(self.top, self.c, titolo, testo, pulsanti, tipo)
        finally:
            self._riprendi()

    def _avvisa(self, titolo, testo):
        try:
            tema.avvisa(self.top, self.c, titolo, testo)
        finally:
            self._riprendi()

    # --- azioni -------------------------------------------------------------------------------------------

    def _chiudi_finestra(self):
        self.chiusa = True
        try:
            self.top.grab_release()
        except tk.TclError:
            pass
        self.top.destroy()

    def chiudi(self):
        if self.modifiche:
            scelta = self._scegli("Salvare le correzioni?", "Hai %s. Salvare e scrivere nel gioco le "
                                  "correzioni?" % _quante(len(self.modifiche), "correzione non salvata",
                                                          "correzioni non salvate"),
                                  [("Salva", "salva"), ("Non salvare", "esci"), ("Annulla", None)])
            if scelta == "salva":
                return self.salva()
            if scelta != "esci":
                return False
        self._chiudi_finestra()
        return True

    def _da_salvare(self):
        """Le correzioni pronte (None se ce n'e' una vuota o se hai scelto di tornare a correggere)."""
        vuote = [t for t, v in self.modifiche.items() if not v.strip()]
        if vuote:
            self._avvisa("Traduzione vuota", "La traduzione di \"%s\" è vuota: scrivila, oppure premi Esc per tenere "
                         "quella di prima. Per farla rifare a PatchSmith usa \"Ritraduci selezionati…\"."
                         % _in_cella(vuote[0])[:80])
            self._vai_a(vuote[0])
            return None
        dubbi = [(t, correzioni.avvisi(t, v, self.elenco.protetti)) for t, v in self.modifiche.items()]
        dubbi = [(t, a) for t, a in dubbi if a]
        if dubbi:
            righe = "\n".join("- \"%s\": %s" % (_in_cella(t)[:50], "; ".join(a)) for t, a in dubbi[:5])
            scelta = self._scegli("Controlla le correzioni", "Alcune correzioni hanno qualcosa che non torna:\n%s%s\n\n"
                                  "Il gioco potrebbe mostrarle male (per esempio un segnaposto come {0} va copiato "
                                  "così com'è). Salvare lo stesso?" % (righe, "\n…" if len(dubbi) > 5 else ""),
                                  [("Torna a correggere", None), ("Salva lo stesso", "salva")], "avviso")
            if scelta != "salva":
                self._vai_a(dubbi[0][0])
                return None
        return dict(self.modifiche)

    def _vai_a(self, testo):
        """Mostra la riga di un testo (con tutte le righe, se il filtro o la ricerca la nascondono)."""
        x = self.elenco.voci[self.indice[testo]]
        if x not in self.mostrate:
            self.cerca.set("")
            if self._ricerca_in_attesa is not None:  # (la ricerca vuota la rifa' qui sotto, subito)
                self.top.after_cancel(self._ricerca_in_attesa)
            self.scelta_filtro.current(0)
            self.aggiorna_elenco()
        posizione = self.mostrate.index(x)
        self.pagina = posizione // PAGINA
        self._mostra_pagina()
        self._scegli_riga(posizione % PAGINA)
        self._vai_alla_traduzione()

    def _salva_modifiche(self, modifiche):
        """Salva (correzioni.salva); False se non riesce (la finestra resta aperta, con le modifiche)."""
        viste = {t: self.elenco.voci[self.indice[t]].cache for t in modifiche} if self.elenco.unity else None
        try:
            correzioni.salva(self.elenco.percorso_profilo, modifiche, viste, prova=self.elenco.prova,
                             log=self.app._log)
        except (OSError, RuntimeError, ValueError) as e:
            self._avvisa("Correzioni non salvate", "Non sono riuscito a salvare le correzioni: %s\n\nLe correzioni "
                         "sono ancora qui: riprova tra un attimo (per esempio dopo aver chiuso il gioco)." % e)
            return False
        self.modifiche = {}
        return True

    def salva(self):
        """"Salva e applica al gioco": le correzioni in memoria, poi (finestra chiusa) "Riapplica" da sola."""
        if not self.modifiche:
            self._avvisa("Niente da salvare", "Non hai ancora corretto nessuna traduzione: scegli una riga, scrivi la "
                         "traduzione qui sotto e poi premi \"Salva e applica al gioco\".")
            return False
        modifiche = self._da_salvare()
        if modifiche is None or not self._salva_modifiche(modifiche):
            return False
        self._chiudi_finestra()
        self.app._dopo_correzioni(len(modifiche))
        return True

    def _prima_salva(self):
        """Prima di un'operazione della finestra principale: le correzioni non salvate si salvano (o si annulla)."""
        if not self.modifiche:
            return True
        scelta = self._scegli("Correzioni non salvate", "Hai %s: salvo le correzioni prima di continuare?" % _quante(
            len(self.modifiche), "correzione non salvata", "correzioni non salvate"),
            [("Salva e continua", "salva"), ("Annulla", None)])
        if scelta != "salva":
            return False
        modifiche = self._da_salvare()
        return modifiche is not None and self._salva_modifiche(modifiche)

    def _traduttore_attuale(self):
        from .gui import TRADUTTORI
        nome = self.app._backend()
        return nome, TRADUTTORI.get(nome, "il traduttore di prova" if nome == "finto" else nome)

    def ritraduci_selezionati(self):
        from . import ollama
        righe = self.righe_scelte()
        if not righe:
            self._avvisa("Nessuna riga scelta", "Scegli nell'elenco le righe da ritradurre (con Maiusc o Ctrl ne "
                         "scegli più di una; Ctrl+A tutte quelle della pagina).")
            return False
        nome, etichetta = self._traduttore_attuale()
        pulsanti = []
        if nome != "finto":
            try:
                pronto = ollama.stato() == "pronto"
            except OSError:
                pronto = False
            if pronto:
                from .gui import MODELLI_LOCALI
                pulsanti += [("Traduttore locale: %s" % MODELLI_LOCALI[k], k) for k in ("12b", "4b")
                             if ollama.modello_presente(k)]
            if nome == "locale" and not pulsanti:
                self._avvisa("Traduttore locale da installare", "Per ritradurre serve un traduttore: il traduttore "
                             "locale non è ancora installato. Installalo con \"Installa…\" nella finestra principale, "
                             "oppure scegli lì un altro traduttore.")
                return False
        if nome != "locale":
            pulsanti.append(("Con %s" % etichetta, "attuale"))
        testo = ("Ritraduco %s con domande nuove, così la traduzione può venire diversa da quella di adesso "
                 "(anche per le righe che hai corretto a mano: le hai scelte tu). Se la traduzione nuova non passa i "
                 "controlli resta quella di prima. Poi la scrivo nel gioco." % _quante(len(righe), "riga", "righe"))
        if nome in ("google", "deepl", "mymemory") and not any(k in ("12b", "4b") for _, k in pulsanti):
            testo += ("\n\nCon %s di solito la traduzione torna uguale: per una diversa serve il traduttore locale."
                      % etichetta)
        scelta = self._scegli("Ritraduci le righe scelte", testo, pulsanti + [("Annulla", None)])
        if scelta is None or not self._prima_salva():
            return False
        self._chiudi_finestra()
        self.app._ritraduci_correzioni([x.testo for x in righe], None if scelta == "attuale" else scelta)
        return True

    def ritraduci_tutto(self):
        from . import backend, ollama
        from .limiter import descrivi_durata
        nome, _ = self._traduttore_attuale()
        try:
            presente = nome == "finto" or (ollama.stato() == "pronto" and ollama.modello_presente("12b"))
        except OSError:
            presente = False
        if not presente:
            scelta = self._scegli("Modello Qualità da installare", "Per ritradurre con il modello Qualità (12B) "
                                  "bisogna prima installarlo: PatchSmith ti dice quanto scarica e parte solo dopo il "
                                  "tuo sì. Finita l'installazione ritraduco da solo. Da quel momento il traduttore "
                                  "scelto è il traduttore locale con il modello Qualità (puoi cambiarlo nella finestra "
                                  "principale).",
                                  [("Installa il modello Qualità", "installa"), ("Annulla", None)])
            if scelta != "installa" or not self._prima_salva():
                return False
            self._chiudi_finestra()
            self.app._installa_12b_e_ritraduci()
            return True
        testi, a_mano = correzioni.testi_da_ritradurre(self.elenco)
        if not testi:
            self._avvisa("Niente da ritradurre", "Tutte le traduzioni di questo gioco sono corrette a mano: restano "
                         "come sono.")
            return False
        stima = backend.Locale("12b", log=None).stima([len(t) for t in testi])
        testo = ("Ritraduco con il modello Qualità (12B) %d testi di questo gioco: le traduzioni automatiche e i testi "
                 "rimasti in originale. " % len(testi))
        if self.elenco.automatiche_note:
            testo += ("Le tue correzioni a mano (%d) restano come sono. " % a_mano) if a_mano else ""
            if self.elenco.non_annotate:
                testo += ("%s di una versione vecchia di PatchSmith, che non ricordava quali hai corretto a "
                          "mano: ritraduco anche %s. " % (
                              _quante(self.elenco.non_annotate, "traduzione è", "traduzioni sono"),
                              "quella" if self.elenco.non_annotate == 1 else "quelle"))
        else:
            testo += ("Questo gioco è stato tradotto con una versione vecchia di PatchSmith, che non ricordava quali "
                      "traduzioni hai corretto a mano: ritraduco anche quelle. ")
        testo += ("Se una traduzione nuova non riesce resta quella di prima. Tempo stimato: %s (il gioco deve restare "
                  "chiuso). Poi le scrivo nel gioco." % descrivi_durata(stima))
        scelta = self._scegli("Ritraduci tutto con Qualità (12B)", testo, [("Annulla", None), ("Ritraduci", "si")])
        if scelta != "si" or not self._prima_salva():
            return False
        self._chiudi_finestra()
        self.app._ritraduci_correzioni(None, "12b")
        return True

    def ricomincia(self):
        a_mano = sum(1 for x in self.elenco.voci if x.a_mano)
        tradotte = sum(1 for x in self.elenco.voci if x.memoria is not None or x.cache is not None)
        scelta = self._scegli(
            "Ricominciare da zero?",
            "Il gioco torna com'era (come con \"Ripristina originali\"), tutte le sue traduzioni (%d%s) vengono messe "
            "da parte in una copia di sicurezza, nella cartella memory\\copies di PatchSmith, e il gioco si traduce di "
            "nuovo da capo con il traduttore scelto. Serve solo se le traduzioni sono venute male in blocco: per "
            "qualche frase basta correggerla qui.\n\nIl gioco deve essere chiuso." % (
                tradotte, ", comprese %d corrette a mano" % a_mano if a_mano else ""),
            [("Annulla", None), ("Ricomincia da zero", "si")], "avviso")
        if scelta != "si":
            return False
        self._chiudi_finestra()
        self.app._traduci(da_zero=True)
        return True

    def apri_file(self):
        if not self._prima_salva():
            return False
        self._chiudi_finestra()
        self.app._apri_file_traduzioni()
        return True
