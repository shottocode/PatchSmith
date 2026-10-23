"""Command-line interface."""
import argparse
import json
import os
import sys

from . import (__version__, adapter_unity, adapter_unreal, adapter_xna, auto as automatic, language,
               detection, scan)
from . import components, dotnet, legacy, project, ui_language as ui, xnb
from .backend import BACKEND, BackendError, create_backend, default
from .project import Project
from .text import DETAIL, readable, slug


def _log(message=""):
    print(ui.translate(readable(str(message)).replace(DETAIL, "")), flush=True)


def _ui_lang():
    """The window's language (settings.json, otherwise the Windows one): also for the help and the messages here."""
    try:
        with open(project.data_folder("settings.json"), encoding="utf-8") as f:
            choice = legacy.load(f).get("interface")
    except (OSError, ValueError, AttributeError):
        choice = None
    ui.set_(choice or ui.default_language())


def _translate_help(parser):
    """The help (-h) in the interface language: description and explanations of commands and options."""
    parser.description = ui.translate(parser.description)
    for action in parser._actions:
        action.help = ui.translate(action.help)
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                choice.help = ui.translate(choice.help)
            for subfolders in {id(p): p for p in action.choices.values()}.values():  # (the aliases: same parser)
                _translate_help(subfolders)


def _profile(value_):
    """Accepts a game profile path or just the name (looked up in profiles/). A path that no longer exists (e.g.
    profili\\<gioco>.json, the former name of the folder, written in the translator's command inside an already
    translated game) counts for its name."""
    if os.path.isfile(value_):
        return value_
    if os.path.isdir(value_):
        raise SystemExit("You need a profile, not a folder: %s" % value_)
    name = os.path.splitext(os.path.basename(value_))[0] if value_.lower().endswith(".json") else value_
    candidate = os.path.join(automatic.profiles_folder(), name + ".json")
    if os.path.exists(candidate):
        return candidate
    raise SystemExit("Profile not found: %s" % value_)


def _backend_name(value_):
    """--backend: also the names of earlier versions (locale, finto)."""
    return legacy.code("backend", value_)


def cmd_detect(a):
    detection.print_summary(detection.detect(a.folder), _log)


