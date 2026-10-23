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
Game profile, translation memory and settings go to the PATCHSMITH_DATA folder (test/run_all.py creates a temporary
one); without it, to "data-gui-test" next to the test game: never among PatchSmith's real data. The settings that
were already in that folder are put back as they were at the end (even if PATCHSMITH_DATA points to the real data).
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

import fake_web_game  # noqa: E402
from patchsmith import (auto as automatic, outcome, gui, ollama, project, report,
                        theme)  # noqa: E402

# the expected title of the "Analyze and translate" end dialog for each outcome
TITLES = {"OK": "Translation installed", "PARTIAL": "Translation partially installed",
          "NOTHING_TO_DO": "Nothing to do", "OFFICIAL": "The game already has your language",
          "NOT_SUPPORTED": "Game not yet supported", "ANTICHEAT": "Game with anti-cheat",
          "GAME_OPEN": "The game is open", "TRANSLATOR_BLOCKED": "Translation interrupted",
          "TRANSLATOR_MISSING": "Translator not ready", "ALREADY_ORIGINAL": "Game already original",
          "PERMISSIONS": "Locked files", "WRONG_FOLDER": "Wrong folder",
          "ERROR": "Operation failed"}
WINDOWS = []  # (title, text, buttons) of the choice dialogs shown by the interface
CHECKS = []


def check(name, condition):
    CHECKS.append(bool(condition))
    print("%s: %s" % (name, "OK" if condition else "NO"))


def _choose(_g, _c, title_text, text, buttons, kind="info"):
    WINDOWS.append((title_text, text, list(buttons)))
    print("WINDOW:", title_text, "|", " ".join(text.split())[:160], "|", [p for p, _ in buttons])
    return None  # like closing with OK: no action


WARNINGS = []  # titles of the warning dialogs


def _inform(_g, _c, title_text, text):
    WARNINGS.append(title_text)
    print("WARNING:", title_text, "|", " ".join(text.split())[:200])


def main(folder):
    # the warning dialogs and the questions would wait for a click: they are printed, and questions are answered "no"
    theme.inform = _inform
    theme.warn = theme.error = theme.inform
    theme.ask = lambda _g, _c, title_text, text: print("QUESTION (answer: no):", title_text) or False
    theme.choose = _choose
    gui.show_file = lambda _p: None
    reports = os.path.join(os.path.dirname(os.path.abspath(folder)), "reports-gui-test")
    report.desktop_folder = lambda: reports  # never the real Desktop
    # the report must not copy the real logs of the live translator (components/ollama)
    ollama.folder = lambda: os.path.join(os.path.dirname(os.path.abspath(folder)), "ollama-gui-test")
    automatic.GAME_SPACE = automatic.DATA_SPACE = 0  # the free space of the test disk does not count here
    # the graphics card (for the proposed model) is simulated: 8 GB -> Quality (12B)
    ollama.recommended_model = lambda gb=None: ("12b", "Your graphics card has 8 GB of memory: I recommend the "
                                                  "Quality model (12B).")
    if not os.environ.get("PATCHSMITH_DATA"):
        project.DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(folder)), "data-gui-test")
    gui.SETTINGS = None  # those of the test data folder
    settings_before = _read_bytes(gui._settings())  # the window rewrites them: they are put back at the end
    # the options of GameMaker games are in %LOCALAPPDATA%\<game name>: a test copy shares them with the real game,
    # so the test uses a folder of its own
    os.environ["LOCALAPPDATA"] = os.path.join(os.path.dirname(os.path.abspath(folder)), "appdata-gui-test")
    try:
        path, outcome_ok = _trial(folder, reports)
        _trial_saved_languages()
        _trial_live_journal()
    finally:  # even if the test stops halfway
        if settings_before is None:
            if os.path.exists(gui._settings()):
                os.remove(gui._settings())
        else:
            with open(gui._settings(), "wb") as f:
                f.write(settings_before)
    settings_back = _read_bytes(gui._settings()) == settings_before
    print("settings as they were before:", "OK" if settings_back else "NO")
    outcome_ok = outcome_ok and settings_back and all(CHECKS)
    print("GUI TEST:", "OK" if outcome_ok else "FAILED")
    return path, outcome_ok


def _wait_for(app, root, seconds=120):
    """Waits for the end of the running job and lets the last messages (and the end dialog) arrive."""
    start = time.time()
    while (app.work is None or app.work.is_alive()) and time.time() - start < seconds:
        root.update()
        time.sleep(0.05)
    for _ in range(20):
        root.update()
        time.sleep(0.05)


def _logs():
    return sorted(glob.glob(os.path.join(glob.escape(gui.logs_folder()), "*.log")), key=os.path.getmtime)


