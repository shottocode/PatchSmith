"""Tests of the fonts' characters and of the labels' space, for all engines (point 9).

Usage: python -I test/test_font_length.py <empty_temp_folder>
Fake games, fonts and logs (test/fake_unity.py, test/data/signals): no real game, no network, no real translator
(fake in-memory translators). Covers:
- Unity fonts (font_unity): the cmap table of .ttf files; TextMeshPro fonts with the field tree (bundles) and without
  it (_Data files, recognized by their shape); dynamic fonts with their .ttf; the rule "at least one font can draw
  it"; result kept in memory/<game>.unity/font.json until the game files change;
- Unity Localization: a table written with è -> e' if no font has è (the translation memory keeps è; the French table
  is not touched); a character that cannot be adapted leaves the original and goes into problemi.json; with a font
  that has è nothing changes;
- XUnity: BepInEx.cfg with WriteUnityLog at installation (and the check puts it back); the missing characters read
  from the logs (real lines); the after-play check adapts the cache lines written by PatchSmith (not those changed by
  hand), notes the characters in the game profile, and from then on the cache and the live translator avoid them; in
  read-only mode it writes nothing;
- Unity Localization with the Player.log (IL2CPP game): characters noted and table rewritten with "Reapply";
- Unreal: characters of the game's other Latin languages (full Latin-1 if they use them), no limit without clues;
- labels too long: shorter requests to the local translator (context 'width', also in the real prompt), the
  shortest valid one kept, the others in problemi.json with the original text and in the "Too long" list of
  "Fix translations"; only once; not the fixes made by hand; with the other translators only noted; the space of the
  official translations; outcome and messages.
"""
import hashlib
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from patchsmith import (adapter_unity, adapter_unity_loc, adapter_unreal, auto as automatic, backend,
                        characters as chars, components, checks,
                        corrections, outcome, font_unity, project, scan,
                        xunity_service)
from patchsmith.project import Project  # noqa: E402
import fake_unity as fake  # noqa: E402

