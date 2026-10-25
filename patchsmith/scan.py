"""Scan of the game folder: finds the files that contain readable text."""
import json
import os

from . import adapter_csv, adapter_gamemaker, adapter_webjson, interruption
from .text import is_readable_text

EXCLUDED_FOLDERS = {"node_modules", ".git", "__pycache__", ".svn", "swiftshader",
                    # Steam emulators: not part of the game, PatchSmith does not touch them
                    # (e.g. steam_settings/achievements.json)
                    "steam_settings", "steamsettings", "smartsteamemu", "steamemu", "ali213"}
MAX_JSON_SIZE = 64 * 1024 * 1024
OTHER_FORMATS = {
    ".csv": "CSV", ".tsv": "CSV", ".po": "gettext PO", ".pot": "gettext PO", ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML", ".txt": "text", ".rpy": "Ren'Py", ".locres": "Unreal locres",
    ".ks": "KiriKiri/Tyrano", ".csv.import": None, ".translation": "Godot", ".mo": "gettext MO",
}
CHARS_THRESHOLD = 500
STRINGS_THRESHOLD = 10
# a {key: text} file without the language in its name is a text table if at least this share of its values is text
# also for the rules of generic JSON files (sentences, capitalized words): not a list of files or colors
TABLE_SHARE = 0.4
# detected engine -> font characters to respect in the game profiles of the generic search (see Project._adapt)
FONT_ENGINES = {"GameMaker": "gamemaker", "XNA": "xna", "FNA": "xna", "MonoGame": "xna"}


def _game_files(folder):
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED_FOLDERS)
        for f in sorted(files):
            if adapter_webjson.MARKER in f:  # backups, info and scripts we created
                continue
            yield os.path.join(root, f)


def engine_data(data):
    """The engine whose project data the JSON is, or None. Its strings are the names of the objects, layouts,
    animations, sounds and variables that the game looks up, not texts: translated, the game no longer finds them
    (Construct: data.json of the export, {"project": [name, first layout, plugins, object types...]})."""
    if isinstance(data, dict) and list(data) == ["project"] and isinstance(data["project"], list) \
            and len(data["project"]) > 10 and isinstance(data["project"][0], str):
        return "Construct"
    return None


def _json_candidate(rel, data):
    """The candidate of a JSON file: readable texts by the generic rules and, if the file is a {key: text}
    dictionary (with sections too), also by the rules of localization files (where a single word is a text: 'wood',
    'OK')."""
    stat = adapter_webjson.schema_stats(data)
    include, _ = adapter_webjson.suggest_schemas(stat)
    c = {"path": rel, "readable_strings": sum(stat[s]["readable"] for s in include),
         "chars": sum(stat[s]["chars"] for s in include), "stats": stat}
    values = adapter_gamemaker._strings(data)
    if values is not None:
        texts = [v for v in values if adapter_gamemaker.localization_text(v)]
        c["localization_patterns"] = adapter_gamemaker.json_patterns(data)
        c["localization_texts"] = [len(texts), sum(len(v) for v in texts)]
        # a text table has only texts and sections: a list of objects is a data file (e.g. PluginInfo.json of the
        # Wwise sounds, with the names and types of the libraries), where a single word is an identifier; and it is
        # not a save or an options file ('High', 'On' translated could no longer be read)
        c["table"] = _sections_only(data) and not adapter_gamemaker._TO_SKIP.search(rel) and \
            sum(1 for v in values if is_readable_text(v)) >= TABLE_SHARE * len(values)
    return c


def _sections_only(data, depth=0):
    """True if the dictionary has only simple values and {key: value} sections, no lists."""
    return isinstance(data, dict) and depth < 20 and all(
        not isinstance(v, (dict, list)) or _sections_only(v, depth + 1) for v in data.values())


def _csv_candidate(rel, f):
    i, code = f.source()
    texts = [t for _, t in f.texts(i)]
    return {"path": rel, "format": "csv", "column": f.header[i].strip().lstrip("\ufeff"),
            "language": code, "languages": sorted(f.full_languages(i)), "readable_strings": len(texts),
            "chars": sum(len(t) for t in texts), "stats": {}}


def _mark_localization(candidates):
    """The {key: text} JSON files that are localization files: text tables (see TABLE_SHARE) or files of the
    same family in at least two languages (lang/{L}.json, strings{L}.json). The rules of localization files apply to
    them: single words are translated too."""
    families = {}
    for c in candidates:
        code = adapter_gamemaker.path_language(c["path"])
        if "localization_patterns" in c and code:
            families.setdefault(adapter_gamemaker.family(c["path"]), set()).add(code.split("-")[0])
    for c in candidates:
        if "localization_patterns" not in c:
            continue
        code = adapter_gamemaker.path_language(c["path"])
        if c["table"] or (code and len(families.get(adapter_gamemaker.family(c["path"]), ())) >= 2):
            c["localization"] = True
            c["readable_strings"], c["chars"] = c["localization_texts"]