def _read_text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _trial_saved_languages():
    """Folder, From and To can only be chosen (no hand-typed text); invalid saved languages (old settings) go back
    to the defaults."""
    with open(gui._settings(), "w", encoding="utf-8") as f:
        json.dump({"from": "Klingon", "to": "xx-zz", "folder": ""}, f)
    root = tk.Tk()
    try:
        app = gui.App(root)
        free_entries = [w for w in root.winfo_children()[0].winfo_children() if isinstance(w, ttk.Entry)]
        check("folder, From and To can only be chosen; invalid saved languages -> the default ones",
                  app.src_lang.get() == gui.language.label(gui.language.AUTO)
                  and app.a.get() == gui.language.label(gui.language.windows_language())
                  and free_entries and all(str(w.cget("state")) == "readonly" for w in free_entries))
    finally:
        root.destroy()
    # translator and key chosen and the window closed without doing anything else: on reopening they are still there
    root = tk.Tk()
    app = gui.App(root)
    app.translator.set(gui.TRANSLATORS["deepl"])
    app._change_translator(just_chosen=True)
    app.credential.set(" test-key:fx ")
    app._close()
    root = tk.Tk()
    try:
        app = gui.App(root)
        check("DeepL translator and key chosen, window closed: still chosen when it is reopened",
                  app._chosen == "deepl" and app.credential.get() == "test-key:fx")
    finally:
        root.destroy()


def _trial_live_journal():
    """During play: the new lines of the translator's journal summarized in the log (only those from now on)."""
    from patchsmith import xunity_service
    name = "live-game-test"
    journal = xunity_service.journal_path(name)
    os.makedirs(os.path.dirname(journal), exist_ok=True)
    lines = [{"text": "Old line", "outcome": "ok", "backend": "local", "response": "Riga vecchia"}]
    with open(journal, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in lines))
    root = tk.Tk()
    try:
        app = gui.App(root)
        app._journal_read[name] = os.path.getsize(journal)  # as at the first look: the earlier history does not count
        new_lines = [{"text": "Upgrades", "outcome": "ok", "backend": "local", "response": "Potenziamenti"},
                 {"text": "Settings", "outcome": "ok", "backend": "memory", "response": "Impostazioni"},
                 {"text": "Hire {{A}} warriors", "outcome": "ok", "backend": "local",
                  "response": "Assumi {{A}} guerrieri"},
                 {"text": "Strange text", "outcome": "rejected", "backend": "local", "response": "Strange text"}]
        with open(journal, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in new_lines))
        app._summarize_journal(name)
        app._summarize_journal(name)  # nothing new: no extra line
        root.update()
        app._drain_queue()
        text = app.text.get("1.0", "end")
        check("while playing: new translated phrases summarized in the log (not those from the memory nor the "
                  "earlier history), only once",
                  text.count("While playing: 2 new phrases translated") == 1 and "1 left untranslated" in text
                  and "Riga vecchia" not in text)
    finally:
        root.destroy()
        os.remove(journal)


