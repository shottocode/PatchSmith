"""Interface language (ui_language.py, patchsmith/ui_language/*.json).

Usage: python -I test/test_ui_language.py <test_folder_that_does_not_exist>
- translation of the texts: identical, with values inside (%s, %d), translated pieces inside, log prefixes, several
  lines;
- the English file: every translation has the same placeholders as the Italian text; texts already in another language
  stay as they were and the code is not there;
- command line: English commands and Italian aliases are the same; help (-h) in the interface language;
- the window in English: buttons, drop-down menus, "From"/"To" languages (and back to the code), little flag with the
  languages menu; once a language is chosen, the whole window (log included) changes at once, without reopening, and
  remembers it.
"""
import collections
import json
import os
import subprocess
import sys
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (gui, icon, ui_language as ui, language,  # noqa: E402
                        project)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def test_translation():
    ui.set_("it")
    t = ui.translate
    check("testi uguali, con valori dentro, pezzi dentro, prefissi del registro, ripieghi e righe non nel file",
              t("Create report") == "Crea rapporto"
              and t("Translated 400 of 1654 (24%)...") == "Tradotti 400 di 1654 (24%)..."
              and t("  ! The game is open: close it and try again.") == "  ! Il gioco e' aperto: chiudilo e riprova."
              and t("Analysis and translation: D:\\games\\x") == "Analisi e traduzione: D:\\games\\x"
              and t("English (en)") == "Inglese (en)"
              and t("A sentence that is not in the file") == "A sentence that is not in the file"
              and t("Create report\nCreate report") == "Crea rapporto\nCrea rapporto")
    # a message made of several entries of the file one after the other (also an entry of two sentences): in groups
    # of sentences
    entries = ui._state["texts"]
    test = {"%s are missing. I'll add them.": "Mancano %s. Le metto io.",
            "%d sentences are left.": "%d frasi sono rimaste."}
    entries.update(test)
    ui._state["models"] = None
    try:
        check("messaggio di piu' frasi: tradotto a gruppi di frasi, quelle che non ci sono restano",
                  t("à, è are missing. I'll add them. 3 sentences are left.")
                  == "Mancano à, è. Le metto io. 3 frasi sono rimaste."
                  and t("The game is open: close it and try again. A new sentence.")
                  == "Il gioco e' aperto: chiudilo e riprova. A new sentence.")
    finally:
        for k in test:
            del entries[k]
        ui._state["models"] = None
    ui.set_("en")
    check("in inglese (la lingua del codice) nessuna traduzione", ui.translate("Create report") == "Create report")
    languages = ui.languages()
    check("lingue disponibili: inglese (dal codice) e italiano (dal file)",
              languages["en"]["name"] == "English" and languages["it"]["name"] == "Italiano")
    # first start: the Windows language if PatchSmith has it (Italian), otherwise English; the chosen one is saved
    real, before = language.windows_language, os.environ.pop("PATCHSMITH_INTERFACE", None)
    try:
        defaults = {}
        for windows in ("it-IT", "en-US", "fr-FR", "de-DE", None):
            language.windows_language = lambda default_language="it", w=windows: w
            defaults[windows] = ui.default_language()
    finally:
        language.windows_language = real
        if before is not None:
            os.environ["PATCHSMITH_INTERFACE"] = before
    check("lingua al primo avvio: quella di Windows se c'e' (italiano), altrimenti inglese (%s)" % defaults,
              defaults == {"it-IT": "it", "en-US": "en", "fr-FR": "en", "de-DE": "en", None: "en"})