def scan_folder(folder, log=print, engine_files=None):
    """Returns (candidates, others).

    candidates: [{"path": rel, "readable_strings": n, "chars": c, "stats": {...}}], plus
    "localization" for {key: text} files and "format": "csv" for tables with one column per language.
    The skipped files (too large, unreadable JSON, engine project data) go to the log with how many they are.
    engine_files: list that receives the paths of the engine project data files (see engine_data).
    """
    candidates, others, large, unreadable, engines = [], {}, [], [], {}
    for path in _game_files(folder):
        interruption.check()
        rel = os.path.relpath(path, folder).replace(os.sep, "/")
        extension = os.path.splitext(path)[1].lower()
        if extension == ".json":
            try:
                size = os.path.getsize(path)
                if size > MAX_JSON_SIZE:
                    large.append("%s (%d MB)" % (rel, size // (1024 * 1024)))
                    continue
                f = adapter_webjson.JSONFile(path)
            except (OSError, ValueError, UnicodeDecodeError):
                unreadable.append(rel)
                continue
            engine = engine_data(f.data)
            if engine:
                engines.setdefault(engine, []).append(rel)
                if engine_files is not None:
                    engine_files.append(rel)
                continue
            c = _json_candidate(rel, f.data)
            if c["readable_strings"] or c.get("localization_texts", [0])[0]:
                candidates.append(c)
        elif extension in adapter_csv.EXTENSIONS:
            if os.path.exists(path + ".import"):  # Godot source: the game uses the compiled file
                continue
            f = adapter_csv.table(adapter_gamemaker.read_original(path, lambda x: x))
            if f:
                candidates.append(_csv_candidate(rel, f))
            else:
                others.setdefault(OTHER_FORMATS[extension], []).append(rel)
        elif OTHER_FORMATS.get(extension):
            others.setdefault(OTHER_FORMATS[extension], []).append(rel)
    _mark_localization(candidates)
    candidates = [c for c in candidates if c["readable_strings"]]
    candidates.sort(key=lambda c: -c["chars"])
    if large:
        log("  ! %d JSON files too large, not read (over %d MB): %s" % (
            len(large), MAX_JSON_SIZE // (1024 * 1024), ", ".join(large[:3])))
    if unreadable:
        log("  ! %d .json files not read because they are not valid JSON: %s" % (
            len(unreadable), ", ".join(unreadable[:3])))
    for engine, paths in sorted(engines.items()):
        log("  Project data of %s, not translated (names that the game looks up, not texts): %s" % (
            engine, ", ".join(paths[:3])))
    return candidates, others


def above_threshold(c):
    """True if the candidate has enough texts to enter the game profile. A CSV table with one column per language
    always enters (it is surely a translation table: see adapter_csv.CSVFile.table); for localization files the
    number of texts is enough (one-word labels are short)."""
    if c.get("format") == "csv":
        return True
    if c.get("localization"):
        return c["readable_strings"] >= STRINGS_THRESHOLD
    return c["chars"] >= CHARS_THRESHOLD and c["readable_strings"] >= STRINGS_THRESHOLD


def create_profile(name, folder, candidates, origin, dest_lang, backend, web_roots=(), engine=None, log=None):
    """Game profile with the files above the threshold and the proposed schemas (can be edited by hand).

    web_roots: index.html of web games (relative paths): they become 'web-ui' entries for the menus.
    engine: the detected engine ('GameMaker', 'XNA'...): for GameMaker and XNA the translations adapt to the characters
    of the game's fonts (profile["game_engine"]; GameMaker is also profile["engine"]).
    log: to write which files with few texts are left out.
    """
    file, excluded = [], []
    for index in web_roots:
        if "!/" in index:  # inside an .asar archive: not supported yet
            continue
        file.append({"path": index, "format": "web-ui"})
    for c in candidates:
        if not above_threshold(c):
            excluded.append(c)
            continue
        if c.get("format") == "csv":
            file.append({"path": c["path"], "format": "csv", "column": c["column"], "localization": True})
            continue
        if c.get("localization"):
            file.append({"path": c["path"], "format": "json", "localization": True,
                         "include": c["localization_patterns"], "exclude": []})
            continue
        include, exclude = adapter_webjson.suggest_schemas(c["stats"])
        file.append({
            "path": c["path"],
            "format": "json",
            "include": include,
            "exclude": exclude,
        })
    if log and excluded:
        log("  %d files with little text not included (fewer than %d texts or %d characters): %s" % (
            len(excluded), STRINGS_THRESHOLD, CHARS_THRESHOLD, ", ".join(
                "%s (%d texts, %d characters)" % (c["path"], c["readable_strings"], c["chars"])
                for c in excluded[:3])))
    profile = {
        "name": name,
        "game_folder": os.path.abspath(folder).replace(os.sep, "/"),
        "source_language": origin,
        "target_language": dest_lang,
        "backend": backend,
        "file": file,
        "_note": "Move the patterns between 'include' and 'exclude' to decide what to translate. Values that don't "
                 "appear to be text (id, file, colors...) are ignored anyway.",
    }
    font = FONT_ENGINES.get(engine)
    if font:
        profile["game_engine"] = font
        if font == "gamemaker":
            profile["engine"] = "gamemaker"
    return profile


def print_summary(candidates, others, log=print, detail=None):
    if not candidates:
        log("No readable text file found.")
    else:
        log("Files with readable text (first 15):")
        for c in candidates[:15]:
            marker = "*" if above_threshold(c) else " "
            kind = " (CSV table, column %s)" % c["column"] if c.get("format") == "csv" else \
                " (language file)" if c.get("localization") else ""
            log("  %s %7d characters %5d strings  %s%s" % (marker, c["chars"], c["readable_strings"],
                                                            c["path"], kind))
        log("  (* = included in profile)")
        if len(candidates) > 15:
            log("  ... and %d more files (%d included in the profile)" % (
                len(candidates) - 15, sum(1 for c in candidates[15:] if above_threshold(c))))
    for fmt, items in sorted(others.items()):
        log("Other text files (%s, not yet supported): %d, for example, %s" % (fmt, len(items), items[0]))
    if detail:
        for c in candidates:
            if c["path"] != detail:
                continue
            log("Patterns in %s:" % detail)
            for pattern, s in sorted(c["stats"].items(), key=lambda x: -x[1]["chars"]):
                if s["readable"]:
                    log("  %6d chars %4d/%-4d %s" % (s["chars"], s["readable"], s["strings"], pattern))
                    for e in s["examples"][:2]:
                        log("              > %s" % e)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_file = path + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_file, path)
