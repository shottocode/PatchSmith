"""Tests of "Fix translations" (item 6): the list of a game's translations with its filters, the window
(keyboard, long list in pages, checks while you type), saving (valid translation memory, machine translations that
stay the previous ones, shared translation memory, problemi.json) and writing into the game on its own ("Reapply"),
for a web game, a GameMaker game with the line space and a Unity game (XUnity cache line replaced, the others
identical; game open; line changed by hand after the fix). Then "Re-translate selected" (new requests, also for
lines fixed by hand), "Re-translate everything with Quality (12B)" (fixes made by hand stay), the Quality model
to install, "Start over" (backup copy, shared translation memory), "Open the file (advanced)" with no program
for .json files, a damaged translations file, and the "Fix translations" buttons in the end dialogs.

Usage: python -I test/test_corrections.py <empty_temp_folder>
PatchSmith's data go in <folder>/data_folder; fake translators (no network, no Ollama), fake Unity components, no
real game. PatchSmith's window opens for a few seconds.
"""
import hashlib
import json
import os
import re
import struct
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import tkinter as tk  # noqa: E402

import fake_web_game  # noqa: E402
import test_gamemaker  # noqa: E402
import test_gamemaker_json  # noqa: E402
import test_unity  # noqa: E402
from patchsmith import (adapter_unity, auto as automatic, backend, corrections, outcome, gui,
                        gui_corrections)  # noqa: E402
from patchsmith import ollama, project, scan, steam, theme  # noqa: E402

OUTCOMES = []
INSTALLED = ("Translation installed", "Translation partially installed")  # titles of the end dialog (OK, PARTIAL)
WINDOWS = []  # (title, text, buttons) of the choice dialogs and of the notices
ANSWERS = []  # the answers to the next choice dialogs (None: OK / Cancel)


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def _choose(_g, _c, title_text, text, buttons, kind="info"):
    WINDOWS.append((title_text, text, list(buttons)))
    print("WINDOW:", title_text, "|", " ".join(text.split())[:150], "|", [p for p, _ in buttons])
    return ANSWERS.pop(0) if ANSWERS else None


def _warn(_g, _c, title_text, text):
    WINDOWS.append((title_text, text, []))
    print("WARNING:", title_text, "|", " ".join(text.split())[:150])


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wait_for(app, root, seconds=120):
    start = time.time()
    while (app.work is None or app.work.is_alive()) and time.time() - start < seconds:
        root.update()
        time.sleep(0.03)
    for _ in range(15):
        root.update()
        time.sleep(0.03)


def write(f, text):
    """Like typing in the translation box (also Tk's <<Modified>> event)."""
    f.translation_text.delete("1.0", "end")
    f.translation_text.insert("1.0", text)
    for _ in range(3):
        f.top.update()


def entry(items, text):
    return next(x for x in items.entries if x.text == text)


class DictTranslator(backend.Backend):
    """Fake but "real" translator (not the test translation memory): a small dictionary, the rest an Italian sentence
    with the same codes; the bridge is not translated (left untranslated)."""
    name = "prova-correzioni"
    ENTRIES = {"Start a new game": "Inizia una nuova partita", "Lantern Keeper": "Il guardiano delle lanterne antiche",
            "Delete": "Eliminare", "Quit to the desktop": "Esci al desktop",
            "Continue your journey": "Continua il tuo viaggio", "Options and settings": "Opzioni e impostazioni",
            "Welcome back, keeper. The lanterns are fading again.":
                "Bentornato, guardiano. Le lanterne si stanno spegnendo di nuovo."}

    def __init__(self, entries=None, broken_words=("bridge",)):
        self.entries = dict(self.ENTRIES, **(entries or {}))
        self.broken_words = broken_words
        self.asked = []

    def translate_lines(self, lines, origin, dest_lang):
        self.asked += lines
        output = []
        for r in lines:
            if any(x in r for x in self.broken_words):
                output.append("")
            else:
                output.append(self.entries.get(r) or ("Testo tradotto in italiano " + " ".join(
                    re.findall(r"\[\[\d+\]\]", r))).strip())
        return output


class WithSeeds(DictTranslator):
    """Like the local translator: receives the round for each line (new seeds from round 1)."""
    varying_seeds = True

    def __init__(self, entries=None, broken_words=()):
        super().__init__(entries, broken_words)
        self.rounds = []

    def translate_lines(self, lines, origin, dest_lang, rounds=None):
        self.rounds.append(rounds)
        return super().translate_lines(lines, origin, dest_lang)


# --- functions without a window -----------------------------------------------------------------------------

