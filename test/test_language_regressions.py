"""Regression tests of the official language: the problems found by the review of 0.8.0.

Usage: python -I test/test_language_regressions.py <empty_temp_folder>
Writes only in the temporary folder and in a test registry key (HKCU\\Software\\PatchsmithTest2),
which is deleted at the end. Steam and the real games are not touched.
"""
import hashlib
import os
import stat
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (auto as automatic, cli, language, official_language,
                        language_unity, language_unreal)  # noqa: E402
from patchsmith import project, system, steam  # noqa: E402
import test_gamemaker  # noqa: E402
import test_unreal  # noqa: E402
from test_official_language import appinfo  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


# --- pak with a mount point -------------------------------------------------------------------------

def pak_v9(mount, file):
    """Uncompressed pak v9 with a mount point of choice (the names are relative to it)."""
    body, entries = b"", []
    for name, data in file:
        h = hashlib.sha1(data).digest()
        entry = lambda off, d=data, h=h: struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00" + struct.pack(
            "<I", 0)
        entries.append((name, len(body), entry))
        body += entry(0) + data
    idx = test_unreal.fs(mount) + struct.pack("<i", len(entries)) + b"".join(test_unreal.fs(n) + v(o)
                                                                            for n, o, v in entries)
    tail = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(body), len(idx))
            + hashlib.sha1(idx).digest() + b"\x00" + b"\x00" * 160)
    return body + idx + tail


