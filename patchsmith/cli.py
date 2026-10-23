"""Command-line interface."""
import argparse
import json
import os
import sys

from . import (__version__, adapter_unity, adapter_unreal, adapter_xna, auto as automatic, language,
               detection, scan)
from . import components, dotnet, project, ui_language as ui, xnb
from .backend import BACKEND, BackendError, create_backend, default
from .project import Project
from .text import DETAIL, readable, slug


def _log(message=""):
    print(ui.translate(readable(str(message)).replace(DETAIL, "")), flush=True)


def _ui_lang():
    """The window's language (settings.json, otherwise the Windows one): also for the help and the messages here."""
    try:
        with open(project.data_folder("settings.json"), encoding="utf-8") as f:
            choice = json.load(f).get("interfaccia")
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
        raise SystemExit("Serve un profilo, non una cartella: %s" % value_)
    name = os.path.splitext(os.path.basename(value_))[0] if value_.lower().endswith(".json") else value_
    candidate = os.path.join(automatic.profiles_folder(), name + ".json")
    if os.path.exists(candidate):
        return candidate
    raise SystemExit("Profilo non trovato: %s" % value_)


def cmd_detect(a):
    detection.print_summary(detection.detect(a.folder), _log)


def cmd_scan(a):
    folder = os.path.abspath(a.folder)
    _log("Motore:")
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
                                     outcome["radici_web"] if web else (), engine=outcome.get("motore_principale"),
                                     log=_log)
    if not profile["file"]:
        _log("\nNessun file sopra soglia: profilo non creato.")
        return 1
    path = os.path.join(automatic.profiles_folder(), name + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            registered = json.load(f).get("cartella_gioco", "")
        if os.path.normcase(os.path.abspath(registered)) != os.path.normcase(folder):
            _log("\nIl nome '%s' e' gia' usato per un altro gioco (%s): scegline un altro con --name."
                 % (name, registered))
            return 1
        if not a.sovrascrivi:
            _log("\nIl profilo %s esiste gia' (usa --overwrite per rigenerarlo)." % path)
            return 0
    scan.save_json(path, profile)
    _log("\nProfilo creato: %s" % path)
    for f in profile["file"]:
        if f.get("formato") == "web-ui":
            _log("  %s: interfaccia (menu)" % f["percorso"])
        elif f.get("formato") == "csv":
            _log("  %s: tabella dei testi, colonna %s" % (f["percorso"], f.get("colonna")))
        else:
            _log("  %s: %d schemi inclusi, %d esclusi" % (f["percorso"], len(f["includi"]), len(f["escludi"])))
    _log("Controllalo (facoltativo), poi:  python -I smith.py translate %s" % name)
    return 0


def _engine_profile(path, engine):
    with open(path, encoding="utf-8") as f:
        profile = json.load(f)
    return profile if profile.get("motore") == engine else None


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
        profile_backend = json.load(f).get("backend", "google")
    unity = _unity_profile(path)
    if unity:  # installs/updates XUnity and pre-translates the game's texts
        return 0 if automatic.unity_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    if _unreal_profile(path):
        return 0 if automatic.unreal_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    if _xna_profile(path):
        return 0 if automatic.xna_full(path, _log, network=True, backend=a.backend or profile_backend) else 2
    with open(path, encoding="utf-8") as f:
        backend_name = a.backend or json.load(f).get("backend", "google")
    p = Project(path, _log, test=(backend_name == "finto"))
    return 0 if p.translate(create_backend(backend_name, _log)) else 2


def cmd_apply(a):
    path = _profile(a.profile)
    if _unity_profile(path):  # for Unity: components already downloaded and translations in memory, no network
        return 0 if automatic.unity_full(path, _log, network=False) else 2
    if _unreal_profile(path):
        return 0 if automatic.unreal_full(path, _log, network=False,
                                               backend="finto" if a.test else "google") else 2
    if _xna_profile(path):
        return 0 if automatic.xna_full(path, _log, network=False, backend="finto" if a.test else "google") else 2
    return 0 if Project(path, _log, test=a.test).apply() else 2


def cmd_restore(a):
    if os.path.isdir(a.profile):  # game folder: everything, including the language set by PatchSmith
        automatic.restore(a.profile, _log)
        return 0
    path = _profile(a.profile)
    with open(path, encoding="utf-8") as f:
        folder = json.load(f).get("cartella_gioco")
    if folder and os.path.isdir(folder):  # everything, including the language set or added by PatchSmith
        automatic.restore(folder, _log)
        return 0
    Project(path, _log).restore()


def cmd_status(a):
    path = _profile(a.profile)
    unity = _unity_profile(path)
    if unity:
        adapter_unity.state(unity["cartella_gioco"], unity["nome"], unity["lingua_destinazione"], _log)
        return 0
    unreal = _unreal_profile(path)
    if unreal:
        adapter_unreal.state(unreal["cartella_gioco"], _log)
        return 0
    xna = _xna_profile(path)
    if xna:
        adapter_xna.state(xna["cartella_gioco"], _log)
        return 0
    Project(path, _log).show_status(a.examples)


def cmd_auto(a):
    path, completed, outcome = automatic.analyze_and_translate(
        a.folder, a.src_lang, a.a, a.backend or default(), _log, force=a.force, regenerate=a.regenerate,
        translate_anyway=a.translate_anyway, consent=a.modifica_eseguibili)
    if not path and outcome.get("sperimentale_possibile"):
        _log("Per provarci comunque: aggiungi --force")
    if not path and outcome.get("serve_consenso"):
        _log("Per attivarla: ripeti con --modify-executables (il consenso vale poi per questo gioco)")
    elif not path and outcome.get("lingua_ufficiale"):
        if a.modifica_eseguibili and outcome.get("lingua_impostata") is False:
            return 2  # activation requested but failed (the reason has already been written)
        if outcome.get("motore_supportato", True):
            _log("Per una traduzione automatica comunque: aggiungi --translate-anyway")
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
                _log("Nessuna traduzione di PatchSmith registrata per questa cartella: niente da controllare.")
                return 0
        else:
            path = _profile(a.folder)
        paths = [path]
    else:
        paths = [p for p, profile in automatic.list_profiles()  # (not the test profiles: fake translator)
                    if profile.get("cartella_gioco") and os.path.isdir(profile["cartella_gioco"])
                    and profile.get("backend") != "finto"]
    left_to_do = errors = False
    for path in paths:
        try:
            problems = checks.check(path, apply=not a.dry_run, log=_log, translator=a.backend)
        except Exception as e:  # noqa: BLE001 - a broken game profile does not stop the check of the other games
            _log("ERRORE nel controllo di %s (%s: %s)" % (os.path.basename(path), type(e).__name__, e))
            errors = True
            continue
        left_to_do = left_to_do or any(p.state in checks.TO_DO + ("rimandato", "da_correggere") for p in problems)
        _log()
    if errors:
        return 1
    if a.dry_run:
        _log("(sola lettura: nessuna correzione fatta)")
    return 2 if left_to_do else 0


def cmd_services(a):
    """Status of the online translators (pauses after a block) and, with --reset, reactivation of one."""
    from .limiter import NAMES, Limiter
    if a.reset:
        Limiter(a.reset, _log).reset()
        _log("%s: riattivato. Attenzione: se il servizio rifiuta ancora le richieste, insistere puo' allungare "
             "il blocco." % NAMES.get(a.reset, a.reset))
        return 0
    for service in ("google", "deepl", "mymemory"):
        l = Limiter(service, _log)
        _log("%-9s %s" % (NAMES[service] + ":", l.pause() or "disponibile"))
        block = l.current_block()
        if block and block.get("disattivato"):
            _log("          per riattivarlo: python -I smith.py services --reset %s" % service)
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
            if a.mostra and sys.stdout:
                _log(message)
        return xunity_service.Service(_profile(a.profile), a.port, a.pid, log).launch()


def cmd_local(a):
    """Status of the local translator and, with --install, installation (downloads: they only start this way)."""
    from . import ollama
    if a.install:
        lines = ollama.download_description(a.install)
        if not lines:
            _log("Gia' installato.")
            return 0
        _log("Scarico in %s:\n- %s" % (ollama.folder(), "\n- ".join(lines)))
        try:
            ollama.install(a.install, _log)
        except ollama.LocalError as e:
            _log("Installazione non riuscita: %s" % e)
            return 2
        from .backend import probe_local
        _log("Prova del traduttore locale: traduco \"Start game\"...")
        test = probe_local(a.install[0], _log)
        _log("Traduttore locale: %s" % test["messaggio"])
        if not test["ok"]:
            return 2
    _log("Traduttore locale: %s (in %s)" % (ollama.state(), ollama.folder()))
    for k, m in ollama.MODELS.items():
        _log("  %-4s %-30s %s" % (k, m["etichetta"], "scaricato" if ollama.model_present(k) else
                                  "da scaricare (%.1f GB)" % m["gb"]))
    if ollama.state() != "pronto":
        key, reason = ollama.recommended_model()
        _log(reason)
        _log("Per installarlo: python -I smith.py local --install %s (oppure %s, o entrambi)"
             % (key, "4b" if key == "12b" else "12b"))
    return 0


def cmd_interface(a):
    """Interface languages: list, or --create <codice> to translate the program's texts with the local translator
    (those already in the file stay: fixes made by hand are not lost)."""
    if not a.create:
        for code, info in ui.languages().items():
            _log("%s  %s" % (code, info["nome"]))
        return 0
    from .backend import Local
    flag = json.loads(a.flag) if a.flag else {}
    translator = Local(a.model, log=_log)
    try:
        n = ui.create_language(a.create, a.name or a.create, translator, _log, flag)
    finally:
        translator.close()  # frees the graphics card and closes the translator's server
    _log("Aggiunti %d testi in patchsmith/ui_language/%s.json: rivedili a mano." % (n, a.create))
    return 0


def cmd_package(a):
    """The package to publish: folder and zip with PatchSmith.exe and the portable Python (see package.py)."""
    from . import package
    try:
        package.prepare(a.output, a.python, _log)
    except package.PackageError as e:
        _log("ERRORE: %s" % e)
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
        description="PatchSmith %s: traduce i testi dei giochi direttamente nei file del gioco." % __version__)
    sub = ap.add_subparsers(dest="command", required=True)
    # commands and options in English; the former Italian names stay as aliases (dest: the former name)
    game_language = dict(dest="src_lang", metavar="LANG", default="auto", type=language.code,
                        help="lingua del gioco (predefinita: auto = rilevata)")
    target_language = dict(dest="a", metavar="LANG", default=language.windows_language(), type=language.code,
                         help="lingua di destinazione (predefinita: quella di Windows)")

    s = sub.add_parser("auto", help="tutto in automatico: rileva, trova i testi, traduce e applica")
    s.add_argument("folder", metavar="folder")
    s.add_argument("--from", "--da", **game_language)
    s.add_argument("--to", "--a", **target_language)
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore (predefinito: locale; se non e' installato, dice come installarlo)")
    s.add_argument("--force", "--forza", dest="force", action="store_true",
                   help="prova anche se il motore non e' ancora supportato")
    s.add_argument("--regenerate", "--rigenera", dest="regenerate", action="store_true",
                   help="ricrea il profilo da zero")
    s.add_argument("--translate-anyway", "--traduci-comunque", dest="translate_anyway", action="store_true",
                   help="traduce anche se il gioco ha gia' la lingua di arrivo ufficiale")
    s.add_argument("--modify-executables", "--modifica-eseguibili", dest="modifica_eseguibili", action="store_true",
                   help="consente di modificare gli eseguibili del gioco quando serve per attivare la sua lingua "
                        "ufficiale (con backup; il consenso resta valido per quel gioco)")
    s.set_defaults(fn=cmd_auto)

    s = sub.add_parser("check", aliases=["controlla"],
                       help="controllo dopo aver giocato: crash, traduttore delle frasi nuove, file rimessi dal gioco; "
                            "corregge da solo quello che e' sicuro")
    s.add_argument("folder", nargs="?", metavar="folder",
                   help="cartella del gioco o nome del profilo (predefinito: tutti i giochi)")
    s.add_argument("--dry-run", "--solo-leggi", dest="dry_run", action="store_true",
                   help="dice cosa farebbe, senza correggere ne' scrivere nulla")
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore per le frasi rimaste in originale mentre giocavi (predefinito: quello del gioco)")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("services", aliases=["servizi"], help="stato dei traduttori online (pause dopo un blocco)")
    s.add_argument("--reset", "--azzera", dest="reset", choices=["google", "deepl", "mymemory"],
                   help="riattiva un traduttore in pausa")
    s.set_defaults(fn=cmd_services)

    s = sub.add_parser("local", aliases=["locale"],
                       help="traduttore locale (Ollama + TranslateGemma): stato e installazione")
    s.add_argument("--install", "--installa", dest="install", nargs="+", choices=["4b", "12b"],
                   help="scarica e installa Ollama e i modelli scelti (4b: 3,3 GB, 12b: 8,1 GB)")
    s.set_defaults(fn=cmd_local)

    s = sub.add_parser("xunity-service", aliases=["servizio-xunity"],
                       help="traduttore delle frasi nuove per XUnity mentre si gioca (lo avvia il gioco)")
    s.add_argument("--profile", "--profilo", dest="profile", metavar="PROFILE", required=True,
                   help="nome o percorso del profilo del gioco")
    s.add_argument("--port", "--porta", dest="port", metavar="PORT", type=int,
                   help="porta su 127.0.0.1 (predefinita: quella del gioco)")
    s.add_argument("--pid", type=int, help="processo del gioco: alla sua chiusura il servizio si ferma")
    s.add_argument("--show", "--mostra", dest="mostra", action="store_true", help="scrive il registro anche qui")
    s.set_defaults(fn=cmd_xunity_service)

    s = sub.add_parser("interface", aliases=["interfaccia"],
                       help="lingue dell'interfaccia: elenco, o --create per aggiungerne una")
    s.add_argument("--create", "--crea", dest="create", metavar="CODE",
                   help="crea o completa la lingua (es. en, fr) con il traduttore locale")
    s.add_argument("--name", "--nome", dest="name", metavar="NAME",
                   help="nome della lingua nella lingua stessa (es. English, Français)")
    s.add_argument("--model", "--modello", dest="model", default="4b", choices=["4b", "12b"],
                   help="modello del traduttore locale")
    s.add_argument("--flag", "--bandiera", dest="flag", metavar="JSON",
                   help='bandiera in JSON, es. {"verticali": ["#0055a4", "#ffffff", "#ef4135"]}')
    s.set_defaults(fn=cmd_interface)

    s = sub.add_parser("package", aliases=["pacchetto"],
                       help="prepara il pacchetto da pubblicare (zip con PatchSmith.exe e Python portatile)")
    s.add_argument("--output", "--uscita", dest="output", metavar="FOLDER",
                   help="cartella del pacchetto (per difetto PatchSmith-rilascio accanto al programma)")
    s.add_argument("--python", metavar="FOLDER",
                   help="cartella di un Python installato da usare (per difetto quello attuale)")
    s.set_defaults(fn=cmd_package)

    s = sub.add_parser("gui", help="apre l'interfaccia grafica")
    s.set_defaults(fn=cmd_gui)

    s = sub.add_parser("detect", aliases=["rileva"], help="riconosce il motore del gioco")
    s.add_argument("folder", metavar="folder")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("scan", aliases=["scansiona"], help="trova i file con testo e crea il profilo del gioco")
    s.add_argument("folder", metavar="folder")
    s.add_argument("--name", "--nome", dest="name", metavar="NAME",
                   help="nome del profilo (predefinito: nome della cartella)")
    s.add_argument("--from", "--da", **game_language)
    s.add_argument("--to", "--a", **target_language)
    s.add_argument("--backend", choices=sorted(BACKEND),
                   help="traduttore (predefinito: locale; se non e' installato, dice come installarlo)")
    s.add_argument("--detail", "--dettaglio", dest="detail", metavar="FILE",
                   help="mostra gli schemi di testo di un file (percorso relativo)")
    s.add_argument("--overwrite", "--sovrascrivi", dest="sovrascrivi", action="store_true",
                   help="rigenera il profilo se esiste")
    s.set_defaults(fn=cmd_scan)

    s = sub.add_parser("translate", aliases=["traduci"],
                       help="traduce i testi mancanti e li scrive nel gioco (con backup)")
    s.add_argument("profile", metavar="profile", help="nome o percorso del profilo")
    s.add_argument("--backend", choices=sorted(BACKEND), help="sostituisce il backend del profilo")
    s.set_defaults(fn=cmd_translate)

    s = sub.add_parser("apply", aliases=["applica"], help="riscrive nel gioco le traduzioni in memoria (senza rete)")
    s.add_argument("profile", metavar="profile")
    s.add_argument("--test", "--prova", dest="test", action="store_true",
                   help="usa la memoria delle traduzioni di prova")
    s.set_defaults(fn=cmd_apply)

    s = sub.add_parser("restore", aliases=["ripristina"],
                       help="rimette i file originali del gioco (e la lingua, se impostata da PatchSmith)")
    s.add_argument("profile", metavar="profile", help="profilo oppure cartella del gioco")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("status", aliases=["stato"], help="mostra quanti testi sono tradotti e qualche esempio")
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
        _log("ERRORE: %s" % e)
        if getattr(e, "command", None):  # the messages talk about the interface buttons: here the command
            _log("Da riga di comando: %s" % e.command)
        return 1