def _trial(folder, reports):
    root = tk.Tk()
    app = gui.App(root)
    start = time.time()
    while app.local_model.get() != gui.LOCAL_MODELS["12b"] and time.time() - start < 5:
        root.update()  # (the model suited to the graphics card comes from a thread)
        time.sleep(0.02)
    check("first start without a saved translator: 'Local translator' to install with 'Install…' and the model "
              "suited to the graphics card (12B with 8 GB)", app.translator.get() == gui.TRANSLATORS["local"]
              and app._chosen == "local" and app.translator_status.cget("text") == "to install"
              and app.btn_install.winfo_manager() == "pack" and app.local_model.get() == gui.LOCAL_MODELS["12b"])
    app.folder.set(folder)
    app.forced_backend = "fake"  # test translator, not in the menu
    app._translate()
    # while it works: no second job, no change of translator or installation halfway
    first = app.work
    app._start("Second job", lambda _c: None)
    one_job = app.work is first and all(w.instate(["disabled"]) for w in (
        app.btn_install, app.translator_choice, app.model_choice, app.btn_translate, app.btn_report))
    print("one job at a time, controls disabled:", "OK" if one_job else "NO")
    _wait_for(app, root)
    log_file = app.text.get("1.0", "end")
    state = app.state.get()
    outcome_ok = "Done. Start the game" in log_file and "ERROR" not in log_file and one_job \
        and app.translator_choice.instate(["!disabled", "readonly"])
    print("state:", state)
    print("\n".join(log_file.strip().splitlines()[-6:]))

    # end dialog and log on disk
    check("end dialog 'Translation installed' also for a web game, with the summary",
              WINDOWS and WINDOWS[-1][0] == "Translation installed" and "Translated texts:" in WINDOWS[-1][1])
    saved_items = _logs()
    text = _read_text(saved_items[-1]) if saved_items else ""
    check("log saved to disk (logs/YYYYMMDD-HHMMSS-<game>.log) with the operation and the outcome",
              saved_items and "Analysis and translation" in text and "Done. Start the game" in text
              and "Result: OK" in text and os.path.basename(saved_items[-1])[:8].isdigit())

    # a dialog for each outcome, with its title
    app._operation = "translate"
    wrong_seeds = []
    for code, expected in TITLES.items():
        before = len(WINDOWS)
        app._show_outcome("Analysis and translation", outcome.Outcome(code, "Test reason.", engine="gamemaker",
                                                              written=code in ("OK", "PARTIAL"), translated_items=3,
                                                              remaining=1 if code == "PARTIAL" else 0,
                                                              remaining_examples=["Quit"]))
        if len(WINDOWS) != before + 1 or WINDOWS[-1][0] != expected or "smith.py" in WINDOWS[-1][1]:
            wrong_seeds.append(code)
    check("dialog title for each outcome%s" % (": wrong " + ", ".join(wrong_seeds) if wrong_seeds else ""),
              not wrong_seeds)
    windows = len(WINDOWS)
    app._show_outcome("Analysis and translation", outcome.Outcome("NOT_SUPPORTED", actions=["experimental"],
                                                             extra={"detected_engine": "RenPy"}))
    check("unsupported engine with text files: question about the experimental translation",
              len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Engine not yet supported"
              and "RenPy" in WINDOWS[-1][1] and ("Yes", "experimental") in WINDOWS[-1][2])

    # unexpected error during the job: dialog with the type, traceback in the log on disk
    real = automatic.analyze_and_translate

    def faulty(*_a, **_k):
        return {}["game_folder"]

    automatic.analyze_and_translate = faulty
    try:
        app._translate()
        _wait_for(app, root)
    finally:
        automatic.analyze_and_translate = real
    text = _read_text(_logs()[-1])
    check("unexpected error: 'Unexpected error' dialog with the type and 'Create report'",
              WINDOWS[-1][0] == "Unexpected error" and "KeyError" in WINDOWS[-1][1]
              and ("Create report", "create_report") in WINDOWS[-1][2])
    check("unexpected error: in the log on disk with the traceback",
              "Traceback (most recent call last)" in text and "KeyError: 'game_folder'" in text
              and "in faulty" in text)

    # error in a window command (report_callback_exception): log and dialog
    windows = len(WINDOWS)
    root.after(0, lambda: 1 / 0)
    for _ in range(10):
        root.update()
        time.sleep(0.02)
    text = _read_text(_logs()[-1])
    check("error in a window command: in the log on disk and in a dialog",
              "ZeroDivisionError" in text and "Traceback" in text and len(WINDOWS) == windows + 1
              and WINDOWS[-1][0] == "Unexpected error")

    # after an operation without a folder (installation of the local translator) a window error still goes to the
    # 'interface' log; a permission error in a command does not offer "Try again" (it would repeat the last
    # operation on the game) nor advice about the game
    app._start("Installing the local translator", lambda _c: True, needs_folder=False, operation="install")
    _wait_for(app, root)
    windows = len(WINDOWS)

    def permissions():
        raise PermissionError(13, "Accesso negato", "C:/test/blocked.json")

    root.after(0, permissions)
    for _ in range(10):
        root.update()
        time.sleep(0.02)
    names = [os.path.basename(p) for p in _logs()]
    check("window error after installing the local translator: 'interfaccia' log, dialog without "
              "'Try again'", names[-1].endswith("-interfaccia.log") and names[-2].endswith("-traduttore-locale.log")
              and len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Unexpected error"
              and not any(a == "close_game" for _, a in WINDOWS[-1][2]) and "Program Files" not in WINDOWS[-1][1])

    # an error while looking for the game profile for the log name does not leave the buttons disabled
    real = automatic.find_profile

    def broken_profile(_c):
        raise AttributeError("'list' object has no attribute 'get'")

    automatic.find_profile = broken_profile
    try:
        app._start("Log test", lambda _c: True)
        _wait_for(app, root)
    finally:
        automatic.find_profile = real
    check("log: a damaged profile does not block the operation (buttons active again, log open)",
              app.btn_translate.instate(["!disabled"]) and "Log test" in _read_text(_logs()[-1]))

    # "Use the local translator" after an error (which has no choices in the outcome): Analyze and translate again
    # with the same choices as last time (e.g. "Translate anyway")
    calls, chosen = [], app.translator.get()
    app._translate = lambda **kw: calls.append(kw)
    app._local_ready = lambda: True
    app._change_translator = lambda first=False: None
    app._translate_choices = {"force": True, "translate_anyway": True}
    try:
        app._action("use_local", outcome.Outcome("TRANSLATOR_MISSING", "Test", actions=["install_local"]))
    finally:
        del app._translate, app._local_ready, app._change_translator
        app._translate_choices = {"force": False, "translate_anyway": False}
        app.translator.set(chosen)
    check("'Use the local translator' after an error: same choices as the last Analyze and translate",
              calls == [{"force": True, "translate_anyway": True}])

    _trial_check(app, root, folder)
    _trial_retranslate(app, root)
    _trial_preflight(app, root, folder)
    _trial_install(app, root)
    _trial_fixes(app, root, folder)

    # restore from the window: outcome OK, 'Originals restored'
    app._restore(ask=False)
    _wait_for(app, root)
    check("restore: 'Originals restored' dialog", WINDOWS[-1][0] == "Originals restored")

    # Create report: zip in the test folder (instead of the Desktop) with the operation's log
    already_present = set(glob.glob(os.path.join(glob.escape(reports), "*.zip")))  # from a previous run
    app._create_report()
    _wait_for(app, root)
    created = sorted(set(glob.glob(os.path.join(glob.escape(reports), "*.zip"))) - already_present)
    names = zipfile.ZipFile(created[0]).namelist() if created else []
    check("Create report: 'Report generated' dialog and zip with the logs (also that of the window error), "
              "profile and settings", WINDOWS[-1][0] == "Report generated" and len(created) == 1
              and any(n.startswith("logs/") and n.endswith("-lantern-keeper.log") for n in names)
              and any(n.endswith("-interfaccia.log") for n in names)
              and "settings.json" in names and any(n.startswith("profile/") for n in names))

    # Desktop that cannot be written to (e.g. controlled folder access): the report goes to the logs
    blocked = os.path.join(reports, "not-a-folder")
    open(blocked, "w").close()  # a file instead of the folder: nothing can be written inside
    already_present = set(glob.glob(os.path.join(glob.escape(gui.logs_folder()), "*.zip")))
    report.desktop_folder = lambda: blocked
    try:
        app._create_report()
        _wait_for(app, root)
    finally:
        report.desktop_folder = lambda: reports
        os.remove(blocked)
    fallback = sorted(set(glob.glob(os.path.join(glob.escape(gui.logs_folder()), "*.zip"))) - already_present)
    check("Create report with the Desktop blocked: report in the logs folder, 'Report generated' dialog",
              len(fallback) == 1 and WINDOWS[-1][0] == "Report generated" and fallback[0] in WINDOWS[-1][1])
    for p in fallback:  # it does not stay among the test data
        os.remove(p)

    automatic.restore(folder, print)
    path = automatic.find_profile(folder)
    root.destroy()
    return path, outcome_ok


