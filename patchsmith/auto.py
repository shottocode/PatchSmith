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
from . import language_unreal, legacy, detection, scan, system
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
REASON_ANTICHEAT = ("The game has an anti-cheat system: adding or changing its files could get your account banned.")
REASON_IL2CPP = ("It's a Unity game compiled using IL2CPP that doesn't use Unity Localization's text tables: For now, "
                 "PatchSmith can only translate Unity Mono games and those with these tables.")
REASON_ENCRYPTED = ("The game stores its text and code in an encrypted archive (%s), which is only unlocked while the "
                    "game is running. PatchSmith does not override the protections put in place by the developers, so "
                    "this game cannot be translated.")
REASON_WEB_LIVE = ("The game text is encrypted: PatchSmith translates it for you while you're playing, directly within "
                   "the game page, without needing to open the encrypted file. The first time a phrase appears, it "
                   "will first be in English, then in Italian; after that, it will always be in Italian.")
REASON_CRC = "The game checks the integrity (CRC) of the text files: a modified file would not be loaded."
REASON_BINARY_CATALOG = ("The game lists its files with text in a binary catalog (catalog.bin, used by newer Unity "
                         "games), which PatchSmith doesn't yet know how to read. I can't tell if a modified text file "
                         "would be loaded, so I don't make any changes.")
REASON_TABLES_WITHOUT_SOURCE = ("The game uses Unity Localization text tables, but not in the original language (%s): "
                                'its languages are %s. Choose one in "From:" and try again.')
# profile["live"] of Unity games: "service" = PatchSmith translates the new sentences while the game is played
# (the plugin starts xunity_service; it is the normal choice); "google" = XUnity asks Google by itself (only written
# by hand)
LIVE_CHOICES = ("service", "google", "none")  # none: chosen at the end of "Analyze and translate"


def _normalize(path):
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _inside(child, parent):
    child, parent = _normalize(child), _normalize(parent)
    return child == parent or child.startswith(parent + os.sep)


def profiles_folder():
    return PROFILES_FOLDER or data_folder("profiles")


def list_profiles():
    for path in sorted(glob.glob(os.path.join(glob.escape(profiles_folder()), "*.json"))):
        # <name>.stato.json: fingerprints of versions 0.1-0.2; <name>.controllo.json: the after-play check
        if path.endswith((".stato.json", ".controllo.json")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = legacy.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):  # a damaged file (e.g. a list) is not a game profile: skipped like an unreadable one
            yield path, data


def find_profile(folder):
    """Game profile of the same game: same folder, or a folder that contains it or is contained in it."""
    exact, neighbors = [], []
    for path, profile in list_profiles():
        registered = profile.get("game_folder", "")
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
    executables, down to two subfolders], 'unity': 'company/product' (Unity's app.info), 'steam': appid (Steam
    manifest, if the game is installed from Steam; with steam=False it is not looked for), 'name': the folder name as
    for the game profile, without the '-2'}. It stays in the game profile (profile['identity']): a moved game is
    recognized even when the previous folder is no longer there."""
    from . import language_unity
    from . import steam as steam_module
    output = {"name": _base_name(folder)}
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
    """True if the two identities (see identity) belong to the same game. The surest data both have decides: the Unity
    product, then the Steam appid, then an executable with the same name; without any of these, the folder name. The
    '-2' is removed only from a game profile's name (from_profile: identity of an old game profile, of which only the
    name is known): 'game-2' as a folder is usually the sequel, not a copy."""
    for key in ("unity", "steam"):
        if a.get(key) and b.get(key):
            return str(a[key]).lower() == str(b[key]).lower()
    if a.get("exe") and b.get("exe"):
        return bool(set(a["exe"]) & set(b["exe"]))
    x, y = a.get("name") or "", b.get("name") or ""
    if not x or not y or "gioco" in (x, y):
        return False
    return (x == y or (bool(b.get("from_profile")) and _without_number(y) == x)
            or (bool(a.get("from_profile")) and _without_number(x) == y))


def _count_entries(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return 0
    return len(data) if isinstance(data, dict) else 0


def same_games(folder, dest_lang=None, test=False):
    """The game profiles of the same game (see same_game) in another folder, with translations saved in the
    'dest_lang' language, when this folder does not have a game profile yet: [{'profile', 'name', 'folder',
    'texts', 'moved'}], first the one with the most recently used translation memory. 'moved': that game
    profile's folder is no longer there (game moved or reinstalled elsewhere). test=True: the translation memories of
    the test translator ('fake' backend)."""
    if find_profile(folder):
        return []
    own, output = None, []
    for path, profile in list_profiles():
        registered = profile.get("game_folder")
        if not registered or not profile.get("name") or not profile.get("target_language"):
            continue
        if _inside(registered, folder) or _inside(folder, registered):
            continue
        if dest_lang and not language.same_language(profile["target_language"], dest_lang):
            continue
        memory = memory_path(profile, test)
        texts = _count_entries(memory)
        if not texts:
            continue
        moved = not os.path.isdir(registered)
        theirs = profile.get("identity") if isinstance(profile.get("identity"), dict) else None
        if theirs is None:  # game profile from before this version: from its folder, if still there, or from the name
            theirs = identity(registered, steam=False) if not moved else {"name": profile["name"], "from_profile": True}
        if own is None:
            own = identity(folder)
        if not same_game(own, theirs):
            continue
        try:
            date = os.path.getmtime(memory)
        except OSError:
            date = 0
        output.append((date, {"profile": path, "name": profile["name"], "folder": registered, "texts": texts,
                              "moved": moved}))
    return [v for _, v in sorted(output, key=lambda x: -x[0])]


def _move_profile(from_profile, folder, log):
    """The game profile's game was moved (its folder is no longer there): the game profile moves to the new folder,
    with the same translation memory. True if it did so."""
    try:
        profile = _read_profile(from_profile)
    except (OSError, ValueError):
        return False
    old_folder = profile.get("game_folder") if isinstance(profile, dict) else None
    if not old_folder or os.path.isdir(old_folder):
        return False
    profile["game_folder"] = os.path.abspath(folder).replace(os.sep, "/")
    profile["previous_folder"] = old_folder
    scan.save_json(from_profile, profile)
    log("Same game as \"%s\", which was in %s: I'm using its profile and its translations in this folder."
        % (profile.get("name"), old_folder))
    return True


def _copy_translations(from_profile, path, log):
    """The saved translations of the same game in another folder (translation memory and machine translations, the
    test ones too, and the copy of the XUnity cache) become those of this game profile, if it has none yet; the game's
    names removed or added by hand (do_not_translate) come along too. Returns the entries copied."""
    if _normalize(from_profile) == _normalize(path):
        return 0
    try:
        old, new = _read_profile(from_profile), _read_profile(path)
        if not language.same_language(old["target_language"], new["target_language"]):
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
        cache = adapter_unity._memory_folder(old["name"])
        if os.path.isdir(cache) and not os.path.exists(adapter_unity._memory_folder(new["name"])):
            shutil.copytree(cache, adapter_unity._memory_folder(new["name"]))
    except (OSError, ValueError) as e:  # (disk full, damaged file): translate what is missing, as without it
        log("  ! I haven't been able to copy all the translations of \"%s\" (%s). I'll translate the remaining ones."
            % (old["name"], e))
    # the names not to translate: those chosen for the other copy (removed or added by hand) apply here too
    seen = [n for n in old.get("found_names") or [] if isinstance(n, str)]
    if old.get("do_not_translate") is not None:
        new["do_not_translate"] = list(dict.fromkeys(
            [n for n in old.get("do_not_translate") or [] if isinstance(n, str)] +
            [n for n in new.get("do_not_translate") or [] if isinstance(n, str) and n not in seen]))
        new["found_names"] = list(dict.fromkeys(seen + [n for n in new.get("found_names") or []
                                                            if isinstance(n, str)]))
    new["translations_from"] = old["name"]
    scan.save_json(path, new)
    if copied:
        log("I'm reusing the translations of \"%s\" (same game in %s): %d texts already translated."
            % (old["name"], old.get("game_folder"), copied))
    else:
        log('  (Translations of "%s" are not needed: this folder already has its own)' % old["name"])
    return copied


def game_supported(outcome):
    """(supported, web).

    web: Electron/NW.js, a web page with no other engines, or RPG Maker MV/MZ (they have an index.html).
    supported: translatable automatically. RPG Maker MV/MZ stays experimental (confirmation needed):
    its events mix dialogues and plugin commands (often Japanese words used as
    identifiers), which the generic rules cannot tell apart.
    """
    engines = {r["name"] for r in outcome["results"] if r["section"] == "Engine"}
    containers = {r["name"] for r in outcome["results"] if r["section"] == "Container"}
    sdk = {r["name"] for r in outcome["results"] if r["section"] == "SDK"}
    rpg_web = "RPG Maker MV/MZ" in outcome["framework_web"] or "RPGMaker" in engines
    web = bool(containers & {"Electron"} or sdk & {"NWJS"} or rpg_web or engines == {"Web"})
    return web and not rpg_web, web


def unity_engine(outcome):
    """'mono', 'il2cpp' or None if the game is not Unity."""
    if outcome.get("main_engine") != "Unity":
        return None
    sdk = {r["name"] for r in outcome["results"] if r["section"] == "SDK"}
    return "il2cpp" if "UnityIL2CPP" in sdk else "mono"


def unreal_engine(outcome):
    return outcome.get("main_engine") == "Unreal"


def _prepare_unreal(folder, existing, origin, dest_lang, outcome, log, backend="google"):
    """Game profile for an Unreal game: the .locres files are translated and a patch pak is added."""
    if has_anticheat(outcome):
        log("This game has an anti-cheat system: I won't add modified paks (risk of a ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="unreal")
    info = adapter_unreal.analyze(folder)
    if not info["paks"]:
        log("I can't find the game's .pak archives.")
        return None, _mark(outcome, "NOT_SUPPORTED", "I can't find the .pak files containing the data for this Unreal "
                                                      "game.",
                            engine="unreal", actions=["create_report"])
    for problem in info["problems"]:
        log("  ! " + problem)
    if not info["versions"]:
        return None, _mark(outcome, "NOT_SUPPORTED", "The game's data archives (.pak) are in a format that PatchSmith "
                                                      "doesn't yet recognize (Unreal 5, IoStore, or encrypted "
                                                      "archives).", engine="unreal",
                            actions=["create_report"])
    # the localized texts (.locres) are looked for before creating the game profile: a game without them does not
    # leave an empty game profile, and an unreadable pak does not stop the search in the others
    skipped = []
    sources = adapter_unreal.find_locres(folder, skipped)
    if skipped:
        log("  ! %d archives not read (skipped): %s" % (len(skipped), "; ".join(skipped[:3])))
    if not sources:
        log("No localization files (.locres) found in the game's archives that PatchSmith can read.")
        if language_unreal.game_languages(folder)[0] or skipped:
            # the .locres files are there (outside the archives) or might be in the archives that cannot be read
            reason = ("The game text files (.locres) are in files that PatchSmith doesn't yet know how to translate "
                      "(outside of the .pak archives, or in archives in a newer or encrypted format).")
        else:
            reason = ("The game has no localized texts (.locres files): its texts are inside the other game data, "
                      "which PatchSmith can't translate yet.")
        return None, _mark(outcome, "NOT_SUPPORTED", reason, engine="unreal", actions=["create_report"])
    if existing:
        profile = _read_profile(existing)
        profile.update({"engine": "unreal", "source_language": origin, "target_language": dest_lang,
                        "backend": backend})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
                   "engine": "unreal", "source_language": origin, "target_language": dest_lang,
                   "backend": backend, "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Unreal: I'll translate the localization files (.locres) and put them in an additional patch pak")
    return path, outcome


def unreal_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the .locres files (network=True: new texts too; False: only from the translation memory) and writes
    the pak. backend="fake": test without network, with the separate test translation memory. The outcome goes
    into outcome["outcome"]."""
    profile = _read_profile(profile_path)
    folder, a = profile["game_folder"], profile["target_language"]
    sources, texts, culture = adapter_unreal.texts_to_translate(folder, log)
    src_lang = profile.get("source_language") or language.AUTO
    if src_lang == language.AUTO:
        base = culture.split("-")[0]
        src_lang = base if base in language.NAMES else culture
        log("  Original game language (from the .locmeta file): %s" % language.label(src_lang))
    if src_lang.split("-")[0] == a.split("-")[0]:
        log("  The game is already in %s: nothing to translate." % language.label(a))
        _mark(outcome, "NOTHING_TO_DO", _reason_already_in(a), engine="unreal")
        return False
    project = Project(profile_path, log, test=(backend == "fake"))
    failed = project.translate_texts(texts, create_backend(backend, log), src_lang, a) if network else 0
    interruption.check()
    refused = {}
    n = adapter_unreal.install(folder, a, profile["name"], project.memory, log, refused=refused)
    # translations with characters the game's fonts lack: into problemi.json as for the other engines (those written
    # by PatchSmith are redone at the next translation without those characters)
    project._refused = {t: {"reason": "characters that the game's fonts can't draw: %s" % " ".join(m),
                              "file": adapter_unreal.pak_name(a), "do_not_use": m} for t, m in refused.items()}
    project._applied = {t for t in texts if isinstance(project.memory.get(t), str) and t not in refused}
    project._record_applied()
    _write_outcome(outcome, project, texts, bool(n), "unreal")
    return bool(n) and not failed


