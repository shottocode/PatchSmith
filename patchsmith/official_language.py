"""Official language: if the game already has the translation in the target language, that is used instead of
translating.

Sources:
1. Steam: for a game installed from Steam, Steam's cache (appinfo.vdf) lists the official languages;
2. the game's files, engine by engine (Unreal: .locres per language; Unity: I2 / Unity Localization languages;
   XNA/.NET: satellites; GameMaker: files per language);
3. for a folder that is not a Steam installation, the game can be recognized from its executables: this counts only
   if the files say nothing and the folder name resembles the game's, otherwise it is just a note.
For Chinese the script matters (zh-TW and zh-CN are different languages).
When the language is there, PatchSmith SETS it where a standard, safe and reversible setting exists
(Unreal: GameUserSettings.ini; Unity with I2: the "I2 Language" preference), with the game closed, with a log of the
changes and restore; otherwise it explains exactly how to choose it. It does not modify Steam or unofficial
components.
"""
import datetime
import hashlib
import json
import os

from . import language, legacy, steam


def _base(code):
    return (code or "").split("-")[0].lower()


def _steam_name(code):
    if code in steam.STEAM_NAMES:
        return steam.STEAM_NAMES[code]
    key = language.comparison_key(code)
    if key == "zh-hant":
        return "tchinese"
    if key == "zh-hans":
        return "schinese"
    return steam.STEAM_NAMES.get(_base(code))


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- detection ------------------------------------------------------------------------------------

def detect(folder, dest_lang, engine=None, log=None):
    """What is known about the 'destinazione' language in the game.

    {'present': True/False/None, 'sources': [text], 'notes': [text], 'steam': installation info or None,
     'steam_known': game recognized from the executables or None, 'steam_language': current language in Steam,
     'steam_selectable': True/False/None, 'engine': {...data for the setting...}}"""
    r = {"present": None, "sources": [], "notes": [], "steam": None, "steam_known": None, "steam_language": None,
         "steam_selectable": None, "engine": {}}
    name = _steam_name(dest_lang)
    label = language.label(dest_lang)

    # 1) Steam (a problem with Steam's cache must never block the translation)
    info, known, steam_says, selectable = _steam_data(folder, name, log)
    # 2) the game's files, according to the engine
    try:
        present, engine_data = languages_in_files(folder, engine, dest_lang)
    except Exception as e:  # a detector that fails must not block the translation
        present, engine_data = None, {}
        if log:
            log("  ! Check of the languages in the files failed (%s: %s)" % (type(e).__name__, e))
    r["engine"] = engine_data
    root = engine_data.get("root") if engine_data.get("kind") == "sheet" else None
    if not info and root and os.path.normcase(root) != os.path.normcase(os.path.abspath(folder)):
        # the folder above the game was chosen: the Steam installation is recognized from the game's folder
        root_info, _, root_says, sel_root = _steam_data(root, name, log, installed_only=True)
        if root_info:
            info, known, steam_says, selectable = root_info, None, root_says, sel_root
    r["steam_selectable"] = selectable
    if info:
        r["steam"], r["steam_language"] = info, info.get("language")
    elif known:
        r["steam_known"] = known
    if steam_says is not None:
        state = "is one of the official languages" if steam_says else "is not one of the official languages"
        if info:
            r["sources"].append("Steam: %s %s" % (label, state))
    in_files = None if present is None else bool(engine_data.get("found"))
    if in_files:
        r["sources"].append("Game files: the %s language is present" % label)

    # 3) decision
    if info:  # Steam installation: Steam wins (the language may be in a package not yet downloaded)
        r["present"] = True if in_files else steam_says if steam_says is not None else in_files
    else:
        r["present"] = in_files
        if known and steam_says is not None:
            game = "\"%s\"" % (known.get("name") or known["appid"])
            if in_files is None and known.get("similar_folder"):
                r["present"] = steam_says
                r["sources"].append("Steam recognizes this game (%s, launched by %s): %s %s" % (
                    game, known["executable"], label, state))
            elif steam_says and not in_files:
                r["notes"].append("Perhaps it's the Steam game %s, which has %s official language support: if that's "
                                 "the case, look for the language setting in the game options or in the launcher."
                                 % (game, label))
    return r