def test_file():
    with open(os.path.join(ui.FOLDER, "it.json"), encoding="utf-8") as f:
        texts = json.load(f)["texts"]
    mismatched = [k for k, v in texts.items() if collections.Counter(ui._MARKER.findall(k))
               != collections.Counter(ui._MARKER.findall(v))]
    check("file italiano: %d testi, ogni traduzione con gli stessi segnaposto (%s)" % (len(texts), mismatched[:3]),
              len(texts) > 1000 and not mismatched)
    # the code's texts that are not English (markers written into games, words to recognize the languages) stay as
    # they are, and the code (e.g. the script for Godot) is not among the texts to translate
    changed_files = [k for k, v in texts.items() if language.obvious_language(k.replace("e.g.", ""), "en")
                     and v != k]
    code = [k for k in texts if k.startswith("if ") or "==" in k or k.startswith("the and of ")]
    check("file italiano: testi in altre lingue lasciati com'erano (%s), niente codice (%s)"
          % (changed_files[:2], code[:2]), not changed_files and not code)
    missing = [k for k in ui.program_texts() if k not in texts]
    check("file italiano: c'e' ogni testo del programma (%d mancanti: %s)" % (len(missing), missing[:2]), not missing)
    check("testi del programma: niente codice ne' elenchi di parole",
              not ui._looks_like_text("if not _done:") and not ui._looks_like_text("if f == null:")
              and not ui._looks_like_text(" ".join(sorted(language._WORDS["en"])))
              and ui._looks_like_text("The game is open: close it and try again.")
              and ui._looks_like_text("Game texts: %d, already translated: %d%s, to translate: %d"))


def _pixel_png(path):
    """The RGBA pixel rows of a PNG written by icon.png (without filters), to compare the pixels and not the
    compressed bytes: zlib and zlib-ng (Python 3.14+) compress differently."""
    import struct
    import zlib
    with open(path, "rb") as f:
        data = f.read()
    side, position, compressed = struct.unpack(">I", data[16:20])[0], 8, b""
    while position < len(data):
        length, kind = struct.unpack(">I4s", data[position:position + 8])
        if kind == b"IDAT":
            compressed += data[position + 8:position + 8 + length]
        position += 12 + length
    raw = zlib.decompress(compressed)
    line = 1 + 4 * side
    return [raw[i * line + 1:(i + 1) * line] for i in range(side)]


def test_sounds(app, root):
    """Sounds: off by default, the button turns them on (and remembers it); at the end of an operation the first dialog
    plays the sound of its type only once; the check at startup does not; all of them sounds.DURATION seconds long."""
    import wave
    import io
    from patchsmith import sounds, theme
    heard, real = [], sounds.play
    sounds.play = heard.append
    try:
        sounds_off = not app.sounds_on
        app._change_sounds()
        with open(gui._settings(), encoding="utf-8") as f:
            saved_sounds = json.load(f).get("sounds")
        is_on = heard == ["click"]
        del heard[:]

        def fake_operation(*_a):  # like _finish_operation: two dialogs one after the other
            theme.ON_SHOW("error")
            theme.ON_SHOW("info")
        real_finish = app._finish_operation
        app._finish_operation = fake_operation
        app._finish("Analisi e traduzione", None, None)
        after_error = list(heard)
        app._finish_operation = lambda *_a: theme.ON_SHOW("question")
        app._finish("Analisi e traduzione", None, None)
        app._finish_operation = lambda *_a: theme.ON_SHOW("warning")  # a warning: like a question
        app._finish("Analisi e traduzione", None, None)

        def partly(*_a):  # partly translated (warning), as in _show_window: "done"
            app._forced_sound = "done"
            theme.ON_SHOW("warning")
        app._finish_operation = partly
        app._finish("Analisi e traduzione", None, None)
        app._operation, app._auto_check = "check", True
        app._finish("Controllo", None, None)  # at startup no "done" (even partly translated): the warning plays
        app._operation, app._auto_check = None, False
        app._finish_operation = real_finish
        theme.ON_SHOW("info")  # outside the end of an operation every dialog plays: information and warnings
        theme.ON_SHOW("warning")  # like questions (e.g. the Google warning when you choose it), errors as errors
        theme.ON_SHOW("error")
        root.after(150, lambda: [w.destroy() for w in root.winfo_children() if isinstance(w, tk.Toplevel)])
        app._sound_pending = True
        theme.inform(root, app.colors, "Prova", "Una finestra vera: chiama AL_MOSTRARE")
        # the buttons' click: a pressed one plays, a disabled one does not (also in another window)
        events = list(heard)
        del heard[:]
        another = tk.Toplevel(root)
        button, off_button = gui.ttk.Button(another, text="Prova"), gui.ttk.Button(another, text="Spento")
        button.pack()
        off_button.pack()
        off_button.state(["disabled"])
        root.update()
        button.event_generate("<ButtonPress-1>", x=5, y=5)
        off_button.event_generate("<ButtonPress-1>", x=5, y=5)
        button_clicks = list(heard)
        another.destroy()
        app._change_sounds()  # off again
        mute_button = gui.ttk.Button(root, text="Muto")
        mute_button.place(x=0, y=0)
        root.update()
        del heard[:]
        mute_button.event_generate("<ButtonPress-1>", x=5, y=5)  # sounds off: nothing
        button_clicks.append(list(heard))
        mute_button.destroy()
        with open(gui._settings(), encoding="utf-8") as f:
            sounds_off_after = json.load(f).get("sounds") is False and not app.sounds_on
    finally:
        sounds.play = real
    durations = [wave.open(io.BytesIO(sounds.SOUNDS[n]())).getnframes() / sounds.SAMPLE_RATE
                 for n in ("done", "error", "question")]
    click = wave.open(io.BytesIO(sounds.click())).getnframes() / sounds.SAMPLE_RATE
    check("suoni: spenti di default, accesi (con il clic) e ricordati dal pulsante; alla fine di un'operazione "
              "la prima finestra suona \"fatto\" se e' un'informazione o un tradotto in parte (non al controllo "
              "all'apertura); ogni altra finestra sempre, errori come errori e il resto come domande (%s); durata %s"
              % (events, durations),
              sounds_off and saved_sounds is True and is_on and after_error == ["error", "question"]
              and events == ["error", "question", "question", "question", "done", "question", "question", "question",
                              "error", "done"]
              and sounds_off_after
              and all(abs(d - sounds.DURATION) < 0.001 for d in durations) and click < 0.1)
    check("clic dei pulsanti (anche in un'altra finestra), non per quelli disattivati ne' a suoni spenti (%s)"
              % button_clicks, button_clicks == ["click", []])


