"""Tema chiaro/scuro dell'interfaccia grafica.

"auto" segue l'impostazione di Windows (Impostazioni > Personalizzazione > Colori > modalita' app);
se non si riesce a leggerla si usa il tema scuro.
"""
import sys
import tkinter as tk
from tkinter import ttk

PALETTE = {
    "scuro": {
        "sfondo": "#202020", "campo": "#2b2b2b", "testo": "#e8e8e8", "tenue": "#9a9a9a",
        "bordo": "#3d3d3d", "pulsante": "#333333", "pulsante_sopra": "#404040", "premuto": "#4a4a4a",
        "accento": "#4c8bf5", "selezione": "#264f78",
    },
    "chiaro": {
        "sfondo": "#f3f3f3", "campo": "#ffffff", "testo": "#1b1b1b", "tenue": "#606060",
        "bordo": "#c8c8c8", "pulsante": "#fdfdfd", "pulsante_sopra": "#e8f0fe", "premuto": "#d6e4fd",
        "accento": "#1a73e8", "selezione": "#cce0ff",
    },
}


def tema_di_sistema():
    """'scuro' o 'chiaro' secondo Windows; 'scuro' se l'impostazione non e' leggibile."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            chiave = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chiave) as k:
                return "chiaro" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1 else "scuro"
        except OSError:
            pass
    return "scuro"


def _barra_titolo(radice, scuro):
    """Barra del titolo scura su Windows 10/11 (attributo DWM 'immersive dark mode')."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        radice.update_idletasks()
        finestra = ctypes.windll.user32.GetParent(radice.winfo_id())
        valore = ctypes.c_int(1 if scuro else 0)
        for attributo in (20, 19):  # 20: Windows 10 2004+ / 11; 19: build precedenti
            esito = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                finestra, attributo, ctypes.byref(valore), ctypes.sizeof(valore))
            if esito == 0:
                break
    except (AttributeError, OSError):
        pass