def xna_engine(outcome):
    return outcome.get("main_engine") in ("XNA", "FNA", "MonoGame")


def _prepare_xna(folder, existing, origin, dest_lang, outcome, log, backend="google", translate_anyway=False):
    """Game profile for an XNA/FNA/MonoGame game with localized .NET resources. None if it has none at all."""
    if has_anticheat(outcome):
        log("This game has an anti-cheat system: I won't be adding any files (which could lead to a ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="xna")
    info = adapter_xna.analyze(folder)
    for problem in info["problems"]:
        log("  ! " + problem)
    if not info["resource_sets"]:
        if info["problems"]:  # there are localized resources, but not usable (e.g. signed assemblies)
            return None, _mark(outcome, "NOT_SUPPORTED", "The game's text resources are signed (strong-named "
                                                          "assemblies): PatchSmith can't translate them yet.",
                                engine="xna", actions=["create_report"])
        log("  No localized .NET resources (.resources with satellites): trying the generic text search.")
        return None
    if existing:
        profile = _read_profile(existing)
        profile.update({"engine": "xna", "source_language": origin, "target_language": dest_lang,
                        "backend": backend, "translate_anyway": translate_anyway})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
                   "engine": "xna", "source_language": origin, "target_language": dest_lang,
                   "backend": backend, "file": [], "translate_anyway": translate_anyway}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 .NET: I'll translate the text resources and add them as a satellite assembly (keeping the original "
        "game files intact)")
    return path, outcome


