"""Automatic procedure: from the game folder to the applied translation, with no manual steps.

Used by the graphical interface and by the `tn auto` command.
"""
import collections
import datetime
import glob
import json
import os
import re
import shutil
import struct

from . import (adapter_gamemaker, adapter_unity, adapter_unreal, adapter_xna, gamemaker, language,
               official_language)
from . import (adapter_csv, adapter_godot, adapter_unity_loc, adapter_web_live, adapter_webjson,
               configurator, shared_file, interruption,
               names)
from . import language_unreal, detection, scan, system
from .backend import create_backend
from .outcome import LIVE, set_ as _mark
from .project import BACKUP_SUFFIX, Project, data_folder, read_info, memory_path, restore_file
from .project import find_info
from .text import DETAIL, is_readable_text, slug

PROFILES_FOLDER = None  # None = profiles/ among the user's data (project.data_folder); the tests change it
WEB_ENGINES = {"Web", "Electron", "NWJS"}
# Folder names too generic to serve as the game profile's name: the parent folder is used.
GENERIC_NAMES = {"game", "games", "gioco", "bin", "bin64", "win64", "win32", "x64", "x86", "app", "data",
                 "release", "build", "windows", "windowsnoeditor", "binaries", "resources", "content"}
# reasons of the outcomes (outcome.py), in words for the player
REASON_ANTICHEAT = ("Il gioco ha un sistema anti-cheat: aggiungere o cambiare i suoi file potrebbe far bannare "
                    "l'account.")
REASON_IL2CPP = ("E' un gioco Unity compilato in IL2CPP che non usa le tabelle dei testi di Unity Localization: per "
                 "ora PatchSmith sa tradurre solo i giochi Unity Mono e quelli con queste tabelle.")
REASON_ENCRYPTED = ("Il gioco tiene testi e codice in un archivio cifrato (%s), che si apre solo mentre il gioco gira. "
                  "PatchSmith non forza le protezioni messe dagli sviluppatori, quindi questo gioco non si puo' "
                  "tradurre.")
REASON_WEB_LIVE = ("I testi del gioco sono cifrati: li traduce PatchSmith mentre giochi, nella pagina del gioco, "
                   "senza aprire il file cifrato. La prima volta una frase compare in inglese per un attimo, poi in "
                   "italiano; dalla volta dopo e' subito tradotta.")
REASON_CRC = "Il gioco controlla l'impronta (CRC) dei file con i testi: un file modificato non verrebbe caricato."
REASON_BINARY_CATALOG = ("Il gioco elenca i suoi file con i testi in un catalogo binario (catalog.bin, dei giochi "
                           "Unity piu' recenti) che PatchSmith non sa ancora leggere: non posso sapere se un file dei "
                           "testi modificato verrebbe caricato, quindi non modifico nulla.")
REASON_TABLES_WITHOUT_SOURCE = ("Il gioco ha le tabelle dei testi di Unity Localization, ma non nella lingua di "
                                "partenza (%s): le sue lingue sono %s. Scegline una in \"Da:\" e riprova.")
# profilo["dal_vivo"] of Unity games: "servizio" = PatchSmith translates the new sentences while the game is played
# (the plugin starts xunity_service; it is the normal choice); "google" = XUnity asks Google by itself (only written
# by hand)
LIVE_CHOICES = ("servizio", "google", "nessuno")  # nessuno: chosen at the end of "Analyze and translate"


def _normalize(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _inside(child, parent):
    child, parent = _normalize(child), _normalize(parent)
    return child == parent or child.startswith(parent + os.sep)


def profiles_folder():
    return PROFILES_FOLDER or data_folder("profiles")


def list_profiles():
    for path in sorted(glob.glob(os.path.join(glob.escape(profiles_folder()), "*.json"))):
        # <nome>.state.json: fingerprints of versions 0.1-0.2; <nome>.checks.json: the after-play check
        if path.endswith((".stato.json", ".controllo.json")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):  # a damaged file (e.g. a list) is not a game profile: skipped like an unreadable one
            yield path, data


def find_profile(folder):
    """Game profile of the same game: same folder, or a folder that contains it or is contained in it."""
    exact, neighbors = [], []
    for path, profile in list_profiles():
        registered = profile.get("cartella_gioco", "")
        if not registered:
            continue
        if _normalize(registered) == _normalize(folder):
            exact.append(path)
        elif _inside(registered, folder) or _inside(folder, registered):
            neighbors.append(path)
    if exact:
        return exact[0]
    return neighbors[0] if len(neighbors) == 1 else None


def _base_name(folder):
    """The game profile's name for the folder, without the number that tells it apart from existing ones ('-2')."""
    parts = [p for p in os.path.abspath(folder).replace("\\", "/").split("/") if p]
    name = parts[-1] if parts else "gioco"
    for p in reversed(parts):
        if p.lower() not in GENERIC_NAMES:
            name = p
            break
    return slug(name)


def name_for_folder(folder):
    base = _base_name(folder)
    candidate, n = base, 2
    existing_names = {os.path.splitext(os.path.basename(p))[0] for p, _ in list_profiles()}
    while candidate in existing_names:
        candidate, n = "%s-%d" % (base, n), n + 1
    return candidate


# --- the same game in another folder (copied, moved, reinstalled) ----------------------------------------------

# executables that do not tell which game it is (programs common to many games)
_COMMON_EXECUTABLES = {"nw.exe", "electron.exe", "node.exe", "python.exe", "pythonw.exe", "notepad.exe", "elevate.exe",
                      "dotnet.exe", "7z.exe", "7za.exe", "qtwebengineprocess.exe", "cefsharp.browsersubprocess.exe",
                      "steamerrorreporter.exe", "steamerrorreporter64.exe",
                      # .NET published with its runtime (XNA/FNA/MonoGame games), Ren'Py, DOSBox/ScummVM, Squirrel
                      "createdump.exe", "zsync.exe", "zsyncmake.exe", "dosbox.exe", "scummvm.exe", "update.exe",
                      "squirrel.exe"}


def identity(folder, steam=True):
    """What recognizes the same game even in another folder (read-only, fast): {'exe': [lowercase names of the game's
    executables, down to two subfolders], 'unity': 'azienda/prodotto' (Unity's app.info), 'steam': appid (Steam
    manifest, if the game is installed from Steam; with steam=False it is not looked for), 'nome': the folder name as
    for the game profile, without the '-2'}. It stays in the game profile (profilo['identita']): a moved game is
    recognized even when the previous folder is no longer there."""
    from . import language_unity
    from . import steam as steam_module
    output = {"nome": _base_name(folder)}
    try:
        exe = sorted({e.rsplit("/", 1)[-1] for e in steam_module._folder_executables(folder)} - _COMMON_EXECUTABLES)
    except OSError:
        exe = []
    if exe:
        output["exe"] = exe[:20]
    try:
        data = language_unity.data_folder(folder)
        company, product = language_unity.company_product(data) if data else (None, None)
    except (OSError, ValueError):
        company = product = None
    if product:
        output["unity"] = "%s/%s" % (company or "", product)
    if steam:
        try:
            installed = steam_module.game_installed(folder)
        except Exception:  # noqa: BLE001 - Steam missing or unreadable: do without it
            installed = None
        if installed and installed.get("appid"):
            output["steam"] = str(installed["appid"])
    return output


def _without_number(name):
    return re.sub(r"-\d+$", "", name or "")


def same_game(a, b):
    """True if the two identities (see identita) belong to the same game. The surest data both have decides: the Unity
    product, then the Steam appid, then an executable with the same name; without any of these, the folder name. The
    '-2' is removed only from a game profile's name (from_profile: identity of an old game profile, of which only the
    name is known): 'gioco-2' as a folder is usually the sequel, not a copy."""
    for key in ("unity", "steam"):
        if a.get(key) and b.get(key):
            return str(a[key]).lower() == str(b[key]).lower()
    if a.get("exe") and b.get("exe"):
        return bool(set(a["exe"]) & set(b["exe"]))
    x, y = a.get("nome") or "", b.get("nome") or ""
    if not x or not y or "gioco" in (x, y):
        return False
    return (x == y or (bool(b.get("da_profilo")) and _without_number(y) == x)
            or (bool(a.get("da_profilo")) and _without_number(x) == y))


def _count_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, dict) else 0


def same_games(folder, dest_lang=None, test=False):
    """The game profiles of the same game (see same_game) in another folder, with translations saved in the
    'destinazione' language, when this folder does not have a game profile yet: [{'profilo', 'nome', 'cartella',
    'testi', 'spostato'}], first the one with the most recently used translation memory. 'spostato': that game
    profile's folder is no longer there (game moved or reinstalled elsewhere). prova=True: the translation memories of
    the test translator ('finto' backend)."""
    if find_profile(folder):
        return []
    own, output = None, []
    for path, profile in list_profiles():
        registered = profile.get("cartella_gioco")
        if not registered or not profile.get("nome") or not profile.get("lingua_destinazione"):
            continue
        if _inside(registered, folder) or _inside(folder, registered):
            continue
        if dest_lang and not language.same_language(profile["lingua_destinazione"], dest_lang):
            continue
        memory = memory_path(profile, test)
        texts = _count_entries(memory)
        if not texts:
            continue
        moved = not os.path.isdir(registered)
        theirs = profile.get("identita") if isinstance(profile.get("identita"), dict) else None
        if theirs is None:  # game profile from before this version: from its folder, if still there, or from the name
            theirs = identity(registered, steam=False) if not moved else {"nome": profile["nome"], "da_profilo": True}
        if own is None:
            own = identity(folder)
        if not same_game(own, theirs):
            continue
        try:
            date = os.path.getmtime(memory)
        except OSError:
            date = 0
        output.append((date, {"profilo": path, "nome": profile["nome"], "cartella": registered, "testi": texts,
                              "spostato": moved}))
    return [v for _, v in sorted(output, key=lambda x: -x[0])]


def _move_profile(from_profile, folder, log):
    """The game profile's game was moved (its folder is no longer there): the game profile moves to the new folder,
    with the same translation memory. True if it did so."""
    try:
        profile = _read_profile(from_profile)
    except (OSError, ValueError):
        return False
    old_folder = profile.get("cartella_gioco") if isinstance(profile, dict) else None
    if not old_folder or os.path.isdir(old_folder):
        return False
    profile["cartella_gioco"] = os.path.abspath(folder).replace(os.sep, "/")
    profile["cartella_precedente"] = old_folder
    scan.save_json(from_profile, profile)
    log("Stesso gioco di \"%s\", che era in %s: uso il suo profilo e le sue traduzioni in questa cartella."
        % (profile.get("nome"), old_folder))
    return True


def _copy_translations(from_profile, path, log):
    """The saved translations of the same game in another folder (translation memory and machine translations, the
    test ones too, and the copy of the XUnity cache) become those of this game profile, if it has none yet; the game's
    names removed or added by hand (non_tradurre) come along too. Returns the entries copied."""
    if _normalize(from_profile) == _normalize(path):
        return 0
    try:
        old, new = _read_profile(from_profile), _read_profile(path)
        if not language.same_language(old["lingua_destinazione"], new["lingua_destinazione"]):
            return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    copied = 0
    try:
        for test in (False, True):
            source, dest_lang = memory_path(old, test), memory_path(new, test)
            if not os.path.exists(source) or os.path.exists(dest_lang):
                continue
            for tail in (".json", ".automatiche.json"):
                from_file, target_file = source[:-len(".json")] + tail, dest_lang[:-len(".json")] + tail
                if os.path.exists(from_file) and not os.path.exists(target_file):
                    # with the lock and on a temporary name: the other copy's translation memory may be in use
                    # (the open game is updating it) and no half-written file must be left here
                    data = shared_file.read_json(from_file)
                    if isinstance(data, dict):
                        shared_file.save_merging(target_file, dict(data), {})
            copied += _count_entries(dest_lang)
        cache = adapter_unity._memory_folder(old["nome"])
        if os.path.isdir(cache) and not os.path.exists(adapter_unity._memory_folder(new["nome"])):
            shutil.copytree(cache, adapter_unity._memory_folder(new["nome"]))
    except (OSError, ValueError) as e:  # (disk full, damaged file): translate what is missing, as without it
        log("  ! Non sono riuscito a copiare tutte le traduzioni di \"%s\" (%s): traduco quello che manca."
            % (old["nome"], e))
    # the names not to translate: those chosen for the other copy (removed or added by hand) apply here too
    seen = [n for n in old.get("nomi_trovati") or [] if isinstance(n, str)]
    if old.get("non_tradurre") is not None:
        new["non_tradurre"] = list(dict.fromkeys(
            [n for n in old.get("non_tradurre") or [] if isinstance(n, str)] +
            [n for n in new.get("non_tradurre") or [] if isinstance(n, str) and n not in seen]))
        new["nomi_trovati"] = list(dict.fromkeys(seen + [n for n in new.get("nomi_trovati") or []
                                                            if isinstance(n, str)]))
    new["traduzioni_da"] = old["nome"]
    scan.save_json(path, new)
    if copied:
        log("Riuso le traduzioni di \"%s\" (stesso gioco in %s): %d testi gia' tradotti."
            % (old["nome"], old.get("cartella_gioco"), copied))
    else:
        log("  (le traduzioni di \"%s\" non servono: questa cartella ha gia' le sue)" % old["nome"])
    return copied


def game_supported(outcome):
    """(supportato, web).

    web: Electron/NW.js, a web page with no other engines, or RPG Maker MV/MZ (they have an index.html).
    supportato: translatable automatically. RPG Maker MV/MZ stays experimental (confirmation needed):
    its events mix dialogues and plugin commands (often Japanese words used as
    identifiers), which the generic rules cannot tell apart.
    """
    engines = {r["nome"] for r in outcome["risultati"] if r["sezione"] == "Engine"}
    containers = {r["nome"] for r in outcome["risultati"] if r["sezione"] == "Container"}
    sdk = {r["nome"] for r in outcome["risultati"] if r["sezione"] == "SDK"}
    rpg_web = "RPG Maker MV/MZ" in outcome["framework_web"] or "RPGMaker" in engines
    web = bool(containers & {"Electron"} or sdk & {"NWJS"} or rpg_web or engines == {"Web"})
    return web and not rpg_web, web


def unity_engine(outcome):
    """'mono', 'il2cpp' or None if the game is not Unity."""
    if outcome.get("motore_principale") != "Unity":
        return None
    sdk = {r["nome"] for r in outcome["risultati"] if r["sezione"] == "SDK"}
    return "il2cpp" if "UnityIL2CPP" in sdk else "mono"


