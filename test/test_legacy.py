"""Files written by earlier versions, with Italian keys and codes (patchsmith/legacy.py and project.migrate_formats).

Usage: python -I test/test_legacy.py <test_folder_that_does_not_exist>
- legacy.upgrade on old game profiles, settings, check state, journal lines, records, backup info and manifests:
  English keys and codes, maps of files and texts left as they are, "da" alone (Danish) untouched, nothing renamed
  twice (the new names are never old ones), the new files unchanged;
- PatchSmith's readers with old files: game profile found by its folder, backup info, check state, journal of the live
  translator, state and command of the BepInEx plugin, --backend finto on the command line;
- migrate_formats: profiles, records and settings rewritten with the original kept next to them, translation memories
  and the files written by the live translator untouched, nothing to do the second time.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_unity, auto as automatic, checks, cli, legacy, project,  # noqa: E402
                        xunity_service)

OUTCOMES = []

OLD_PROFILE = {"nome": "lantern-keeper", "cartella_gioco": "C:/Giochi/Lantern Keeper", "motore": "web",
               "lingua_origine": "en", "lingua_destinazione": "it", "backend": "locale", "dal_vivo": "servizio",
               "non_tradurre": ["Lantern Keeper"], "identita": {"nome": "lantern-keeper", "exe": ["lk.exe"]},
               "file": [{"percorso": "data/menu.json", "formato": "json", "includi": ["menu.*"], "escludi": [],
                         "riferimenti": {"da": "Afslut", "fr": "Quitter"}}]}
NEW_PROFILE = {"name": "lantern-keeper", "game_folder": "C:/Giochi/Lantern Keeper", "engine": "web",
               "source_language": "en", "target_language": "it", "backend": "local", "live": "service",
               "do_not_translate": ["Lantern Keeper"], "identity": {"name": "lantern-keeper", "exe": ["lk.exe"]},
               "file": [{"path": "data/menu.json", "format": "json", "include": ["menu.*"], "exclude": [],
                         "references": {"da": "Afslut", "fr": "Quitter"}}]}
OLD_SETTINGS = {"cartella": "C:/Giochi/Lantern Keeper", "da": "auto", "a": "it", "backend": "finto", "tema": "scuro",
                "interfaccia": "it", "chiave_deepl": "", "musica": False, "suoni": True, "versione": 2}
NEW_SETTINGS = {"folder": "C:/Giochi/Lantern Keeper", "from": "auto", "to": "it", "backend": "fake", "theme": "dark",
                "interface": "it", "deepl_key": "", "music": False, "sounds": True, "version": 2}
OLD_STATE = {"versione": 1, "ultimo_controllo": "2026-10-20T10:00:00",
             "visti": {"manifesto": 12.5, "diario": 3, "creato:x.json": "1/2"},
             "rimedi": {"GIOCO_APERTO": [{"segnale": "tolti:a.json", "quando": "t", "riuscito": True}],
                        "riconfigura": []},
             "ultimo": {"quando": "t", "corretti": 1, "da_fare": 0, "problemi": [
                 {"codice": "CRASH_AVVIO", "gravita": "errore", "messaggio": "...", "stato": "corretto",
                  "azione": "ripristina", "segnale": "crash:1", "sicuro": True, "dettaglio": ""}]}}
NEW_STATE = {"version": 1, "last_check": "2026-10-20T10:00:00",
             "seen": {"manifest": 12.5, "journal": 3, "created:x.json": "1/2"},
             "remedies": {"GAME_OPEN": [{"signal": "removed:a.json", "when": "t", "success": True}], "reconfigure": []},
             "last": {"when": "t", "fixed_count": 1, "to_do": 0, "problems": [
                 {"code": "STARTUP_CRASH", "severity": "error", "message": "...", "state": "fixed",
                  "action": "restore", "signal": "crash:1", "safe": True, "detail": ""}]}}
OLD_JOURNAL = {"ts": "2026-10-18T04:30:24", "testo": "Settings", "da": "en", "a": "it", "esito": "rifiutata",
               "backend": "memoria", "motivo": "x", "risposta": "Settings"}
NEW_JOURNAL = {"ts": "2026-10-18T04:30:24", "text": "Settings", "from": "en", "to": "it", "outcome": "rejected",
               "backend": "memory", "reason": "x", "response": "Settings"}
OLD_RECORD = {"tipo": "gamemaker_opzioni", "file": "options.ini", "posto": ["a", "b", 0], "precedente": "en",
              "scritto": "it", "data": "2026-10-20", "modifiche": [["riga", 1, "Culture", "en"], ["aggiunta", "x"],
                                                                 ["sezione", ["a"], False]]}
NEW_RECORD = {"kind": "gamemaker_options", "file": "options.ini", "spot": ["a", "b", 0], "previous": "en",
              "written": "it", "date": "2026-10-20", "changes": [["line", 1, "Culture", "en"], ["added", "x"],
                                                                 ["section", ["a"], False]]}
# backup info and manifest: the maps of files (and their names, even Italian words) stay as they are
OLD_INFO = {"hash_originale": "aa", "hash_scritti": ["bb"], "creati": ["dati"], "precedenti": {"dati": "cc"},
            "web_vivo": True}
NEW_INFO = {"original_hash": "aa", "written_hashes": ["bb"], "created": ["dati"], "prior": {"dati": "cc"},
            "web_live": True}
OLD_MANIFEST = {"patchsmith": True, "versione": 1, "data": "2026-10-20", "in_corso": False,
                "file": {"dati": "h1", "memoria": "h2", "testo": "h3"}, "cartelle_create": ["dati"]}
NEW_MANIFEST = {"patchsmith": True, "version": 1, "date": "2026-10-20", "in_progress": False,
                "file": {"dati": "h1", "memoria": "h2", "testo": "h3"}, "created_folders": ["dati"]}
SAMPLES = [("profile", OLD_PROFILE, NEW_PROFILE), ("settings", OLD_SETTINGS, NEW_SETTINGS),
           ("check state", OLD_STATE, NEW_STATE), ("journal line", OLD_JOURNAL, NEW_JOURNAL),
           ("language record", OLD_RECORD, NEW_RECORD), ("backup info", OLD_INFO, NEW_INFO),
           ("manifest", OLD_MANIFEST, NEW_MANIFEST)]


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=2))


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def test_upgrade():
    for label, old, new in SAMPLES:
        up = legacy.upgrade(old)
        check("%s: English keys and codes%s" % (label, "" if up == new else " (%s)" % up), up == new)
        check("%s: already new, stays as it is" % label, legacy.upgrade(new) == new)
    entries = {"nome": {"dove": "gioco", "motivo": "x", "tentativi": 2}, "data": {"dove": "memoria"}}
    check("texts with a problem: the texts stay (even when they are words like 'nome'), the entries are upgraded",
          legacy.upgrade_entries(entries) == {"nome": {"where": "game", "reason": "x", "attempts": 2},
                                              "data": {"where": "memory"}})
    check("'da' alone (Danish) does not change; 'da' and 'a' together do",
          legacy.upgrade({"da": "Dansk", "it": "Italiano"}) == {"da": "Dansk", "it": "Italiano"}
          and legacy.upgrade({"da": "en", "a": "it"}) == {"from": "en", "to": "it"})
    check("plugin state: keys and outcomes",
          legacy.upgrade({"Esito": "python-mancante", "Data": "d", "Processo": "1", "Programma": "p",
                          "Dettaglio": ""}) == {"Outcome": "python-missing", "Date": "d", "Process": "1",
                                                "Program": "p", "Detail": ""})
    chains = {k: v for k, v in legacy.KEYS.items() if v in legacy.KEYS}
    code_chains = {f: [c for c in codes.values() if c in codes] for f, codes in legacy.CODES.items()}
    signal_chains = [s for s in list(legacy.SIGNALS.values()) + list(legacy.SIGNAL_PREFIXES.values())
                     if s in legacy.SIGNALS or s in legacy.SIGNAL_PREFIXES]
    check("no new name is also an old name (%s %s %s)"
          % (chains, {f: c for f, c in code_chains.items() if c}, signal_chains),
          not chains and not any(code_chains.values()) and not signal_chains)
    check("command line: --backend finto and locale (earlier names)",
          cli._backend_name("finto") == "fake" and cli._backend_name("locale") == "local"
          and cli._backend_name("deepl") == "deepl")


def test_readers(base):
    data = project.DATA_ROOT
    game = os.path.join(base, "Lantern Keeper")
    os.makedirs(game)
    profile = dict(OLD_PROFILE, cartella_gioco=game.replace(os.sep, "/"))
    path = os.path.join(automatic.profiles_folder(), "lantern-keeper.json")
    write(path, profile)
    found = automatic.find_profile(game)
    check("earlier profile: found by its folder", found and os.path.normcase(found) == os.path.normcase(path))
    check("earlier profile: read with the new names",
          automatic._read_profile(path)["target_language"] == "it" and project.Project(path).profile["live"]
          == "service")
    menu = os.path.join(game, "data", "menu.json")
    write(menu, "{}")
    write(menu + project.INFO_SUFFIX, OLD_INFO)
    info = project.read_info(menu)
    check("earlier backup info: read (hash of the original)",
          info and info["original_hash"] == "aa" and info["prior"] == {"dati": "cc"})
    write(checks.state_path(path), OLD_STATE)
    state = checks.read_state(path)
    check("earlier check state: read",
          state.get("last_check") == "2026-10-20T10:00:00" and state["remedies"].get("GAME_OPEN"))
    write(xunity_service.journal_path("lantern-keeper"),
          json.dumps(OLD_JOURNAL) + "\n" + json.dumps(NEW_JOURNAL) + "\n")
    lines = xunity_service.read_journal("lantern-keeper")
    check("live translator's journal, earlier and current lines", lines == [NEW_JOURNAL, NEW_JOURNAL])
    plugin = os.path.join(game, *adapter_unity.SERVICE_STATE.split("/"))
    write(plugin, "# plugin\r\nEsito=cfg-mancante\r\nData=2026-10-18 04:29:33\r\nDettaglio=x\r\n")
    cfg = os.path.join(game, *adapter_unity.SERVICE_CONFIG.split("/"))
    write(cfg, 'Programma=C:\\Python\\pythonw.exe\r\nArgomenti=-I "smith.py" servizio-xunity --profilo "p.json" '
               '--porta 51234\r\nCartella=C:\\PatchSmith\r\n')
    check("BepInEx plugin compiled by an earlier version: state and command read",
          adapter_unity.read_service_state(game) == {"Outcome": "cfg-missing", "Date": "2026-10-18 04:29:33",
                                                     "Detail": "x"}
          and adapter_unity.read_service_command(game).get("Program") == "C:\\Python\\pythonw.exe"
          and adapter_unity.read_service_command(game).get("Folder") == "C:\\PatchSmith")
    os.makedirs(os.path.join(game, "BepInEx", "plugins", "Patchsmith"), exist_ok=True)
    open(os.path.join(game, *adapter_unity.SERVICE_PLUGIN.split("/")), "wb").write(b"dll")
    check("translator address from the earlier command (--porta)",
          adapter_unity.service_url(game) == xunity_service.address(51234))
    del data


def test_migrate(base):
    root = project.DATA_ROOT
    old_files = {
        "settings.json": OLD_SETTINGS,
        "profiles/gioco.json": OLD_PROFILE,
        "profiles/gioco.controllo.json": OLD_STATE,
        "languages/abc.json": OLD_RECORD,
        "configurations/def.json": {"leve": {"lingua": {"stato": "applicata", "valore": "it", "data": "d"}},
                                    "consensi": {"eseguibili": True}},
    }
    untouched = {
        "memory/gioco.it.json": {"nome": "Nome", "data": "Data", "Settings": "Impostazioni"},
        "memory/gioco.it.problemi.json": {"Quit": {"dove": "gioco", "motivo": "x"}},
        "memory/gioco.richieste.jsonl": json.dumps(OLD_JOURNAL) + "\n",
        "services.json": {"versione": 1, "google": {"richieste": []}},
    }
    for rel, content in list(old_files.items()) + list(untouched.items()):
        write(os.path.join(root, *rel.split("/")), content)
    before = {rel: read_bytes(os.path.join(root, *rel.split("/"))) for rel in list(old_files) + list(untouched)}
    rewritten = project.migrate_formats()
    names = sorted(os.path.relpath(p, root).replace(os.sep, "/") for p in rewritten)
    check("migration: profiles, check state, records and settings rewritten (%s)" % names,
          names == sorted(old_files))
    upgraded = all(json.loads(read_bytes(os.path.join(root, *rel.split("/"))).decode("utf-8"))
                   == legacy.upgrade(content) for rel, content in old_files.items())
    kept = all(read_bytes(os.path.join(root, *rel.split("/")) + project.FORMAT_BACKUP_SUFFIX) == before[rel]
               for rel in old_files)
    check("migration: English names, original kept next to it (%s)" % project.FORMAT_BACKUP_SUFFIX, upgraded and kept)
    check("migration: translation memories and the live translator's files not touched",
          all(read_bytes(os.path.join(root, *rel.split("/"))) == before[rel] for rel in untouched))
    again = project.migrate_formats()
    check("migration: nothing to do the second time, earlier copies intact",
          again == [] and all(read_bytes(os.path.join(root, *rel.split("/")) + project.FORMAT_BACKUP_SUFFIX)
                              == before[rel] for rel in old_files))
    check("migration: the list of games ignores the earlier copies",
          [os.path.basename(p) for p, _ in automatic.list_profiles()] == ["gioco.json"])


def main(base):
    os.makedirs(base)
    project.DATA_ROOT = os.path.join(base, "dati")  # never the real data
    automatic.PROFILES_FOLDER = None
    test_upgrade()
    test_readers(os.path.join(base, "lettori"))
    project.DATA_ROOT = os.path.join(base, "dati-migrazione")
    test_migrate(base)
    ok = sum(1 for _, r in OUTCOMES if r)
    print("\nRESULT: %d/%d checks passed" % (ok, len(OUTCOMES)))
    return ok == len(OUTCOMES)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