def test_functions(base):
    a = corrections.warnings
    check("checks while you type: missing or extra placeholder, numbers, lines, empty",
              a("Collect {0} coins", "Raccogli monete")[0].startswith("missing {0}")
              and a("Collect {0} coins", "Raccogli {0} {1} monete") == ["extra: {1}"]
              and a("Level 2", "Livello 3") == ["different numbers from the original (missing 2; extra 3)"]
              and a("Line one\nLine two", "Riga uno riga due") == ["lines: 1 instead of 2"]
              and a("Quit", "  ") == ["the translation is empty"]
              and a("Collect {0} coins", "Raccogli {0} monete") == []
              and a("max H", "massimo H", ("H",)) == [] and a("max H", "massimo", ("H",))[0].startswith("missing H"))
    x = corrections.Entry("iron*ore")
    x.space = ("iron*ore", "*", "U", 11, 2, 0)
    check("length as the game writes it (line breaks redistributed in the line space)",
              corrections.measure(x, "minerale di ferro") == (11, 11)
              and corrections.too_long(*corrections.measure(x, "minerale di ferro grezzo e pesante antico"))
              and not corrections.too_long(12, 11) and corrections.measure(corrections.Entry("a"), "b") is None)

    def problems(text, translated, dest="it", problem=None, machine=None, cache=None):
        v = corrections.Entry(text)
        v.memory = translated
        v.cache = cache
        corrections._problems_of(v, problem or {}, dest, None, (), (), machine or {}, {}, {})
        return v.problems

    check("suspicious: infinitive verb in a button (with the right form), a word turned into a phrase",
              "\"Esporta\", not \"Esportare\"" in problems("Export", "Esportare").get("suspicious", "")
              and "Chiudi" in problems("Close", "Chiudere").get("suspicious", "")
              and "the imperative (\"Rifare\"" in problems("Redo", "Rifare").get("suspicious", "")
              and "suspicious" not in problems("None", "— Nessuno —")
              and "suspicious" not in problems("Chest", "Forziere") and "suspicious" not in problems("Save", "Salva")
              and "a phrase" in problems("Inventory", "Il tuo inventario personale").get("suspicious", "")
              and "suspicious" not in problems("Export", "Esportare", dest="fr"))
    check("filters from the recorded problems: left untranslated, not written to the game, suspicious; fixed by "
              "hand; a problem of a previous translation does not count",
              "remaining" in problems("The bridge is broken", None, problem={"where": "translation", "reason": "x"})
              and problems("Sword", "Spada→",
                           problem={"where": "game", "value": "Spada→", "reason": "font"})["game"] == "font"
              and "game" not in problems("Sword", "Spada", problem={"where": "game", "value": "Spada→"})
              and "suspicious" in problems("Wood", "Legna", problem={"where": "memory", "value": "Legna"})
              and problems("Wood", "Legno", machine={"Wood": "Legna"}).get("by_hand") == "fixed by hand"
              and "by_hand" not in problems("Wood", "Legno", machine={"Wood": "Legno"}))
    check("filters from the checks: identical to the original, another language, numbers and placeholders (even "
              "just to look at), short label that became much longer",
              problems("Open the door to the castle", "Open the door to the castle").get("remaining")
              == "identical to the original"
              and "language" in problems("Open the door", "Abre la puerta de la casa y el castillo")
              and problems("Collect {0} coins", "Raccogli monete")["placeholders"].startswith("missing {0}")
              and problems("Collect {0} coins", "Raccogli {0} monete")["placeholders"] == ""
              and "long" in problems("Credits", "Ringraziamenti finali")
              and "long" not in problems("Save", "Salva"))
    profile = {"name": "funzioni", "target_language": "it"}
    corrections.mark_to_write(profile, {"Start": ("Inizia", "Nuova partita")})
    corrections.mark_to_write(profile, {"Start": ("Nuova partita", "Gioca"), "Quit": (None, "Esci")})
    pending = corrections.to_write(profile)
    corrections.written_texts(profile, ["Start"])
    check("cache lines to write: a second fix keeps the earlier line; the written ones are removed",
              pending["Start"]["before"] == "Inizia" and pending["Start"]["value"] == "Gioca"
              and pending["Quit"]["before"] is None and list(corrections.to_write(profile)) == ["Quit"]
              and corrections.to_write_path(profile).endswith("funzioni.it.da_scrivere.json"))
    os.remove(corrections.to_write_path(profile))
    # "Open the file (advanced)" on a PC with no program for .json files: Notepad, with the full path
    launched, real_start, real_popen = [], getattr(os, "startfile", None), gui.subprocess.Popen

    def without_program(_p):
        raise OSError(1155, "Nessuna applicazione associata")

    os.startfile = without_program
    gui.subprocess.Popen = lambda command, *a, **k: launched.append(command)
    try:
        gui._open(os.path.join(base, "memoria.json"))
    finally:
        os.startfile, gui.subprocess.Popen = real_start, real_popen
    check("'Open the file (advanced)' with no program for .json files: Windows Notepad, never from the current "
              "folder", len(launched) == 1 and os.path.isabs(launched[0][0])
              and launched[0][0].lower().endswith(os.path.join("system32", "notepad.exe")))
    f = outcome.window(outcome.Outcome("PARTIAL", written=True, translated_items=5, remaining=2,
                                       remaining_examples=["Quit"], actions=["use_local"]), "translate")
    ok = outcome.window(outcome.Outcome("OK", written=True, translated_items=5), "translate")
    rejected = outcome.Outcome("PARTIAL", written=True, translated_items=5, extra={"hand_fixes_discarded": 1})
    check("end of operation: 'Fix translations' with texts left untranslated or not written to the game (with the "
              "right list), not if everything is translated nor for an unexpected error",
              ("Fix translations", "fix") in f["buttons"] and "Fix translations" not in str(ok["buttons"])
              and ("Fix translations", "fix") in outcome.window(rejected, "reapply")["buttons"]
              and outcome.fixes_filter(rejected) == "game"
              and outcome.fixes_filter(outcome.Outcome("PARTIAL", remaining=1)) == "remaining"
              and not outcome.to_fix(outcome.Outcome("ERROR", remaining=1, extra={"unexpected": True}))
              and "list \"Not written to the game\"" in outcome.window(rejected, "reapply")["text"]
              and "I wrote your correction to the game." in outcome.window(outcome.Outcome(
                  "OK", written=True, translated_items=5, extra={"fixes_written": 1}), "reapply")["text"])


# --- web game -------------------------------------------------------------------------------------------------