def unreal_engine(outcome):
    return outcome.get("motore_principale") == "Unreal"


def _prepare_unreal(folder, existing, origin, dest_lang, outcome, log, backend="google"):
    """Game profile for an Unreal game: the .locres files are translated and a patch pak is added."""
    if has_anticheat(outcome):
        log("Questo gioco ha un anti-cheat: non aggiungo pak modificati (rischio di ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="unreal")
    info = adapter_unreal.analyze(folder)
    if not info["paks"]:
        log("Non trovo archivi .pak del gioco.")
        return None, _mark(outcome, "NON_SUPPORTATO", "Non trovo gli archivi .pak con i dati di questo gioco Unreal.",
                            engine="unreal", actions=["crea_rapporto"])
    for problem in info["problemi"]:
        log("  ! " + problem)
    if not info["versioni"]:
        return None, _mark(outcome, "NON_SUPPORTATO", "Gli archivi dei dati del gioco (.pak) sono in un formato che "
                            "PatchSmith non sa ancora leggere (Unreal 5, IoStore o archivi cifrati).", engine="unreal",
                            actions=["crea_rapporto"])
    # the localized texts (.locres) are looked for before creating the game profile: a game without them does not
    # leave an empty game profile, and an unreadable pak does not stop the search in the others
    skipped = []
    sources = adapter_unreal.find_locres(folder, skipped)
    if skipped:
        log("  ! %d archivi non letti (li salto): %s" % (len(skipped), "; ".join(skipped[:3])))
    if not sources:
        log("Nessun file di localizzazione (.locres) negli archivi del gioco che PatchSmith sa leggere.")
        if language_unreal.game_languages(folder)[0] or skipped:
            # the .locres files are there (outside the archives) or might be in the archives that cannot be read
            reason = ("I testi del gioco (.locres) sono in file che PatchSmith non sa ancora tradurre (fuori dagli "
                      "archivi .pak, o in archivi di formato piu' nuovo o cifrati).")
        else:
            reason = ("Il gioco non ha testi localizzati (file .locres): i suoi testi sono dentro gli altri dati del "
                      "gioco, che oggi PatchSmith non sa tradurre.")
        return None, _mark(outcome, "NON_SUPPORTATO", reason, engine="unreal", actions=["crea_rapporto"])
    if existing:
        profile = _read_profile(existing)
        profile.update({"motore": "unreal", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "backend": backend})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
                   "motore": "unreal", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": backend, "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Unreal: traduco i file di localizzazione (.locres) e li metto in un pak di patch aggiuntivo")
    return path, outcome


def unreal_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the .locres files (rete=True: new texts too; False: only from the translation memory) and writes the
    pak. backend="finto": test without network, with the separate test translation memory. The outcome goes into
    esito["esito"]."""
    profile = _read_profile(profile_path)
    folder, a = profile["cartella_gioco"], profile["lingua_destinazione"]
    sources, texts, culture = adapter_unreal.texts_to_translate(folder, log)
    src_lang = profile.get("lingua_origine") or language.AUTO
    if src_lang == language.AUTO:
        base = culture.split("-")[0]
        src_lang = base if base in language.NAMES else culture
        log("  Lingua originale del gioco (dal file .locmeta): %s" % language.label(src_lang))
    if src_lang.split("-")[0] == a.split("-")[0]:
        log("  Il gioco e' gia' in %s: niente da tradurre." % language.label(a))
        _mark(outcome, "NULLA_DA_FARE", _reason_already_in(a), engine="unreal")
        return False
    project = Project(profile_path, log, test=(backend == "finto"))
    failed = project.translate_texts(texts, create_backend(backend, log), src_lang, a) if network else 0
    interruption.check()
    refused = {}
    n = adapter_unreal.install(folder, a, profile["nome"], project.memory, log, refused=refused)
    # translations with characters the game's fonts lack: into problems.json as for the other engines (those written
    # by PatchSmith are redone at the next translation without those characters)
    project._refused = {t: {"motivo": "caratteri che i font del gioco non sanno disegnare: %s" % " ".join(m),
                              "file": adapter_unreal.pak_name(a), "non_usare": m} for t, m in refused.items()}
    project._applied = {t for t in texts if isinstance(project.memory.get(t), str) and t not in refused}
    project._record_applied()
    _write_outcome(outcome, project, texts, bool(n), "unreal")
    return bool(n) and not failed


def xna_engine(outcome):
    return outcome.get("motore_principale") in ("XNA", "FNA", "MonoGame")


def _prepare_xna(folder, existing, origin, dest_lang, outcome, log, backend="google", translate_anyway=False):
    """Game profile for an XNA/FNA/MonoGame game with localized .NET resources. None if it has none at all."""
    if has_anticheat(outcome):
        log("Questo gioco ha un anti-cheat: non aggiungo file (rischio di ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="xna")
    info = adapter_xna.analyze(folder)
    for problem in info["problemi"]:
        log("  ! " + problem)
    if not info["insiemi"]:
        if info["problemi"]:  # there are localized resources, but not usable (e.g. signed assemblies)
            return None, _mark(outcome, "NON_SUPPORTATO", "Le risorse di testo del gioco sono firmate (assembly con "
                                "nome sicuro): PatchSmith non sa ancora tradurle.", engine="xna",
                                actions=["crea_rapporto"])
        log("  Nessuna risorsa .NET localizzata (.resources con satelliti): provo la ricerca generica dei testi.")
        return None
    if existing:
        profile = _read_profile(existing)
        profile.update({"motore": "xna", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "backend": backend, "traduci_comunque": translate_anyway})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
                   "motore": "xna", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": backend, "file": [], "traduci_comunque": translate_anyway}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 .NET: traduco le risorse di testo e le aggiungo come assembly satellite (file del gioco intatti)")
    return path, outcome


def xna_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the .NET resources (rete=True: new texts too; False: only from the translation memory) and writes
    the satellites. The outcome goes into esito["esito"]."""
    profile = _read_profile(profile_path)
    folder, a = profile["cartella_gioco"], profile["lingua_destinazione"]
    resource_sets, texts = adapter_xna.texts_to_translate(folder, log)
    src_lang = profile.get("lingua_origine") or language.AUTO
    if src_lang == language.AUTO:
        src_lang, reason = language.detect(texts)
        log("  Lingua originale del gioco: %s (%s)" % (language.label(src_lang), reason))
    if src_lang.split("-")[0] == a.split("-")[0]:
        log("  Il gioco e' gia' in %s: niente da tradurre." % language.label(a))
        _mark(outcome, "NULLA_DA_FARE", _reason_already_in(a), engine="xna")
        return False
    if not profile.get("traduci_comunque") and a.split("-")[0].lower() in adapter_xna.present_languages(resource_sets):
        log("  Il gioco ha gia' la traduzione ufficiale in %s: sceglila nel menu delle lingue del gioco."
            % language.label(a))
        _mark(outcome, "UFFICIALE", _reason_official(a), engine="xna", actions=["scegli_lingua", "traduci_comunque"])
        return False
    project = Project(profile_path, log, test=(backend == "finto"))
    failed = project.translate_texts(texts, create_backend(backend, log), src_lang, a) if network else 0
    interruption.check()
    n = adapter_xna.install(folder, src_lang, a, profile["nome"], project.memory, log)
    _write_outcome(outcome, project, texts, bool(n), "xna")
    return bool(n) and not failed


def godot_engine(outcome):
    return outcome.get("motore_principale") == "Godot"


def _prepare_godot(folder, existing, origin, dest_lang, outcome, log, backend="google"):
    """Game profile of a Godot 4 game with Godot's translation tables (see adapter_godot). None, None if the game has
    no tables (the generic route of text files is tried)."""
    located = adapter_godot.find_pck(folder)
    if not located:
        return None, None
    try:
        pck = adapter_godot.Pck(located[0])
        all_items = adapter_godot.tables(pck)
    except (adapter_godot.GodotError, OSError, ValueError) as e:
        log("  Godot: non riesco a leggere il pacchetto dei dati (%s)" % e)
        reason = ("Il pacchetto dei dati del gioco (Godot) e' cifrato: PatchSmith non forza le protezioni degli "
                  "sviluppatori." if "cifrat" in str(e) else "Non riesco a leggere il pacchetto dei dati del gioco "
                  "(Godot): %s." % e)
        return None, _mark(outcome, "NON_SUPPORTATO", reason, engine="godot", actions=["crea_rapporto"])
    log(DETAIL + "  Godot %s, pacchetto %s, tabelle delle traduzioni: %s" % (
        pck.version(), os.path.relpath(located[0], folder), ", ".join(sorted(all_items)) or "nessuna"))
    if not all_items:
        return None, None
    if any(language.same_language(c, dest_lang) for c in all_items):
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % language.label(dest_lang))
        return None, _mark(outcome, "UFFICIALE", _reason_official(dest_lang), engine="godot",
                            actions=["scegli_lingua"])
    source = origin if origin and origin != language.AUTO else "en"
    code = language.best(all_items, source)
    if code is None:
        return None, _mark(outcome, "NON_SUPPORTATO", REASON_TABLES_WITHOUT_SOURCE % (
            language.label(source), ", ".join(_language_name(c) for c in sorted(all_items))), engine="godot",
            actions=["crea_rapporto"])
    entry = {"pck": os.path.relpath(located[0], folder).replace(os.sep, "/"), "tabella": all_items[code],
            "lingua": code}
    if existing:
        profile = _read_profile(existing)
        profile.update({"motore": "godot", "godot": entry, "file": [], "lingua_origine": origin,
                        "lingua_destinazione": dest_lang, "backend": backend,
                        "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"), "motore": "godot",
                   "godot": entry, "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": backend, "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Godot: tabelle dei testi in %d lingue (%s); traduco quella in %s, senza toccare i file del gioco"
        % (len(all_items), ", ".join(sorted(all_items)), language.label(code)))
    return path, outcome


