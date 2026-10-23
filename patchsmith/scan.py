"""Scan of the game folder: finds the files that contain readable text."""
import json
import os

from . import adapter_csv, adapter_gamemaker, adapter_webjson, interruption as interruzione
from .text import e_testo_leggibile

CARTELLE_ESCLUSE = {"node_modules", ".git", "__pycache__", ".svn", "swiftshader",
                    # Steam emulators: not part of the game, PatchSmith does not touch them
                    # (e.g. steam_settings/achievements.json)
                    "steam_settings", "steamsettings", "smartsteamemu", "steamemu", "ali213"}
MAX_DIMENSIONE_JSON = 64 * 1024 * 1024
ALTRI_FORMATI = {
    ".csv": "CSV", ".tsv": "CSV", ".po": "gettext PO", ".pot": "gettext PO", ".xml": "XML",
    ".yaml": "YAML", ".yml": "YAML", ".txt": "testo", ".rpy": "Ren'Py", ".locres": "Unreal locres",
    ".ks": "KiriKiri/Tyrano", ".csv.import": None, ".translation": "Godot", ".mo": "gettext MO",
}
SOGLIA_CARATTERI = 500
SOGLIA_STRINGHE = 10
# a {chiave: testo} file without the language in its name is a text table if at least this share of its values is text
# also for the rules of generic JSON files (sentences, capitalized words): not a list of files or colors
QUOTA_TABELLA = 0.4
# detected engine -> font characters to respect in the game profiles of the generic search (see Progetto._adatta)
MOTORI_FONT = {"GameMaker": "gamemaker", "XNA": "xna", "FNA": "xna", "MonoGame": "xna"}


def _file_del_gioco(cartella):
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if d.lower() not in CARTELLE_ESCLUSE)
        for f in sorted(files):
            if adapter_webjson.MARCATORE in f:  # backups, info and scripts we created
                continue
            yield os.path.join(radice, f)


def _candidato_json(rel, dati):
    """The candidate of a JSON file: readable texts by the generic rules and, if the file is a {chiave: testo}
    dictionary (with sections too), also by the rules of localization files (where a single word is a text: 'wood',
    'OK')."""
    stat = adapter_webjson.statistiche_schemi(dati)
    includi, _ = adapter_webjson.proponi_schemi(stat)
    c = {"percorso": rel, "stringhe_leggibili": sum(stat[s]["leggibili"] for s in includi),
         "caratteri": sum(stat[s]["caratteri"] for s in includi), "statistiche": stat}
    valori = adapter_gamemaker._stringhe(dati)
    if valori is not None:
        testi = [v for v in valori if adapter_gamemaker.testo_localizzazione(v)]
        c["schemi_localizzazione"] = adapter_gamemaker.schemi_json(dati)
        c["testi_localizzazione"] = [len(testi), sum(len(v) for v in testi)]
        # a text table has only texts and sections: a list of objects is a data file (e.g. PluginInfo.json of the
        # Wwise sounds, with the names and types of the libraries), where a single word is an identifier; and it is
        # not a save or an options file ('High', 'On' translated could no longer be read)
        c["tabella"] = _solo_sezioni(dati) and not adapter_gamemaker._DA_SALTARE.search(rel) and \
            sum(1 for v in valori if e_testo_leggibile(v)) >= QUOTA_TABELLA * len(valori)
    return c


def _solo_sezioni(dati, profondita=0):
    """True if the dictionary has only simple values and {chiave: valore} sections, no lists."""
    return isinstance(dati, dict) and profondita < 20 and all(
        not isinstance(v, (dict, list)) or _solo_sezioni(v, profondita + 1) for v in dati.values())


def _candidato_csv(rel, f):
    i, codice = f.sorgente()
    testi = [t for _, t in f.testi(i)]
    return {"percorso": rel, "formato": "csv", "colonna": f.intestazione[i].strip().lstrip("\ufeff"),
            "lingua": codice, "lingue": sorted(f.lingue_piene(i)), "stringhe_leggibili": len(testi),
            "caratteri": sum(len(t) for t in testi), "statistiche": {}}