def cmd_scan(a):
    folder = os.path.abspath(a.folder)
    _log("Engine:")
    outcome = detection.detect(folder)
    detection.print_summary(outcome, lambda m: _log("  " + m))
    _log()
    candidates, others = scan.scan_folder(folder, _log)
    scan.print_summary(candidates, others, _log, detail=a.detail)
    existing = automatic.find_profile(folder)
    if a.name:
        name = slug(a.name)
    elif existing:
        name = os.path.splitext(os.path.basename(existing))[0]
    else:
        name = automatic.name_for_folder(folder)
    _, web = automatic.game_supported(outcome)
    profile = scan.create_profile(name, folder, candidates, a.src_lang, a.a, a.backend or default(),
                                     outcome["web_roots"] if web else (), engine=outcome.get("main_engine"),
                                     log=_log)
    if not profile["file"]:
        _log("\nNo file above threshold: profile not created.")
        return 1
    path = os.path.join(automatic.profiles_folder(), name + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            registered = legacy.load(f).get("game_folder", "")
        if os.path.normcase(os.path.abspath(registered)) != os.path.normcase(folder):
            _log("\nThe name '%s' is already used for another game (%s): choose another one with --name."
                 % (name, registered))
            return 1
        if not a.overwrite:
            _log("\nProfile %s already exists (use --overwrite to regenerate it)." % path)
            return 0
    scan.save_json(path, profile)
    _log("\nProfile created: %s" % path)
    for f in profile["file"]:
        if f.get("format") == "web-ui":
            _log("  %s: Interface (Menu)" % f["path"])
        elif f.get("format") == "csv":
            _log("  %s: Text table, column %s" % (f["path"], f.get("column")))
        else:
            _log("  %s: %d patterns included, %d patterns excluded" % (f["path"], len(f["include"]),
                                                                        len(f["exclude"])))
    _log("Check it (optional), then:  python -I smith.py translate %s" % name)
    return 0


def _engine_profile(path, engine):
    with open(path, encoding="utf-8") as f:
        profile = legacy.load(f)
    return profile if profile.get("engine") == engine else None


def _unity_profile(path):
    """The game profile if it belongs to a Unity game (translated by XUnity), otherwise None."""
    return _engine_profile(path, "unity")


def _unreal_profile(path):
    return _engine_profile(path, "unreal")


def _xna_profile(path):
    return _engine_profile(path, "xna")


def cmd_translate(a):
    path = _profile(a.profile)
    with open(path, encoding="utf-8") as f:
        profile_backend = legacy.load(f).get("backend", "google")
    unity = _unity_profile(path)
    if unity:  # installs/updates XUnity and pre-translates the game's texts
        return 0 if automatic.unity_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    if _unreal_profile(path):
        return 0 if automatic.unreal_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    if _xna_profile(path):
        return 0 if automatic.xna_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    with open(path, encoding="utf-8") as f:
        backend_name = a.backend or legacy.load(f).get("backend", "google")
    p = Project(path, _log, test=(backend_name == "fake"))
    return 0 if p.translate(create_backend(backend_name, _log)) else 2


def cmd_apply(a):
    path = _profile(a.profile)
    if _unity_profile(path):  # for Unity: components already downloaded and translations in memory, no network
        return 0 if automatic.unity_full(path, _log, network=False) else 2
    if _unreal_profile(path):
        return 0 if automatic.unreal_full(path, _log, network=False,
                                               backend="fake" if a.test else "google") else 2
    if _xna_profile(path):
        return 0 if automatic.xna_full(path, _log, network=False, backend="fake" if a.test else "google") else 2
    return 0 if Project(path, _log, test=a.test).apply() else 2


def cmd_restore(a):
    if os.path.isdir(a.profile):  # game folder: everything, including the language set by PatchSmith
        automatic.restore(a.profile, _log)
        return 0
    path = _profile(a.profile)
    with open(path, encoding="utf-8") as f:
        folder = legacy.load(f).get("game_folder")
    if folder and os.path.isdir(folder):  # everything, including the language set or added by PatchSmith
        automatic.restore(folder, _log)
        return 0
    Project(path, _log).restore()


def cmd_status(a):
    path = _profile(a.profile)
    unity = _unity_profile(path)
    if unity:
        adapter_unity.state(unity["game_folder"], unity["name"], unity["target_language"], _log)
        return 0
    unreal = _unreal_profile(path)
    if unreal:
        adapter_unreal.state(unreal["game_folder"], _log)
        return 0
    xna = _xna_profile(path)
    if xna:
        adapter_xna.state(xna["game_folder"], _log)
        return 0
    Project(path, _log).show_status(a.examples)


def cmd_auto(a):
    path, completed, outcome = automatic.analyze_and_translate(
        a.folder, a.src_lang, a.a, a.backend or default(), _log, force=a.force, regenerate=a.regenerate,
        translate_anyway=a.translate_anyway, consent=a.modify_executables)
    if not path and outcome.get("experimental_possible"):
        _log("To try anyway: add --force")
    if not path and outcome.get("needs_consent"):
        _log("To activate it: repeat with --modify-executables (the consent then stays valid for this game)")
    elif not path and outcome.get("official_language"):
        if a.modify_executables and outcome.get("language_set") is False:
            return 2  # activation requested but failed (the reason has already been written)
        if outcome.get("engine_supported", True):
            _log("For a machine translation anyway: add --translate-anyway")
        return 0  # the official language has been handled: not an error
    return 0 if completed else 2


def cmd_check(a):
    """After-play check (checks.py) of one game, or of all the games with the translation installed.
    Exit code: 0 if nothing is left to do, 2 if something is left (including a fix postponed because the game was
    open, or not made because of --dry-run), 1 if the check of a game failed."""
    from . import checks
    if a.folder:
        if os.path.isdir(a.folder):
            path = automatic.find_profile(a.folder)
            if not path:
                _log("No PatchSmith translation found for this folder: nothing to check.")
                return 0
        else:
            path = _profile(a.folder)
        paths = [path]
    else:
        paths = [p for p, profile in automatic.list_profiles()  # (not the test profiles: fake translator)
                    if profile.get("game_folder") and os.path.isdir(profile["game_folder"])
                    and profile.get("backend") != "fake"]
    left_to_do = errors = False
    for path in paths:
        try:
            problems = checks.check(path, apply=not a.dry_run, log=_log, translator=a.backend)
        except Exception as e:  # noqa: BLE001 - a broken game profile does not stop the check of the other games
            _log("ERROR while checking %s (%s: %s)" % (os.path.basename(path), type(e).__name__, e))
            errors = True
            continue
        left_to_do = left_to_do or any(p.state in checks.TO_DO + ("postponed", "to_fix") for p in problems)
        _log()
    if errors:
        return 1
    if a.dry_run:
        _log("(read-only: no changes made)")
    return 2 if left_to_do else 0


def cmd_services(a):
    """Status of the online translators (pauses after a block) and, with --reset, reactivation of one."""
    from .limiter import NAMES, Limiter
    if a.reset:
        Limiter(a.reset, _log).reset()
        _log("%s: Reactivated. Please note: if the service continues to reject requests, persistent attempts may "
             "prolong the block." % NAMES.get(a.reset, a.reset))
        return 0
    for service in ("google", "deepl", "mymemory"):
        l = Limiter(service, _log)
        _log("%-9s %s" % (NAMES[service] + ":", l.pause() or "available"))
        block = l.current_block()
        if block and block.get("disabled"):
            _log("          to reactivate it: python -I smith.py services --reset %s" % service)
    return 0


def cmd_xunity_service(a):
    """Translator of new lines for XUnity (started by the PatchSmith plugin when the game starts, without a
    window): writes to components/ollama/servizio-xunity.log; the requested lines go into the journal
    memory/<gioco>.requests.jsonl."""
    import datetime
    from . import ollama, xunity_service
    log_file = os.path.join(ollama.folder(), "servizio-xunity.log")
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    try:
        if os.path.getsize(log_file) > 2_000_000:
            os.replace(log_file, log_file + ".1")
    except OSError:
        pass
    with open(log_file, "a", encoding="utf-8") as f:
        def log(message=""):
            f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), message))
            f.flush()
            if a.show and sys.stdout:
                _log(message)
        return xunity_service.Service(_profile(a.profile), a.port, a.pid, log).launch()