def _trial_check(app, root, folder):
    """After-play check: check of the chosen game, at startup (without dialogs), line under the folder,
    "Last session details" with the buttons for the right game, notice that PatchSmith was updated while the window
    is open."""
    from patchsmith import checks
    # from the button, on the web game just translated: nothing to do, end dialog and line
    windows = len(WINDOWS)
    app._check()
    _wait_for(app, root)
    check("check of the chosen game: end dialog and line 'After the last session of ...'",
              len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Check after playing"
              and app.check_status.get() == "After the last session of %s: no problem"
              % os.path.basename(os.path.normpath(folder)))
    check("check: log on disk with the result", "Check after playing" in _read_text(
        _logs()[-1]) and "Result: OK" in _read_text(_logs()[-1]))
    # at startup, with a problem to do in another game: no dialog, the line with 'Last session details'
    other = os.path.join(os.path.dirname(os.path.abspath(folder)), "Other Game")
    real = checks.check

    def fake(path, apply=True, log=print, translator=None, events=None, **_k):
        log("Check after playing: test")
        return [checks.Problem("CRASH", "error", "The game closed suddenly.", action="restore",
                                   state="to_do"),
                checks.Problem("TEMP_FILES", "info", "Removed 2 temporary files.", state="fixed")]

    real_as_outcome = checks.as_outcome
    checks.check = fake
    checks.as_outcome = lambda problems, path=None, title_text=None: real_as_outcome(problems, None, "Other Game")
    try:
        windows = len(WINDOWS)
        app._check(["fake-profile"])
        _wait_for(app, root)
    finally:
        checks.check, checks.as_outcome = real, real_as_outcome
    check("check at startup: no dialog, line '1 problem fixed, 1 to do' and 'Last session details'",
              len(WINDOWS) == windows and app.check_status.get() == "After the last session of Other Game: "
              "1 problem fixed, 1 to do" and app.btn_details.winfo_manager() == "pack")
    names = [os.path.basename(p) for p in _logs()]
    check("check at startup: 'controllo' log", names[-1].endswith("-controllo.log"))
    # Last session details: the dialog with "To do" and "Restore originals"; once chosen, it applies to that game
    app.check_outcomes[0].extra["folder"] = other
    choices, real_restore, real_choose = [], app._restore, theme.choose

    def choose(_g, _c, title_text, text, buttons, kind="info"):
        _choose(_g, _c, title_text, text, buttons, kind)
        return "restore"

    app._restore = lambda ask=True: choices.append(app.folder.get())
    theme.choose = choose
    try:
        app._check_details()
    finally:
        app._restore, theme.choose = real_restore, real_choose
        app.folder.set(folder)
    check("Last session details: 'Something to do' with 'Restore originals', for that game's folder",
              WINDOWS[-1][0] == "Something to do" and "To do:" in WINDOWS[-1][1]
              and ("Restore originals", "restore") in WINDOWS[-1][2] and choices == [os.path.normpath(other)])
    # a game that cannot be checked (unexpected error) does not stop the others: "to do" with the report
    def broken(path, apply=True, log=print, translator=None, events=None, **_k):
        if path == "broken":
            raise KeyError("game_folder")
        return []

    checks.check = broken
    try:
        app._check(["broken", "good"])
        _wait_for(app, root)
    finally:
        checks.check = real
    errors = [p for e in app.check_outcomes for p in e.extra.get("checks", []) if p["code"] == "ERROR"]
    check("check at startup: a game with an error does not stop the others (error to do, with the report)",
              len(app.check_outcomes) == 2 and len(errors) == 1 and errors[0]["action"] == "create_report"
              and "KeyError" in errors[0]["message"] and "1 to do" in app.check_status.get())
    # "Try again" in the dialog of a check (game open): the check of that game again, not the last operation done
    # (e.g. a translation)
    redone_calls, real_check = [], app._check
    app._check = lambda profiles=None, at_open=None: redone_calls.append((profiles, at_open))
    try:
        app._action("close_game", outcome.Outcome("GAME_OPEN", actions=["close_game"],
                                                extra={"checks": [], "profile": "profile-x"}))
    finally:
        app._check = real_check
    check("'Try again' after a check with the game open: redoes the check of that game, with the dialog",
              redone_calls == [(["profile-x"], False)])
    # at startup, but you have already started something: it does not start right away, the line says so; it starts
    # by itself as soon as that finishes
    work_before = app.work
    app.work = object()
    app._check = lambda profiles=None, at_open=None: redone_calls.append((profiles, at_open))
    try:
        redone_calls.clear()
        app._start_initial_check(["fake-profile"])
        line = app.check_status.get()
        for _ in range(10):
            root.update()
            time.sleep(0.02)
        during = list(redone_calls)
        app.work = None
        start = time.time()
        while not redone_calls and time.time() - start < 4:
            root.update()
            time.sleep(0.02)
    finally:
        app._check = real_check
        app.work = work_before
    del app._check
    check("check at startup during another operation: the line says so and it starts as soon as that finishes "
              "(%s)" % redone_calls, "as soon as the current operation finishes" in line and during == []
              and redone_calls == [(["fake-profile"], None)])
    # "Create report" from the Last session details of another game: the report is for that game, with its outcome
    requested_reports, real_create_report = [], report.create
    report.create = lambda c, dest_lang=None, outcome=None, **_k: requested_reports.append((c, outcome)) or \
        os.path.join(dest_lang or "", "fake-report.zip")
    e_other = outcome.Outcome("PARTIAL", actions=["create_report"], extra={"checks": [], "folder": other})
    try:
        app._action("create_report", e_other)
        _wait_for(app, root)
    finally:
        report.create = real_create_report
        app.folder.set(folder)
    check("'Create report' from the Last session details of another game: report of that game with its outcome",
              requested_reports == [(os.path.normpath(other), e_other)])
    # PatchSmith updated while the window is open: notice, only once
    real_fingerprint = gui.code_fingerprint
    gui.code_fingerprint = lambda: ("9.9.9", 0)
    try:
        before = len(WARNINGS)
        app._start("Test", lambda _c: None, needs_folder=False, keep_log=False)
        _wait_for(app, root)
        app._start("Test", lambda _c: None, needs_folder=False, keep_log=False)
        _wait_for(app, root)
    finally:
        gui.code_fingerprint = real_fingerprint
    check("PatchSmith updated with the window open: warning to reopen it, only once",
              WARNINGS[before:] == ["PatchSmith has been updated"])


