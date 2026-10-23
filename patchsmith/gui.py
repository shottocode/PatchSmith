"""Graphical interface (Tkinter, included in Python): choose the game's folder and press Translate.

Every operation ends with a dialog chosen by its outcome (outcome.py: translated, partly, not supported, game
open, translator stopped...) with the buttons for the next step. The log of every operation is also saved to
disk, in logs/ among the user's data (the last MAX_LOGS), with the technical details of the errors; "Create
report" puts them in a zip on the Desktop (report.py).
On opening, the games with new signals after the last session (crash, translator stopped, files put back by the
game...) are checked on their own (checks.py): the result is in the line under the folder, with "Details" (and
"Re-translate" for the lines translated while playing that need redoing).
"Fix translations…" opens the window with the list of the game's translations and their problems
(gui_corrections.py): the fixes saved there are written to the game on their own ("Reapply"); from there also start
the re-translation of the chosen texts, "Re-translate everything with Quality (12B)" and "Start over".
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

from . import (__version__, auto as automatic, checks, outcome as outcomes, icon,
               interruption, language, report, xunity_service)
from . import ui_language as ui, legacy, music, project, sounds, theme
from .backend import verify_translator
from .outcome import Outcome
from .project import data_folder
from .text import DETAIL, readable, slug

SETTINGS = None  # None = settings.json among the user's data (project.data_folder); the tests change it
SETTINGS_VERSION = 2
# Translator used by the interface. The "fake" test translator is only available from the command line
# (--backend finto) and for the automatic tests (App.forced_backend).
DEFAULT_BACKEND = "local"  # fallback: the real default is backend.default()
TRANSLATORS = {"local": "Local translator (on your PC)", "google": "Free Google (unofficial)",
              "deepl": "DeepL (with your free key)", "mymemory": "MyMemory (free)"}
GOOGLE_WARNING = ("Free Google uses the address of Google's web translator, which isn't made for programs: Google can "
                  "reject requests after a few phrases (from some PCs, from the very first) and block them for "
                  "hours.\n\nWhen this happens, PatchSmith stops on its own, pauses Google and saves what it has "
                  "already translated; next to the translator you'll see until when the pause lasts. If you then "
                  'choose another translator, "Analyze and translate" resumes from where you had got to: it only '
                  "translates the missing phrases.\n\nFor translating an entire game, it's best to use a local "
                  "translator (on your PC, with no limits) or DeepL with your free key.")
LOCAL_MODELS = {"4b": "Fast (4B)", "12b": "Quality (12B, slower)"}
# the preliminary checks (automatic.preflight_checks): title and type of the dialog
PREFLIGHT_WINDOWS = {"GAME_OPEN": ("The game is open", "warning"), "LANGUAGE": ("Language to check", "warning"),
                        "READ_ONLY": ("Protected game folder", "error"),
                        "DISK_SPACE": ("Low free space", "warning"),
                        "TRANSLATOR_PAUSED": ("Translator paused", "warning"),
                        "LOCAL_MISSING": ("Local translator to install", "question"),
                        "SAME_GAME": ("Same game already translated?", "question")}
_STOPPED = object()  # result of a work stopped with the "Stop" button
MAX_LOGS = 30  # operation logs kept on disk (the oldest are deleted)


def _settings():
    return SETTINGS or data_folder("settings.json")


def _notepad():
    """Windows Notepad, with the full path (never a notepad.exe from the current folder)."""
    return os.path.join(os.environ.get("SystemRoot") or r"C:\Windows", "System32", "notepad.exe")


def _open(path):
    """Opens the file with the default program; if Windows has none for that file type (e.g. .json on a new PC),
    with Notepad. OSError if neither works."""
    if sys.platform.startswith("win"):
        try:
            os.startfile(path)  # noqa: S606 - opens the file with the default program
        except OSError:
            subprocess.Popen([_notepad(), path])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def show_file(path):
    """Opens the folder with the file selected (File Explorer)."""
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        _open(os.path.dirname(path))


def logs_folder():
    return data_folder("logs")


def code_fingerprint():
    """(version written in patchsmith/__init__.py, most recent date of the program's files): if it changes while the
    window is open, PatchSmith was updated and the window still uses the old code."""
    folder = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(folder, "__init__.py"), encoding="utf-8") as f:
            m = re.search(r"__version__\s*=\s*[\"']([^\"']+)[\"']", f.read())
        version = m.group(1) if m else None
    except OSError:
        version = None
    dates = []
    for p in glob.glob(os.path.join(glob.escape(folder), "*.py")):
        try:
            dates.append(os.path.getmtime(p))
        except OSError:
            pass
    return version, max(dates, default=0)


def _fixed_label(n, singular, many):
    return "%d %s" % (n, singular if n == 1 else many)


def _music_note(master, color, on):
    """16x16 musical note drawn here (like the speaker): crossed out if the music is off."""
    img = tk.PhotoImage(master=master, width=16, height=16)
    periods = [(x, y) for x in range(2, 8) for y in range(10, 15)
               if ((x - 4.5) / 2.6) ** 2 + ((y - 12) / 1.9) ** 2 <= 1]
    periods += [(x, y) for x in (7, 8) for y in range(2, 12)]  # the stem
    periods += [(9, 3), (10, 3), (10, 4), (11, 4), (11, 5), (12, 6), (12, 7), (12, 8), (11, 9)]  # the flag
    if not on:
        periods += [(i, 15 - i) for i in range(1, 15)] + [(i, 14 - i) for i in range(1, 14)]
    for x, y in set(periods):
        img.put(color, (x, y))
    return img


def _speaker(master, color, is_on):
    """16x16 speaker drawn here (like the flags): with the waves if on, with the x if off."""
    img = tk.PhotoImage(master=master, width=16, height=16)
    periods = [(x, y) for x in range(1, 4) for y in range(6, 10)]  # the body
    periods += [(x, y) for x in range(4, 8) for y in range(6 - (x - 3), 10 + (x - 3))]  # the cone
    if is_on:
        for radius in (3.0, 5.6):  # two waves
            periods += [(x, y) for x in range(9, 16) for y in range(16)
                      if abs(((x - 7.5) ** 2 + (y - 7.5) ** 2) ** 0.5 - radius) < 0.6 and abs(y - 7.5) < radius * 0.8]
    else:
        periods += [(10 + i, 5 + i) for i in range(6)] + [(10 + i, 10 - i) for i in range(6)]
    for x, y in periods:
        img.put(color, (x, y))
    return img


class App:
    def __init__(self, root):
        self.root = root
        self.tail = queue.Queue()
        self._log_lines = []  # the window's log lines as written (to change language)
        self.work = None
        self._log_file = None  # log file of the running operation (logs/AAAAMMGG-HHMMSS-<gioco>.log)
        self._lock = threading.Lock()
        self._operation = None  # traduci / riapplica / ripristina / installa / rapporto
        self._retry = None  # for "Try again": repeats the last operation on the game
        self._translate_choices = {"force": False, "translate_anyway": False}  # of the last Analyze and translate
        self._in_error = False
        self.last_outcome = None
        self.check_outcomes = []  # outcomes of the last after-play check (one per game), for "Details"
        self._auto_check = False  # the running check is the one started on its own on opening
        # the loaded code: if the program's files change (update) the user is told to reopen the window
        self._code = code_fingerprint()
        self._warned_code = False
        # errors in the window's commands (pythonw has no console): in the log on disk and in a dialog
        root.report_callback_exception = self._ui_error
        root.title("PatchSmith")  # (the version is in the logs and in the reports)
        try:  # hammer and folder (icon.py) on this window and on those that open later
            icon.apply(root)
        except tk.TclError:  # damaged images: Tk's icon stays
            pass
        root.minsize(720, 480)
        prefs = self._read_settings()
        # interface language (flag at the top right): the texts are translated when the window shows them
        self.ui_lang = ui.set_(prefs.get("interface") or ui.default_language())
        ui.hook_tk()
        _translate_constants()
        self.theme_choice = prefs.get("theme", "auto")  # auto (like Windows) / dark / light
        self.sounds_on = bool(prefs.get("sounds", False))  # off by default (button with the speaker)
        self.music_on = bool(prefs.get("music", False))  # off by default (button with the note)
        self._sound_pending = False  # true at the end of an operation, until the first dialog (_dialog_sound)
        self._forced_sound = None  # the sound to use for the next dialog instead of the one of its type
        theme.ON_SHOW = self._dialog_sound
        # the click of every button, also in the other windows (dialogs, "Fix translations")
        root.bind_class("TButton", "<ButtonPress-1>", self._button_click, add="+")
        if prefs.get("version", 1) < 2:  # since 0.2.1 the game's language is detected on its own
            prefs["from"] = language.AUTO
        self.colors = theme.apply(root, self.theme_choice)

        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Game folder:").grid(row=0, column=0, sticky="w")
        self.folder = tk.StringVar(value=prefs.get("folder", ""))
        # only from "Browse…": no paths typed by hand (wrong or half-typed); the text can be copied
        self.folder_entry = ttk.Entry(frame, textvariable=self.folder, state="readonly")
        self.folder_entry.grid(row=0, column=1, sticky="ew", padx=6)
        # as wide as the column (that is, as "Create report"): the right edges are all aligned; as tall as the
        # folder field
        self.btn_browse = ttk.Button(frame, text="Browse…", style="Campo.TButton", command=self._browse)
        self.btn_browse.grid(row=0, column=2, sticky="nsew")
        # flag: interface language (the languages are the files in patchsmith/ui_language/)
        self._flags = {c: ui.flag(c, root) for c in ui.languages()}
        # (a drop-down drawn here: Windows menus have a light border even in the dark theme)
        corner = ttk.Frame(frame)  # under "Browse…": music, sounds, light/dark theme and language
        corner.grid(row=2, column=2, sticky="e", pady=(8, 0))
        self.btn_music = ttk.Button(corner, style="Bandiera.TButton", command=self._change_music)
        self.btn_music.pack(side="left", padx=(0, 4))
        self.btn_sounds = ttk.Button(corner, style="Bandiera.TButton", command=self._change_sounds)
        self.btn_sounds.pack(side="left", padx=(0, 4))
        self.btn_theme = ttk.Button(corner, style="Bandiera.TButton", command=self._change_theme)
        self.btn_theme.pack(side="left", padx=(0, 4))
        self.btn_language = ttk.Button(corner, image=self._flags.get(self.ui_lang),
                                     style="Bandiera.TButton", command=self._languages_menu)
        self.btn_language.pack(side="left")
        self._theme_symbol()
        self._sounds_symbol()
        self._music_symbol()
        self._language_dropdown = None

        # bottom row, under the log: on the left the buttons of the after-play check ("Last session details",
        # "Re-translate N lines"), visible only when there is something to do (the result is already in the
        # log), then the status line; "Create report" on the right. check_status stays the summary of the check
        # (for the tests and the log).
        self.bottom = ttk.Frame(frame)
        self.bottom.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        check_row = self.check_row = ttk.Frame(self.bottom)
        self.check_status = tk.StringVar(value="")
        self.btn_details = ttk.Button(check_row, text="Last session details",
                                       command=self._check_details)
        # lines translated while playing that need redoing: the check on opening does not re-translate them on its
        # own (loading the local translator or waiting for Google's pauses would block the window), this button does
        self.btn_retranslate = ttk.Button(check_row, text="Re-translate", command=self._retranslate)

        options = ttk.Frame(frame)
        options.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(options, text="From:").pack(side="left")
        # languages only from the list; a saved language that is not there (old settings) goes back to the default
        source_items, target_items = language.labels(with_auto=True), language.labels()
        # (in the menus the language names are in the interface language: from there _code goes back to the code)
        self._labels = {ui.translate(e): e for e in source_items}
        src_lang = language.label(prefs.get("from") or language.AUTO)
        self.src_lang = tk.StringVar(value=ui.translate(src_lang if src_lang in source_items
                                                        else language.label(language.AUTO)))
        ttk.Combobox(options, textvariable=self.src_lang, values=source_items, state="readonly",
                     width=22).pack(side="left", padx=(4, 12))
        ttk.Label(options, text="To:").pack(side="left")
        a = language.label(prefs.get("to") or language.windows_language())
        self.a = tk.StringVar(value=ui.translate(a if a in target_items else
                                                        language.label(language.windows_language())))
        ttk.Combobox(options, textvariable=self.a, values=target_items, state="readonly",
                     width=20).pack(side="left", padx=(4, 12))
        # forced_backend is for the automatic tests ("fake")
        self.forced_backend = None

        services = ttk.Frame(frame)
        services.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(services, text="Translator:").pack(side="left")
        self.deepl_key = prefs.get("deepl_key", "")
        self.email_mymemory = prefs.get("email_mymemory", "")
        from . import ollama
        from .backend import default as default_translator
        default = default_translator()
        chosen = prefs.get("backend") if prefs.get("backend") in TRANSLATORS else default
        self.translator = tk.StringVar(value=TRANSLATORS[chosen])
        self._chosen = chosen
        self.translator_choice = ttk.Combobox(services, textvariable=self.translator, values=list(TRANSLATORS.values()),
                                              state="readonly", width=32)
        self.translator_choice.pack(side="left", padx=(4, 8))
        self.translator_choice.bind("<<ComboboxSelected>>", lambda _e: self._change_translator(just_chosen=True))
        self.credential_label = ttk.Label(services, text="")
        self.credential = tk.StringVar()
        self.credential_entry = ttk.Entry(services, textvariable=self.credential, width=30)
        # tests the DeepL key or the MyMemory email right away, without waiting for "Analyze and translate"
        self.btn_verify = ttk.Button(services, text="Check", command=self._verify_credential)
        self._warned_google = False
        self.translator_status = ttk.Label(services, text="", style="Stato.TLabel")
        self.btn_reactivate = ttk.Button(services, text="Reactivate", command=self._reactivate)
        # the model: the saved one, otherwise the best one already downloaded; with neither, the one suited to the
        # graphics card is proposed (ollama.recommended_model, looked up in the background: _suggest_model)
        self._model_chosen = prefs.get("local_model") in LOCAL_MODELS
        model = prefs.get("local_model") if self._model_chosen else ollama.default_model()
        self._model_recommendation = None  # (chiave, motivo) from the graphics card
        self._local_probe = None  # the last test of the local translator (backend.probe_local)
        self._then = None  # to redo after installing the local translator (e.g. "Analyze and translate")
        self.local_model = tk.StringVar(value=LOCAL_MODELS[model])
        self.model_choice = ttk.Combobox(services, textvariable=self.local_model,
                                           values=list(LOCAL_MODELS.values()), state="readonly", width=22)
        self.model_choice.bind("<<ComboboxSelected>>", lambda _e: self._choose_model())
        self.btn_install = ttk.Button(services, text="Install…", command=self._install_local)
        self._change_translator(first=True)
        self.root.after(60_000, lambda: self._update_service_status(repeat=True))
        if not self._model_chosen and not any(ollama.model_present(k) for k in LOCAL_MODELS):
            threading.Thread(target=lambda: self.tail.put(("model", ollama.recommended_model())),
                             daemon=True).start()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        self.btn_translate = ttk.Button(buttons, text="Analyze and translate", command=self._translate)
        self.btn_translate.pack(side="left")
        self.btn_fix = ttk.Button(buttons, text="Fix translations…", command=self._fix)
        self.btn_fix.pack(side="left", padx=6)
        self.btn_restore = ttk.Button(buttons, text="Restore originals", command=self._restore)
        self.btn_restore.pack(side="right")
        # enabled only while an operation is running: it stops at the first safe point (translations saved)
        self.btn_stop = ttk.Button(buttons, text="Stop", command=self._stop)
        self.btn_stop.pack(side="right", padx=6)
        self.btn_stop.state(["disabled"])
        # disabled while a work is running: never two works at once (nor translator changes halfway)
        self.buttons = [self.btn_translate, self.btn_fix, self.btn_restore,
                         self.btn_install, self.btn_reactivate, self.translator_choice, self.model_choice,
                         self.btn_retranslate]

        # progress bar: visible only while an operation is running
        self.bar = ttk.Progressbar(frame, mode="indeterminate")
        self.bar.grid(row=5, column=0, columnspan=3, sticky="ew")
        self.bar.grid_remove()

        log_file = ttk.Frame(frame)
        log_file.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        frame.rowconfigure(6, weight=1)
        self.text = tk.Text(log_file, wrap="word", height=18, font=("Consolas", 9), state="disabled")
        theme.color_text(self.text, self.colors)
        scrollbar = ttk.Scrollbar(log_file, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.state = ui.TextVar(value="")
        self.status_label = ttk.Label(self.bottom, textvariable=self.state, style="Stato.TLabel")
        self.status_label.pack(side="left")
        # while the game is open with its translator on (see _watch_live)
        self.btn_stop_live = ttk.Button(self.bottom, text="Stop the game translator",
                                         command=self._stop_live)
        self._live_on = None  # name of the game with the translator on, the last time it was checked
        self._journal_read = {}  # game -> how far its journal has already been summarized in the log
        # logs, settings (without key or email) and useful files in a zip on the Desktop, to send to whoever helps
        self.btn_report = ttk.Button(frame, text="Create report", command=self._create_report)
        self.btn_report.grid(row=7, column=2, sticky="e", pady=(6, 0))
        self.buttons.append(self.btn_report)

        self.root.after(100, self._drain_queue)
        self.root.after(3000, self._watch_live)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(500, self._initial_check)
        if self.music_on:  # on last time: it starts again
            music.launch()

    def _close(self):
        """Do not close halfway through an installation or a write to the game's files."""
        if self.work is not None and self.work.is_alive():
            if not theme.ask(self.root, self.colors, "Operation in progress",
                               "PatchSmith is still working on the game files.\nIf you close now, the operation is "
                               "left half-done. Close anyway?"):
                return
        self._save_settings()  # chosen translator, key/email, folder and languages: found again on reopening
        music.stop()
        self.root.destroy()

    # --- settings ------------------------------------------------------------------------------

    def _read_settings(self):
        try:
            with open(_settings(), encoding="utf-8") as f:
                return legacy.load(f)
        except (OSError, ValueError):
            return {}

    def _change_translator(self, first=False, just_chosen=False):
        """Shows the right field (DeepL key, email for MyMemory) and the status of the chosen translator. just_chosen:
        chosen just now from the menu (for Google, once per session, the dialog that explains the blocks)."""
        if not first:  # keeps what was written in the previous translator's field
            if self._chosen == "deepl":
                self.deepl_key = self.credential.get().strip()
            elif self._chosen == "mymemory":
                self.email_mymemory = self.credential.get().strip()
        self._chosen = next((k for k, v in TRANSLATORS.items() if v == self.translator.get()), DEFAULT_BACKEND)
        for w in (self.credential_label, self.credential_entry, self.btn_verify, self.translator_status,
                  self.btn_reactivate, self.model_choice, self.btn_install):
            w.pack_forget()
        if self._chosen == "deepl":
            self.credential_label.configure(text="DeepL key:")
            self.credential.set(self.deepl_key)
            self.credential_entry.configure(show="•")
        elif self._chosen == "mymemory":
            self.credential_label.configure(text="Email (optional):")
            self.credential.set(self.email_mymemory)
            self.credential_entry.configure(show="")
        if self._chosen in ("deepl", "mymemory"):
            self.credential_label.pack(side="left")
            self.credential_entry.pack(side="left", padx=(4, 4))
            self.btn_verify.pack(side="left", padx=(0, 8))
        if self._chosen == "local":
            self.model_choice.pack(side="left", padx=(0, 8))
        self._update_service_status()
        if just_chosen:
            self._save_settings()
        if just_chosen and self._chosen == "google" and not self._warned_google:
            self._warned_google = True
            theme.warn(self.root, self.colors, "Free Google: it may get blocked", GOOGLE_WARNING)

    def _verify_credential(self):
        """"Check" button: the DeepL key or the MyMemory email, in the background (one small request)."""
        name = self._chosen
        value_ = "".join(self.credential.get().split()) if name == "deepl" else self.credential.get().strip()
        if name == "deepl":
            self.deepl_key = value_
            if not value_:
                theme.warn(self.root, self.colors, "DeepL key", "First, paste your DeepL key in the field next to "
                                                                   'it. You can get one for free with a "DeepL API '
                                                                   'Free" account (500,000 characters per month).')
                return
        elif name == "mymemory":
            self.email_mymemory = value_
        self._save_settings()
        self.btn_verify.state(["disabled"])
        self.btn_verify.configure(text="Checking…")

        def _work():
            self.tail.put(("verify", (name, verify_translator(name, value_))))

        threading.Thread(target=_work, daemon=True).start()

    def _finish_verify(self, name, result):
        self.btn_verify.state(["!disabled"])
        self.btn_verify.configure(text="Check")
        ok, message = result
        title_text = {"deepl": "DeepL key", "mymemory": "MyMemory"}.get(name, "Check")
        if ok:
            theme.inform(self.root, self.colors, title_text, message)
        else:
            theme.error(self.root, self.colors, title_text, message)

    def _model_key(self):
        return next((k for k, v in LOCAL_MODELS.items() if v == self.local_model.get()), "4b")

    def _choose_model(self):
        self._model_chosen = True  # chosen by the user: the graphics card no longer changes it
        self._update_service_status()

    def _suggest_model(self, recommendation, sync=False):
        """The model suited to the graphics card (ollama.recommended_model: (chiave, motivo)), if the user has not
        chosen one and none is downloaded: the 12B with at least 8 GB of video memory, otherwise the 4B."""
        from . import ollama
        self._model_recommendation = recommendation
        if self._model_chosen or (self._busy() and not sync) or \
                any(ollama.model_present(k) for k in LOCAL_MODELS):
            return
        key = recommendation[0] if recommendation and recommendation[0] in LOCAL_MODELS else None
        if key and self.local_model.get() != LOCAL_MODELS[key]:
            self.local_model.set(LOCAL_MODELS[key])
            self._update_service_status()

    def _busy(self):
        return self.work is not None and self.work.is_alive()

    def _install_local(self, then=None):
        """Guided installation of the local translator: proposes the model suited to the graphics card, lists what it
        downloads and starts only after the yes. At the end a test sentence (_install_and_test) tells whether it works
        and how fast it is; then (if it succeeds) runs 'poi', for example the "Analyze and translate" that was waiting
        for the translator."""
        from . import ollama
        if self._busy():
            return
        if not self._model_chosen and not any(ollama.model_present(k) for k in LOCAL_MODELS):
            self._suggest_model(self._model_recommendation or ollama.recommended_model(), sync=True)
        selected = [self._model_key()]
        lines = ollama.download_description(selected)
        if not lines:
            self._update_service_status()
            if then:
                then()
            return
        recommendation = ""
        if self._model_recommendation and not ollama.model_present(selected[0]):
            recommendation = ('\n\n%s "%s" is selected now; for the other one, select it in the menu next to it and '
                              'press "Install…" again.' % (self._model_recommendation[1], LOCAL_MODELS[selected[0]]))
        try:
            slow = ollama.performance_warning()
        except Exception:  # noqa: BLE001 - only a warning: it must not prevent the installation
            slow = ""
        text = ("The local translator needs these downloads, saved in %s:\n\n- %s\n\nNo limits, no restrictions: after "
                "installation, it translates even without an internet connection.%s%s\n\nShall I proceed?"
                 % (ollama.folder(), "\n- ".join(lines), recommendation, "\n\nWarning: " + slow if slow else ""))
        if theme.ask(self.root, self.colors, "Install the local translator?", text):
            self._then = then
            a = self._target_lang()
            self._start("Installing the local translator", lambda _c: self._install_and_test(selected, a),
                        stoppable=True, needs_folder=False, operation="install")

    def _install_and_test(self, selected, a="it"):
        """(in the background work) Installs and then translates a test sentence, measuring the times: the result
        (backend.probe_local) goes to the status line at the bottom."""
        from . import backend, ollama
        ollama.install(selected, self._log)
        self._log('Testing the local translator: translating "Start game" (the first time, the model loads into the '
                  "graphics card: it can take a minute)…")
        result = backend.probe_local(selected[0], self._log, a=a)
        self._log(("  " if result["ok"] else "  ! ") + result["message"])
        return result

    def _update_service_status(self, repeat=False):
        """Next to the translator: whether it is paused after a block (until when) or disabled (Reactivate button);
        for the local translator whether it is ready or to install. With ripeti=True it refreshes every minute."""
        from .limiter import Limiter
        self.translator_status.pack_forget()
        self.btn_reactivate.pack_forget()
        self.btn_install.pack_forget()
        if self._chosen == "local":
            from . import ollama
            is_ready = ollama.installed() and ollama.model_present(self._model_key())
            slow = is_ready and (self._local_probe or {}).get("gpu") == 0  # the test: processor only
            # installed: no text (only the warning if it uses the processor); otherwise "to install" with "Install…"
            text = ("slow: without a graphics card" if slow else "") if is_ready else "to install"
            self.translator_status.configure(text=text)
            if text:
                self.translator_status.pack(side="left")
            if not is_ready:
                self.btn_install.pack(side="left", padx=(6, 0))
            if repeat:
                self.root.after(60_000, lambda: self._update_service_status(repeat=True))
            return
        short = Limiter(self._chosen, log=None).short_status()
        if short:
            self.translator_status.configure(text=short)
            self.translator_status.pack(side="left")
            if short.startswith("disabled"):
                self.btn_reactivate.pack(side="left", padx=(6, 0))
        if repeat:
            self.root.after(60_000, lambda: self._update_service_status(repeat=True))

    def _reactivate(self):
        from .limiter import NAMES, Limiter
        name = NAMES.get(self._chosen, self._chosen)
        if theme.ask(self.root, self.colors, "Reactivate %s?" % name,
                       "%s has rejected the requests several times. If you reactivate it and it keeps rejecting them, "
                       "insisting may extend the block. Reactivate?" % name):
            Limiter(self._chosen, log=None).reset()
            self._update_service_status()

    def _save_settings(self):
        if self._chosen == "deepl":  # no spaces or newlines pasted by mistake inside the key
            self.deepl_key = "".join(self.credential.get().split())
        elif self._chosen == "mymemory":
            self.email_mymemory = self.credential.get().strip()
        labels = getattr(self, "_labels", {})
        data = {"folder": self.folder.get(),
                "from": language.code(labels.get(self.src_lang.get(), self.src_lang.get())),
                "to": self._target_lang(),
                "backend": self._chosen, "version": SETTINGS_VERSION,
                "theme": self.theme_choice, "sounds": getattr(self, "sounds_on", False),
                "music": getattr(self, "music_on", False),
                "deepl_key": self.deepl_key, "email_mymemory": self.email_mymemory,
                "local_model": self._model_key(), "interface": getattr(self, "ui_lang", ui.current())}
        try:
            os.makedirs(os.path.dirname(_settings()), exist_ok=True)
            with open(_settings(), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _language_code(self, label):
        """The language code from a label of the "From" or "To" menu (also translated into the interface language)."""
        return language.code(self._labels.get(label, label))

    def _theme_symbol(self):
        """On the theme button the symbol of the current theme: moon = dark, sun = light."""
        self.btn_theme.configure(text="☾" if self.colors.get("name") == "dark" else "☀")

    def _change_theme(self):
        """Sun/moon button: switches from the dark theme to the light one and vice versa, at once, and remembers it."""
        self.theme_choice = "light" if self.colors.get("name") == "dark" else "dark"
        self.colors = theme.apply(self.root, self.theme_choice)
        theme.color_text(self.text, self.colors)
        self._theme_symbol()
        self._sounds_symbol()  # (the speaker and the note are drawn with the text color)
        self._music_symbol()
        self._close_dropdown()
        self._save_settings()

    def _sounds_symbol(self):
        """On the sounds button the speaker with the waves (on) or with the x (off), in the text color."""
        self._sounds_img = _speaker(self.root, self.colors["text"], self.sounds_on)
        self.btn_sounds.configure(image=self._sounds_img)

    def _change_sounds(self):
        """Button with the speaker: turns the sounds on or off (turning them on plays the click) and remembers it."""
        self.sounds_on = not self.sounds_on
        self._sounds_symbol()
        self._close_dropdown()
        self._save_settings()
        if self.sounds_on:
            sounds.play("click")

    def _music_symbol(self):
        """On the music button the note (on) or the crossed-out note (off), in the text color."""
        self._music_img = _music_note(self.root, self.colors["text"], self.music_on)
        self.btn_music.configure(image=self._music_img)

    def _change_music(self):
        """Button with the note: turns the background music (music.py) on or off and remembers it."""
        self.music_on = not self.music_on
        self._music_symbol()
        self._close_dropdown()
        self._save_settings()
        if self.music_on:
            music.launch()
        else:
            music.stop()

    def _button_click(self, event):
        """With the sounds on, the click when a button is pressed (not a disabled one)."""
        try:
            active = not event.widget.instate(["disabled"])
        except (AttributeError, tk.TclError):
            return
        if self.sounds_on and active:
            sounds.play("click")

    def _dialog_sound(self, kind):
        """With the sounds on every dialog plays a sound, at any moment: "error" for errors, "question" for
        questions, warnings and information (also the Google warning when you choose it). The first dialog at the end
        of an operation, if it is information, plays "done" instead; so does a translation written to the game only
        in part (a warning, but the game is translated: _show_window)."""
        forced, self._forced_sound = self._forced_sound, None
        end, self._sound_pending = self._sound_pending, False
        if not self.sounds_on:
            return
        if kind == "error":
            sounds.play("error")
        elif end and (forced or kind == "info"):
            sounds.play(forced or "done")
        else:
            sounds.play("question")

    def _languages_menu(self):
        """The language drop-down under the flag, with the theme's colors (border included)."""
        if self._language_dropdown is not None:
            self._close_dropdown()
            return
        c = self.colors
        t = self._language_dropdown = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.configure(background=c["border"])
        inside = tk.Frame(t, background=c["field"])
        inside.pack(padx=1, pady=1)
        self._language_entries = []
        for code, info in ui.languages().items():
            entry = tk.Label(inside, image=self._flags[code], text=ui.VERBATIM + " %s (%s)" % (
                info["name"], code), compound="left", anchor="w", padx=8, pady=3, background=c["field"],
                foreground=c["text"], cursor="hand2")
            entry.pack(fill="x")
            entry.bind("<Enter>", lambda _e, v=entry: v.configure(background=c["selection"]))
            entry.bind("<Leave>", lambda _e, v=entry: v.configure(background=c["field"]))
            entry.bind("<Button-1>", lambda _e, k=code: (self._close_dropdown(), self._change_ui(k)))
            self._language_entries.append((code, entry))
        t.update_idletasks()
        b = self.btn_language
        x = b.winfo_rootx() + b.winfo_width() - t.winfo_reqwidth()  # right-aligned with the button
        t.geometry("+%d+%d" % (x, b.winfo_rooty() + b.winfo_height() + 2))
        t.bind("<Escape>", lambda _e: self._close_dropdown())
        t.bind("<FocusOut>", lambda _e: self.root.after(150, self._close_dropdown))
        t.focus_force()

    def _close_dropdown(self):
        if self._language_dropdown is not None:
            try:
                self._language_dropdown.destroy()
            except tk.TclError:
                pass
            self._language_dropdown = None

    def _change_ui(self, code):
        """The flag: the window switches at once to the chosen language (log included), which stays in the settings.
        Without reopening PatchSmith: the original texts are remembered in the widgets (ui.retranslate)."""
        if code == self.ui_lang:
            return
        if self._busy():
            theme.warn(self.root, self.colors, "Operation in progress",
                       "Wait for the process to finish, then change the language.")
            return
        # the choices of the drop-down menus as codes, before their labels change
        src_lang, a, model = (self._language_code(self.src_lang.get()), self._language_code(self.a.get()),
                              self._model_key())
        self.ui_lang = ui.set_(code)
        _translate_constants()
        ui.retranslate(self.root)
        self._labels = {ui.translate(e): e for e in language.labels(with_auto=True)}
        self.src_lang.set(ui.translate(language.label(src_lang)))
        self.a.set(ui.translate(language.label(a)))
        # (the names of the translators and models reached the menus already translated: they are set again here)
        self.translator_choice.configure(values=list(TRANSLATORS.values()))
        self.translator.set(TRANSLATORS[self._chosen])
        self.model_choice.configure(values=list(LOCAL_MODELS.values()))
        self.local_model.set(LOCAL_MODELS[model])
        self.btn_language.configure(image=self._flags.get(self.ui_lang))
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", "".join(self._shown_line(r) + "\n" for r in self._log_lines))
        self.text.see("end")
        self.text.configure(state="disabled")
        self._save_settings()

    def _target_lang(self):
        a = language.code(getattr(self, "_labels", {}).get(self.a.get(), self.a.get()))
        return language.windows_language() if a == language.AUTO else a

    def _backend(self):
        return self.forced_backend or self._chosen

    # --- log and background work ------------------------------------------------------------

    def _log(self, message):
        original = str(message)
        line = readable(original)
        if DETAIL in line:  # technical detail: only in the log on disk (and in "Create report")
            self._write_log(line.replace(DETAIL, ""))
            return
        self.tail.put(("log", original))  # in the window in the interface language (_shown_line)
        self._write_log(line)  # on disk as originally written

    def _shown_line(self, original):
        """A log line as seen in the window: translated, or in Italian with the accents."""
        if self.ui_lang != ui.SOURCE:
            return ui.translate(original)
        return readable(original)

    def _open_log(self, folder, description, name=None):
        """New log on disk for the operation: logs/AAAAMMGG-HHMMSS-<nome>.log, with the name of the game
        profile (or of its folder); without a folder 'interface' (window errors outside an operation) or the given
        name. The oldest beyond MAX_LOGS are deleted. Without a log the work goes on anyway: there must never be
        an error here (the buttons are already disabled)."""
        self._close_log()
        if not name:
            name = slug(os.path.basename(os.path.normpath(folder))) if folder else "interfaccia"
            try:  # a damaged game profile is reported later by the operation, with its dialog
                profile = automatic.find_profile(folder) if folder and os.path.isdir(folder) else None
                if profile:
                    name = os.path.splitext(os.path.basename(profile))[0]
            except Exception:  # noqa: S110
                pass
        now = datetime.datetime.now()
        try:
            header = "PatchSmith %s - %s - %s\nFolder: %s\nTranslator: %s, languages: %s -> %s\n" % (
                __version__, description, now.strftime("%Y-%m-%d %H:%M:%S"), folder or "-", self._backend(),
                self._language_code(self.src_lang.get()), self._target_lang())
        except Exception:  # noqa: BLE001 - the header must not prevent the log
            header = "PatchSmith %s - %s - %s\n" % (__version__, description, now)
        try:
            os.makedirs(logs_folder(), exist_ok=True)
            path = os.path.join(logs_folder(), "%s-%s.log" % (now.strftime("%Y%m%d-%H%M%S"), name))
            f = open(path, "a", encoding="utf-8")
            f.write(header)
            f.flush()
        except OSError:
            return
        with self._lock:
            self._log_file = f
        for old in sorted(glob.glob(os.path.join(glob.escape(logs_folder()), "*.log")))[:-MAX_LOGS]:
            try:
                os.remove(old)
            except OSError:
                pass

    def _write_log(self, text):
        with self._lock:
            if self._log_file is None:
                return
            try:
                self._log_file.write(text + "\n")
                self._log_file.flush()
            except (OSError, ValueError):
                pass

    def _close_log(self):
        with self._lock:
            if self._log_file is not None:
                try:
                    self._log_file.close()
                except OSError:
                    pass
            self._log_file = None

    def log_path(self):
        """The log on disk of the running operation (or None)."""
        with self._lock:
            return self._log_file.name if self._log_file is not None else None

    def _drain_queue(self):
        try:
            while True:
                kind, value_ = self.tail.get_nowait()
                if kind == "log":
                    self._log_lines.append(value_)
                    self.text.configure(state="normal")
                    self.text.insert("end", self._shown_line(value_) + "\n")
                    self.text.see("end")
                    self.text.configure(state="disabled")
                elif kind == "end":
                    self._finish(*value_)
                elif kind == "checks":
                    self._start_initial_check(value_)
                elif kind == "model":
                    self._suggest_model(value_)
                elif kind == "verify":
                    self._finish_verify(*value_)
        except queue.Empty:
            pass
        finally:  # even if an end-of-operation dialog fails, the window keeps reading the queue
            self.root.after(100, self._drain_queue)

    def _ui_error(self, kind, value_, tb):
        """Error in a window command: the technical detail goes to the log on disk, the user gets a dialog with the
        type of error and the offer to create a report (never "Try again": it would repeat the last operation on the
        game, which has nothing to do with this error)."""
        text = "".join(traceback.format_exception(kind, value_, tb))
        is_open = self._log_file is not None
        if not is_open:
            self._open_log("", "Error in the interface", "interfaccia")
        self._write_log("UNEXPECTED INTERFACE ERROR (%s: %s)\n%s" % (kind.__name__, value_, text))
        if not is_open:
            self._close_log()
        if self._in_error or not issubclass(kind, Exception):
            return
        self._in_error = True
        try:
            e = Outcome("ERROR", str(value_).strip(), actions=["create_report"],
                      extra={"unexpected": True, "kind": kind.__name__})
            self.last_outcome = e
            self.state.set("Unexpected error: %s" % kind.__name__)
            self._show_window(e, "interface")
        except Exception:  # noqa: S110 - an error here too: it is already in the log, no further attempt
            pass
        finally:
            self._in_error = False

    def _start(self, description, func, stoppable=False, needs_folder=True, operation=None, keep_log=True,
               log_name=None):
        """Runs func(cartella) in a thread. operazione: traduci/riapplica/ripristina/installa/rapporto/controlla
        (chooses the final dialog); registra=False: without a log on disk (e.g. creating the report);
        log_name: the end of the log's name (default: the game)."""
        folder = self.folder.get().strip().strip('"')
        if self._busy():  # one work at a time: reset() would cancel a "Stop" still in progress
            return
        if needs_folder and (not folder or not os.path.isdir(folder)):
            theme.warn(self.root, self.colors, "Missing folder", "Choose a valid game folder.")
            return
        self._check_code()
        self._operation = operation
        self._save_settings()
        for b in self.buttons:
            b.state(["disabled"])
        interruption.reset()
        self.btn_stop.state(["!disabled"] if stoppable else ["disabled"])  # only where it can be stopped
        self.bar.grid()
        self.bar.start(12)
        self.state.set(description + "…")
        if keep_log:
            self._open_log(folder if needs_folder else "", description,
                                log_name or ("traduttore-locale" if operation == "install" else None))
        self._log("\n" + "=" * 70 + "\n" + description + ": " + folder)

        def _run():
            try:
                result = func(folder)
                self.tail.put(("end", (description, result, None)))
            except interruption.Interrupted:
                self.tail.put(("end", (description, _STOPPED, None)))
            except Exception as e:  # the error must be shown to the user, it must not close the app
                # the technical detail (traceback) only in the log on disk: it is for whoever helps, not for the player
                self._write_log("ERROR (%s: %s)\n%s" % (type(e).__name__, e, traceback.format_exc()))
                self.tail.put(("end", (description, None, e)))

        self.work = threading.Thread(target=_run, daemon=True)
        self.work.start()

    def _stop(self):
        interruption.request_stop()
        self.btn_stop.state(["disabled"])
        self.state.set("Stopping as soon as possible (the translations done so far stay saved)…")
        self._log("Stop requested: stopping at the first safe point.")

    def _finish(self, description, result, error):
        # at the end of an operation the first dialog plays a sound (if sounds are on; not the check on opening)
        self._sound_pending = not (self._operation == "check" and self._auto_check)
        try:
            self._finish_operation(description, result, error)
        finally:
            self._sound_pending = False

    def _finish_operation(self, description, result, error):
        self.bar.stop()
        self.bar.grid_remove()
        self.btn_stop.state(["disabled"])
        self._update_service_status()  # a block just received shows at once next to the translator
        for b in self.buttons:
            b.state(["!disabled"])
        operation = self._operation
        then = None  # the operation that was waiting for the local translator's installation (only if it succeeded)
        if operation == "install":
            then, self._then = self._then, None
        if result is _STOPPED:
            if description.startswith("Installing"):
                self.state.set(description + ": stopped. 'Install…' resumes the download where it left off.")
            else:
                self.state.set(description + ": stopped. The translations done are saved: 'Analyze and translate' "
                                             "resumes from there.")
            self._log("Stopped.")
            self._close_log()
            return
        if error:
            e = outcomes.from_exception(error)
            if getattr(error, "from_scratch", False):  # "Start over" did not start (see _translate)
                e.extra["from_scratch"] = True
            self._log("Unexpected error (%s): %s" % (type(error).__name__, error) if e.extra.get("unexpected")
                      else "ERROR: %s" % error)
            if getattr(error, "command", None):
                self._write_log("(from command line: %s)" % error.command)
            self._write_log(e.summary())
            self._close_log()
            self.last_outcome = e
            self.state.set("%s: %s" % (description, outcomes.STATE[e.code]))
            if operation == "check" and self._auto_check:  # on opening: no error dialogs
                self.check_status.set("Check after the last session failed (the details are in the log): you can "
                                      "create a report.")
                return
            self._show_window(e, operation)
            return
        if operation == "check" and isinstance(result, list):
            for e in result:
                self._write_log(e.summary())
            self._close_log()
            self.check_outcomes = result
            self.last_outcome = result[-1] if result else None
            self._update_check_line()
            self.state.set("")  # the result is in the log (and in the bottom buttons, if there is something to do)
            if not self._auto_check:
                self._show_check()
            return
        if operation == "install" and isinstance(result, dict):  # installed, with the test sentence
            self._close_log()
            self._local_probe = result
            self._update_service_status()
            self.state.set("Local translator installed: %s" % result["message"])
            if then and result.get("ok"):  # again the operation that was waiting for the local translator
                self.root.after(100, then)
            elif not result.get("ok"):  # after downloads of a few GB the bottom line is not enough
                text = "The local translator is installed, but %s" % result["message"]
                if then:
                    text += ("\n\nThat's why the operation that was waiting for the translator didn't start: press its "
                             "button again to try again.")
                if "report" not in text:
                    text += "\n\nIf it happens again, create a report (button in the bottom right)."
                theme.warn(self.root, self.colors, "Local translator test failed", text)
            return
        if operation == "report" and result:
            self.state.set("Report created: %s" % result)
            choice = theme.choose(self.root, self.colors, "Report generated",
                                 "I've created the report:\n%s\n\nIt contains the logs and settings that will help you "
                                 "understand what happened, without the DeepL key or your email address. You can send "
                                 "it to whoever is helping you with PatchSmith." % result,
                                 [("OK", None), ("Open the folder", "open")])
            if choice == "open":
                show_file(result)
            return
        if isinstance(result, Outcome):
            self._write_log(result.summary())
            self._close_log()
            self._show_outcome(description, result)
            return
        self._close_log()
        self.state.set(description + ": completed.")

    def _local_ready(self):
        from . import ollama
        try:
            return ollama.state() == "ready"
        except OSError:
            return False

    def _show_window(self, e, operation):
        """The end-of-operation dialog for the outcome (text and buttons from esito.finestra) and the chosen action."""
        data = outcomes.window(e, operation or "translate", local_ready=self._local_ready(),
                              current=self._backend())
        if e.code == "PARTIAL" and e.written and operation != "restore":  # translated, with a few texts left
            self._forced_sound = "done"
        choice = theme.choose(self.root, self.colors, data["title"], data["text"], data["buttons"], data["kind"])
        self._action(choice, e)
        return data

    def _show_outcome(self, description, e):
        """End of Analyze and translate / Reapply / Restore: one dialog for each outcome, with the next step."""
        self.last_outcome = e
        x = e.extra
        if e.code == "OFFICIAL" and x.get("official_language"):  # official language present (official_language.py)
            lines = x.get("lines") or [e.reason]
            if x.get("needs_consent"):
                self.state.set("The game already has its official language: you need to give your consent to activate "
                               "it.")
                if theme.ask(self.root, self.colors, "Activate the game's language?",
                               "\n\n".join(lines) + "\n\nShall I proceed? (I'm only asking you this once, specifically "
                                                    "for this game.)"):
                    self._translate(force=x.get("force", False), consent=True)
                return
            if not x.get("is_supported", True):
                self.state.set("Official language activated." if x.get("is_set") else
                               "The game already has its official language.")
                theme.inform(self.root, self.colors, "Official language", "\n\n".join(lines))
                return
            self.state.set("The game already has its official language.")
            if theme.ask(
                self.root, self.colors, "Official language available",
                "\n\n".join(lines) + "\n\nDo you still want an automatic translation? (Usually, this isn't necessary: "
                                     "the official one is better)",
            ):
                self._translate(force=x.get("force", False), translate_anyway=True)
            return
        if e.code == "NOT_SUPPORTED" and "experimental" in e.actions:
            self.state.set("%s: %s" % (description, outcomes.STATE[e.code]))
            choice = theme.choose(
                self.root, self.colors, "Engine not yet supported",
                "This game's engine (%s) is not supported automatically yet.\n\nHowever, I found files with text. "
                "Would you like to try translating them experimentally?\n(The original files are still backed up, "
                "though.)" % x.get("detected_engine", "not recognized"),
                [("Yes", "experimental"), ("No", None), ("Create report", "create_report")], "question")
            if choice == "experimental":
                self._translate(force=True, translate_anyway=x.get("translate_anyway", False))
            else:
                self._action(choice, e)
            return
        self.state.set("%s: %s" % (description, outcomes.STATE[e.code]))
        if self._operation == "translate" and e.written and e.live is not None and e.engine in ("unity", "web"):
            self._ask_live(e)
        self._show_window(e, self._operation)

    # --- live translation (while playing) -------------------------------------------------------------------------

    def _ask_live(self, e):
        """At the end of "Analyze and translate": keep the translator on while playing? The choice stays in the game
        profile (automatic.set_live) and holds until the next "Analyze and translate"."""
        folder = self.folder.get().strip().strip('"')
        try:
            _profile, enable = automatic.live_translation(folder)
        except Exception:  # noqa: BLE001 - only an extra question
            enable = None
        if enable is None:
            return
        choice = theme.choose(
            self.root, self.colors, "Translation while playing",
            "The phrases found now are already translated. While you play, PatchSmith can keep its translator on to "
            "also translate the new phrases that appear (the first time they show in English for a moment).\n\nIt "
            "starts with the game and closes on its own when you close the game. It uses the graphics card (about 3 GB "
            "of memory) only while you play. While it's on, below you'll see from time to time what it has translated, "
            "and you can stop it with a button.\n\nKeep it on?",
            [("Yes, keep it on", True), ("No, only the saved translations", False)], "question")
        if choice is None:  # dialog closed: it stays as it was
            return
        if choice != enable:
            try:
                automatic.set_live(folder, choice, self._log)
            except Exception as ex:  # noqa: BLE001 - the translation is already in the game: say so and go on
                self._log(("! I couldn't turn on the translation while you play (%s): try again with 'Analyze and "
                           "translate'." if choice else "! I couldn't turn off the translation while you play (%s): "
                           "try again with 'Analyze and translate'.") % ex)
                return
        if not choice:
            e.live = "none"

    def _watch_live(self):
        """Every 5 seconds: if the game of the chosen folder is open with its translator on, the button to stop it
        and, in the log, the new lines translated since the last time."""
        try:
            self._check_live()
        except Exception as e:  # noqa: BLE001 - only a help: it must never disturb the window
            self._write_log("(game translator monitoring: %s: %s)" % (type(e).__name__, e))
        finally:
            self.root.after(5000, self._watch_live)

    def _check_live(self):
        folder = self.folder.get().strip().strip('"')
        profile, enable = automatic.live_translation(folder) if folder and os.path.isdir(folder) else \
            (None, None)
        name = profile.get("name") if profile and enable else None
        if name and name not in self._journal_read:  # the earlier history is not repeated: only the lines from now on
            try:
                self._journal_read[name] = os.path.getsize(xunity_service.journal_path(name))
            except OSError:
                self._journal_read[name] = 0
        is_on = bool(name) and xunity_service.is_on(name, wait=0.2)
        if is_on:
            if not self.btn_stop_live.winfo_manager():
                self.btn_stop_live.pack(side="left", padx=(0, 10), before=self.status_label)
            if self._live_on != name:
                self._log("The game is open with the PatchSmith translator on: here you'll see, every now and then, "
                          "the new phrases it translates.")
        else:
            self.btn_stop_live.pack_forget()
        if name:
            self._summarize_journal(name)
        if self._live_on and not is_on:
            self._log("The game translator has closed (game closed or stopped).")
        self._live_on = name if is_on else None

    def _summarize_journal(self, name):
        """The new lines of the translator's journal since the last reading, in one log line."""
        path = xunity_service.journal_path(name)
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        was_read = self._journal_read.get(name, 0)
        if size < was_read:  # journal restarted (too big): from the beginning
            was_read = 0
        if size == was_read:
            return
        with open(path, "rb") as f:
            f.seek(was_read)
            data = f.read(size - was_read)
        end = data.rfind(b"\n") + 1  # only whole lines: the rest at the next reading
        self._journal_read[name] = was_read + end
        new_lines, remaining_count = [], 0
        for line in data[:end].decode("utf-8", "replace").splitlines():
            try:
                r = legacy.loads(line)
            except ValueError:
                continue
            if r.get("outcome") == "ok" and r.get("backend") not in ("memory", "shared", "already_translated", None):
                new_lines.append((r.get("text", ""), r.get("response", "")))
            elif r.get("outcome") in ("rejected", "error"):
                remaining_count += 1
        if not new_lines and not remaining_count:
            return
        text = "While playing: %s" % _fixed_label(len(new_lines), "new phrase translated", "new phrases translated")
        if new_lines:
            example = new_lines[-1]
            text += ' (e.g., "%s" → "%s")' % (" ".join(example[0].split())[:50], " ".join(example[1].split())[:50])
        if remaining_count:
            text += ", %s untranslated" % _fixed_label(remaining_count, "left", "left")
        self._log(text)

    def _stop_live(self):
        folder = self.folder.get().strip().strip('"')
        profile, _active = automatic.live_translation(folder)
        if profile and xunity_service.stop(profile.get("name", "")):
            self._log("Game translator stopped: new phrases remain in the original language until the next time you "
                      "start the game (graphics card free).")
        self.btn_stop_live.pack_forget()
        self._live_on = None

    def _action(self, action, e):
        """The button chosen in the end-of-operation dialog."""
        if e is not None and e.extra.get("folder") and action in ("restore", "reapply", "translate",
                                                                     "translate_anyway", "use_local",
                                                                     "create_report", "fix"):
            # from the after-play check of another game (on opening): the action (the report too) is for
            # that game
            self.folder.set(os.path.normpath(e.extra["folder"]))
        if action == "show_remaining":
            choice = theme.choose(self.root, self.colors, "Texts left untranslated", outcomes.remaining_text(e),
                                 [("OK", None), ("Fix translations", "fix"),
                                  ("Create report", "create_report")])
            self._action(choice, e)
        elif action == "fix":  # the window with the right list (left untranslated, not written to the game)
            self._fix(outcomes.fixes_filter(e))
        elif action == "create_report":
            self._create_report(e)
        elif action == "reapply":
            self._reapply()
        elif action == "close_game" and e is not None and "checks" in e.extra and e.extra.get("profile"):
            self._check([e.extra["profile"]], at_open=False)  # the check of that game again
        elif action == "close_game" and self._retry:
            self._retry()
        elif action in ("use_local", "install_local"):
            self.translator.set(TRANSLATORS["local"])
            self._change_translator()
            if action == "install_local" or not self._local_ready():
                self._install_local()
            else:  # Analyze and translate again with the same choices (e.g. "Translate anyway"), also after an error
                self._translate(**self._choices(e))
        elif action == "translate_anyway":
            self._translate(force=self._choices(e)["force"], translate_anyway=True)
        elif action == "restore":  # asks for confirmation before putting the originals back
            self._restore()
        elif action == "translate":
            self._translate()
        elif action == "retranslate" and e is not None and e.extra.get("profile"):
            self._check([e.extra["profile"]], at_open=False, retranslate=True)

    def _choices(self, e):
        """forza and translate_anyway of the last Analyze and translate (from the outcome, or remembered: an outcome
        born from an error does not have them); from_scratch if the outcome is from a "Start over" that did not start
        (translator not ready: with the new one it starts over, it does not just translate)."""
        choices = dict(self._translate_choices)
        choices.update({k: e.extra[k] for k in choices if k in e.extra})
        if e.extra.get("from_scratch"):
            choices["from_scratch"] = True
        return choices

    # --- actions -------------------------------------------------------------------------------

    def _browse(self):
        choice = filedialog.askdirectory(title="Game folder", initialdir=self.folder.get() or None)
        if choice:
            self.folder.set(os.path.normpath(choice))

    def _preflight(self, operation, repeat, backend=None):
        """Before a long operation, the quick checks of automatic.preflight_checks (game open, languages,
        protected folder, space, translator paused or to install, same game already translated elsewhere), with a
        dialog for each problem found. Returns the choices to start ({'reuse': profilo or None}), or None if it does
        not start: cancelled, or another way chosen ("Try again" and "Use the local translator" redo 'ripeti', so
        the checks run again; "Install" installs the local translator and then redoes 'ripeti')."""
        folder = self.folder.get().strip().strip('"')
        if self._busy():
            return None
        if not folder or not os.path.isdir(folder):
            return {"reuse": None}  # "Missing folder" is said by _start
        try:
            found = automatic.preflight_checks(folder, backend, operation, self._language_code(self.src_lang.get()),
                                                       self._target_lang())
        except Exception as e:  # noqa: BLE001 - a check that fails must not prevent working
            self._log("  ! Pre-start checks failed (%s: %s): starting anyway." % (type(e).__name__, e))
            return {"reuse": None}
        choices = {"reuse": None}
        for p in found:
            title_text, kind = PREFLIGHT_WINDOWS.get(p.code, ("Before starting", "warning"))
            buttons = {
                "retry": [("Try again", "retry"), ("Cancel", None)],
                "continue": [("Cancel", None), ("Continue anyway", "continue")],
                "use_local": [("Use the local translator", "use_local"), ("Continue anyway", "continue"),
                               ("Cancel", None)],
                # (continuing without: the official language needs no translating, and without a translator Analyze
                # and translate stops before changing the game)
                "install_local": [("Install the local translator" if p.code != "LOCAL_MISSING" else "Install now",
                                     "install_local"), ("Continue anyway", "continue"), ("Cancel", None)],
                "reuse": [("Yes, use them again", "reuse"), ("No, translate from scratch", "start_over"),
                          ("Cancel", None)],
            }.get(p.action, [("OK", None)])
            self.state.set("%s: %s" % (title_text, p.message.split("\n")[0][:120]))
            choice = theme.choose(self.root, self.colors, title_text, p.message, buttons, kind)
            if choice == "retry":
                self.root.after(50, repeat)
                return None
            if choice in ("use_local", "install_local"):
                self.translator.set(TRANSLATORS["local"])
                self._change_translator()
                if choice == "use_local" and self._local_ready():
                    self.root.after(50, repeat)
                else:
                    self._install_local(then=repeat)
                return None
            if choice == "reuse":
                choices["reuse"] = p.data.get("profile")
            elif choice not in ("continue", "start_over"):  # Cancel, OK of a problem that stops, dialog closed
                self.state.set("%s: Not started." % title_text)
                return None
        return choices

    def _translate(self, force=False, translate_anyway=False, consent=False, from_scratch=False, regenerate=False):
        """Analyze and translate. from_scratch ("Start over" in Fix translations): first the game's saved translations
        are set aside in a copy (automatic.start_over) and the game profile is rebuilt (rigenera). Once those are
        set aside, "Try again" (e.g. the game opened halfway through the work) continues the new translation with
        rigenera: it does not set aside again the one just made."""
        from .backend import BackendError
        src_lang, a, backend = self._language_code(self.src_lang.get()), self._target_lang(), self._backend()
        repeat = lambda: self._translate(force=force, translate_anyway=translate_anyway, consent=consent,  # noqa: E731
                                       from_scratch=from_scratch, regenerate=regenerate)
        # (the yes to the game's official language translates nothing: no translator checks)
        choices = self._preflight("translate", repeat, None if consent else backend)
        if choices is None:
            return
        reuse = choices.get("reuse")

        def _work(folder):
            """The outcome of Analyze and translate (esito.Esito), with the choices made so they can be repeated."""
            if from_scratch:  # (with a translator that is missing or paused it stops before touching anything)
                try:
                    automatic.start_over(folder, self._log, test=backend == "fake", backend=backend)
                except BackendError as error:
                    error.from_scratch = True  # nothing touched: "Use the local translator" starts over (_choices)
                    raise
                self._retry = lambda: self._translate(force=force, translate_anyway=translate_anyway,
                                                     consent=consent, regenerate=True)
            path, completed, outcome = automatic.analyze_and_translate(
                folder, src_lang, a, backend, self._log, force=force, regenerate=from_scratch or regenerate,
                translate_anyway=translate_anyway, consent=consent, reuse=reuse)
            e = outcome.get("outcome") or Outcome("OK" if completed else "PARTIAL", written=bool(path))
            e.extra.update(force=force, translate_anyway=translate_anyway)
            return e

        self._retry = repeat
        self._translate_choices = {"force": force, "translate_anyway": translate_anyway}
        self._start("Analysis and translation", _work, stoppable=True, operation="translate")

    def _reapply(self):
        test = self._backend() == "fake"
        if self._preflight("reapply", self._reapply) is None:
            return

        def _work(folder):
            outcome = {}
            done = automatic.reapply(folder, self._log, test=test, outcome=outcome)
            e = outcome.get("outcome") or Outcome("OK" if done else "PARTIAL")
            if outcome.get("added_language"):
                e.extra["added_language"] = outcome["added_language"]
            return e

        self._retry = self._reapply
        self._start("Reapplying", _work, operation="reapply")

    def _restore(self, ask=True):
        if ask and not theme.ask(self.root, self.colors, "Restore", "Restore the original game files?\nYour "
                                                                       'translations are saved. "Analyze and '
                                                                       'translate" will restore them instantly.'):
            return
        if self._preflight("restore", lambda: self._restore(ask=False)) is None:
            return

        def _work(folder):
            outcome = {}
            done = automatic.restore(folder, self._log, outcome=outcome)
            return outcome.get("outcome") or Outcome("OK" if done else "PARTIAL")

        self._retry = lambda: self._restore(ask=False)
        self._start("Restoring", _work, operation="restore")

    def _create_report(self, e=None):
        """Zip on the Desktop with logs, game profile, settings without key or email and useful files of the game. If
        the Desktop cannot be written to (e.g. Windows "Controlled folder access"), in the logs folder. e: the outcome
        of the dialog it was requested from (default: the last one; from the "Details" of the after-play check it is
        the one of the chosen game, not of the last one checked)."""
        if self._busy():
            return
        last, chosen = e if e is not None else self.last_outcome, self._chosen

        def _work(folder):
            extra = ["Selected translator: %s" % chosen]
            try:
                return report.create(folder, report.desktop_folder(), outcome=last, extra=extra)
            except OSError as e:
                self._log("  ! I can't write the report to the Desktop (%s): I'm putting it in PatchSmith's logs "
                          "folder." % e)
                return report.create(folder, logs_folder(), outcome=last, extra=extra)

        self._start("Creating the report", _work, needs_folder=False, operation="report", keep_log=False)

    # --- after-play check ----------------------------------------------------------------------

    def _initial_check(self):
        """On opening: the games with new signals after the last session are looked for (in the background, file
        dates only); if there are any, the check starts on its own (see _start_initial_check)."""
        def search():
            try:
                found = checks.to_check()
            except Exception as e:  # noqa: BLE001 - it must never prevent using PatchSmith
                self._log("  ! Check after the last session failed (%s: %s)" % (type(e).__name__, e))
                found = []
            self.tail.put(("checks", found))

        threading.Thread(target=search, daemon=True).start()

    def _start_initial_check(self, found, window_open=False):
        modal_open = bool(found) and self.work is None and bool(str(self.root.tk.call("grab", "current")))
        if modal_open or (found and self.work is None and window_open):
            # a dialog is open (e.g. the checks before "Analyze and translate" or "Install the local
            # translator?"): wait for it to close, and a moment more (the choice made there, e.g. "Try again", starts
            # right after), otherwise the chosen operation would not start (one work at a time)
            self.root.after(300, lambda: self._start_initial_check(found, window_open=modal_open))
            return
        if not found:
            self._show_last_check()
            return
        if self.work is not None:  # meanwhile you already started an operation: the check starts afterwards
            if not self.check_status.get().startswith("There's something new"):  # (only one line in the log)
                self._log("There's something new after the last session (%s): I'll check it as soon as the current "
                          "operation finishes." % _fixed_label(len(found), "game", "games"))
            self.check_status.set("There's something new after the last session (%s): I'll check it as soon as the "
                                  "current operation finishes." % _fixed_label(len(found), "game", "games"))
            if getattr(self, "_pending_check", None):
                self.root.after_cancel(self._pending_check)
            self._pending_check = self.root.after(2000, lambda: self._start_initial_check(found))
            return
        self._pending_check = None
        self._check(found)

    def _show_last_check(self):
        """Without news: if something was left to do at the last check of the chosen game, it reminds of it."""
        folder = self.folder.get().strip().strip('"')
        try:
            path = automatic.find_profile(folder) if folder and os.path.isdir(folder) else None
            e = checks.saved_outcome(path) if path else None
        except Exception:  # noqa: BLE001 - only a reminder
            e = None
        if e is not None and e.extra.get("to_do"):
            self.check_outcomes = [e]
            self._update_check_line(last=True)

    def _check(self, profiles=None, at_open=None, retranslate=None):
        """After-play check: the game of the chosen folder (profili=None) or the given games. at_open
        (default: with the given games) = the check started on its own on opening: no dialogs at the end, the
        result goes to the line under the folder. ritraduci (default: not on opening): re-translate now the lines
        translated while playing that stayed provisional; not on opening (loading the local translator or
        waiting for Google's pauses would freeze the window): they stay to do, with the "Re-translate" button."""
        translator = self._backend()
        at_open = profiles is not None if at_open is None else at_open
        retranslate = not at_open if retranslate is None else retranslate

        def _work(folder):
            if profiles is None:
                path = automatic.find_profile(folder)
                if not path:
                    self._log("There is no PatchSmith translation to check for this folder.")
                    return [Outcome("NOTHING_TO_DO", "There is no PatchSmith translation to check for this folder: use "
                                                     '"Analyze and translate" first.')]
                items = [path]
            else:
                items = profiles
            results = []
            for path in items:
                interruption.check()
                try:
                    problems = checks.check(path, apply=True, log=self._log, translator=translator,
                                                   retranslate=retranslate)
                except interruption.Interrupted:
                    raise
                except Exception as e:  # noqa: BLE001 - a game that cannot be checked does not stop the others
                    self._write_log("ERROR (%s: %s)\n%s" % (type(e).__name__, e, traceback.format_exc()))
                    problems = [checks.error_problem(e)]
                results.append(checks.as_outcome(problems, path))
            return results

        if self._busy():
            return
        self._auto_check = at_open
        self._retry = lambda: self._check(profiles, at_open=False)  # "Try again": with the dialog at the end
        self._start("Check after playing", _work, stoppable=True, needs_folder=profiles is None,
                    operation="check", log_name="controllo" if profiles is not None else None)

    def _update_check_line(self, last=False):
        """The line under the folder: 'Dopo l'ultima partita a X: 2 problemi corretti, 1 da fare' and 'Details'."""
        with_problems = [e for e in self.check_outcomes if e.extra.get("checks")]
        self.btn_details.pack_forget()
        self.btn_retranslate.pack_forget()
        if not self.check_outcomes or all(e.code == "NOTHING_TO_DO" for e in self.check_outcomes):
            self.check_status.set("")
            self._show_check_line()
            return
        fixed_count = sum(e.extra.get("fixed_count", 0) for e in self.check_outcomes)
        to_do = sum(e.extra.get("to_do", 0) for e in self.check_outcomes)
        if len(self.check_outcomes) == 1:
            text = "%s %s: " % ("At the last check of" if last else "After the last session of",
                                 self.check_outcomes[0].extra.get("game", "the game"))
        else:
            text = "After the last session (%s): " % _fixed_label(len(self.check_outcomes), "game", "games")
        if with_problems:
            self.btn_details.pack(side="left")
        if fixed_count or to_do:
            text += "%s, %s" % (_fixed_label(fixed_count, "problem fixed", "problems fixed"),
                                 _fixed_label(to_do, "to do", "to do"))
        else:
            text += "no problem"
        if any(e.code == "GAME_OPEN" for e in self.check_outcomes):
            text += " (Some fixes will be applied right after the game closes)"
        self.check_status.set(text)
        profiles, sentences = self._to_retranslate()
        if profiles:
            self.btn_retranslate.configure(text="Re-translate %s" % _fixed_label(sentences, "phrase", "phrases")
                                           if sentences else "Re-translate the phrases")
            self.btn_retranslate.pack(side="left", padx=(6, 0) if with_problems else 0)
        self._show_check_line()

    def _show_check_line(self):
        """The check's buttons (at the bottom, on the left) only if there is one to use."""
        if self.btn_details.winfo_manager() or self.btn_retranslate.winfo_manager():
            if not self.check_row.winfo_manager():
                self.check_row.pack(side="left", padx=(0, 10), before=self.status_label)
        else:
            self.check_row.pack_forget()

    def _to_retranslate(self):
        """([profili], how many lines) with lines translated while playing that need re-translating (left by the check
        on opening: problems with the 'retranslate' action still to do)."""
        profiles, sentences = [], 0
        for e in self.check_outcomes:
            entries = [p for p in e.extra.get("checks") or [] if p.get("action") == "retranslate"
                    and outcomes.GROUP_STATES.get(p.get("state")) == "to_do"]
            if entries and e.extra.get("profile"):
                profiles.append(e.extra["profile"])
                sentences += sum(int(p.get("sentences") or 0) for p in entries)
        return list(dict.fromkeys(profiles)), sentences

    def _retranslate(self):
        """The "Re-translate N lines" button: the check of the games with lines to re-translate, this time with the
        re-translation (and the dialogs at the end)."""
        profiles, _ = self._to_retranslate()
        if profiles:
            self._check(profiles, at_open=False, retranslate=True)

    def _check_details(self):
        self._show_check(all_=True)

    def _show_check(self, all_=False):
        """One dialog per game with the problems (fixed, to do, to know) and the buttons for those to do; at the
        end of the check started from the button, even without problems. A chosen button closes the other dialogs."""
        outcomes_to_show = [e for e in self.check_outcomes if e.extra.get("checks")] if all_ else \
            list(self.check_outcomes)
        for e in outcomes_to_show:
            data = outcomes.window(e, "check", local_ready=self._local_ready(), current=self._backend())
            choice = theme.choose(self.root, self.colors, data["title"], data["text"], data["buttons"],
                                 data["kind"])
            if choice:
                self._action(choice, e)
                break

    def _check_code(self):
        """PatchSmith updated while the window was open: the user is told (once) to close and reopen it."""
        if self._warned_code:
            return
        current = code_fingerprint()
        if current != self._code or (current[0] and current[0] != __version__):
            self._warned_code = True
            theme.warn(self.root, self.colors, "PatchSmith has been updated",
                        "PatchSmith has been updated while this window was open: please close and reopen PatchSmith, "
                        "otherwise you will continue to use the older version.")

    # --- fix translations (gui_corrections.py) ---------------------------------------------------------------

    def _fix(self, filter_=None):
        """"Fix translations…": the window with the translations of the chosen folder's game and their problems;
        filtro: the list to show at once (see corrections.FILTERS). Returns the window (or None)."""
        from . import corrections, gui_corrections
        if self._busy():
            return None
        folder = self.folder.get().strip().strip('"')
        try:
            path = automatic.find_profile(folder) if folder and os.path.isdir(folder) else None
        except Exception:  # noqa: BLE001 - damaged game profile: as if untranslated (Analyze and translate says so)
            path = None
        if not path:
            theme.inform(self.root, self.colors, "No translation", "There are no translations for this folder yet: "
                                                                       'use "Analyze and translate" first.')
            return None
        try:
            items = corrections.load(path, test=self._backend() == "fake", log=self._log)
        except (OSError, ValueError, KeyError, RuntimeError) as e:
            reason = ("The translation file for this game is corrupted (possibly manually edited with an error) and is "
                      "unreadable." if isinstance(e, RuntimeError) and "JSON" in str(e) else
                      "I can't read this game's translations (%s)." % e)
            # the detail goes to the interface log (outside an operation none is open) and to the
            # outcome: "Create report" below must be about this, not about the last operation
            is_open = self._log_file is not None
            if not is_open:
                self._open_log("", "Fix translations", "interfaccia")
            self._write_log("Fix translations did not open (%s: %s)" % (type(e).__name__, e))
            if not is_open:
                self._close_log()
            self.last_outcome = Outcome("ERROR", reason, actions=["create_report"])
            choice = theme.choose(self.root, self.colors, "Unreadable translations",
                                  reason + "\n\nYou can open the file and edit it manually (advanced), or create a "
                                           "report to send to whoever is helping you with PatchSmith.",
                                  [("OK", None), ("Open the file (advanced)", "open"),
                                   ("Create report", "create_report")], "error")
            if choice == "open":
                self._open_translations_file()
            elif choice:
                self._action(choice, None)
            return None
        return gui_corrections.Window(self, items, filter_)

    def _open_translations_file(self):
        """"Open the file (advanced)": the translations file in the default program (or in Notepad)."""
        folder = self.folder.get().strip().strip('"')
        try:
            path = automatic.memory_file(folder, self._target_lang()) if folder else None
        except Exception:  # noqa: BLE001 - as without a file
            path = None
        if not path or not os.path.exists(path):
            theme.inform(self.root, self.colors, "No translation", "There are currently no translations for this "
                                                                       "folder.")
            return
        theme.inform(
            self.root, self.colors, "Open the translation file",
            "The translation file opens: on the left, the original text, on the right, the translation.\nOnly edit the "
            'text on the right, save the file and then press "Analyze and translate".\n\n"Fix translations…" is '
            "usually more convenient, as it also checks placeholders and lengths.")
        try:
            _open(path)
        except OSError as e:
            theme.warn(self.root, self.colors, "File not opened", "I can't open the file (%s). You can find it "
                                                                  "here:\n%s" % (e.strerror or e, path))
            show_file(path)

    def _after_fixes(self, how_many):
        """Fixes saved in the "Fix translations" window: they are written to the game on their own, with "Reapply"
        (first the checks: if the game is open, "Try again" once you have closed it)."""
        self._log("Fix translations: %s saved, writing them into the game." % _fixed_label(how_many, "fix",
                                                                                    "fixes"))
        self.state.set("Corrections saved: writing them into the game…")
        self._reapply()

    def _retranslate_fixes(self, texts=None, model=None):
        """From "Fix translations": re-translates the chosen texts (texts=None: "Re-translate everything") with the
        chosen translator or with the local translator and the given model, then writes them to the game
        (automatic.retranslate)."""
        backend = self._backend()
        if model and backend != "fake":  # a chosen model is the local translator's
            backend = "local"
        test = backend == "fake"
        if self._preflight("reapply", lambda: self._retranslate_fixes(texts, model)) is None:
            return

        def _work(folder):
            outcome = {}
            done = automatic.retranslate(folder, texts, backend, self._log, test=test, outcome=outcome, model=model)
            return outcome.get("outcome") or Outcome("OK" if done else "PARTIAL")

        self._retry = self._reapply  # (the new translations are already saved: "Try again" writes them to the game)
        self._start("Re-translation", _work, stoppable=True, operation="retranslate")

    def _install_12b_and_retranslate(self):
        """"Re-translate everything with Quality (12B)" without the model: the local translator's Quality model is
        chosen, installed (with the usual question) and then the re-translation starts on its own."""
        self.translator.set(TRANSLATORS["local"])
        self._change_translator()
        self.local_model.set(LOCAL_MODELS["12b"])
        self._choose_model()
        self._install_local(then=lambda: self._retranslate_fixes(None, "12b"))


_TRANSLATORS_IT, _MODELS_IT = dict(TRANSLATORS), dict(LOCAL_MODELS)


def _translate_constants():
    """The names of the translators and models in the interface language (the drop-down menus compare them)."""
    global TRANSLATORS, LOCAL_MODELS
    TRANSLATORS = {k: ui.translate(v) for k, v in _TRANSLATORS_IT.items()}
    LOCAL_MODELS = {k: ui.translate(v) for k, v in _MODELS_IT.items()}


def main():
    project.migrate_data()  # the data with the former Italian names (profiles, memory...) move to the English ones
    if sys.platform.startswith("win"):
        try:  # on the taskbar PatchSmith with its own icon, not grouped under Python
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PatchSmith.PatchSmith")
        except (AttributeError, OSError):
            pass
        # PatchSmith.exe with the icon next to PatchSmith.pyw (if missing or old: building it takes a second)
        threading.Thread(target=icon.executable, daemon=True).start()
    # the music ready in advance, even if it is off: the button with the note starts it at once
    threading.Thread(target=music.prepare, daemon=True).start()
    root = tk.Tk()
    App(root)  # the theme (light/dark like Windows) is applied by App
    root.mainloop()