def godot_complete(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the table of the source language (rete=True) and puts override.cfg, the script and the translations
    next to the executable (see adapter_godot). rete=False: only the translations already in memory (Reapply)."""
    profile = _read_profile(profile_path)
    folder, a, entry = profile["cartella_gioco"], profile["lingua_destinazione"], profile["godot"]
    if system.game_open(folder):
        log("Il gioco e' aperto: chiudilo e riprova.")
        _mark(outcome, "GIOCO_APERTO", "Il gioco e' aperto.", engine="godot", actions=["chiudi_gioco"])
        return False
    pck_path = os.path.join(folder, *entry["pck"].split("/"))
    _language, texts = adapter_godot.table_texts(adapter_godot.Pck(pck_path).read_file(entry["tabella"]))
    texts = list(dict.fromkeys(t for t in texts if t.strip()))
    log("  Testi nella tabella del gioco: %d" % len(texts))
    src_lang = entry["lingua"]
    test = backend == "finto"
    project = Project(profile_path, log, test=test)
    failed = 0
    if network:
        translator = create_backend(backend, log)
        if not test and profile.get("backend") != backend:
            profile["backend"] = backend
            scan.save_json(profile_path, profile)
        failed = project.translate_texts(texts, translator, src_lang, a)
    translations = {t: project.memory[t] for t in texts if isinstance(project.memory.get(t), str)}
    if system.game_open(folder):
        log("  ! Il gioco e' stato aperto nel frattempo: chiudilo e premi 'Riapplica' (le traduzioni sono gia' salvate).")
        _mark(outcome, "GIOCO_APERTO", "Il gioco e' stato aperto mentre lavoravo: le traduzioni sono salvate ma non "
               "ancora nel gioco.", engine="godot", actions=["chiudi_gioco", "riapplica"])
        return False
    adapter_godot.install(os.path.dirname(pck_path), src_lang, translations, a, log)
    _write_outcome(outcome, project, texts, True, "godot",
                     reason="Nel gioco lascia selezionata la lingua %s: i testi compaiono in %s." % (
                         language.label(src_lang), language.label(a)))
    if failed:
        _after_attempts(outcome, log)
    return not failed


def gamemaker_engine(outcome):
    return outcome.get("motore_principale") == "GameMaker"


def _prepare_gamemaker(folder, existing, origin, dest_lang, outcome, log, backend="google",
                       translate_anyway=False):
    """Game profile for a GameMaker game with external localization files. None if it has none."""
    roots = gamemaker.roots(folder)
    if len(roots) > 1:
        log("Nella cartella ci sono %d giochi GameMaker: scegli la cartella di uno solo." % len(roots))
        return None, _mark(outcome, "CARTELLA_SBAGLIATA", "Nella cartella ci sono %d giochi GameMaker: scegli la "
                            "cartella di uno solo." % len(roots), engine="gamemaker")
    if roots and os.path.normcase(roots[0]) != os.path.normcase(os.path.abspath(folder)):
        log("  Cartella del gioco: %s" % roots[0])
        folder = roots[0]
        existing = existing or find_profile(folder)
    found = adapter_gamemaker.localization_file(folder)
    sources, languages, base = adapter_gamemaker.choose_sources(found, origin, folder)
    fmt = "righe"
    if not sources:  # a JSON file for each language (e.g. Forager: local/english.json, local/french.json...)
        json_found = adapter_gamemaker.language_json_files(folder, log)
        json_sources, json_languages, base_json = adapter_gamemaker.choose_sources(json_found, origin, folder)
        if json_sources:
            found, sources, languages, base, fmt = json_found, json_sources, json_languages, base_json, "json"
    tables = {}
    if not sources:  # CSV tables with one column for each language (e.g. translations.csv: id,english,french...)
        tables = {r: f for r, f in adapter_csv.tables(folder).items() if f.source(origin)[0] is not None}
        if tables:
            sources, fmt = sorted(tables), "csv"
            found = {}
            base = tables[sources[0]].source(origin)[1]
            languages = set()
            for f in tables.values():
                languages |= {c.split("-")[0].lower() for c in f.full_languages(f.source(origin)[0])}
            log("  Tabelle dei testi con una colonna per lingua: %s" % ", ".join(sources))
    if not sources:
        log("  Nessun file di localizzazione riconosciuto: provo la ricerca generica dei testi." if not found else
            "  Ci sono file di localizzazione, ma non riconosco quelli della lingua originale: provo la ricerca generica.")
        return None
    if dest_lang.split("-")[0].lower() in languages and not translate_anyway:
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % language.label(dest_lang))
        return None, _mark(outcome, "UFFICIALE", _reason_official(dest_lang), engine="gamemaker",
                            actions=["scegli_lingua", "traduci_comunque"])
    if has_anticheat(outcome):
        log("Questo gioco ha un anti-cheat: non modifico i suoi file (rischio di ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="gamemaker")
    log("  File di testo della lingua originale: %d (altre lingue nel gioco: %s)"
        % (len(sources), ", ".join(sorted(languages - {base})) or "nessuna"))
    if base and base != "en" and "en" in languages:
        log("  ! Traduco i file in %s: nel gioco dovrai selezionare quella lingua, non l'inglese."
            % language.label(base))
    if existing:  # files translated by a previous game profile (e.g. experimental) that must not be translated now
        _put_back_excluded(folder, [v.get("percorso") for v in _read_profile(existing).get("file", [])
                                    if v.get("percorso") and v.get("percorso") not in sources], log)
    if fmt == "righe":
        entries = [{"percorso": r, "formato": "righe"} for r in sources]
    elif fmt == "csv":
        entries = [{"percorso": r, "formato": "csv", "localizzazione": True,
                 "colonna": tables[r].header[tables[r].source(origin)[0]].strip().lstrip("\ufeff")}
                for r in sources]
    else:
        entries = _json_entries(folder, found, sources, log, dest_lang)
    officials = adapter_gamemaker.official_letters(folder, found, sources) if fmt != "csv" else \
        _table_letters(tables, origin)
    if existing:
        profile = _read_profile(existing)
        profile.update({"motore": "gamemaker", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "backend": backend, "file": entries, "lingua_file": base, "lettere_ufficiali": officials,
                        "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
                   "motore": "gamemaker", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": backend, "file": entries, "lingua_file": base, "lettere_ufficiali": officials}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 GameMaker: traduco i file di testo della lingua originale (backup automatico degli originali)")
    return path, outcome


def _table_letters(tables, origin):
    """Accented Latin letters of the official translations in the CSV tables (the columns of the other languages)."""
    letters = set()
    for f in tables.values():
        source = f.source(origin)[0]
        for i in f.languages:
            if i != source:
                letters |= {c for _, t in f.texts(i) for c in t if adapter_gamemaker._accented_latin(c)}
    return "".join(sorted(letters))


def _json_entries(folder, found, sources, log, dest_lang=None):
    """Game profile entries for the original language's JSON localization files, with what can be learned from the
    game and from its official translations in the same folder:
    - writing conventions (line breaks, paragraph start symbol) and use of capitals (lowercase games with capitals
      as font icons, e.g. Forager: 'EP', 'H');
    - the space of each text (longest line and number of lines of the versions that use the same font);
    - the official translations to show the local translator as a reference;
    - if the game loads all the files in the folder by itself (e.g. 'local/*.json'), the translation becomes a new
      language (local/italian.json) and the original stays as it is."""
    entries = []
    read_file = adapter_gamemaker._read_json
    data_win, mapping = None, None
    win_path = adapter_gamemaker.data_file(folder)
    try:
        if win_path:
            data_win = gamemaker.read_file(win_path)
            maps = gamemaker.font_sprite_maps(os.path.dirname(win_path), data_win)
            mapping = max((m for _, m in maps), key=len) if maps else None
    except (gamemaker.GameMakerError, OSError, ValueError, IndexError, struct.error):
        data_win = data_win if isinstance(data_win, bytes) else None
    for rel in sources:
        # from the original (backup), not from the file already translated by a previous version
        data = adapter_gamemaker.read_original(os.path.join(folder, *rel.split("/")), read_file)
        others = []  # (codice, relativo, dati) of the official translations of the same file
        for other in found:
            if other not in sources and adapter_gamemaker.family(other) == adapter_gamemaker.family(rel):
                try:
                    others.append((found[other], other,
                                  adapter_gamemaker.read_original(os.path.join(folder, *other.split("/")), read_file)))
                except (OSError, ValueError):
                    continue
        # the texts by key (also inside sections: 'menu._play'), here and in the official translations
        flat, other_flat = adapter_gamemaker.flatten(data), [adapter_gamemaker.flatten(d) for _, _, d in others]
        conventions = adapter_gamemaker.markup(flat, other_flat)
        texts = [v for v in flat.values() if adapter_gamemaker.localization_text(v)]
        style = adapter_gamemaker.caps_style(texts)
        codes = adapter_gamemaker.uppercase_codes(texts, mapping, conventions["punto_elenco"]) \
            if style == "minuscolo" else []
        dedicated = adapter_gamemaker.dedicated_fonts(data_win, [c for c, _, _ in others]) if data_win else set()
        same_font = [p for (c, _, _), p in zip(others, other_flat) if c.split("-")[0] not in dedicated]
        entry = {"percorso": rel, "formato": "json", "localizzazione": True,
                "includi": adapter_gamemaker.json_patterns(data), "escludi": [],
                "a_capo": conventions["a_capo"], "punto_elenco": conventions["punto_elenco"],
                "maiuscole": style, "codici": codes, "riferimenti": {c: r for c, r, _ in others},
                "limiti": adapter_gamemaker.line_limits(flat, same_font, conventions["a_capo"])
                if conventions["a_capo"] else {}}
        rel_folder = os.path.dirname(rel)
        relative_to_game = os.path.relpath(os.path.join(folder, *rel_folder.split("/")),
                                            os.path.dirname(win_path)).replace(os.sep, "/") if win_path else ""
        if dest_lang and rel_folder and data_win and not relative_to_game.startswith("..") and \
                gamemaker.folder_mask(os.path.dirname(win_path), relative_to_game,
                                            os.path.splitext(rel)[1], data_win):
            name = adapter_gamemaker.new_language_name(dest_lang, os.path.join(folder, *rel_folder.split("/")),
                                                       os.path.splitext(rel)[1])
            if name:
                entry["lingua_nuova"] = rel_folder + "/" + name
        entries.append(entry)
        mentioned = []
        if conventions["a_capo"]:
            mentioned.append("'%s' = a capo" % conventions["a_capo"])
        if conventions["punto_elenco"]:
            mentioned.append("'%s' = simbolo a inizio paragrafo" % conventions["punto_elenco"])
        if mentioned:
            log("  Convenzioni del gioco in %s: %s (rispettate nella traduzione)" % (rel, ", ".join(mentioned)))
        if style == "minuscolo":
            log("  Il gioco scrive in minuscolo%s: la traduzione fa lo stesso" % (
                " e usa le maiuscole come icone (%s, protette nella traduzione)" % ", ".join(codes) if codes else ""))
        if mapping:
            log("  Font a immagini del gioco: %d caratteri (le lettere che non ha vengono adattate)" % len(mapping))
        if entry.get("lingua_nuova"):
            log("  Il gioco carica da solo le lingue in %s/: la traduzione diventa una lingua nuova (%s), "
                "l'originale resta com'e'" % (rel_folder, entry["lingua_nuova"]))
    return entries


def folder_languages(folder, relative):
    """The names (without extension) of the language files of the same family as 'relativo' (e.g. english,
    french...): they are the values the game saves as the chosen language."""
    family = adapter_gamemaker.family(relative)
    names = {os.path.splitext(os.path.basename(r))[0] for r in adapter_gamemaker.language_json_files(folder)
            if adapter_gamemaker.family(r) == family}
    return names | {os.path.splitext(os.path.basename(relative))[0]}


def _put_back_excluded(folder, rel_paths, log):
    """Puts back the original of the files that a previous game profile had translated and that must not be
    translated now (e.g. the official translations translated by mistake by the experimental mode)."""
    for rel in rel_paths:
        path = os.path.join(folder, *rel.split("/"))
        if not read_info(path):
            continue
        try:
            is_open = system.game_open(folder)
        except system.SysError:
            is_open = True
        if is_open or not restore_file(path, lambda _m: None):
            log("  ! %s era stato tradotto prima ma non va tradotto: usa 'Ripristina originali' (a gioco chiuso)" % rel)
        else:
            log("  %s era stato tradotto prima ma non va tradotto: rimesso l'originale" % rel)


def _texts_language(folder, rel):
    """Language recognized from the texts of a JSON file (from the original), or None."""
    try:
        data = adapter_gamemaker.read_original(os.path.join(folder, *rel.split("/")),
                                                 adapter_gamemaker._read_json)
    except (OSError, ValueError):
        return None
    texts = [v for _, _, v in adapter_webjson.leaves(data) if is_readable_text(v)][:3000]
    return language.detect(texts)[0] if texts else None


def _original_language_only(candidates, folder, origin, dest_lang, log):
    """Among identical files in several languages (same family: local/{L}.json, lang/{L}/ui.json) keeps only those of
    the original language: the others are the game's official translations and must not be translated.
    Returns (candidates to keep, removed paths, files in the target language)."""
    families = {}
    for c in candidates:
        code = adapter_gamemaker.path_language(c["percorso"])
        if code:
            families.setdefault(adapter_gamemaker.family(c["percorso"]), []).append((c, code))
    base = (origin or "auto").split("-")[0].lower()
    remove, target_files = set(), []
    for fam, items in sorted(families.items()):
        languages = {code.split("-")[0] for _, code in items}
        if len(languages) < 2:
            continue
        target_files += [c["percorso"] for c, code in items if dest_lang and language.same_language(code, dest_lang)]
        keep_lang = "en" if base == "auto" else base
        kept_items = [c for c, code in items if code.split("-")[0] == keep_lang]
        if not kept_items:
            continue
        if base == "auto":  # an English much smaller than the other files is not necessarily the original language
            largest = max(c.get("stringhe_leggibili", 0) for c, _ in items)
            if max(c.get("stringhe_leggibili", 0) for c in kept_items) < 0.8 * largest:
                continue
        dropped = []
        for c, code in items:
            if code.split("-")[0] == keep_lang:
                continue
            detected = _texts_language(folder, c["percorso"])
            if detected is None or language.same_language(detected, keep_lang):
                continue  # the texts are not in another language: it is not a translation, it stays
            dropped.append(c["percorso"])
        if dropped:
            remove.update(dropped)
            log("  File per lingua (%s): traduco solo quelli in %s; gli altri %d sono le traduzioni ufficiali del "
                "gioco" % (fam, language.label(keep_lang), len(dropped)))
    return [c for c in candidates if c["percorso"] not in remove], remove, target_files


def has_anticheat(outcome):
    return any(r["sezione"] == "AntiCheat" for r in outcome["risultati"])


def _read_profile(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _language_name(code):
    """'Francese (fr)' for a code of the text tables ('fr', 'zh-cn', 'pt-br'); the code itself if unknown."""
    known_code = next((c for c in language.NAMES if c.lower() == (code or "").lower()), None)
    return language.label(known_code or language.best(language.NAMES, code) or code) if code else ""


def _crc_state(bundle_path):
    """adapter_unity_loc.bundle_crc, but also looking at the catalog's original: after the first translation the
    catalog.json in the game is the one written by PatchSmith (CRC already 0, "libero"), but the game profile entry must
    keep naming it. Without this, a new "Analyze and translate" would remove it from the game profile: "Reapply" after
    the game put back its files (Steam verification) would rewrite the bundle with the CRC back in the catalog, and the
    game would no longer load it."""
    state, catalog = adapter_unity_loc.bundle_crc(bundle_path)
    if state == "libero" and catalog and os.path.isfile(catalog + BACKUP_SUFFIX):
        if adapter_unity_loc.bundle_crc(bundle_path, read_from=catalog + BACKUP_SUFFIX)[0] == "controllato":
            state = "controllato"
    return state, catalog


def _table_entries(folder, bundle, log):
    """Game profile entries to rewrite these text table bundles, or "binario" / "crc" if the Addressables catalog
    checks their fingerprint and it cannot be removed."""
    entries = []
    for rel in bundle:
        entry = {"percorso": rel, "formato": "unity-tabelle"}
        state, catalog = _crc_state(os.path.join(folder, *rel.split("/")))
        if state == "binario":
            log("  Il gioco elenca i suoi bundle in un catalogo binario (catalog.bin) in cui non trovo con certezza %s: "
                "non modifico le tabelle dei testi." % rel.split("/")[-1])
            return "binario"
        if state == "controllato":
            entry["catalogo"] = os.path.relpath(catalog, folder).replace(os.sep, "/")
            log(DETAIL + "  Il catalogo del gioco controlla l'impronta (CRC) di %s: la tolgo solo per questo file "
                "(%s con backup, 'Ripristina originali' lo rimette)" % (rel.split("/")[-1], os.path.basename(catalog)))
        elif state == "sconosciuto" and adapter_unity_loc.checked_crc(os.path.join(folder, *rel.split("/"))):
            log("  Il gioco controlla l'impronta dei suoi bundle e non trovo quella di %s nel catalogo: non posso "
                "modificare le tabelle dei testi." % rel.split("/")[-1])
            return "crc"
        entries.append(entry)
    return entries


def _mono_tables(folder, origin, dest_lang, log):
    """Unity Mono games with Unity Localization: besides XUnity, the text tables inside their bundles are translated.
    It is needed for the texts XUnity cannot replace, like dialogues animated letter by letter (Febucci's Text
    Animator, e.g. Big Dig: XUnity sees the sentence in pieces and the game puts the original back); this way the game
    itself gives Italian to the animation. Returns the game profile entries ([] if not possible or not needed)."""
    all_items = adapter_unity_loc.table_bundles(folder)
    if not all_items or any(language.same_language(c, dest_lang) for c in all_items):
        return []
    source = origin if origin and origin != language.AUTO else "en"
    code = language.best(all_items, source)
    if code is None:
        return []
    entries = _table_entries(folder, all_items[code], log)
    if isinstance(entries, str):
        log("  Le tabelle dei testi restano in originale nei file: le traduce solo XUnity mentre giochi.")
        return []
    log("  Unity Localization: traduco anche le tabelle dei testi in %s dentro il loro bundle (backup automatico): "
        "cosi' anche i dialoghi animati compaiono in italiano" % language.label(source))
    return entries


def _prepare_unity_tables(folder, existing, origin, dest_lang, log):
    """Game profile for a Unity game with Unity Localization text tables (IL2CPP too): the tables in the source
    language (with "auto", English) are translated inside their bundles, recognized by their content too (see
    adapter_unity_loc.table_bundles). Returns the game profile's path, or:
    - "ufficiale": the game already has the target language;
    - "crc": the catalog checks the bundles' fingerprint and it cannot be removed for the bundle to translate;
    - "binario": the catalog is catalog.bin (it is unknown whether it checks the fingerprint);
    - ("lingua", lingue): there are tables, but not in the source language;
    - None: the game does not use Unity Localization.
    If the catalog (catalog.json) checks the fingerprint of a bundle to translate, the game profile entry also has
    "catalogo": Project sets that bundle's CRC to 0 there before rewriting it (with backup, see
    Project._remove_crc)."""
    all_items = adapter_unity_loc.table_bundles(folder)
    if not all_items:
        return None
    if any(language.same_language(c, dest_lang) for c in all_items):
        log("Il gioco ha gia' la traduzione in %s: sceglila nel menu delle lingue del gioco."
            % language.label(dest_lang))
        return "ufficiale"
    source = origin if origin and origin != language.AUTO else "en"
    code = language.best(all_items, source)
    if code is None:
        log("  Unity Localization: tabelle dei testi in %s, ma non in %s." % (", ".join(sorted(all_items)),
                                                                            language.label(source)))
        return "lingua", sorted(all_items)
    entries = _table_entries(folder, all_items[code], log)
    if isinstance(entries, str):
        return entries
    log("  Unity Localization: tabelle dei testi in %d lingue (%s); traduco quella in %s (%s) dentro il suo bundle "
        "(backup automatico), senza mod" % (len(all_items), ", ".join(sorted(all_items)), language.label(source),
                                            ", ".join(r.split("/")[-1] for r in all_items[code])))
    if existing:
        profile = _read_profile(existing)
        profile.update({"motore": "unity-localization", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "file": entries, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
                   "motore": "unity-localization", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": "locale", "file": entries}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Unity Localization: traduco le tabelle dei testi (nel gioco lascia selezionato %s)"
        % (language.endonym(source) or language.label(source)))
    return path


def _prepare_unity(folder, existing, origin, dest_lang, outcome, log, backend="google"):
    """Game profile for a Unity game: the translation happens inside the game with XUnity.AutoTranslator; for IL2CPP
    games with Unity Localization the text tables inside the bundles are translated.

    The game profile keeps the chosen translator (backend: the translator started by the game uses it too) and who
    translates the new sentences while the game is played (live, see LIVE_CHOICES): so "Reapply" and the
    command line redo the same thing even after "Restore originals"."""
    il2cpp = unity_engine(outcome) == "il2cpp"
    if has_anticheat(outcome):  # first of all, for IL2CPP games too (their bundles are game files)
        log("Questo gioco ha un anti-cheat: %s (rischio di ban)." % (
            "non modifico i suoi file" if il2cpp else "non installo un loader di mod"))
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="unity")
    if il2cpp:
        root = adapter_unity.find_root(folder) or folder
        tables = _prepare_unity_tables(root, existing or find_profile(root), origin, dest_lang, log)
        if tables == "ufficiale":
            return None, _mark(outcome, "UFFICIALE", _reason_official(dest_lang), engine="unity-localization",
                                actions=["scegli_lingua"])
        if tables == "crc":
            return None, _mark(outcome, "NON_SUPPORTATO", REASON_CRC, engine="unity-localization",
                                actions=["crea_rapporto"])
        if tables == "binario":
            return None, _mark(outcome, "NON_SUPPORTATO", REASON_BINARY_CATALOG, engine="unity-localization",
                                actions=["crea_rapporto"])
        if isinstance(tables, tuple):  # tables, but not in the source language
            source = origin if origin and origin != language.AUTO else "en"
            return None, _mark(outcome, "NON_SUPPORTATO", REASON_TABLES_WITHOUT_SOURCE % (
                language.label(source), ", ".join(dict.fromkeys(_language_name(c) for c in tables[1]))),
                engine="unity-localization", actions=["crea_rapporto"])
        if tables:
            return tables, outcome
        log("Gioco Unity IL2CPP senza tabelle di Unity Localization: non ancora supportato (servirebbe BepInEx 6).")
        return None, _mark(outcome, "NON_SUPPORTATO", REASON_IL2CPP, engine="unity", actions=["crea_rapporto"])
    root = adapter_unity.find_root(folder)
    if not root:
        log("Non trovo l'eseguibile del gioco Unity (il .exe con accanto la cartella _Data).")
        return None, _mark(outcome, "CARTELLA_SBAGLIATA", "Non trovo l'eseguibile del gioco Unity (il file .exe con "
                            "accanto la cartella che finisce con _Data): scegli la cartella dove c'e' l'eseguibile.",
                            engine="unity")
    if _normalize(root) != _normalize(folder):
        log("Cartella del gioco: %s" % root)
        folder = root
        existing = existing or find_profile(folder)
    if existing:
        profile = _read_profile(existing)
        if profile.get("motore") != "unity" and profile.get("file"):
            log("  (il profilo aveva anche traduzioni di file di testo: con Unity si usa solo la traduzione dentro il "
                "gioco)")
        profile.update({"motore": "unity", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"), "backend": backend})
        if profile.get("dal_vivo") not in LIVE_CHOICES:
            profile["dal_vivo"] = "servizio"
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
                   "motore": "unity", "lingua_origine": origin, "lingua_destinazione": dest_lang,
                   "backend": backend, "dal_vivo": "servizio", "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    profile["file"] = _mono_tables(folder, origin, dest_lang, log)
    scan.save_json(path, profile)
    log("== 2/3 Unity Mono: i testi verranno tradotti dentro il gioco da XUnity.AutoTranslator (con BepInEx)")
    return path, outcome


def _merge(old, new, log):
    """Adds the new files to the existing game profile and removes those the game no longer has."""
    folder = old["cartella_gioco"]
    kept_items = []
    for f in old["file"]:
        if os.path.exists(os.path.join(folder, *f["percorso"].split("/"))):
            kept_items.append(f)
        else:
            log("  - %s non esiste piu' nel gioco: tolto dal profilo" % f["percorso"])
    present = {f["percorso"] for f in kept_items}
    added = [f for f in new["file"] if f["percorso"] not in present]
    old["file"] = kept_items + added
    for key in ("lingua_origine", "lingua_destinazione", "backend"):
        old[key] = new[key]
    return old, added


def prepare(folder, origin="auto", dest_lang=None, backend="google", log=print, force=False, regenerate=False,
            translate_anyway=False, consent=False):
    """Detects the engine, finds the texts and creates/updates the game profile. Returns (percorso_profilo, esito)."""
    path, outcome = _prepare(folder, origin, dest_lang, backend, log, force, regenerate, translate_anyway,
                               consent)
    if path:
        _names_in_profile(path, log)
        _profile_identity(path)
    if translate_anyway and path:
        # machine translation wanted (and possible): if we had set the official language I put it back as it was,
        # otherwise the game would stay on the official language and the installed translation would not be seen. If
        # there is nothing to translate the official language stays.
        with open(path, encoding="utf-8") as f:
            registered = json.load(f).get("cartella_gioco")
        for d in dict.fromkeys(os.path.abspath(x) for x in (folder, registered) if x):
            if official_language.is_set(d) and official_language.restore(d, log):
                log("  La traduzione automatica prende il posto della lingua originale del gioco.")
    return path, outcome


def _names_in_profile(path, log):
    """The game's names not to translate (studio and title: see names.py) in the game profile, profilo["non_tradurre"],
    together with those already there (added by hand too). Project protects them like the game's codes. The names
    found also stay in profilo["nomi_trovati"]: a name found once and then removed by hand from "non_tradurre" (for
    example a title you want translated) does not come back."""
    try:
        profile = _read_profile(path)
        found = names.names_of_game(profile["cartella_gioco"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    already = [n for n in profile.get("non_tradurre") or [] if isinstance(n, str) and n.strip()]
    seen = profile.get("nomi_trovati") if isinstance(profile.get("nomi_trovati"), list) else []
    new_items = {"non_tradurre": list(dict.fromkeys(already + [n for n in found if n not in seen])),
             "nomi_trovati": list(dict.fromkeys(seen + found))}
    if any(profile.get(k, []) != v for k, v in new_items.items()):
        profile.update(new_items)
        scan.save_json(path, profile)
    items = new_items["non_tradurre"]
    if items:
        log("  Nomi del gioco che restano come sono nella traduzione: %s" % ", ".join(items))


def _profile_identity(path):
    """The game's identity (see identita) in the game profile: used to recognize it if you move or copy it elsewhere."""
    try:
        profile = _read_profile(path)
        new_value = identity(profile["cartella_gioco"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    if profile.get("identita") != new_value:
        profile["identita"] = new_value
        scan.save_json(path, profile)


def _prepare(folder, origin, dest_lang, backend, log, force, regenerate, translate_anyway, consent):
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError("La cartella non esiste: %s" % folder)
    dest_lang = dest_lang or language.windows_language()
    existing = find_profile(folder)
    if existing:
        with open(existing, encoding="utf-8") as f:
            registered = json.load(f)["cartella_gioco"]
        if _normalize(registered) != _normalize(folder):
            log("Questo gioco e' gia' registrato con la cartella %s: uso quella." % registered)
            folder = registered

    log("== 1/3 Riconoscimento del motore")
    outcome = detection.detect(folder)
    detection.print_summary(outcome, lambda m: log(DETAIL + "  " + m))
    log("  Motore: %s" % (outcome.get("motore_principale") or "non riconosciuto"))
    is_supported, web = game_supported(outcome)
    if not translate_anyway:
        # does the game already have the official translation? Then that one is used, not a machine translation
        official = official_language.detect(folder, dest_lang, outcome.get("motore_principale"), log)
        if official["presente"]:
            log("== Lingua ufficiale")
            interruption.check()
            done, setting = official_language.set_(folder, official, dest_lang, log, consent)
            lines = official_language.message(official, dest_lang, setting, done)
            installed_ok = installed_translation(folder)
            if installed_ok:
                lines.append("E' ancora installata la traduzione automatica di PatchSmith: se il gioco mostra quella "
                             "invece della lingua ufficiale, premi 'Ripristina originali' e poi di nuovo 'Analizza "
                             "e traduci'.")
            for line in lines:
                log("  " + line)
            outcome = dict(outcome, lingua_ufficiale=official, lingua_ufficiale_righe=lines,
                         traduzione_installata=installed_ok, motore_supportato=bool(is_supported or web),
                         lingua_impostata=done, serve_consenso=done is None)
            return None, _mark(outcome, "UFFICIALE", lines[0] if lines else "", actions=["scegli_lingua"],
                                extra={"lingua_ufficiale": True, "righe": lines,
                                       "supportato": bool(is_supported or web), "serve_consenso": done is None,
                                       "impostata": done})
        for note in official["note"]:
            log("  Nota: " + note)
    if unity_engine(outcome):
        return _prepare_unity(folder, existing, origin, dest_lang, outcome, log, backend)
    if unreal_engine(outcome):
        return _prepare_unreal(folder, existing, origin, dest_lang, outcome, log, backend)
    if xna_engine(outcome):
        result = _prepare_xna(folder, existing, origin, dest_lang, outcome, log, backend, translate_anyway)
        if result is not None:
            return result
    if gamemaker_engine(outcome):
        result = _prepare_gamemaker(folder, existing, origin, dest_lang, outcome, log, backend,
                                       translate_anyway)
        if result is not None:
            return result
    if godot_engine(outcome):
        path, result = _prepare_godot(folder, existing, origin, dest_lang, outcome, log, backend)
        if path or result is not None:
            return path, result

    encrypted = detection.encrypted_container(outcome)
    if encrypted and adapter_web_live.suitable(folder):  # the texts are translated live, in the game's page
        return _prepare_web_live(folder, existing, origin, dest_lang, backend, log), outcome
    if encrypted:  # before looking for texts: the only readable ones would be others' files (e.g. a Steam emulator)
        log("  " + REASON_ENCRYPTED % encrypted)
        return None, _mark(outcome, "NON_SUPPORTATO", REASON_ENCRYPTED % encrypted, engine="web",
                           actions=["crea_rapporto"])
    log("== 2/3 Ricerca dei testi")
    candidates, others = scan.scan_folder(folder, log)
    candidates, dropped, target_files = _original_language_only(candidates, folder, origin, dest_lang, log)
    # CSV tables with the target language column filled: official translation
    target_files += [c["percorso"] for c in candidates if c.get("formato") == "csv" and dest_lang and any(
        language.same_language(x, dest_lang) for x in c.get("lingue", ()) if x != c.get("lingua"))]
    if target_files and not translate_anyway:
        log("Il gioco ha gia' la traduzione in %s (%s): sceglila nel menu delle lingue del gioco."
            % (language.label(dest_lang), ", ".join(sorted(target_files)[:2])))
        return None, _mark(outcome, "UFFICIALE", _reason_official(dest_lang), engine="web" if web else None,
                            actions=["scegli_lingua", "traduci_comunque"])
    scan.print_summary(candidates, others, lambda m: log(DETAIL + "  " + m))
    name = os.path.splitext(os.path.basename(existing))[0] if existing else name_for_folder(folder)
    # the detected engine stays in the game profile: in GameMaker and XNA the translations adapt to the font characters
    new = scan.create_profile(name, folder, candidates, origin, dest_lang, backend,
                                   outcome["radici_web"] if web else (), engine=outcome.get("motore_principale"),
                                   log=log)

    if not new["file"]:
        log("Nessun testo traducibile trovato con gli strumenti attuali.")
        for c in outcome["consigli"]:
            log("  * " + c)
        return None, _mark(outcome, "NON_SUPPORTATO", _reason_no_text(outcome, folder), actions=["crea_rapporto"],
                            engine=(outcome.get("motore_principale") or "").lower() or None)
    if not is_supported and not force:
        log("Motore %s: non ancora supportato in automatico." % (outcome["motore_principale"] or "non riconosciuto"))
        for c in outcome["consigli"]:
            log("  * " + c)
        log("Ho trovato comunque %d file con testo: puoi tradurli in modo sperimentale." % len(new["file"]))
        engine = outcome["motore_principale"] or "non riconosciuto"
        return None, _mark(dict(outcome, sperimentale_possibile=True), "NON_SUPPORTATO",
                            "Il motore del gioco (%s) non e' ancora supportato in automatico." % engine,
                            engine=(outcome.get("motore_principale") or "").lower() or None,
                            actions=["sperimentale", "crea_rapporto"], extra={"motore_rilevato": engine})

    if existing and not regenerate:
        with open(existing, encoding="utf-8") as f:
            profile, added = _merge(json.load(f), new, log)
        for key in ("motore", "motore_gioco"):  # game profiles created before the engine stayed in the profile
            if new.get(key) and not profile.get(key):
                profile[key] = new[key]
        excluded = [f["percorso"] for f in profile["file"] if f["percorso"] in dropped]
        if excluded:
            profile["file"] = [f for f in profile["file"] if f["percorso"] not in dropped]
            log("  Tolti dal profilo (traduzioni ufficiali del gioco): %s" % ", ".join(excluded))
            _put_back_excluded(folder, excluded, log)
        log(DETAIL + "Profilo esistente aggiornato: %s (%d file nuovi)" % (existing, len(added)))
        path = existing
    else:
        profile = new
        path = existing or os.path.join(profiles_folder(), name + ".json")
        log(DETAIL + "Profilo creato: %s" % path)
    scan.save_json(path, profile)
    for f in profile["file"]:
        kind = "interfaccia (menu)" if f.get("formato") == "web-ui" else \
            "tabella dei testi (colonna %s)" % f.get("colonna") if f.get("formato") == "csv" else \
            "%s (%d campi)" % ("file di lingua" if f.get("localizzazione") else "dati", len(f.get("includi", [])))
        log(DETAIL + "  - %s: %s" % (f["percorso"], kind))
    log("  File con testi da tradurre: %d" % len(profile["file"]))
    if profile.get("motore_gioco"):
        log("  Le traduzioni si adattano ai caratteri che i font del gioco sanno disegnare")
    return path, outcome


def _prepare_web_live(folder, existing, origin, dest_lang, backend, log):
    """Game profile of an Electron game with encrypted files but a plain preload.js (see adapter_web_live): no files
    to translate, the texts are translated while the game is played. Returns the game profile's path."""
    if existing:
        profile = _read_profile(existing)
        if profile.get("file"):  # (e.g. files of a Steam emulator translated by mistake before): no longer used
            log(DETAIL + "  Tolti dal profilo: %s" % ", ".join(f["percorso"] for f in profile["file"]))
        profile.update({"motore": "web-vivo", "file": [], "lingua_origine": origin, "lingua_destinazione": dest_lang,
                        "backend": backend, "dal_vivo": "servizio"})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"nome": name, "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"), "motore": "web-vivo",
                   "lingua_origine": origin, "lingua_destinazione": dest_lang, "backend": backend,
                   "dal_vivo": "servizio", "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Testi cifrati: li traduce PatchSmith mentre giochi, nella pagina del gioco (il file cifrato non si "
        "tocca)")
    return path