def _trial_retranslate(app, root):
    """The check at startup does not re-translate the journal's lines (the window would freeze): a
    "Re-translate N lines" button in the line, which (like "Re-translate the phrases" in the Last session details)
    redoes the check with the re-translation."""
    from patchsmith import checks
    received, real, real_as_outcome = [], checks.check, checks.as_outcome

    def fake(path, apply=True, log=print, translator=None, events=None, retranslate=True):
        received.append(retranslate)
        return [checks.Problem("SENTENCES_TO_RETRANSLATE", "warning", "While you were playing, 3 phrases were left "
                                   "untranslated.", action="retranslate", state="to_do", extra={"sentences": 3})]

    def fake_as_outcome(problems, path=None, title_text=None):
        e = real_as_outcome(problems, None, "Journal Game")
        e.extra["profile"] = path
        return e

    checks.check, checks.as_outcome = fake, fake_as_outcome
    try:
        app._check(["journal-profile"])
        _wait_for(app, root)
    finally:
        checks.check, checks.as_outcome = real, real_as_outcome
    check("check at startup: the journal's phrases are not re-translated now, 'Re-translate 3 phrases' button "
              "in the line", received == [False] and app.btn_retranslate.winfo_manager() == "pack"
              and app.btn_retranslate.cget("text") == "Re-translate 3 phrases")
    redone_calls = []
    app._check = lambda profiles=None, at_open=None, retranslate=None: redone_calls.append(
        (profiles, at_open, retranslate))
    try:
        app._retranslate()
        app._action("retranslate", app.check_outcomes[0])
    finally:
        del app._check
    check("'Re-translate' (line and Last session details): the check of that game with re-translation and dialogs",
              redone_calls == [(["journal-profile"], False, True)] * 2)
    app.check_outcomes = []
    app._update_check_line()


