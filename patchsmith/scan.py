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
    ".yaml": "YAML", ".yml": "YAML", ".txt": "testo", ".rpy": "Ren'Py", ".locres": "Unreal locres",
    ".ks": "KiriKiri/Tyrano", ".csv.import": None, ".translation": "Godot", ".mo": "gettext MO",
}
CHARS_THRESHOLD = 500
STRINGS_THRESHOLD = 10
# a {chiave: testo} file without the language in its name is a text table if at least this share of its values is text
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


def _json_candidate(rel, data):
    """The candidate of a JSON file: readable texts by the generic rules and, if the file is a {chiave: testo}
    dictionary (with sections too), also by the rules of localization files (where a single word is a text: 'wood',
    'OK')."""
    stat = adapter_webjson.schema_stats(data)
    include, _ = adapter_webjson.suggest_schemas(stat)
    c = {"percorso": rel, "stringhe_leggibili": sum(stat[s]["leggibili"] for s in include),
         "caratteri": sum(stat[s]["caratteri"] for s in include), "statistiche": stat}
    values = adapter_gamemaker._strings(data)
    if values is not None:
        texts = [v for v in values if adapter_gamemaker.localization_text(v)]
        c["schemi_localizzazione"] = adapter_gamemaker.json_patterns(data)
        c["testi_localizzazione"] = [len(texts), sum(len(v) for v in texts)]
        # a text table has only texts and sections: a list of objects is a data file (e.g. PluginInfo.json of the
        # Wwise sounds, with the names and types of the libraries), where a single word is an identifier; and it is
        # not a save or an options file ('High', 'On' translated could no longer be read)
        c["tabella"] = _sections_only(data) and not adapter_gamemaker._TO_SKIP.search(rel) and \
            sum(1 for v in values if is_readable_text(v)) >= TABLE_SHARE * len(values)
    return c


def _sections_only(data, depth=0):
    """True if the dictionary has only simple values and {chiave: valore} sections, no lists."""
    return isinstance(data, dict) and depth < 20 and all(
        not isinstance(v, (dict, list)) or _sections_only(v, depth + 1) for v in data.values())


def _csv_candidate(rel, f):
    i, code = f.source()
    texts = [t for _, t in f.texts(i)]
    return {"percorso": rel, "formato": "csv", "colonna": f.header[i].strip().lstrip("\ufeff"),
            "lingua": code, "lingue": sorted(f.full_languages(i)), "stringhe_leggibili": len(texts),
            "caratteri": sum(len(t) for t in texts), "statistiche": {}}


def _mark_localization(candidates):
    """The {chiave: testo} JSON files that are localization files: text tables (see TABLE_SHARE) or files of the
    same family in at least two languages (lang/{L}.json, strings{L}.json). The rules of localization files apply to
    them: single words are translated too."""
    families = {}
    for c in candidates:
        code = adapter_gamemaker.path_language(c["percorso"])
        if "schemi_localizzazione" in c and code:
            families.setdefault(adapter_gamemaker.family(c["percorso"]), set()).add(code.split("-")[0])
    for c in candidates:
        if "schemi_localizzazione" not in c:
            continue
        code = adapter_gamemaker.path_language(c["percorso"])
        if c["tabella"] or (code and len(families.get(adapter_gamemaker.family(c["percorso"]), ())) >= 2):
            c["localizzazione"] = True
            c["stringhe_leggibili"], c["caratteri"] = c["testi_localizzazione"]