def test_music(app):
    """Background music: off by default, the button with the note turns it on and off (and remembers it); Windows does
    the loop without gaps (tested with silence); "Miniera" with exactly 16 bars."""
    import inspect
    import re
    import time
    from patchsmith import music
    calls, actual = [], (music.launch, music.stop)
    music.launch, music.stop = (lambda *_a: calls.append("avvia")), (lambda: calls.append("ferma"))
    try:
        music_off = not app.music_on
        app._change_music()
        with open(gui._settings(), encoding="utf-8") as f:
            saved_music = json.load(f).get("music")
        app._change_music()
        with open(gui._settings(), encoding="utf-8") as f:
            music_off_after = json.load(f).get("music") is False
    finally:
        music.launch, music.stop = actual
    silent = b"\0\0" * int(0.2 * music.SAMPLE_RATE)  # 0.2 seconds of silence, repeated by the Windows loop
    modal_open = music._open(silent)
    time.sleep(0.7)
    played = music._played()
    music._close()
    # ready in advance: the first time generated and written to a file (here in a temporary test folder), then read
    # from the file in an instant
    import tempfile
    old_folder, tempfile.tempdir = tempfile.tempdir, tempfile.mkdtemp()
    try:
        music._READY.clear()
        generated = music.prepare()
        written_lang = os.path.isfile(music._ready_file("mine"))
        music._READY.clear()
        start = time.time()
        loaded = music.prepare()
        seconds = time.time() - start
    finally:
        tempfile.tempdir = old_folder
    melody = re.search(r'melody = """(.*?)"""', inspect.getsource(music.mine), re.S).group(1)
    measures = [sum(p for _n, p in music._read(b)) for b in re.split(r"\s{3,}", melody.strip())]
    check("musica: spenta di default, accesa e spenta dal pulsante con la nota e ricordata (%s); loop senza stacchi "
              "(%s byte suonati da un blocco di %d); Miniera di %d battute da 8 ottavi" % (calls, played,
                                                                                         len(silent), len(measures)),
              music_off and saved_music is True and music_off_after and calls == ["avvia", "ferma"]
              and (not modal_open or played and played > 2 * len(silent)) and music._state["output"] is None
              and measures == [8] * 16)
    check("musica pronta in anticipo: generata e salvata la prima volta, poi letta dal file (%.3f s)" % seconds,
              generated and written_lang and loaded == generated and seconds < 0.5)