def test_web(base, root, app):
    game = os.path.join(base, "Lantern Keeper")
    rel = fake_web_game.create(game)
    texts_file = os.path.join(game, *rel.split("/"))
    automatic.create_backend = lambda name, log=print: DictTranslator()
    path, _, _ = automatic.analyze_and_translate(game, "auto", "it", "local", silent)
    profile = read_file(path)
    memory = project.memory_path(profile)
    auto_file = memory[:-5] + ".automatiche.json"
    items = corrections.load(path)
    occurrences = items.counts()
    check("list: all the game's texts (also the one left untranslated), with the filters",
              occurrences["all"] == 19 and occurrences["remaining"] == 1 and occurrences["by_hand"] == 0
              and entry(items, "The bridge is broken: you need a rope and two planks.").problems.get("remaining", "")
              .startswith("without translation")
              and "long" in entry(items, "Lantern Keeper").problems
              and "\"Elimina\"" in entry(items, "Delete").problems.get("suspicious", ""))
    found = [x.text for x in items.apply_filter("all", "LANTERN")]
    check("case-insensitive search in the original and in the translation",
              "Lantern Keeper" in found and "Press any key to light the lantern near the gate." in found
              and [x.text for x in items.apply_filter("all", "guardiano")] == ["Lantern Keeper",
                                                                            "Welcome back, keeper. The lanterns are "
                                                                            "fading again."])

    # the window: "Left untranslated" list, translation written by hand, checks while you type
    app.folder.set(game)
    f = app._fix()
    check("window: title with the game, 'Show' menu with the counts, Original/Translation/Problem columns "
              "(Limit only if the game makes the texts' space known)", f is not None
              and f.top.title() == "Fix translations - lantern-keeper"
              and "Left untranslated (1)" in f.filter_choice.cget("values")
              and len(f.tree.get_children()) == 19
              and tuple(f.tree.cget("displaycolumns")) == ("original", "translation", "problem"))
    f.choose_filter("remaining")
    lines = f.tree.get_children()
    f._choose_line(0)
    write(f, "Il ponte è rotto: ti servono una corda e due assi.")
    check("line left untranslated: you write the translation, the line becomes 'modified, to be saved'",
              len(lines) == 1 and f.changes == {"The bridge is broken: you need a rope and two planks.":
                                                  "Il ponte è rotto: ti servono una corda e due assi."}
              and "modified" in f.tree.item(lines[0], "tags")
              and f.tree.item(lines[0], "values")[3] == "modified, to be saved" and f.top.title().endswith("*"))
    f.choose_filter("all")
    f._go_to("Quit to the desktop")
    write(f, "Esci {0} al desktop")
    warning = f.warning.get()
    f._undo_line()
    check("checks while you type (extra placeholder) and Esc that puts back the previous translation",
              "extra: {0}" in warning and "Quit to the desktop" not in f.changes
              and f.translation_text.get("1.0", "end-1c") == "Esci al desktop")
    f._go_to("Start a new game")
    write(f, "Nuova partita")
    length = f.length.get()
    line = f.tree.selection()
    f.search.set("new game")
    for _ in range(12):  # the search starts on its own after a moment
        root.update()
        time.sleep(0.03)
    searched = (f.tree.selection(), f.translation_text.get("1.0", "end-1c"), len(f.tree.get_children()))
    f.search.set("")
    f.update_list()
    check("search while you fix a line: the line stays selected, with what you wrote",
              searched == (line, "Nuova partita", 1) and f.tree.selection() == line)
    before = f.tree.selection()
    f._confirm_and_next()
    after = f.tree.selection()
    hotkeys_bound = all(f.tree.bind(t) for t in ("<Return>", "<F2>", "<Escape>")) and \
        f.translation_text.bind("<Control-Return>") and f.top.bind("<Control-f>") and f.top.bind("<Control-s>") \
        and f.top.bind("<Alt-Right>")
    f._go_to_search()
    root.update()
    check("keyboard: Enter/F2, Ctrl+Enter goes to the next line, Ctrl+F searches, Ctrl+S saves, Alt+arrows pages; "
              "length while you type", hotkeys_bound and before != after and len(after) == 1
              and "Length: 13 characters (original: 16)" == length)
    f._go_to("Delete")
    check("the selected line's whole problem under the translation (e.g. 'Elimina', not 'Eliminare')",
              f.problem.get().startswith("Problem: infinitive verb") and "\"Elimina\"" in f.problem.get())
    # empty text or text with a wrong placeholder: it is not saved without saying so
    f._go_to("Continue your journey")
    write(f, "")
    f.save()
    empty = WINDOWS[-1][0]
    write(f, "Continua {0} il tuo viaggio")
    ANSWERS[:] = [None]  # "Go back to editing"
    f.save()
    check("saving with an empty translation or an extra placeholder: it says so first, the window stays open",
              empty == "Empty translation" and WINDOWS[-1][0] == "Check the corrections"
              and ("Save anyway", "save") in WINDOWS[-1][2] and f.top.winfo_exists()
              and read_file(memory)["Start a new game"] == "Inizia una nuova partita")
    f._undo_line()
    # save: valid translation memory with the new value, machine translations as they were, shared translation
    # memory, then Reapply on its own
    game_before = fingerprint(texts_file)
    machine_before = read_file(auto_file)
    f.save()
    wait_for(app, root)
    mem = read_file(memory)
    auto = read_file(auto_file)
    shared = read_file(project.shared_path("en", "it"))
    origin = read_file(project.shared_origin_path("en", "it"))
    problems = read_file(project.problems_path(profile))
    texts = read_file(texts_file)
    check("saved: valid translation memory with the fixes, the machine translations keep the previous one (so "
              "it is a fix made by hand; for the text left untranslated an empty entry)",
              mem["Start a new game"] == "Nuova partita"
              and mem["The bridge is broken: you need a rope and two planks."].startswith("Il ponte")
              and auto["Start a new game"] == machine_before["Start a new game"] == "Inizia una nuova partita"
              and "The bridge is broken: you need a rope and two planks." not in machine_before
              and auto.get("The bridge is broken: you need a rope and two planks.") == "")
    check("shared translation memory: the fix of a short text applies to all games (fixed by hand)",
              shared.get("Start a new game") == "Nuova partita" and origin["Start a new game"].get("by_hand") is True
              and origin["Start a new game"].get("game") == "lantern-keeper")
    check("the text translated by hand leaves the remaining ones (problemi.json)",
              "The bridge is broken: you need a rope and two planks." not in problems)
    check("Reapply on its own after saving: the game's file has changed, with the fixes",
              fingerprint(texts_file) != game_before and texts["menu"]["start"] == "Nuova partita"
              and texts["dialoghi"][2]["text"].startswith("Il ponte") and WINDOWS[-1][0] == "Translation reapplied"
              and not f.top.winfo_exists())
    items = corrections.load(path)
    check("reopened: 'Fixed by hand' (2), none left untranslated", items.counts()["by_hand"] == 2
              and items.counts()["remaining"] == 0)

    # closing with unsaved fixes: "Don't save" exits without saving, "Cancel" stays
    f = app._fix()
    f._go_to("Quit to the desktop")
    write(f, "Torna al desktop")
    ANSWERS[:] = [None]
    f.close()
    modal_open = f.top.winfo_exists()
    ANSWERS[:] = ["exit"]
    f.close()
    check("closing with unsaved fixes: it asks ('Cancel' stays open, 'Don't save' exits without "
              "saving)", modal_open and not f.top.winfo_exists() and WINDOWS[-1][0] == "Save the changes?"
              and read_file(memory)["Quit to the desktop"] == "Esci al desktop")

    # Re-translate selected: new requests (round 1) also for the line fixed by hand, then into the game
    new = WithSeeds({"Start a new game": "Comincia una partita nuova", "Continue your journey": "Prosegui il viaggio"})
    real_create = corrections.create_translator
    to_ask = []
    corrections.create_translator = lambda name, model=None, log=print: to_ask.append((name, model)) or new
    real_state, real_present = ollama.state, ollama.model_present
    try:
        f = app._fix()
        f._go_to("Start a new game")
        f.tree.selection_add(str(f.idx["Continue your journey"]))
        f.retranslate_selected()  # local translator chosen but not installed: it says so, the window stays
        without_local = (WINDOWS[-1][0], f.top.winfo_exists())
        ollama.state, ollama.model_present = (lambda: "ready"), (lambda key: key == "4b")
        ANSWERS[:] = ["4b"]
        f.retranslate_selected()
        models = WINDOWS[-1][2]
        wait_for(app, root)
    finally:
        corrections.create_translator = real_create
        ollama.state, ollama.model_present = real_state, real_present
    check("Re-translate selected: without the local translator it says so; with the local translator you pick the "
              "model (only the downloaded ones)", without_local == ("Local translator to install", True)
              and models == [("Local translator: Fast (4B)", "4b"), ("Cancel", None)])
    mem, auto, texts = read_file(memory), read_file(auto_file), read_file(texts_file)
    check("Re-translate selected: also the line fixed by hand, with new requests (round 1), then written to the game",
              mem["Start a new game"] == "Comincia una partita nuova" and mem["Continue your journey"] ==
              "Prosegui il viaggio" and auto["Start a new game"] == "Comincia una partita nuova"
              and new.rounds and all(g and min(g) >= 1 for g in new.rounds)
              and texts["menu"]["start"] == "Comincia una partita nuova" and to_ask == [("local", "4b")])
    check("Re-translate: end dialog 'Translations redone' with how many", WINDOWS[-1][0] == "Translations redone"
              and "I re-translated 2 of 2 texts." in WINDOWS[-1][1])
    # pressing "Re-translate selected" again on the same line the requests are new again (random round, never 0):
    # with a fixed round the local translator would give the previous answer
    sequence, real_round = iter([5, 9]), corrections._new_round
    corrections._new_round = lambda: next(sequence)
    two = []
    try:
        for i in range(2):
            t = WithSeeds({"Start a new game": "Avvia la partita %d" % i})
            corrections.retranslate(path, ["Start a new game"], t, silent)
            two.append(t.rounds)
    finally:
        corrections._new_round = real_round
    random_rounds = {corrections._new_round() for _ in range(20)}
    check("Re-translate selected again on the same line: other requests (random round, never 0)",
              two == [[[5]], [[9]]] and min(random_rounds) >= 1 and len(random_rounds) > 1
              and read_file(memory)["Start a new game"] == "Avvia la partita 1")

    # Re-translate everything with Quality (12B): fixes made by hand stay
    corrections.save(path, {"Options and settings": "Opzioni varie"})
    real_state, real_present = ollama.state, ollama.model_present
    ollama.state, ollama.model_present = (lambda: "ready"), (lambda key: True)
    quality = WithSeeds({"Options and settings": "Opzioni (rifatte)", "Quit to the desktop": "Torna al desktop",
                       "Lantern Keeper": "Guardiano"})
    corrections.create_translator = lambda name, model=None, log=print: to_ask.append((name, model)) or quality
    try:
        f = app._fix()
        ANSWERS[:] = ["yes"]
        f.retranslate_all()
        question = WINDOWS[-1]
        wait_for(app, root)
    finally:
        corrections.create_translator = real_create
        ollama.state, ollama.model_present = real_state, real_present
    mem = read_file(memory)
    check("Re-translate everything with Quality (12B): the Quality model, the fixes made by hand stay (also in the "
              "question), the machine translations redone with the normal requests",
              to_ask[-1] == ("local", "12b") and "Your manual corrections (2) stay" in question[1]
              and mem["Options and settings"] == "Opzioni varie" and mem["Quit to the desktop"] == "Torna al desktop"
              and mem["Lantern Keeper"] == "Guardiano" and "Options and settings" not in quality.asked
              and all(not g or not any(g) for g in quality.rounds))
    # ... without the Quality model: offer to install it, then it re-translates on its own
    install = []
    real_install = app._install_12b_and_retranslate
    app._install_12b_and_retranslate = lambda: install.append(True)
    try:
        f = app._fix()
        ANSWERS[:] = ["install"]
        f.retranslate_all()
    finally:
        app._install_12b_and_retranslate = real_install
    check("Re-translate everything without the Quality model: 'Quality model to install', then installation and "
              "re-translation", install == [True] and WINDOWS[-1][0] == "Quality model to install"
              and not f.top.winfo_exists())
    # and the model choice in the interface: Quality chosen, then the installation with the re-translation after
    then, real_install_local = [], app._install_local
    app._install_local = lambda then=None: install.append(then)
    try:
        app._install_12b_and_retranslate()
    finally:
        app._install_local = real_install_local
    check("... the local translator with the Quality model chosen, and the re-translation after the installation",
              app._chosen == "local" and app._model_key() == "12b" and callable(install[-1]))

    # from the end dialogs: "Fix translations" opens the right list; "Show remaining" offers it
    app._action("fix", outcome.Outcome("PARTIAL", written=True, extra={"hand_fixes_discarded": 1}))
    open_toplevels = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel) and w.winfo_exists()]
    f_game = getattr(app, "_last_fixes", None)
    ANSWERS[:] = [None]
    app._action("show_remaining", outcome.Outcome("PARTIAL", remaining=1, remaining_examples=["Quit"]))
    check("'Fix translations' from an end dialog: the window with the 'Not written to the game' list; "
              "'Show remaining' offers 'Fix translations'",
              len(open_toplevels) == 1 and f_game is not None and f_game.filter_ == "game"
              and ("Fix translations", "fix") in WINDOWS[-1][2])
    f_game.close()

    # damaged translations file (edited by hand with an error): it says so and offers to open it
    with open(memory, "rb") as fm:
        good = fm.read()
    with open(memory, "w", encoding="utf-8") as fm:
        fm.write('{"Start a new game": "Nuova partita",}')
    try:
        modal_open = app._fix()
    finally:
        with open(memory, "wb") as fm:
            fm.write(good)
    check("damaged translations file: 'Unreadable translations' with 'Open the file (advanced)', in plain "
              "words", modal_open is None and WINDOWS[-1][0] == "Unreadable translations"
              and ("Open the file (advanced)", "open") in WINDOWS[-1][2] and "JSON" not in WINDOWS[-1][1])

    # Start over: original game, backup copy, shared translation memory without the game's machine translations
    shared_before = read_file(project.shared_path("en", "it"))
    origin = read_file(project.shared_origin_path("en", "it"))
    game_machine = {t for t, o in origin.items() if o.get("game") == "lantern-keeper" and not o.get("by_hand")}
    by_hand = {t for t, o in origin.items() if o.get("by_hand")}
    original = json.dumps(fake_web_game.TEXTS, ensure_ascii=False, indent=2).encode("utf-8")
    # without a ready translator (local not installed, or paused) nothing is touched: the game would be left without
    translated = fingerprint(texts_file)
    stops = []

    class Paused(DictTranslator):
        def pause(self):
            return "Google is paused until tomorrow."

    for create in (backend.create_backend, lambda name, log=print: Paused()):
        automatic.create_backend = create
        try:
            automatic.start_over(game, silent, backend="local")
        except backend.BackendError as error:
            stops.append(outcome.from_exception(error).code)
    check("Start over without a ready translator: it stops before touching anything",
              stops == ["TRANSLATOR_MISSING", "TRANSLATOR_BLOCKED"] and fingerprint(texts_file) == translated
              and os.path.exists(memory))
    automatic.create_backend = lambda name, log=print: DictTranslator()
    copy_dir = automatic.start_over(game, silent, backend="local")
    shared = read_file(project.shared_path("en", "it"))
    removed = read_file(os.path.join(copy_dir, "comune.en-it.tolte.json"))
    with open(texts_file, "rb") as ft:
        put_back_text = ft.read()
    check("Start over: the game is original again; memory, machine translations and problems set aside "
              "in memory/copies", put_back_text == original and copy_dir
              and os.path.dirname(copy_dir) == project.data_folder("memory", "copies")
              and not os.path.exists(memory) and not os.path.exists(auto_file)
              and read_file(os.path.join(copy_dir, os.path.basename(memory)))["Options and settings"] ==
              "Opzioni varie")
    check("Start over: the game's machine translations out of the shared translation memory (kept in the "
              "copy), the fixes made by hand stay", game_machine and set(removed) == game_machine
              and not game_machine & set(shared) and by_hand == {"Options and settings"}
              and all(shared[t] == shared_before[t] for t in by_hand))
    automatic.create_backend = lambda name, log=print: DictTranslator()
    automatic.analyze_and_translate(game, "auto", "it", "local", silent, regenerate=True)
    mem = read_file(memory)
    check("... and the translation starts over, with all the game's files (the fixes made by hand from the shared "
              "translation memory)", mem.get("Continue your journey") == "Continua il tuo viaggio"
              and mem.get("Options and settings") == "Opzioni varie" and len(mem) >= 18)
    # from the window, with the test translator (its own separate translation memory): "Start over" and Analyze and
    # translate
    app.forced_backend = "fake"
    calls, real_restart = [], automatic.start_over

    def paused(*_a, **_k):
        raise backend.BackendError("Google is paused until tomorrow.", "TRANSLATOR_BLOCKED")

    try:
        app._translate()
        wait_for(app, root)
        f = app._fix()
        ANSWERS[:] = ["yes"]
        f.restart()
        wait_for(app, root)
        titles = [t for t, _, _ in WINDOWS]
        # "Try again" afterwards (e.g. game opened halfway through): continues the new translation, does not set it
        # aside again
        automatic.start_over = lambda *a, **k: calls.append(1) or real_restart(*a, **k)
        app._retry()
        wait_for(app, root)
        to_retry = (list(calls), WINDOWS[-1][0])
        # a "Start over" that did not start (translator paused): "Use the local translator" starts over
        automatic.start_over = paused
        app._translate(from_scratch=True)
        wait_for(app, root)
        not_started = (app.last_outcome.code, app._choices(app.last_outcome).get("from_scratch"))
    finally:
        automatic.start_over = real_restart
        app.forced_backend = None
    copies = sorted(os.listdir(project.data_folder("memory", "copies")), key=lambda n: os.path.getmtime(
        project.data_folder("memory", "copies", n)))
    check("after 'Start over', 'Try again' continues the new translation without setting it aside again; if it "
              "had not started (translator paused), 'Use the local translator' starts over",
              to_retry[0] == [] and to_retry[1].startswith(INSTALLED) and len(copies) == 2
              and not_started == ("TRANSLATOR_BLOCKED", True))
    check("'Start over…' from the window: asks for confirmation, sets the memory aside and translates again",
              "Start over from scratch?" in titles and titles[-1].startswith(INSTALLED)
              and len(copies) == 2
              and any(n.endswith(".prova.json") for n in os.listdir(project.data_folder("memory", "copies",
                                                                                          copies[-1])))
              and os.path.exists(project.memory_path(profile, test=True)))
    automatic.restore(game, silent)