def pak_v11(mount, names):
    """Pak v11 (UE 4.26+/UE5) with only the folder index (FullDirectoryIndex), without data."""
    folders = {}
    for n in names:
        c, _, f = n.rpartition("/")
        folders.setdefault((c + "/") if c else "/", []).append(f)
    fdi = struct.pack("<i", len(folders))
    for c, files in folders.items():
        fdi += test_unreal.fs(c) + struct.pack("<i", len(files))
        fdi += b"".join(test_unreal.fs(f) + struct.pack("<i", k) for k, f in enumerate(files))
    off_index = len(fdi)
    idx = (test_unreal.fs(mount) + struct.pack("<iQ", len(names), 0) + struct.pack("<I", 0)
              + struct.pack("<I", 1) + struct.pack("<qq", 0, len(fdi)) + b"\x00" * 20)
    tail = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 11, off_index, len(idx))
            + hashlib.sha1(idx).digest() + b"\x00" * 160)
    return fdi + idx + tail


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    automatic.PROFILES_FOLDER = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatic.PROFILES_FOLDER)
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    os.environ["LOCALAPPDATA"] = os.path.join(base, "LocalAppData")
    system.game_open = lambda c: False

    # === languages: comparison with the variants (Chinese by script) =================================
    check("lingua: zh-TW non e' zh-CN, pt-BR e' portoghese",
              not language.same_language("zh-TW", "zh-CN") and language.same_language("zh-HK", "zh-Hant")
              and language.same_language("zh", "zh-CN") and language.same_language("pt-BR", "pt"))
    check("lingua: il migliore e' lo stesso codice, poi senza regione, mai l'altra scrittura",
              language.best(["it-IT", "it", "en"], "it") == "it"
              and language.best(["it-IT", "en"], "it") == "it-IT"
              and language.best(["zh-Hans", "en"], "zh-TW") is None
              and language.best(["zh-Hans", "zh-Hant"], "zh-TW") == "zh-Hant"
              and language.best(["zh-Hans", "zh-Hant"], "zh-CN") == "zh-Hans")

    # === Steam ==========================================================================================
    steam_root = os.path.join(base, "Steam")
    library = os.path.join(base, "Libreria")
    os.makedirs(os.path.join(steam_root, "appcache"))
    os.makedirs(os.path.join(library, "steamapps", "common"))
    steam.steam_folder = lambda: steam_root
    write(os.path.join(steam_root, "steamapps", "libraryfolders.vdf"),
           ('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
            % library.replace("\\", "\\\\")).encode())
    ita = {"italian": {"supported": "true"}, "english": {"supported": "true"}}
    appinfo_file = os.path.join(steam_root, "appcache", "appinfo.vdf")
    write(appinfo_file, appinfo({
        500: {"common": {"name": "Gioco Con Redist", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "RealGame.exe"}}},
              "extended": {"script": "_CommonRedist\\vcredist\\vc_redist.x64.exe", "altro": "MyRepack.exe"}},
        501: {"common": {"name": "Gioco Protetto", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Game\\start_protected_game.exe"}}}},
        502: {"common": {"name": "Primo Condiviso", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Shared.exe"}}}},
        503: {"common": {"name": "Secondo Condiviso", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Shared.exe"}}}},
        504: {"common": {"name": "Alpha", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Alpha.exe"}}}},
        505: {"common": {"name": "Beta", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Beta.exe"}}}},
        506: {"common": {"name": "Dungeon Short", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "Bin\\DDS.EXE"}}, "installdir": "Dungeon Short"},
              "depots": {"baselanguages": "english"}},
        507: {"common": {"name": "Gioco Sette", "type": "Game", "supported_languages": ita},
              "config": {"launch": {"0": {"executable": "SetteGame.exe"}}}},
        508: {"common": {"name": "Gioco Steam", "type": "Game", "supported_languages": ita}},
    }))
    redist = os.path.join(base, "Repack")
    write(os.path.join(redist, "_CommonRedist", "vcredist", "vc_redist.x64.exe"), b"MZ")
    write(os.path.join(redist, "MyRepack.exe"), b"MZ")
    check("Steam: installatori nella cartella e nomi fuori dai percorsi di avvio non riconoscono un gioco",
              steam.identify_from_executables(redist) is None)
    shielded = os.path.join(base, "Protetto")
    write(os.path.join(shielded, "Game", "start_protected_game.exe"), b"MZ")
    check("Steam: nome di avvio generico (start_protected_game.exe) ignorato",
              steam.identify_from_executables(shielded) is None)
    shared_dir = os.path.join(base, "Condiviso")
    write(os.path.join(shared_dir, "Shared.exe"), b"MZ")
    check("Steam: eseguibile avviato da due giochi -> nessuno", steam.identify_from_executables(shared_dir) is None)
    tied = os.path.join(base, "Pari")
    write(os.path.join(tied, "Alpha.exe"), b"MZ")
    write(os.path.join(tied, "Beta.exe"), b"MZ")
    check("Steam: pari merito -> nessun vincitore", steam.identify_from_executables(tied) is None)
    short = os.path.join(base, "Dungeon Short")
    write(os.path.join(short, "bin", "dds.exe"), b"MZ")
    r = steam.identify_from_executables(short)
    check("Steam: nome corto con percorso esatto, maiuscole diverse -> riconosciuto, cartella simile",
              r and r["appid"] == "506" and r["cartella_simile"])
    r = official_language.detect(short, "it")
    lines = official_language.message(r, "it")
    check("cartella riconosciuta e confermata: lingua presente; menu Lingua di Steam solo se c'e'",
              r["presente"] is True and r["selezionabile_steam"] is False
              and not any("Proprieta'" in x for x in lines) and any("non e' un'installazione" in x for x in lines))
    lines = official_language.message(r, "it", ["Ho impostato l'italiano."], True)
    check("messaggio: dopo un'impostazione riuscita niente istruzioni contraddittorie",
              not any("non e' un'installazione" in x for x in lines))
    rng = os.path.join(base, "CartellaACaso")
    write(os.path.join(rng, "SetteGame.exe"), b"MZ")
    r = official_language.detect(rng, "it")
    check("Steam: riconoscimento non confermato dal nome della cartella -> solo una nota, nessun blocco",
              r["presente"] is None and r["note"] and r["steam_conosciuto"]["appid"] == "507")
    steam_game = os.path.join(library, "steamapps", "common", "Gioco Steam")
    os.makedirs(steam_game)
    write(os.path.join(library, "steamapps", "appmanifest_508.acf"),
           b'"AppState"\n{\n\t"appid"\t\t"508"\n\t"name"\t\t"Gioco Steam"\n\t"installdir"\t\t"Gioco Steam"\n}\n')
    link = os.path.join(base, "Collegamento")
    done = subprocess.run(["cmd", "/c", "mklink", "/J", link, steam_game], capture_output=True).returncode == 0
    if done:
        info = steam.game_installed(link)
        check("Steam: gioco raggiunto da una junction riconosciuto come installato", info and info["appid"] == "508")
        os.rmdir(link)
    else:
        print("SALTATO  junction non creabile qui")
    reads = []
    old_folder = steam._build_index
    steam._build_index = lambda d: (reads.append(1), old_folder(d))[1]
    steam._CACHE.clear()
    steam.app_info(506)
    steam.app_info(507)
    official_language.detect(short, "it")
    steam._build_index = old_folder
    check("Steam: appinfo.vdf letto una volta sola", len(reads) == 1)
    intact = read_file(appinfo_file)
    for name, data in (("troncato", intact[:100]), ("vuoto", b""),
                       ("tabella fuori dal file", intact[:8] + struct.pack("<q", 10 ** 9) + intact[16:])):
        write(appinfo_file, data)
        steam._CACHE.clear()
        try:
            steam.app_info(506)
            error = None
        except steam.SteamError as e:
            error = e
        except Exception as e:  # noqa: BLE001 - this is exactly what must not happen
            error = "SBAGLIATO: %r" % e
        warnings = []
        try:
            r = official_language.detect(short, "it", log=warnings.append)
            ok = r["presente"] is None
        except Exception:
            ok = False
        check("Steam: appinfo.vdf %s -> ErroreSteam, e il rilevamento continua" % name,
                  isinstance(error, steam.SteamError) and ok)
    write(appinfo_file, intact)
    steam._CACHE.clear()

    # === Unreal =========================================================================================
    ue = os.path.join(base, "GiocoUE")
    paks = os.path.join(ue, "Proj", "Content", "Paks")
    entries = [("UI", "Start", "Start")]
    write(os.path.join(paks, "Proj-WindowsNoEditor.pak"), pak_v9("../../../Proj/Content/Localization/Game/", [
        ("Game.locmeta", test_unreal.locmeta("en")), ("en/Game.locres", test_unreal.locres_v2(entries)),
        ("it/Game.locres", test_unreal.locres_v2([("UI", "Start", "Inizia")]))]))
    write(os.path.join(paks, "Online-Plugin.pak"), pak_v9("../../../Engine/Plugins/Online/X/", [
        ("Content/Localization/X/fr/X.locres", test_unreal.locres_v2(entries))]))
    os.makedirs(os.path.join(ue, "Engine", "Binaries", "ThirdParty"))
    write(os.path.join(ue, "Engine", "Binaries", "ThirdParty", "x.dll"), b"x")
    languages, prog = language_unreal.game_languages(ue)
    check("Unreal: pak v9 montato in una sottocartella letto; plugin del motore esclusi",
              languages == {"en", "it"} and prog == "Proj")
    ue11 = os.path.join(base, "GiocoUE5")
    write(os.path.join(ue11, "Proj5", "Content", "Paks", "Proj5-Windows.pak"), pak_v11(
        "../../../Proj5/Content/Localization/Game/", ["en/Game.locres", "zh-Hant/Game.locres", "Game.locmeta"]))
    languages, prog = language_unreal.game_languages(ue11)
    check("Unreal: pak v11 (indice delle cartelle) con punto di montaggio",
              languages == {"en", "zh-Hant"} and prog == "Proj5")
    check("Unreal: cinese per scrittura (zh-TW -> zh-Hant; mai zh-Hans al posto di zh-Hant)",
              language_unreal.target_tag({"zh-Hant", "en"}, "zh-TW") == "zh-Hant"
              and language_unreal.target_tag({"zh-Hans", "en"}, "zh-TW") is None
              and official_language.languages_in_files(ue11, "Unreal", "zh-CN")[1]["trovata"] is False)
    game_gus = os.path.join(ue, "Proj", "Saved", "Config", "Windows", "GameUserSettings.ini")
    write(game_gus, b"[Internationalization]\r\nLanguage=en\r\n")
    check("Unreal: impostazioni cercate anche nella cartella del gioco",
              language_unreal.settings_file("Proj", ue) == game_gus)
    os.remove(game_gus)
    test = os.path.join(base, "ini", "GameUserSettings.ini")
    write(test, b"[/Script/Engine.InputSettings]\r\nKeyboardCulture=it-IT\r\n[/Script/Game.Settings]\r\n"
                  b"LanguageFilter=1\r\n")
    check("Unreal: KeyboardCulture e filtri non sono la lingua del gioco",
              language_unreal.read_internationalization(test)[1] == [])
    write(test, b"[/Game/UI/BP_Settings.BP_Settings_C]\r\nCurrentLocalization=en\r\n")
    check("Unreal: impostazione della lingua nelle sezioni /Game/ riconosciuta",
              language_unreal.read_internationalization(test)[1])

    def round_trip(name, original, expected=None):
        write(test, original)
        changes = language_unreal.set_culture(test, "it")
        after = read_file(test)
        restored = language_unreal.restore_culture(test, changes, "it") and read_file(test) == original
        check("Unreal: %s -> impostato e ripristinato identico" % name,
                  restored and (expected is None or expected in after))
    round_trip("a capo misti (CRLF e LF)", b"[A]\r\nx=1\n[Internationalization]\nLanguage=en\r\n[B]\r\ny=2",
                     b"[Internationalization]\nLanguage=it\r\n")
    round_trip("chiavi doppie e con maiuscole diverse",
                     b"[Internationalization]\r\nLanguage=en\r\nlanguage=it\r\nLanguage=fr\r\n", b"Language=it\r\nlanguage=it\r\nLanguage=it\r\n")
    round_trip("sezione aggiunta a un file senza a capo finale", b"[A]\r\nx=1", b"Culture=it")
    round_trip("sezione aggiunta a un file vuoto", b"", b"Culture=it")
    round_trip("riga aggiunta in fondo a una sezione senza a capo finale", b"[Internationalization]\r\nX=1",
                     b"Culture=it")
    write(test, b"[Internationalization]\r\nLanguage=en\r\nLanguage=en\r\n")
    changes = language_unreal.set_culture(test, "it")
    write(test, b"[Internationalization]\r\nLanguage=it\r\nLanguage=de\r\n")  # the player changes the second one
    language_unreal.restore_culture(test, changes, "it")
    check("Unreal: ripristino riga per riga, solo dove c'e' ancora il valore scritto",
              read_file(test) == b"[Internationalization]\r\nLanguage=en\r\nLanguage=de\r\n")
    gus = os.path.join(os.environ["LOCALAPPDATA"], "Proj", "Saved", "Config", "WindowsNoEditor", "GameUserSettings.ini")
    write(gus, b"\xff\xfe" + "[Internationalization]\r\nLanguage=en\r\n".encode("utf-16-le") + b"\x00")
    detection = {"motore": {"tipo": "unreal", "progetto": "Proj", "tag": "it", "trovata": True}}
    try:
        done, lines = official_language.set_(ue, detection, "it", silent)
        ok = not done and any("menu" in x for x in lines)
    except Exception:
        ok = False
    check("Unreal: impostazioni UTF-16 rovinate -> istruzioni, nessun errore", ok)
    original_gus = b"[Internationalization]\r\nLanguage=en\r\n"
    write(gus, original_gus)
    done, _ = official_language.set_(ue, detection, "it", silent)
    system.game_open = lambda c: True
    outcome = official_language.restore(ue, silent)
    check("Unreal: ripristino con il gioco aperto -> non scrivo e il registro resta",
              done and outcome is False and official_language.is_set(ue) and b"Language=it" in read_file(gus))
    system.game_open = lambda c: False
    os.chmod(gus, stat.S_IREAD)
    write(os.path.join(ue, "Proj", "Content", "Paks", "zz_Patchsmith_it_999_P.pak"), b"finto")
    messages = []
    try:
        automatic.restore(ue, messages.append)
        ok = official_language.is_set(ue) and any("lingua del gioco" in m for m in messages)
    except Exception as e:
        ok = False
        messages.append(repr(e))
    os.chmod(gus, stat.S_IREAD | stat.S_IWRITE)
    os.remove(os.path.join(ue, "Proj", "Content", "Paks", "zz_Patchsmith_it_999_P.pak"))
    check("Ripristina originali: impostazioni di sola lettura -> continua, avvisa e tiene il registro", ok)
    check("Ripristina da riga di comando con la cartella del gioco", cli.main(["ripristina", ue]) == 0
              and not official_language.is_set(ue) and read_file(gus) == original_gus)
    os.remove(gus)
    done, lines = official_language.set_(ue, detection, "it", silent)
    done2, lines2 = official_language.set_(ue, detection, "it", silent)
    check("Unreal: impostazioni non trovate -> 'avvialo una volta' solo la prima volta, poi il menu",
              not done and any("avvialo una volta" in x for x in lines)
              and not done2 and not any("avvialo una volta" in x for x in lines2) and any("menu" in x for x in lines2))
    write(gus, original_gus)

    # translate anyway: no detection, and the language set before goes back to how it was
    path, outcome = automatic.prepare(ue, "auto", "it", "finto", silent)
    set_before = official_language.is_set(ue) and b"Language=it" in read_file(gus)
    real_detect = official_language.detect

    def do_not_call(*_a, **_k):
        raise AssertionError("rileva non deve essere chiamato")
    official_language.detect = do_not_call
    try:
        path, outcome = automatic.prepare(ue, "auto", "it", "finto", silent, translate_anyway=True)
        ok = path is not None and not official_language.is_set(ue) and read_file(gus) == original_gus
    except AssertionError:
        ok = False
    official_language.detect = real_detect
    check("Traduci comunque: niente rilevamento, lingua rimessa com'era, traduzione preparata",
              set_before and ok)
    check("Riga di comando: 'auto' con la lingua ufficiale gestita esce con 0",
              cli.main(["auto", ue, "--a", "it", "--backend", "finto"]) == 0)
    write(os.path.join(ue, "patchsmith-unreal.json"), b"{}")
    path, outcome = automatic.prepare(ue, "auto", "it", "finto", silent)
    check("Lingua ufficiale con una traduzione di PatchSmith ancora installata: lo dice",
              outcome.get("traduzione_installata")
              and any("ancora installata" in x for x in outcome["lingua_ufficiale_righe"]))
    os.remove(os.path.join(ue, "patchsmith-unreal.json"))
    automatic.restore(ue, silent)

    # === Unity ==========================================================================================
    i2_entries = [("English", "en"), ("Chinese (Simplified)", "zh-CN"), ("Chinese (Traditional)", "zh-TW"),
               ("Italiano", "it-IT")]
    check("I2: voce scelta per codice e scrittura (zh-TW -> Traditional; it -> it-IT)",
              language_unity.i2_entry(i2_entries, "zh-TW") == "Chinese (Traditional)"
              and language_unity.i2_entry(i2_entries, "zh-CN") == "Chinese (Simplified)"
              and language_unity.i2_entry(i2_entries, "it") == "Italiano"
              and language_unity.i2_entry([("English", "en"), ("Chinese", "zh-CN")], "zh-TW") is None)
    un = os.path.join(base, "GiocoUnity")
    data = os.path.join(un, "Gioco_Data")
    write(os.path.join(data, "globalgamemanagers"), b"x")
    write(os.path.join(data, "app.info"), b"PatchsmithTest2\nGioco")
    write(os.path.join(data, "Managed", "I2Localization.dll"), b"MZ I2.Loc\x00")
    aa = os.path.join(data, "StreamingAssets", "aa")
    write(os.path.join(aa, "catalog.bin"), b"\x00\x01UnityEngine.Localization.Locale\x00\x12Italian (it).asset\x00"
                                            b"\x07Locale-itI\x00\x10French (fr).asset\x00")
    check("Unity Localization: catalogo binario (solo i codici con etichetta Locale-)",
              language_unity.unity_localization_languages(data) == {"it"})

    def s(t):
        b = t.encode()
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    def list_(*entries):
        return struct.pack("<i", len(entries)) + b"".join(s(n) + s(c) + b"\x00\x00\x00\x00" for n, c in entries) + \
            struct.pack("<ii", 0, 0)
    write(os.path.join(data, "resources.assets"), b"\x00" * 64 + list_(("English", "en"), ("Italiano", "it")) +
           b"\x00" * 64)
    for k in range(4):
        write(os.path.join(data, "level%d" % k), b"\x00" * 64 + list_(("English", "en"), ("Lingua%d" % k, "x%d" % k))
               + b"\x00" * 64)
    check("I2: dopo la prima lista si leggono solo pochi altri file",
              [n for n, _ in language_unity.i2_languages(data)] == ["English", "Italiano", "Lingua0", "Lingua1"])
    real_i2 = language_unity.i2_languages

    def broken(_data):
        raise struct.error("asset rovinato")
    language_unity.i2_languages = broken
    present, dm = official_language.languages_in_files(un, "Unity", "it")
    language_unity.i2_languages = real_i2
    check("Unity: un errore nella lettura di I2 non fa perdere Unity Localization",
              present == {"it"} and dm["trovata"])
    import winreg
    key = "Software\\PatchsmithTest2\\Gioco"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, language_unity.pref_name("I2 Language"), 0, winreg.REG_SZ, "English")
    r = official_language.detect(un, "it", "Unity")
    done, lines = official_language.set_(un, r, "it", silent)
    check("Unity: impostazione descritta come tentativo ('dovrebbe')", done and any("dovrebbe" in x for x in lines))
    official_language.restore(un, silent)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
        value_, kind = winreg.QueryValueEx(k, language_unity.pref_name("I2 Language"))
    check("Unity: ripristino di un valore di tipo testo (REG_SZ)", kind == winreg.REG_SZ and value_ == "English")
    write(os.path.join(data, "Managed", "I2Localization.dll"),
           b"MZ I2.Loc\x00" + "LastSelectedLanguage".encode("utf-16-le"))
    r = official_language.detect(un, "it", "Unity")
    done, lines = official_language.set_(un, r, "it", silent)
    check("Unity: I2 modificato dal gioco (LastSelectedLanguage) -> solo istruzioni",
              not done and any("menu" in x for x in lines) and not official_language.is_set(un))
    for k in (key, "Software\\PatchsmithTest2"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
        except OSError:
            pass

    # === GameMaker: translate anyway even if the game has the files in Italian ============================
    gm = os.path.join(base, "Gioco GM")
    write(os.path.join(gm, "Localization", "english_ui.txt"), test_gamemaker.UI.encode())
    write(os.path.join(gm, "Localization", "italian", "ui.txt"), b"0 Combatti\r\n1 Cambia\r\n")
    latin = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    write(os.path.join(gm, "data.win"), test_gamemaker.data_win([("fn_testo", latin)]))
    write(os.path.join(gm, "Gioco.exe"), b"MZ")
    path, outcome = automatic.prepare(gm, "auto", "it", "finto", silent)
    path2, _ = automatic.prepare(gm, "auto", "it", "finto", silent, translate_anyway=True)
    check("GameMaker: italiano nei file -> lingua ufficiale; con 'traduci comunque' si traduce",
              path is None and outcome.get("lingua_ufficiale") and path2 is not None)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
