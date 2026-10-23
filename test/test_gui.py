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

import fake_web_game  # noqa: E402
from patchsmith import (auto as automatic, outcome, gui, ollama, project, report,
                        theme)  # noqa: E402

# the expected title of the "Analyze and translate" end dialog for each outcome
TITLES = {"OK": "Translation installed", "PARZIALE": "Translation partially installed",
          "NULLA_DA_FARE": "Nothing to do", "UFFICIALE": "The game already has your language",
          "NON_SUPPORTATO": "Game not yet supported", "ANTICHEAT": "Game with anti-cheat",
          "GIOCO_APERTO": "The game is open", "TRADUTTORE_BLOCCATO": "Translation interrupted",
          "TRADUTTORE_ASSENTE": "Translator not ready", "GIA_ORIGINALE": "Game already original",
          "PERMESSI": "Locked files", "CARTELLA_SBAGLIATA": "Wrong folder",
          "ERRORE": "Operation failed"}
WINDOWS = []  # (titolo, testo, pulsanti) of the choice dialogs shown by the interface
CHECKS = []


def check(name, condition):
    CHECKS.append(bool(condition))
    print("%s: %s" % (name, "OK" if condition else "NO"))


def _choose(_g, _c, title_text, text, buttons, kind="info"):
    WINDOWS.append((title_text, text, list(buttons)))
    print("FINESTRA:", title_text, "|", " ".join(text.split())[:160], "|", [p for p, _ in buttons])
    return None  # like closing with OK: no action


WARNINGS = []  # titles of the warning dialogs


def _inform(_g, _c, title_text, text):
    WARNINGS.append(title_text)
    print("AVVISO:", title_text, "|", " ".join(text.split())[:200])


