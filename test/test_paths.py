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
PatchSmith's data go to PATCHSMITH_DATA (test/run_all.py creates one for the test); without it, to a
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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import tkinter as tk  # noqa: E402

import fake_web_game  # noqa: E402
import test_checks as pc  # noqa: E402  (fake Unity game, fake packages, reduced real logs)
import test_formats as pf  # noqa: E402  (GameMaker game in the style of Another Door, fake translator)
import fake_unity  # noqa: E402
from patchsmith import (adapter_unity, auto as automatic, backend, checks, corrections,
                        outcome, gui, limiter, ollama, project, report,
                        xunity_service, system, steam, theme)

OUTCOMES = []
WINDOWS = []  # (type, title, text, buttons)
ANSWERS = {}  # title -> answer (or list of answers): None = OK/Cancel/dialog closed
# words that tell the player what to do next (a button or an instruction in the text)
STEP = re.compile(r"\b(press|start|choose|close|try|install|use|open|create|fix|leave|play|check|reapply|wait|"
                   r"you can|need|just|move|paste|restart|go back|return|stay|remain|tell me|do\b)\w*",
                   re.I)
TECHNICAL = re.compile(r"smith\.py|Traceback|\.json\b|\bNone\b|%[sd]\b|\{\}|Errno|Exception|KeyError|TypeError|"
                     r"\bprofile\b|PATCHSMITH_", re.I)


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name, flush=True)


def silent(_):
    pass


def _dialog_answer(title_text, default_language=None):
    r = ANSWERS.get(title_text, default_language)
    if isinstance(r, list):
        r = r.pop(0) if r else default_language
    return r


def _choose(_g, _c, title_text, text, buttons, kind="info"):
    WINDOWS.append(("scegli", title_text, text, list(buttons)))
    print("FINESTRA [%s] %s | %s | %s" % (kind, title_text, " ".join(text.split()), [p for p, _ in buttons]))
    return _dialog_answer(title_text)


def _ask(_g, _c, title_text, text):
    WINDOWS.append(("chiedi", title_text, text, [("Yes", True), ("No", False)]))
    response = bool(_dialog_answer(title_text, False))
    print("DOMANDA %s | %s | risposta: %s" % (title_text, " ".join(text.split()), "sì" if response else "no"))
    return response


def _inform(_g, _c, title_text, text):
    WINDOWS.append(("informa", title_text, text, [("OK", None)]))
    print("AVVISO %s | %s" % (title_text, " ".join(text.split())))


def is_clear(f):
    """The dialog tells the next step (a button other than OK/Cancel, or an instruction in the text) without technical
    names."""
    _, title_text, text, buttons = f
    others = [p for p, v in buttons if v not in (None, False, True)]
    finished = re.search(r"no problem|Start the game", text)  # nothing to do: OK is enough
    return not TECHNICAL.search(title_text + " " + text) and bool(others or finished or STEP.search(text))


def windows_from(n):
    return WINDOWS[n:]


def latest(title_text=None, src_lang=0):
    choices = [f for f in WINDOWS[src_lang:] if title_text is None or f[1] == title_text]
    return choices[-1] if choices else ("", "", "", [])


def buttons(f):
    return [v for _, v in f[3]]


def wait_for(app, root, seconds=120):
    """Waits for the end of the running work and lets the last messages (and the final dialog) arrive."""
    start = time.time()
    for _ in range(3):
        root.update()
        time.sleep(0.02)
    while app.work is not None and app.work.is_alive() and time.time() - start < seconds:
        root.update()
        time.sleep(0.03)
    for _ in range(25):
        root.update()
        time.sleep(0.03)


def fingerprints(folder):
    output = {}
    for r, _, fs in os.walk(folder):
        for n in fs:
            p = os.path.join(r, n)
            with open(p, "rb") as f:
                output[os.path.relpath(p, folder)] = hashlib.sha256(f.read()).hexdigest()
    return output


class Translator(backend.Backend):
    """A "real" fake translator (not the 'fake' backend: real translation memory): words reversed, recognizable;
    those in ODD come back with a character no font has, or the same as the original."""
    name = "dictionary"
    ODD = {}

    def __init__(self):
        self.received = []

    def translate_lines(self, lines, origin, dest_lang, *_a, **_k):
        from patchsmith import interruption
        interruption.check()
        self.received += list(lines)
        return [self.ODD.get(r, pf.ITALIAN.get(r, pf.mock(r))) for r in lines]