def test_window(base):
    project.DATA_ROOT = os.path.join(base, "data_folder")
    gui.SETTINGS = None
    os.makedirs(project.DATA_ROOT, exist_ok=True)
    with open(gui._settings(), "w", encoding="utf-8") as f:
        json.dump({"interface": "en", "from": "auto", "to": "it", "backend": "google"}, f)
    root = tk.Tk()
    try:
        app = gui.App(root)
        buttons = [str(w.cget("text")) for w in app.buttons if isinstance(w, gui.ttk.Button)]
        check("finestra in inglese: pulsanti (%s), traduttore, lingue Da/A e ritorno al codice" % buttons[:4],
                  "Analyze and translate" in buttons and "Create report" in buttons
                  and app.translator.get() == "Free Google (unofficial)" and app._chosen == "google"
                  and app.src_lang.get() == "Detect automatically"
                  and app._language_code(app.src_lang.get()) == language.AUTO
                  and app._language_code("English (en)") == "en" and app._target_lang() == "it")
        root.update()
        heights = (app.btn_browse.winfo_height(), app.folder_entry.winfo_height())
        positions = (app.btn_browse.winfo_rooty(), app.folder_entry.winfo_rooty())
        check("'Browse…' alto quanto il campo della cartella e alla stessa altezza (%s, %s)" % (heights, positions),
                  heights[0] == heights[1] and positions[0] == positions[1])
        equal_count = all(_pixel_png(os.path.join(icon.FOLDER, "%d.png" % side)) == icon.draw(side)
                     for side in (16, 32))
        check("icona (martello e cartella) sulla finestra e per quelle che si aprono dopo; le immagini sono quelle che "
                  "disegna icon.py", len(getattr(root, "_patchsmith_icon", ())) == 6 and equal_count)
        app._languages_menu()
        entries = [str(v.cget("text")).strip() for _c, v in app._language_entries]
        modal_open = app._language_dropdown is not None
        app._languages_menu()  # again: it closes
        check("bandierina con la tendina delle lingue (%s), nomi nella loro lingua; si apre e si chiude" % entries,
                  "Italiano (it)" in entries and "English (en)" in entries and app.btn_language.cget("image")
                  and modal_open and app._language_dropdown is None)
        before = app.colors["name"]
        symbol_before = str(app.btn_theme.cget("text"))
        app._change_theme()
        after, symbol_after = app.colors["name"], str(app.btn_theme.cget("text"))
        with open(gui._settings(), encoding="utf-8") as f:
            saved_theme = json.load(f).get("theme")
        app._change_theme()
        check("pulsante sole/luna: tema scuro <-> chiaro subito (anche il registro), simbolo giusto, ricordato",
                  {before, after} == {"dark", "light"} and saved_theme == after and app.colors["name"] == before
                  and symbol_before == ("☾" if before == "dark" else "☀") and symbol_after != symbol_before
                  and str(app.text.cget("background")) == app.colors["field"])
        test_sounds(app, root)
        test_music(app)
        app._log("Analysis and translation: D:\\giochi\\x")
        app.state.set("Translated 400 of 1654 (24%)...")
        app._drain_queue()
        log_en = app.text.get("1.0", "end").strip()
        started = []
        real = gui.subprocess.Popen
        gui.subprocess.Popen = lambda *a, **k: started.append(a[0])
        try:
            app._change_ui("it")
            root.update()
            buttons = [str(w.cget("text")) for w in app.buttons if isinstance(w, gui.ttk.Button)]
            with open(gui._settings(), encoding="utf-8") as f:
                saved = json.load(f).get("interface")
            check("scelta l'italiano: tutta la finestra subito in italiano, senza riaprire PatchSmith (pulsanti %s, "
                      "traduttore, Da/A con le stesse scelte, registro, riga di stato) e ricordato" % buttons[:2],
                      not started and root.winfo_exists() and saved == "it"
                      and "Analizza e traduci" in buttons and "Crea rapporto" in buttons
                      and app.translator.get() == "Google gratuito (non ufficiale)" and app._chosen == "google"
                      and "Google gratuito (non ufficiale)" in app.translator_choice.cget("values")
                      and app.src_lang.get() == "Rileva automaticamente"
                      and app._language_code(app.src_lang.get()) == language.AUTO
                      and app._target_lang() == "it" and app.a.get() == "Italiano (it)"
                      and log_en == "Analysis and translation: D:\\giochi\\x"
                      and app.text.get("1.0", "end").strip() == "Analisi e traduzione: D:\\giochi\\x"
                      and app.state.get() == "Tradotti 400 di 1654 (24%)...")
            app._change_ui("en")
            root.update()
            buttons = [str(w.cget("text")) for w in app.buttons if isinstance(w, gui.ttk.Button)]
            check("e di nuovo l'inglese: pulsanti e registro rifatti",
                      not started and "Analyze and translate" in buttons
                      and app.src_lang.get() == "Detect automatically"
                      and app.text.get("1.0", "end").strip() == log_en
                      and app.state.get() == "Translated 400 of 1654 (24%)...")
        finally:
            gui.subprocess.Popen = real
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass
        ui.set_("it")
        gui._translate_constants()