def main(folder):
    # the warning dialogs and the questions would wait for a click: they are printed, and questions are answered "no"
    theme.inform = _inform
    theme.warn = theme.error = theme.inform
    theme.ask = lambda _g, _c, title_text, text: print("DOMANDA (risposta: no):", title_text) or False
    theme.choose = _choose
    gui.show_file = lambda _p: None
    reports = os.path.join(os.path.dirname(os.path.abspath(folder)), "rapporti-prova-gui")
    report.desktop_folder = lambda: reports  # never the real Desktop
    # the report must not copy the real logs of the live translator (components/ollama)
    ollama.folder = lambda: os.path.join(os.path.dirname(os.path.abspath(folder)), "ollama-prova-gui")
    automatic.GAME_SPACE = automatic.DATA_SPACE = 0  # the free space of the test disk does not count here
    # the graphics card (for the proposed model) is simulated: 8 GB -> Quality (12B)
    ollama.recommended_model = lambda gb=None: ("12b", "Your graphics card has 8 GB of memory: I recommend the "
                                                  "Quality model (12B).")
    if not os.environ.get("PATCHSMITH_DATI"):
        project.DATA_ROOT = os.path.join(os.path.dirname(os.path.abspath(folder)), "dati-prova-gui")
    gui.SETTINGS = None  # those of the test data folder
    settings_before = _read_bytes(gui._settings())  # the window rewrites them: they are put back at the end
    # the options of GameMaker games are in %LOCALAPPDATA%\<game name>: a test copy shares them with the real game,
    # so the test uses a folder of its own
    os.environ["LOCALAPPDATA"] = os.path.join(os.path.dirname(os.path.abspath(folder)), "appdata-prova-gui")
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
    print("impostazioni com'erano prima:", "OK" if settings_back else "NO")
    outcome_ok = outcome_ok and settings_back and all(CHECKS)
    print("COLLAUDO GUI:", "OK" if outcome_ok else "FALLITO")
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
        json.dump({"da": "Klingon", "a": "xx-zz", "cartella": ""}, f)
    root = tk.Tk()
    try:
        app = gui.App(root)
        free_entries = [w for w in root.winfo_children()[0].winfo_children() if isinstance(w, ttk.Entry)]
        check("cartella, Da e A solo da scegliere; lingue salvate non valide -> quelle predefinite",
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
    app.credential.set(" chiave-di-prova:fx ")
    app._close()
    root = tk.Tk()
    try:
        app = gui.App(root)
        check("traduttore DeepL e chiave scelti, finestra chiusa: alla riapertura sono ancora scelti",
                  app._chosen == "deepl" and app.credential.get() == "chiave-di-prova:fx")
    finally:
        root.destroy()


def _trial_live_journal():
    """During play: the new lines of the translator's journal summarized in the log (only those from now on)."""
    from patchsmith import xunity_service
    name = "gioco-dal-vivo-prova"
    journal = xunity_service.journal_path(name)
    os.makedirs(os.path.dirname(journal), exist_ok=True)
    lines = [{"testo": "Old line", "esito": "ok", "backend": "locale", "risposta": "Riga vecchia"}]
    with open(journal, "w", encoding="utf-8") as f:
        f.write("".join(json.dumps(r) + "\n" for r in lines))
    root = tk.Tk()
    try:
        app = gui.App(root)
        app._journal_read[name] = os.path.getsize(journal)  # as at the first look: the earlier history does not count
        new_lines = [{"testo": "Upgrades", "esito": "ok", "backend": "locale", "risposta": "Potenziamenti"},
                 {"testo": "Settings", "esito": "ok", "backend": "memoria", "risposta": "Impostazioni"},
                 {"testo": "Hire {{A}} warriors", "esito": "ok", "backend": "locale", "risposta": "Assumi {{A}} guerrieri"},
                 {"testo": "Strange text", "esito": "rifiutata", "backend": "locale", "risposta": "Strange text"}]
        with open(journal, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in new_lines))
        app._summarize_journal(name)
        app._summarize_journal(name)  # nothing new: no extra line
        root.update()
        app._drain_queue()
        text = app.text.get("1.0", "end")
        check("mentre giochi: frasi nuove tradotte riassunte nel registro (non quelle dalla memoria ne' la storia "
                  "di prima), una volta sola",
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
    check("primo avvio senza traduttore salvato: 'Traduttore locale' da installare con 'Installa…' e il modello "
              "adatto alla scheda video (12B con 8 GB)", app.translator.get() == gui.TRANSLATORS["locale"]
              and app._chosen == "locale" and app.translator_status.cget("text") == "to install"
              and app.btn_install.winfo_manager() == "pack" and app.local_model.get() == gui.LOCAL_MODELS["12b"])
    app.folder.set(folder)
    app.forced_backend = "finto"  # test translator, not in the menu
    app._translate()
    # while it works: no second job, no change of translator or installation halfway
    first = app.work
    app._start("Secondo lavoro", lambda _c: None)
    one_job = app.work is first and all(w.instate(["disabled"]) for w in (
        app.btn_install, app.translator_choice, app.model_choice, app.btn_translate, app.btn_report))
    print("un lavoro alla volta, comandi disattivati:", "OK" if one_job else "NO")
    _wait_for(app, root)
    log_file = app.text.get("1.0", "end")
    state = app.state.get()
    outcome_ok = "Done. Start the game" in log_file and "ERRORE" not in log_file and one_job \
        and app.translator_choice.instate(["!disabled", "readonly"])
    print("stato:", state)
    print("\n".join(log_file.strip().splitlines()[-6:]))

    # end dialog and log on disk
    check("finestra di fine 'Traduzione installata' anche per un gioco web, con il riepilogo",
              WINDOWS and WINDOWS[-1][0] == "Translation installed" and "Translated texts:" in WINDOWS[-1][1])
    saved_items = _logs()
    text = _read_text(saved_items[-1]) if saved_items else ""
    check("registro salvato su disco (logs/AAAAMMGG-HHMMSS-<gioco>.log) con l'operazione e l'esito",
              saved_items and "Analysis and translation" in text and "Done. Start the game" in text
              and "Result: OK" in text and os.path.basename(saved_items[-1])[:8].isdigit())

    # a dialog for each outcome, with its title
    app._operation = "traduci"
    wrong_seeds = []
    for code, expected in TITLES.items():
        before = len(WINDOWS)
        app._show_outcome("Analysis and translation", outcome.Outcome(code, "Motivo di prova.", engine="gamemaker",
                                                              written=code in ("OK", "PARZIALE"), translated_items=3,
                                                              remaining=1 if code == "PARZIALE" else 0,
                                                              remaining_examples=["Quit"]))
        if len(WINDOWS) != before + 1 or WINDOWS[-1][0] != expected or "smith.py" in WINDOWS[-1][1]:
            wrong_seeds.append(code)
    check("titolo della finestra per ogni esito%s" % (": sbagliati " + ", ".join(wrong_seeds) if wrong_seeds else ""),
              not wrong_seeds)
    windows = len(WINDOWS)
    app._show_outcome("Analysis and translation", outcome.Outcome("NON_SUPPORTATO", actions=["sperimentale"],
                                                             extra={"motore_rilevato": "RenPy"}))
    check("motore non supportato con file di testo: domanda sulla traduzione sperimentale",
              len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Engine not yet supported"
              and "RenPy" in WINDOWS[-1][1] and ("Yes", "sperimentale") in WINDOWS[-1][2])

    # unexpected error during the job: dialog with the type, traceback in the log on disk
    real = automatic.analyze_and_translate

    def faulty(*_a, **_k):
        return {}["cartella_gioco"]

    automatic.analyze_and_translate = faulty
    try:
        app._translate()
        _wait_for(app, root)
    finally:
        automatic.analyze_and_translate = real
    text = _read_text(_logs()[-1])
    check("errore imprevisto: finestra 'Errore imprevisto' con il tipo e 'Crea rapporto'",
              WINDOWS[-1][0] == "Unexpected error" and "KeyError" in WINDOWS[-1][1]
              and ("Create report", "crea_rapporto") in WINDOWS[-1][2])
    check("errore imprevisto: nel registro su disco con il traceback",
              "Traceback (most recent call last)" in text and "KeyError: 'cartella_gioco'" in text
              and "in faulty" in text)

    # error in a window command (report_callback_exception): log and dialog
    windows = len(WINDOWS)
    root.after(0, lambda: 1 / 0)
    for _ in range(10):
        root.update()
        time.sleep(0.02)
    text = _read_text(_logs()[-1])
    check("errore in un comando della finestra: nel registro su disco e in una finestra",
              "ZeroDivisionError" in text and "Traceback" in text and len(WINDOWS) == windows + 1
              and WINDOWS[-1][0] == "Unexpected error")

    # after an operation without a folder (installation of the local translator) a window error still goes to the
    # 'interfaccia' log; a permission error in a command does not offer "Try again" (it would repeat the last
    # operation on the game) nor advice about the game
    app._start("Installing the local translator", lambda _c: True, needs_folder=False, operation="installa")
    _wait_for(app, root)
    windows = len(WINDOWS)

    def permissions():
        raise PermissionError(13, "Accesso negato", "C:/prova/bloccato.json")

    root.after(0, permissions)
    for _ in range(10):
        root.update()
        time.sleep(0.02)
    names = [os.path.basename(p) for p in _logs()]
    check("errore della finestra dopo l'installazione del traduttore locale: registro 'interfaccia', finestra "
              "senza 'Riprova'", names[-1].endswith("-interfaccia.log") and names[-2].endswith("-traduttore-locale.log")
              and len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Unexpected error"
              and not any(a == "chiudi_gioco" for _, a in WINDOWS[-1][2]) and "Program Files" not in WINDOWS[-1][1])

    # an error while looking for the game profile for the log name does not leave the buttons disabled
    real = automatic.find_profile

    def broken_profile(_c):
        raise AttributeError("'list' object has no attribute 'get'")

    automatic.find_profile = broken_profile
    try:
        app._start("Prova del registro", lambda _c: True)
        _wait_for(app, root)
    finally:
        automatic.find_profile = real
    check("registro: un profilo rovinato non blocca l'operazione (pulsanti di nuovo attivi, registro aperto)",
              app.btn_translate.instate(["!disabled"]) and "Prova del registro" in _read_text(_logs()[-1]))

    # "Use the local translator" after an error (which has no choices in the outcome): Analyze and translate again
    # with the same choices as last time (e.g. "Translate anyway")
    calls, chosen = [], app.translator.get()
    app._translate = lambda **kw: calls.append(kw)
    app._local_ready = lambda: True
    app._change_translator = lambda first=False: None
    app._translate_choices = {"force": True, "translate_anyway": True}
    try:
        app._action("usa_locale", outcome.Outcome("TRADUTTORE_ASSENTE", "Prova", actions=["installa_locale"]))
    finally:
        del app._translate, app._local_ready, app._change_translator
        app._translate_choices = {"force": False, "translate_anyway": False}
        app.translator.set(chosen)
    check("'Usa il traduttore locale' dopo un errore: stesse scelte dell'ultimo Analizza e traduci",
              calls == [{"force": True, "translate_anyway": True}])

    _trial_check(app, root, folder)
    _trial_retranslate(app, root)
    _trial_preflight(app, root, folder)
    _trial_install(app, root)
    _trial_fixes(app, root, folder)

    # restore from the window: outcome OK, 'Originals restored'
    app._restore(ask=False)
    _wait_for(app, root)
    check("ripristino: finestra 'Originali ripristinati'", WINDOWS[-1][0] == "Originals restored")

    # Create report: zip in the test folder (instead of the Desktop) with the operation's log
    already_present = set(glob.glob(os.path.join(glob.escape(reports), "*.zip")))  # from a previous run
    app._create_report()
    _wait_for(app, root)
    created = sorted(set(glob.glob(os.path.join(glob.escape(reports), "*.zip"))) - already_present)
    names = zipfile.ZipFile(created[0]).namelist() if created else []
    check("Crea rapporto: finestra 'Rapporto creato' e zip con registri (anche dell'errore nella finestra), "
              "profilo e impostazioni", WINDOWS[-1][0] == "Report generated" and len(created) == 1
              and any(n.startswith("registri/") and n.endswith("-lantern-keeper.log") for n in names)
              and any(n.endswith("-interfaccia.log") for n in names)
              and "settings.json" in names and any(n.startswith("profilo/") for n in names))

    # Desktop that cannot be written to (e.g. controlled folder access): the report goes to the logs
    blocked = os.path.join(reports, "non-una-cartella")
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
    check("Crea rapporto con il Desktop bloccato: rapporto nella cartella dei registri, finestra 'Rapporto creato'",
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
    check("controllo del gioco scelto: finestra di fine e riga 'Dopo l'ultima partita a ...'",
              len(WINDOWS) == windows + 1 and WINDOWS[-1][0] == "Check after playing"
              and app.check_status.get() == "After the last session of %s: no problem"
              % os.path.basename(os.path.normpath(folder)))
    check("controllo: registro su disco con il risultato", "Check after playing" in _read_text(
        _logs()[-1]) and "Result: OK" in _read_text(_logs()[-1]))
    # at startup, with a problem to do in another game: no dialog, the line with 'Last session details'
    other = os.path.join(os.path.dirname(os.path.abspath(folder)), "Altro Gioco")
    real = checks.check

    def fake(path, apply=True, log=print, translator=None, events=None, **_k):
        log("Check after playing: test")
        return [checks.Problem("CRASH", "errore", "The game closed suddenly.", action="ripristina",
                                   state="da_fare"),
                checks.Problem("TEMPORANEI", "info", "Removed 2 temporary files.", state="corretto")]

    real_as_outcome = checks.as_outcome
    checks.check = fake
    checks.as_outcome = lambda problems, path=None, title_text=None: real_as_outcome(problems, None, "Altro Gioco")
    try:
        windows = len(WINDOWS)
        app._check(["profilo-finto"])
        _wait_for(app, root)
    finally:
        checks.check, checks.as_outcome = real, real_as_outcome
    check("controllo all'apertura: nessuna finestra, riga '1 problema corretto, 1 da fare' e 'Dettagli'",
              len(WINDOWS) == windows and app.check_status.get() == "After the last session of Altro Gioco: "
              "1 problem fixed, 1 to do" and app.btn_details.winfo_manager() == "pack")
    names = [os.path.basename(p) for p in _logs()]
    check("controllo all'apertura: registro 'controllo'", names[-1].endswith("-controllo.log"))
    # Last session details: the dialog with "To do" and "Restore originals"; once chosen, it applies to that game
    app.check_outcomes[0].extra["cartella"] = other
    choices, real_restore, real_choose = [], app._restore, theme.choose

    def choose(_g, _c, title_text, text, buttons, kind="info"):
        _choose(_g, _c, title_text, text, buttons, kind)
        return "ripristina"

    app._restore = lambda ask=True: choices.append(app.folder.get())
    theme.choose = choose
    try:
        app._check_details()
    finally:
        app._restore, theme.choose = real_restore, real_choose
        app.folder.set(folder)
    check("Dettagli: 'Qualcosa da fare' con 'Ripristina originali', per la cartella di quel gioco",
              WINDOWS[-1][0] == "Something to do" and "To do:" in WINDOWS[-1][1]
              and ("Restore originals", "ripristina") in WINDOWS[-1][2] and choices == [os.path.normpath(other)])
    # a game that cannot be checked (unexpected error) does not stop the others: "to do" with the report
    def broken(path, apply=True, log=print, translator=None, events=None, **_k):
        if path == "rotto":
            raise KeyError("cartella_gioco")
        return []

    checks.check = broken
    try:
        app._check(["rotto", "buono"])
        _wait_for(app, root)
    finally:
        checks.check = real
    errors = [p for e in app.check_outcomes for p in e.extra.get("controllo", []) if p["codice"] == "ERRORE"]
    check("controllo all'apertura: un gioco con un errore non ferma gli altri (errore da fare, con il rapporto)",
              len(app.check_outcomes) == 2 and len(errors) == 1 and errors[0]["azione"] == "crea_rapporto"
              and "KeyError" in errors[0]["messaggio"] and "1 to do" in app.check_status.get())
    # "Try again" in the dialog of a check (game open): the check of that game again, not the last operation done
    # (e.g. a translation)
    redone_calls, real_check = [], app._check
    app._check = lambda profiles=None, at_open=None: redone_calls.append((profiles, at_open))
    try:
        app._action("chiudi_gioco", outcome.Outcome("GIOCO_APERTO", actions=["chiudi_gioco"],
                                                extra={"controllo": [], "profilo": "profilo-x"}))
    finally:
        app._check = real_check
    check("'Riprova' dopo un controllo con il gioco aperto: rifa' il controllo di quel gioco, con la finestra",
              redone_calls == [(["profilo-x"], False)])
    # at startup, but you have already started something: it does not start right away, the line says so; it starts
    # by itself as soon as that finishes
    work_before = app.work
    app.work = object()
    app._check = lambda profiles=None, at_open=None: redone_calls.append((profiles, at_open))
    try:
        redone_calls.clear()
        app._start_initial_check(["profilo-finto"])
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
    check("controllo all'apertura durante un'altra operazione: la riga lo dice e parte da solo appena finisce "
              "(%s)" % redone_calls, "as soon as the current operation finishes" in line and during == []
              and redone_calls == [(["profilo-finto"], None)])
    # "Create report" from the Last session details of another game: the report is for that game, with its outcome
    requested_reports, real_create_report = [], report.create
    report.create = lambda c, dest_lang=None, outcome=None, **_k: requested_reports.append((c, outcome)) or \
        os.path.join(dest_lang or "", "rapporto-finto.zip")
    e_other = outcome.Outcome("PARZIALE", actions=["crea_rapporto"], extra={"controllo": [], "cartella": other})
    try:
        app._action("crea_rapporto", e_other)
        _wait_for(app, root)
    finally:
        report.create = real_create_report
        app.folder.set(folder)
    check("'Crea rapporto' dai Dettagli di un altro gioco: rapporto di quel gioco con il suo esito",
              requested_reports == [(os.path.normpath(other), e_other)])
    # PatchSmith updated while the window is open: notice, only once
    real_fingerprint = gui.code_fingerprint
    gui.code_fingerprint = lambda: ("9.9.9", 0)
    try:
        before = len(WARNINGS)
        app._start("Prova", lambda _c: None, needs_folder=False, keep_log=False)
        _wait_for(app, root)
        app._start("Prova", lambda _c: None, needs_folder=False, keep_log=False)
        _wait_for(app, root)
    finally:
        gui.code_fingerprint = real_fingerprint
    check("PatchSmith aggiornato con la finestra aperta: avviso di riaprirla, una volta sola",
              WARNINGS[before:] == ["PatchSmith has been updated"])


def _trial_retranslate(app, root):
    """The check at startup does not re-translate the journal's lines (the window would freeze): a
    "Re-translate N lines" button in the line, which (like "Re-translate the phrases" in the Last session details)
    redoes the check with the re-translation."""
    from patchsmith import checks
    received, real, real_as_outcome = [], checks.check, checks.as_outcome

    def fake(path, apply=True, log=print, translator=None, events=None, retranslate=True):
        received.append(retranslate)
        return [checks.Problem("FRASI_DA_RITRADURRE", "avviso", "While you were playing, 3 phrases were left "
                                   "untranslated.", action="ritraduci", state="da_fare", extra={"frasi": 3})]

    def fake_as_outcome(problems, path=None, title_text=None):
        e = real_as_outcome(problems, None, "Gioco Diario")
        e.extra["profilo"] = path
        return e

    checks.check, checks.as_outcome = fake, fake_as_outcome
    try:
        app._check(["profilo-diario"])
        _wait_for(app, root)
    finally:
        checks.check, checks.as_outcome = real, real_as_outcome
    check("controllo all'apertura: le frasi del diario non si ritraducono adesso, pulsante 'Ritraduci 3 frasi' "
              "nella riga", received == [False] and app.btn_retranslate.winfo_manager() == "pack"
              and app.btn_retranslate.cget("text") == "Re-translate 3 phrases")
    redone_calls = []
    app._check = lambda profiles=None, at_open=None, retranslate=None: redone_calls.append(
        (profiles, at_open, retranslate))
    try:
        app._retranslate()
        app._action("ritraduci", app.check_outcomes[0])
    finally:
        del app._check
    check("'Ritraduci' (riga e Dettagli): il controllo di quel gioco con la ritraduzione e le finestre",
              redone_calls == [(["profilo-diario"], False, True)] * 2)
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
        check("gioco aperto prima di Analizza e traduci, Riapplica e Ripristina: 'Il gioco è aperto' con "
                  "'Riprova', nessuna operazione avviata", started == []
                  and [f[0] for f in WINDOWS[-3:]] == ["The game is open"] * 3 and ("Try again", "riprova")
                  in WINDOWS[-1][2])
        state = {"aperto": True}
        system.game_open = lambda _c: state["aperto"]
        responses[:] = ["riprova"]
        try:
            app._translate()
            state["aperto"] = False  # game closed: "Try again" redoes the checks and starts
            for _ in range(15):
                root.update()
                time.sleep(0.02)
        finally:
            system.game_open = real_is_open
        check("'Riprova' a gioco chiuso: di nuovo i controlli, poi l'operazione parte",
                  started == ["Analysis and translation"])
        started.clear()
        # protected folder and wrong language: it stops, with the reason
        real_write_check = automatic.check_writable
        automatic.check_writable = lambda _c: PermissionError(13, "Accesso negato")
        try:
            app._translate()
        finally:
            automatic.check_writable = real_write_check
        check("cartella protetta: finestra con il motivo e solo OK, nessuna operazione",
                  started == [] and WINDOWS[-1][0] == "Protected game folder" and WINDOWS[-1][2] == [("OK", None)]
                  and "administrator" in WINDOWS[-1][1])
        a_before = app.a.get()
        app.a.set("Italianoo")
        try:
            app._translate()
        finally:
            app.a.set(a_before)
        check("lingua non riconosciuta in A: 'Lingua da controllare', nessuna operazione",
                  started == [] and WINDOWS[-1][0] == "Language to check" and "Italianoo" in WINDOWS[-1][1])
        # Google paused: "Install the local translator" chooses it, installs it and then repeats Analyze and translate
        chosen, app.forced_backend = app.translator.get(), None
        app.translator.set(gui.TRANSLATORS["google"])
        app._change_translator()
        limiter.Limiter("google", log=None).blocked(429, "prova")
        installs = []
        app._install_local = lambda then=None: installs.append(then)
        try:
            app._translate()
            pause = WINDOWS[-1]
            responses[:] = ["installa_locale"]
            app._translate()
            after_pause = app._chosen
            app._translate()  # now the local translator is chosen, and it is not installed
            absent = WINDOWS[-1]
            # the yes to the official language (consenso) translates nothing: no questions about the translator
            windows = len(WINDOWS)
            app._translate(consent=True)
            consent = (list(started), [f[0] for f in WINDOWS[windows:]])
            started.clear()
        finally:
            limiter.Limiter("google", log=None).reset()
            del app._install_local
            app.translator.set(chosen)
            app._change_translator()
            app.forced_backend = "finto"
        check("Google in pausa: 'Traduttore in pausa' con 'Installa il traduttore locale' e 'Continua comunque'; "
                  "scelto: traduttore locale e installazione, poi di nuovo Analizza e traduci",
                  started == [] and pause[0] == "Translator paused" and ("Install the local translator",
                                                                        "installa_locale") in pause[2]
                  and ("Continue anyway", "continua") in pause[2] and after_pause == "locale"
                  and len(installs) == 1 and callable(installs[0]))
        check("traduttore locale non installato: 'Installa ora' prima di partire (o 'Continua comunque': la lingua "
                  "ufficiale non serve tradurla), nessuna operazione",
                  started == [] and absent[0] == "Local translator to install"
                  and ("Install now", "installa_locale") in absent[2] and ("Continue anyway", "continua")
                  in absent[2])
        check("consenso alla lingua ufficiale con il traduttore locale da installare: parte senza domande sul "
                  "traduttore (non serve tradurre)", consent == (["Analysis and translation"], []))
    finally:
        system.game_open = real_is_open
        del app._start
        theme.choose = real_choose

    # a copy of the game (same folder name, elsewhere): "Yes, use them again" -> the original's translation memory
    copy_dir = os.path.join(os.path.dirname(os.path.abspath(folder)), "copia-prova-gui",
                         os.path.basename(os.path.normpath(folder)))
    shutil.rmtree(os.path.dirname(copy_dir), ignore_errors=True)
    for p, prof in list(automatic.list_profiles()):  # (from a previous run of the test launched on its own)
        if prof.get("cartella_gioco") and automatic._inside(prof["cartella_gioco"], os.path.dirname(copy_dir)):
            os.remove(p)
    fake_web_game.create(copy_dir)
    original = json.load(open(automatic.find_profile(folder), encoding="utf-8"))
    entries = json.load(open(project.memory_path(original, test=True), encoding="utf-8"))
    theme.choose = lambda _g, _c, title_text, text, buttons, kind="info": (
        _choose(_g, _c, title_text, text, buttons, kind)
        or ("riusa" if title_text == "Same game already translated?" else None))
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
    check("copia del gioco in un'altra cartella: 'Stesso gioco già tradotto?' con 'Sì, riusale'; riusate le sue "
              "traduzioni (profilo nuovo con la stessa memoria)", question
              and ("Yes, use them again", "riusa") in question[2]
              and new and profile_copy.get("nome") != original["nome"]
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
        {"ok": True, "messaggio": "ready, on the graphics card: \"Start game\" -> \"Inizia\".", "scheda": 1.0},
        {"ok": False, "messaggio": "the translation test failed: the local translator is not responding.",
         "scheda": None}]
    ollama.download_description = lambda selected: ["finto da scaricare"]
    ollama.install = lambda selected, log=print: True
    backend.probe_local = lambda model=None, log=print, **_k: tests.append(model) or trial_outcomes.pop(0)
    theme.ask = lambda *_a: True
    try:
        warnings = len(WARNINGS)
        app._install_local(then=lambda: redone.append("di nuovo"))
        _wait_for(app, root)
        first_result = (list(redone), app.state.get(), len(WARNINGS) - warnings)
        app._install_local(then=lambda: redone.append("di nuovo"))
        _wait_for(app, root)
    finally:
        ollama.download_description, ollama.install, backend.probe_local, theme.ask = actual
    check("installazione chiesta prima di partire: frase di prova riuscita -> l'operazione riparte da sola",
              first_result[0] == ["di nuovo"] and "Local translator installed: ready" in first_result[1]
              and not first_result[2] and len(tests) == 2)
    check("frase di prova non riuscita: finestra con il motivo, l'operazione che aspettava non parte",
              redone == ["di nuovo"] and WARNINGS[-1] == "Local translator test failed"
              and "not responding" in app.state.get())
    # already installed: "Install now" runs the waiting operation right away; preliminary checks that fail
    # (unexpected error): it starts anyway, and the log says so
    ollama.download_description = lambda selected: []
    immediate, real_cp = [], automatic.preflight_checks
    automatic.preflight_checks = lambda *_a, **_k: 1 / 0
    try:
        app._install_local(then=lambda: immediate.append("subito"))
        choices = app._preflight("traduci", lambda: None, "finto")
        for _ in range(10):
            root.update()
            time.sleep(0.02)
    finally:
        ollama.download_description, automatic.preflight_checks = actual[0], real_cp
    check("traduttore locale gia' installato: l'operazione che aspettava parte subito; controlli prima di partire "
              "non riusciti: si parte lo stesso, con una riga nel registro", immediate == ["subito"]
              and choices == {"riusa": None}
              and "Pre-start checks failed" in app.text.get("1.0", "end"))

    # the check at startup arrives while a dialog is open: it waits for it to close
    window = tk.Toplevel(root)
    window.grab_set()
    check_list, work = [], app.work
    app.work = None
    app._check = lambda profiles=None, **_k: check_list.append(profiles)
    try:
        app._start_initial_check(["profilo-in-attesa"])
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
    check("controllo all'apertura con una finestra aperta: parte solo dopo che si e' chiusa",
              during == [] and immediate == [] and check_list == [["profilo-in-attesa"]])
    # dialog closed with a choice that starts an operation right after (e.g. "Try again"): the check does not start
    # right away, it waits for the operation to finish
    window = tk.Toplevel(root)
    window.grab_set()
    check_list[:] = []
    app.work = None
    app._check = lambda profiles=None, **_k: check_list.append(profiles)
    try:
        app._start_initial_check(["profilo-in-attesa"])
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
    check("finestra chiusa con un'operazione che parte subito dopo: il controllo all'apertura aspetta che finisca",
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
    check("Correggi traduzioni: finestra con l'elenco, riga cambiata e salvata: memoria valida con il valore "
              "nuovo, Riapplica da sola che cambia il file del gioco", lines > 10 and cell == "Nuova partita (corretta)"
              and memory.get("Start a new game") == "Nuova partita (corretta)"
              and hashlib.sha256(after).hexdigest() != before and "Nuova partita (corretta)" in after.decode("utf-8")
              and WINDOWS[-1][0] == "Translation reapplied" and not window.top.winfo_exists())
    windows = len(WINDOWS)
    app._show_outcome("Analysis and translation", outcome.Outcome("PARZIALE", written=True, translated_items=3,
                                                             remaining=1, remaining_examples=["Quit"]))
    check("finestra di fine con testi rimasti: pulsante 'Correggi traduzioni'",
              len(WINDOWS) == windows + 1 and ("Fix translations", "correggi") in WINDOWS[-1][2])


def _read_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    path, outcome_ok = main(sys.argv[1])
    print("profilo usato:", path)
    sys.exit(0 if outcome_ok else 1)
