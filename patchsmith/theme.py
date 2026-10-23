"""Light/dark theme of the graphical interface.

"auto" follows the Windows setting (Settings > Personalization > Colors > app mode);
if it cannot be read, the dark theme is used.
"""
import sys
import tkinter as tk
from tkinter import ttk

PALETTE = {
    "dark": {
        "background": "#202020", "field": "#2b2b2b", "text": "#e8e8e8", "muted": "#9a9a9a",
        "border": "#3d3d3d", "button": "#333333", "button_hover": "#404040", "pressed": "#4a4a4a",
        "accent": "#4c8bf5", "selection": "#264f78",
    },
    "light": {
        "background": "#f3f3f3", "field": "#ffffff", "text": "#1b1b1b", "muted": "#606060",
        "border": "#c8c8c8", "button": "#fdfdfd", "button_hover": "#e8f0fe", "pressed": "#d6e4fd",
        "accent": "#1a73e8", "selection": "#cce0ff",
    },
}


def system_theme():
    """'dark' or 'light' according to Windows; 'dark' if the setting cannot be read."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 1 else "dark"
        except OSError:
            pass
    return "dark"


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

    style.configure(".", background=c["background"], foreground=c["text"], fieldbackground=c["field"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    troughcolor=c["field"], focuscolor=c["accent"], selectbackground=c["selection"],
                    selectforeground=c["text"], insertcolor=c["text"], arrowcolor=c["text"])
    style.configure("TFrame", background=c["background"])
    style.configure("TLabel", background=c["background"], foreground=c["text"])
    style.configure("Stato.TLabel", background=c["background"], foreground=c["muted"])
    style.configure("TButton", background=c["button"], foreground=c["text"], padding=(10, 4),
                    bordercolor=c["border"], focusthickness=1)
    style.map("TButton",
              background=[("disabled", c["background"]), ("pressed", c["pressed"]), ("active", c["button_hover"])],
              foreground=[("disabled", c["muted"])],
              bordercolor=[("focus", c["accent"])])
    style.configure("TEntry", fieldbackground=c["field"], foreground=c["text"], insertcolor=c["text"])
    style.map("TEntry", bordercolor=[("focus", c["accent"])], fieldbackground=[("readonly", c["field"])],
              foreground=[("readonly", c["text"])])
    style.configure("TCombobox", fieldbackground=c["field"], background=c["button"], foreground=c["text"],
                    arrowcolor=c["text"])
    style.map("TCombobox",
              fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["text"])],
              selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["text"])],
              background=[("active", c["button_hover"])], bordercolor=[("focus", c["accent"])])
    style.configure("Horizontal.TProgressbar", background=c["accent"], troughcolor=c["field"],
                    bordercolor=c["border"], lightcolor=c["accent"], darkcolor=c["accent"])
    style.configure("Vertical.TScrollbar", background=c["button"], troughcolor=c["background"],
                    bordercolor=c["background"], arrowcolor=c["text"])
    style.map("Vertical.TScrollbar", background=[("active", c["button_hover"])])
    # lists (the "Fix translations" window)
    style.configure("Treeview", background=c["field"], fieldbackground=c["field"], foreground=c["text"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"])
    style.map("Treeview", background=[("selected", c["selection"])], foreground=[("selected", c["text"])])
    style.configure("Treeview.Heading", background=c["button"], foreground=c["text"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], relief="flat")
    style.map("Treeview.Heading", background=[("active", c["button_hover"])])

    # button next to a field ("Browse…"): no margin above and below, as tall as the field
    style.configure("Campo.TButton", padding=(10, 0))
    # the small language flag: as big as the flag (the clam theme gives buttons at least 11 characters)
    style.configure("Bandiera.TButton", padding=(3, 2), width=0)
    # button with a menu (the small language flag) and the menus (they are not ttk widgets)
    style.configure("TMenubutton", background=c["button"], foreground=c["text"], bordercolor=c["border"],
                    arrowcolor=c["text"], padding=(6, 3))
    style.map("TMenubutton", background=[("pressed", c["pressed"]), ("active", c["button_hover"])],
              bordercolor=[("focus", c["accent"])])
    for option_name, color in (("background", c["field"]), ("foreground", c["text"]),
                            ("activeBackground", c["selection"]), ("activeForeground", c["text"]),
                            ("selectColor", c["text"])):
        root.option_add("*Menu.%s" % option_name, color)
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 1)

    # drop-down list of the comboboxes (it is not a ttk widget)
    root.option_add("*TCombobox*Listbox.background", c["field"])
    root.option_add("*TCombobox*Listbox.foreground", c["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", c["selection"])
    root.option_add("*TCombobox*Listbox.selectForeground", c["text"])
    root.configure(background=c["background"])
    _title_bar(root, name == "dark")
    return dict(c, name=name)


# --- message dialogs with the theme's colors ---------------------------------------------------
# Tkinter's messageboxes use the Windows MessageBox, which always stays light: these replace
# them with dialogs we draw ourselves.

ICONS = {"info": ("i", "accent"), "warning": ("!", "#d39b2a"), "error": ("✕", "#d9534f"),
         "question": ("?", "accent")}
ON_SHOW = None  # function(tipo) called when a dialog appears (the main window: the sounds)


def window(parent, c, title_text, text, kind="info", buttons=(("OK", True),), cancel=None):
    """Modal dialog. pulsanti: [(etichetta, valore)], the first is the default one (Enter).
    Esc or the window's X return `annulla`."""
    top = tk.Toplevel(parent)
    top.withdraw()
    top.title(title_text)
    top.configure(background=c["background"])
    top.resizable(False, False)
    top.transient(parent)

    body = ttk.Frame(top, padding=(20, 18, 24, 18))
    body.pack(fill="both", expand=True)
    symbol, color = ICONS.get(kind, ICONS["info"])
    color = c.get(color, color)
    icon = tk.Canvas(body, width=32, height=32, highlightthickness=0, background=c["background"])
    icon.create_oval(1, 1, 31, 31, fill=color, outline=color)
    icon.create_text(16, 16, text=symbol, fill="#ffffff", font=("Segoe UI", 13, "bold"))
    icon.grid(row=0, column=0, sticky="n", padx=(0, 14))
    ttk.Label(body, text=text, wraplength=400, justify="left").grid(row=0, column=1, sticky="w")

    footer = tk.Frame(top, background=c["field"])
    footer.pack(fill="x")
    result = {"value": cancel}

    def close(value_):
        result["value"] = value_
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
    _title_bar(top, c.get("name") == "dark")
    if ON_SHOW is not None:
        try:
            ON_SHOW(kind)
        except Exception:  # noqa: BLE001 - a sound must never prevent the dialog
            pass
    top.grab_set()
    button_widgets[-1].focus_set()  # the first button (default)
    parent.wait_window(top)
    return result["value"]


def inform(parent, c, title_text, text):
    window(parent, c, title_text, text, "info")


def warn(parent, c, title_text, text):
    window(parent, c, title_text, text, "warning")


def error(parent, c, title_text, text):
    window(parent, c, title_text, text, "error")


def ask(parent, c, title_text, text):
    """True if the user chooses Yes."""
    return bool(window(parent, c, title_text, text, "question", (("Yes", True), ("No", False)), cancel=False))


def choose(parent, c, title_text, text, buttons, kind="info"):
    """Dialog with several buttons [(etichetta, valore)] (e.g. "OK", "Show remaining", "Create report"): returns
    the value of the button pressed. The first is the default (Enter); Esc or the X return None."""
    return window(parent, c, title_text, text, kind, buttons, cancel=None)


def color_text(widget, c):
    """Colors for the classic Tk widgets (e.g. the log's tk.Text)."""
    if isinstance(widget, tk.Text):
        widget.configure(background=c["field"], foreground=c["text"], insertbackground=c["text"],
                         selectbackground=c["selection"], selectforeground=c["text"],
                         highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"],
                         relief="flat", borderwidth=0, padx=6, pady=4)