def cmd_local(a):
    """Status of the local translator and, with --install, installation (downloads: they only start this way)."""
    from . import ollama
    if a.install:
        lines = ollama.download_description(a.install)
        if not lines:
            _log("Already installed.")
            return 0
        _log("Downloading to %s:\n- %s" % (ollama.folder(), "\n- ".join(lines)))
        try:
            ollama.install(a.install, _log)
        except ollama.LocalError as e:
            _log("Installation failed: %s" % e)
            return 2
        from .backend import probe_local
        _log("Testing the local translator: I'll translate \"Start game\"...")
        test = probe_local(a.install[0], _log)
        _log("Local translator: %s" % test["message"])
        if not test["ok"]:
            return 2
    _log("Local translator: %s (in %s)" % ({"ready": "ready"}.get(ollama.state(), ollama.state()), ollama.folder()))
    for k, m in ollama.MODELS.items():
        _log("  %-4s %-30s %s" % (k, m["label"], "downloaded" if ollama.model_present(k) else
                                  "to download (%.1f GB)" % m["gb"]))
    if ollama.state() != "ready":
        key, reason = ollama.recommended_model()
        _log(reason)
        _log("To install it: python -I smith.py local --install %s (or %s, or both)"
             % (key, "4b" if key == "12b" else "12b"))
    return 0