def xna_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the .NET resources (network=True: new texts too; False: only from the translation memory) and writes
    the satellites. The outcome goes into outcome["outcome"]."""
    profile = _read_profile(profile_path)
    folder, a = profile["game_folder"], profile["target_language"]
    resource_sets, texts = adapter_xna.texts_to_translate(folder, log)
    src_lang = profile.get("source_language") or language.AUTO
    if src_lang == language.AUTO:
        src_lang, reason = language.detect(texts)
        log("  Original game language: %s (%s)" % (language.label(src_lang), reason))
    if src_lang.split("-")[0] == a.split("-")[0]:
        log("  The game is already in %s: nothing to translate." % language.label(a))
        _mark(outcome, "NOTHING_TO_DO", _reason_already_in(a), engine="xna")
        return False
    if not profile.get("translate_anyway") and a.split("-")[0].lower() in adapter_xna.present_languages(resource_sets):
        log("  The game already has an official translation in %s: simply select it from the game's language menu."
            % language.label(a))
        _mark(outcome, "OFFICIAL", _reason_official(a), engine="xna", actions=["choose_language", "translate_anyway"])
        return False
    project = Project(profile_path, log, test=(backend == "fake"))
    failed = project.translate_texts(texts, create_backend(backend, log), src_lang, a) if network else 0
    interruption.check()
    n = adapter_xna.install(folder, src_lang, a, profile["name"], project.memory, log)
    _write_outcome(outcome, project, texts, bool(n), "xna")
    return bool(n) and not failed


def godot_engine(outcome):
    return outcome.get("main_engine") == "Godot"


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
        log("  Godot: I can't read the data package (%s)" % e)
        reason = ("The game data package (Godot) is encrypted: PatchSmith does not bypass developer protections."
                  if "encrypted" in str(e) else "I can't read the game's data package (Godot): %s." % e)
        return None, _mark(outcome, "NOT_SUPPORTED", reason, engine="godot", actions=["create_report"])
    log(DETAIL + "  Godot %s, package %s, translation tables: %s" % (
        pck.version(), os.path.relpath(located[0], folder), ", ".join(sorted(all_items)) or "none"))
    if not all_items:
        return None, None
    if any(language.same_language(c, dest_lang) for c in all_items):
        log("The game already has a translation in %s: simply select it from the game's language menu."
            % language.label(dest_lang))
        return None, _mark(outcome, "OFFICIAL", _reason_official(dest_lang), engine="godot",
                            actions=["choose_language"])
    source = origin if origin and origin != language.AUTO else "en"
    code = language.best(all_items, source)
    if code is None:
        return None, _mark(outcome, "NOT_SUPPORTED", REASON_TABLES_WITHOUT_SOURCE % (
            language.label(source), ", ".join(_language_name(c) for c in sorted(all_items))), engine="godot",
            actions=["create_report"])
    entry = {"pck": os.path.relpath(located[0], folder).replace(os.sep, "/"), "table": all_items[code],
            "language": code}
    if existing:
        profile = _read_profile(existing)
        profile.update({"engine": "godot", "godot": entry, "file": [], "source_language": origin,
                        "target_language": dest_lang, "backend": backend,
                        "game_folder": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"), "engine": "godot",
                   "godot": entry, "source_language": origin, "target_language": dest_lang,
                   "backend": backend, "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Godot: Text tables in %d languages (%s); I will translate the one in %s, without modifying the game "
        "files"
        % (len(all_items), ", ".join(sorted(all_items)), language.label(code)))
    return path, outcome


def godot_complete(profile_path, log=print, network=True, backend="google", outcome=None):
    """Translates the table of the source language (network=True) and puts override.cfg, the script and the translations
    next to the executable (see adapter_godot). network=False: only the translations already in memory (Reapply)."""
    profile = _read_profile(profile_path)
    folder, a, entry = profile["game_folder"], profile["target_language"], profile["godot"]
    if system.game_open(folder):
        log("The game is open: close it and try again.")
        _mark(outcome, "GAME_OPEN", "The game is open.", engine="godot", actions=["close_game"])
        return False
    pck_path = os.path.join(folder, *entry["pck"].split("/"))
    _language, texts = adapter_godot.table_texts(adapter_godot.Pck(pck_path).read_file(entry["table"]))
    texts = list(dict.fromkeys(t for t in texts if t.strip()))
    log("  Text in the game table: %d" % len(texts))
    src_lang = entry["language"]
    test = backend == "fake"
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
        log("  ! The game was opened in the meantime: close it and press 'Reapply' (the translations are already "
            "saved).")
        _mark(outcome, "GAME_OPEN", "The game was opened while I was working: the translations are saved but not in "
                                       "the game yet.", engine="godot", actions=["close_game", "reapply"])
        return False
    adapter_godot.install(os.path.dirname(pck_path), src_lang, translations, a, log)
    _write_outcome(outcome, project, texts, True, "godot",
                     reason="In the game, keep the %s language selected: the texts appear in %s." % (
                         language.label(src_lang), language.label(a)))
    if failed:
        _after_attempts(outcome, log)
    return not failed


def gamemaker_engine(outcome):
    return outcome.get("main_engine") == "GameMaker"


def _prepare_gamemaker(folder, existing, origin, dest_lang, outcome, log, backend="google",
                       translate_anyway=False):
    """Game profile for a GameMaker game with external localization files. None if it has none."""
    roots = gamemaker.roots(folder)
    if len(roots) > 1:
        log("The folder contains %d GameMaker games: choose the folder of just one." % len(roots))
        return None, _mark(outcome, "WRONG_FOLDER", "The folder contains %d GameMaker games: choose the folder "
                                                          "of just one." % len(roots), engine="gamemaker")
    if roots and os.path.normcase(roots[0]) != os.path.normcase(os.path.abspath(folder)):
        log("  Game folder: %s" % roots[0])
        folder = roots[0]
        existing = existing or find_profile(folder)
    found = adapter_gamemaker.localization_file(folder)
    sources, languages, base = adapter_gamemaker.choose_sources(found, origin, folder)
    fmt = "lines"
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
            log("  Text table with one column per language: %s" % ", ".join(sources))
    if not sources:
        log("  No recognized localization file found: attempting generic text search." if not found else
            "  There are localization files, but I don't recognize the ones for the original language: I'll try a "
            "generic search.")
        return None
    if dest_lang.split("-")[0].lower() in languages and not translate_anyway:
        log("The game already has a translation in %s: simply select it from the game's language menu."
            % language.label(dest_lang))
        return None, _mark(outcome, "OFFICIAL", _reason_official(dest_lang), engine="gamemaker",
                            actions=["choose_language", "translate_anyway"])
    if has_anticheat(outcome):
        log("This game has an anti-cheat system: I don't modify its files (which could lead to a ban).")
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="gamemaker")
    log("  Original-language text files: %d (other languages in the game: %s)"
        % (len(sources), ", ".join(sorted(languages - {base})) or "none"))
    if base and base != "en" and "en" in languages:
        log("  ! I'm translating the files into %s: In the game, you'll need to select that language, not English."
            % language.label(base))
    if existing:  # files translated by a previous game profile (e.g. experimental) that must not be translated now
        _put_back_excluded(folder, [v.get("path") for v in _read_profile(existing).get("file", [])
                                    if v.get("path") and v.get("path") not in sources], log)
    if fmt == "lines":
        entries = [{"path": r, "format": "lines"} for r in sources]
    elif fmt == "csv":
        entries = [{"path": r, "format": "csv", "localization": True,
                 "column": tables[r].header[tables[r].source(origin)[0]].strip().lstrip("\ufeff")}
                for r in sources]
    else:
        entries = _json_entries(folder, found, sources, log, dest_lang)
    officials = adapter_gamemaker.official_letters(folder, found, sources) if fmt != "csv" else \
        _table_letters(tables, origin)
    if existing:
        profile = _read_profile(existing)
        profile.update({"engine": "gamemaker", "source_language": origin, "target_language": dest_lang,
                        "backend": backend, "file": entries, "file_language": base, "official_letters": officials,
                        "game_folder": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
                   "engine": "gamemaker", "source_language": origin, "target_language": dest_lang,
                   "backend": backend, "file": entries, "file_language": base, "official_letters": officials}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 GameMaker: I translate the original language text files (automatic backup of the originals)")
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
        others = []  # (code, relative path, data) of the official translations of the same file
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
        codes = adapter_gamemaker.uppercase_codes(texts, mapping, conventions["bullet"]) \
            if style == "lowercase" else []
        dedicated = adapter_gamemaker.dedicated_fonts(data_win, [c for c, _, _ in others]) if data_win else set()
        same_font = [p for (c, _, _), p in zip(others, other_flat) if c.split("-")[0] not in dedicated]
        entry = {"path": rel, "format": "json", "localization": True,
                "include": adapter_gamemaker.json_patterns(data), "exclude": [],
                "line_break": conventions["line_break"], "bullet": conventions["bullet"],
                "capitals": style, "codes": codes, "references": {c: r for c, r, _ in others},
                "limits": adapter_gamemaker.line_limits(flat, same_font, conventions["line_break"])
                if conventions["line_break"] else {}}
        rel_folder = os.path.dirname(rel)
        relative_to_game = os.path.relpath(os.path.join(folder, *rel_folder.split("/")),
                                            os.path.dirname(win_path)).replace(os.sep, "/") if win_path else ""
        if dest_lang and rel_folder and data_win and not relative_to_game.startswith("..") and \
                gamemaker.folder_mask(os.path.dirname(win_path), relative_to_game,
                                            os.path.splitext(rel)[1], data_win):
            name = adapter_gamemaker.new_language_name(dest_lang, os.path.join(folder, *rel_folder.split("/")),
                                                       os.path.splitext(rel)[1])
            if name:
                entry["new_language"] = rel_folder + "/" + name
        entries.append(entry)
        mentioned = []
        if conventions["line_break"]:
            mentioned.append("'%s' = line break" % conventions["line_break"])
        if conventions["bullet"]:
            mentioned.append("'%s' = symbol at the beginning of a paragraph" % conventions["bullet"])
        if mentioned:
            log("  Game conventions in %s: %s (followed in the translation)" % (rel, ", ".join(mentioned)))
        if style == "lowercase":
            log("  The game writes in lowercase%s: the translation does the same" % (
                " and uses capital letters as icons (%s, protected in the translation)" % ", ".join(codes)
                if codes else ""))
        if mapping:
            log("  Game bitmap font: %d characters (the letters it lacks are adapted)" % len(mapping))
        if entry.get("new_language"):
            log("  The game automatically loads the languages in %s/: the translation becomes a new language (%s), "
                "while the original remains unchanged" % (rel_folder, entry["new_language"]))
    return entries


def folder_languages(folder, relative):
    """The names (without extension) of the language files of the same family as 'relative' (e.g. english,
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
            log("  ! %s had been translated before but must not be translated: use 'Restore originals' (with the game "
                "closed)" % rel)
        else:
            log("  %s had been translated before but must not be translated: original put back" % rel)


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
        code = adapter_gamemaker.path_language(c["path"])
        if code:
            families.setdefault(adapter_gamemaker.family(c["path"]), []).append((c, code))
    base = (origin or "auto").split("-")[0].lower()
    remove, target_files = set(), []
    for fam, items in sorted(families.items()):
        languages = {code.split("-")[0] for _, code in items}
        if len(languages) < 2:
            continue
        target_files += [c["path"] for c, code in items if dest_lang and language.same_language(code, dest_lang)]
        keep_lang = "en" if base == "auto" else base
        kept_items = [c for c, code in items if code.split("-")[0] == keep_lang]
        if not kept_items:
            continue
        if base == "auto":  # an English much smaller than the other files is not necessarily the original language
            largest = max(c.get("readable_strings", 0) for c, _ in items)
            if max(c.get("readable_strings", 0) for c in kept_items) < 0.8 * largest:
                continue
        dropped = []
        for c, code in items:
            if code.split("-")[0] == keep_lang:
                continue
            detected = _texts_language(folder, c["path"])
            if detected is None or language.same_language(detected, keep_lang):
                continue  # the texts are not in another language: it is not a translation, it stays
            dropped.append(c["path"])
        if dropped:
            remove.update(dropped)
            log("  Files per language (%s): I only translate the ones in %s; the other %d are the game's official "
                "translations" % (fam, language.label(keep_lang), len(dropped)))
    return [c for c in candidates if c["path"] not in remove], remove, target_files


def has_anticheat(outcome):
    return any(r["section"] == "AntiCheat" for r in outcome["results"])


def _read_profile(path):
    with open(path, encoding="utf-8") as f:
        return legacy.load(f)


def _language_name(code):
    """'French (fr)' for a code of the text tables ('fr', 'zh-cn', 'pt-br'); the code itself if unknown."""
    known_code = next((c for c in language.NAMES if c.lower() == (code or "").lower()), None)
    return language.label(known_code or language.best(language.NAMES, code) or code) if code else ""


def _crc_state(bundle_path):
    """adapter_unity_loc.bundle_crc, but also looking at the catalog's original: after the first translation the
    catalog.json in the game is the one written by PatchSmith (CRC already 0, "unlocked"), but the game profile entry
    must keep naming it. Without this, a new "Analyze and translate" would remove it from the game profile: "Reapply"
    after the game put back its files (Steam verification) would rewrite the bundle with the CRC back in the catalog,
    and the game would no longer load it."""
    state, catalog = adapter_unity_loc.bundle_crc(bundle_path)
    if state == "unlocked" and catalog and os.path.isfile(catalog + BACKUP_SUFFIX):
        if adapter_unity_loc.bundle_crc(bundle_path, read_from=catalog + BACKUP_SUFFIX)[0] == "checked":
            state = "checked"
    return state, catalog


def _table_entries(folder, bundle, log):
    """Game profile entries to rewrite these text table bundles, or "binary" / "crc" if the Addressables catalog
    checks their fingerprint and it cannot be removed."""
    entries = []
    for rel in bundle:
        entry = {"path": rel, "format": "unity-tables"}
        state, catalog = _crc_state(os.path.join(folder, *rel.split("/")))
        if state == "binary":
            log("  The game lists its bundles in a binary catalog (catalog.bin) where I can't reliably find %s: I'm "
                "not changing the text tables." % rel.split("/")[-1])
            return "binary"
        if state == "checked":
            entry["catalog"] = os.path.relpath(catalog, folder).replace(os.sep, "/")
            log(DETAIL + "  The game's catalog checks the CRC of %s: I remove it only for this file (%s with backup, "
                         "'Restore originals' puts it back)" % (rel.split("/")[-1], os.path.basename(catalog)))
        elif state == "unknown" and adapter_unity_loc.checked_crc(os.path.join(folder, *rel.split("/"))):
            log("  The game checks the hashes of its bundles and I can't find the one for %s in the catalog: I can't "
                "modify the text tables." % rel.split("/")[-1])
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
        log("  The text tables stay untranslated in the files: only XUnity translates them while you play.")
        return []
    log("  Unity Localization: also translating the %s text tables inside their bundle (automatic backup): this way "
        "the animated dialogues also appear in Italian" % language.label(source))
    return entries


def _prepare_unity_tables(folder, existing, origin, dest_lang, log):
    """Game profile for a Unity game with Unity Localization text tables (IL2CPP too): the tables in the source
    language (with "auto", English) are translated inside their bundles, recognized by their content too (see
    adapter_unity_loc.table_bundles). Returns the game profile's path, or:
    - "official": the game already has the target language;
    - "crc": the catalog checks the bundles' fingerprint and it cannot be removed for the bundle to translate;
    - "binary": the catalog is catalog.bin (it is unknown whether it checks the fingerprint);
    - ("language", languages): there are tables, but not in the source language;
    - None: the game does not use Unity Localization.
    If the catalog (catalog.json) checks the fingerprint of a bundle to translate, the game profile entry also has
    "catalog": Project sets that bundle's CRC to 0 there before rewriting it (with backup, see
    Project._remove_crc)."""
    all_items = adapter_unity_loc.table_bundles(folder)
    if not all_items:
        return None
    if any(language.same_language(c, dest_lang) for c in all_items):
        log("The game already has a translation in %s: simply select it from the game's language menu."
            % language.label(dest_lang))
        return "official"
    source = origin if origin and origin != language.AUTO else "en"
    code = language.best(all_items, source)
    if code is None:
        log("  Unity Localization: text tables in %s, but not in %s." % (", ".join(sorted(all_items)),
                                                                            language.label(source)))
        return "language", sorted(all_items)
    entries = _table_entries(folder, all_items[code], log)
    if isinstance(entries, str):
        return entries
    log("  Unity Localization: text tables in %d languages (%s); translating the %s one (%s) inside its bundle "
        "(automatic backup), without mods" % (len(all_items), ", ".join(sorted(all_items)), language.label(source),
                                            ", ".join(r.split("/")[-1] for r in all_items[code])))
    if existing:
        profile = _read_profile(existing)
        profile.update({"engine": "unity-localization", "source_language": origin, "target_language": dest_lang,
                        "file": entries, "game_folder": os.path.abspath(folder).replace(os.sep, "/")})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
                   "engine": "unity-localization", "source_language": origin, "target_language": dest_lang,
                   "backend": "local", "file": entries}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Unity Localization: I will translate the text tables (in the game, please leave %s selected)"
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
        log("This game has an anti-cheat system: %s (risk of being banned)." % (
            "I won't modify its files" if il2cpp else "I'm not installing a mod loader"))
        return None, _mark(outcome, "ANTICHEAT", REASON_ANTICHEAT, engine="unity")
    if il2cpp:
        root = adapter_unity.find_root(folder) or folder
        tables = _prepare_unity_tables(root, existing or find_profile(root), origin, dest_lang, log)
        if tables == "official":
            return None, _mark(outcome, "OFFICIAL", _reason_official(dest_lang), engine="unity-localization",
                                actions=["choose_language"])
        if tables == "crc":
            return None, _mark(outcome, "NOT_SUPPORTED", REASON_CRC, engine="unity-localization",
                                actions=["create_report"])
        if tables == "binary":
            return None, _mark(outcome, "NOT_SUPPORTED", REASON_BINARY_CATALOG, engine="unity-localization",
                                actions=["create_report"])
        if isinstance(tables, tuple):  # tables, but not in the source language
            source = origin if origin and origin != language.AUTO else "en"
            return None, _mark(outcome, "NOT_SUPPORTED", REASON_TABLES_WITHOUT_SOURCE % (
                language.label(source), ", ".join(dict.fromkeys(_language_name(c) for c in tables[1]))),
                engine="unity-localization", actions=["create_report"])
        if tables:
            return tables, outcome
        log("Unity IL2CPP game without Unity Localization tables: Not yet supported (BepInEx 6 would be required).")
        return None, _mark(outcome, "NOT_SUPPORTED", REASON_IL2CPP, engine="unity", actions=["create_report"])
    root = adapter_unity.find_root(folder)
    if not root:
        log("I can't find the Unity game executable (the .exe file with the _Data folder next to it).")
        return None, _mark(outcome, "WRONG_FOLDER", "I can't find the Unity game executable (the .exe file with "
                                                          "the folder next to it that ends with _Data). Please choose "
                                                          "the folder where the executable is located.",
                            engine="unity")
    if _normalize(root) != _normalize(folder):
        log("Game folder: %s" % root)
        folder = root
        existing = existing or find_profile(folder)
    if existing:
        profile = _read_profile(existing)
        if profile.get("engine") != "unity" and profile.get("file"):
            log("  (The profile also included text file translations: with Unity, translations are only done within "
                "the game)")
        profile.update({"engine": "unity", "source_language": origin, "target_language": dest_lang,
                        "game_folder": os.path.abspath(folder).replace(os.sep, "/"), "backend": backend})
        if profile.get("live") not in LIVE_CHOICES:
            profile["live"] = "service"
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
                   "engine": "unity", "source_language": origin, "target_language": dest_lang,
                   "backend": backend, "live": "service", "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    profile["file"] = _mono_tables(folder, origin, dest_lang, log)
    scan.save_json(path, profile)
    log("== 2/3 Unity Mono: The text will be translated within the game by XUnity.AutoTranslator (with BepInEx)")
    return path, outcome