def scan_folder(folder, log=print):
    """Returns (candidati, altri_formati).

    candidati: [{"percorso": rel, "stringhe_leggibili": n, "caratteri": c, "statistiche": {...}}], plus
    "localizzazione" for {chiave: testo} files and "formato": "csv" for tables with one column per language.
    The skipped files (too large, unreadable JSON) go to the log with how many they are.
    """
    candidates, others, large, unreadable = [], {}, [], []
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
            c = _json_candidate(rel, f.data)
            if c["stringhe_leggibili"] or c.get("testi_localizzazione", [0])[0]:
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
    candidates = [c for c in candidates if c["stringhe_leggibili"]]
    candidates.sort(key=lambda c: -c["caratteri"])
    if large:
        log("  ! %d file JSON troppo grandi, non letti (oltre %d MB): %s" % (
            len(large), MAX_JSON_SIZE // (1024 * 1024), ", ".join(large[:3])))
    if unreadable:
        log("  ! %d file .json non letti perche' non sono JSON validi: %s" % (
            len(unreadable), ", ".join(unreadable[:3])))
    return candidates, others


def above_threshold(c):
    """True if the candidate has enough texts to enter the game profile. A CSV table with one column per language
    always enters (it is surely a translation table: see adapter_csv.CSVFile.table); for localization files the
    number of texts is enough (one-word labels are short)."""
    if c.get("formato") == "csv":
        return True
    if c.get("localizzazione"):
        return c["stringhe_leggibili"] >= STRINGS_THRESHOLD
    return c["caratteri"] >= CHARS_THRESHOLD and c["stringhe_leggibili"] >= STRINGS_THRESHOLD


def create_profile(name, folder, candidates, origin, dest_lang, backend, web_roots=(), engine=None, log=None):
    """Game profile with the files above the threshold and the proposed schemas (can be edited by hand).

    web_roots: index.html of web games (relative paths): they become 'web-ui' entries for the menus.
    motore: the detected engine ('GameMaker', 'XNA'...): for GameMaker and XNA the translations adapt to the characters
    of the game's fonts (profilo["motore_gioco"]; GameMaker is also profilo["motore"]).
    log: to write which files with few texts are left out.
    """
    file, excluded = [], []
    for index in web_roots:
        if "!/" in index:  # inside an .asar archive: not supported yet
            continue
        file.append({"percorso": index, "formato": "web-ui"})
    for c in candidates:
        if not above_threshold(c):
            excluded.append(c)
            continue
        if c.get("formato") == "csv":
            file.append({"percorso": c["percorso"], "formato": "csv", "colonna": c["colonna"], "localizzazione": True})
            continue
        if c.get("localizzazione"):
            file.append({"percorso": c["percorso"], "formato": "json", "localizzazione": True,
                         "includi": c["schemi_localizzazione"], "escludi": []})
            continue
        include, exclude = adapter_webjson.suggest_schemas(c["statistiche"])
        file.append({
            "percorso": c["percorso"],
            "formato": "json",
            "includi": include,
            "escludi": exclude,
        })
    if log and excluded:
        log("  %d file con pochi testi non inclusi (meno di %d testi o %d caratteri): %s" % (
            len(excluded), STRINGS_THRESHOLD, CHARS_THRESHOLD, ", ".join(
                "%s (%d testi, %d caratteri)" % (c["percorso"], c["stringhe_leggibili"], c["caratteri"])
                for c in excluded[:3])))
    profile = {
        "nome": name,
        "cartella_gioco": os.path.abspath(folder).replace(os.sep, "/"),
        "lingua_origine": origin,
        "lingua_destinazione": dest_lang,
        "backend": backend,
        "file": file,
        "_nota": "Sposta gli schemi tra 'includi' ed 'escludi' per decidere cosa tradurre. "
                 "I valori che non sembrano testo (id, file, colori...) vengono saltati comunque.",
    }
    font = FONT_ENGINES.get(engine)
    if font:
        profile["motore_gioco"] = font
        if font == "gamemaker":
            profile["motore"] = "gamemaker"
    return profile


def print_summary(candidates, others, log=print, detail=None):
    if not candidates:
        log("Nessun file con testo leggibile trovato.")
    else:
        log("File con testo leggibile (i primi 15):")
        for c in candidates[:15]:
            marker = "*" if above_threshold(c) else " "
            kind = " (tabella CSV, colonna %s)" % c["colonna"] if c.get("formato") == "csv" else \
                " (file di lingua)" if c.get("localizzazione") else ""
            log("  %s %7d caratteri %5d stringhe  %s%s" % (marker, c["caratteri"], c["stringhe_leggibili"],
                                                            c["percorso"], kind))
        log("  (* = incluso nel profilo)")
        if len(candidates) > 15:
            log("  ... e altri %d file (%d inclusi nel profilo)" % (
                len(candidates) - 15, sum(1 for c in candidates[15:] if above_threshold(c))))
    for fmt, items in sorted(others.items()):
        log("Altri file di testo (%s, non ancora supportati): %d, es. %s" % (fmt, len(items), items[0]))
    if detail:
        for c in candidates:
            if c["percorso"] != detail:
                continue
            log("Schemi in %s:" % detail)
            for pattern, s in sorted(c["statistiche"].items(), key=lambda x: -x[1]["caratteri"]):
                if s["leggibili"]:
                    log("  %6d car. %4d/%-4d %s" % (s["caratteri"], s["leggibili"], s["stringhe"], pattern))
                    for e in s["esempi"][:2]:
                        log("              > %s" % e)


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_file = path + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temp_file, path)