def _steam_data(folder, name, log, installed_only=False):
    """(Steam installation, game recognized from the executables, official language according to Steam, selectable
    in Steam); None where it is not known. Steam errors stop nothing."""
    info = known = steam_says = selectable = None
    try:
        info = steam.game_installed(folder)
        if not info and not installed_only:
            known = steam.identify_from_executables(folder)
        appid = info["appid"] if info else known["appid"] if known else None
        if appid and name:
            app_data = steam.app_info(appid)
            if app_data:
                officials = steam.official_from_info(app_data)
                if officials is not None:
                    steam_says = bool(officials.get(name, {}).get("supported"))
                selectable = name in steam.selectable_from_info(app_data)
    except Exception as e:
        if log:
            log("  ! Failed to read Steam data (%s: %s): continuing without" % (type(e).__name__, e))
    return info, known, steam_says, selectable


def languages_in_files(folder, engine, dest_lang):
    """({languages} or None if not known, data to set it; 'found' says whether the requested language is there).
    Game sheets (games known one by one) come before the engine."""
    from . import games as sheets
    sheet, roots = sheets.identify(folder)
    if sheet:
        languages = sheet.present_languages(roots[0])
        return languages, {"kind": "sheet", "sheet": sheet.ID, "root": roots[0] if len(roots) == 1 else None,
                        "roots": roots, "found": bool(languages) and language.best(languages, dest_lang) is not None}
    if engine == "Unreal":
        from . import language_unreal
        languages, project = language_unreal.game_languages(folder)
        tag = language_unreal.target_tag(languages, dest_lang) if languages else None
        return languages, {"kind": "unreal", "project": project, "tag": tag, "found": tag is not None}
    if engine == "Unity":
        from . import language_unity
        data = language_unity.data_folder(folder)
        if not data:
            return None, {}
        languages, entry, i2, ul = set(), None, None, None
        try:
            i2 = language_unity.i2_languages(data) if language_unity.uses_i2(data) else None
        except Exception:  # I2 unreadable: Unity Localization remains
            i2 = None
        if i2:
            languages |= {c or n for n, c in i2}
            entry = language_unity.i2_entry(i2, dest_lang)
        try:
            ul = language_unity.unity_localization_languages(data)
        except Exception:
            ul = None
        # also the languages of the text tables, recognized from the bundles' content: the catalog does not always
        # list them under "Locales/" (e.g. "Locals/Italian (it).asset", or no name with the code)
        try:
            from . import adapter_unity_loc
            streaming = os.path.join(data, "StreamingAssets")
            tables = adapter_unity_loc.tables_by_content(streaming) if os.path.isdir(streaming) else {}
        except Exception:
            tables = {}
        if tables:
            present = {c.lower() for c in ul or ()}
            ul = set(ul or ()) | {c for c in tables if c.lower() not in present}
        if ul:
            languages |= set(ul)
        company, product = language_unity.company_product(data)
        found_root = entry is not None or bool(ul and language.best(ul, dest_lang))
        engine_data = {"kind": "unity", "data_folder": data, "company": company, "product": product,
                       "i2_language": entry, "i2_custom": bool(entry) and language_unity.i2_custom(data),
                       "unity_localization": sorted(ul) if ul else None, "found": found_root}
        return (languages if (i2 or ul) else None), engine_data
    if engine in ("XNA", "FNA", "MonoGame"):
        from . import adapter_xna
        resource_sets = adapter_xna.find_resources(folder)  # satellites created by PatchSmith do not count
        if not resource_sets:
            return None, {"kind": "xna"}
        codes = {c for i in resource_sets for cultures in i["bases"].values() for c in cultures}
        codes |= {i["neutral_language"] for i in resource_sets if i["neutral_language"]}
        return codes, {"kind": "xna", "found": language.best(codes, dest_lang) is not None}
    if engine == "GameMaker":
        from . import adapter_csv, adapter_gamemaker
        found = adapter_gamemaker.localization_file(folder)
        found.update(adapter_gamemaker.language_json_files(folder))  # also the JSON files per language
        languages = {l for l in found.values() if l}
        # and the CSV tables with one column per language: the full columns count (an empty one is a language
        # planned but not translated)
        tables = adapter_csv.tables(folder)
        for f in tables.values():
            languages |= f.full_languages()
        return (languages if found or tables else None), {"kind": "gamemaker",
                                                          "found": language.best(languages, dest_lang) is not None}
    return None, {}


# --- setting and restore --------------------------------------------------------------------------

def _log_file(folder):
    from .project import data_folder
    key = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:16]
    return data_folder("languages", "%s.json" % key)


def _read_log(folder):
    try:
        with open(_log_file(folder), encoding="utf-8") as f:
            return legacy.load(f)
    except (OSError, ValueError):
        return None