def applica(radice, scelta="auto"):
    """Applica il tema a tutta l'applicazione. Restituisce la palette usata."""
    nome = tema_di_sistema() if scelta not in PALETTE else scelta
    c = PALETTE[nome]
    stile = ttk.Style(radice)
    stile.theme_use("clam")  # l'unico tema ttk interamente ricolorabile

    stile.configure(".", background=c["sfondo"], foreground=c["testo"], fieldbackground=c["campo"],
                    bordercolor=c["bordo"], lightcolor=c["bordo"], darkcolor=c["bordo"],
                    troughcolor=c["campo"], focuscolor=c["accento"], selectbackground=c["selezione"],
                    selectforeground=c["testo"], insertcolor=c["testo"], arrowcolor=c["testo"])
    stile.configure("TFrame", background=c["sfondo"])
    stile.configure("TLabel", background=c["sfondo"], foreground=c["testo"])
    stile.configure("Stato.TLabel", background=c["sfondo"], foreground=c["tenue"])
    stile.configure("TButton", background=c["pulsante"], foreground=c["testo"], padding=(10, 4),
                    bordercolor=c["bordo"], focusthickness=1)
    stile.map("TButton",
              background=[("disabled", c["sfondo"]), ("pressed", c["premuto"]), ("active", c["pulsante_sopra"])],
              foreground=[("disabled", c["tenue"])],
              bordercolor=[("focus", c["accento"])])
    stile.configure("TEntry", fieldbackground=c["campo"], foreground=c["testo"], insertcolor=c["testo"])
    stile.map("TEntry", bordercolor=[("focus", c["accento"])], fieldbackground=[("readonly", c["campo"])],
              foreground=[("readonly", c["testo"])])
    stile.configure("TCombobox", fieldbackground=c["campo"], background=c["pulsante"], foreground=c["testo"],
                    arrowcolor=c["testo"])
    stile.map("TCombobox",
              fieldbackground=[("readonly", c["campo"])], foreground=[("readonly", c["testo"])],
              selectbackground=[("readonly", c["campo"])], selectforeground=[("readonly", c["testo"])],
              background=[("active", c["pulsante_sopra"])], bordercolor=[("focus", c["accento"])])
    stile.configure("Horizontal.TProgressbar", background=c["accento"], troughcolor=c["campo"],
                    bordercolor=c["bordo"], lightcolor=c["accento"], darkcolor=c["accento"])
    stile.configure("Vertical.TScrollbar", background=c["pulsante"], troughcolor=c["sfondo"],
                    bordercolor=c["sfondo"], arrowcolor=c["testo"])
    stile.map("Vertical.TScrollbar", background=[("active", c["pulsante_sopra"])])
    # elenchi (la finestra "Correggi traduzioni")
    stile.configure("Treeview", background=c["campo"], fieldbackground=c["campo"], foreground=c["testo"],
                    bordercolor=c["bordo"], lightcolor=c["bordo"], darkcolor=c["bordo"])
    stile.map("Treeview", background=[("selected", c["selezione"])], foreground=[("selected", c["testo"])])
    stile.configure("Treeview.Heading", background=c["pulsante"], foreground=c["testo"], bordercolor=c["bordo"],
                    lightcolor=c["bordo"], darkcolor=c["bordo"], relief="flat")
    stile.map("Treeview.Heading", background=[("active", c["pulsante_sopra"])])

    # pulsante accanto a un campo ("Sfoglia…"): senza margine sopra e sotto, alto quanto il campo
    stile.configure("Campo.TButton", padding=(10, 0))
    # la bandierina della lingua: grande quanto la bandiera (il tema clam da' ai pulsanti almeno 11 caratteri)
    stile.configure("Bandiera.TButton", padding=(3, 2), width=0)
    # pulsante con menu (la bandierina della lingua) e i menu (non sono widget ttk)
    stile.configure("TMenubutton", background=c["pulsante"], foreground=c["testo"], bordercolor=c["bordo"],
                    arrowcolor=c["testo"], padding=(6, 3))
    stile.map("TMenubutton", background=[("pressed", c["premuto"]), ("active", c["pulsante_sopra"])],
              bordercolor=[("focus", c["accento"])])
    for opzione, colore in (("background", c["campo"]), ("foreground", c["testo"]),
                            ("activeBackground", c["selezione"]), ("activeForeground", c["testo"]),
                            ("selectColor", c["testo"])):
        radice.option_add("*Menu.%s" % opzione, colore)
    radice.option_add("*Menu.relief", "flat")
    radice.option_add("*Menu.borderWidth", 1)

    # elenco a discesa delle combobox (non e' un widget ttk)
    radice.option_add("*TCombobox*Listbox.background", c["campo"])
    radice.option_add("*TCombobox*Listbox.foreground", c["testo"])
    radice.option_add("*TCombobox*Listbox.selectBackground", c["selezione"])
    radice.option_add("*TCombobox*Listbox.selectForeground", c["testo"])
    radice.configure(background=c["sfondo"])
    _barra_titolo(radice, nome == "scuro")
    return dict(c, nome=nome)


# --- finestre di messaggio con i colori del tema -----------------------------------------------
# I messagebox di Tkinter usano la MessageBox di Windows, che resta sempre chiara: queste le
# sostituiscono con finestre disegnate da noi.

ICONE = {"info": ("i", "accento"), "avviso": ("!", "#d39b2a"), "errore": ("✕", "#d9534f"),
         "domanda": ("?", "accento")}
AL_MOSTRARE = None  # funzione(tipo) chiamata quando compare una finestra (la finestra principale: i suoni)