class Blocked(Translator):
    """Translates the first group of texts, then the online service blocks the requests (like a Google 429)."""
    max_texts = 3

    def translate_lines(self, lines, origin, dest_lang, *_a, **_k):
        if self.received:
            limiter.Limiter("google", log=None).blocked(429, "test")
            raise backend.Blocked("Google rejected the requests (too many requests): I'll try again later.",
                                   "TRANSLATOR_BLOCKED")
        return super().translate_lines(lines, origin, dest_lang)


# --- setup -------------------------------------------------------------------------------------------------

def prepare(base):
    theme.inform = theme.warn = theme.error = _inform
    theme.ask = _ask
    theme.choose = _choose
    gui.show_file = lambda _p: None
    gui._open = lambda _p: None
    report.desktop_folder = lambda: os.path.join(base, "Desktop")
    ollama.folder = lambda: os.path.join(base, "ollama")
    automatic.GAME_SPACE = automatic.DATA_SPACE = 0
    ollama.recommended_model = lambda gb=None: ("4b", "Your graphics card has 4 GB of memory: I recommend the Fast "
                                                      "model (4B).")
    if not os.environ.get("PATCHSMITH_DATA"):
        project.DATA_ROOT = os.path.join(base, "data_folder")
    gui.SETTINGS = None
    for v, rel in (("LOCALAPPDATA", "appdata"), ("USERPROFILE", "utente"), ("TEMP", "temp")):
        if not os.environ.get("PATCHSMITH_DATA"):  # (with run_all they are already test folders)
            os.environ[v] = os.path.join(base, rel)
        os.makedirs(os.environ[v], exist_ok=True)
    steam.steam_folder = lambda: None
    steam.game_installed = lambda folder, steam=None: None
    steam.identify_from_executables = lambda folder: None
    checks.crash_events = lambda since: []  # never the real Windows log
    # the local translator "installed" (simulated state) and the fake translator in place of all of them
    STATE["local"] = "ready"
    ollama.state = lambda: STATE["local"]
    ollama.installed = lambda: STATE["local"] == "ready"
    ollama.model_present = lambda key=None: STATE["local"] == "ready"
    automatic.create_backend = checks.create_backend = create_backend
    STATE["translator"] = Translator
    # Unity: BepInEx and XUnity packages already "downloaded", the translator's plugin without a compiler
    pc.prepare_components(os.path.join(base, "components"))
    adapter_unity._compile_plugin = pc._fake_plugin


STATE = {}


def create_backend(name, log=print):
    """Like backend.create_backend, but with the fake translator: the local translator not installed does not start,
    the online one, if paused, says so (pause(), like GoogleFree)."""
    if name == "local" and STATE["local"] != "ready":
        raise backend.BackendError(backend.NOT_INSTALLED, "TRANSLATOR_MISSING", command=backend.INSTALL_COMMAND)
    t = STATE["translator"]()
    if name in ("google", "deepl", "mymemory"):
        t.pause = lambda: limiter.Limiter(name, log=None).pause()
    return t


def open_window():
    root = tk.Tk()
    app = gui.App(root)
    for _ in range(20):
        root.update()
        time.sleep(0.02)
    app.translator.set(gui.TRANSLATORS["local"])
    app._change_translator()
    return root, app


# --- 1. web game -------------------------------------------------------------------------------------------