def _merge(old, new, log):
    """Adds the new files to the existing game profile and removes those the game no longer has."""
    folder = old["game_folder"]
    kept_items = []
    for f in old["file"]:
        if os.path.exists(os.path.join(folder, *f["path"].split("/"))):
            kept_items.append(f)
        else:
            log("  - %s no longer exists in the game: removed from profile" % f["path"])
    present = {f["path"] for f in kept_items}
    added = [f for f in new["file"] if f["path"] not in present]
    old["file"] = kept_items + added
    for key in ("source_language", "target_language", "backend"):
        old[key] = new[key]
    return old, added


def prepare(folder, origin="auto", dest_lang=None, backend="google", log=print, force=False, regenerate=False,
            translate_anyway=False, consent=False):
    """Detects the engine, finds the texts and creates/updates the game profile. Returns (profile_path, outcome)."""
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
            registered = legacy.load(f).get("game_folder")
        for d in dict.fromkeys(os.path.abspath(x) for x in (folder, registered) if x):
            if official_language.is_set(d) and official_language.restore(d, log):
                log("  Automatic translation replaces the original game language.")
    return path, outcome


def _names_in_profile(path, log):
    """The game's names not to translate (studio and title: see names.py) in the game profile,
    profile["do_not_translate"], together with those already there (added by hand too). Project protects them like the
    game's codes. The names found also stay in profile["found_names"]: a name found once and then removed by hand from
    "do_not_translate" (for example a title you want translated) does not come back."""
    try:
        profile = _read_profile(path)
        found = names.names_of_game(profile["game_folder"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    already = [n for n in profile.get("do_not_translate") or [] if isinstance(n, str) and n.strip()]
    seen = profile.get("found_names") if isinstance(profile.get("found_names"), list) else []
    new_items = {"do_not_translate": list(dict.fromkeys(already + [n for n in found if n not in seen])),
             "found_names": list(dict.fromkeys(seen + found))}
    if any(profile.get(k, []) != v for k, v in new_items.items()):
        profile.update(new_items)
        scan.save_json(path, profile)
    items = new_items["do_not_translate"]
    if items:
        log("  Game names kept as they are in the translation: %s" % ", ".join(items))


def _profile_identity(path):
    """The game's identity (see identity) in the game profile: used to recognize it if you move or copy it elsewhere."""
    try:
        profile = _read_profile(path)
        new_value = identity(profile["game_folder"])
    except (OSError, ValueError, KeyError, TypeError):
        return
    if profile.get("identity") != new_value:
        profile["identity"] = new_value
        scan.save_json(path, profile)


def _prepare(folder, origin, dest_lang, backend, log, force, regenerate, translate_anyway, consent):
    folder = os.path.abspath(folder)
    if not os.path.isdir(folder):
        raise ValueError("The folder doesn't exist: %s" % folder)
    dest_lang = dest_lang or language.windows_language()
    existing = find_profile(folder)
    if existing:
        with open(existing, encoding="utf-8") as f:
            registered = legacy.load(f)["game_folder"]
        if _normalize(registered) != _normalize(folder):
            log("This game is already registered with the %s folder; I'm using that one." % registered)
            folder = registered

    log("== 1/3 Engine detection")
    outcome = detection.detect(folder)
    detection.print_summary(outcome, lambda m: log(DETAIL + "  " + m))
    log("  Engine: %s" % (outcome.get("main_engine") or "not recognized"))
    is_supported, web = game_supported(outcome)
    if not translate_anyway:
        # does the game already have the official translation? Then that one is used, not a machine translation
        official = official_language.detect(folder, dest_lang, outcome.get("main_engine"), log)
        if official["present"]:
            log("== Official language")
            interruption.check()
            done, setting = official_language.set_(folder, official, dest_lang, log, consent)
            lines = official_language.message(official, dest_lang, setting, done)
            installed_ok = installed_translation(folder)
            if installed_ok:
                lines.append("PatchSmith's automatic translation is still installed: if the game shows it instead of "
                             "the official language, press 'Restore originals' and then 'Analyze and translate' again.")
            for line in lines:
                log("  " + line)
            outcome = dict(outcome, official_language=official, official_language_lines=lines,
                         translation_installed=installed_ok, engine_supported=bool(is_supported or web),
                         language_set=done, needs_consent=done is None)
            return None, _mark(outcome, "OFFICIAL", lines[0] if lines else "", actions=["choose_language"],
                                extra={"official_language": True, "lines": lines,
                                       "is_supported": bool(is_supported or web), "needs_consent": done is None,
                                       "is_set": done})
        for note in official["notes"]:
            log("  Note: " + note)
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
        return None, _mark(outcome, "NOT_SUPPORTED", REASON_ENCRYPTED % encrypted, engine="web",
                           actions=["create_report"])
    log("== 2/3 Searching for texts")
    candidates, others = scan.scan_folder(folder, log)
    candidates, dropped, target_files = _original_language_only(candidates, folder, origin, dest_lang, log)
    # CSV tables with the target language column filled: official translation
    target_files += [c["path"] for c in candidates if c.get("format") == "csv" and dest_lang and any(
        language.same_language(x, dest_lang) for x in c.get("languages", ()) if x != c.get("language"))]
    if target_files and not translate_anyway:
        log("The game already has a translation in %s (%s): choose it in the game's language menu."
            % (language.label(dest_lang), ", ".join(sorted(target_files)[:2])))
        return None, _mark(outcome, "OFFICIAL", _reason_official(dest_lang), engine="web" if web else None,
                            actions=["choose_language", "translate_anyway"])
    scan.print_summary(candidates, others, lambda m: log(DETAIL + "  " + m))
    name = os.path.splitext(os.path.basename(existing))[0] if existing else name_for_folder(folder)
    # the detected engine stays in the game profile: in GameMaker and XNA the translations adapt to the font characters
    new = scan.create_profile(name, folder, candidates, origin, dest_lang, backend,
                                   outcome["web_roots"] if web else (), engine=outcome.get("main_engine"),
                                   log=log)

    if not new["file"]:
        log("No translatable text found using current tools.")
        for c in outcome["advice"]:
            log("  * " + c)
        return None, _mark(outcome, "NOT_SUPPORTED", _reason_no_text(outcome, folder), actions=["create_report"],
                            engine=(outcome.get("main_engine") or "").lower() or None)
    if not is_supported and not force:
        log("Engine %s: Not yet supported automatically." % (outcome["main_engine"] or "not recognized"))
        for c in outcome["advice"]:
            log("  * " + c)
        log("I did find %d files containing text: you can translate them experimentally." % len(new["file"]))
        engine = outcome["main_engine"] or "not recognized"
        return None, _mark(dict(outcome, experimental_possible=True), "NOT_SUPPORTED",
                            "The game engine (%s) is not supported automatically yet." % engine,
                            engine=(outcome.get("main_engine") or "").lower() or None,
                            actions=["experimental", "create_report"], extra={"detected_engine": engine})

    if existing and not regenerate:
        with open(existing, encoding="utf-8") as f:
            profile, added = _merge(legacy.load(f), new, log)
        for key in ("engine", "game_engine"):  # game profiles created before the engine stayed in the profile
            if new.get(key) and not profile.get(key):
                profile[key] = new[key]
        excluded = [f["path"] for f in profile["file"] if f["path"] in dropped]
        if excluded:
            profile["file"] = [f for f in profile["file"] if f["path"] not in dropped]
            log("  Removed from profile (official game translations): %s" % ", ".join(excluded))
            _put_back_excluded(folder, excluded, log)
        log(DETAIL + "Existing profile updated: %s (%d new files)" % (existing, len(added)))
        path = existing
    else:
        profile = new
        path = existing or os.path.join(profiles_folder(), name + ".json")
        log(DETAIL + "Profile created: %s" % path)
    scan.save_json(path, profile)
    for f in profile["file"]:
        kind = "interface (menu)" if f.get("format") == "web-ui" else \
            "text table (column %s)" % f.get("column") if f.get("format") == "csv" else \
            "%s (%d fields)" % ("language file" if f.get("localization") else "data", len(f.get("include", [])))
        log(DETAIL + "  - %s: %s" % (f["path"], kind))
    log("  Files with texts to translate: %d" % len(profile["file"]))
    if profile.get("game_engine"):
        log("  The translations adapt to the characters that the game's fonts are able to draw")
    return path, outcome


def _prepare_web_live(folder, existing, origin, dest_lang, backend, log):
    """Game profile of an Electron game with encrypted files but a plain preload.js (see adapter_web_live): no files
    to translate, the texts are translated while the game is played. Returns the game profile's path."""
    if existing:
        profile = _read_profile(existing)
        if profile.get("file"):  # (e.g. files of a Steam emulator translated by mistake before): no longer used
            log(DETAIL + "  Removed from profile: %s" % ", ".join(f["path"] for f in profile["file"]))
        profile.update({"engine": "web-live", "file": [], "source_language": origin, "target_language": dest_lang,
                        "backend": backend, "live": "service"})
        path = existing
    else:
        name = name_for_folder(folder)
        profile = {"name": name, "game_folder": os.path.abspath(folder).replace(os.sep, "/"), "engine": "web-live",
                   "source_language": origin, "target_language": dest_lang, "backend": backend,
                   "live": "service", "file": []}
        path = os.path.join(profiles_folder(), name + ".json")
    scan.save_json(path, profile)
    log("== 2/3 Encrypted texts: PatchSmith translates them while you play, in the game page (the encrypted file is "
        "not touched)")
    return path


def live_translation(folder):
    """(profile, active) if the folder's game has translation while playing (Unity, live web), otherwise (profile or
    None, None)."""
    path = find_profile(folder)
    if not path:
        return None, None
    try:
        profile = _read_profile(path)
    except (OSError, ValueError):
        return None, None
    if profile.get("engine") not in ("unity", "web-live"):
        return profile, None
    return profile, profile.get("live") != "none"


def set_live(folder, enable, log=print):
    """Turns translation while playing on or off (the choice at the end of "Analyze and translate"): it stays in the
    game profile and is applied to the game right away if it is closed (if it is open, its translator stops and the
    rest applies from the next check at startup or from "Reapply"). Returns True if applied in the game."""
    from . import xunity_service
    path = find_profile(folder)
    profile = _read_profile(path)
    profile["live"] = "service" if enable else "none"
    scan.save_json(path, profile)
    if not enable and xunity_service.stop(profile["name"]):
        log("Game translator stopped.")
    game = profile["game_folder"]
    if system.game_open(game):
        log("The game is open: the choice applies from the next time you start it.")
        return False
    if profile.get("engine") == "web-live":
        web_live_full(path, log=lambda m: log(DETAIL + m) if m.strip() else None, network=False,
                          backend=None)
    else:
        src_lang = profile.get("source_language")  # the one XUnity is already configured with, if "auto"
        ini = adapter_unity.read_ini(os.path.join(game, *adapter_unity.CONFIG_XUNITY.split("/")))
        if src_lang in (None, language.AUTO):
            src_lang = (ini.get("General") or {}).get("FromLanguage") or "en"
        adapter_unity.install(game, src_lang, profile["target_language"],
                               profile["name"], lambda m: log(DETAIL + m),
                               local=path if enable else "none")
    log("Translation while playing: on." if enable else
        "Translation while playing: disabled (only previously saved translations).")
    return True


def web_live_full(profile_path, log=print, network=True, backend="google", outcome=None):
    """Installs (or updates) the live translation of a web game with encrypted files: the script in preload.js and the
    translations already made (the game's translation memory). No pre-translation: the texts are known only while the
    game is played."""
    profile = _read_profile(profile_path)
    folder, a = profile["game_folder"], profile["target_language"]
    src_lang = profile.get("source_language")
    # (the texts are encrypted: the language cannot be detected first)
    src_lang = "en" if src_lang in (None, language.AUTO) else src_lang
    if system.game_open(folder):
        log("The game is open: close it and try again.")
        _mark(outcome, "GAME_OPEN", "The game is open.", engine="web", actions=["close_game"])
        return False
    if network and backend not in (None, "fake") and profile.get("backend") != backend:
        profile["backend"] = backend  # used by the translator started by the game
        scan.save_json(profile_path, profile)
    project = Project(profile_path, log, test=backend == "fake")
    active = profile.get("live") != "none"
    n = adapter_web_live.install(folder, profile_path, profile["name"], src_lang, a, project.memory, log,
                                  active=active)
    from . import xunity_service
    expected = xunity_service.expected_translators(profile) if active else []
    live = "local" if "local" in expected else ("online" if expected else "none")
    log("  Phrases while playing: %s" % LIVE.get(live, live))
    _mark(outcome, "OK", REASON_WEB_LIVE, engine="web", written=True, translated_items=n, live=live,
           actions=["install_local"] if live == "none" else [])
    return True


def analyze_and_translate(folder, origin="auto", dest_lang=None, backend="google", log=print,
                       force=False, regenerate=False, translate_anyway=False, consent=False, reuse=None):
    """Complete procedure. Returns (profile_path, completed, outcome); the outcome for the interface (code, counts,
    reason, actions: see outcome.py) is in outcome["outcome"]. reuse: the game profile of the same game in another
    folder (see same_games) whose translations to reuse: if that folder is no longer there the game profile moves to
    this one, otherwise its translations are copied into the new game profile before translating."""
    moved = bool(reuse) and _move_profile(reuse, folder, log)
    path, outcome = prepare(folder, origin, dest_lang, backend, log, force, regenerate, translate_anyway,
                              consent)
    interruption.check()  # stopped during the analysis: no questions or writes afterwards
    if not path:
        if "outcome" not in outcome:  # every exit has its own outcome: this is only a fallback
            _mark(outcome, "NOT_SUPPORTED", "I haven't found a safe way to translate this game.",
                   actions=["create_report"])
        return None, False, outcome
    if reuse and not moved:
        _copy_translations(reuse, path, log)
    profile = _read_profile(path)
    if profile.get("engine") == "godot":
        log("== 3/3 Translation of the text table (game files are not modified)")
        is_ready = godot_complete(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Done. Start the game with the %s language selected: the texts appear in %s." % (
                language.label(profile["godot"]["language"]), language.label(profile["target_language"])))
        elif adapter_godot.manifest_present(os.path.dirname(os.path.join(
                profile["game_folder"], *profile["godot"]["pck"].split("/")))):
            log("Partly done: some texts were left untranslated (see above).")
        return path, bool(is_ready), outcome
    if profile.get("engine") == "web-live":
        log("== 3/3 Live translation (automatic backup of preload.js)")
        is_ready = web_live_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Done. Start the game: the phrases get translated while you play (the first time they appear in "
                "English for a moment).")
        return path, bool(is_ready), outcome
    if profile.get("engine") == "unity":
        log("== 3/3 Installation of BepInEx + XUnity.AutoTranslator (no game files are modified)")
        is_ready = unity_full(path, log, network=True, anticheat=has_anticheat(outcome), backend=backend,
                                outcome=outcome)
        if is_ready is False and adapter_unity.manifest_present(profile["game_folder"]):
            log("Partially complete: XUnity is installed, but the pre-translation is not yet complete (see above).")
        if is_ready:
            no_live = getattr(outcome.get("outcome"), "live", None) == "none"
            log("Done. Start the game: the pre-translated phrases appear immediately in Italian; the others (e.g., "
                "texts")
            log("generated on the fly) %s. In the game: ALT+T toggles original/translated."
                % ("for now they stay untranslated (see above)" if no_live else "they are being translated live"))
        return path, bool(is_ready), outcome
    if profile.get("engine") == "unreal":
        log("== 3/3 Translating the .locres files and writing the patch pak (the original paks are not touched)")
        is_ready = unreal_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Done. The original language must be selected in the game (usually English): the translation")
            log("takes its place. To go back to the original: 'Restore originals'.")
        elif adapter_unreal.manifest_present(profile["game_folder"]):
            log("Partly done: some texts were left untranslated (see above).")
            _after_attempts(outcome, log)
        return path, bool(is_ready), outcome
    if profile.get("engine") == "xna":
        log("== 3/3 Translation of .NET resources and satellite creation (game files are not modified)")
        is_ready = xna_full(path, log, network=True, backend=backend, outcome=outcome)
        if is_ready:
            log("Done. In the game, keep (or select) the original language, usually English: the translation")
            log("takes its place. To go back to the original: 'Restore originals'.")
        elif adapter_xna.manifest_present(profile["game_folder"]):
            log("Partly done: some texts were left untranslated (see above).")
            _after_attempts(outcome, log)
        return path, bool(is_ready), outcome
    log("== 3/3 Translation and writing into the game (automatic backup of the originals)")
    if backend == "fake":
        log("  (offline test: fake translations go into a separate memory)")
    p = Project(path, log, test=(backend == "fake"))
    completed = p.translate(create_backend(backend, log))
    log("Done. Start the game to see the translation." if completed else
        "Partly done: some texts were left untranslated (see above).")
    if profile.get("engine") == "gamemaker":
        _after_writing(p.profile, log, outcome, set_=backend != "fake")
    if profile.get("engine") == "unity-localization":
        source = profile.get("source_language") if profile.get("source_language") not in (None, language.AUTO) else "en"
        log("In the game, keep the %s language selected: its texts are now translated."
            % (language.endonym(source) or language.label(source)))
    _apply_outcome(outcome, p, profile.get("engine") or ("web" if game_supported(outcome)[1] else None))
    if not completed:
        _after_attempts(outcome, log)
    return path, completed, outcome


def _reason_already_in(dest_lang):
    return "The game is already in %s: there's nothing to translate." % language.label(dest_lang)


def _reason_official(dest_lang):
    return "The game already has an official translation in %s." % language.label(dest_lang)


def _reason_no_text(outcome, folder):
    """Why there is nothing to translate, worded for the type of game (what the detection saw)."""
    engine = outcome.get("main_engine")
    containers = {r["name"] for r in outcome["results"] if r["section"] == "Container"}
    if engine == "GameMaker":
        return ("The text in this GameMaker game is located within the game's data file (data.win), not in separate "
                "text files: PatchSmith doesn't know how to translate them yet.")
    if engine in ("XNA", "FNA", "MonoGame"):
        return ("The text in this game is written directly into its code, not in separate language files: PatchSmith "
                "doesn't yet know how to translate it.")
    if any("!/" in r for r in outcome.get("web_roots") or ()) or \
            ("Electron" in containers and os.path.exists(os.path.join(folder, "resources", "app.asar"))):
        return ("The game files are packaged in an archive (app.asar). PatchSmith doesn't yet know how to translate "
                "the text within it.")
    if engine:
        return ("I haven't found any files with text that PatchSmith can read (game engine: %s)." % engine)
    return "I couldn't recognize the game's engine and found no files with texts to translate."


def _write_outcome(outcome, project, texts, written, engine, live=None, reason="", actions=()):
    """Outcome of the engines that write the translations from the translation memory into the game (Unity, Unreal,
    XNA): OK, PARTIAL or TRANSLATOR_BLOCKED, with the translated texts and those left untranslated. actions: extra
    ones (e.g. for Unity's new sentences without a translator)."""
    if outcome is None:
        return
    unique = list(dict.fromkeys(t for t in texts if isinstance(t, str)))
    remaining = [t for t in unique if not (isinstance(project.memory.get(t), str) and project.memory.get(t))]
    fields = dict(engine=engine, written=written, translated_items=len(unique) - len(remaining),
                 remaining=len(remaining), remaining_examples=remaining[:20], live=live, extra=_extra_attempts(project))
    if project.translation_outcome == "TRANSLATOR_BLOCKED" and remaining:
        _mark(outcome, "TRANSLATOR_BLOCKED", project.translation_reason, actions=["use_local"] + list(actions),
               **fields)
    elif remaining or not written:
        _mark(outcome, "PARTIAL", reason or ("" if written else "I haven't written anything in the game."),
               actions=["use_local"] + list(actions), **fields)
    else:
        _mark(outcome, "OK", reason, actions=list(actions), **fields)


def _apply_outcome(outcome, project, engine):
    """Outcome after Project.translate or apply (games with text files: web, GameMaker, Unity Localization...)."""
    if outcome is None:
        return
    r = project.last_apply or {"ok": 0, "missing": 0, "examples": [], "discarded": 0, "writes": 0,
                                    "is_open": False}
    # written: the game has at least one translation (a file rewritten without translations does not count)
    fields = dict(engine=engine, written=r["writes"] > 0 and r["ok"] > 0, translated_items=r["ok"],
                 remaining=r["missing"], remaining_examples=r["examples"], extra=_extra_attempts(project))
    if outcome.get("added_language"):
        fields["extra"]["added_language"] = outcome["added_language"]
    if r["is_open"]:
        _mark(outcome, "GAME_OPEN", "The game is open: the translations are ready and saved, but I haven't written "
                                       "them to the game.", actions=["close_game", "reapply"], **fields)
    elif project.translation_outcome == "NOTHING_TO_DO":
        _mark(outcome, "NOTHING_TO_DO", project.translation_reason, **fields)
    elif project.translation_outcome == "TRANSLATOR_BLOCKED" and r["missing"]:
        _mark(outcome, "TRANSLATOR_BLOCKED", project.translation_reason, actions=["use_local"], **fields)
    elif r["missing"]:
        # whole sentences, singular and plural (pieces cannot be translated in the interface's language)
        if r["discarded"] == 1:
            reason = ("One translation was not written because the game wouldn't display it properly (a shape "
                      "different from the original, or characters its fonts don't have).")
        elif r["discarded"]:
            reason = ("%d translations were not written because the game wouldn't display them properly (a shape "
                      "different from the original, or characters its fonts don't have)." % r["discarded"])
        else:
            reason = ""
        if r["discarded"]:  # (for "Fix translations": the "Not written to the game" list, see outcome.py)
            fields["extra"]["discarded"] = r["discarded"]
        # texts never translated (not just rejected): another translator may help
        _mark(outcome, "PARTIAL", reason, actions=["use_local"] if r["missing"] > r["discarded"] else [], **fields)
    else:
        _mark(outcome, "OK", **fields)


def _after_writing(profile, log, outcome, set_=True):
    """GameMaker, after writing the translations: if the translation is an added language, sets it in the options
    saved by the game (or says how to choose it); otherwise says to leave the original language."""
    folder = profile["game_folder"]
    entries = [v for v in profile.get("file", []) if v.get("new_language")]
    written_texts = [v for v in entries if os.path.exists(os.path.join(folder, *v["new_language"].split("/")))]
    if entries and not written_texts:
        log("The translation is NOT in the game (see above: if the game was open, close it and press 'Analyze and "
            "translate' again, which uses the translations already saved).")
        return
    if not entries:
        log("In the game, keep the %s language selected: its texts are now translated."
            % language.label(profile.get("file_language") or "en"))
        return
    name = os.path.splitext(os.path.basename(written_texts[0]["new_language"]))[0]
    is_set = False
    if set_:
        is_set, lines = official_language.set_added_language(
            folder, name, folder_languages(folder, written_texts[0]["path"]), log,
            source=os.path.splitext(os.path.basename(written_texts[0]["path"]))[0])
        for r in lines:
            log("  " + r)
    outcome["added_language"] = {"name": name, "is_set": is_set}
    log("In the game, the translation is the language '%s': %s. The original remains selectable in the menu." % (
        name, "I've already set it up in the game's options" if is_set else
        "choose it in Options > Language (using the arrows)"))


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
    settings). Returns True if everything is fine; the outcome (see outcome.py) goes into outcome["outcome"]."""
    path = find_profile(folder)
    root = registered = folder
    if path:
        with open(path, encoding="utf-8") as f:
            root = registered = legacy.load(f)["game_folder"]
        # also look in the chosen folder, if it contains the registered one
        if _inside(root, folder):
            root = folder
    for d in dict.fromkeys(os.path.abspath(x) for x in (folder, registered, root) if x and os.path.isdir(x)):
        try:
            is_open = system.game_open(d)
        except system.SysError as e:
            log(str(e))
            _mark(outcome, "GAME_OPEN", str(e), actions=["close_game"])
            return False
        if is_open:
            log("The game is open: close it and run 'Restore originals' again. I haven't touched anything.")
            _mark(outcome, "GAME_OPEN", "The game is open.", actions=["close_game"])
            return False
    modified = find_info(root)
    candidates = [registered, root, folder, adapter_unity.find_root(folder)]
    if not path:  # parent folder with several registered games: I consider all of them
        candidates += [p.get("game_folder") for _, p in list_profiles()
                      if p.get("game_folder") and _inside(p["game_folder"], folder)]
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
        log("No files modified in this folder: the game is already in its original state.")
        _mark(outcome, "ALREADY_ORIGINAL", "I haven't found any PatchSmith changes in this folder: the game is "
                                        "already in its original state.")
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
        additions = [os.path.splitext(n)[0] for n in info.get("created", []) if n.lower().endswith(".json")]
        if additions and gamemaker.roots(root):
            source = os.path.splitext(os.path.basename(p))[0]
            names = {os.path.splitext(n)[0] for n in os.listdir(os.path.dirname(p)) if n.lower().endswith(".json")}
            if official_language.put_back_added_language(root, additions, source, names, log) is False:
                log("I haven't removed the translation: the game would restart with a language that no longer exists. "
                    "Close the game, check that its options are not read-only, and repeat 'Restore originals'.")
                _mark(outcome, "PERMISSIONS", "I haven't removed the translation: I can't change the chosen language "
                                           "in the game's options, which would revert to a language that no longer "
                                           "exists. Please check that the game's options are not read-only.",
                      actions=["close_game"])
                return False
    blocked_files = []
    for p in modified:
        try:
            restore_file(p, log)
        except (OSError, RuntimeError) as e:
            blocked_files.append(os.path.basename(p))
            log("  ! %s not restored (%s)" % (os.path.basename(p), e))
    language_unrestored = False
    for d in languages:  # last: a problem here must not prevent removing the translation
        try:
            if official_language.restore(d, log) is False:
                language_unrestored = True
        except Exception as e:
            language_unrestored = True
            log("  ! I wasn't able to set the game language back to its original setting (%s: %s): please select it "
                "from the game menu."
                % (type(e).__name__, e))
    if blocked_files:
        log("Still to put back: %s. Close the programs that use them (antivirus included) and repeat 'Restore "
            "originals'." % ", ".join(blocked_files))
        _mark(outcome, "PERMISSIONS", "Still to put back: %s." % ", ".join(blocked_files[:5]),
               actions=["close_game", "create_report"])
        return False
    if language_unrestored:
        log("Originals restored, except the game language (see above): repeat 'Restore originals' to try again.")
        _mark(outcome, "PARTIAL", "Originals restored, except the game language: choose it in the game menu, or press "
                                   '"Restore originals" again to try again.')
        return False
    log("Originals restored.")
    _mark(outcome, "OK", "Originals restored.")
    return True


def reapply(folder, log=print, test=False, outcome=None):
    """Rewrites the saved translations into the game, without network. True if everything is translated; the outcome
    (see outcome.py) goes into outcome["outcome"] (and GameMaker's added language into outcome["added_language"])."""
    path = find_profile(folder)
    if not path:
        log("No existing translation for this folder.")
        _mark(outcome, "NOTHING_TO_DO", "There is no saved translation for this folder: use 'Analyze and translate' "
                                        "first.")
        return False
    profile = _read_profile(path)
    if profile.get("engine") == "godot":  # override.cfg, script and saved translations, without network
        return bool(godot_complete(path, log, network=False, backend="fake" if test else None, outcome=outcome))
    if profile.get("engine") == "web-live":  # the script in preload.js and the saved translations, without network
        return bool(web_live_full(path, log, network=False, backend="fake" if test else None, outcome=outcome))
    if profile.get("engine") == "unity":  # reinstalls from the components already downloaded, with saved translations
        return bool(unity_full(path, log, network=False, backend="fake" if test else "google", outcome=outcome))
    if profile.get("engine") == "unreal":
        return bool(unreal_full(path, log, network=False, backend="fake" if test else "google", outcome=outcome))
    if profile.get("engine") == "xna":
        return bool(xna_full(path, log, network=False, backend="fake" if test else "google", outcome=outcome))
    project = Project(path, log, test=test)
    done = project.apply()
    if profile.get("engine") == "gamemaker":
        _after_writing(project.profile, log, outcome if outcome is not None else {}, set_=not test)
    _apply_outcome(outcome, project, profile.get("engine") or ("web" if any(
        v.get("format") == "web-ui" for v in profile.get("file", [])) else None))
    return done


def retranslate(folder, texts=None, backend="local", log=print, test=False, outcome=None, model=None):
    """"Fix translations": re-translates the chosen texts now (texts=None: all the machine translations and the texts
    left untranslated; fixes made by hand stay), see corrections.retranslate, and then rewrites them into the game like
    "Reapply" (with the game closed: with the game open the new translations stay saved and the outcome says to press
    "Reapply"). model: the local translator model to use (e.g. '12b'). True if everything is fine; the outcome in
    outcome["outcome"], with extra["retranslation"] = {retranslated, remaining_count, texts, interrupted}."""
    from . import corrections
    path = find_profile(folder)
    if not path:
        log("No existing translation for this folder.")
        _mark(outcome, "NOTHING_TO_DO", "There is no saved translation for this folder: use 'Analyze and translate' "
                                        "first.")
        return False
    translator = corrections.create_translator("fake" if test else backend, model, log)
    result = corrections.retranslate(path, texts, translator, log, test=test)
    interruption.check()
    registered = _read_profile(path).get("game_folder") or folder
    try:
        is_open = os.path.isdir(registered) and system.game_open(registered)
    except system.SysError:
        is_open = True
    if is_open:
        log("The game is open: the new translations are saved; I'll write them into the game with 'Reapply' when it's "
            "closed.")
        _mark(outcome, "GAME_OPEN", "The new translations are saved, but the game is open: I haven't written them "
                                       "to the game yet.", actions=["close_game", "reapply"])
        done = False
    else:
        done = reapply(folder, log, test=test, outcome=outcome)
    if outcome is not None and outcome.get("outcome") is not None:
        outcome["outcome"].extra["retranslation"] = result
    return done and not result["interrupted"]


class RestartError(RuntimeError):
    """"Start over" did not start (code: a code from outcome.CODES, for the window)."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def start_over(folder, log=print, test=False, backend=None):
    """"Start over" (Fix translations): puts back the game's original files (like "Restore originals": the text scan
    must see the originals, not the translated files) and sets aside all the saved translations, the ones fixed by
    hand too, in memory/copies/<game>-<date>/ (nothing is lost), so that the next "Analyze and translate" (with
    regenerate) translates everything again. The saved translations are the game's translation memory with the machine
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
            raise BackendError("%s To start completely fresh, you'll need a translator ready to go: I haven't touched "
                               "anything." % pause,
                                "TRANSLATOR_BLOCKED")
    profile = _read_profile(path)
    game, a, name = profile.get("game_folder") or folder, profile["target_language"], profile["name"]
    put_back = {}
    log("Start over from scratch: first, I'm going to restore the original game files")
    restore(folder, log, outcome=put_back)
    e = put_back.get("outcome")
    if e is not None and e.code not in ("OK", "ALREADY_ORIGINAL", "PARTIAL"):
        raise RestartError("%s To start completely from scratch, you'll need the original files: I haven't saved any "
                           "translations." % (e.reason or "I wasn't able to put the original game files back."),
                               e.code if e.code in ("GAME_OPEN", "PERMISSIONS") else None)
    memory = memory_path(profile, test)
    file = [memory, problems_path(profile, test)]
    if not test:
        file += [memory[:-len(".json")] + ".automatiche.json", corrections.to_write_path(profile)]
        if profile.get("engine") == "unity":
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
    log("Previous translations set aside in %s (%d files): starting from scratch." % (copy_dir, len(present)))
    if not test:
        _remove_from_shared(profile, memory, copy_dir, log)
    return copy_dir