def _segna_localizzazione(candidati):
    """The {chiave: testo} JSON files that are localization files: text tables (see QUOTA_TABELLA) or files of the
    same family in at least two languages (lang/{L}.json, strings{L}.json). The rules of localization files apply to
    them: single words are translated too."""
    famiglie = {}
    for c in candidati:
        codice = adapter_gamemaker.lingua_del_percorso(c["percorso"])
        if "schemi_localizzazione" in c and codice:
            famiglie.setdefault(adapter_gamemaker.famiglia(c["percorso"]), set()).add(codice.split("-")[0])
    for c in candidati:
        if "schemi_localizzazione" not in c:
            continue
        codice = adapter_gamemaker.lingua_del_percorso(c["percorso"])
        if c["tabella"] or (codice and len(famiglie.get(adapter_gamemaker.famiglia(c["percorso"]), ())) >= 2):
            c["localizzazione"] = True
            c["stringhe_leggibili"], c["caratteri"] = c["testi_localizzazione"]


def scansiona(cartella, log=print):
    """Returns (candidati, altri_formati).

    candidati: [{"percorso": rel, "stringhe_leggibili": n, "caratteri": c, "statistiche": {...}}], plus
    "localizzazione" for {chiave: testo} files and "formato": "csv" for tables with one column per language.
    The skipped files (too large, unreadable JSON) go to the log with how many they are.
    """
    candidati, altri, grandi, illeggibili = [], {}, [], []
    for percorso in _file_del_gioco(cartella):
        interruzione.controlla()
        rel = os.path.relpath(percorso, cartella).replace(os.sep, "/")
        estensione = os.path.splitext(percorso)[1].lower()
        if estensione == ".json":
            try:
                dimensione = os.path.getsize(percorso)
                if dimensione > MAX_DIMENSIONE_JSON:
                    grandi.append("%s (%d MB)" % (rel, dimensione // (1024 * 1024)))
                    continue
                f = adapter_webjson.FileJSON(percorso)
            except (OSError, ValueError, UnicodeDecodeError):
                illeggibili.append(rel)
                continue
            c = _candidato_json(rel, f.dati)
            if c["stringhe_leggibili"] or c.get("testi_localizzazione", [0])[0]:
                candidati.append(c)
        elif estensione in adapter_csv.ESTENSIONI:
            if os.path.exists(percorso + ".import"):  # Godot source: the game uses the compiled file
                continue
            f = adapter_csv.tabella(adapter_gamemaker.leggi_originale(percorso, lambda x: x))
            if f:
                candidati.append(_candidato_csv(rel, f))
            else:
                altri.setdefault(ALTRI_FORMATI[estensione], []).append(rel)
        elif ALTRI_FORMATI.get(estensione):
            altri.setdefault(ALTRI_FORMATI[estensione], []).append(rel)
    _segna_localizzazione(candidati)
    candidati = [c for c in candidati if c["stringhe_leggibili"]]
    candidati.sort(key=lambda c: -c["caratteri"])
    if grandi:
        log("  ! %d file JSON troppo grandi, non letti (oltre %d MB): %s" % (
            len(grandi), MAX_DIMENSIONE_JSON // (1024 * 1024), ", ".join(grandi[:3])))
    if illeggibili:
        log("  ! %d file .json non letti perche' non sono JSON validi: %s" % (
            len(illeggibili), ", ".join(illeggibili[:3])))
    return candidati, altri


def sopra_soglia(c):
    """True if the candidate has enough texts to enter the game profile. A CSV table with one column per language
    always enters (it is surely a translation table: see adapter_csv.FileCSV.tabella); for localization files the
    number of texts is enough (one-word labels are short)."""
    if c.get("formato") == "csv":
        return True
    if c.get("localizzazione"):
        return c["stringhe_leggibili"] >= SOGLIA_STRINGHE
    return c["caratteri"] >= SOGLIA_CARATTERI and c["stringhe_leggibili"] >= SOGLIA_STRINGHE


def crea_profilo(nome, cartella, candidati, origine, destinazione, backend, radici_web=(), motore=None, log=None):
    """Game profile with the files above the threshold and the proposed schemas (can be edited by hand).

    radici_web: index.html of web games (relative paths): they become 'web-ui' entries for the menus.
    motore: the detected engine ('GameMaker', 'XNA'...): for GameMaker and XNA the translations adapt to the characters
    of the game's fonts (profilo["motore_gioco"]; GameMaker is also profilo["motore"]).
    log: to write which files with few texts are left out.
    """
    file, esclusi = [], []
    for index in radici_web:
        if "!/" in index:  # inside an .asar archive: not supported yet
            continue
        file.append({"percorso": index, "formato": "web-ui"})
    for c in candidati:
        if not sopra_soglia(c):
            esclusi.append(c)
            continue
        if c.get("formato") == "csv":
            file.append({"percorso": c["percorso"], "formato": "csv", "colonna": c["colonna"], "localizzazione": True})
            continue
        if c.get("localizzazione"):
            file.append({"percorso": c["percorso"], "formato": "json", "localizzazione": True,
                         "includi": c["schemi_localizzazione"], "escludi": []})
            continue
        includi, escludi = adapter_webjson.proponi_schemi(c["statistiche"])
        file.append({
            "percorso": c["percorso"],
            "formato": "json",
            "includi": includi,
            "escludi": escludi,
        })
    if log and esclusi:
        log("  %d file con pochi testi non inclusi (meno di %d testi o %d caratteri): %s" % (
            len(esclusi), SOGLIA_STRINGHE, SOGLIA_CARATTERI, ", ".join(
                "%s (%d testi, %d caratteri)" % (c["percorso"], c["stringhe_leggibili"], c["caratteri"])
                for c in esclusi[:3])))
    profilo = {
        "nome": nome,
        "cartella_gioco": os.path.abspath(cartella).replace(os.sep, "/"),
        "lingua_origine": origine,
        "lingua_destinazione": destinazione,
        "backend": backend,
        "file": file,
        "_nota": "Sposta gli schemi tra 'includi' ed 'escludi' per decidere cosa tradurre. "
                 "I valori che non sembrano testo (id, file, colori...) vengono saltati comunque.",
    }
    font = MOTORI_FONT.get(motore)
    if font:
        profilo["motore_gioco"] = font
        if font == "gamemaker":
            profilo["motore"] = "gamemaker"
    return profilo


def stampa(candidati, altri, log=print, dettaglio=None):
    if not candidati:
        log("Nessun file con testo leggibile trovato.")
    else:
        log("File con testo leggibile (i primi 15):")
        for c in candidati[:15]:
            segno = "*" if sopra_soglia(c) else " "
            tipo = " (tabella CSV, colonna %s)" % c["colonna"] if c.get("formato") == "csv" else \
                " (file di lingua)" if c.get("localizzazione") else ""
            log("  %s %7d caratteri %5d stringhe  %s%s" % (segno, c["caratteri"], c["stringhe_leggibili"],
                                                            c["percorso"], tipo))
        log("  (* = incluso nel profilo)")
        if len(candidati) > 15:
            log("  ... e altri %d file (%d inclusi nel profilo)" % (
                len(candidati) - 15, sum(1 for c in candidati[15:] if sopra_soglia(c))))
    for formato, elenco in sorted(altri.items()):
        log("Altri file di testo (%s, non ancora supportati): %d, es. %s" % (formato, len(elenco), elenco[0]))
    if dettaglio:
        for c in candidati:
            if c["percorso"] != dettaglio:
                continue
            log("Schemi in %s:" % dettaglio)
            for schema, s in sorted(c["statistiche"].items(), key=lambda x: -x[1]["caratteri"]):
                if s["leggibili"]:
                    log("  %6d car. %4d/%-4d %s" % (s["caratteri"], s["leggibili"], s["stringhe"], schema))
                    for e in s["esempi"][:2]:
                        log("              > %s" % e)


def salva_json(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    temporaneo = percorso + ".tmp"
    with open(temporaneo, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(temporaneo, percorso)