def cmd_interface(a):
    """Interface languages: list, or --create <codice> to translate the program's texts with the local translator
    (those already in the file stay: fixes made by hand are not lost)."""
    if not a.create:
        for code, info in ui.languages().items():
            _log("%s  %s" % (code, info["name"]))
        return 0
    from .backend import Local
    flag = legacy.upgrade(json.loads(a.flag)) if a.flag else {}
    translator = Local(a.model, log=_log)
    try:
        n = ui.create_language(a.create, a.name or a.create, translator, _log, flag)
    finally:
        translator.close()  # frees the graphics card and closes the translator's server
    _log("Added %d texts to patchsmith/ui_language/%s.json: review them by hand." % (n, a.create))
    return 0


def cmd_package(a):
    """The package to publish: folder and zip with PatchSmith.exe and the portable Python (see package.py)."""
    from . import package
    try:
        package.prepare(a.output, a.python, _log)
    except package.PackageError as e:
        _log("ERROR: %s" % e)
        return 1
    return 0


def cmd_gui(a):
    from .gui import main as launch
    launch()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    project.migrate_data()  # data with the former Italian names (game profiles, memory...) move to the English ones
    _ui_lang()
    ap = argparse.ArgumentParser(
        prog="smith",
        description="PatchSmith %s: Translates game text directly into game files." % __version__)
    sub = ap.add_subparsers(dest="command", required=True)
    # commands and options in English; the former Italian names stay as aliases (dest: the former name)
    game_language = dict(dest="src_lang", metavar="LANG", default="auto", type=language.code,
                        help="game language (default: auto = detected)")
    target_language = dict(dest="a", metavar="LANG", default=language.windows_language(), type=language.code,
                         help="target language (default: the language of Windows)")

    s = sub.add_parser("auto", help="everything is automatic: it detects, finds the text, translates, and applies it")
    s.add_argument("folder", metavar="folder")
    s.add_argument("--from", "--da", **game_language)
    s.add_argument("--to", "--a", **target_language)
    s.add_argument("--backend", choices=sorted(BACKEND), type=_backend_name,
                   help="translator (default: local; if not installed, it will tell you how to install it)")
    s.add_argument("--force", "--forza", dest="force", action="store_true",
                   help="try even if the engine isn't supported yet")
    s.add_argument("--regenerate", "--rigenera", dest="regenerate", action="store_true",
                   help="recreate the profile from scratch")
    s.add_argument("--translate-anyway", "--traduci-comunque", dest="translate_anyway", action="store_true",
                   help="translates even if the game already officially has the target language")
    s.add_argument("--modify-executables", "--modifica-eseguibili", dest="modify_executables", action="store_true",
                   help="allows you to modify the game's executables when needed to activate its official language "
                        "(with a backup; the consent remains valid for that game)")
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("check", aliases=["controlla"],
                       help="post-game check: crash, new phrase translator, files re-installed by the game; "
                            "automatically fixes what is safe")
    s.add_argument("folder", nargs="?", metavar="folder",
                   help="game folder or profile name (default: all games)")
    s.add_argument("--dry-run", "--solo-leggi", dest="dry_run", action="store_true",
                   help="says what it would do, without fixing or writing anything")
    s.add_argument("--backend", choices=sorted(BACKEND), type=_backend_name,
                   help="translator for phrases left untranslated while you were playing (default: the game's)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("services", aliases=["servizi"], help="status of the online translators (pauses after a block)")
    s.add_argument("--reset", "--azzera", dest="reset", choices=["google", "deepl", "mymemory"],
                   help="reactivate a paused translator")
    s.set_defaults(fn=cmd_services)

    s = sub.add_parser("local", aliases=["locale"],
                       help="local translator (Ollama + TranslateGemma): status and installation")
    s.add_argument("--install", "--installa", dest="install", nargs="+", choices=["4b", "12b"],
                   help="download and install Ollama and the chosen models (4b: 3.3 GB, 12b: 8.1 GB)")
    s.set_defaults(fn=cmd_local)

    s = sub.add_parser("xunity-service", aliases=["servizio-xunity"],
                       help="new phrase translator for XUnity while playing (started by the game)")
    s.add_argument("--profile", "--profilo", dest="profile", metavar="PROFILE", required=True,
                   help="game profile name or path")
    s.add_argument("--port", "--porta", dest="port", metavar="PORT", type=int,
                   help="port on 127.0.0.1 (default: the game's)")
    s.add_argument("--pid", type=int, help="game process: when you close the game, the service will stop")
    s.add_argument("--show", "--mostra", dest="show", action="store_true", help="also writes the log here")
    s.set_defaults(fn=cmd_xunity_service)

    s = sub.add_parser("interface", aliases=["interfaccia"],
                       help="interface languages: list, or --create to add one")
    s.add_argument("--create", "--crea", dest="create", metavar="CODE",
                   help="create or complete the language (e.g., en, fr) using the local translator")
    s.add_argument("--name", "--nome", dest="name", metavar="NAME",
                   help="language name within the language itself (e.g., English, Français)")
    s.add_argument("--model", "--modello", dest="model", default="4b", choices=["4b", "12b"],
                   help="local translator model")
    s.add_argument("--flag", "--bandiera", dest="flag", metavar="JSON",
                   help='flag in JSON, e.g. {"vertical": ["#0055a4", "#ffffff", "#ef4135"]}')
    s.set_defaults(fn=cmd_interface)

    s = sub.add_parser("package", aliases=["pacchetto"],
                       help="prepare the package for publishing (a ZIP file containing PatchSmith.exe and the portable "
                            "Python distribution)")
    s.add_argument("--output", "--uscita", dest="output", metavar="FOLDER",
                   help="package folder (default: PatchSmith-rilascio next to the program)")
    s.add_argument("--python", metavar="FOLDER",
                   help="folder of an installed Python to use (default: the current one)")
    s.set_defaults(fn=cmd_package)

    s = sub.add_parser("gui", help="opens the graphical interface")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("detect", aliases=["rileva"], help="detects the game engine")
    s.add_argument("folder", metavar="folder")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("scan", aliases=["scansiona"], help="finds the files with text and creates the game profile")
    s.add_argument("folder", metavar="folder")
    s.add_argument("--name", "--nome", dest="name", metavar="NAME",
                   help="profile name (default: folder name)")
    s.add_argument("--from", "--da", **game_language)
    s.add_argument("--to", "--a", **target_language)
    s.add_argument("--backend", choices=sorted(BACKEND), type=_backend_name,
                   help="translator (default: local; if not installed, it will tell you how to install it)")
    s.add_argument("--detail", "--dettaglio", dest="detail", metavar="FILE",
                   help="display the text patterns of a file (relative path)")
    s.add_argument("--overwrite", "--sovrascrivi", dest="overwrite", action="store_true",
                   help="regenerates the profile if it exists")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("translate", aliases=["traduci"],
                       help="translates the missing texts and writes them into the game (with backup)")
    s.add_argument("profile", metavar="profile", help="profile name or path")
    s.add_argument("--backend", choices=sorted(BACKEND), type=_backend_name, help="replaces the profile backend")
    s.set_defaults(fn=cmd_translate)

    s = sub.add_parser("apply", aliases=["applica"], help="rewrites the saved translations into the game (no network)")
    s.add_argument("profile", metavar="profile")
    s.add_argument("--test", "--prova", dest="test", action="store_true",
                   help="uses the test translation memory")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("restore", aliases=["ripristina"],
                       help="restores the original game files (and the language, if set by PatchSmith)")
    s.add_argument("profile", metavar="profile", help="profile or game folder")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("status", aliases=["stato"], help="show how many texts have been translated and some examples")
    s.add_argument("profile", metavar="profile")
    s.add_argument("--examples", "--esempi", dest="examples", metavar="N", type=int, default=8)
    s.set_defaults(fn=cmd_status)

    _translate_help(ap)
    a = ap.parse_args(argv)
    try:
        return a.fn(a) or 0
    except (adapter_unity.UnityError, adapter_unreal.UnrealError, adapter_xna.XnaError, components.ComponentError,
            dotnet.DotnetError, xnb.XnbError, BackendError,
            RuntimeError, ValueError) as e:
        _log("ERROR: %s" % e)
        if getattr(e, "command", None):  # the messages talk about the interface buttons: here the command
            _log("From the command line: %s" % e.command)
        return 1
