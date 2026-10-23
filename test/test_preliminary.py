"""Tests of the checks before starting and of the safe default choices (point 5).

Usage: python -I test/test_preliminary.py <empty_temp_folder>
PatchSmith's data go to <cartella>/dati, the "installed" local translator to <cartella>/ollama (empty: not
installed); Steam, open processes, free space and graphics card are simulated. No real game, no network,
no program started. Covers:
- automatic.preflight_checks: game open, From/To languages, folder that cannot be written to (also in the
  Program Files folder), little space, translator paused (local one installed or not), local translator not
  installed, and what is checked for Analyze and translate, Reapply and Restore; the test file does not stay in the
  folder;
- the same game in another folder: identity (Unity, Steam, executables, name), a copy reuses the translation memory
  of the original (also the translations fixed by hand), a moved game takes its game profile with it;
- default translator: always the local one (even if not installed); Google "often blocked";
- graphics card: nvidia-smi, Windows registry, WMI (at the 4 GB limit it cannot be told) and the recommended model;
- test of the local translator after installation (backend.probe_local): graphics card, processor only, error;
- limiter messages that recommend the local translator.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_web_game  # noqa: E402
from patchsmith import (auto as automatic, backend, gui, limiter, language, ollama,
                        project, system, steam)  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def codes(problems):
    return [p.code for p in problems]


def by_code(problems, code):
    return next((p for p in problems if p.code == code), None)


def web_game(folder, exe=None):
    fake_web_game.create(folder)
    if exe:
        open(os.path.join(folder, exe), "wb").write(b"MZ")
    return folder


def unity_game(folder, exe_name, company, product):
    data = os.path.join(folder, exe_name + "_Data")
    os.makedirs(data)
    open(os.path.join(folder, exe_name + ".exe"), "wb").write(b"MZ")
    open(os.path.join(data, "globalgamemanagers"), "wb").write(b"\x00" * 16 + b"2022.3.10f1\x00")
    with open(os.path.join(data, "app.info"), "w", encoding="utf-8") as f:
        f.write("%s\n%s" % (company, product))
    return folder


class FakeServer:
    """In place of ollama.Server for backend.prova_locale: answers and share of the model on the graphics card."""

    def __init__(self, response="Inizia la partita", vram=100, error=None):
        self.response, self.vram, self.error = response, vram, error
        self.closed = False

    def launch(self):
        if self.error:
            raise self.error

    def call(self, path, data=None, timeout=180):
        if path == "/api/chat":
            return {"message": {"content": self.response}, "done_reason": "stop", "eval_count": 5,
                    "eval_duration": 100_000_000, "prompt_eval_count": 20}
        if path == "/api/ps":
            return {"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": self.vram}]}
        return {}

    def close(self):
        self.closed = True


# --- checks before starting ------------------------------------------------------------------------------------

def test_checks(base):
    game = web_game(os.path.join(base, "giochi", "Lantern Keeper"), "Lantern Keeper.exe")
    before = sorted(os.listdir(game))
    problems = automatic.preflight_checks(game, "fake", "translate", language.AUTO, "it")
    check("gioco pronto: nessun problema, e il file di prova della scrittura non resta (%s)" % codes(problems),
              problems == [] and sorted(os.listdir(game)) == before)

    # game open: "Try again", for all the operations; if it cannot be checked, the operation decides
    real = system.game_open
    system.game_open = lambda _c: True
    try:
        all_items = {op: codes(automatic.preflight_checks(game, "fake", op, language.AUTO, "it"))
                 for op in ("translate", "reapply", "restore")}
        is_open = automatic.preflight_checks(game, "fake", "translate", language.AUTO, "it")[0]
    finally:
        system.game_open = real
    check("gioco aperto: primo controllo, con 'Riprova', per tradurre, riapplicare e ripristinare",
              all(c == ["GAME_OPEN"] for c in all_items.values()) and is_open.action == "retry"
              and "Try again" in is_open.message)

    def system_error(_c):
        raise system.SysError("test")
    system.game_open = system_error
    try:
        problems = automatic.preflight_checks(game, "fake", "translate", language.AUTO, "it")
    finally:
        system.game_open = real
    check("controllo dei processi non riuscito: nessun avviso qui (lo dice l'operazione stessa)", problems == [])

    # languages
    cases ={("Inglesse", "it"): None, ("en", "Italianoo"): None, ("xx", "it"): "continue", ("it", "it"): None,
            ("en", "it"): "ok", (language.AUTO, "it"): "ok", ("en", "pt-BR"): "continue"}
    wrong_seeds = []
    for (src_lang, a), expected in cases.items():
        found = [p for p in automatic.preflight_checks(game, "fake", "translate", src_lang, a) if p.code == "LANGUAGE"]
        got = "ok" if not found else found[0].action
        if got != expected:
            wrong_seeds.append("%s->%s: %s" % (src_lang, a, got))
    same = automatic._language_problems("it", "it")
    check("lingue: sconosciute e uguali fermano, un codice fuori elenco si puo' usare comunque%s" % (
        " (sbagliati: %s)" % wrong_seeds if wrong_seeds else ""),
              not wrong_seeds and "same language" in same[0].message)
    check("lingue: si controllano solo per Analizza e traduci",
              "LANGUAGE" not in codes(automatic.preflight_checks(game, "fake", "reapply", "Inglesse", "it")))

    # folder that cannot be written to (Program Files, controlled folder access, antivirus)
    real_version = automatic.check_writable
    automatic.check_writable = lambda _c: PermissionError(13, "Accesso negato")
    old_pf = os.environ.get("ProgramFiles")
    try:
        os.environ["ProgramFiles"] = os.path.join(base, "altrove")
        outside = {op: by_code(automatic.preflight_checks(game, "fake", op, language.AUTO, "it"),
                                "READ_ONLY") for op in ("translate", "reapply", "restore")}
        os.environ["ProgramFiles"] = os.path.join(base, "giochi")
        inside = by_code(automatic.preflight_checks(game, "fake", "translate", language.AUTO, "it"),
                            "READ_ONLY")
    finally:
        automatic.check_writable = real_version
        if old_pf is None:
            os.environ.pop("ProgramFiles", None)
        else:
            os.environ["ProgramFiles"] = old_pf
    check("cartella protetta: si ferma (nessuna azione) per tutte le operazioni, con 'amministratore' e l'accesso "
              "controllato alle cartelle", all(p and p.action is None for p in outside.values())
              and "administrator" in outside["translate"].message
              and "Controlled Folder Access" in outside["translate"].message)
    check("cartella protetta dentro Programmi: lo dice e spiega come spostare il gioco",
              inside and "Program Files" in inside.message and "Move install folder" in inside.message)
    one_file = os.path.join(base, "un-file")
    open(one_file, "w").close()
    check("prova di scrittura vera: si' in una cartella (niente resta), errore se non e' una cartella",
              real_version(game) is None and sorted(os.listdir(game)) == before
              and isinstance(real_version(one_file), OSError))

    # free space
    real_disk_usage = automatic.shutil.disk_usage
    automatic.shutil.disk_usage = lambda _p: types.SimpleNamespace(total=10 ** 12, used=0, free=50 * 1024 ** 2)
    try:
        space = {op: [p for p in automatic.preflight_checks(game, "fake", op, language.AUTO, "it")
                       if p.code == "DISK_SPACE"] for op in ("translate", "reapply", "restore")}
    finally:
        automatic.shutil.disk_usage = real_disk_usage
    check("poco spazio (50 MB): avviso per il gioco e per i dati, si puo' continuare; non per Ripristina",
              len(space["translate"]) == 2 and len(space["reapply"]) == 2 and not space["restore"]
              and all(p.action == "continue" and "50 MB" in p.message for p in space["translate"]))

    # translator paused: the local translator is offered (installing it, if missing)
    limiter.Limiter("google", log=None).blocked(429, "test")
    real_state = ollama.state
    try:
        pause = by_code(automatic.preflight_checks(game, "google", "translate", language.AUTO, "it"),
                           "TRANSLATOR_PAUSED")
        ollama.state = lambda: "ready"
        ready_text = by_code(automatic.preflight_checks(game, "google", "translate", language.AUTO, "it"),
                            "TRANSLATOR_PAUSED")
        ollama.state = real_state
        reapply = codes(automatic.preflight_checks(game, "google", "reapply", language.AUTO, "it"))
        deepl = codes(automatic.preflight_checks(game, "deepl", "translate", language.AUTO, "it"))
    finally:
        ollama.state = real_state
        limiter.Limiter("google", log=None).reset()
    check("Google in pausa: 'Installa il traduttore locale' (non installato), 'Usa il traduttore locale' "
              "(installato); niente per Riapplica ne' per un altro traduttore",
              pause and pause.action == "install_local" and "Google" in pause.message
              and "local translator" in pause.message and ready_text and ready_text.action == "use_local"
              and "TRANSLATOR_PAUSED" not in reapply and "TRANSLATOR_PAUSED" not in deepl)

    # local translator chosen but not installed
    absent = by_code(automatic.preflight_checks(game, "local", "translate", language.AUTO, "it"),
                         "LOCAL_MISSING")
    ollama.state = lambda: "ready"
    try:
        is_ready = codes(automatic.preflight_checks(game, "local", "translate", language.AUTO, "it"))
    finally:
        ollama.state = real_state
    check("traduttore locale non installato: 'Installa ora' prima di partire; installato: nessun avviso",
              absent and absent.action == "install_local" and "LOCAL_MISSING" not in is_ready
              and "continue without" in absent.message)
    # "Continue anyway" without a translator: the game is analyzed, but it stops before changing it (the official
    # language, if there is one, is activated without a translator)
    fingerprints = {n: open(os.path.join(r, n), "rb").read() for r, _d, f in os.walk(game) for n in f}
    try:
        automatic.analyze_and_translate(game, "auto", "it", "local", silent)
        error = None
    except backend.BackendError as e:
        error = e
    check("continuare senza traduttore locale: errore 'traduttore non pronto' prima di toccare il gioco",
              error is not None and getattr(error, "code", None) == "TRANSLATOR_MISSING"
              and {n: open(os.path.join(r, n), "rb").read() for r, _d, f in os.walk(game) for n in f} == fingerprints)


# --- the same game in another folder -----------------------------------------------------------------------------

def test_same_game(base):
    unity_a = unity_game(os.path.join(base, "unity", "A", "Rubinite"), "Rubinite", "Cup Dog Games", "Rubinite")
    unity_b = unity_game(os.path.join(base, "unity", "B", "Rubinite Copia"), "Rubinite", "Cup Dog Games", "Rubinite")
    unity_c = unity_game(os.path.join(base, "unity", "C", "Altro"), "Rubinite", "Studio", "Altro Gioco")
    a, b, c = (automatic.identity(x) for x in (unity_a, unity_b, unity_c))
    check("identita Unity: azienda/prodotto ed eseguibili (%s)" % a,
              a.get("unity") == "Cup Dog Games/Rubinite" and a.get("exe") == ["rubinite.exe"])
    check("stesso gioco: stesso prodotto Unity (anche in una cartella con un altro nome); un prodotto diverso no, "
              "anche con lo stesso eseguibile", automatic.same_game(a, b) and not automatic.same_game(a, c))
    check("stesso gioco: Steam (appid), eseguibili, nome della cartella (anche con il '-2' del nome di un profilo, "
              "ma una cartella 'Gioco 2' e' il seguito)",
              automatic.same_game({"steam": "123"}, {"steam": "123", "exe": ["x.exe"]})
              and not automatic.same_game({"steam": "123", "exe": ["x.exe"]}, {"steam": "9", "exe": ["x.exe"]})
              and automatic.same_game({"exe": ["a.exe", "b.exe"]}, {"exe": ["b.exe"]})
              and not automatic.same_game({"exe": ["a.exe"]}, {"exe": ["b.exe"], "name": "a"})
              and automatic.same_game({"name": "lantern-keeper"}, {"name": "lantern-keeper-2", "from_profile": True})
              and automatic.same_game({"name": "lantern-keeper-2", "from_profile": True}, {"name": "lantern-keeper"})
              and not automatic.same_game({"name": "lantern-keeper"}, {"name": "lantern-keeper-2"})
              and not automatic.same_game({"name": "gioco"}, {"name": "gioco"}))
    real = steam.game_installed
    steam.game_installed = lambda folder, steam=None: {"appid": "4242", "name": "Prova"}
    try:
        with_steam = automatic.identity(unity_a)
        unlabeled = automatic.identity(unity_a, steam=False)
    finally:
        steam.game_installed = real
    check("identita: appid di Steam dal manifesto (non cercato con steam=False)",
              with_steam.get("steam") == "4242" and "steam" not in unlabeled)
    # programs that many games share (.NET runtime, Ren'Py, DOSBox): they do not mean it is the same game
    singular = web_game(os.path.join(base, "net", "Uno", "Gioco"), "Primo Gioco.exe")
    two = web_game(os.path.join(base, "net", "Due", "Gioco"), "Secondo Gioco.exe")
    for d in (singular, two):
        os.makedirs(os.path.join(d, "lib", "py3-windows-x86_64"))
        for exe in ("createdump.exe", os.path.join("lib", "py3-windows-x86_64", "zsync.exe"), "DOSBox.exe"):
            open(os.path.join(d, exe), "wb").write(b"MZ")
    i_one, i_two = automatic.identity(singular), automatic.identity(two)
    check("identita: createdump, zsync, DOSBox non contano; giochi diversi con quei programmi in comune non sono "
              "lo stesso gioco (%s)" % i_one,
              i_one.get("exe") == ["primo gioco.exe"] and not automatic.same_game(i_one, i_two))

    # a copy of the already translated web game: its translation memory is offered (with the test translator, the
    # test one)
    original = web_game(os.path.join(base, "web", "Originale", "Lantern Keeper"), "Lantern Keeper.exe")
    path, completed, _ = automatic.analyze_and_translate(original, "auto", "it", "fake", silent)
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    memory = project.memory_path(profile, test=True)
    entries = json.load(open(memory, encoding="utf-8"))
    entries["Start a new game"] = "VALORE CORRETTO A MANO"
    json.dump(entries, open(memory, "w", encoding="utf-8"), ensure_ascii=False)
    check("originale tradotto: l'identita resta nel profilo",
              completed and profile.get("identity", {}).get("exe") == ["lantern keeper.exe"])
    copy_dir = web_game(os.path.join(base, "web", "Copia", "Lantern Keeper"), "Lantern Keeper.exe")
    proposal = by_code(automatic.preflight_checks(copy_dir, "fake", "translate", language.AUTO, "it"),
                          "SAME_GAME")
    online = by_code(automatic.preflight_checks(copy_dir, "deepl", "translate", language.AUTO, "it"),
                        "SAME_GAME")
    other_language = by_code(automatic.preflight_checks(copy_dir, "fake", "translate", language.AUTO, "fr"),
                              "SAME_GAME")
    check("copia: 'stesso gioco gia' tradotto' con il profilo dell'originale e i testi salvati; non con una memoria "
              "vuota (traduttore vero) ne' per un'altra lingua", proposal and proposal.action == "reuse"
              and proposal.data["profile"] == path and proposal.data["texts"] == len(entries)
              and not proposal.data["moved"] and "Shall I reuse its translations" in proposal.message
              and online is None and other_language is None)
    # a game profile from a previous version (without 'identity'): recognized by its folder, if it is still there
    with open(path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in profile.items() if k != "identity"}, f, ensure_ascii=False)
    try:
        old = by_code(automatic.preflight_checks(copy_dir, "fake", "translate", language.AUTO, "it"),
                             "SAME_GAME")
    finally:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False)
    check("profilo di una versione precedente, senza identita: riconosciuto dalla sua cartella",
              old and old.data["profile"] == path and not old.data["moved"])
    lines = []
    new, completed, _ = automatic.analyze_and_translate(copy_dir, "auto", "it", "fake", lines.append,
                                                         reuse=proposal.data["profile"])
    with open(new, encoding="utf-8") as f:
        profile_copy = json.load(f)
    copy_memory = json.load(open(project.memory_path(profile_copy, test=True), encoding="utf-8"))
    check("copia con 'Riusa': profilo nuovo con la memoria dell'originale (anche la correzione a mano) e "
              "l'origine segnata", new != path and completed
              and copy_memory.get("Start a new game") == "VALORE CORRETTO A MANO"
              and profile_copy.get("translations_from") == profile["name"]
              and any("I'm reusing the translations" in r for r in lines))
    check("copia con il suo profilo: non si chiede piu'",
              "SAME_GAME" not in codes(automatic.preflight_checks(copy_dir, "fake", "translate", language.AUTO,
                                                                             "it")))
    # copying the translations fails (disk full, file in use): what is missing is translated from scratch, without
    # stopping "Analyze and translate" and without leaving a half-written translation memory
    third_game = web_game(os.path.join(base, "web", "Terza", "Lantern Keeper"), "Lantern Keeper.exe")
    real_save, failures = automatic.shared_file.save_merging, []

    def broken_save(*a, **k):  # the first write (the copy of the translation memory) fails, the others succeed
        if not failures:
            failures.append(a[0])
            raise OSError(28, "No space left on device")
        return real_save(*a, **k)

    automatic.shared_file.save_merging = broken_save
    lines = []
    try:
        new3, completed3, _ = automatic.analyze_and_translate(third_game, "auto", "it", "fake", lines.append,
                                                               reuse=path)
    finally:
        automatic.shared_file.save_merging = real_save
    with open(new3, encoding="utf-8") as f:
        memory3 = json.load(open(project.memory_path(json.load(f), test=True), encoding="utf-8"))
    check("copia delle traduzioni non riuscita: Analizza e traduci continua e traduce da capo, con l'avviso",
              completed3 and failures and failures[0].endswith(".json")
              and memory3.get("Start a new game") != "VALORE CORRETTO A MANO"
              and memory3.get("Start a new game") and any("I haven't been able to copy" in r for r in lines))

    # a moved game: the recorded folder is no longer there, the game profile moves to the new folder
    moved = os.path.join(automatic.profiles_folder(), "gioco-spostato.json")
    with open(moved, "w", encoding="utf-8") as f:
        json.dump({"name": "gioco-spostato", "game_folder": os.path.join(base, "sparita", "Gioco").replace(
            os.sep, "/"), "source_language": "auto", "target_language": "it", "backend": "fake", "file": [],
            "identity": {"name": "game", "exe": ["gioco spostato.exe"]}}, f)
    for test in (False, True):
        with open(project.memory_path({"name": "gioco-spostato", "target_language": "it"}, test), "w",
                  encoding="utf-8") as f:
            json.dump({"Start a new game": "Nuova partita (spostato)"}, f)
    new_folder = web_game(os.path.join(base, "web", "Nuovo Disco", "Gioco Spostato 2"), "Gioco Spostato.exe")
    profiles_before = len(list(automatic.list_profiles()))
    proposal = by_code(automatic.preflight_checks(new_folder, "deepl", "translate", language.AUTO, "it"),
                          "SAME_GAME")
    check("gioco spostato: proposto con 'la cartella non c'e' piu'' (dall'identita salvata nel profilo)",
              proposal and proposal.data["moved"] and proposal.data["profile"] == moved
              and "no longer exists" in proposal.message)
    path_s, _, _ = automatic.analyze_and_translate(new_folder, "auto", "it", "fake", silent,
                                                     reuse=proposal.data["profile"])
    with open(moved, encoding="utf-8") as f:
        after = json.load(f)
    check("gioco spostato con 'Riusa': stesso profilo e stessa memoria nella cartella nuova, nessun profilo nuovo",
              path_s == moved and automatic._normalize(after["game_folder"]) == automatic._normalize(
                  new_folder)
              and after.get("previous_folder", "").endswith("sparita/Gioco")
              and len(list(automatic.list_profiles())) == profiles_before
              and json.load(open(project.memory_path(after, test=True), encoding="utf-8")).get(
                  "Start a new game") == "Nuova partita (spostato)")


# --- default choices, graphics card, test of the local translator ---------------------------------------------

def test_defaults(base):
    check("traduttore predefinito: quello locale anche se non e' installato ('da installare', 'Installa...')",
              ollama.state() != "ready" and backend.default() == "local")
    check("etichette: Google 'non ufficiale' (con l'avviso alla scelta), modelli con il compromesso",
              "unofficial" in gui.TRANSLATORS["google"] and "block them" in gui.GOOGLE_WARNING
              and "slower" in gui.LOCAL_MODELS["12b"])

    # graphics card: nvidia-smi, then the registry, then WMI; at WMI's 4 GB limit it cannot be told
    actual = ollama._nvidia_smi, ollama._video_registry, ollama._wmi_video
    calls = []
    try:
        for values, expected in (((8.0, 4.0, 2.0), 8.0), ((None, 6.0, 2.0), 6.0), ((None, None, 2.0), 2.0),
                               ((None, None, None), None)):
            ollama._VIDEO_MEMORY.clear()
            ollama._nvidia_smi, ollama._video_registry, ollama._wmi_video = (
                (lambda v=v, i=i: calls.append(i) or v) for i, v in enumerate(values))
            calls.clear()
            got = ollama.video_memory()
            if got != expected:
                raise AssertionError((values, got))
        order = calls == [0, 1, 2]
        ollama._nvidia_smi = lambda: (_ for _ in ()).throw(OSError("guasto"))
        ollama._VIDEO_MEMORY.clear()
        ollama._video_registry = lambda: 8.0
        robust = ollama.video_memory() == 8.0
        ok = True
    except AssertionError as e:
        print("   ", e)
        ok = order = robust = False
    finally:
        ollama._nvidia_smi, ollama._video_registry, ollama._wmi_video = actual
        ollama._VIDEO_MEMORY.clear()
    check("memoria video: nvidia-smi, poi registro di Windows, poi WMI; un errore non ferma nulla",
              ok and order and robust)
    real_run = ollama.subprocess.run
    responses, commands = {}, []
    ollama.subprocess.run = lambda command, **_k: commands.append(command[0]) or types.SimpleNamespace(
        returncode=0, stdout=responses["output"], stderr=b"")
    real_program = ollama._nvidia_smi_program
    ollama._nvidia_smi_program = lambda: sys.executable  # a file that exists, never executed (run is simulated)
    try:
        responses["output"] = b"8192\r\n"
        nvidia = ollama._nvidia_smi()
        responses["output"] = b"4293918720\r\n"
        wmi_limit = ollama._wmi_video()
        responses["output"] = b"2147483648\r\n0\r\n"
        wmi_2 = ollama._wmi_video()
    finally:
        ollama.subprocess.run, ollama._nvidia_smi_program = real_run, real_program
    check("letture: nvidia-smi in MiB (8192 -> 8 GB), WMI al limite di 4 GB = non si sa, WMI 2 GB; PowerShell "
              "con il percorso intero (%s)" % commands[1:2],
              nvidia == 8.0 and wmi_limit is None and wmi_2 == 2.0 and all(os.path.isabs(c) for c in commands))
    # nvidia-smi never from the current folder (a program with that name there must not start): System32, NVSMI, PATH
    current_name, elsewhere = os.path.join(base, "nvsmi-corrente"), os.path.join(base, "nvsmi-path")
    for d in (current_name, elsewhere):
        os.makedirs(d)
        open(os.path.join(d, "nvidia-smi.exe"), "wb").close()
    env_vars = {k: os.environ.get(k) for k in ("SystemRoot", "ProgramFiles", "PATH")}
    cwd = os.getcwd()
    try:
        os.environ.update(SystemRoot=os.path.join(base, "windows"), ProgramFiles=os.path.join(base, "programmi"))
        os.chdir(current_name)
        os.environ["PATH"] = os.pathsep.join([".", current_name])
        current_only = ollama._nvidia_smi_program()
        os.environ["PATH"] = os.pathsep.join([".", current_name, elsewhere])
        in_path = ollama._nvidia_smi_program()
    finally:
        os.chdir(cwd)
        for k, v in env_vars.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    check("nvidia-smi: mai quello della cartella corrente, si' quello nel PATH (%s, %s)"
              % (current_only, in_path), current_only is None and in_path and os.path.dirname(in_path) == elsewhere)
    advice = {gb: ollama.recommended_model(gb)[0] for gb in (8.0, 7.9, 6.0, 4.0)}
    ollama._VIDEO_MEMORY[:] = [None]
    try:
        unlabeled = ollama.recommended_model()
    finally:
        ollama._VIDEO_MEMORY.clear()
    check("modello consigliato: 12B da 8 GB (anche 7,9), 4B sotto e se la scheda non si conosce (%s)" % advice,
              advice == {8.0: "12b", 7.9: "12b", 6.0: "4b", 4.0: "4b"} and unlabeled[0] == "4b"
              and "8 GB" in ollama.recommended_model(8.0)[1] and "Fast" in unlabeled[1])
    check("avviso prima di scaricare: grafica integrata o scheda vecchia -> lento sul processore (con DeepL come "
              "alternativa); poca RAM -> avviso; scheda da 8 GB, 16 GB di RAM o PC sconosciuto -> nessun avviso",
              "processor" in ollama.performance_warning(0.5, 16) and "DeepL" in ollama.performance_warning(0.5, 16)
              and "RAM" in ollama.performance_warning(8, 6) and ollama.performance_warning(8, 16) == ""
              and ollama.performance_warning(3.9, 16) == "")

    # command line: "local" without installation tells the recommended model and the command to install it;
    # "--installa" also does the test sentence (code 2 if it fails)
    from patchsmith import cli
    output = io.StringIO()
    ollama._VIDEO_MEMORY[:] = [8.0]
    try:
        with contextlib.redirect_stdout(output):
            status_code = cli.main(["local"])
    finally:
        ollama._VIDEO_MEMORY.clear()
    text = output.getvalue()
    actual = ollama.download_description, ollama.install, backend.probe_local
    tests = []
    ollama.download_description = lambda selected: ["fake"]
    ollama.install = lambda selected, log=print: True
    backend.probe_local = lambda model=None, log=print, **_k: tests.append(model) or {
        "ok": False, "message": "la prova di traduzione non e' riuscita: prova"}
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            trial_code = cli.main(["local", "--installa", "12b"])
    finally:
        ollama.download_description, ollama.install, backend.probe_local = actual
    check("riga di comando 'locale': modello consigliato (12B con 8 GB) e comando; '--installa' fa la frase di "
              "prova e esce con 2 se non riesce", status_code == 0 and "Quality model (12B)" in text
              and "--install 12b (or 4b" in text and trial_code == 2 and tests == ["12b"])

    # test of the local translator after installation
    server = FakeServer()
    r = backend.probe_local("4b", silent, server=server)
    check("prova del traduttore locale: tradotto sulla scheda video, con i tempi (%s)" % r["message"],
              r["ok"] and r["gpu"] == 1.0 and r["translation"] == "Inizia la partita"
              and "on the graphics card" in r["message"] and "Start game" in r["message"]
              and r["seconds"] is not None)
    r = backend.probe_local("4b", silent, server=FakeServer(vram=0))
    check("prova: solo processore -> 'sara' lento' e il consiglio", r["ok"] and r["gpu"] == 0
              and "only with the processor" in r["message"])
    r = backend.probe_local("4b", silent, server=FakeServer(response="Start game"))
    check("prova: frase rimasta in originale -> non riuscita, senza errori", not r["ok"]
              and "stayed untranslated" in r["message"])
    r = backend.probe_local("4b", silent, server=FakeServer(error=ollama.LocalError("non parte")))
    check("prova: server che non parte -> non riuscita, con il motivo, senza eccezioni",
              not r["ok"] and "non parte" in r["message"])

    # the limiter recommends the local translator
    message = limiter.Limiter("google", log=None).blocked(429, "test")
    limiter.Limiter("google", log=None).reset()
    check("blocco di Google: il messaggio consiglia il traduttore locale, senza comandi",
              "local translator" in message and "smith.py" not in message)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "data_folder")
    automatic.PROFILES_FOLDER = None
    ollama.folder = lambda: os.path.join(base, "ollama")  # local translator not installed
    steam.game_installed = lambda folder, steam=None: None  # no real Steam
    # the free space of the tests' disk must not count (the space test simulates it separately)
    shutil.disk_usage = lambda _p: types.SimpleNamespace(total=10 ** 13, used=0, free=10 ** 12)
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    test_checks(base)
    test_same_game(base)
    test_defaults(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