def test_executable(base):
    """PatchSmith.exe with the icon next to a test PatchSmith.pyw: compiled, not rebuilt if already up to date, and on
    double click it opens the .pyw with the installed Python (here the test .pyw only writes a file)."""
    if not sys.platform.startswith("win"):
        return
    folder = os.path.join(base, "programma con spazi")
    os.makedirs(folder)
    with open(os.path.join(folder, "PatchSmith.pyw"), "w", encoding="utf-8") as f:
        f.write("import os, sys\nopen(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'partito.txt'), 'w')"
                ".write(sys.version)\n")
    created_one = icon.executable(folder)
    calls, real = [], icon.subprocess.run
    icon.subprocess.run = lambda *a, **k: calls.append(a)
    try:
        again = icon.executable(folder)
    finally:
        icon.subprocess.run = real
    exe = os.path.join(folder, "PatchSmith.exe")
    with open(exe, "rb") as f:
        data = f.read()
    started_file = os.path.join(folder, "partito.txt")
    if created_one:
        subprocess.run([exe], timeout=30)
        for _ in range(40):
            if os.path.isfile(started_file):
                break
            time.sleep(0.25)
    check("PatchSmith.exe con l'icona dentro accanto al .pyw (%d byte); non ricompilato se aggiornato; apre il .pyw "
              "con Python" % len(data), created_one and again and not calls and data[:2] == b"MZ"
              and b"\x89PNG" in data and os.path.isfile(started_file))


def test_command_line(base):
    """Commands and options in English, the former Italian names as aliases; the help in the interface language."""
    smith = os.path.join(project.PROJECT_FOLDER, "smith.py")
    game = os.path.join(base, "gioco vuoto")
    os.makedirs(game)

    def run_command(arguments, window_language):
        test_env = dict(os.environ, PATCHSMITH_DATA=os.path.join(base, "dati-cli"),
                        PATCHSMITH_INTERFACE=window_language)
        r = subprocess.run([sys.executable, "-I", smith] + arguments, capture_output=True, encoding="utf-8",
                           errors="replace", env=test_env, timeout=120)
        return r.returncode, r.stdout
    english, alias = run_command(["detect", game], "it"), run_command(["rileva", game], "it")
    check("riga di comando: 'detect' e il nome di prima 'rileva' fanno lo stesso",
              english[0] == 0 and english == alias)
    code, help_text = run_command(["-h"], "en")
    _, auto_help = run_command(["auto", "-h"], "en")
    _, help_it = run_command(["-h"], "it")
    check("aiuto in inglese: comandi inglesi con gli alias, spiegazioni tradotte; in italiano resta italiano",
              code == 0 and "restore (ripristina)" in help_text and "restores the original game files" in help_text
              and "--force, --forza" in auto_help and "--from LANG" in auto_help
              and "rimette i file originali del gioco" in help_it)


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    test_translation()
    test_file()
    test_command_line(base)
    test_window(base)
    test_executable(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