def _remove_from_shared(profile, memory, copy_dir, log):
    """Start over: the entries of the shared translation memory machine-translated for this game (provenance) are
    removed, and kept in the copy; those fixed by hand stay (they apply to all games)."""
    from .project import shared_path, shared_origin_path
    name, dest = profile["name"], profile["target_language"]
    end = "-%s.origine.json" % dest
    for file_name in os.listdir(os.path.dirname(memory)):
        if not (file_name.startswith("comune.") and file_name.endswith(end)):
            continue
        orig = file_name[len("comune."):-len(end)]
        try:
            origin = legacy.upgrade_entries(shared_file.read_json(shared_origin_path(orig, dest)))
            shared = shared_file.read_json(shared_path(orig, dest))
        except (OSError, ValueError):
            continue
        if not isinstance(origin, dict) or not isinstance(shared, dict):
            continue
        removed = [t for t, o in origin.items()
                   if isinstance(o, dict) and o.get("game") == name and not o.get("by_hand")]
        if not removed:
            continue
        with open(os.path.join(copy_dir, "comune.%s-%s.tolte.json" % (orig, dest)), "w", encoding="utf-8") as f:
            json.dump({t: shared.get(t) for t in removed}, f, ensure_ascii=False, indent=2)
        shared_file.save_merging(shared_path(orig, dest), {k: v for k, v in shared.items() if k not in removed},
                                    shared)
        shared_file.save_merging(shared_origin_path(orig, dest),
                                    {k: v for k, v in origin.items() if k not in removed}, origin)
        log("  Shared memory: Removed %d automatic translations from this game (saved in the copy)" % len(removed))