def _trial_preflight(app, root, folder):
    """The preliminary checks: with a problem the operation does not start (_start is not called) and the dialog
    offers the right step; then a copy of the game in another folder reuses its translations."""
    from patchsmith import limiter, system
    started, responses = [], []
    app._start = lambda description, *_a, **_k: started.append(description)
    real_choose = theme.choose

    def choose(_g, _c, title_text, text, buttons, kind="info"):
        _choose(_g, _c, title_text, text, buttons, kind)
        return responses.pop(0) if responses else None  # no answer: Cancel (or OK)

    theme.choose = choose
    real_is_open = system.game_open
    try:
        # game open: "Try again"; when cancelled nothing starts
        system.game_open = lambda _c: True
        try:
            app._translate()
            app._reapply()
            app._restore(ask=False)
        finally:
            system.game_open = real_is_open
        check("game open before Analyze and translate, Reapply and Restore: 'The game is open' with "
                  "'Try again', no operation started", started == []
                  and [f[0] for f in WINDOWS[-3:]] == ["The game is open"] * 3 and ("Try again", "retry")
                  in WINDOWS[-1][2])
        state = {"is_open": True}
        system.game_open = lambda _c: state["is_open"]
        responses[:] = ["retry"]
        try:
            app._translate()
            state["is_open"] = False  # game closed: "Try again" redoes the checks and starts
            for _ in range(15):
                root.update()
                time.sleep(0.02)
        finally:
            system.game_open = real_is_open
        check("'Try again' with the game closed: the checks again, then the operation starts",
                  started == ["Analysis and translation"])
        started.clear()
        # protected folder and wrong language: it stops, with the reason
        real_write_check = automatic.check_writable
        automatic.check_writable = lambda _c: PermissionError(13, "Accesso negato")
        try:
            app._translate()
        finally:
            automatic.check_writable = real_write_check
        check("protected folder: dialog with the reason and only OK, no operation",
                  started == [] and WINDOWS[-1][0] == "Protected game folder" and WINDOWS[-1][2] == [("OK", None)]
                  and "administrator" in WINDOWS[-1][1])
        a_before = app.a.get()
        app.a.set("Italianoo")
        try:
            app._translate()
        finally:
            app.a.set(a_before)
        check("unrecognized language in To: 'Language to check', no operation",
                  started == [] and WINDOWS[-1][0] == "Language to check" and "Italianoo" in WINDOWS[-1][1])
        # Google paused: "Install the local translator" chooses it, installs it and then repeats Analyze and translate
        chosen, app.forced_backend = app.translator.get(), None
        app.translator.set(gui.TRANSLATORS["google"])
        app._change_translator()
        limiter.Limiter("google", log=None).blocked(429, "test")
        installs = []
        app._install_local = lambda then=None: installs.append(then)
        try:
            app._translate()
            pause = WINDOWS[-1]
            responses[:] = ["install_local"]
            app._translate()
            after_pause = app._chosen
            app._translate()  # now the local translator is chosen, and it is not installed
            absent = WINDOWS[-1]
            # the yes to the official language (consent) translates nothing: no questions about the translator
            windows = len(WINDOWS)
            app._translate(consent=True)
            consent = (list(started), [f[0] for f in WINDOWS[windows:]])
            started.clear()
        finally:
            limiter.Limiter("google", log=None).reset()
            del app._install_local
            app.translator.set(chosen)
            app._change_translator()
            app.forced_backend = "fake"
        check("Google paused: 'Translator paused' with 'Install the local translator' and 'Continue anyway'; "
                  "chosen: local translator and installation, then Analyze and translate again",
                  started == [] and pause[0] == "Translator paused" and ("Install the local translator",
                                                                        "install_local") in pause[2]
                  and ("Continue anyway", "continue") in pause[2] and after_pause == "local"
                  and len(installs) == 1 and callable(installs[0]))
        check("local translator not installed: 'Install now' before starting (or 'Continue anyway': the official "
                  "language needs no translation), no operation",
                  started == [] and absent[0] == "Local translator to install"
                  and ("Install now", "install_local") in absent[2] and ("Continue anyway", "continue")
                  in absent[2])
        check("consent to the official language with the local translator to install: it starts with no questions "
                  "about the translator (no translation needed)", consent == (["Analysis and translation"], []))
    finally:
        system.game_open = real_is_open
        del app._start
        theme.choose = real_choose

    # a copy of the game (same folder name, elsewhere): "Yes, use them again" -> the original's translation memory
    copy_dir = os.path.join(os.path.dirname(os.path.abspath(folder)), "copy-gui-test",
                         os.path.basename(os.path.normpath(folder)))
    shutil.rmtree(os.path.dirname(copy_dir), ignore_errors=True)
    for p, prof in list(automatic.list_profiles()):  # (from a previous run of the test launched on its own)
        if prof.get("game_folder") and automatic._inside(prof["game_folder"], os.path.dirname(copy_dir)):
            os.remove(p)
    fake_web_game.create(copy_dir)
    original = json.load(open(automatic.find_profile(folder), encoding="utf-8"))
    entries = json.load(open(project.memory_path(original, test=True), encoding="utf-8"))
    theme.choose = lambda _g, _c, title_text, text, buttons, kind="info": (
        _choose(_g, _c, title_text, text, buttons, kind)
        or ("reuse" if title_text == "Same game already translated?" else None))
    try:
        app.folder.set(copy_dir)
        windows = len(WINDOWS)
        app._translate()
        _wait_for(app, root)
    finally:
        theme.choose = real_choose
        app.folder.set(folder)
    question = next((f for f in WINDOWS[windows:] if f[0] == "Same game already translated?"), None)
    new = automatic.find_profile(copy_dir)
    profile_copy = json.load(open(new, encoding="utf-8")) if new else {}
    copy_memory = json.load(open(project.memory_path(profile_copy, test=True), encoding="utf-8")) \
        if new else {}
    log_file = app.text.get("1.0", "end")
    check("copy of the game in another folder: 'Same game already translated?' with 'Yes, use them again'; its "
              "translations reused (new profile with the same memory)", question
              and ("Yes, use them again", "reuse") in question[2]
              and new and profile_copy.get("name") != original["name"]
              and all(copy_memory.get(k) == v for k, v in entries.items())
              and "I'm reusing the translations" in log_file
              and WINDOWS[-1][0] == "Translation installed")
    automatic.restore(copy_dir, print)


