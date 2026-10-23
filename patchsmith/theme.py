"""Light/dark theme of the graphical interface.

"auto" follows the Windows setting (Settings > Personalization > Colors > app mode);
if it cannot be read, the dark theme is used.
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


def system_theme():
    """'scuro' or 'chiaro' according to Windows; 'scuro' if the setting cannot be read."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return "chiaro" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1 else "scuro"
        except OSError:
            pass
    return "scuro"


def _title_bar(root, dark):
    """Dark title bar on Windows 10/11 (DWM attribute 'immersive dark mode')."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        root.update_idletasks()
        window = ctypes.windll.user32.GetParent(root.winfo_id())
        value_ = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # 20: Windows 10 2004+ / 11; 19: earlier builds
            outcome = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                window, attribute, ctypes.byref(value_), ctypes.sizeof(value_))
            if outcome == 0:
                break
    except (AttributeError, OSError):
        pass


def apply(root, choice="auto"):
    """Applies the theme to the whole application. Returns the palette used."""
    name = system_theme() if choice not in PALETTE else choice
    c = PALETTE[name]
    style = ttk.Style(root)
    style.theme_use("clam")  # the only ttk theme that can be fully recolored

    style.configure(".", background=c["sfondo"], foreground=c["testo"], fieldbackground=c["campo"],
                    bordercolor=c["bordo"], lightcolor=c["bordo"], darkcolor=c["bordo"],
                    troughcolor=c["campo"], focuscolor=c["accento"], selectbackground=c["selezione"],
                    selectforeground=c["testo"], insertcolor=c["testo"], arrowcolor=c["testo"])
    style.configure("TFrame", background=c["sfondo"])
    style.configure("TLabel", background=c["sfondo"], foreground=c["testo"])
    style.configure("Stato.TLabel", background=c["sfondo"], foreground=c["tenue"])
    style.configure("TButton", background=c["pulsante"], foreground=c["testo"], padding=(10, 4),
                    bordercolor=c["bordo"], focusthickness=1)
    style.map("TButton",
              background=[("disabled", c["sfondo"]), ("pressed", c["premuto"]), ("active", c["pulsante_sopra"])],
              foreground=[("disabled", c["tenue"])],
              bordercolor=[("focus", c["accento"])])
    style.configure("TEntry", fieldbackground=c["campo"], foreground=c["testo"], insertcolor=c["testo"])
    style.map("TEntry", bordercolor=[("focus", c["accento"])], fieldbackground=[("readonly", c["campo"])],
              foreground=[("readonly", c["testo"])])
    style.configure("TCombobox", fieldbackground=c["campo"], background=c["pulsante"], foreground=c["testo"],
                    arrowcolor=c["testo"])
    style.map("TCombobox",
              fieldbackground=[("readonly", c["campo"])], foreground=[("readonly", c["testo"])],
              selectbackground=[("readonly", c["campo"])], selectforeground=[("readonly", c["testo"])],
              background=[("active", c["pulsante_sopra"])], bordercolor=[("focus", c["accento"])])
    style.configure("Horizontal.TProgressbar", background=c["accento"], troughcolor=c["campo"],
                    bordercolor=c["bordo"], lightcolor=c["accento"], darkcolor=c["accento"])
    style.configure("Vertical.TScrollbar", background=c["pulsante"], troughcolor=c["sfondo"],
                    bordercolor=c["sfondo"], arrowcolor=c["testo"])
    style.map("Vertical.TScrollbar", background=[("active", c["pulsante_sopra"])])
    # lists (the "Fix translations" window)
    style.configure("Treeview", background=c["campo"], fieldbackground=c["campo"], foreground=c["testo"],
                    bordercolor=c["bordo"], lightcolor=c["bordo"], darkcolor=c["bordo"])
    style.map("Treeview", background=[("selected", c["selezione"])], foreground=[("selected", c["testo"])])
    style.configure("Treeview.Heading", background=c["pulsante"], foreground=c["testo"], bordercolor=c["bordo"],
                    lightcolor=c["bordo"], darkcolor=c["bordo"], relief="flat")
    style.map("Treeview.Heading", background=[("active", c["pulsante_sopra"])])

    # button next to a field ("Browse…"): no margin above and below, as tall as the field
    style.configure("Campo.TButton", padding=(10, 0))
    # the small language flag: as big as the flag (the clam theme gives buttons at least 11 characters)
    style.configure("Bandiera.TButton", padding=(3, 2), width=0)
    # button with a menu (the small language flag) and the menus (they are not ttk widgets)
    style.configure("TMenubutton", background=c["pulsante"], foreground=c["testo"], bordercolor=c["bordo"],
                    arrowcolor=c["testo"], padding=(6, 3))
    style.map("TMenubutton", background=[("pressed", c["premuto"]), ("active", c["pulsante_sopra"])],
              bordercolor=[("focus", c["accento"])])
    for option_name, color in (("background", c["campo"]), ("foreground", c["testo"]),
                            ("activeBackground", c["selezione"]), ("activeForeground", c["testo"]),
                            ("selectColor", c["testo"])):
        root.option_add("*Menu.%s" % option_name, color)
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 1)

    # drop-down list of the comboboxes (it is not a ttk widget)
    root.option_add("*TCombobox*Listbox.background", c["campo"])
    root.option_add("*TCombobox*Listbox.foreground", c["testo"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["selezione"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["testo"])
    root.configure(background=c["sfondo"])
    _title_bar(root, name == "scuro")
    return dict(c, nome=name)


# --- message dialogs with the theme's colors ---------------------------------------------------
# Tkinter's messageboxes use the Windows MessageBox, which always stays light: these replace
# them with dialogs we draw ourselves.

ICONS = {"info": ("i", "accento"), "avviso": ("!", "#d39b2a"), "errore": ("✕", "#d9534f"),
         "domanda": ("?", "accento")}
ON_SHOW = None  # function(tipo) called when a dialog appears (the main window: the sounds)


def window(parent, c, title_text, text, kind="info", buttons=(("OK", True),), cancel=None):
    """Modal dialog. pulsanti: [(etichetta, valore)], the first is the default one (Enter).
    Esc or the window's X return `annulla`."""
    top = tk.Toplevel(parent)
    top.withdraw()
    top.title(title_text)
    top.configure(background=c["sfondo"])
    top.resizable(False, False)
    top.transient(parent)

    body = ttk.Frame(top, padding=(20, 18, 24, 18))
    body.pack(fill="both", expand=True)
    symbol, color = ICONS.get(kind, ICONS["info"])
    color = c.get(color, color)
    icon = tk.Canvas(body, width=32, height=32, highlightthickness=0, background=c["sfondo"])
    icon.create_oval(1, 1, 31, 31, fill=color, outline=color)
    icon.create_text(16, 16, text=symbol, fill="#ffffff", font=("Segoe UI", 13, "bold"))
    icon.grid(row=0, column=0, sticky="n", padx=(0, 14))
    ttk.Label(body, text=text, wraplength=400, justify="left").grid(row=0, column=1, sticky="w")

    footer = tk.Frame(top, background=c["campo"])
    footer.pack(fill="x")
    result = {"valore": cancel}

    def close(value_):
        result["valore"] = value_
        top.grab_release()
        top.destroy()

    button_widgets = []
    for label, value_ in reversed(list(buttons)):
        # minimum width of 10 characters: longer labels ("Use the local translator") are not cut
        b = ttk.Button(footer, text=label, width=-10, command=lambda v=value_: close(v))
        b.pack(side="right", padx=(0, 12), pady=12)
        button_widgets.append(b)
    default = buttons[0][1]
    top.bind("<Return>", lambda _e: close(default))
    top.bind("<Escape>", lambda _e: close(cancel))
    top.protocol("WM_DELETE_WINDOW", lambda: close(cancel))

    top.update_idletasks()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - top.winfo_reqwidth()) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - top.winfo_reqheight()) // 3)
    top.geometry("+%d+%d" % (x, y))
    top.deiconify()
    _title_bar(top, c.get("nome") == "scuro")
    if ON_SHOW is not None:
        try:
            ON_SHOW(kind)
        except Exception:  # noqa: BLE001 - a sound must never prevent the dialog
            pass
    top.grab_set()
    button_widgets[-1].focus_set()  # the first button (default)
    parent.wait_window(top)
    return result["valore"]


def inform(parent, c, title_text, text):
    window(parent, c, title_text, text, "info")


def warn(parent, c, title_text, text):
    window(parent, c, title_text, text, "avviso")


def error(parent, c, title_text, text):
    window(parent, c, title_text, text, "errore")


def ask(parent, c, title_text, text):
    """True if the user chooses Yes."""
    return bool(window(parent, c, title_text, text, "domanda", (("Sì", True), ("No", False)), cancel=False))


def choose(parent, c, title_text, text, buttons, kind="info"):
    """Dialog with several buttons [(etichetta, valore)] (e.g. "OK", "Show remaining", "Create report"): returns
    the value of the button pressed. The first is the default (Enter); Esc or the X return None."""
    return window(parent, c, title_text, text, kind, buttons, cancel=None)


def color_text(widget, c):
    """Colors for the classic Tk widgets (e.g. the log's tk.Text)."""
    if isinstance(widget, tk.Text):
        widget.configure(background=c["campo"], foreground=c["testo"], insertbackground=c["testo"],
                         selectbackground=c["selezione"], selectforeground=c["testo"],
                         highlightthickness=1, highlightbackground=c["bordo"], highlightcolor=c["accento"],
                         relief="flat", borderwidth=0, padx=6, pady=4)