def live_translation(folder):
    """(profilo, attiva) if the folder's game has translation while playing (Unity, live web), otherwise (profilo or
    None, None)."""
    path = find_profile(folder)
    if not path:
        return None, None
    try:
        profile = _read_profile(path)
    except (OSError, ValueError):
        return None, None
    if profile.get("motore") not in ("unity", "web-vivo"):
        return profile, None
    return profile, profile.get("dal_vivo") != "nessuno"


def set_live(folder, enable, log=print):
    """Turns translation while playing on or off (the choice at the end of "Analyze and translate"): it stays in the
    game profile and is applied to the game right away if it is closed (if it is open, its translator stops and the
    rest applies from the next check at startup or from "Reapply"). Returns True if applied in the game."""
    from . import xunity_service
    path = find_profile(folder)
    profile = _read_profile(path)
    profile["dal_vivo"] = "servizio" if enable else "nessuno"
    scan.save_json(path, profile)
    if not enable and xunity_service.stop(profile["nome"]):
        log("Traduttore del gioco fermato.")
    game = profile["cartella_gioco"]
    if system.game_open(game):
        log("Il gioco e' aperto: la scelta vale dalla prossima volta che lo avvii.")
        return False
    if profile.get("motore") == "web-vivo":
        web_live_full(path, log=lambda m: log(DETAIL + m) if m.strip() else None, network=False,
                          backend=None)
    else:
        src_lang = profile.get("lingua_origine")  # the one XUnity is already configured with, if "auto"
        ini = adapter_unity.read_ini(os.path.join(game, *adapter_unity.CONFIG_XUNITY.split("/")))
        if src_lang in (None, language.AUTO):
            src_lang = (ini.get("General") or {}).get("FromLanguage") or "en"
        adapter_unity.install(game, src_lang, profile["lingua_destinazione"],
                               profile["nome"], lambda m: log(DETAIL + m),
                               local=path if enable else "nessuno")
    log("Traduzione mentre giochi: %s." % ("accesa" if enable else "spenta (solo le traduzioni gia' salvate)"))
    return True