def web_path(app, root, base):
    game = os.path.join(base, "giochi", "Lantern Keeper")
    fake_web_game.create(game)
    originals = fingerprints(game)
    app.folder.set(game)
    n = len(WINDOWS)
    app._translate()
    wait_for(app, root)
    end = latest(src_lang=n)
    check("web: 'Analizza e traduci' con il traduttore locale -> 'Traduzione installata' con il riepilogo e il passo "
              "dopo (%s)" % end[1], end[1] == "Translation installed" and "Translated texts" in end[2]
              and is_clear(end))
    texts_file = os.path.join(game, *fake_web_game.TEXTS_FILE.split("/"))
    with open(texts_file, encoding="utf-8") as f:
        translated = f.read()
    check("web: il file del gioco e' tradotto", "Start a new game" not in translated)

    # after-play check from the button: nothing to do
    n = len(WINDOWS)
    app._check()
    wait_for(app, root)
    end = latest(src_lang=n)
    check("web: controllo dopo aver giocato -> nessun problema, con il passo dopo",
              end[1] == "Check after playing" and is_clear(end)
              and "no problem" in app.check_status.get())

    # Fix translations: one line fixed and saved, then Reapply on its own
    window = app._fix()
    window._go_to("Start a new game")
    window.translation_text.delete("1.0", "end")
    window.translation_text.insert("1.0", "Inizia una nuova partita")
    for _ in range(5):
        root.update()
    n = len(WINDOWS)
    window.save()
    wait_for(app, root)
    end = latest(src_lang=n)
    with open(texts_file, encoding="utf-8") as f:
        check("web: correzione salvata e scritta nel gioco da sola ('Traduzione riapplicata')",
                  "Inizia una nuova partita" in f.read() and end[1] == "Translation reapplied" and is_clear(end))

    # Restore (confirm) and then Reapply
    ANSWERS["Restore"] = True
    n = len(WINDOWS)
    app._restore()
    wait_for(app, root)
    end = latest(src_lang=n)
    check("web: 'Ripristina originali' -> 'Originali ripristinati', file identici all'originale",
              end[1] == "Originals restored" and fingerprints(game) == originals and is_clear(end))
    n = len(WINDOWS)
    app._reapply()
    wait_for(app, root)
    end = latest(src_lang=n)
    with open(texts_file, encoding="utf-8") as f:
        check("web: 'Riapplica' dopo Ripristina -> di nuovo tradotto, con la correzione",
                  "Inizia una nuova partita" in f.read() and end[1] == "Translation reapplied" and is_clear(end))

    # Create report: the zip with the logs, the game profile and the texts with problems, without game files
    n = len(WINDOWS)
    app._create_report()
    wait_for(app, root)
    end = latest(src_lang=n)
    zips = glob.glob(os.path.join(glob.escape(report.desktop_folder()), "*.zip"))
    names = zipfile.ZipFile(zips[-1]).namelist() if zips else []
    check("web: 'Crea rapporto' -> 'Rapporto creato' sul Desktop con registri e profilo",
              end[1] == "Report generated" and any(x.startswith("registri/") for x in names)
              and any(x.startswith("profilo/") for x in names) and not any(x.endswith(".html") for x in names))
    return game


# --- 2. game open, translator paused or blocked, local one not installed, texts left untranslated -------------