def unity_full(profile_path, log=print, network=True, anticheat=False, backend="google", outcome=None):
    """Installs/updates XUnity and pre-translates the game's texts into its cache.

    network=True: extracts the texts from the game's files and translates those not yet in memory with the chosen
    translator (which also becomes the game profile's one, read by the translator started by the game).
    network=False: puts back into the cache only the translations already in memory (no connection).
    PatchSmith translates the new sentences while the game is played (see profile["live"] and xunity_service).
    The outcome (see outcome.py) goes into outcome["outcome"]: live says who translates the new sentences (see
    _live).
    """
    profile = _read_profile(profile_path)
    folder, a = profile["game_folder"], profile["target_language"]
    backend = "google" if backend in (None, "xunity-google") else backend
    test = backend == "fake"
    translator = create_backend(backend, log) if network else None  # before installing: missing key = no changes
    interruption.check()
    # the translator started by the game reads the profile
    if network and not test and profile.get("backend") != backend:
        profile["backend"] = backend
        scan.save_json(profile_path, profile)
    # new sentences while playing: always to PatchSmith's translator (if the plugin compiles), even with network=False
    # ("Reapply" after "Restore originals"); in the test it is left as it is (the test translation memory is another)
    choice = profile.get("live") if profile.get("live") in LIVE_CHOICES else "service"
    local = None if test else (profile_path if choice == "service" else
                                 "none" if choice == "none" else False)
    src_lang = adapter_unity.install(folder, profile["source_language"], a, profile["name"], log, anticheat=anticheat,
                                local=local)
    if not src_lang:
        _mark(outcome, "NOTHING_TO_DO", _reason_already_in(a), engine="unity")
        return False
    project = Project(profile_path, log, test=test)
    failed = 0
    texts = []
    # wrong XUnity cache lines written by PatchSmith (or by its live translator): they are redone
    cache = {} if test else _cache_lines_to_redo(folder, a, src_lang, project, log)
    if network:
        info = adapter_unity.analyze(folder)
        texts = adapter_unity.extract_texts(folder, info, src_lang, log)
        log("  Pre-translation: %d game texts found" % len(texts))
        present = set(texts)
        texts += [t for t in cache if t not in present]
        if texts:
            try:
                failed = project.translate_texts(texts, translator, src_lang, a, redo={
                    t: "XUnity cache line to be corrected" for t, v in cache.items()
                    if project.memory.get(t) == v})
            except interruption.Interrupted:
                log("  BepInEx and XUnity.AutoTranslator are already installed in the game: phrases that aren't "
                    "pre-translated will be translated while you play. 'Analyze and translate' completes the "
                    "pre-translation, 'Restore originals' removes them.")
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
        log("  ! Google has just paused requests: XUnity will not use it for new phrases")
    live, note = _live(folder, profile, local)
    log("  New phrases while playing: %s" % LIVE.get(live, live))
    actions = {"no_translator": ["install_local"], "plugin_none": ["create_report"]}.get(note, [])
    incomplete_note = ("%d phrases are not pre-translated: %s." % (failed, "they will be translated as you play"
                                                           if live in ("local", "online", "google") else
                                                           "they will stay untranslated")) if failed else ""

    def _unity_outcome():
        _write_outcome(outcome, project, texts, True, "unity", live, incomplete_note, actions)
        if outcome is not None and note:
            outcome["outcome"].extra["live_note"] = note

    if test:
        log("  (offline test: fake translations don't get cached by XUnity)")
        _unity_outcome()
        return src_lang
    info = adapter_unity.analyze(folder)
    if adapter_unity.is_running(folder, info["exe"]):
        log("  ! The game was opened in the meantime: close it and press 'Reapply' (the translations are already "
            "saved).")
        _mark(outcome, "GAME_OPEN", "The game was opened while I was working: BepInEx and XUnity are installed, but "
                                       "the pre-translated phrases are not in the game yet.",
              engine="unity", written=True, live=live, actions=["close_game", "reapply"])
        return src_lang
    # the fixes made with "Fix translations" (also the re-translations asked there): their lines are written now,
    # with the game closed, in place of the previous ones (see corrections.to_write_path)
    from . import corrections
    pending = corrections.to_write(profile)
    current_count = adapter_unity.xunity_cache_entries(folder, a) if pending else {}
    for t, d in pending.items():
        translations[t] = d["value"]
        present_value = current_count.get(t)
        substitute.pop(t, None)
        if present_value is not None and present_value != d["value"] and d.get("before") in (None, present_value):
            substitute[t] = present_value
        # otherwise: line changed after the fix (by hand in the file?), it stays as is
    counts = {}
    excluded = profile.get("missing_chars")  # letters the game's fonts lack: adapted in the cache
    new_lines = adapter_unity.write_xunity_cache(folder, a, translations, log, substitute=substitute,
                                              counts=counts, excluded=excluded)
    log("  Pre-translation: %d phrases added to the XUnity cache (already present: %d)"
        % (new_lines, len(translations) - new_lines))
    if any(v.get("format") == "unity-tables" for v in project.profile.get("file", [])):
        log("  Unity Localization text tables, inside the game:")
        project.apply()
    written_texts = 0
    if pending:
        from .characters import adapt_without
        after = adapter_unity.xunity_cache_entries(folder, a)
        # (a fix with a letter the fonts lack is written adapted: it counts as written that way too)
        written_texts = sum(1 for t, d in pending.items()
                      if after.get(t) in (d["value"], adapt_without(d["value"], excluded, a) or d["value"]))
        log("  Fixes made in 'Fix translations': %d written to the XUnity cache%s" % (
            written_texts, ", %d left as they were (the line was changed after the fix)"
            % (len(pending) - written_texts) if written_texts < len(pending) else ""))
        try:
            corrections.written_texts(profile, list(pending))
        except OSError as e:  # already in the cache: next time they are rewritten the same (no harm)
            log("  ! List of fixes to write not updated (%s)" % e)
    replaced = max(0, counts.get("replaced", 0) - sum(1 for t in pending if t in substitute))
    if cache:
        log("  XUnity cache: %d lines fixed with a new translation%s" % (
            replaced, ", %d remain as they were (no better translation for now)"
            % (len(cache) - replaced) if len(cache) > replaced else ""))
    _unity_outcome()
    if outcome is not None and replaced:
        outcome["outcome"].extra["cache_fixed"] = replaced
    if outcome is not None and written_texts:
        outcome["outcome"].extra["fixes_written"] = written_texts
    if failed:
        log(DETAIL + "  ! Incomplete pre-translation: %d phrases %s." % (failed, "they will be translated as you play"
                                                             if live != "none" else "they stay untranslated"))
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
    folder, a = profile["game_folder"], profile["target_language"]
    current_count = adapter_unity.xunity_cache_entries(folder, a)
    translations, substitute = {}, {}
    for t, d in pending.items():
        translations[t] = d["value"]
        present_value = current_count.get(t)
        if present_value is not None and present_value != d["value"] and d.get("before") in (None, present_value):
            substitute[t] = present_value
    excluded = profile.get("missing_chars")
    adapter_unity.write_xunity_cache(folder, a, translations, log, substitute=substitute, excluded=excluded)
    after = adapter_unity.xunity_cache_entries(folder, a)
    written_texts = sum(1 for t, d in pending.items()
                  if after.get(t) in (d["value"], adapt_without(d["value"], excluded, a) or d["value"]))
    corrections.written_texts(profile, list(pending))
    return written_texts, len(pending)