def web_live_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Installs (or updates) the live translation of a web game with encrypted files: the script in preload.js and the
    translations already made (the game's translation memory). No pre-translation: the texts are known only while the
    game is played."""
    profile = _read_profile(profile_path)
    folder, a = profile["cartella_gioco"], profile["lingua_destinazione"]
    src_lang = profile.get("lingua_origine")
    # (the texts are encrypted: the language cannot be detected first)
    src_lang = "en" if src_lang in (None, language.AUTO) else src_lang
    if system.game_open(folder):
        log("Il gioco e' aperto: chiudilo e riprova.")
        _mark(outcome, "GIOCO_APERTO", "Il gioco e' aperto.", engine="web", actions=["chiudi_gioco"])
        return False
    if network and backend not in (None, "finto") and profile.get("backend") != backend:
        profile["backend"] = backend  # used by the translator started by the game
        scan.save_json(profile_path, profile)
    project = Project(profile_path, log, test=backend == "finto")
    active = profile.get("dal_vivo") != "nessuno"
    n = adapter_web_live.install(folder, profile_path, profile["nome"], src_lang, a, project.memory, log,
                                  active=active)
    from . import xunity_service
    expected = xunity_service.expected_translators(profile) if active else []
    live = "locale" if "locale" in expected else ("online" if expected else "nessuno")
    log("  Frasi mentre giochi: %s" % LIVE.get(live, live))
    _mark(outcome, "OK", REASON_WEB_LIVE, engine="web", written=True, translated_items=n, live=live,
           actions=["installa_locale"] if live == "nessuno" else [])
    return True


def analyze_and_translate(folder, origin="auto", dest_lang=None, backend="google", log=print,
                       force=False, regenerate=False, translate_anyway=False, consent=False, reuse=None):
    """Complete procedure. Returns (profile_path, completato, esito); the outcome for the interface (code, counts,
    reason, actions: see outcome.py) is in esito["esito"]. riusa: the game profile of the same game in another folder
    (see same_games) whose translations to reuse: if that folder is no longer there the game profile moves to this
    one, otherwise its translations are copied into the new game profile before translating."""
    moved = bool(reuse) and _move_profile(reuse, folder, log)
    path, outcome = prepare(folder, origin, dest_lang, backend, log, force, regenerate, translate_anyway,
                              consent)
    interruption.check()  # stopped during the analysis: no questions or writes afterwards
    if not path:
        if "esito" not in outcome:  # every exit has its own outcome: this is only a fallback
            _mark(outcome, "NON_SUPPORTATO", "Non ho trovato un modo sicuro per tradurre questo gioco.",
                   actions=["crea_rapporto"])
        return None, False, outcome
    if reuse and not moved:
        _copy_translations(reuse, path, log)
    profile = _read_profile(path)
    if profile.get("motore") == "godot":
        log("== 3/3 Traduzione della tabella dei testi (i file del gioco non vengono toccati)")
        is_ready = godot_complete(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Fatto. Avvia il gioco con la lingua %s selezionata: i testi compaiono in %s." % (
                language.label(profile["godot"]["lingua"]), language.label(profile["lingua_destinazione"])))
        elif adapter_godot.manifest_present(os.path.dirname(os.path.join(
                profile["cartella_gioco"], *profile["godot"]["pck"].split("/")))):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
        return path, bool(is_ready), outcome
    if profile.get("motore") == "web-vivo":
        log("== 3/3 Traduzione dal vivo (backup automatico di preload.js)")
        is_ready = web_live_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Fatto. Avvia il gioco: le frasi si traducono mentre giochi (la prima volta compaiono in inglese per "
                "un attimo).")
        return path, bool(is_ready), outcome
    if profile.get("motore") == "unity":
        log("== 3/3 Installazione di BepInEx + XUnity.AutoTranslator (nessun file del gioco viene modificato)")
        is_ready = unity_full(path, log, network=True, anticheat=has_anticheat(outcome), backend=backend,
                                outcome=outcome)
        if is_ready is False and adapter_unity.manifest_present(profile["cartella_gioco"]):
            log("Fatto in parte: XUnity e' installato, ma la pre-traduzione non e' completa (vedi sopra).")
        if is_ready:
            no_live = getattr(outcome.get("esito"), "live", None) == "nessuno"
            log("Fatto. Avvia il gioco: le frasi pre-tradotte compaiono subito in italiano; le altre (es. testi")
            log("composti al momento) %s. Nel gioco: ALT+T alterna originale/tradotto."
                % ("per ora restano in originale (vedi sopra)" if no_live else "vengono tradotte dal vivo"))
        return path, bool(is_ready), outcome
    if profile.get("motore") == "unreal":
        log("== 3/3 Traduzione dei .locres e scrittura del pak di patch (i pak originali non vengono toccati)")
        is_ready = unreal_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Fatto. Nel gioco deve essere selezionata la lingua originale (di solito English): la traduzione")
            log("prende il suo posto. Per tornare all'originale: 'Ripristina originali'.")
        elif adapter_unreal.manifest_present(profile["cartella_gioco"]):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
            _after_attempts(outcome, log)
        return path, bool(is_ready), outcome
    if profile.get("motore") == "xna":
        log("== 3/3 Traduzione delle risorse .NET e creazione dei satelliti (i file del gioco non vengono toccati)")
        is_ready = xna_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Fatto. Nel gioco lascia (o seleziona) la lingua originale, di solito English: la traduzione")
            log("prende il suo posto. Per tornare all'originale: 'Ripristina originali'.")
        elif adapter_xna.manifest_present(profile["cartella_gioco"]):
            log("Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
            _after_attempts(outcome, log)
        return path, bool(is_ready), outcome
    log("== 3/3 Traduzione e scrittura nel gioco (backup automatico degli originali)")
    if backend == "finto":
        log("  (prova senza rete: le traduzioni finte vanno in una memoria separata)")
    p = Project(path, log, test=(backend == "finto"))
    completed = p.translate(create_backend(backend, log))
    log("Fatto. Avvia il gioco per vedere la traduzione." if completed else
        "Fatto in parte: alcuni testi sono rimasti in originale (vedi sopra).")
    if profile.get("motore") == "gamemaker":
        _after_writing(p.profile, log, outcome, set_=backend != "finto")
    if profile.get("motore") == "unity-localization":
        source = profile.get("lingua_origine") if profile.get("lingua_origine") not in (None, language.AUTO) else "en"
        log("Nel gioco lascia selezionata la lingua %s: i suoi testi ora sono tradotti."
            % (language.endonym(source) or language.label(source)))
    _apply_outcome(outcome, p, profile.get("motore") or ("web" if game_supported(outcome)[1] else None))
    if not completed:
        _after_attempts(outcome, log)
    return path, completed, outcome


def _reason_already_in(dest_lang):
    return "Il gioco e' gia' in %s: non c'e' niente da tradurre." % language.label(dest_lang)


def _reason_official(dest_lang):
    return "Il gioco ha gia' la traduzione ufficiale in %s." % language.label(dest_lang)


def _reason_no_text(outcome, folder):
    """Why there is nothing to translate, worded for the type of game (what the detection saw)."""
    engine = outcome.get("motore_principale")
    containers = {r["nome"] for r in outcome["risultati"] if r["sezione"] == "Container"}
    if engine == "GameMaker":
        return ("I testi di questo gioco GameMaker sono dentro il file dei dati del gioco (data.win), non in file di "
                "testo separati: PatchSmith non sa ancora tradurli.")
    if engine in ("XNA", "FNA", "MonoGame"):
        return ("I testi di questo gioco sono scritti nel suo codice, non in file di lingua separati: PatchSmith non "
                "sa ancora tradurli.")
    if any("!/" in r for r in outcome.get("radici_web") or ()) or \
            ("Electron" in containers and os.path.exists(os.path.join(folder, "resources", "app.asar"))):
        return ("I file del gioco sono chiusi in un archivio (app.asar): PatchSmith non sa ancora tradurre i testi "
                "che ci sono dentro.")
    if engine:
        return ("Non ho trovato file con testi che PatchSmith sappia leggere (motore del gioco: %s)." % engine)
    return "Non ho riconosciuto il motore del gioco e non ho trovato file con testi da tradurre."


def _write_outcome(outcome, project, texts, written, engine, live=None, reason="", actions=()):
    """Outcome of the engines that write the translations from the translation memory into the game (Unity, Unreal,
    XNA): OK, PARZIALE or TRADUTTORE_BLOCCATO, with the translated texts and those left untranslated. azioni: extra
    ones (e.g. for Unity's new sentences without a translator)."""
    if outcome is None:
        return
    unique = list(dict.fromkeys(t for t in texts if isinstance(t, str)))
    remaining = [t for t in unique if not (isinstance(project.memory.get(t), str) and project.memory.get(t))]
    fields = dict(engine=engine, written=written, translated_items=len(unique) - len(remaining),
                 remaining=len(remaining), remaining_examples=remaining[:20], live=live, extra=_extra_attempts(project))
    if project.translation_outcome == "TRADUTTORE_BLOCCATO" and remaining:
        _mark(outcome, "TRADUTTORE_BLOCCATO", project.translation_reason, actions=["usa_locale"] + list(actions),
               **fields)
    elif remaining or not written:
        _mark(outcome, "PARZIALE", reason or ("" if written else "Non ho scritto nulla nel gioco."),
               actions=["usa_locale"] + list(actions), **fields)
    else:
        _mark(outcome, "OK", reason, actions=list(actions), **fields)


def _apply_outcome(outcome, project, engine):
    """Outcome after Progetto.traduci or applica (games with text files: web, GameMaker, Unity Localization...)."""
    if outcome is None:
        return
    r = project.last_apply or {"ok": 0, "mancanti": 0, "esempi": [], "scartate": 0, "scritti": 0,
                                    "aperto": False}
    # scritto: the game has at least one translation (a file rewritten without translations does not count)
    fields = dict(engine=engine, written=r["scritti"] > 0 and r["ok"] > 0, translated_items=r["ok"],
                 remaining=r["mancanti"], remaining_examples=r["esempi"], extra=_extra_attempts(project))
    if outcome.get("lingua_aggiunta"):
        fields["extra"]["lingua_aggiunta"] = outcome["lingua_aggiunta"]
    if r["aperto"]:
        _mark(outcome, "GIOCO_APERTO", "Il gioco e' aperto: le traduzioni sono pronte e salvate, ma non le ho scritte "
               "nel gioco.", actions=["chiudi_gioco", "riapplica"], **fields)
    elif project.translation_outcome == "NULLA_DA_FARE":
        _mark(outcome, "NULLA_DA_FARE", project.translation_reason, **fields)
    elif project.translation_outcome == "TRADUTTORE_BLOCCATO" and r["mancanti"]:
        _mark(outcome, "TRADUTTORE_BLOCCATO", project.translation_reason, actions=["usa_locale"], **fields)
    elif r["mancanti"]:
        reason = ("%s perche' il gioco non %s mostrerebbe bene (forma diversa dall'originale o caratteri che i suoi "
                  "font non hanno)." % ("Una traduzione non e' stata scritta" if r["scartate"] == 1 else
                                        "%d traduzioni non sono state scritte" % r["scartate"],
                                        "la" if r["scartate"] == 1 else "le")) if r["scartate"] else ""
        if r["scartate"]:  # (for "Fix translations": the "Not written to the game" list, see outcome.py)
            fields["extra"]["scartate"] = r["scartate"]
        # texts never translated (not just rejected): another translator may help
        _mark(outcome, "PARZIALE", reason, actions=["usa_locale"] if r["mancanti"] > r["scartate"] else [], **fields)
    else:
        _mark(outcome, "OK", **fields)


def _after_writing(profile, log, outcome, set_=True):
    """GameMaker, after writing the translations: if the translation is an added language, sets it in the options
    saved by the game (or says how to choose it); otherwise says to leave the original language."""
    folder = profile["cartella_gioco"]
    entries = [v for v in profile.get("file", []) if v.get("lingua_nuova")]
    written_texts = [v for v in entries if os.path.exists(os.path.join(folder, *v["lingua_nuova"].split("/")))]
    if entries and not written_texts:
        log("La traduzione NON e' nel gioco (vedi sopra: se il gioco era aperto, chiudilo e premi di nuovo 'Analizza "
            "e traduci', che usa le traduzioni gia' salvate).")
        return
    if not entries:
        log("Nel gioco lascia selezionata la lingua %s: i suoi testi ora sono tradotti."
            % language.label(profile.get("lingua_file") or "en"))
        return
    name = os.path.splitext(os.path.basename(written_texts[0]["lingua_nuova"]))[0]
    is_set = False
    if set_:
        is_set, lines = official_language.set_added_language(
            folder, name, folder_languages(folder, written_texts[0]["percorso"]), log,
            source=os.path.splitext(os.path.basename(written_texts[0]["percorso"]))[0])
        for r in lines:
            log("  " + r)
    outcome["lingua_aggiunta"] = {"nome": name, "impostata": is_set}
    log("Nel gioco la traduzione e' la lingua '%s': %s. L'originale resta selezionabile nel menu." % (
        name, "l'ho gia' impostata nelle opzioni del gioco" if is_set else
        "sceglila in Opzioni > Lingua (con le frecce)"))


def installed_translation(folder):
    """True if the folder has a PatchSmith machine translation (modified files or added components)."""
    try:
        root = adapter_unity.find_root(folder)
        translated_items = [p for p in find_info(folder) if not configurator.configurator_file(p)]
        return bool(translated_items or adapter_unity.manifest_present(folder) or
                    (root and adapter_unity.manifest_present(root)) or adapter_unreal.manifest_present(folder)
                    or adapter_xna.installed(folder))
    except (OSError, ValueError):
        return False


def restore(folder, log=print, outcome=None):
    """Puts back the originals of all the modified files, even without a game profile; the game's language last.
    With the game open it touches nothing (files in use cannot be put back and the game would rewrite its
    settings). Returns True if everything is fine; the outcome (see outcome.py) goes into esito["esito"]."""
    path = find_profile(folder)
    root = registered = folder
    if path:
        with open(path, encoding="utf-8") as f:
            root = registered = json.load(f)["cartella_gioco"]
        # also look in the chosen folder, if it contains the registered one
        if _inside(root, folder):
            root = folder
    for d in dict.fromkeys(os.path.abspath(x) for x in (folder, registered, root) if x and os.path.isdir(x)):
        try:
            is_open = system.game_open(d)
        except system.SysError as e:
            log(str(e))
            _mark(outcome, "GIOCO_APERTO", str(e), actions=["chiudi_gioco"])
            return False
        if is_open:
            log("Il gioco e' aperto: chiudilo e ripeti 'Ripristina originali'. Non ho toccato nulla.")
            _mark(outcome, "GIOCO_APERTO", "Il gioco e' aperto.", actions=["chiudi_gioco"])
            return False
    modified = find_info(root)
    candidates = [registered, root, folder, adapter_unity.find_root(folder)]
    if not path:  # parent folder with several registered games: I consider all of them
        candidates += [p.get("cartella_gioco") for _, p in list_profiles()
                      if p.get("cartella_gioco") and _inside(p["cartella_gioco"], folder)]
    unity = list(dict.fromkeys(os.path.abspath(d) for d in candidates
                               if d and adapter_unity.manifest_present(d)))
    unreal = list(dict.fromkeys(os.path.abspath(d) for d in candidates
                                if d and adapter_unreal.manifest_present(d)))
    xna = list(dict.fromkeys(os.path.abspath(d) for d in candidates
                             if d and os.path.isdir(d) and adapter_xna.installed(d)))
    godot = list(dict.fromkeys(os.path.abspath(d) for d in candidates
                               if d and adapter_godot.manifest_present(d)))
    languages = list(dict.fromkeys(os.path.abspath(d) for d in candidates if d and official_language.is_set(d)))
    if not modified and not unity and not unreal and not xna and not godot and not languages:
        log("Nessun file modificato in questa cartella: il gioco e' gia' originale.")
        _mark(outcome, "GIA_ORIGINALE", "Non ho trovato modifiche di PatchSmith in questa cartella: il gioco e' gia' "
               "originale.")
        return False
    name = os.path.splitext(os.path.basename(path))[0] if path else name_for_folder(root)
    for d in unity:
        adapter_unity.uninstall(d, name, log)
    for d in unreal:
        adapter_unreal.uninstall(d, log)
    for d in xna:
        adapter_xna.uninstall(d, log)
    for d in godot:
        adapter_godot.uninstall(d, log)
    for p in modified:  # added languages (e.g. local/italian.json): the game's options must not keep them
        info = read_info(p) or {}
        additions = [os.path.splitext(n)[0] for n in info.get("creati", []) if n.lower().endswith(".json")]
        if additions and gamemaker.roots(root):
            source = os.path.splitext(os.path.basename(p))[0]
            names = {os.path.splitext(n)[0] for n in os.listdir(os.path.dirname(p)) if n.lower().endswith(".json")}
            if official_language.put_back_added_language(root, additions, source, names, log) is False:
                log("Non ho tolto la traduzione: il gioco ripartirebbe con una lingua che non c'e' piu'. Chiudi il "
                    "gioco, controlla che le sue opzioni non siano di sola lettura e ripeti 'Ripristina originali'.")
                _mark(outcome, "PERMESSI", "Non ho tolto la traduzione: non riesco a cambiare la lingua scelta nelle "
                       "opzioni del gioco, che ripartirebbe con una lingua che non c'e' piu'. Controlla che le "
                       "opzioni del gioco non siano di sola lettura.", actions=["chiudi_gioco"])
                return False
    blocked_files = []
    for p in modified:
        try:
            restore_file(p, log)
        except (OSError, RuntimeError) as e:
            blocked_files.append(os.path.basename(p))
            log("  ! %s non rimesso (%s)" % (os.path.basename(p), e))
    language_unrestored = False
    for d in languages:  # last: a problem here must not prevent removing the translation
        try:
            if official_language.restore(d, log) is False:
                language_unrestored = True
        except Exception as e:
            language_unrestored = True
            log("  ! Non ho potuto rimettere la lingua del gioco com'era (%s: %s): sceglila nel menu del gioco."
                % (type(e).__name__, e))
    if blocked_files:
        log("Restano da rimettere: %s. Chiudi i programmi che li usano (anche antivirus) e ripeti 'Ripristina "
            "originali'." % ", ".join(blocked_files))
        _mark(outcome, "PERMESSI", "Restano da rimettere: %s." % ", ".join(blocked_files[:5]),
               actions=["chiudi_gioco", "crea_rapporto"])
        return False
    if language_unrestored:
        log("Originali ripristinati, tranne la lingua del gioco (vedi sopra): ripeti 'Ripristina originali' per "
            "riprovare.")
        _mark(outcome, "PARZIALE", "Originali ripristinati, tranne la lingua del gioco: sceglila nel menu del gioco, "
               "oppure premi di nuovo \"Ripristina originali\" per riprovare.")
        return False
    log("Originali ripristinati.")
    _mark(outcome, "OK", "Originali ripristinati.")
    return True


def reapply(folder, log=print, test=False, outcome=None):
    """Rewrites the saved translations into the game, without network. True if everything is translated; the outcome
    (see outcome.py) goes into esito["esito"] (and GameMaker's added language into esito["lingua_aggiunta"])."""
    path = find_profile(folder)
    if not path:
        log("Nessuna traduzione registrata per questa cartella.")
        _mark(outcome, "NULLA_DA_FARE", "Per questa cartella non c'e' nessuna traduzione salvata: usa prima "
               "'Analizza e traduci'.")
        return False
    profile = _read_profile(path)
    if profile.get("motore") == "godot":  # override.cfg, script and saved translations, without network
        return bool(godot_complete(path, log, network=False, backend="finto" if test else None, outcome=outcome))
    if profile.get("motore") == "web-vivo":  # the script in preload.js and the saved translations, without network
        return bool(web_live_full(path, log, network=False, backend="finto" if test else None, outcome=outcome))
    if profile.get("motore") == "unity":  # reinstalls from the components already downloaded, with saved translations
        return bool(unity_full(path, log, network=False, backend="finto" if test else "google", outcome=outcome))
    if profile.get("motore") == "unreal":
        return bool(unreal_full(path, log, network=False, backend="finto" if test else "google", outcome=outcome))
    if profile.get("motore") == "xna":
        return bool(xna_full(path, log, network=False, backend="finto" if test else "google", outcome=outcome))
    project = Project(path, log, test=test)
    done = project.apply()
    if profile.get("motore") == "gamemaker":
        _after_writing(project.profile, log, outcome if outcome is not None else {}, set_=not test)
    _apply_outcome(outcome, project, profile.get("motore") or ("web" if any(
        v.get("formato") == "web-ui" for v in profile.get("file", [])) else None))
    return done


def retranslate(folder, texts=None, backend="locale", log=print, test=False, outcome=None, model=None):
    """"Fix translations": re-translates the chosen texts now (testi=None: all the machine translations and the texts
    left untranslated; fixes made by hand stay), see corrections.retranslate, and then rewrites them into the game like
    "Reapply" (with the game closed: with the game open the new translations stay saved and the outcome says to press
    "Reapply"). modello: the local translator model to use (e.g. '12b'). True if everything is fine; the outcome in
    esito["esito"], with extra["ritraduzione"] = {ritradotte, rimaste, testi, interrotta}."""
    from . import corrections
    path = find_profile(folder)
    if not path:
        log("Nessuna traduzione registrata per questa cartella.")
        _mark(outcome, "NULLA_DA_FARE", "Per questa cartella non c'e' nessuna traduzione salvata: usa prima "
               "'Analizza e traduci'.")
        return False
    translator = corrections.create_translator("finto" if test else backend, model, log)
    result = corrections.retranslate(path, texts, translator, log, test=test)
    interruption.check()
    registered = _read_profile(path).get("cartella_gioco") or folder
    try:
        is_open = os.path.isdir(registered) and system.game_open(registered)
    except system.SysError:
        is_open = True
    if is_open:
        log("Il gioco e' aperto: le traduzioni nuove sono salvate, le scrivo nel gioco con 'Riapplica' quando e' "
            "chiuso.")
        _mark(outcome, "GIOCO_APERTO", "Le traduzioni nuove sono salvate, ma il gioco e' aperto: non le ho ancora "
               "scritte nel gioco.", actions=["chiudi_gioco", "riapplica"])
        done = False
    else:
        done = reapply(folder, log, test=test, outcome=outcome)
    if outcome is not None and outcome.get("esito") is not None:
        outcome["esito"].extra["ritraduzione"] = result
    return done and not result["interrotta"]


class RestartError(RuntimeError):
    """"Start over" did not start (codice: a code from esito.CODICI, for the window)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def start_over(folder, log=print, test=False, backend=None):
    """"Start over" (Fix translations): puts back the game's original files (like "Restore originals": the text scan
    must see the originals, not the translated files) and sets aside all the saved translations, the ones fixed by
    hand too, in memory/copies/<gioco>-<data>/ (nothing is lost), so that the next "Analyze and translate" (with
    rigenera) translates everything again. The saved translations are the game's translation memory with the machine
    translations, the texts with problems and the fixes to write; for Unity also the XUnity cache saved by "Restore".
    From the shared translation memory, the short texts machine-translated for this game are removed (and kept in the
    copy): otherwise they would come back the same. If the originals cannot be put back (game open, locked files) it
    touches no translation (RestartError). backend: the translator that will translate again: if it is not there
    (not installed, missing key) or is paused, BackendError before touching anything (otherwise the game would be left
    without a translation). Returns the folder of the copy (None if there was nothing to set aside)."""
    from . import corrections
    from .backend import BackendError
    from .project import problems_path
    path = find_profile(folder)
    if not path:
        return None
    if backend:
        translator = create_backend(backend, log)
        pause = translator.pause() if hasattr(translator, "pause") else None
        if pause:
            raise BackendError("%s Per ricominciare da zero serve un traduttore pronto: non ho toccato nulla." % pause,
                                "TRADUTTORE_BLOCCATO")
    profile = _read_profile(path)
    game, a, name = profile.get("cartella_gioco") or folder, profile["lingua_destinazione"], profile["nome"]
    put_back = {}
    log("Ricomincia da zero: prima rimetto i file originali del gioco")
    restore(folder, log, outcome=put_back)
    e = put_back.get("esito")
    if e is not None and e.code not in ("OK", "GIA_ORIGINALE", "PARZIALE"):
        raise RestartError("%s Per ricominciare da zero servono i file originali: non ho messo da parte nessuna "
                               "traduzione." % (e.reason or "Non ho potuto rimettere i file originali del gioco."),
                               e.code if e.code in ("GIOCO_APERTO", "PERMESSI") else None)
    memory = memory_path(profile, test)
    file = [memory, problems_path(profile, test)]
    if not test:
        file += [memory[:-len(".json")] + ".automatiche.json", corrections.to_write_path(profile)]
        if profile.get("motore") == "unity":
            file += [adapter_unity._cache_path(game, a), adapter_unity._memory_folder(name)]
    present = [f for f in file if os.path.exists(f)]
    if not present:
        return None
    base = data_folder("memory", "copies", "%s-%s" % (name, datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    copy_dir, n = base, 2
    while os.path.exists(copy_dir):  # (twice in the same second: never mix two copies)
        copy_dir, n = "%s-%d" % (base, n), n + 1
    os.makedirs(copy_dir)
    for f in present:
        dest = os.path.join(copy_dir, os.path.basename(f) if not os.path.normcase(f).startswith(
            os.path.normcase(os.path.abspath(game))) else "cache-nel-gioco-" + os.path.basename(f))
        shutil.move(f, dest)
    log("Traduzioni di prima messe da parte in %s (%d file): ricomincio da zero." % (copy_dir, len(present)))
    if not test:
        _remove_from_shared(profile, memory, copy_dir, log)
    return copy_dir


def _remove_from_shared(profile, memory, copy_dir, log):
    """Start over: the entries of the shared translation memory machine-translated for this game (provenance) are
    removed, and kept in the copy; those fixed by hand stay (they apply to all games)."""
    from .project import shared_path, shared_origin_path
    name, dest = profile["nome"], profile["lingua_destinazione"]
    end = "-%s.origine.json" % dest
    for file_name in os.listdir(os.path.dirname(memory)):
        if not (file_name.startswith("comune.") and file_name.endswith(end)):
            continue
        orig = file_name[len("comune."):-len(end)]
        try:
            origin = shared_file.read_json(shared_origin_path(orig, dest))
            shared = shared_file.read_json(shared_path(orig, dest))
        except (OSError, ValueError):
            continue
        if not isinstance(origin, dict) or not isinstance(shared, dict):
            continue
        removed = [t for t, o in origin.items() if isinstance(o, dict) and o.get("gioco") == name and not o.get("mano")]
        if not removed:
            continue
        with open(os.path.join(copy_dir, "comune.%s-%s.tolte.json" % (orig, dest)), "w", encoding="utf-8") as f:
            json.dump({t: shared.get(t) for t in removed}, f, ensure_ascii=False, indent=2)
        shared_file.save_merging(shared_path(orig, dest), {k: v for k, v in shared.items() if k not in removed},
                                    shared)
        shared_file.save_merging(shared_origin_path(orig, dest),
                                    {k: v for k, v in origin.items() if k not in removed}, origin)
        log("  Memoria comune: tolte %d traduzioni automatiche di questo gioco (conservate nella copia)" % len(removed))


def unity_full(profile_path, log=print, network=True, anticheat=False, backend="google", outcome=None):
    """Installs/updates XUnity and pre-translates the game's texts into its cache.

    rete=True: extracts the texts from the game's files and translates those not yet in memory with the chosen
    translator (which also becomes the game profile's one, read by the translator started by the game).
    rete=False: puts back into the cache only the translations already in memory (no connection).
    PatchSmith translates the new sentences while the game is played (see profilo["dal_vivo"] and xunity_service).
    The outcome (see outcome.py) goes into esito["esito"]: live says who translates the new sentences (see
    _live).
    """
    profile = _read_profile(profile_path)
    folder, a = profile["cartella_gioco"], profile["lingua_destinazione"]
    backend = "google" if backend in (None, "xunity-google") else backend
    test = backend == "finto"
    translator = create_backend(backend, log) if network else None  # before installing: missing key = no changes
    interruption.check()
    # the translator started by the game reads the profile
    if network and not test and profile.get("backend") != backend:
        profile["backend"] = backend
        scan.save_json(profile_path, profile)
    # new sentences while playing: always to PatchSmith's translator (if the plugin compiles), even with rete=False
    # ("Reapply" after "Restore originals"); in the test it is left as it is (the test translation memory is another)
    choice = profile.get("dal_vivo") if profile.get("dal_vivo") in LIVE_CHOICES else "servizio"
    local = None if test else (profile_path if choice == "servizio" else
                                 "nessuno" if choice == "nessuno" else False)
    src_lang = adapter_unity.install(folder, profile["lingua_origine"], a, profile["nome"], log, anticheat=anticheat,
                                local=local)
    if not src_lang:
        _mark(outcome, "NULLA_DA_FARE", _reason_already_in(a), engine="unity")
        return False
    project = Project(profile_path, log, test=test)
    failed = 0
    texts = []
    # wrong XUnity cache lines written by PatchSmith (or by its live translator): they are redone
    cache = {} if test else _cache_lines_to_redo(folder, a, src_lang, project, log)
    if network:
        info = adapter_unity.analyze(folder)
        texts = adapter_unity.extract_texts(folder, info, src_lang, log)
        log("  Pre-traduzione: %d testi del gioco trovati" % len(texts))
        present = set(texts)
        texts += [t for t in cache if t not in present]
        if texts:
            try:
                failed = project.translate_texts(texts, translator, src_lang, a, redo={
                    t: "riga della cache di XUnity da correggere" for t, v in cache.items()
                    if project.memory.get(t) == v})
            except interruption.Interrupted:
                log("  BepInEx e XUnity.AutoTranslator sono gia' installati nel gioco: le frasi non pre-tradotte "
                    "verranno tradotte mentre giochi. 'Analizza e traduci' completa la pre-traduzione, 'Ripristina "
                    "originali' li toglie.")
                raise
        translations = {t: project.memory[t] for t in texts if t in project.memory}
    else:
        translations = dict(project.memory)
        texts = list(translations)
    # the lines to fix are rewritten only with a new translation that does not have the same problems
    game_names = project.active_names(list(cache))
    substitute = {t: v for t, v in cache.items() if isinstance(translations.get(t), str) and translations[t] != v
                   and not adapter_unity.check_xunity_cache({t: translations[t]}, src_lang, a, game_names)}
    if adapter_unity.live_translator(folder) == "google" and not adapter_unity.google_free():
        # Google blocked the requests during the pre-translation: XUnity must not keep trying while the game is played
        adapter_unity.configure(folder, src_lang, a, url=None, google=False)
        log("  ! Google ha appena messo in pausa le richieste: XUnity non lo usera' per le frasi nuove")
    live, note = _live(folder, profile, local)
    log("  Frasi nuove mentre giochi: %s" % LIVE.get(live, live))
    actions = {"senza_traduttore": ["installa_locale"], "plugin_nessuno": ["crea_rapporto"]}.get(note, [])
    incomplete_note = ("%d frasi non sono pre-tradotte: %s." % (failed, "verranno tradotte mentre giochi"
                                                           if live in ("locale", "online", "google") else
                                                           "resteranno in originale")) if failed else ""

    def _unity_outcome():
        _write_outcome(outcome, project, texts, True, "unity", live, incomplete_note, actions)
        if outcome is not None and note:
            outcome["esito"].extra["dal_vivo_nota"] = note

    if test:
        log("  (prova senza rete: le traduzioni finte non vanno nella cache di XUnity)")
        _unity_outcome()
        return src_lang
    info = adapter_unity.analyze(folder)
    if adapter_unity.is_running(folder, info["exe"]):
        log("  ! Il gioco e' stato aperto nel frattempo: chiudilo e premi 'Riapplica' (le traduzioni sono gia' salvate).")
        _mark(outcome, "GIOCO_APERTO", "Il gioco e' stato aperto mentre lavoravo: BepInEx e XUnity sono installati, ma "
               "le frasi pre-tradotte non sono ancora nel gioco.", engine="unity", written=True, live=live,
               actions=["chiudi_gioco", "riapplica"])
        return src_lang
    # the fixes made with "Fix translations" (also the re-translations asked there): their lines are written now,
    # with the game closed, in place of the previous ones (see corrections.to_write_path)
    from . import corrections
    pending = corrections.to_write(profile)
    current_count = adapter_unity.xunity_cache_entries(folder, a) if pending else {}
    for t, d in pending.items():
        translations[t] = d["valore"]
        present_value = current_count.get(t)
        substitute.pop(t, None)
        if present_value is not None and present_value != d["valore"] and d.get("prima") in (None, present_value):
            substitute[t] = present_value
        # otherwise: line changed after the fix (by hand in the file?), it stays as is
    counts = {}
    excluded = profile.get("caratteri_mancanti")  # letters the game's fonts lack: adapted in the cache
    new_lines = adapter_unity.write_xunity_cache(folder, a, translations, log, substitute=substitute,
                                              counts=counts, excluded=excluded)
    log("  Pre-traduzione: %d frasi aggiunte alla cache di XUnity (gia' presenti: %d)"
        % (new_lines, len(translations) - new_lines))
    if any(v.get("formato") == "unity-tabelle" for v in project.profile.get("file", [])):
        log("  Tabelle dei testi di Unity Localization, dentro il gioco:")
        project.apply()
    written_texts = 0
    if pending:
        from .characters import adapt_without
        after = adapter_unity.xunity_cache_entries(folder, a)
        # (a fix with a letter the fonts lack is written adapted: it counts as written that way too)
        written_texts = sum(1 for t, d in pending.items()
                      if after.get(t) in (d["valore"], adapt_without(d["valore"], excluded, a) or d["valore"]))
        log("  Correzioni fatte in 'Correggi traduzioni': %d scritte nella cache di XUnity%s" % (
            written_texts, ", %d lasciate com'erano (la riga e' stata cambiata dopo la correzione)"
            % (len(pending) - written_texts) if written_texts < len(pending) else ""))
        try:
            corrections.written_texts(profile, list(pending))
        except OSError as e:  # already in the cache: next time they are rewritten the same (no harm)
            log("  ! Elenco delle correzioni da scrivere non aggiornato (%s)" % e)
    replaced = max(0, counts.get("sostituite", 0) - sum(1 for t in pending if t in substitute))
    if cache:
        log("  Cache di XUnity: %d righe corrette con una traduzione nuova%s" % (
            replaced, ", %d restano com'erano (nessuna traduzione migliore per ora)"
            % (len(cache) - replaced) if len(cache) > replaced else ""))
    _unity_outcome()
    if outcome is not None and replaced:
        outcome["esito"].extra["cache_corrette"] = replaced
    if outcome is not None and written_texts:
        outcome["esito"].extra["correzioni_scritte"] = written_texts
    if failed:
        log(DETAIL + "  ! Pre-traduzione incompleta: %d frasi %s." % (failed, "verranno tradotte mentre giochi"
                                                             if live != "nessuno" else "restano in originale"))
        _after_attempts(outcome, log)
        return False
    return src_lang


def write_unity_fixes(profile, log=print):
    """Unity, with the game closed: the XUnity cache lines still to write (fixes from "Fix translations" saved with the
    game open, see corrections.to_write_path) written now, without reinstalling anything, with the same rules as
    unity_full: a line changed after the fix (by hand in the file) stays as it is, the letters the game's fonts
    lack are adapted. Used by the after-play check. Returns (written, how many were waiting)."""
    from . import corrections
    from .characters import adapt_without
    pending = corrections.to_write(profile)
    if not pending:
        return 0, 0
    folder, a = profile["cartella_gioco"], profile["lingua_destinazione"]
    current_count = adapter_unity.xunity_cache_entries(folder, a)
    translations, substitute = {}, {}
    for t, d in pending.items():
        translations[t] = d["valore"]
        present_value = current_count.get(t)
        if present_value is not None and present_value != d["valore"] and d.get("prima") in (None, present_value):
            substitute[t] = present_value
    excluded = profile.get("caratteri_mancanti")
    adapter_unity.write_xunity_cache(folder, a, translations, log, substitute=substitute, excluded=excluded)
    after = adapter_unity.xunity_cache_entries(folder, a)
    written_texts = sum(1 for t, d in pending.items()
                  if after.get(t) in (d["valore"], adapt_without(d["valore"], excluded, a) or d["valore"]))
    corrections.written_texts(profile, list(pending))
    return written_texts, len(pending)


def _cache_lines_to_redo(folder, a, src_lang, project, log):
    """{originale: traduzione nella cache} of the XUnity cache lines to fix (read-only):
    - those that adapter_unity.check_xunity_cache reports (identical to the original with several words, in
      another language, different line breaks or placeholders, a translated game name);
    - the provisional answers of the live translator (journal: 'rilassata', 'rifiutata', 'rinviata'), which XUnity
      saved as they were.
    Only those written by PatchSmith: the translation in the cache is the one from the translation memory, the machine
    translations or the journal. Lines changed by hand (or written by others) are never touched."""
    entries = adapter_unity.xunity_cache_entries(folder, a)
    if not entries or src_lang == language.AUTO:
        return {}
    our_translations, provisional = our_values(project.profile, project.memory, project._read_machine())
    flagged = adapter_unity.check_xunity_cache(entries, src_lang, project.profile["lingua_destinazione"],
                                                     project.active_names(list(entries)))
    for t, response in provisional.items():
        if entries.get(t) == response and t not in flagged:
            flagged[t] = (response, ["tradotta mentre giocavi senza tutti i controlli"])
    to_redo = {t: v for t, (v, _) in flagged.items() if v in our_translations.get(t, ())}
    if flagged:
        example = next(iter(to_redo or flagged))
        log(DETAIL + "  Cache di XUnity: %d righe da correggere (es. %r: %s); %d le ha scritte PatchSmith e si "
            "sostituiscono con una traduzione nuova, le altre (cambiate a mano o scritte da altri) restano come sono"
            % (len(flagged), example[:50], ", ".join(flagged[example][1]), len(to_redo)))
    return to_redo


def our_values(profile, memory, machine):
    """({originale: {translations written by PatchSmith}}, {originale: provisional answer from the journal}) to
    recognize the XUnity cache lines written by PatchSmith (the others, changed by hand or written by others, are not
    touched): the translation memory, the machine translations, the live translator's answers (journal), also with
    the characters the game's fonts lack already adapted (profilo["caratteri_mancanti"]). Read-only."""
    from .characters import adapt_without
    from .xunity_service import read_journal
    excluded = set(profile.get("caratteri_mancanti") or "")
    a = profile.get("lingua_destinazione") or "it"
    our_translations, provisional = {}, {}

    def add(t, v):
        values = our_translations.setdefault(t, set())
        values.add(v)
        if excluded:
            adapted_text = adapt_without(v, excluded, a)
            if adapted_text:
                values.add(adapted_text)
    for store in (memory, machine):
        for t, v in (store or {}).items():
            if isinstance(v, str):
                add(t, v)
    for line in read_journal(profile["nome"]):
        response = line.get("risposta")
        if not isinstance(response, str):
            continue
        add(line["testo"], response)
        if line.get("esito") == "ok":
            provisional.pop(line["testo"], None)  # translated properly later
        else:
            provisional[line["testo"]] = response
    return our_translations, provisional


def _extra_attempts(project):
    """For the outcome: the texts left without a translation even after the new automatic rounds (Project._recover),
    the saved translations redone and the fixes made by hand that cannot be written into the game."""
    extra = {}
    if project.last_failed:
        extra["ritentati"] = len(project.last_failed)
    if project.redone:
        extra["rifatte"] = project.redone
    problems = project.problems()
    by_hand = [t for t, v in problems.items() if v.get("dove") == "gioco" and not v.get("automatica")]
    if by_hand:
        extra["corrette_a_mano_scartate"] = len(by_hand)
    if getattr(project, "shortened", 0):
        extra["accorciate"] = project.shortened
    if getattr(project, "long_noted", 0):  # (only those of this operation: the others are already in the list)
        extra["troppo_lunghe"] = project.long_noted
    if getattr(project, "_font_adapted", 0):
        extra["adattate_font"] = project._font_adapted
    return extra


def _after_attempts(outcome, log):
    """In place of the old 'rilancia' (which repeated the same requests to the translator, with the same answers): what
    PatchSmith has already tried on its own and what is still needed."""
    extra = getattr((outcome or {}).get("esito"), "extra", None) or {}
    if extra.get("ritentati"):
        log("  %d testi restano in originale anche dopo i nuovi tentativi automatici (frasi separate, parametri "
            "diversi): li vedi con 'Mostra i rimasti' e alla prossima "
            "'Analizza e traduci' li riprovo con parametri nuovi. Una traduzione salvata che non va bene si corregge "
            "con 'Correggi traduzioni'." % extra["ritentati"])
    if extra.get("corrette_a_mano_scartate"):
        log("  %d traduzioni corrette a mano non si possono scrivere nel gioco (il motivo e' sopra): sistemale con "
            "'Correggi traduzioni'." % extra["corrette_a_mano_scartate"])


def _live(folder, profile, local):
    """(who translates the new sentences while the game is played, note) for the outcome. The first is 'locale' or
    'online' (PatchSmith's translator with the local translator, or only with the online ones), 'google' (XUnity on
    its own), 'nessuno', None (XUnity not installed). The note (outcome.LIVE_NOTES) explains the cases to fix:
    'senza_traduttore' (no translator available now), 'plugin_google'/'plugin_nessuno' (the plugin that starts
    PatchSmith's translator could not be prepared)."""
    from . import xunity_service
    kind = adapter_unity.live_translator(folder)
    if kind == "servizio":
        names = xunity_service.expected_translators(profile)
        if not names:
            return "nessuno", "senza_traduttore"
        return ("locale" if "locale" in names else "online"), None
    if local and kind in ("google", "nessuno"):  # PatchSmith's translator was wanted, but the plugin is not there
        return kind, "plugin_" + kind
    return kind, None


def memory_file(folder, dest_lang=None):
    """Path of the game's translations file, without opening it (so it opens even if it is broken)."""
    path = find_profile(folder)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            profile = json.load(f)
    except (OSError, ValueError):
        return None
    if dest_lang:
        profile = dict(profile, lingua_destinazione=dest_lang)
    if profile.get("motore") == "unity":
        return adapter_unity.translations_file(profile["cartella_gioco"], profile["nome"],
                                               profile["lingua_destinazione"])
    return memory_path(profile)


# --- checks before starting ------------------------------------------------------------------------------------

# a problem found before a long operation: code, message for the player, proposed action, data
Preflight = collections.namedtuple("Preliminare", "code message action data")
PREFLIGHT_CODES = ("GIOCO_APERTO", "LINGUA", "SOLA_LETTURA", "SPAZIO", "TRADUTTORE_IN_PAUSA", "LOCALE_ASSENTE",
                      "STESSO_GIOCO")
GAME_SPACE = 500 * 1024 * 1024  # free on the game's disk to translate or reapply (backups, BepInEx, pak...)
DATA_SPACE = 100 * 1024 * 1024  # free on the disk of PatchSmith's data (translation memory, game profiles, logs)
_WHAT = {"traduci": "tradurlo", "riapplica": "riapplicare le traduzioni", "ripristina": "rimettere i file originali"}


def check_writable(folder):
    """None if the folder can be written to (with a test file, removed right away), otherwise the error (OSError)."""
    test = os.path.join(folder, ".patchsmith-prova-scrittura-%d.tmp" % os.getpid())
    try:
        with open(test, "wb") as f:
            f.write(b"patchsmith")
    except OSError as e:
        return e
    finally:
        try:
            os.remove(test)
        except OSError:
            pass
    return None


def _in_program_files(folder):
    """True if the folder is inside C:\\Programmi (Program Files), which Windows protects."""
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(env_var)
        if root and _inside(folder, root):
            return True
    return False


def _read_only_message(folder, error):
    if not isinstance(error, PermissionError):
        return ("Non posso scrivere nella cartella del gioco (%s): %s. Controlla che il disco non sia pieno o di sola "
                "lettura, poi riprova." % (folder, error.strerror or error))
    text = "Non posso scrivere nella cartella del gioco (%s): Windows nega l'accesso.\n\n" % folder
    if _in_program_files(folder):
        text += ("Il gioco è nella cartella Programmi, che Windows protegge. Puoi spostarlo in un'altra cartella (con "
                  "Steam: Proprietà del gioco > File installati > Sposta la cartella di installazione, in una libreria "
                  "fuori da Programmi), oppure chiudere PatchSmith e riaprirlo come amministratore (tasto destro > "
                  "Esegui come amministratore).")
    else:
        text += ("Di solito la blocca l'\"Accesso controllato alle cartelle\" di Windows (Sicurezza di Windows > "
                  "Protezione da virus e minacce > Protezione ransomware) o l'antivirus. Puoi consentire Python in "
                  "quelle impostazioni, oppure chiudere PatchSmith e riaprirlo come amministratore (tasto destro > "
                  "Esegui come amministratore).")
    return text + "\n\nNon ho toccato nulla."


def _readable_size(byte):
    return "%.1f GB" % (byte / 1024 ** 3) if byte >= 1024 ** 3 else "%d MB" % (byte // 1024 ** 2)


def _looks_like_code(code):
    return bool(re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?", code or ""))


def _language_problems(src_lang, a):
    """The problems of the chosen languages (From/To): unknown, a code not in the list, the same language."""
    output = []
    for field_label, code in (("Da (lingua del gioco)", src_lang), ("A (lingua della traduzione)", a)):
        if code is None or code in language.NAMES or (code == language.AUTO and field_label.startswith("Da")):
            continue
        if _looks_like_code(code):
            output.append(Preflight(
                "LINGUA", "La lingua in %s è il codice \"%s\", che non è nell'elenco. Se è un codice valido di Google "
                "Translate puoi continuare; altrimenti scegli la lingua dall'elenco." % (field_label, code), "continua",
                {"codice": code}))
        else:
            output.append(Preflight(
                "LINGUA", "Non riconosco la lingua in %s: \"%s\". Scegli una lingua dall'elenco%s." % (
                    field_label, code, " oppure \"Rileva automaticamente\"" if field_label.startswith("Da") else ""),
                None, {"codice": code}))
    if not output and src_lang and a and src_lang != language.AUTO and language.same_language(src_lang, a):
        output.append(Preflight(
            "LINGUA", "Da e A sono la stessa lingua (%s): non c'è niente da tradurre. In Da scegli la lingua del gioco, "
            "oppure \"Rileva automaticamente\"." % language.label(a), None, {"codice": a}))
    return output


def preflight_checks(folder, backend=None, operation="traduci", src_lang=None, a=None):
    """Quick checks before a long operation (operazione: traduci, riapplica, ripristina), so as not to discover a
    problem after minutes or hours of work. Returns [Preflight(codice, messaggio, azione, dati)] in the order in
    which to show them (empty: it can start):
    - GIOCO_APERTO (action 'riprova'): a program from the game folder is open;
    - LINGUA (traduci only; None, or 'continua' for a code not in the list): From/To languages unknown or the same;
    - SOLA_LETTURA (None): the game folder cannot be written to (Program Files, controlled access, antivirus);
    - SPAZIO (traduci and riapplica; 'continua'): little space on the game's disk or on the one of PatchSmith's data;
    - TRADUTTORE_IN_PAUSA (traduci only; 'usa_locale' or 'installa_locale'): the chosen online translator is paused
      after a block (Limiter.pause);
    - LOCALE_ASSENTE (traduci only; 'installa_locale', or continue: the official language does not need translating,
      and without a translator Analyze and translate stops before modifying the game): the local translator is
      chosen, but it is not installed;
    - STESSO_GIOCO (traduci only, folder without a game profile; 'riusa'): the same game is already translated in
      another folder (copied, moved, reinstalled); dati: what same_games says.
    Reads only, except for a test file written and removed right away in the game folder. With the test translator
    ('finto') no translator checks."""
    folder = os.path.abspath(folder)
    output = []
    try:
        path = find_profile(folder)
        registered = _read_profile(path).get("cartella_gioco") if path else None
    except (OSError, ValueError, AttributeError):
        path = registered = None
    folders = list(dict.fromkeys(os.path.abspath(d) for d in (folder, registered) if d and os.path.isdir(d)))
    for d in folders:
        try:
            is_open = system.game_open(d)
        except system.SysError:
            is_open = False  # unknown: the operation's own checks will tell
        if is_open:
            output.append(Preflight("GIOCO_APERTO", "Il gioco è aperto: chiudilo e premi \"Riprova\". Mentre è aperto "
                                      "non posso %s." % _WHAT.get(operation, "lavorare sui suoi file"), "riprova",
                                      {"cartella": d}))
            break
    if operation == "traduci":
        output += _language_problems(src_lang, a)
    where = registered if registered and os.path.isdir(registered) else (adapter_unity.find_root(folder) or folder)
    error = check_writable(where)
    if error is not None:
        output.append(Preflight("SOLA_LETTURA", _read_only_message(where, error), None,
                                  {"cartella": where, "errore": str(error)}))
    if operation in ("traduci", "riapplica"):
        for disk, minimum, text in ((where, GAME_SPACE, "Sul disco del gioco (%s) restano solo %s liberi: PatchSmith "
                                      "salva una copia dei file originali e aggiunge i suoi file"),
                                     (data_folder(), DATA_SPACE, "Sul disco dove PatchSmith salva le traduzioni "
                                      "(%s) restano solo %s liberi")):
            existing = disk
            while existing and not os.path.isdir(existing) and os.path.dirname(existing) != existing:
                existing = os.path.dirname(existing)  # (the data folder may not exist yet)
            try:
                free_bytes = shutil.disk_usage(existing).free
            except (OSError, ValueError):
                continue
            if free_bytes < minimum:
                units = os.path.splitdrive(os.path.abspath(existing))[0] or existing
                output.append(Preflight("SPAZIO", (text % (units, _readable_size(free_bytes))) + ". Con il disco pieno "
                                          "l'operazione si fermerebbe a metà: libera un po' di spazio e riprova, oppure "
                                          "continua comunque.", "continua",
                                        {"cartella": existing, "liberi": free_bytes}))
    if operation == "traduci" and backend not in (None, "finto"):
        from . import ollama
        from .limiter import NAMES, Limiter
        try:
            local_ready = ollama.state() == "pronto"
        except OSError:
            local_ready = False
        if backend in NAMES:
            try:
                pause = Limiter(backend, log=None).pause()
            except (OSError, ValueError, TypeError, AttributeError):
                pause = None
            if pause:
                output.append(Preflight(
                    "TRADUTTORE_IN_PAUSA", "%s\n\nIl traduttore locale non ha limiti né blocchi: %s\n\nSe continui con "
                    "%s, uso solo le traduzioni già salvate: i testi nuovi restano in originale." % (
                        pause, "vuoi usarlo?" if local_ready else "si scarica una volta sola e poi traduce anche "
                        "senza internet. Vuoi installarlo?", NAMES[backend]),
                    "usa_locale" if local_ready else "installa_locale", {"traduttore": backend}))
        elif backend == "locale" and not local_ready:
            output.append(Preflight(
                "LOCALE_ASSENTE", "Hai scelto il traduttore locale, ma non è ancora installato: si scarica una volta "
                "sola (qualche GB: prima ti dico cosa scarica) e poi traduce senza limiti, senza blocchi e anche senza "
                "internet.\n\nSe preferisci un traduttore online, sceglilo nel menu \"Traduttore\". Se continui senza "
                "installarlo analizzo lo stesso il gioco: se ha già la sua traduzione ufficiale basta quella, "
                "altrimenti mi fermo prima di modificare qualcosa.", "installa_locale", {}))
    if operation == "traduci" and not path:
        try:
            similar = same_games(folder, a, test=backend == "finto")
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            similar = []
        if similar:
            s = similar[0]
            was_at = ("che era in %s (quella cartella non c'è più: il gioco è stato spostato?). Il suo profilo passa "
                        "a questa cartella" % s["cartella"] if s["spostato"] else "in %s" % s["cartella"])
            output.append(Preflight(
                "STESSO_GIOCO", "Questa cartella non ha ancora traduzioni, ma sembra lo stesso gioco di \"%s\", %s, già "
                "tradotto: %d testi salvati.\n\nRiuso le sue traduzioni? Così non lo traduco da capo: traduco solo i "
                "testi che mancano." % (s["nome"], was_at, s["testi"]), "riusa", s))
    return output