def unexpected_path(app, root, base):
    game = os.path.join(base, "giochi", "Ember Road")
    fake_web_game.create(game)
    app.folder.set(game)
    real_is_open = system.game_open
    # game open before starting: nothing starts, "Try again" with the game closed starts
    state = {"is_open": True}
    system.game_open = lambda _c: state["is_open"]
    ANSWERS["The game is open"] = ["retry"]
    try:
        n = len(WINDOWS)
        app._translate()
        question = latest(src_lang=n)
        state["is_open"] = False
        wait_for(app, root)
    finally:
        system.game_open = real_is_open
    end = latest(src_lang=n)
    check("gioco aperto prima di partire: 'Il gioco è aperto' con 'Riprova'; chiuso il gioco, 'Riprova' traduce",
              question[1] == "The game is open" and "retry" in buttons(question) and is_clear(question)
              and end[1] == "Translation installed")

    # online translator paused before starting: "Continue anyway" -> only the saved translations
    other = os.path.join(base, "giochi", "Frost Hollow")
    fake_web_game.create(other)
    app.folder.set(other)
    app.translator.set(gui.TRANSLATORS["google"])
    app._change_translator()
    limiter.Limiter("google", log=None).blocked(429, "test")
    ANSWERS["Translator paused"] = ["continue"]
    try:
        n = len(WINDOWS)
        app._translate()
        wait_for(app, root)
    finally:
        limiter.Limiter("google", log=None).reset()
    pause = latest("Translator paused", src_lang=n)
    end = latest(src_lang=n)
    check("Google in pausa: finestra con 'Usa il traduttore locale'; 'Continua comunque' -> esito con il passo dopo "
              "(%s: %s)" % (end[1], buttons(end)), pause[1] and "use_local" in buttons(pause) and is_clear(pause)
              and end[1] in ("Translation interrupted", "Translation partially installed") and is_clear(end)
              and "use_local" in buttons(end))
    # "Use the local translator" from the final dialog: selects it and translates
    n = len(WINDOWS)
    app._action("use_local", app.last_outcome)
    wait_for(app, root)
    end = latest(src_lang=n)
    check("... 'Usa il traduttore locale': scelto e tradotto ('%s')" % end[1],
              app._chosen == "local" and end[1] == "Translation installed")

    # Google blocked halfway through the work: "Translation interrupted", the translations done stay
    third = os.path.join(base, "giochi", "Moss Gate")
    fake_web_game.create(third)
    app.folder.set(third)
    app.translator.set(gui.TRANSLATORS["google"])
    app._change_translator()
    STATE["translator"] = Blocked
    try:
        n = len(WINDOWS)
        app._translate()
        wait_for(app, root)
    finally:
        STATE["translator"] = Translator
        limiter.Limiter("google", log=None).reset()
    end = latest(src_lang=n)
    check("Google bloccato a meta': '%s' con il passo dopo e 'Usa il traduttore locale' (%s)"
              % (end[1], buttons(end)), end[1] in ("Translation interrupted", "Translation partially installed")
              and is_clear(end) and "use_local" in buttons(end))

    # local translator not installed: "Continue anyway" -> "Translator not ready" with "Install"; once installed
    # (fake) -> the translation starts again on its own
    app.translator.set(gui.TRANSLATORS["local"])
    app._change_translator()
    STATE["local"] = "non installato"
    fourth = os.path.join(base, "giochi", "Salt Marsh")
    fake_web_game.create(fourth)
    originals = fingerprints(fourth)
    app.folder.set(fourth)
    ANSWERS["Local translator to install"] = ["continue"]
    n = len(WINDOWS)
    app._translate()
    wait_for(app, root)
    absent = latest("Local translator to install", src_lang=n)
    end = latest(src_lang=n)
    check("traduttore locale non installato: 'Installa ora' prima di partire; 'Continua comunque' -> '%s' con "
              "'Installa il traduttore locale', gioco non toccato" % end[1],
              absent[1] and "install_local" in buttons(absent) and is_clear(absent)
              and end[1] == "Translator not ready" and "install_local" in buttons(end) and is_clear(end)
              and fingerprints(fourth) == originals)
    actual = ollama.download_description, ollama.install, backend.probe_local

    def install(selected, log=print):
        STATE["local"] = "ready"
        return True

    ollama.download_description = lambda selected: ["Ollama (finto)", "modello Veloce (finto)"]
    ollama.install = install
    backend.probe_local = lambda model=None, log=print, **_k: {
        "ok": True, "message": "ready, on the graphics card: \"Start game\" -> \"Inizia\".", "gpu": 1.0}
    ANSWERS["Install the local translator?"] = True
    ANSWERS["Local translator to install"] = ["install_local"]
    try:
        STATE["local"] = "non installato"
        n = len(WINDOWS)
        app._translate()
        wait_for(app, root)  # the installation
        wait_for(app, root)  # and then the translation that was waiting
    finally:
        ollama.download_description, ollama.install, backend.probe_local = actual
        STATE["local"] = "ready"
    end = latest(src_lang=n)
    check("'Installa ora' prima di partire: installa (finto), frase di prova, poi traduce da solo ('%s')"
              % end[1], end[1] == "Translation installed")

    # a text left untranslated (the translator never translates it): "Show remaining" and "Fix translations"
    fifth = os.path.join(base, "giochi", "Tide Bell")
    fake_web_game.create(fifth)
    app.folder.set(fifth)
    Translator.ODD = {"Rest here to save your progress and recover your strength.": ""}
    try:
        n = len(WINDOWS)
        app._translate()
        wait_for(app, root)
    finally:
        Translator.ODD = {}
    end = latest(src_lang=n)
    check("testi rimasti in originale: '%s' con 'Mostra i rimasti' e 'Correggi traduzioni' (%s)"
              % (end[1], buttons(end)), end[1] == "Translation partially installed" and is_clear(end)
              and "fix" in buttons(end)
              and "Rest here" in end[2] + json.dumps(app.last_outcome.remaining_examples))
    open_toplevels = []
    app._fix = lambda filter_=None: open_toplevels.append(filter_)
    try:
        app._action("fix", app.last_outcome)
    finally:
        del app._fix
    check("... 'Correggi traduzioni' apre l'elenco dei rimasti", open_toplevels == ["remaining"])
    window = app._fix("remaining")
    lines = len(window.tree.get_children()) if window else 0
    check("... la finestra mostra i testi rimasti (%d)" % lines, lines >= 1)
    if window:
        window.close()