def _trial_install(app, root):
    """The installation of the local translator requested before starting ("Install now"): after the successful test
    sentence the operation that was waiting restarts by itself; if the test fails a dialog says so and the operation
    does not start. Then: the check at startup waits for an open dialog to close (the operation chosen there would not
    start, one job at a time)."""
    from patchsmith import backend
    actual = ollama.download_description, ollama.install, backend.probe_local, theme.ask
    redone, tests, trial_outcomes = [], [], [
        {"ok": True, "message": "ready, on the graphics card: \"Start game\" -> \"Inizia\".", "gpu": 1.0},
        {"ok": False, "message": "the translation test failed: the local translator is not responding.",
         "gpu": None}]
    ollama.download_description = lambda selected: ["fake to download"]
    ollama.install = lambda selected, log=print: True
    backend.probe_local = lambda model=None, log=print, **_k: tests.append(model) or trial_outcomes.pop(0)
    theme.ask = lambda *_a: True
    try:
        warnings = len(WARNINGS)
        app._install_local(then=lambda: redone.append("again"))
        _wait_for(app, root)
        first_result = (list(redone), app.state.get(), len(WARNINGS) - warnings)
        app._install_local(then=lambda: redone.append("again"))
        _wait_for(app, root)
    finally:
        ollama.download_description, ollama.install, backend.probe_local, theme.ask = actual
    check("installation requested before starting: test sentence succeeded -> the operation restarts by itself",
              first_result[0] == ["again"] and "Local translator installed: ready" in first_result[1]
              and not first_result[2] and len(tests) == 2)
    check("test sentence failed: dialog with the reason, the waiting operation does not start",
              redone == ["again"] and WARNINGS[-1] == "Local translator test failed"
              and "not responding" in app.state.get())
    # already installed: "Install now" runs the waiting operation right away; preliminary checks that fail
    # (unexpected error): it starts anyway, and the log says so
    ollama.download_description = lambda selected: []
    immediate, real_cp = [], automatic.preflight_checks
    automatic.preflight_checks = lambda *_a, **_k: 1 / 0
    try:
        app._install_local(then=lambda: immediate.append("immediately"))
        choices = app._preflight("translate", lambda: None, "fake")
        for _ in range(10):
            root.update()
            time.sleep(0.02)
    finally:
        ollama.download_description, automatic.preflight_checks = actual[0], real_cp
    check("local translator already installed: the waiting operation starts right away; pre-start checks "
              "failed: it starts anyway, with a line in the log", immediate == ["immediately"]
              and choices == {"reuse": None}
              and "Pre-start checks failed" in app.text.get("1.0", "end"))

    # the check at startup arrives while a dialog is open: it waits for it to close
    window = tk.Toplevel(root)
    window.grab_set()
    check_list, work = [], app.work
    app.work = None
    app._check = lambda profiles=None, **_k: check_list.append(profiles)
    try:
        app._start_initial_check(["waiting-profile"])
        for _ in range(25):
            root.update()
            time.sleep(0.02)
        during = list(check_list)
        window.grab_release()
        window.destroy()
        root.update()
        # (right after closing it still waits a moment: the choice made there starts first)
        immediate = list(check_list)
        start = time.time()
        while not check_list and time.time() - start < 3:
            root.update()
            time.sleep(0.02)
    finally:
        del app._check
        app.work = work
    check("check at startup with a dialog open: it starts only after the dialog is closed",
              during == [] and immediate == [] and check_list == [["waiting-profile"]])
    # dialog closed with a choice that starts an operation right after (e.g. "Try again"): the check does not start
    # right away, it waits for the operation to finish
    window = tk.Toplevel(root)
    window.grab_set()
    check_list[:] = []
    app.work = None
    app._check = lambda profiles=None, **_k: check_list.append(profiles)
    try:
        app._start_initial_check(["waiting-profile"])
        for _ in range(20):
            root.update()
            time.sleep(0.02)
        window.grab_release()
        window.destroy()
        root.after(250, lambda: setattr(app, "work", work))  # the operation chosen in the dialog (within 300 ms)
        start = time.time()
        while time.time() - start < 1.2:
            root.update()
            time.sleep(0.02)
    finally:
        del app._check
        app.work = work
    check("dialog closed with an operation that starts right after: the check at startup waits for it to finish",
              check_list == [] and "as soon as the current operation finishes" in app.check_status.get())
    if app._pending_check:  # (in the test it must not restart later)
        root.after_cancel(app._pending_check)
        app._pending_check = None
    app.check_status.set("")