def _save_log(folder, data):
    from .adapter_webjson import write_atomic
    path = _log_file(folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _first_time(folder):
    """True the first time the user is asked to start the game to create its settings (then it is not repeated,
    so as not to keep asking to start it forever if the settings are somewhere else)."""
    marker = _log_file(folder)[:-len(".json")] + ".avvio"
    if os.path.exists(marker):
        return False
    try:
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        with open(marker, "w", encoding="utf-8") as f:
            f.write(_now())
    except OSError:
        pass
    return True


def _is_open(folder):
    """None if the game is closed, otherwise the reason not to touch anything."""
    from . import system
    try:
        if system.game_open(folder):
            return "the game is open: close it and try again"
    except system.SysError as e:
        return str(e)
    return None


def set_(folder, detection, dest_lang, log=print, consent=False):
    """Sets the language where it can be done safely. Returns (fatto, [lines to show]); fatto is None when the
    user's consent is needed (e.g. to modify the executables): then the lines are the proposal."""
    m = detection.get("engine") or {}
    name = language.label(dest_lang)
    in_menu = "choose %s in the game's options menu" % name
    if m.get("kind") == "sheet":
        from . import games as sheets
        if detection.get("steam") and detection.get("steam_selectable"):
            return False, []  # installed from Steam: the language is chosen there (the message says where)
        sheet = sheets.by_id(m.get("sheet"))
        if sheet and not m.get("root") and len(m.get("roots") or []) > 1:
            return False, ["The folder contains %d copies of %s (%s): choose the folder of just one."
                           % (len(m["roots"]), sheet.NAME, "; ".join(m["roots"]))]
        if sheet and m.get("root"):
            return sheet.set_language(m["root"], dest_lang, consent, log)
    if m.get("kind") == "unreal" and m.get("tag"):
        from . import language_unreal
        gus = language_unreal.settings_file(m.get("project"), folder)
        if not gus:
            if _first_time(folder):
                return False, ["I can't find the game settings yet: start it once and close it, then repeat 'Analyze "
                               "and translate' and I'll try to set %s myself (or %s)." % (name, in_menu)]
            return False, ["I can't find the game settings (GameUserSettings.ini): %s." % in_menu]
        try:
            _, other_ones = language_unreal.read_internationalization(gus)
        except (OSError, ValueError) as e:
            return False, ["I was unable to access the game settings (%s): %s." % (e, in_menu)]
        if other_ones:
            return False, ["The game handles the language by itself (%s): %s." % (", ".join(other_ones[:2]), in_menu)]
        reason = _is_open(folder)
        if reason:
            return False, ["I'm not setting the language: %s ('Analyze and translate' sets it)." % reason]
        try:
            changes = language_unreal.set_culture(gus, m["tag"])
        except (OSError, ValueError) as e:
            return False, ["I wasn't able to save the game settings (%s): %s." % (e, in_menu)]
        if not changes:
            return True, ["The game settings are already set to %s (%s)." % (name, gus),
                          "If the game is still in another language, %s." % in_menu]
        previous = _read_log(folder)
        if previous and previous.get("kind") == "unreal" and previous.get("file") == gus:
            changes = previous["changes"] + changes  # the oldest values (the original ones) win
        lines = ["I have set %s in the game settings (%s, [Internationalization])." % (name, gus),
                 "Start the game: if it's still in another language, the game uses its own menu: %s." % in_menu]
        try:
            _save_log(folder, {"kind": "unreal", "file": gus, "tag": m["tag"], "changes": changes,
                                       "date": _now()})
        except OSError as e:
            lines.append("Warning: I couldn't save the restore log (%s)." % e)
        return True, lines
    if m.get("kind") == "unity" and m.get("i2_language"):
        from . import language_unity
        company, product = m.get("company"), m.get("product")
        if not company or not product:
            return False, [in_menu[0].upper() + in_menu[1:] + "."]
        if m.get("i2_custom"):
            return False, ["The game saves the language in its own way: %s." % in_menu]
        try:
            previous = language_unity.read_pref(company, product, language_unity.PREF_I2)
        except OSError as e:
            return False, ["I was unable to access the game's settings (%s): %s." % (e, in_menu)]
        if previous is False:
            if _first_time(folder):
                return False, ["The game has no preferences yet: start it once and close it, then repeat 'Analyze and "
                               "translate' and I'll try to set %s myself (or %s)." % (name, in_menu)]
            return False, ["I can't find the game settings: %s." % in_menu]
        value_ = m["i2_language"]
        written = value_.encode("utf-8") + b"\x00"
        if previous and previous[1] == written:
            return True, ["The game's language is already set to %s." % name,
                          "If the game is still in another language, %s." % in_menu]
        reason = _is_open(folder)
        if reason:
            return False, ["I'm not setting the language: %s ('Analyze and translate' sets it)." % reason]
        try:
            language_unity.write_pref_string(company, product, language_unity.PREF_I2, value_)
        except (OSError, ValueError) as e:
            return False, ["I was unable to set the language (%s): %s." % (e, in_menu)]
        old = _read_log(folder)
        if old and old.get("kind") == "unity_i2" and old.get("company") == company and \
                old.get("product") == product:
            before = old.get("previous")  # the value from before the first time
        else:
            before = [previous[0], previous[1].hex()] if previous else None
        lines = ["I have set the game language to %s (following I2 Localization's preference: \"%s\")."
                 % (name, value_),
                 "Start the game: it should start in %s. If it's still in another language, the game uses its own "
                 "setting: %s." % (name, in_menu)]
        try:
            _save_log(folder, {"kind": "unity_i2", "company": company, "product": product,
                                       "key": language_unity.PREF_I2, "written": written.hex(), "previous": before,
                                       "date": _now()})
        except OSError as e:
            lines.append("Warning: I couldn't save the restore log (%s)." % e)
        return True, lines
    return False, []


def set_added_language(folder, name, languages, log=print, source=None, force=False):
    """After adding a language to the game (e.g. local/italian.json of a GameMaker game), sets it in the options
    saved by the game, with a backup copy and a log for the restore. (fatto, [righe]).
    If the player chose another language after the previous time, their choice stays. forza=True: the language was
    changed by the game, not by the player (e.g. an update had removed the added language's file and the game went
    back to the original: the after-play check says so)."""
    from . import language_gamemaker
    from .project import data_folder
    reason = _is_open(folder)
    if reason:
        return False, ["I'm not setting the language '%s' in the game options: %s." % (name, reason)]
    saves = data_folder("save-backups", "lingua-" + os.path.basename(_log_file(folder))[:-5])
    old = _read_log(folder)
    if not (old and old.get("kind") == "gamemaker_options"):
        old = None
    try:
        log_file, reason = language_gamemaker.set_(folder, name, languages, saves, log,
                                                    respect=old.get("written") if old and not force else None)
    except (OSError, ValueError, KeyError, StopIteration) as e:
        return False, ["I wasn't able to set the language in the game options (%s)." % e]
    if log_file is None:
        return False, ["I don't set the language from outside: %s." % reason]
    if old and old.get("file") == log_file["file"] and old.get("previous"):
        log_file["previous"] = old["previous"]  # the language from before the first time wins
    elif not log_file.get("previous") and source:
        log_file["previous"] = source  # already set (e.g. by the player): on restore the original comes back
    log_file["date"] = _now()
    try:
        _save_log(folder, log_file)
    except OSError as e:
        return True, ["Warning: I couldn't save the restore log (%s)." % e]
    return True, []


def put_back_added_language(folder, additions, source, languages, log=print):
    """Before removing the languages added by PatchSmith (e.g. 'italiano'): if the options saved by the game still
    have it, the previous language is put back, so the game does not restart with a language that no longer exists.
    Only if PatchSmith set it for THIS folder (log): the options are in %LOCALAPPDATA% under the game's name, and a
    copy of the game (e.g. a test copy) shares them with the real one, which may have its own added language.
    Without a log everything is left alone (Forager, without the language file, goes back to English).
    None if there was nothing to do, True if put back, False if it was needed but could not be done."""
    from . import language_gamemaker
    log_file = _read_log(folder) or {}
    if log_file.get("kind") != "gamemaker_options":
        return None
    try:
        path, spot = language_gamemaker.search(folder, set(languages) | set(additions))
    except OSError:
        return None
    if (path is None or spot[3] not in additions
            or os.path.normcase(path) != os.path.normcase(log_file.get("file") or "")):
        return None
    previous = log_file.get("previous")
    if not previous or previous in additions:
        previous = source
    reason = _is_open(folder)
    if reason:
        log("  ! Game language not restored: %s." % reason)
        return False
    try:
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        from .adapter_webjson import write_atomic
        write_atomic(path, language_gamemaker.change(text, spot, previous).encode("utf-8"))
    except (OSError, ValueError) as e:
        log("  ! I was unable to set the language back to '%s' in the game options (%s)." % (previous, e))
        return False
    log("  Game language: set back to '%s' (it was '%s', the added language)" % (previous, spot[3]))
    if log_file.get("kind") == "gamemaker_options":
        try:
            os.remove(_log_file(folder))
        except OSError:
            pass
    return True


def _lever_roots(folder):
    """The folders where the configurator's language lever can be (the chosen one and the game's)."""
    from . import configurator, games as sheets
    roots = [os.path.abspath(folder)]
    for root in sheets.identify(folder)[1]:
        if os.path.normcase(root) not in {os.path.normcase(r) for r in roots}:
            roots.append(root)
    return [r for r in roots if configurator.lever(r, "language")]


def is_set(folder):
    return os.path.exists(_log_file(folder)) or bool(_lever_roots(folder))


def restore(folder, log=print):
    """Puts the language back as it was before PatchSmith. None if there was nothing to put back, True if done (or
    if it had already changed in the meantime), False if it could not be done: the log stays so it can be retried."""
    from . import configurator
    outcomes = [_restore_setting(folder, log)]
    for root in _lever_roots(folder):
        outcome = configurator.restore(root, "language", log)
        if outcome:
            log("  Game language: set back to how it was")
        outcomes.append(outcome)
    if False in outcomes:
        return False
    return True if True in outcomes else None


def _restore_setting(folder, log):
    path = _log_file(folder)
    if not os.path.exists(path):
        return None
    data = _read_log(folder)
    if not isinstance(data, dict):
        log("  ! Game language: unreadable registry (%s): choose the language in the game menu." % path)
        return False
    reason = _is_open(folder)
    if reason:
        log("  ! Game language not restored: %s (still to do)." % reason)
        return False
    try:
        if data.get("kind") == "unreal":
            from . import language_unreal
            ok = language_unreal.restore_culture(data["file"], data["changes"], data["tag"]) > 0
        elif data.get("kind") == "unity_i2":
            from . import language_unity
            previous = tuple(data["previous"]) if data.get("previous") else None
            ok = language_unity.restore_pref(data["company"], data["product"], data["key"], previous,
                                              bytes.fromhex(data["written"]))
        elif data.get("kind") == "gamemaker_options":
            from . import language_gamemaker
            ok = language_gamemaker.restore(data)
        else:
            ok = False
    except (OSError, ValueError, TypeError, KeyError) as e:
        where = data.get("file") or "Windows registry, HKCU\\Software\\%s\\%s" % (data.get("company"),
                                                                                   data.get("product"))
        log("  ! I wasn't able to revert the game's language to its original setting (%s; %s): either remove read-only "
            "mode and try again, or select the language in the game's menu." % (e, where))
        return False
    log("  Game language: %s" % ("restored to how it was" if ok else
                                    "changed in the meantime (by the game or by you): leaving it as it is"))
    os.remove(path)
    return True


# --- messages -------------------------------------------------------------------------------------

def message(detection, dest_lang, setting=None, done=False):
    """Lines to show the user when the official language is there."""
    name = language.label(dest_lang)
    lines = ["The game already has an official translation in %s: it's better than an automated translation." % name]
    lines += ["  - " + f for f in detection["sources"]]
    if setting:
        lines += setting
    if done is None:  # proposal waiting for the user's consent: no other instructions
        return lines
    if (detection.get("engine") or {}).get("kind") == "sheet" and setting:
        return lines  # the game sheet already said what it did, or why not and what to do
    info = detection.get("steam")
    if info:
        if detection.get("steam_language") == _steam_name(dest_lang):
            lines.append("Steam already uses %s for this game: if the game is in another language, select it from its "
                         "options." % name)
        elif detection.get("steam_selectable"):
            lines.append('In Steam: Library > Right-click on "%s" > Properties > General > Language > %s.' % (
                info.get("name") or "the game", name))
        elif not setting:
            lines.append("Choose %s in the game options (this game doesn't have a language menu on Steam)." % name)
    elif detection.get("steam_known"):
        if done is False:
            lines.append("I can't set it myself: this folder isn't a Steam installation, and many games use the "
                         "language that the store (Steam or Epic) passes to them at startup.")
            if detection.get("steam_selectable"):
                lines.append("From Steam: Library > Right-click on the game > Properties > General > Language > %s."
                             % name)
            lines.append("Alternatively, try searching for %s in the game's options or in the launcher from which you "
                         "launch it." % name)
    elif not setting:
        lines.append("To activate it: choose %s in the game options (or in the launcher from which you launch it)."
                     % name)
    return lines