# --- 3. GameMaker game -------------------------------------------------------------------------------------

def gamemaker_path(app, root, base):
    game = os.path.join(base, "giochi", "Porta finta")
    pf.door_game(game)
    originals = fingerprints(game)
    app.folder.set(game)
    # the game open during the work (opened after the checks before starting): the translations stay saved and
    # nothing is written; "Try again" with the game closed writes them. One translation has a character that no font
    # of the game has (a star): the game could not show it
    real_is_open = project.game_open
    project.game_open = lambda _c: True
    Translator.ODD = {"VULTURE TOKEN": "GETTONE ★ AVVOLTOIO"}
    try:
        n = len(WINDOWS)
        app._translate()
        wait_for(app, root)
    finally:
        project.game_open = real_is_open
    end = latest(src_lang=n)
    check("GameMaker aperto durante il lavoro: 'Il gioco è aperto' (traduzioni pronte, non scritte) con Riprova "
              "(%s: %s)" % (end[1], buttons(end)), end[1] == "The game is open" and "close_game" in buttons(end)
              and is_clear(end) and fingerprints(game) == originals)
    try:
        n = len(WINDOWS)
        app._action("close_game", app.last_outcome)  # "Try again", with the game closed
        wait_for(app, root)
    finally:
        Translator.ODD = {}
    end = latest(src_lang=n)
    check("GameMaker: '%s' con il passo dopo; la traduzione che il font non sa disegnare porta all'elenco "
              "'Non scritte nel gioco' (%s)" % (end[1], buttons(end)),
              end[1] == "Translation partially installed" and is_clear(end) and "fix" in buttons(end)
              and outcome.fixes_filter(app.last_outcome) == "game" and "\"Not written to the game\"" in end[2]
              and "\"Left untranslated\"" not in end[2])
    with open(os.path.join(game, "localization", "stringsEN.json"), encoding="utf-8") as f:
        translated = f.read()
    check("GameMaker: file inglese tradotto, la stella mai scritta nel gioco", "Gioca" in translated
              and "★" not in translated)
    # the after-play check: nothing to do
    n = len(WINDOWS)
    app._check()
    wait_for(app, root)
    end = latest(src_lang=n)
    check("GameMaker: controllo dopo aver giocato con il passo dopo (%s)" % end[1], is_clear(end))
    window = app._fix("game")
    entries = [window.tree.item(i, "values")[0] for i in window.tree.get_children()] if window else []
    check("GameMaker: la traduzione con la stella e' in 'Non scritte nel gioco' (%s)" % entries[:3],
              any("VULTURE" in v for v in entries))
    if window:
        window.close()
    ANSWERS["Restore"] = True
    app._restore()
    wait_for(app, root)
    check("GameMaker: Ripristina -> file identici all'originale", fingerprints(game) == originals)
    return game


# --- 4. Unity Mono game ------------------------------------------------------------------------------------

COMPANY, PRODUCT = "Studio Finto", "Lumen Mine"


def create_unity(game):
    pc.create_unity_game(game, PRODUCT, "2022.3.10f1", COMPANY, PRODUCT)
    open(os.path.join(game, "UnityPlayer.dll"), "wb").write(b"finto")
    sentences = ["Start a new game", "Welcome to the old mine. Collect all the gems!", "Press any key to continue",
             "The door is locked. Find the key first.", "Settings and options", "Your lantern is running out of oil."]
    with open(os.path.join(game, PRODUCT + "_Data", "Managed", "Assembly-CSharp.dll"), "wb") as f:
        f.write(fake_unity.dll(sentences))