def _trial_fixes(app, root, folder):
    """"Fix translations…" (step 6): the window with the list, a translation changed in the line and "Save and apply to
    the game": the translation memory (the test translator's) stays valid JSON with the new value and Reapply starts on
    its own and changes the game's file. Then "Fix translations" in the end dialog with some texts left."""
    import hashlib
    app.folder.set(folder)
    texts_file = os.path.join(folder, *fake_web_game.TEXTS_FILE.split("/"))
    with open(texts_file, "rb") as f:
        before = hashlib.sha256(f.read()).hexdigest()
    window = app._fix()
    lines = len(window.tree.get_children()) if window else 0
    window._go_to("Start a new game")
    window.translation_text.delete("1.0", "end")
    window.translation_text.insert("1.0", "Nuova partita (corretta)")
    for _ in range(5):
        root.update()
    cell = window.tree.item(window.tree.selection()[0], "values")[1]
    window.save()
    _wait_for(app, root)
    profile = json.load(open(automatic.find_profile(folder), encoding="utf-8"))
    try:
        memory = json.load(open(project.memory_path(profile, test=True), encoding="utf-8"))
    except ValueError:
        memory = {}
    with open(texts_file, "rb") as f:
        after = f.read()
    check("Fix translations: window with the list, line changed and saved: valid memory with the new value, "
              "Reapply on its own that changes the game's file", lines > 10 and cell == "Nuova partita (corretta)"
              and memory.get("Start a new game") == "Nuova partita (corretta)"
              and hashlib.sha256(after).hexdigest() != before and "Nuova partita (corretta)" in after.decode("utf-8")
              and WINDOWS[-1][0] == "Translation reapplied" and not window.top.winfo_exists())
    windows = len(WINDOWS)
    app._show_outcome("Analysis and translation", outcome.Outcome("PARTIAL", written=True, translated_items=3,
                                                             remaining=1, remaining_examples=["Quit"]))
    check("end dialog with texts left: 'Fix translations' button",
              len(WINDOWS) == windows + 1 and ("Fix translations", "fix") in WINDOWS[-1][2])


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path, outcome_ok = main(sys.argv[1])
    print("profile used:", path)
    sys.exit(0 if outcome_ok else 1)