def _cache_lines_to_redo(folder, a, src_lang, project, log):
    """{original: translation in the cache} of the XUnity cache lines to fix (read-only):
    - those that adapter_unity.check_xunity_cache reports (identical to the original with several words, in
      another language, different line breaks or placeholders, a translated game name);
    - the provisional answers of the live translator (journal: 'relaxed', 'rejected', 'deferred'), which XUnity
      saved as they were.
    Only those written by PatchSmith: the translation in the cache is the one from the translation memory, the machine
    translations or the journal. Lines changed by hand (or written by others) are never touched."""
    entries = adapter_unity.xunity_cache_entries(folder, a)
    if not entries or src_lang == language.AUTO:
        return {}
    our_translations, provisional = our_values(project.profile, project.memory, project._read_machine())
    flagged = adapter_unity.check_xunity_cache(entries, src_lang, project.profile["target_language"],
                                                     project.active_names(list(entries)))
    for t, response in provisional.items():
        if entries.get(t) == response and t not in flagged:
            flagged[t] = (response, ["translated while you were playing, without all the checks"])
    to_redo = {t: v for t, (v, _) in flagged.items() if v in our_translations.get(t, ())}
    if flagged:
        example = next(iter(to_redo or flagged))
        log(DETAIL + "  XUnity cache: %d lines to fix (e.g. %r: %s); %d were written by PatchSmith and get replaced "
                     "with a new translation, the others (changed by hand or written by others) stay as they are"
            % (len(flagged), example[:50], ", ".join(flagged[example][1]), len(to_redo)))
    return to_redo


def our_values(profile, memory, machine):
    """({original: {translations written by PatchSmith}}, {original: provisional answer from the journal}) to
    recognize the XUnity cache lines written by PatchSmith (the others, changed by hand or written by others, are not
    touched): the translation memory, the machine translations, the live translator's answers (journal), also with
    the characters the game's fonts lack already adapted (profile["missing_chars"]). Read-only."""
    from .characters import adapt_without
    from .xunity_service import read_journal
    excluded = set(profile.get("missing_chars") or "")
    a = profile.get("target_language") or "it"
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
    for line in read_journal(profile["name"]):
        response = line.get("response")
        if not isinstance(response, str):
            continue
        add(line["text"], response)
        if line.get("outcome") == "ok":
            provisional.pop(line["text"], None)  # translated properly later
        else:
            provisional[line["text"]] = response
    return our_translations, provisional


def _extra_attempts(project):
    """For the outcome: the texts left without a translation even after the new automatic rounds (Project._recover),
    the saved translations redone and the fixes made by hand that cannot be written into the game."""
    extra = {}
    if project.last_failed:
        extra["retried"] = len(project.last_failed)
    if project.redone:
        extra["redone"] = project.redone
    problems = project.problems()
    by_hand = [t for t, v in problems.items() if v.get("where") == "game" and not v.get("is_machine")]
    if by_hand:
        extra["hand_fixes_discarded"] = len(by_hand)
    if getattr(project, "shortened", 0):
        extra["shortened"] = project.shortened
    if getattr(project, "long_noted", 0):  # (only those of this operation: the others are already in the list)
        extra["too_long"] = project.long_noted
    if getattr(project, "_font_adapted", 0):
        extra["font_adapted"] = project._font_adapted
    return extra


