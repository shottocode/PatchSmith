"""Test of the official language: fake Steam, fake Unreal game and fake Unity (I2) game.

Usage: python -I test/test_official_language.py <empty_temp_folder>
Writes only to the temporary folder and to a test registry key (HKCU\\Software\\PatchsmithTest),
which is deleted at the end. Steam and the real games are not touched.
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (auto as automatic, official_language, language_unity,
                        language_unreal, project, steam)  # noqa: E402
import test_unreal  # noqa: E402  (builders of pak and .locres)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


# --- fake appinfo.vdf (v41: keys in the string table) ------------------------------------------------

def appinfo(app):
    """app: {appid: tree of dictionaries}. Returns the bytes of an appinfo.vdf version 0x07564429."""
    keys, idx = [], {}

    def k(name):
        if name not in idx:
            idx[name] = len(keys)
            keys.append(name)
        return struct.pack("<i", idx[name])

    def kv(tree):
        out = bytearray()
        for name, v in tree.items():
            if isinstance(v, dict):
                out += b"\x00" + k(name) + kv(v) + b"\x08"
            elif isinstance(v, int):
                out += b"\x02" + k(name) + struct.pack("<i", v)
            else:
                out += b"\x01" + k(name) + str(v).encode() + b"\x00"
        return bytes(out)

    record = bytearray()
    for appid, tree in app.items():
        body = kv({"appinfo": tree}) + b"\x08"
        head = struct.pack("<IIQ", 2, 0, 0) + b"\x00" * 20 + struct.pack("<I", 1) + hashlib.sha1(body).digest()
        record += struct.pack("<II", appid, len(head) + len(body)) + head + body
    record += struct.pack("<I", 0)
    table = struct.pack("<I", len(keys)) + b"".join(c.encode() + b"\x00" for c in keys)
    return struct.pack("<IIq", 0x07564429, 1, 16 + len(record)) + bytes(record) + table


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    steam_root = os.path.join(base, "Steam")
    library = os.path.join(base, "Libreria")
    os.makedirs(os.path.join(steam_root, "appcache"))
    os.makedirs(os.path.join(steam_root, "steamapps"))
    os.makedirs(os.path.join(library, "steamapps", "common"))
    steam.steam_folder = lambda: steam_root
    open(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"), "w").write(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t// commento\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
        % (steam_root.replace("\\", "\\\\"), library.replace("\\", "\\\\")))

    # --- text VDF
    v = steam.read_vdf('"A"\n{\n\t"x"\t"1 \\"q\\""\n\t// commento\n\t"B" { "y" "2" }\n}\n')
    check("VDF di testo: annidamento, virgolette con escape, commenti", v == {"A": {"x": '1 "q"', "B": {"y": "2"}}})

    # --- appinfo: three games
    open(os.path.join(steam_root, "appcache", "appinfo.vdf"), "wb").write(appinfo({
        100: {"common": {"name": "Gioco Steam", "type": "Game",
                         "supported_languages": {"english": {"supported": "true"}, "italian": {"supported": "true",
                                                                                               "subtitles": "true"}}},
              "depots": {"baselanguages": "english,italian"}},
        200: {"common": {"name": "Gioco Senza Italiano", "type": "Game",
                         "supported_languages": {"english": {"supported": 1}}}},
        300: {"common": {"name": "Gioco Fuori Steam", "type": "Game",
                         "supported_languages": {"italian": {"supported": "true"}}},
              "config": {"launch": {"0": {"executable": "GiocoFuoriSteam.exe"}}}},
        301: {"common": {"name": "Gioco Fuori Steam Demo", "type": "Game"},
              "config": {"launch": {"0": {"executable": "GiocoFuoriSteam.exe"}}}},
    }))
    check("appinfo v41: lingue ufficiali", steam.official_languages(100) == {
        "english": {"supported": True}, "italian": {"supported": True, "subtitles": True}}
        and steam.official_languages(200) == {"english": {"supported": True}})
    check("appinfo: lingue selezionabili da Steam (baselanguages)",
              steam.selectable_languages(100) == {"english", "italian"} and steam.selectable_languages(200) == set())

    # --- game installed by Steam (appmanifest)
    steam_game = os.path.join(library, "steamapps", "common", "Gioco Steam")
    os.makedirs(os.path.join(steam_game, "bin"))
    open(os.path.join(library, "steamapps", "appmanifest_100.acf"), "w").write(
        '"AppState"\n{\n\t"appid"\t\t"100"\n\t"name"\t\t"Gioco Steam"\n\t"installdir"\t\t"Gioco Steam"\n'
        '\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n')
    info = steam.game_installed(os.path.join(steam_game, "bin"))
    check("gioco installato riconosciuto (anche da una sottocartella)", info and info["appid"] == "100"
              and info["lingua"] == "english")
    r = official_language.detect(steam_game, "it")
    lines = official_language.message(r, "it")
    check("Steam: italiano ufficiale e selezionabile -> istruzioni per il menu Lingua di Steam",
              r["presente"] and r["selezionabile_steam"] and any("General > Language" in x for x in lines))

    # --- folder outside Steam, recognized by the executable (the demo does not count)
    outside = os.path.join(base, "Fuori")
    os.makedirs(outside)
    open(os.path.join(outside, "GiocoFuoriSteam.exe"), "wb").write(b"MZ")
    known = steam.identify_from_executables(outside)
    check("gioco fuori da Steam riconosciuto dall'eseguibile, non la demo", known and known["appid"] == "300")
    r = official_language.detect(outside, "it")
    check("fuori da Steam: italiano ufficiale segnalato, senza istruzioni Steam per la cartella",
              r["presente"] and r["steam"] is None and
              any("isn't a Steam installation" in x for x in official_language.message(r, "it")))

    # --- Unreal: game with Italian, setting and restore in GameUserSettings.ini
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    automatic.PROFILES_FOLDER = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatic.PROFILES_FOLDER)
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    os.environ["LOCALAPPDATA"] = os.path.join(base, "LocalAppData")
    ue = os.path.join(base, "GiocoUE")
    paks = os.path.join(ue, "Proj", "Content", "Paks")
    os.makedirs(paks)
    os.makedirs(os.path.join(ue, "Engine", "Binaries", "ThirdParty"))
    open(os.path.join(ue, "Engine", "Binaries", "ThirdParty", "x.dll"), "wb").write(b"x")
    entries = [("UI", "Start", "Start")]
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(test_unreal.pak_v9([
        ("Proj/Content/Localization/Game/Game.locmeta", test_unreal.locmeta("en")),
        ("Proj/Content/Localization/Game/en/Game.locres", test_unreal.locres_v2(entries)),
        ("Proj/Content/Localization/Game/it-IT/Game.locres", test_unreal.locres_v2([("UI", "Start", "Inizia")])),
        ("Proj/Plugins/X/Content/Localization/X/fr/X.locres", test_unreal.locres_v2(entries)),
    ]))
    languages, prog = language_unreal.game_languages(ue)
    check("Unreal: lingue del gioco (i plugin non contano), progetto", languages == {"en", "it-IT"} and prog == "Proj")
    check("Unreal: si usa il nome esatto della cartella (it-IT)", language_unreal.target_tag(languages) == "it-IT")
    gus_dir = os.path.join(os.environ["LOCALAPPDATA"], "Proj", "Saved", "Config", "WindowsNoEditor")
    os.makedirs(gus_dir)
    gus = os.path.join(gus_dir, "GameUserSettings.ini")
    original_gus = b"[/Script/Engine.GameUserSettings]\r\nVersion=5\r\n\r\n[Internationalization]\r\nLanguage=en\r\n"
    open(gus, "wb").write(original_gus)
    import patchsmith.system as system
    system.game_open = lambda c: False
    path, outcome = automatic.prepare(ue, "auto", "it", "finto", silent)
    check("Unreal: niente traduzione automatica, lingua impostata", path is None and outcome.get("lingua_ufficiale")
              and b"Language=it-IT" in open(gus, "rb").read() and official_language.is_set(ue))
    check("Unreal: messaggio con quello che e' stato fatto",
              any("I have set" in x for x in outcome.get("lingua_ufficiale_righe", [])))
    automatic.restore(ue, silent)
    check("Unreal: ripristino identico e registro tolto", open(gus, "rb").read() == original_gus
              and not official_language.is_set(ue))
    open(gus, "wb").write(b"[/Script/Game.Settings]\r\nLanguageSetting=English\r\n")
    done, lines = official_language.set_(ue, official_language.detect(ue, "it", "Unreal"), "it", silent)
    check("Unreal: il gioco ha una sua impostazione della lingua -> solo istruzioni",
              not done and b"Culture" not in open(gus, "rb").read() and any("menu" in x for x in lines))
    system.game_open = lambda c: True
    open(gus, "wb").write(original_gus)
    done, lines = official_language.set_(ue, official_language.detect(ue, "it", "Unreal"), "it", silent)
    check("Unreal: gioco aperto -> non scrivo", not done and open(gus, "rb").read() == original_gus)
    system.game_open = lambda c: False

    # --- Unity with I2: list of languages in the assets, preference in the registry (test key)
    check("PlayerPrefs: nome del valore come Unity",
              language_unity.pref_name("I2 Language") == "I2 Language_h3293684300")
    un = os.path.join(base, "GiocoUnity")
    data = os.path.join(un, "Gioco_Data")
    os.makedirs(os.path.join(data, "Managed"))
    open(os.path.join(data, "globalgamemanagers"), "wb").write(b"x")
    open(os.path.join(data, "app.info"), "w").write("PatchsmithTest\nGioco")
    open(os.path.join(data, "Managed", "I2Localization.dll"), "wb").write(b"MZ I2.Loc\x00")

    def s(t):
        b = t.encode()
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    list_ = struct.pack("<i", 3) + s("English") + s("en") + b"\x00\x00\x00\x00" + s("Italiano") + s("it") + \
        b"\x00\x00\x00\x00" + s("Deutsch") + s("de") + b"\x01\x00\x00\x00" + struct.pack("<ii", 0, 0)
    open(os.path.join(data, "resources.assets"), "wb").write(b"\x00" * 64 + list_ + b"\x00" * 64)
    check("I2: lingue attive dagli asset (le disattivate no)",
              language_unity.i2_languages(data) == [("English", "en"), ("Italiano", "it")])
    import winreg
    key = "Software\\PatchsmithTest\\Gioco"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
    except OSError:
        pass
    r = official_language.detect(un, "it", "Unity")
    done, lines = official_language.set_(un, r, "it", silent)
    check("Unity: gioco mai avviato (nessuna chiave) -> non scrivo, istruzioni", not done
              and language_unity.read_pref("PatchsmithTest", "Gioco", "I2 Language") is False)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, language_unity.pref_name("I2 Language"), 0, winreg.REG_BINARY, b"English\x00")
        winreg.SetValueEx(k, "Altro_h1", 0, winreg.REG_DWORD, 7)
    done, lines = official_language.set_(un, r, "it", silent)
    check("Unity: preferenza I2 impostata al nome esatto ('Italiano')", done and
              language_unity.read_pref("PatchsmithTest", "Gioco", "I2 Language")[1] == b"Italiano\x00")
    official_language.restore(un, silent)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        other = winreg.QueryValueEx(k, "Altro_h1")[0]
    check("Unity: ripristino del solo valore toccato",
              language_unity.read_pref("PatchsmithTest", "Gioco", "I2 Language")[1] == b"English\x00" and other == 7)
    for k in (key, "Software\\PatchsmithTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
        except OSError:
            pass

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