UNITY_ORIGINALS = {}


def unity_path(app, root, base):
    game = os.path.join(base, "giochi", PRODUCT)
    create_unity(game)
    originals = fingerprints(game)
    UNITY_ORIGINALS.update(originals)
    app.folder.set(game)
    n = len(WINDOWS)
    Translator.ODD = {"The door is locked. Find the key first.": "La porta è chiusa. Trova prima la chiave."}
    try:
        app._translate()
        wait_for(app, root)
    finally:
        Translator.ODD = {}
    end = latest(src_lang=n)
    check("Unity: 'Traduzione installata' con ALT+T e chi traduce le frasi nuove (%s)" % end[1],
              end[1] == "Translation installed" and "ALT+T" in end[2] and "PatchSmith" in end[2] and is_clear(end))
    ini = adapter_unity.read_ini(os.path.join(game, *adapter_unity.CONFIG_XUNITY.split("/")))
    check("Unity: frasi nuove al traduttore di PatchSmith (CustomTranslate), registro di Unity acceso",
              (ini.get("Service") or {}).get("Endpoint") == "CustomTranslate"
              and adapter_unity.service_installed(game) and adapter_unity.unity_log_enabled(game))
    path = automatic.find_profile(game)
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    name = profile["name"]

    # a fix made with the game open: it stays waiting ("Try again" not pressed, the player plays)
    real_is_open = system.game_open
    window = app._fix()
    window._go_to("Start a new game")
    window.translation_text.delete("1.0", "end")
    window.translation_text.insert("1.0", "Nuova partita")
    for _ in range(5):
        root.update()
    system.game_open = lambda _c: True
    try:
        n = len(WINDOWS)
        window.save()
        wait_for(app, root)
    finally:
        system.game_open = real_is_open
    question = latest(src_lang=n)
    check("Unity: correzione salvata con il gioco aperto: 'Il gioco è aperto' con 'Riprova' (%s)" % question[1],
              question[1] == "The game is open" and is_clear(question))
    check("Unity: la correzione aspetta di essere scritta", "Start a new game" in corrections.to_write(profile))

    # after the session: the reduced real logs (Google stopped in Player-prev.log, letters the font lacks in
    # Player.log, crash on start) and the journal of new lines
    time.sleep(1.2)
    after = time.time() - 0.5  # (after PatchSmith's last write to the game, before the check)
    user = os.environ["USERPROFILE"]
    locallow = os.path.join(user, "AppData", "LocalLow", COMPANY, PRODUCT)
    google_error = pc.with_path(pc.read_file(os.path.join(pc.SIGNALS, "unity-player-errore-google.log")),
                                    pc.NEW_COPY, game)
    google_error = google_error.replace("One Million Coins_Data", PRODUCT + "_Data")
    pc.write(os.path.join(locallow, "Player-prev.log"), google_error, after)
    letters = ("Mono path[0] = '%s/%s_Data/Managed'\n" % (game.replace("\\", "/"), PRODUCT) +
               "The character with Unicode value \\u00E8 was not found in the [LiberationSans SDF] font asset or any "
               "potential fallbacks. It was replaced by Unicode character \\u25A1 in text object [Testo].\n")
    pc.write(os.path.join(locallow, "Player.log"), letters, after)
    pc.write(xunity_service.journal_path(name), pc.read_file(os.path.join(pc.SIGNALS, "diario-richieste.jsonl")))
    os.utime(xunity_service.journal_path(name), (after, after))
    return game, name, path