# --- long list: in pages --------------------------------------------------------------------------------------

def test_pages(app, root):
    entries = []
    for i in range(750):
        x = corrections.Entry("Text number %d of the game" % i)
        x.memory = "Testo numero %d del gioco" % i
        if i % 100 == 0:
            x.problems["suspicious"] = "da guardare"
        entries.append(x)
    items = corrections.Items("profilo-finto.json", {"name": "lungo", "target_language": "it"}, entries, False,
                              "en", (), True)
    start = time.perf_counter()
    f = gui_corrections.Window(app, items)
    seconds = time.perf_counter() - start
    page1 = (len(f.tree.get_children()), f.position.get())
    f.change_page(1)
    f.change_page(1)
    page3 = (len(f.tree.get_children()), f.position.get(), f.btn_next.instate(["disabled"]))
    f.search.set("number 7")
    f._go_to_list()
    wanted = len(f.tree.get_children())  # 7, 70-79, 700-749
    f.search.set("")
    f.choose_filter("suspicious")
    suspects = len(f.tree.get_children())
    pages_with_few = f.pages_row.winfo_manager()  # (8 lines: a single page, no page buttons)
    f.close()
    check("long list in pages (%.2f s): %d lines at a time, Next page up to the last one, search and filter; with "
              "a single page the page row is not there" % (seconds, gui_corrections.PAGE),
              page1 == (300, "Page 1 of 3 (lines 1–300 of 750)")
              and page3 == (150, "Page 3 of 3 (lines 601–750 of 750)", True)
              and wanted == 61 and suspects == 8 and pages_with_few == "" and seconds < 5)