def _after_attempts(outcome, log):
    """In place of the old 'rilancia' (which repeated the same requests to the translator, with the same answers): what
    PatchSmith has already tried on its own and what is still needed."""
    extra = getattr((outcome or {}).get("outcome"), "extra", None) or {}
    if extra.get("retried"):
        log("  %d texts are still untranslated even after the new automatic attempts (separate phrases, different "
            "parameters): you can see them with 'Show remaining', and at the next 'Analyze and translate' I'll retry "
            "them with new parameters. A saved translation that isn't right can be fixed with 'Fix translations'."
            % extra["retried"])
    if extra.get("hand_fixes_discarded"):
        log("  %d translations you fixed by hand can't be written to the game (the reason is above): fix them with "
            "'Fix translations'." % extra["hand_fixes_discarded"])


def _live(folder, profile, local):
    """(who translates the new sentences while the game is played, note) for the outcome. The first is 'local' or
    'online' (PatchSmith's translator with the local translator, or only with the online ones), 'google' (XUnity on
    its own), 'none', None (XUnity not installed). The note (outcome.LIVE_NOTES) explains the cases to fix:
    'no_translator' (no translator available now), 'plugin_google'/'plugin_none' (the plugin that starts
    PatchSmith's translator could not be prepared)."""
    from . import xunity_service
    kind = adapter_unity.live_translator(folder)
    if kind == "service":
        names = xunity_service.expected_translators(profile)
        if not names:
            return "none", "no_translator"
        return ("local" if "local" in names else "online"), None
    if local and kind in ("google", "none"):  # PatchSmith's translator was wanted, but the plugin is not there
        return kind, "plugin_" + kind
    return kind, None


def memory_file(folder, dest_lang=None):
    """Path of the game's translations file, without opening it (so it opens even if it is broken)."""
    path = find_profile(folder)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            profile = legacy.load(f)
    except (OSError, ValueError):
        return None
    if dest_lang:
        profile = dict(profile, target_language=dest_lang)
    if profile.get("engine") == "unity":
        return adapter_unity.translations_file(profile["game_folder"], profile["name"],
                                               profile["target_language"])
    return memory_path(profile)


# --- checks before starting ------------------------------------------------------------------------------------

# a problem found before a long operation: code, message for the player, proposed action, data
Preflight = collections.namedtuple("Preflight", "code message action data")
PREFLIGHT_CODES = ("GAME_OPEN", "LANGUAGE", "READ_ONLY", "DISK_SPACE", "TRANSLATOR_PAUSED", "LOCAL_MISSING",
                      "SAME_GAME")
GAME_SPACE = 500 * 1024 * 1024  # free on the game's disk to translate or reapply (backups, BepInEx, pak...)
DATA_SPACE = 100 * 1024 * 1024  # free on the disk of PatchSmith's data (translation memory, game profiles, logs)
_WHAT = {"translate": "translate it", "reapply": "reapply the translations", "restore": "restore the original files"}


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
    """True if the folder is inside Program Files (C:\\Programmi on Italian Windows), which Windows protects."""
    for env_var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        root = os.environ.get(env_var)
        if root and _inside(folder, root):
            return True
    return False


def _read_only_message(folder, error):
    if not isinstance(error, PermissionError):
        return ("I can't write to the game's folder (%s): %s. Make sure the disk isn't full or read-only, then try "
                "again." % (folder, error.strerror or error))
    text = "I can't write to the game's folder (%s): Windows is denying access.\n\n" % folder
    if _in_program_files(folder):
        text += ("The game is in the Program Files folder, which Windows protects. You can move it to another folder "
                 "(with Steam: game Properties > Installed Files > Move install folder, to a library outside Program "
                 "Files), or close PatchSmith and reopen it as administrator (right-click > Run as administrator).")
    else:
        text += ("Usually, Windows' \"Controlled Folder Access\" (Windows Security > Virus & threat protection > "
                 "Ransomware protection) or your antivirus software will block it. You can allow Python in these "
                 "settings, or close PatchSmith and reopen it as an administrator "
                 "(right-click > Run as administrator).")
    return text + "\n\nI haven't touched anything."


def _readable_size(byte):
    return "%.1f GB" % (byte / 1024 ** 3) if byte >= 1024 ** 3 else "%d MB" % (byte // 1024 ** 2)


def _looks_like_code(code):
    return bool(re.fullmatch(r"[a-z]{2,3}(-[A-Za-z0-9]{2,4})?", code or ""))


def _language_problems(src_lang, a):
    """The problems of the chosen languages (From/To): unknown, a code not in the list, the same language."""
    output = []
    for field_label, code in (("From (in-game language)", src_lang), ("To (translation language)", a)):
        if code is None or code in language.NAMES or (code == language.AUTO and field_label.startswith("From")):
            continue
        if _looks_like_code(code):
            output.append(Preflight(
                "LANGUAGE", "The language in %s is the code \"%s\", which is not in the list. If it's a valid Google "
                          "Translate code, you can continue; otherwise, choose a language from the list."
                          % (field_label, code), "continue",
                {"code": code}))
        else:
            output.append(Preflight(
                "LANGUAGE",
                "I don't recognize the language in %s: \"%s\". Please choose a language from the list%s." % (
                    field_label, code, ' or "Detect automatically"' if field_label.startswith("From") else ""),
                None, {"code": code}))
    if not output and src_lang and a and src_lang != language.AUTO and language.same_language(src_lang, a):
        output.append(Preflight(
            "LANGUAGE", "From and To are the same language (%s): there's nothing to translate. In From, choose the "
                      "game's language, or \"Detect automatically\"." % language.label(a), None, {"code": a}))
    return output


def preflight_checks(folder, backend=None, operation="translate", src_lang=None, a=None):
    """Quick checks before a long operation (operation: translate, reapply, restore), so as not to discover a
    problem after minutes or hours of work. Returns [Preflight(code, message, action, data)] in the order in
    which to show them (empty: it can start):
    - GAME_OPEN (action 'retry'): a program from the game folder is open;
    - LANGUAGE (translate only; None, or 'continue' for a code not in the list): From/To languages unknown or the
      same;
    - READ_ONLY (None): the game folder cannot be written to (Program Files, controlled access, antivirus);
    - DISK_SPACE (translate and reapply; 'continue'): little space on the game's disk or on the one of PatchSmith's
      data;
    - TRANSLATOR_PAUSED (translate only; 'use_local' or 'install_local'): the chosen online translator is paused
      after a block (Limiter.pause);
    - LOCAL_MISSING (translate only; 'install_local', or continue: the official language does not need translating,
      and without a translator Analyze and translate stops before modifying the game): the local translator is
      chosen, but it is not installed;
    - SAME_GAME (translate only, folder without a game profile; 'reuse'): the same game is already translated in
      another folder (copied, moved, reinstalled); data: what same_games says.
    Reads only, except for a test file written and removed right away in the game folder. With the test translator
    ('fake') no translator checks."""
    folder = os.path.abspath(folder)
    output = []
    try:
        path = find_profile(folder)
        registered = _read_profile(path).get("game_folder") if path else None
    except (OSError, ValueError, AttributeError):
        path = registered = None
    folders = list(dict.fromkeys(os.path.abspath(d) for d in (folder, registered) if d and os.path.isdir(d)))
    for d in folders:
        try:
            is_open = system.game_open(d)
        except system.SysError:
            is_open = False  # unknown: the operation's own checks will tell
        if is_open:
            output.append(Preflight("GAME_OPEN", "The game is open: close it and press \"Try again\". While it's "
                                                    "open, I can't %s." % _WHAT.get(operation, "work on its files"),
                                      "retry", {"folder": d}))
            break
    if operation == "translate":
        output += _language_problems(src_lang, a)
    where = registered if registered and os.path.isdir(registered) else (adapter_unity.find_root(folder) or folder)
    error = check_writable(where)
    if error is not None:
        output.append(Preflight("READ_ONLY", _read_only_message(where, error), None,
                                  {"folder": where, "error": str(error)}))
    if operation in ("translate", "reapply"):
        for disk, minimum, text in ((where, GAME_SPACE, "On the game's disk (%s) only %s is free: PatchSmith saves a "
                                                        "copy of the original files and adds its own files"),
                                     (data_folder(), DATA_SPACE, "On the disk where PatchSmith saves the translations "
                                                                 "(%s) only %s is free")):
            existing = disk
            while existing and not os.path.isdir(existing) and os.path.dirname(existing) != existing:
                existing = os.path.dirname(existing)  # (the data folder may not exist yet)
            try:
                free_bytes = shutil.disk_usage(existing).free
            except (OSError, ValueError):
                continue
            if free_bytes < minimum:
                units = os.path.splitdrive(os.path.abspath(existing))[0] or existing
                output.append(Preflight("DISK_SPACE", (text % (units, _readable_size(free_bytes)))
                                        + ". With a full disk the operation would stop halfway: free up some space "
                                          "and try again, or continue anyway.",
                                        "continue",
                                        {"folder": existing, "free_bytes": free_bytes}))
    if operation == "translate" and backend not in (None, "fake"):
        from . import ollama
        from .limiter import NAMES, Limiter
        try:
            local_ready = ollama.state() == "ready"
        except OSError:
            local_ready = False
        if backend in NAMES:
            try:
                pause = Limiter(backend, log=None).pause()
            except (OSError, ValueError, TypeError, AttributeError):
                pause = None
            if pause:
                output.append(Preflight(
                    "TRANSLATOR_PAUSED", "%s\n\nThe local translator has no limitations or restrictions: %s\n\nIf "
                                           "you continue with %s, I will only use the saved translations: new texts "
                                           "will remain in the original language." % (
                        pause,
                        "would you like to use it?" if local_ready
                        else "it downloads only once and then translates even "
                             "without internet. Do you want to install it?",
                        NAMES[backend]),
                    "use_local" if local_ready else "install_local", {"translator": backend}))
        elif backend == "local" and not local_ready:
            output.append(Preflight(
                "LOCAL_MISSING", "You chose the local translator, but it isn't installed yet: it's downloaded only "
                                  "once (a few GB: I'll tell you first what it downloads) and then it translates with "
                                  "no limits, no blocks and even without internet.\n\nIf you prefer to use an online "
                                  'translator, choose one from the "Translator" menu. If you continue without '
                                  "installing it, I will still analyze the game: if it already has an official "
                                  "translation, I will simply use that, otherwise I will stop before making any "
                                  "changes.", "install_local", {}))
    if operation == "translate" and not path:
        try:
            similar = same_games(folder, a, test=backend == "fake")
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            similar = []
        if similar:
            s = similar[0]
            was_at = ("which was in %s (that folder no longer exists: was the game moved?). Its profile now uses this "
                      "folder" % s["folder"] if s["moved"] else "in %s" % s["folder"])
            output.append(Preflight(
                "SAME_GAME", "This folder doesn't have any translations yet, but it seems to be the same game as "
                                '"%s", %s, which is already translated: %d saved texts.\n\nShall I reuse its '
                                "translations? That way I don't translate it from scratch: I only translate the "
                                "missing texts." % (s["name"], was_at, s["texts"]), "reuse", s))
    return output