def unity_path_after(app, root, base, game, name, path):
    """At the next opening of PatchSmith (new window): the after-play check starts on its own, without
    dialogs; the line under the folder, "Details", "Re-translate"; then Restore, Reapply and the report."""
    originals = UNITY_ORIGINALS
    n = len(WINDOWS)
    start = time.time()
    while (app.work is None or app.work.is_alive()) and time.time() - start < 60:
        root.update()
        time.sleep(0.03)
    wait_for(app, root)
    line = app.check_status.get()
    print("RIGA:", line, "| Ritraduci:", app.btn_retranslate.winfo_manager() and app.btn_retranslate.cget("text"))
    check("Unity: controllo all'apertura senza finestre, la riga sotto la cartella con 'Dettagli' (%s)" % line,
              len(WINDOWS) == n and line.startswith("After the last session") and
              app.btn_details.winfo_manager() == "pack")
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    entries = adapter_unity.xunity_cache_entries(game, "it")
    check("Unity: lettere che il font non ha annotate e frasi gia' scritte adattate (è -> e')",
              "è" in (profile.get("missing_chars") or "") and not any("è" in v for v in entries.values()))
    check("Unity: la correzione fatta a gioco aperto scritta nella cache dal controllo all'apertura (%s)"
              % entries.get("Start a new game"), entries.get("Start a new game") == "Nuova partita"
              and not corrections.to_write(profile))
    n = len(WINDOWS)
    app._check_details()
    details = windows_from(n)
    check("Unity: 'Dettagli' -> finestra con il passo dopo per ogni problema, anche la correzione scritta da sola "
              "(%s)" % [f[1] for f in details], details and all(is_clear(f) for f in details)
              and any("while the game was open was not in the game yet" in f[2] for f in details))
    check("Unity: pulsante 'Ritraduci 5 frasi' nella riga (le frasi del diario)",
              app.btn_retranslate.winfo_manager() == "pack"
              and app.btn_retranslate.cget("text") == "Re-translate 5 phrases")
    n = len(WINDOWS)
    app._retranslate()
    wait_for(app, root)
    end = latest(src_lang=n)
    check("Unity: 'Ritraduci 5 frasi' -> tradotte e messe nel gioco, finestra con il passo dopo (%s)" % end[1],
              is_clear(end) and "5 phrases translated and written to the game" in end[2])

    # Restore and Reapply: everything removed and everything back, with the adapted letters
    ANSWERS["Restore"] = True
    n = len(WINDOWS)
    app._restore()
    wait_for(app, root)
    end = latest(src_lang=n)
    remaining = sorted(set(fingerprints(game)) - set(originals))
    check("Unity: Ripristina -> nessun file di PatchSmith, BepInEx o XUnity nel gioco (%s)" % remaining[:5],
              end[1] == "Originals restored" and fingerprints(game) == originals)
    n = len(WINDOWS)
    app._reapply()
    wait_for(app, root)
    end = latest(src_lang=n)
    entries = adapter_unity.xunity_cache_entries(game, "it")
    check("Unity: Riapplica -> di nuovo installato, correzione e lettere adattate (%s)" % end[1],
              end[1] == "Translation reapplied" and entries.get("Start a new game") == "Nuova partita"
              and not any("è" in v for v in entries.values()) and adapter_unity.unity_log_enabled(game))

    # the report: the Unity and BepInEx logs, the configuration, the check, the fonts' letters
    n = len(WINDOWS)
    app._create_report()
    wait_for(app, root)
    zips = sorted(glob.glob(os.path.join(glob.escape(report.desktop_folder()), "*.zip")), key=os.path.getmtime)
    names = zipfile.ZipFile(zips[-1]).namelist() if zips else []
    print("RAPPORTO:", names)
    check("Unity: rapporto con Player.log, configurazione di XUnity e di BepInEx, controllo e diario",
              any(x.endswith("Player.log") for x in names) and any("AutoTranslatorConfig.ini" in x for x in names)
              and any("BepInEx.cfg" in x for x in names) and any(x.endswith(".controllo.json") for x in names)
              and any(x.endswith(".richieste.jsonl") for x in names))
    return game


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    prepare(base)
    root, app = open_window()
    try:
        web_path(app, root, base)
        unexpected_path(app, root, base)
        gamemaker_path(app, root, base)
        game, name, path = unity_path(app, root, base)
        root.destroy()  # PatchSmith closed, the player plays; then it is reopened
        root, app = open_window()
        unity_path_after(app, root, base, game, name, path)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
    unclear = [f for f in WINDOWS if not is_clear(f) and f[1] not in ("Restore", "Install the local translator?")]
    for f in unclear:
        print("FINESTRA SENZA PASSO DOPO O CON NOMI TECNICI:", f[1], "|", " ".join(f[2].split()), f[3])
    check("ogni finestra mostrata dice il passo dopo, senza nomi tecnici (%d finestre)" % len(WINDOWS), not unclear)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