OUTCOMES = []
SIGNALS = os.path.join(HERE, "data", "signals")
ASCII = "".join(chr(c) for c in range(32, 127))


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def write(path, text, date=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    if date is not None:
        os.utime(path, (date, date))


def snapshot(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            st = os.stat(p)
            output[os.path.relpath(p, folder)] = (st.st_size, st.st_mtime_ns)
    return output


EN = ["New Game", "Continue", "Options", "The door is locked. Find the key first.", "Quit to desktop"]
FR = ["Nouvelle partie", "Continuer", "Options", "La porte est fermée.", "Quitter"]
TABLE_EN = "localization-string-tables-english(en)_assets_all.bundle"
TABLE_FR = "localization-string-tables-french(fr)_assets_all.bundle"


# --- Unity fonts -------------------------------------------------------------------------------------------------

def test_font(base):
    check("font .ttf: characters of the cmap table (format 4)",
              font_unity.ttf_chars(fake.ttf("abcè€")) == set("abcè€")
              and font_unity.ttf_chars(b"non e' un font") is None)
    data = fake.tmp_font_data("Pixel SDF", ASCII + "é", 0)
    check("TextMeshPro font without the field tree: character table recognized by its shape",
              {chr(c) for c in font_unity.tmp_table(b"\x07" * 37 + data)} == set(ASCII + "é")
              and font_unity._object_name(data) == "Pixel SDF" and font_unity.tmp_table(b"\x01\x00" * 99) is None)
    # game with a font in the bundle (with the tree) and one in resources.assets (without), a dynamic one with its .ttf
    game = os.path.join(base, "font")
    fake.il2cpp_game(game, {"ui_assets_all.bundle": fake.bundle([("CAB-f", fake.font_file(
        [("Titles SDF", ASCII + "ü", 0)]))], compression="lz4", block=4096)}, extra={
        "Game_Data/resources.assets": fake.font_file([("Texts SDF", ASCII + "à", 0)], trees=False)})
    game_data = os.path.join(game, "Game_Data")
    found = font_unity.game_fonts(game_data)
    names = sorted((f["name"], f["mode"]) for f in found["tmp"])
    check("game fonts: the one in the bundle (with the mode) and the one in resources.assets (without tree)",
              names == [("Texts SDF", None), ("Titles SDF", 0)] and found["complete"])
    allowed, reason = font_unity.allowed_chars(game_data)
    check("rule: a character is fine if at least one font has it (à from one, ü from the other), è is not",
              allowed is not None and {"à", "ü"} <= allowed and "è" not in allowed
              and "2 TextMeshPro fonts" in reason)
    # dynamic font: its .ttf counts
    dynamic = os.path.join(base, "dynamic")
    fake.il2cpp_game(dynamic, {"fonts_assets_all.bundle": fake.bundle([("CAB-d", fake.font_file(
        [("Body SDF", "", 1), ("Pixel SDF", ASCII, 0)], [("Body", ASCII + "àèéìòù")]))])})
    allowed, reason = font_unity.allowed_chars(os.path.join(dynamic, "Game_Data"))
    check("dynamic font: the characters of its .ttf count too (è can be drawn)",
              allowed is not None and "è" in allowed and "dynamic" in reason)
    without_ttf = os.path.join(base, "without-ttf")
    fake.il2cpp_game(without_ttf, {"fonts_assets_all.bundle": fake.bundle([("CAB-d", fake.font_file(
        [("Body SDF", "", 1), ("Pixel SDF", ASCII, 0)]))])})
    empty_game = os.path.join(base, "without-font")
    fake.il2cpp_game(empty_game, {TABLE_EN: fake.table_bundles([("UI_en", "en", EN)])})
    check("unknown (no limit): dynamic fonts without .ttf, no TextMeshPro font",
              font_unity.allowed_chars(os.path.join(without_ttf, "Game_Data"))[0] is None
              and font_unity.allowed_chars(os.path.join(empty_game, "Game_Data"))[0] is None)
    # the result stays in the cache file until the game files change
    cache = os.path.join(base, "cache", "font.json")
    first = font_unity.allowed_chars(game_data, cache=cache)
    real = font_unity.game_fonts
    font_unity.game_fonts = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("read again"))
    try:
        second = font_unity.allowed_chars(game_data, cache=cache)
        reread = True
    except AssertionError:
        second, reread = None, False
    finally:
        font_unity.game_fonts = real
    bundle = os.path.join(game_data, "StreamingAssets", "aa", "StandaloneWindows64", "ui_assets_all.bundle")
    with open(bundle, "wb") as f:  # a game update: the font now also has è
        f.write(fake.bundle([("CAB-f", fake.font_file([("Titles SDF", ASCII + "üè", 0)]))]))
    third = font_unity.allowed_chars(game_data, cache=cache)
    check("cache: the second time the fonts are not read again; after a game update they are, and now è is fine",
              reread and second == first and "è" not in first[0] and third[0] is not None and "è" in third[0]
              and read_json(cache)["ranges"])


def _test_memory(profile):
    return project.memory_path(read_json(profile), test=True)


def test_unity_localization(base):
    font = fake.bundle([("CAB-f", fake.font_file([("Texts SDF", ASCII + "é", 0)]))])
    game = os.path.join(base, "loc")
    folder = fake.il2cpp_game(game, {TABLE_EN: fake.table_bundles([("UI_en", "en", EN)]),
                                          TABLE_FR: fake.table_bundles([("UI_fr", "fr", FR)]),
                                          "fonts_assets_all.bundle": font})
    fr_before = fingerprint(os.path.join(folder, TABLE_FR))
    profile, _full, e = automatic.analyze_and_translate(game, "en", "it", backend="fake", log=silent)
    name = read_json(profile)["name"]
    cache = project.data_folder("memory", "%s.unity" % name, "font.json")
    check("Unity Localization: translation without accents written as it is; fonts read and kept in font.json",
              e["outcome"].code == "OK" and os.path.exists(cache) and [t for _, t in adapter_unity_loc.TablesFile(
                  os.path.join(folder, TABLE_EN)).texts()] == ["[it] " + t for t in EN])
    memory = _test_memory(profile)
    data = read_json(memory)
    data.update({EN[3]: "La porta è chiusa. Trova prima la chiave, perché serve.", EN[4]: "Esci → desktop"})
    write_json(memory, data)
    lines = []
    d = {}
    automatic.reapply(game, lines.append, test=True, outcome=d)
    texts = [t for _, t in adapter_unity_loc.TablesFile(os.path.join(folder, TABLE_EN)).texts()]
    check("font without è: in the table è becomes e' (é stays: the font has it); in memory è stays; the French "
              "table is not touched", texts[3] == "La porta e' chiusa. Trova prima la chiave, perché serve."
              and read_json(memory)[EN[3]].startswith("La porta è") and fingerprint(os.path.join(folder, TABLE_FR))
              == fr_before and any("don't have" in r and "è" in r and "é" not in r for r in lines))
    problems = read_json(project.problems_path(read_json(profile), test=True))
    check("a character that cannot be adapted (→): the original stays, in problemi.json with the character "
              "to avoid; the outcome says so (text adapted and translation discarded)",
              texts[4] == EN[4] and problems.get(EN[4], {}).get("where") == "game"
              and "→" in problems[EN[4]].get("do_not_use", "") and d["outcome"].extra.get("font_adapted") == 1
              and '"è" becomes "e"' in "\n".join(outcome._attempts_text(d["outcome"])))
    # characters the game said it cannot draw (Unity's log): they count even if the font has them
    p = read_json(profile)
    p["missing_chars"] = "é"
    scan.save_json(profile, p)
    automatic.reapply(game, silent, test=True, outcome={})
    texts = [t for _, t in adapter_unity_loc.TablesFile(os.path.join(folder, TABLE_EN)).texts()]
    check("missing characters in the profile: adapted even if a font has them (perché -> perche')",
              texts[3] == "La porta e' chiusa. Trova prima la chiave, perche' serve.")
    # with a font that has all the letters: no change
    good = os.path.join(base, "loc-good")
    folder_b = fake.il2cpp_game(good, {TABLE_EN: fake.table_bundles([("UI_en", "en", EN)]),
                                            "fonts_assets_all.bundle": fake.bundle([("CAB-f", fake.font_file(
                                                [("Texts SDF", ASCII + "àèéìòù", 0)]))])})
    profile_b, _c, _e = automatic.analyze_and_translate(good, "en", "it", backend="fake", log=silent)
    memory_b = _test_memory(profile_b)
    data = read_json(memory_b)
    data[EN[3]] = "La porta è chiusa. Trova prima la chiave, perché serve."
    write_json(memory_b, data)
    automatic.reapply(good, silent, test=True, outcome={})
    check("font with all the Italian letters: the translation stays as it is",
              [t for _, t in adapter_unity_loc.TablesFile(os.path.join(folder_b, TABLE_EN)).texts()][3]
              == data[EN[3]])
    # an unexpected error while reading the fonts does not stop the writing: no limit, as before
    real = font_unity.allowed_chars
    font_unity.allowed_chars = lambda *_a, **_k: (_ for _ in ()).throw(KeyError("odd bundle"))
    d = {}
    try:
        done = automatic.reapply(good, silent, test=True, outcome=d)
    except KeyError:
        done = False
    finally:
        font_unity.allowed_chars = real
    check("unexpected error while reading the fonts: the translations are written anyway, without limits",
              done and d["outcome"].code == "OK" and [t for _, t in adapter_unity_loc.TablesFile(
                  os.path.join(folder_b, TABLE_EN)).texts()][3] == data[EN[3]])


# --- XUnity: BepInEx log and after-play check ---------------------------------------------------------------------

def _unity_mono_game(folder, exe_name, company, product):
    import struct
    data = os.path.join(folder, exe_name + "_Data")
    os.makedirs(os.path.join(data, "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    with open(os.path.join(folder, exe_name + ".exe"), "wb") as f:
        f.write(bytes(pe))
    with open(os.path.join(data, "globalgamemanagers"), "wb") as f:
        f.write(b"\x00" * 48 + b"2022.3.10f1\x00")
    with open(os.path.join(data, "Managed", "Assembly-CSharp.dll"), "wb") as f:
        f.write(b"MZ" + "Start a new game".encode("utf-16-le") + b"\x00\x00")
    write(os.path.join(data, "app.info"), "%s\n%s" % (company, product))


def _fake_components(folder):
    import zipfile

    def _zip(path, entries):
        with zipfile.ZipFile(path, "w") as z:
            for name, content in entries.items():
                z.writestr(name, content)
        with open(path, "rb") as f:
            data = f.read()
        return {"url": "file://unused", "file": os.path.basename(path), "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "version": "test", "license": "-"}
    components.FOLDER = folder
    os.makedirs(folder, exist_ok=True)
    components.CATALOG = {
        "bepinex5_x64": _zip(os.path.join(folder, "bep.zip"), {
            "winhttp.dll": b"proxy", "BepInEx/core/BepInEx.dll": b"core"}),
        "xunity_bepinex5": _zip(os.path.join(folder, "xu.zip"), {
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin"})}


def test_log(base):
    found = checks.missing_chars([os.path.join(SIGNALS, "unity-tmp-missing-chars.log")])
    check("real log: characters missing from the fonts (also outside the basic plane) with the font name",
              set(found) == {"\u00d9", "\u2011", "\U0001F41A", "\u10e6"} and found["\u00d9"] == {"slkscr"})
    old = os.path.join(base, "old.log")
    write(old, "Character with ASCII value of 232 was not found in the Font Asset Glyph Table.\n")
    check("old TextMeshPro log: 'ASCII value of 232' -> è",
              set(checks.missing_chars([old])) == {"è"})
    check("missing letters replaced only with simple characters: è -> e', ò -> o' (not ó: the font may lack it), "
              "hyphen ‑ -> '-'; an emoji cannot be replaced",
              chars.adapt_without("Può è pre‑ordine", {"ò", "è", "‑"}, "it") == "Puo' e' pre-ordine"
              and chars.adapt_without("ok \U0001F41A", {"\U0001F41A"}, "it") is None
              and chars.adapt_without("niente", {"è"}, "it") == "niente")
    copy_dir = os.path.join(base, "copy_dir")
    text = open(os.path.join(SIGNALS, "unity-tmp-missing-chars.log"), encoding="utf-8").read()
    check("game copy recognized even without 'Mono path' (IL2CPP games: 'Discovering subsystems')",
              checks.of_this_copy(text.replace("C:/Giochi/Cast n Chill",
                                                      copy_dir.replace("\\", "/")), copy_dir) is True
              and checks.of_this_copy(text, copy_dir) is False)

    name = "pixel-test"
    game = os.path.join(base, "games", "Pixel")
    _unity_mono_game(game, "Pixel", "Studio Pixel", "Pixel")
    path = os.path.join(automatic.profiles_folder(), name + ".json")
    scan.save_json(path, {"name": name, "game_folder": game.replace(os.sep, "/"), "engine": "unity",
                                    "source_language": "en", "target_language": "it", "backend": "google",
                                    "live": "google", "file": []})
    adapter_unity.install(game, "en", "it", name, silent, local=False)
    cfg = os.path.join(game, *adapter_unity.CONFIG_BEPINEX.split("/"))
    check("installation: BepInEx also writes Unity's messages to its log (WriteUnityLog = true)",
              adapter_unity.unity_log_enabled(game) and "WriteUnityLog = true" in open(cfg).read())
    before = time.time() - 600
    after = time.time() - 120
    for rel in (adapter_unity.MANIFEST,):
        os.utime(os.path.join(game, *rel.split("/")), (before, before))
    # the first start: BepInEx completes its configuration (it keeps the value written by PatchSmith)
    write(cfg, "[Logging.Disk]\n\n## Include unity log messages in log file output.\nWriteUnityLog = true\n\n"
                "AppendLog = false\n", before)
    memory = project.memory_path({"name": name, "target_language": "it"})
    our_translations = {"The door is locked": "La porta è chiusa", "Hand line": "Riga è nostra", "Start": "Inizia"}
    write_json(memory, our_translations)
    write_json(memory[:-len(".json")] + ".automatiche.json", our_translations)
    cache = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    write(cache, "The door is locked=La porta è chiusa\nHand line=Riga è cambiata a mano\nStart=Inizia\n", before)
    log = os.path.join(game, *adapter_unity.BEPINEX_LOG.split("/"))
    write(log, "[Info   :   BepInEx] Chainloader startup complete\n[Warning: Unity Log] The character with Unicode "
                "value \\u00E8 was not found in the [Pixel SDF] font asset or any potential fallbacks. It was replaced "
                "by Unicode character \\u25A1 in text object [Text (TMP)].\n", after)
    whole = snapshot(base)
    problems = checks.check(path, apply=False, log=silent)
    font = next((p for p in problems if p.code == "MISSING_FONT_CHARS"), None)
    check("read-only check: missing letters found (they would be fixed automatically), nothing written",
              font is not None and font.state == "to_fix" and "«è»" in font.message
              and "One sentence with those letters" in font.message and snapshot(base) == whole
              and "missing_chars" not in read_json(path))
    problems = checks.check(path, apply=True, log=silent)
    font = next((p for p in problems if p.code == "MISSING_FONT_CHARS"), None)
    lines = open(cache, encoding="utf-8").read()
    check("fix: the line written by PatchSmith adapted (è -> e'), not the one changed by hand, characters "
              "noted in the profile; in memory è stays",
              font is not None and font.state == "fixed" and "The door is locked=La porta e' chiusa" in lines
              and "Hand line=Riga è cambiata a mano" in lines and read_json(path)["missing_chars"] == "è"
              and read_json(memory)["The door is locked"] == "La porta è chiusa")
    problems = checks.check(path, apply=True, log=silent)
    check("second check: nothing to redo", not any(p.code == "MISSING_FONT_CHARS" for p in problems))
    # from now on: the new cache lines and the live translator's answers avoid è
    adapter_unity.write_xunity_cache(game, "it", {"New line": "Novità è qui"}, silent,
                                      excluded=read_json(path)["missing_chars"])
    s = xunity_service.Service(path, port=1, log=silent, backend=backend.Fake())
    ours_now, _ = automatic.our_values(read_json(path), read_json(memory), {})
    check("from now on: new cache lines and live answers without è (é, à and ì stay); the adapted line "
              "is recognized as written by PatchSmith",
              "New line=Novità e' qui" in open(cache, encoding="utf-8").read()
              and s.adapt("Perché è così", "it") == "Perché e' così"
              and "La porta e' chiusa" in ours_now["The door is locked"])
    # a fix made in "Fix translations" with a letter the fonts do not have: with "Reapply" it goes into the cache
    # adapted, and counts as written (not "left as it was")
    data = read_json(memory)
    data["Quit"] = "Esci è"
    write_json(memory, data)
    corrections.mark_to_write(read_json(path), {"Quit": (None, "Esci è")})
    log_lines, d = [], {}
    automatic.reapply(game, log_lines.append, outcome=d)
    check("Reapply with the missing letters: the hand fix written adapted to the cache and counted as "
              "written", "Quit=Esci e'" in open(cache, encoding="utf-8").read()
              and d["outcome"].extra.get("fixes_written") == 1
              and not any("left as they were" in r for r in log_lines))
    # an unexpected error in the font check does not hide the game's other problems
    real = adapter_unity.xunity_cache_entries
    adapter_unity.xunity_cache_entries = lambda *_a, **_k: (_ for _ in ()).throw(KeyError("odd cache"))
    write(log, "[Info   :   BepInEx] Chainloader startup complete\n[Warning: Unity Log] The character with Unicode "
                "value \\u00F9 was not found in the [Pixel SDF] font asset or any potential fallbacks.\n")
    write(cfg, "[Logging.Disk]\nWriteUnityLog = false\n")
    log_lines = []
    try:
        problems = checks.check(path, apply=False, log=log_lines.append)
        ok = True
    except KeyError:
        problems, ok = [], False
    finally:
        adapter_unity.xunity_cache_entries = real
    check("unexpected error in the letter check: only one line in the log, the other problems stay",
              ok and any(p.code == "CONFIGURATION" for p in problems)
              and any("Font letter check failed" in r for r in log_lines))
    # an earlier configuration (WriteUnityLog off): the check puts it back
    write(cfg, "[Logging.Disk]\nWriteUnityLog = false\n")
    problems = checks.check(path, apply=False, log=silent)
    conf = next((p for p in problems if p.code == "CONFIGURATION"), None)
    check("earlier installation with WriteUnityLog off: the check turns it back on (CONFIGURATION)",
              conf is not None and "WriteUnityLog" in conf.detail and "letters missing" in conf.message)
    # BepInEx.cfg with non-UTF-8 bytes (a comment written by hand): it is switched on all the same, the rest stays
    # the same
    with open(cfg, "wb") as f:
        f.write(b"[General]\n# commento \xe8 a mano\n\n[Logging.Disk]\nWriteUnityLog = false\n")
    has_changed = adapter_unity.enable_unity_log(game)
    with open(cfg, "rb") as f:
        raw = f.read()
    check("BepInEx.cfg with non-UTF-8 bytes: WriteUnityLog on, the comment stays byte for byte",
              has_changed and b"# commento \xe8 a mano" in raw and b"WriteUnityLog = true" in raw
              and b"WriteUnityLog = false" not in raw)


def test_localization_log(base):
    """Unity Localization (IL2CPP, without BepInEx): the missing letters from the Player.log, then Reapply."""
    game = os.path.join(base, "loc-log")
    folder = fake.il2cpp_game(game, {TABLE_EN: fake.table_bundles([("UI_en", "en", EN)])})
    profile, _c, _e = automatic.analyze_and_translate(game, "en", "it", backend="fake", log=silent)
    p = read_json(profile)
    memory = project.memory_path(p)  # the real translation memory (a test one: in the test data folder)
    write_json(memory, {EN[0]: "Nuova partita", EN[1]: "Può continuare", EN[2]: "Opzioni"})
    automatic.reapply(game, silent, outcome={})
    old = time.time() - 600
    for n in os.listdir(folder):
        os.utime(os.path.join(folder, n), (old, old))
    text = open(os.path.join(SIGNALS, "unity-tmp-missing-chars.log"), encoding="utf-8").read()
    text = text.replace("C:/Giochi/Cast n Chill/castnchill_Data",
                          os.path.join(game, "Game_Data").replace("\\", "/"))
    text += ("The character with Unicode value \\u00F2 was not found in the [Texts SDF] font asset or any potential "
              "fallbacks. It was replaced by Unicode character \\u25A1 in text object [Text (TMP)].\n")
    log_file = os.path.join(os.environ["USERPROFILE"], "AppData", "LocalLow", "Fake Studio", "Fake Game",
                            "Player.log")
    write(log_file, text, time.time() - 60)
    check("Player.log in the signals of games with Unity Localization (the check at startup sees it)",
              "unity:Player.log" in checks.signals(profile))
    problems = checks.check(profile, apply=True, log=silent)
    font = next((x for x in problems if x.code == "MISSING_FONT_CHARS"), None)
    texts = [t for _, t in adapter_unity_loc.TablesFile(os.path.join(folder, TABLE_EN)).texts()]
    check("Unity Localization: missing ò noted in the profile and table rewritten (Può -> Puo'); characters "
              "found only in the game's texts (Ù, ღ) do not count",
              font is not None and font.state == "fixed" and "«ò»" in font.message and "Ù" not in font.message
              and "ò" in read_json(profile)["missing_chars"] and texts[1] == "Puo' continuare")


# --- Unreal --------------------------------------------------------------------------------------------------------

def test_unreal(base):
    from test_unreal import locmeta, locres_v2, pak_v9
    entries = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit"), ("UI", "Next", "Next -> page")]

    def game(name, other_ones):
        g = os.path.join(base, name)
        paks = os.path.join(g, "Proj", "Content", "Paks")
        os.makedirs(paks)
        file = [("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
                ("Proj/Content/Localization/Game/en/Game.locres", locres_v2(entries))]
        file += [("Proj/Content/Localization/Game/%s/Game.locres" % c, locres_v2([("UI", "Start", t)]))
                 for c, t in other_ones]
        with open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb") as f:
            f.write(pak_v9(file))
        return g, paks
    g, paks = game("unreal-fr", [("fr", "Démarrer"), ("ja", "ゲーム開始")])
    allowed = adapter_unreal.allowed_chars(g, set("Start Game"), {"en"})
    check("Unreal: with French (é) the font has Latin-1 (also ì, ò); Japanese does not count",
              allowed is not None and set("àèìòù") <= allowed and "ゲ" not in allowed and "→" not in allowed)
    memory = {"Start Game": "Inizia la partita è ò", "Quit": "Esci", "Next -> page": "Pagina → successiva ★"}
    refused = {}
    adapter_unreal.install(g, "it", "test-ue", memory, silent, refused=refused)
    was_read = adapter_unreal.Pak(os.path.join(paks, adapter_unreal.pak_name("it")))
    texts = [s for s, _ in adapter_unreal.read_locres(was_read.read_file(
        "Proj/Content/Localization/Game/en/Game.locres"))["strings"]]
    check("Unreal: Italian letters written; a character no language of the game uses and that cannot be "
              "adapted (★) leaves the original and goes back to the caller (problemi.json)",
              "Inizia la partita è ò" in texts and "Next -> page" in texts and "★" in refused.get("Next -> page", ""))
    g2, _ = game("unreal-en", [("de", "Spiel starten")])
    check("Unreal: English only, or other languages without accented letters: no clue, no limit",
              adapter_unreal.allowed_chars(g2, set("Start Game"), {"en"}) is None
              and adapter_unreal._adapt("Novità ★", set(), None, "it") == "Novità ★")


# --- labels too long ---------------------------------------------------------------------------------------------

LONG_LABELS = {"Credits": "Riconoscimenti", "Main Menu": "Menu principale del gioco", "Settings": "Impostazioni",
          "Open the chest to find the gold": "Apri il forziere per trovare tutto l'oro", "Back": "Indietro",
          "Quest Log": "Diario delle missioni"}
SHORT = {"Credits": "Crediti", "Main Menu": "Menu principale gioco", "Quest Log": "Missioni"}


class Labels(backend.Backend):
    """Fake translator that knows about a maximum space (like the local one): long the first time, short if the
    'width' context asks for it."""
    name = "test-labels"
    uses_context = True
    max_texts = 40

    def __init__(self):
        self.requests = []

    def translate_lines(self, lines, origin, dest_lang, contexts=None, rounds=None):
        output = []
        for i, r in enumerate(lines):
            c = (contexts or [None] * len(lines))[i] or {}
            self.requests.append((r, c.get("width")))
            output.append(SHORT.get(r, LONG_LABELS.get(r, "IT " + r)) if c.get("width")
                          else LONG_LABELS.get(r, "IT " + r))
        return output


class NoContext(backend.Backend):
    name = "test-online"

    def __init__(self):
        self.requests = []

    def translate_lines(self, lines, origin, dest_lang):
        self.requests += lines
        return [LONG_LABELS.get(r, "IT " + r) for r in lines]


def _profile(base, name):
    path = os.path.join(base, name + ".json")
    write_json(path, {"name": name, "game_folder": base, "source_language": "en", "target_language": "it",
                           "file": []})
    return path


def _without_shared():
    """Like a new PC: without the shared translation memory (the earlier tests put 'Credits' -> 'Crediti' in it)."""
    for p in (project.shared_path("en", "it"), project.shared_origin_path("en", "it")):
        if os.path.exists(p):
            os.remove(p)


def test_length(base):
    check("space of a label: with the official translations (or the game's space) 1.3 times the original + 2 "
              "or the longest of them; without, only the obvious cases (double and 7 more characters: 'Back' 10, "
              "'Credits' 13); long sentences and multi-line texts excluded",
              project.label_space("Back") == 10 and project.label_space("Credits") == 13
              and project.label_space("Credits", ["Crédits"]) == 12
              and project.label_space("Credits", ["Mitwirkende und Danksagungen"]) == 28
              and project.label_space("Credits", (), 20) == 20 and project.short_label("Main Menu")
              and not project.short_label("Open the chest to find the gold")
              and not project.short_label("Two\nlines"))
    path = _profile(base, "labels")
    p = Project(path, silent)
    b = Labels()
    texts = list(LONG_LABELS)
    p.translate_texts(texts, b, "en", "it")
    asked = [(t, n) for t, n in b.requests if n]
    problems = p.problems()
    machine = read_json(p._machine_path())
    check("labels too long asked again with the maximum number of characters (not the others, not the long "
              "sentence): the shortest valid one stays, also among the machine translations",
              sorted(asked) == [("Credits", 13), ("Main Menu", 17), ("Quest Log", 17)]
              and p.memory["Credits"] == "Crediti" and p.memory["Quest Log"] == "Missioni"
              and machine["Credits"] == "Crediti" and p.memory["Back"] == "Indietro"
              and p.memory["Settings"] == "Impostazioni" and p.shortened == 3)
    extra = automatic._extra_attempts(p)
    check("still too long: in problemi.json with the original text as key ('length', with the translation "
              "and the space); the outcome counts the shortened ones and the one noted now",
              extra.get("shortened") == 3 and extra.get("too_long") == 1
              and problems.get("Main Menu", {}).get("where") == "length"
              and problems["Main Menu"].get("value") == "Menu principale gioco"
              and problems["Main Menu"]["space"] == 17
              and "Credits" not in problems and "Crediti" not in json.dumps(problems))
    b2 = Labels()
    p2 = Project(path, silent)
    p2.translate_texts(texts, b2, "en", "it")
    check("only once: the next time no request (the too long one stays noted, and the outcome does not repeat "
              "it at every translation)", b2.requests == [] and "too_long" not in automatic._extra_attempts(p2)
              and Project(path, silent).problems()["Main Menu"]["where"] == "length")
    # written to the game, it stays noted as long as the translation is that one
    p = Project(path, silent)
    p._refused, p._applied = {}, {"Main Menu", "Credits"}
    p._record_applied()
    check("written to the game: it stays in the list as long as the translation does not change",
              p.problems()["Main Menu"]["where"] == "length")
    items = corrections.load(path)
    entry = next(x for x in items.entries if x.text == "Main Menu")
    check("'Fix translations': in the 'Too long' list with the original text",
              "long" in entry.problems and "too long" in entry.problems["long"]
              and entry in items.apply_filter("long"))
    # fixed by hand: not asked again
    p = Project(path, silent)
    p.fix({"Back": "Torna indietro alla schermata"})
    b3 = Labels()
    Project(path, silent).translate_texts(texts, b3, "en", "it")
    check("a long fix made by hand is not sent to the translator again", b3.requests == [])
    # translators that do not know about a maximum space: only noted, no extra requests
    _without_shared()
    path2 = _profile(base, "labels-online")
    online = NoContext()
    p2 = Project(path2, silent)
    p2.translate_texts(texts, online, "en", "it")
    check("with an online translator: no extra requests, the long labels noted for 'Too long'",
              sorted(online.requests) == sorted(texts) and p2.problems().get("Credits", {}).get("where") == "length")
    # longer official translations: there is room
    path3 = _profile(base, "labels-official")
    b4 = Labels()
    Project(path3, silent).translate_texts(["Credits"], b4, "en", "it", contexts={
        "Credits": {"key": "menu_credits", "references": {"de": "Mitwirkende Personen"}}})
    check("with a longer official translation ('Mitwirkende Personen') 'Riconoscimenti' fits",
              [n for _, n in b4.requests if n] == [])
    # the local translator's real prompt
    loc = backend.Local("4b", log=silent, server=object())
    check("local translator: the maximum number of characters in the prompt only when requested",
              "at most 12 characters" in loc._context_text({"width": 12}, "it")
              and "at most" not in loc._context_text({"key": "menu", "space": 12}, "it"))
    # the outcome
    e = outcome.Outcome(code="OK", extra={"shortened": 2, "too_long": 1})
    text = "\n".join(outcome._attempts_text(e))
    check("outcome: shortened labels and the one still long, with 'Fix translations' > 'Too long'",
              "2 labels were too long for their space" in text
              and "A label might not fit" in text
              and outcome.to_fix(e) and outcome.fixes_filter(e) == "long")
    singular = "\n".join(outcome._attempts_text(outcome.Outcome(code="OK", extra={"shortened": 1})))
    check("outcome with a single label: 'its space in the game' (not 'their space')",
              "A label was too long for its space in the game: I asked for a shorter one." in singular)
    check("'Fix translations': the reason reads well (characters and space)",
              "too long: 21 characters, usually about 17 fit" in entry.problems["long"])
    # noted with an online translator, then with the local one: asked shorter once
    _without_shared()
    path4 = _profile(base, "labels-then-local")
    Project(path4, silent).translate_texts(texts, NoContext(), "en", "it")
    b5 = Labels()
    p5 = Project(path4, silent)
    p5.translate_texts(texts, b5, "en", "it")
    b6 = Labels()
    Project(path4, silent).translate_texts(texts, b6, "en", "it")
    check("noted with an online translator: with the local translator shorter ones are asked, only once",
              sorted(t for t, n in b5.requests if n) == ["Credits", "Main Menu", "Quest Log"]
              and p5.memory["Credits"] == "Crediti" and b6.requests == []
              and Project(path4, silent).problems()["Main Menu"].get("requested") is True)
    # "Re-translate" of a label in the "Too long" list: asked with its space
    b7 = Labels()
    p7 = Project(path4, silent)
    p7.retranslate(["Main Menu"], b7, "en", "it", round_=3)
    check("'Re-translate' of a label too long: asked with its space (17 characters)",
              b7.requests == [("Main Menu", 17)])
    # an unexpected error while shortening the labels does not stop the translation (it is an extra)

    class Broken(Labels):
        def translate_lines(self, lines, origin, dest_lang, contexts=None, rounds=None):
            if any((c or {}).get("width") for c in contexts or []):
                return [object()] * len(lines)  # meaningless answers: error when putting them back together
            return super().translate_lines(lines, origin, dest_lang, contexts, rounds)
    _without_shared()
    log_lines = []
    p8 = Project(_profile(base, "labels-error"), log_lines.append)
    remaining = p8.translate_texts(texts, Broken(), "en", "it")
    check("unexpected error while shortening the labels: translation finished, translations saved, one line "
              "in the log", remaining == 0 and p8.memory["Credits"] == "Riconoscimenti"
              and any("shorter labels failed" in r for r in log_lines))


class ServerPrompt:
    """Instead of ollama.Server: answers based on the prompt (long, or short if it asks 'at most N characters')."""

    def __init__(self):
        self.prompt = []

    def launch(self):
        pass

    def call(self, path, data=None, timeout=180):
        if path == "/api/chat":
            prompt = data["messages"][0]["content"]
            text = prompt.split("\n\n\n", 1)[1]
            self.prompt.append(prompt)
            short = "at most" in prompt
            response = {"Credits": ("Crediti" if short else "Riconoscimenti")}.get(text, "IT " + text)
            return {"message": {"content": response}, "done_reason": "stop", "eval_count": 5,
                    "eval_duration": 100_000_000, "prompt_eval_count": 50}
        if path == "/api/ps":
            return {"models": []}
        return {}

    def close(self):
        pass


def test_local_length(base):
    _without_shared()
    server = ServerPrompt()
    b = backend.Local("4b", log=silent, server=server)
    p = Project(_profile(base, "labels-local"), silent)
    p.translate_texts(["Credits", "Play"], b, "en", "it")
    check("real local translator (fake server): 'Credits' asked again with the maximum of 13 characters in the "
              "prompt, 'Crediti' stays", p.memory["Credits"] == "Crediti"
              and sum("at most 13 characters" in x for x in server.prompt) == 1)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    for env_var, name in (("TEMP", "temp"), ("TMP", "temp"), ("USERPROFILE", "user"),
                            ("LOCALAPPDATA", "appdata"), ("APPDATA", "appdata-roaming")):
        os.environ[env_var] = os.path.join(base, name)
        os.makedirs(os.environ[env_var], exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "data_folder")
    os.environ["PATCHSMITH_DATA"] = project.DATA_ROOT
    for d in ("memory", "profiles"):
        os.makedirs(os.path.join(project.DATA_ROOT, d), exist_ok=True)
    automatic.PROFILES_FOLDER = None
    adapter_unity.is_running = lambda *_: False
    from patchsmith import ollama
    ollama.folder = lambda: os.path.join(base, "ollama")  # log of the test live translator
    _fake_components(os.path.join(base, "components"))
    for name, test in (("font", test_font), ("loc", test_unity_localization), ("log", test_log),
                        ("log-loc", test_localization_log), ("unreal", test_unreal),
                        ("length", test_length), ("length-local", test_local_length)):
        folder = os.path.join(base, name)
        os.makedirs(folder)
        try:
            test(folder)
        except Exception as e:  # noqa: BLE001 - a broken test does not stop the others
            import traceback
            traceback.print_exc()
            check("%s: unexpected error (%s: %s)" % (name, type(e).__name__, e), False)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