# --- GameMaker: the line space and the translations the game cannot display ---------------------------------

def test_gamemaker_space(base, root, app):
    game = os.path.join(base, "Forager finto")
    local = os.path.join(game, "local")
    os.makedirs(local)
    for name, data in (("english", test_gamemaker_json.EN), ("french", test_gamemaker_json.FR),
                       ("spanish", test_gamemaker_json.ES), ("german", test_gamemaker_json.DE)):
        with open(os.path.join(local, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    latin = set(chr(c) for c in range(32, 127)) | set("àèéìíòóùÀÈÉÌÒÙçâêôûœëïüö")
    with open(os.path.join(game, "data.win"), "wb") as f:
        f.write(test_gamemaker.data_win([("fntText", latin), ("fntChat", set(chr(c) for c in range(32, 127)))]))
    open(os.path.join(game, "Forager.exe"), "wb").write(b"MZ")
    entries = dict(test_gamemaker_json.ITALIAN, **{"royal steel": "acciaio della corona reale antica"})
    automatic.create_backend = lambda name, log=print: DictTranslator(entries, broken_words=())
    path, _, _ = automatic.analyze_and_translate(game, "auto", "it", "local", silent)
    items = corrections.load(path)
    steel = entry(items, "royal steel")
    check("GameMaker: 'Too long' with the space the text has in the game (longest line of its "
              "versions), Limit column", steel.space and steel.space[3] == 11 and "long" in steel.problems
              and steel.problems["long"].startswith("longest line:")
              and "about 11 fit in the game" in steel.problems["long"]
              and "long" not in entry(items, "wood").problems)
    app.folder.set(game)
    f = app._fix("long")
    filter_ = f.filter_
    with_limit = "limit" in f.tree.cget("displaycolumns") or f.tree.cget("displaycolumns") == ("#all",)
    f._go_to("royal steel")
    values = f.tree.item(f.tree.selection()[0], "values")
    write(f, "acciaio reale")
    short = f.length.get()
    write(f, "acciaio della corona reale antica e lucente")
    line_len = f.length.get()
    f._undo_line()
    f.close()
    check("GameMaker: the window opened on 'Too long', the limit in the column and while you type",
              filter_ == "long" and values[0] == "royal steel" and re.fullmatch(r"\d+/11", values[2]) and with_limit
              and short.startswith("Longest line: 7 characters (about 11 fit in the game)")
              and line_len.endswith("too long"))
    # a fix with a character the game's fonts lack: it is not written, it goes in "Not written to the game"; a
    # suspicious translation fixed by hand leaves the suspicious ones at once (even without Reapply, as for Unity)
    problems_file = project.problems_path(read_file(path))
    problems = read_file(problems_file) if os.path.exists(problems_file) else {}
    problems["wood"] = {"reason": "test", "where": "memory", "value": "legno", "attempts": 6}
    with open(problems_file, "w", encoding="utf-8") as f:
        json.dump(problems, f)
    corrections.save(path, {"coal": "carbone → nero", "wood": "legname"})
    check("a suspicious translation fixed by hand leaves problemi.json at once",
              "wood" not in read_file(problems_file) and read_file(project.memory_path(read_file(path)))["wood"]
              == "legname")
    e = {}
    automatic.reapply(game, silent, outcome=e)
    items = corrections.load(path)
    coal = entry(items, "coal")
    check("GameMaker: fix with a character the game cannot draw -> 'Not written to the game' with the "
              "reason, and the end dialog offers 'Fix translations' on that list",
              "→" in coal.problems.get("game", "") and e["outcome"].extra.get("hand_fixes_discarded") == 1
              and outcome.fixes_filter(e["outcome"]) == "game"
              and ("Fix translations", "fix") in outcome.window(e["outcome"], "reapply")["buttons"])
    automatic.restore(game, silent)


# --- Unity: the XUnity cache ---------------------------------------------------------------------------------

def test_unity_cache(base, root, app):
    from patchsmith import xunity_service
    test_unity.prepare_components(os.path.join(base, "components"))
    adapter_unity._compile_plugin = test_unity._fake_plugin
    real_state, real_running = ollama.state, adapter_unity.is_running
    ollama.state = lambda: "ready"
    adapter_unity.is_running = lambda *_a: False
    name = "unity-correzioni"
    game = os.path.join(base, "Unity", name)
    test_unity.create_game(game)
    with open(os.path.join(game, "Gioco_Data", "resources.assets"), "wb") as f:  # asset texts (pre-translation)
        f.write(b"\x07\x00" + b"".join(struct.pack("<i", len(t.encode("utf-8"))) + t.encode("utf-8") + b"\x00" * (
            (4 - len(t.encode("utf-8")) % 4) % 4) for t in ("Start a new game", "Settings and options",
                                                           "Back to the main menu")))
    path = os.path.join(automatic.profiles_folder(), name + ".json")
    scan.save_json(path, {"name": name, "game_folder": game.replace(os.sep, "/"), "engine": "unity",
                                    "source_language": "en", "target_language": "it", "backend": "google",
                                    "file": []})
    automatic.create_backend = lambda name_b, log=print: DictTranslator(
        {"Settings and options": "Impostazioni e opzioni"})
    try:
        automatic.unity_full(path, silent, backend="google")
        cache = adapter_unity._cache_path(game, "it")
        with open(cache, "a", encoding="utf-8") as f:
            f.write("Open the door to the castle=Open the door to the castle\nTalk to the guard=Parla con la guardia\n"
                    "Hold {{A}} to leap=Tieni {{A}} per saltare\n")
        with open(xunity_service.journal_path(name), "w", encoding="utf-8") as f:
            f.write(json.dumps({"text": "Hold {{A}} to leap", "outcome": "relaxed",
                                "response": "Tieni {{A}} per saltare"}) + "\n")
        items = corrections.load(path)
        port = entry(items, "Open the door to the castle")
        check("Unity: the list also has the XUnity cache lines (what the game shows): one left "
                  "identical to the original, one translated while you were playing without all the checks",
                  port.cache == port.text and port.memory is None and "remaining" in port.problems
                  and "while you were playing" in entry(items, "Hold {{A}} to leap").problems.get("suspicious", "")
                  and entry(items, "Start a new game").cache == "Inizia una nuova partita"
                  and "English" not in [x.text for x in items.entries])
        with open(cache, encoding="utf-8-sig") as f:
            lines_before = f.read().splitlines()
        memory = project.memory_path({"name": name, "target_language": "it"})
        auto_before = read_file(memory[:-5] + ".automatiche.json")
        app.folder.set(game)
        f = app._fix()
        f._go_to("Open the door to the castle")
        write(f, "Apri la porta del castello")
        f._go_to("Start a new game")
        write(f, "Nuova partita")
        f.save()
        wait_for(app, root)
        with open(cache, encoding="utf-8-sig") as fc:
            lines_after = fc.read().splitlines()
        changed = [(a, b) for a, b in zip(lines_before, lines_after) if a != b]
        mem = read_file(memory)
        check("Unity: the fixed lines replaced in the XUnity cache, all the others identical",
                  len(lines_before) == len(lines_after) and sorted(b for _, b in changed) ==
                  ["Open the door to the castle=Apri la porta del castello", "Start a new game=Nuova partita"])
        check("Unity: memory with the fixes (also the line that was only in the cache), machine translations as "
                  "they were (the cache-only line recorded empty: fixed by hand), nothing left to write",
                  mem["Start a new game"] == "Nuova partita"
                  and mem["Open the door to the castle"] == "Apri la porta del castello"
                  and read_file(memory[:-5] + ".automatiche.json") == dict(auto_before,
                                                                           **{"Open the door to the castle": ""})
                  and entry(corrections.load(path), "Open the door to the castle").by_hand
                  and not corrections.to_write({"name": name, "target_language": "it"}))
        check("Unity: the end dialog says how many fixes it wrote to the game",
                  "I wrote your 2 corrections to the game." in WINDOWS[-1][1])
        # game open: the fix waits (saved), and is written at the next Reapply
        profile = read_file(path)
        corrections.save(path, {"Talk to the guard": "Parla con la sentinella"}, {"Talk to the guard":
                                                                                    "Parla con la guardia"})
        adapter_unity.is_running = lambda *_a: True
        try:
            automatic.reapply(game, silent)
            code = None
        except adapter_unity.UnityError as error:  # (the interface shows it as "The game is open")
            code = outcome.from_exception(error).code
        expected_before = (adapter_unity.xunity_cache_entries(game, "it")["Talk to the guard"],
                     list(corrections.to_write(profile)), code)
        adapter_unity.is_running = lambda *_a: False
        automatic.reapply(game, silent)
        check("Unity with the game open: the fix stays to be written; with the game closed Reapply writes it",
                  expected_before == ("Parla con la guardia", ["Talk to the guard"], "GAME_OPEN")
                  and adapter_unity.xunity_cache_entries(game, "it")["Talk to the guard"] == "Parla con la sentinella")
        # a line changed by hand in the file after the fix stays as it is
        corrections.save(path, {"Talk to the guard": "Parla col guardiano"}, {"Talk to the guard":
                                                                                "Parla con la sentinella"})
        with open(cache, encoding="utf-8-sig") as fc:
            text = fc.read()
        with open(cache, "w", encoding="utf-8") as fc:
            fc.write(text.replace("Talk to the guard=Parla con la sentinella", "Talk to the guard=Parla (a mano)"))
        automatic.reapply(game, silent)
        check("Unity: a line changed by hand in the file after the fix is not overwritten (and does not stay "
                  "pending)", adapter_unity.xunity_cache_entries(game, "it")["Talk to the guard"] == "Parla (a mano)"
                  and not corrections.to_write(profile))
        # Re-translate a cache line: into the translation memory and into the cache
        real_create = corrections.create_translator
        corrections.create_translator = lambda name_b, model=None, log=print: WithSeeds(
            {"Hold [[0]] to leap": "Tieni premuto [[0]] per saltare"})
        try:
            automatic.retranslate(game, ["Hold {{A}} to leap"], "google", silent)
        finally:
            corrections.create_translator = real_create
        check("Unity: Re-translate a provisional cache line -> translation memory and XUnity cache",
                  adapter_unity.xunity_cache_entries(game, "it")["Hold {{A}} to leap"] ==
                  "Tieni premuto {{A}} per saltare" and read_file(memory)["Hold {{A}} to leap"] ==
                  "Tieni premuto {{A}} per saltare")
        # Start over: XUnity removed from the game and its cache set aside too (with the game open: nothing)
        adapter_unity.is_running = lambda *_a: True
        real_is_open = automatic.system.game_open
        automatic.system.game_open = lambda _c: True
        try:
            automatic.start_over(game, silent)
            opened_ok = False
        except automatic.RestartError as error:
            opened_ok = error.code == "GAME_OPEN" and os.path.exists(cache) and os.path.exists(memory) \
                and outcome.from_exception(error).code == "GAME_OPEN"
        finally:
            automatic.system.game_open = real_is_open
            adapter_unity.is_running = lambda *_a: False
        copy_dir = automatic.start_over(game, silent)
        check("Unity: Start over removes XUnity from the game and also sets aside the saved cache; with the "
                  "game open it touches nothing ('The game is open')", opened_ok and not os.path.exists(cache)
                  and not adapter_unity.manifest_present(game) and not os.path.exists(memory)
                  and os.path.exists(os.path.join(copy_dir, name + ".unity", "Translation", "it", "Text",
                                                  "_AutoGeneratedTranslations.txt"))
                  and not os.path.exists(adapter_unity._memory_folder(name)))
    finally:
        ollama.state, adapter_unity.is_running = real_state, real_running


# --- translation memory of a version that did not record machine translations -------------------------------

def test_old_memory(base):
    """A game translated by a version without .automatiche.json, to which a few recorded translations are then added
    (e.g. the live translator, or a game update): entries without a record are machine translations (as for the
    shared translation memory and the translations to redo), not fixes made by hand. Separate data: new shared
    translation memory."""
    real_root = project.DATA_ROOT
    project.DATA_ROOT = os.path.join(base, "dati-memoria-vecchia")
    try:
        game = os.path.join(base, "Gioco vecchio")
        fake_web_game.create(game)
        automatic.create_backend = lambda name, log=print: DictTranslator()
        path, _, _ = automatic.analyze_and_translate(game, "auto", "it", "local", silent)
        memory = project.memory_path(read_file(path))
        auto_file = memory[:-5] + ".automatiche.json"
        mem = read_file(memory)
        with open(auto_file, "w", encoding="utf-8") as f:  # only two recorded entries, one fixed by hand
            json.dump({"Start a new game": mem["Start a new game"], "Delete": "Elimina"}, f)
        items = corrections.load(path)
        texts, by_hand = corrections.texts_to_retranslate(items)
        check("memory of an old version with few recorded entries: 'Fixed by hand' only the real ones, the "
                  "others are re-translated with 'Re-translate everything' (which says so)",
                  items.counts()["by_hand"] == 1 and entry(items, "Delete").by_hand and by_hand == 1
                  and "Delete" not in texts and "Quit to the desktop" in texts and "Start a new game" in texts
                  and items.known_machine and items.unannotated == len(mem) - 2)
        shared_before = read_file(project.shared_path("en", "it"))
        corrections.save(path, {"Quit to the desktop": "Torna a Windows",
                                    "Options and settings": "Options and settings"})
        auto, shared = read_file(auto_file), read_file(project.shared_path("en", "it"))
        items = corrections.load(path)
        check("fix of an unrecorded entry: recorded with the previous translation, so fixed by hand (Re-translate "
                  "everything does not touch it); left identical to the original it stays out of the shared memory",
                  auto.get("Quit to the desktop") == mem["Quit to the desktop"]
                  and auto.get("Options and settings") == mem["Options and settings"]
                  and entry(items, "Quit to the desktop").by_hand and entry(items, "Options and settings").by_hand
                  and "Quit to the desktop" not in corrections.texts_to_retranslate(items)[0]
                  and shared.get("Quit to the desktop") == "Torna a Windows"
                  and shared.get("Options and settings") == shared_before.get("Options and settings")
                  != "Options and settings")
        # the next translation too (which rereads the fixes made by hand for the shared translation memory) does not
        # put it there
        p = project.Project(path, silent)
        p._shared_memory("en", "it")
        check("... nor the shared translation memory reread by the next translation",
                  read_file(project.shared_path("en", "it")).get("Options and settings") != "Options and settings")
        automatic.restore(game, silent)
    finally:
        project.DATA_ROOT = real_root


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    if not os.environ.get("PATCHSMITH_DATA"):
        project.DATA_ROOT = os.path.join(base, "data_folder")
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    automatic.PROFILES_FOLDER = None
    automatic.GAME_SPACE = automatic.DATA_SPACE = 0
    steam.steam_folder = lambda: None
    ollama.folder = lambda: os.path.join(base, "ollama")  # local translator not installed (unless the test says so)
    ollama.recommended_model = lambda gb=None: ("4b", "test")
    theme.choose = _choose
    theme.warn = theme.inform = theme.error = _warn
    theme.ask = lambda _g, _c, title_text, text: WINDOWS.append((title_text, text, [])) or False
    gui.SETTINGS = None
    gui.App._initial_check = lambda self: None  # no after-play check at startup
    real_fix = gui.App._fix

    def fix(self, filter_=None):
        self._last_fixes = real_fix(self, filter_)
        return self._last_fixes

    gui.App._fix = fix
    test_functions(base)
    root = tk.Tk()
    app = gui.App(root)
    try:
        test_web(base, root, app)
        test_pages(app, root)
        test_gamemaker_space(base, root, app)
        test_unity_cache(base, root, app)
        test_old_memory(base)
    finally:
        root.destroy()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