def finestra(genitore, c, titolo, testo, tipo="info", pulsanti=(("OK", True),), annulla=None):
    """Finestra modale. pulsanti: [(etichetta, valore)], il primo e' quello predefinito (Invio).
    Esc o la X della finestra restituiscono `annulla`."""
    top = tk.Toplevel(genitore)
    top.withdraw()
    top.title(titolo)
    top.configure(background=c["sfondo"])
    top.resizable(False, False)
    top.transient(genitore)

    corpo = ttk.Frame(top, padding=(20, 18, 24, 18))
    corpo.pack(fill="both", expand=True)
    simbolo, colore = ICONE.get(tipo, ICONE["info"])
    colore = c.get(colore, colore)
    icona = tk.Canvas(corpo, width=32, height=32, highlightthickness=0, background=c["sfondo"])
    icona.create_oval(1, 1, 31, 31, fill=colore, outline=colore)
    icona.create_text(16, 16, text=simbolo, fill="#ffffff", font=("Segoe UI", 13, "bold"))
    icona.grid(row=0, column=0, sticky="n", padx=(0, 14))
    ttk.Label(corpo, text=testo, wraplength=400, justify="left").grid(row=0, column=1, sticky="w")

    piede = tk.Frame(top, background=c["campo"])
    piede.pack(fill="x")
    risultato = {"valore": annulla}

    def chiudi(valore):
        risultato["valore"] = valore
        top.grab_release()
        top.destroy()

    bottoni = []
    for etichetta, valore in reversed(list(pulsanti)):
        # larghezza minima di 10 caratteri: le etichette piu' lunghe ("Usa il traduttore locale") non si tagliano
        b = ttk.Button(piede, text=etichetta, width=-10, command=lambda v=valore: chiudi(v))
        b.pack(side="right", padx=(0, 12), pady=12)
        bottoni.append(b)
    predefinito = pulsanti[0][1]
    top.bind("<Return>", lambda _e: chiudi(predefinito))
    top.bind("<Escape>", lambda _e: chiudi(annulla))
    top.protocol("WM_DELETE_WINDOW", lambda: chiudi(annulla))

    top.update_idletasks()
    x = genitore.winfo_rootx() + max(0, (genitore.winfo_width() - top.winfo_reqwidth()) // 2)
    y = genitore.winfo_rooty() + max(0, (genitore.winfo_height() - top.winfo_reqheight()) // 3)
    top.geometry("+%d+%d" % (x, y))
    top.deiconify()
    _barra_titolo(top, c.get("nome") == "scuro")
    if AL_MOSTRARE is not None:
        try:
            AL_MOSTRARE(tipo)
        except Exception:  # noqa: BLE001 - un suono non deve mai impedire la finestra
            pass
    top.grab_set()
    bottoni[-1].focus_set()  # il primo pulsante (predefinito)
    genitore.wait_window(top)
    return risultato["valore"]


def informa(genitore, c, titolo, testo):
    finestra(genitore, c, titolo, testo, "info")


def avvisa(genitore, c, titolo, testo):
    finestra(genitore, c, titolo, testo, "avviso")


def errore(genitore, c, titolo, testo):
    finestra(genitore, c, titolo, testo, "errore")


def chiedi(genitore, c, titolo, testo):
    """True se l'utente sceglie Si'."""
    return bool(finestra(genitore, c, titolo, testo, "domanda", (("Sì", True), ("No", False)), annulla=False))


def scegli(genitore, c, titolo, testo, pulsanti, tipo="info"):
    """Finestra con piu' pulsanti [(etichetta, valore)] (es. "OK", "Mostra i rimasti", "Crea rapporto"): restituisce
    il valore del pulsante premuto. Il primo e' il predefinito (Invio); Esc o la X restituiscono None."""
    return finestra(genitore, c, titolo, testo, tipo, pulsanti, annulla=None)


def colora_testo(widget, c):
    """Colori per i widget Tk classici (es. tk.Text del registro)."""
    if isinstance(widget, tk.Text):
        widget.configure(background=c["campo"], foreground=c["testo"], insertbackground=c["testo"],
                         selectbackground=c["selezione"], selectforeground=c["testo"],
                         highlightthickness=1, highlightbackground=c["bordo"], highlightcolor=c["accento"],
                         relief="flat", borderwidth=0, padx=6, pady=4)
