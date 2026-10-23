"""Tests of the Unity Localization adapter (text tables inside the AssetBundles).

Usage: python -I test/test_unity_loc.py <empty_temp_folder> [<bundle with the string tables>]
Always, with fake bundles and games (test/fake_unity.py, no files from real games):
- reading of the bundles (uncompressed, LZ4, LZMA, also in pieces) and of the text tables; the asset tables
  (references, not texts) are not translated;
- a bundle's language from its name ("english(en)"), from its contents ("english", "localization_en",
  "chinese(simplified)") or from the language name if the bundle cannot be read; the official language found this
  way (official_language);
- IL2CPP game with a catalog that checks the fingerprint (CRC): only the translated bundle's one is removed, with
  backup; "Restore originals" puts back catalog and bundle identical; the after-play check sees the catalog put back
  by the game;
- binary catalog (catalog.bin), CRC that cannot be found, anti-cheat, tables without the source language, Italian
  already in the game: no file changed and a clear reason.
With a real bundle (e.g. a game's English one, which is only read and copied): identical rewrite, texts changed and
read back, full flow with the test translator on a minimal copy of the game and identical restore.
"""
import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from patchsmith import (adapter_unity_loc, auto as automatic, checks,  # noqa: E402
                        official_language, project,
                        detection)
from patchsmith import unityfs  # noqa: E402
import fake_unity as fake  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def fingerprint(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


EN = ["New Game", "Continue", "Options", "The door is locked. Find the key first.", "Quit to desktop"]
EN2 = ["Talk to the blacksmith", "You found a rusty sword!"]
FR = ["Nouvelle partie", "Continuer", "Options", "La porte est fermée.", "Quitter"]
IT = ["Nuova partita", "Continua", "Opzioni", "La porta è chiusa.", "Esci"]
GUID = ["0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"]


def table(name, code, texts, **options):
    return fake.table_bundles([(name, code, texts)], **options)


def test_format(base):
    check("LZ4: literals and repeats (overlapping too)",
              unityfs.lz4_decompress(b"\x50hello", 5) == b"hello"
              and unityfs.lz4_decompress(b"\x32abc\x03\x00", 9) == b"abcabcabc"
              and unityfs.lz4_decompress(b"\x15a\x01\x00", 10) == b"a" * 10)
    inner = fake.tables_file([("UI_en", "en", EN), ("Asset_en", "en", GUID)])
    resources = b"\x05\x00\x00\x00Ciao!" * 300
    all_ = True
    for compression in (None, "lz4", "lzma"):
        byte = fake.bundle([("CAB-1", inner), ("CAB-1.resS", resources)], compression=compression, block=700)
        b = unityfs.Bundle(byte)
        texts = [t for _, t in adapter_unity_loc.TablesFile(byte).texts()]
        path = os.path.join(base, "formato-%s.bundle" % compression)
        with open(path, "wb") as f:
            f.write(byte)
        old = unityfs._LZMA_PIECE
        unityfs._LZMA_PIECE = 500  # also LZMA in pieces (in games: blocks of more than 16 MB)
        try:
            with unityfs.Index(path) as idx:
                pieces = {n: b"".join(p for _, p in idx.pieces(s, s + d)) for n, s, d, _ in idx.nodes}
        finally:
            unityfs._LZMA_PIECE = old
        all_ = all_ and b.file["CAB-1"] == inner and texts == EN and pieces == {"CAB-1": inner,
                                                                                  "CAB-1.resS": resources}
    check("uncompressed, LZ4 and LZMA (in blocks) bundles: inner files and tables read, also in pieces; the "
              "asset table (references) is not among the texts", all_)
    broken = os.path.join(base, "rotto.bundle")
    with open(broken, "wb") as f:
        f.write(fake.bundle([("CAB-1", inner)], compression="lz4", block=700)[:-50])  # truncated
    try:
        with unityfs.Index(broken) as idx:
            for _ in idx.pieces():
                pass
        error = None
    except ValueError as e:
        error = e
    check("truncated bundle: format error (ValueError), not a crash", isinstance(error, ValueError))
    # uncompressed and in a single block (like the large bundles of SWORN or LumenTale): read in pieces, only the part
    # that is needed
    big = os.path.join(base, "un-blocco.bundle")
    with open(big, "wb") as f:
        f.write(fake.bundle([("CAB-1", inner), ("CAB-1.resS", resources)], block=1 << 30))
    old = unityfs._LZMA_PIECE
    unityfs._LZMA_PIECE = 500
    try:
        with unityfs.Index(big) as idx:
            blocks = len(idx.blocks)
            _, s, d, _ = idx.nodes[1]
            pieces = list(idx.pieces(s + 10, s + 1700))
    finally:
        unityfs._LZMA_PIECE = old
    check("uncompressed bundle in a single block: read in small pieces, only the requested part",
              blocks == 1 and len(pieces) == 4 and all(len(p) <= 500 for _, p in pieces)
              and pieces[0][0] == s + 10 and b"".join(p for _, p in pieces) == resources[10:1700])
    # LZMA in pieces with a stream that ends before the size written in the index: no EOFError
    short = bytearray(fake.bundle([("CAB-1", inner)], compression="lzma", block=1 << 20))
    q = short.index(b"\x00" * 16 + b"\x00\x00\x00\x01") + 20  # first block entry: decompressed size
    short[q:q + 4] = (int.from_bytes(short[q:q + 4], "big") + 300).to_bytes(4, "big")
    path = os.path.join(base, "lzma-corto.bundle")
    with open(path, "wb") as f:
        f.write(bytes(short))
    unityfs._LZMA_PIECE = 500
    try:
        with unityfs.Index(path) as idx:
            was_read = b"".join(p for _, p in idx.pieces())
        error = None
    except Exception as e:  # noqa: BLE001 - any error that is not a format error is the defect being looked for
        was_read, error = b"", e
    finally:
        unityfs._LZMA_PIECE = old
    check("LZMA in pieces shorter than expected: what is there is read, no EOFError",
              (error is None and was_read == inner) or isinstance(error, ValueError))
    f = adapter_unity_loc.TablesFile(fake.table_bundles([("UI_en", "en", EN)]))
    reread_texts = [t for _, t in adapter_unity_loc.TablesFile(f.serialize(
        {pos: "IT %d àèìòù" % k for k, (pos, _) in enumerate(f.texts())})).texts()]
    check("table rewritten (different lengths, accents): read back equal and in the same order",
              reread_texts == ["IT %d àèìòù" % k for k in range(len(EN))])


def test_languages(base):
    # names like LumenTale: the language in full, without a code; the Locales and the shared data are not languages
    a = os.path.join(base, "nomi-lumentale")
    os.makedirs(a)
    for name, byte in {
        "localization-string-tables-english_assets_all.bundle": table("UI_en", "en", EN, compression="lz4"),
        "localization-string-tables-italiano_assets_all.bundle": table("UI_it", "it", IT),
        "localization-string-tables-français_assets_all.bundle": table("UI_fr", "fr", FR),
        "localization-locales_assets_all.bundle": fake.bundle([("CAB-loc", b"\x00" * 64)]),
        "localization-assets-shared_assets_all.bundle": table("Immagini", "en", GUID),
    }.items():
        with open(os.path.join(a, name), "wb") as f:
            f.write(byte)
    check("language from the contents (m_LocaleId): 'english', 'italiano', 'français' without a code in the name; "
              "Locales and asset tables ignored",
              adapter_unity_loc.tables_by_content(a) == {
                  "en": "localization-string-tables-english_assets_all.bundle",
                  "it": "localization-string-tables-italiano_assets_all.bundle",
                  "fr": "localization-string-tables-français_assets_all.bundle"})
    # names like Lego Party and Rogue Blight; for an unreadable bundle the language name counts
    b = os.path.join(base, "nomi-vari")
    os.makedirs(b)
    for name, byte in {
        "localization_en_assets_all.bundle": table("config_ui_en", "en", EN),
        "localization-string-tables-chinese(simplified)_assets_all.bundle": table("UI_zh", "zh-CN", ["开始游戏"]),
        "localization-string-tables-deutsch_assets_all.bundle": b"non e' un bundle",
        "localization-asset-tables-english(en)_assets_all.bundle": table("Asset_en", "en", EN),
        "localization-string-tables-polish(pl)_assets_all.bundle": b"rovinato, ma il codice e' nel nome",
    }.items():
        with open(os.path.join(b, name), "wb") as f:
            f.write(byte)
    check("language: 'localization_en', 'chinese(simplified)' from the contents (zh-CN), '(pl)' from the name, "
              "unreadable 'deutsch' from the language name; asset tables excluded",
              sorted(adapter_unity_loc.tables_by_content(b)) == ["de", "en", "pl", "zh-cn"]
              and adapter_unity_loc.tables(b)["en"] == "localization_en_assets_all.bundle")
    # official language: the catalog does not list the Locales, the tables say Italian is there
    game = os.path.join(base, "ufficiale-dal-contenuto")
    fake.il2cpp_game(game, {"localization-string-tables-english_assets_all.bundle": table("UI_en", "en", EN),
                               "localization-string-tables-italiano_assets_all.bundle": table("UI_it", "it", IT)},
                       ("catalog.json", fake.catalog([])))
    languages, data = official_language.languages_in_files(game, "Unity", "it")
    check("official language: Italian found from the tables (the catalog does not say it)",
              data.get("found") is True and {"en", "it"} <= {c.lower() for c in languages})


def _flow(base, name, bundle_aa, catalog, extra=None, origin="en"):
    """Analyze and translate (test translator) on a fake IL2CPP game: (game, bundle_folder, profile, outcome,
    fingerprints before)."""
    game = os.path.join(base, name)
    bundle_folder = fake.il2cpp_game(game, bundle_aa, catalog, extra)
    before = {n: fingerprint(os.path.join(bundle_folder, n)) for n in bundle_aa}
    if catalog:
        before[catalog[0]] = fingerprint(os.path.join(os.path.dirname(bundle_folder), catalog[0]))
    profile, _full, outcome = automatic.analyze_and_translate(game, origin, "it", backend="fake", log=silent)
    return game, bundle_folder, profile, outcome, before


def _unchanged(bundle_folder, before):
    aa = os.path.dirname(bundle_folder)
    return all(fingerprint(os.path.join(bundle_folder if n.endswith(".bundle") else aa, n)) == h
               for n, h in before.items()) and not any("patchsmith" in n for d in (aa, bundle_folder)
                                                      for n in os.listdir(d))


def test_crc(base):
    en = "localization-string-tables-english(en)_assets_all.bundle"
    fr = "localization-string-tables-french(fr)_assets_all.bundle"
    catalog = fake.catalog([(en, 3530401984), (fr, 167159835)])
    game, bundle_folder, profile, outcome, before = _flow(
        base, "crc", {en: table("UI_en", "en", EN, compression="lz4"), fr: table("UI_fr", "fr", FR)},
        ("catalog.json", catalog))
    catalog_path = os.path.join(os.path.dirname(bundle_folder), "catalog.json")
    entries = json.load(open(profile, encoding="utf-8"))["file"] if profile else []
    check("CRC checked: profile with the catalog to fix for the English bundle",
              entries == [{"path": "Gioco_Data/StreamingAssets/aa/StandaloneWindows64/" + en,
                        "format": "unity-tables", "catalog": "Gioco_Data/StreamingAssets/aa/catalog.json"}])
    translated_items = [t for _, t in adapter_unity_loc.TablesFile(os.path.join(bundle_folder, en)).texts()]
    after = read_file(catalog_path)
    check("CRC: English bundle translated; in the catalog CRC 0 only for it (the French one stays), same length",
              translated_items == ["[it] " + t for t in EN] and fake.catalog_crc(after) == {en: 0, fr: 167159835}
              and len(after) == len(catalog) and fingerprint(os.path.join(bundle_folder, fr)) == before[fr])
    check("CRC: backup of the original catalog and its info (like the other files)",
              read_file(catalog_path + project.BACKUP_SUFFIX) == catalog
              and project.read_info(catalog_path) is not None)
    reapply_outcome = {}
    check("CRC: Reapply does the same again (catalog and bundle as before)",
              automatic.reapply(game, silent, test=True, outcome=reapply_outcome)
              and read_file(catalog_path) == after and fake.catalog_crc(after)[en] == 0)
    # a new "Analyze and translate": in the game the catalog already has CRC 0 (written by PatchSmith), but the game
    # profile's entry must keep naming it (the original in the backup counts)
    profile2, _full, outcome2 = automatic.analyze_and_translate(game, "en", "it", backend="fake", log=silent)
    check("CRC: a new 'Analyze and translate' keeps the catalog in the profile (the original counts, not the CRC "
              "already 0)",
              profile2 == profile and outcome2["outcome"].code == "OK"
              and json.load(open(profile, encoding="utf-8"))["file"] == entries and read_file(catalog_path) == after)
    # the game puts back its files (Steam's file verification): the after-play check sees it
    shutil.copyfile(catalog_path + project.BACKUP_SUFFIX, catalog_path)
    shutil.copyfile(os.path.join(bundle_folder, en + project.BACKUP_SUFFIX), os.path.join(bundle_folder, en))
    c = checks._Context(profile, json.load(open(profile, encoding="utf-8")), {}, silent, False, None)
    problems = checks._verify_file(c)
    check("CRC: catalog put back by the game -> the after-play check puts it back (Reapply)",
              any(p.code == "RESTORED_BY_GAME" and "catalog.json" in p.message for p in problems))
    automatic.reapply(game, silent, test=True)
    check("CRC: after Reapply the bundle is translated again and the CRC is 0 again in the catalog",
              [t for _, t in adapter_unity_loc.TablesFile(os.path.join(bundle_folder, en)).texts()]
              == ["[it] " + t for t in EN] and read_file(catalog_path) == after)
    check("CRC: Restore originals puts back identical catalog and bundle, no PatchSmith file",
              automatic.restore(game, silent) and _unchanged(bundle_folder, before))

    # two bundles for the source language (tables packed separately), both with the CRC
    en2 = "localization-string-tables-english(en)_assets_dialogues.bundle"
    catalog = fake.catalog([(en, 11), (en2, 22), (fr, 33)])
    game, bundle_folder, profile, outcome, before = _flow(
        base, "crc-due", {en: table("UI_en", "en", EN), en2: table("Dialoghi_en", "en", EN2, compression="lzma"),
                          fr: table("UI_fr", "fr", FR)}, ("catalog.json", catalog))
    after = read_file(os.path.join(os.path.dirname(bundle_folder), "catalog.json"))
    check("CRC with two English bundles: both translated, CRC 0 for both, the French one stays",
              [t for _, t in adapter_unity_loc.TablesFile(os.path.join(bundle_folder, en2)).texts()]
              == ["[it] " + t for t in EN2] and fake.catalog_crc(after) == {en: 0, en2: 0, fr: 33})
    check("CRC with two bundles: Restore puts everything back identical",
              automatic.restore(game, silent) and _unchanged(bundle_folder, before))

    # a game update changes only the catalog (not the English table): the new catalog has the CRC again and the game
    # would not load the translated table. The after-play check fixes it by itself (Reapply).
    catalog = fake.catalog([(en, 3530401984), (fr, 167159835)])
    game, bundle_folder, profile, outcome, before = _flow(
        base, "crc-aggiornato", {en: table("UI_en", "en", EN), fr: table("UI_fr", "fr", FR)},
        ("catalog.json", catalog))
    catalog_path = os.path.join(os.path.dirname(bundle_folder), "catalog.json")
    updated = fake.catalog([(en, 3530401984), (fr, 4242), ("nuovo_assets_all.bundle", 77)])
    with open(catalog_path, "wb") as f:
        f.write(updated)
    c = checks._Context(profile, json.load(open(profile, encoding="utf-8")), {}, silent, False, None)
    problems = checks._verify_file(c)
    check("CRC: update that changes only the catalog -> automatic fix (Reapply), not 'Analyze and "
              "translate'",
              [p.code for p in problems] == ["RESTORED_BY_GAME"] and problems[0].safe
              and problems[0].key == "reapply" and "catalog.json" in problems[0].message)
    automatic.reapply(game, silent, test=True)
    after = read_file(catalog_path)
    check("CRC: after the update the new catalog has CRC 0 for the translated bundle (the rest as in the "
              "update) and becomes the original",
              fake.catalog_crc(after) == {en: 0, fr: 4242, "nuovo_assets_all.bundle": 77}
              and read_file(catalog_path + project.BACKUP_SUFFIX) == updated
              and [t for _, t in adapter_unity_loc.TablesFile(os.path.join(bundle_folder, en)).texts()]
              == ["[it] " + t for t in EN])
    check("CRC: after the update Restore puts back the updated catalog and the original bundle",
              automatic.restore(game, silent) and read_file(catalog_path) == updated
              and fingerprint(os.path.join(bundle_folder, en)) == before[en]
              and not [n for d in (bundle_folder, os.path.dirname(bundle_folder)) for n in os.listdir(d)
                       if "patchsmith" in n])

    # catalog without CRC (like SWORN): the catalog is not touched
    game, bundle_folder, profile, outcome, before = _flow(
        base, "crc-zero", {en: table("UI_en", "en", EN)}, ("catalog.json", fake.catalog([(en, 0)])))
    check("CRC 0 (like SWORN): bundle translated, catalog untouched and without backup",
              outcome["outcome"].code == "OK" and fingerprint(os.path.join(os.path.dirname(bundle_folder),
                                                                      "catalog.json")) == before["catalog.json"]
              and not os.path.exists(os.path.join(os.path.dirname(bundle_folder), "catalog.json"
                                                  + project.BACKUP_SUFFIX))
              and "catalog" not in json.load(open(profile, encoding="utf-8"))["file"][0])


def test_refusals(base):
    en = "localization-string-tables-english(en)_assets_all.bundle"
    # binary catalog (Addressables 2, Unity 6): it is not known whether it checks the fingerprint
    game, bundle_folder, profile, outcome, before = _flow(
        base, "catalogo-bin", {en: table("UI_en", "en", EN)}, ("catalog.bin", b"\x01\x02catalogo binario"))
    e = outcome["outcome"]
    check("catalog.bin: not supported, with the binary catalog as the reason, no file modified",
              profile is None and e.code == "NOT_SUPPORTED" and "catalog.bin" in e.reason
              and "BepInEx" not in e.reason and _unchanged(bundle_folder, before))
    # the catalog checks the fingerprint of other bundles and does not name this one: it cannot be removed
    game, bundle_folder, profile, outcome, before = _flow(
        base, "crc-sconosciuto", {en: table("UI_en", "en", EN)},
        ("catalog.json", fake.catalog([("altro.bundle", 777)])))
    e = outcome["outcome"]
    check("CRC not found in the catalog: not supported (fingerprint), no file modified",
              profile is None and e.code == "NOT_SUPPORTED" and "CRC" in e.reason
              and _unchanged(bundle_folder, before))
    # anti-cheat: before looking at the tables, also in IL2CPP games
    game, bundle_folder, profile, outcome, before = _flow(
        base, "anticheat", {en: table("UI_en", "en", EN)}, ("catalog.json", fake.catalog([(en, 0)])),
        extra={"EasyAntiCheat/EasyAntiCheat_EOS_Setup.exe": b"finto"})
    check("anti-cheat (IL2CPP with tables): detected, ANTICHEAT, no bundle written",
              any(r["section"] == "AntiCheat" for r in detection.detect(game)["results"])
              and profile is None and outcome["outcome"].code == "ANTICHEAT" and _unchanged(bundle_folder, before))
    # tables, but not in the source language: the reason says so (not "requires BepInEx 6")
    game, bundle_folder, profile, outcome, before = _flow(
        base, "senza-inglese", {"localization-string-tables-french(fr)_assets_all.bundle": table("UI_fr", "fr", FR)},
        ("catalog.json", fake.catalog([])))
    e = outcome["outcome"]
    check("tables without the source language: clear reason with the game's languages (by name) and what to "
              "choose, no file modified",
              profile is None and e.code == "NOT_SUPPORTED" and "text tables" in e.reason
              and "original language" in e.reason and "French (fr)" in e.reason and '"From:"' in e.reason
              and "IL2CPP" not in e.reason and "BepInEx" not in e.reason and _unchanged(bundle_folder, before))
    # with the language the game has as source language chosen: its table is translated
    game, bundle_folder, profile, outcome, before = _flow(
        base, "partenza-francese",
        {"localization-string-tables-french(fr)_assets_all.bundle": table("UI_fr", "fr", FR)},
        ("catalog.json", fake.catalog([])), origin="fr")
    check("tables without English, French chosen as source: the French table is translated",
              outcome["outcome"].code == "OK" and [t for _, t in adapter_unity_loc.TablesFile(os.path.join(
                  bundle_folder, "localization-string-tables-french(fr)_assets_all.bundle")).texts()]
              == ["[it] " + t for t in FR])
    # Italian already in the game, with Lego Party-style names: official language
    game, bundle_folder, profile, outcome, before = _flow(
        base, "italiano-dentro", {"localization_en_assets_all.bundle": table("ui_en", "en", EN),
                                  "localization_it_assets_all.bundle": table("ui_it", "it", IT)},
        ("catalog.json", fake.catalog([])))
    check("Italian already in the game ('localization_it' names): OFFICIAL, no file modified",
              profile is None and outcome["outcome"].code == "OFFICIAL" and _unchanged(bundle_folder, before))
    # names without a code: the English table is found from the contents and translated (before: "not supported")
    game, bundle_folder, profile, outcome, before = _flow(
        base, "nomi-senza-codice", {"localization-string-tables-english_assets_all.bundle":
                                    table("UI_en", "en", EN, compression="lz4")},
        ("catalog.json", fake.catalog([("localization-string-tables-english_assets_all.bundle", 0)])))
    translated_items = [t for _, t in adapter_unity_loc.TablesFile(os.path.join(
        bundle_folder, "localization-string-tables-english_assets_all.bundle")).texts()]
    check("English table without a code in the name: found from the contents and translated",
              outcome["outcome"].code == "OK" and translated_items == ["[it] " + t for t in EN])


def test_real_bundle(base, bundle):
    data = open(bundle, "rb").read()
    f = adapter_unity_loc.TablesFile(data)
    texts = f.texts()
    b = unityfs.Bundle(data)
    name = b.nodes[0][0]
    s = unityfs.SerializedFile(b.file[name])
    check("bundle read: %d texts; inner file rewritten without changes is identical byte for byte" % len(texts),
              texts and s.with_new_texts({}) == b.file[name])
    new_items = {pos: "IT %d àèìòù" % k for k, (pos, _) in enumerate(texts)}
    reread_texts = adapter_unity_loc.TablesFile(f.serialize(new_items)).texts()
    check("texts changed (different lengths, accents): read back equal and in the same order",
              [t for _, t in reread_texts] == ["IT %d àèìòù" % k for k in range(len(texts))])
    # full flow on a minimal copy of the game: only the bundle and the catalog, under *_Data
    game = os.path.join(base, "Gioco")
    bundle_folder = os.path.join(game, "Gioco_Data", "StreamingAssets", "aa", "StandaloneWindows64")
    os.makedirs(bundle_folder)
    copy_dir = os.path.join(bundle_folder, os.path.basename(bundle))
    shutil.copyfile(bundle, copy_dir)
    catalog = os.path.join(os.path.dirname(os.path.dirname(bundle)), "catalog.json")
    if os.path.exists(catalog):
        shutil.copyfile(catalog, os.path.join(os.path.dirname(bundle_folder), "catalog.json"))
    before = hashlib.sha256(open(copy_dir, "rb").read()).hexdigest()
    path = automatic._prepare_unity_tables(game, None, "en", "it", silent)
    p = project.Project(path, silent, test=True)
    p.memory = {t: "[it] " + t for _, t in texts}
    p._save_memory = lambda: None
    p.apply()
    translated_items = [t for _, t in adapter_unity_loc.TablesFile(copy_dir).texts()]
    check("flow: English table translated in the bundle, with backup",
              translated_items == ["[it] " + t for _, t in texts] and os.path.exists(copy_dir + project.BACKUP_SUFFIX))
    check("restore: bundle identical to the original, no PatchSmith file left",
              automatic.restore(game, silent) and hashlib.sha256(open(copy_dir, "rb").read()).hexdigest()
              == before and not [n for n in os.listdir(bundle_folder) if "patchsmith" in n])


def _bin_catalog(entries, double=None):
    """Fake catalog.bin like the Addressables 2 one (Big Dig): for each bundle the entry name with the fingerprint,
    the Hash128 fingerprint (16 bytes), the bundle's name (a string with its length in front) and the record of 5
    numbers (fingerprint, name, CRC, size, common data). double: name of a bundle with a second record (ambiguous)."""
    import struct
    data = bytearray(b"\x42\x89\xe3\x0d\x02\x00\x00\x00" + b"\x00" * 24)

    def read_string(s):
        data.extend(struct.pack("<I", len(s)))
        start = len(data)
        data.extend(s)
        return start
    for i, (name, crc) in enumerate(entries):
        hex_str = ("%02x" % (i + 1)) * 16
        read_string(("%s_%s.bundle" % (name[:-len(".bundle")], hex_str)).encode())
        read_string(("{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\%s"
                     % name).encode())
        h = len(data)
        data.extend(bytes.fromhex(hex_str))
        b = read_string(hex_str.encode())
        for _ in range(2 if name == double else 1):
            data.extend(struct.pack("<5I", h, b, crc, 1234 + i, 0x20))
        data.extend(b"\x00\x01")  # the records are not aligned to 4 bytes
    return bytes(data)


def test_catalog_bin(base):
    """Binary catalog (catalog.bin, Addressables 2): the translated bundle's CRC is zeroed, nothing else."""
    import struct
    english = "localization-string-tables-english(en)_assets_all.bundle"
    french = "localization-string-tables-french(fr)_assets_all.bundle"
    aa = os.path.join(base, "aa")
    os.makedirs(os.path.join(aa, "StandaloneWindows64"))
    for n in (english, french):
        open(os.path.join(aa, "StandaloneWindows64", n), "wb").write(b"UnityFS")
    catalog = os.path.join(aa, "catalog.bin")
    byte = _bin_catalog([(french, 0x11111111), (english, 0xd345c8b3)])
    open(catalog, "wb").write(byte)
    bundle = os.path.join(aa, "StandaloneWindows64", english)
    state = adapter_unity_loc.bundle_crc(bundle)
    new = adapter_unity_loc.remove_crc(byte, [english])
    mismatched = [i for i in range(len(byte)) if byte[i] != new[i]] if new and len(new) == len(byte) else None
    crc_fr = adapter_unity_loc._record_bin(byte, french)
    check("catalog.bin: CRC of the English bundle found ('checked'); only that one removed (4 bytes at 0, the "
              "French one stays)", state[0] == "checked" and mismatched is not None and len(mismatched) == 4
              and new[mismatched[0]:mismatched[0] + 4] == b"\x00" * 4 and crc_fr
              and struct.unpack_from("<I", new, crc_fr[0])[0] == 0x11111111)
    open(catalog, "wb").write(new)
    check("catalog.bin: after the change the bundle is 'unlocked'",
              adapter_unity_loc.bundle_crc(bundle)[0] == "unlocked")
    ambiguous = _bin_catalog([(english, 0xd345c8b3)], double=english)
    open(catalog, "wb").write(ambiguous)
    check("catalog.bin: two records for the same bundle -> 'binary' and no change (nothing is touched)",
              adapter_unity_loc.bundle_crc(bundle)[0] == "binary"
              and adapter_unity_loc.remove_crc(ambiguous, [english]) is None)
    open(catalog, "wb").write(_bin_catalog([(french, 1)]))
    check("catalog.bin that does not name the bundle -> 'binary'", adapter_unity_loc.bundle_crc(bundle)[0] == "binary")


def main(base, bundle=None):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    # user data (game profiles, translation memory) in the test folder
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER
    for d in ("memory", "profiles"):
        os.makedirs(os.path.join(project.PROJECT_FOLDER, d))
    automatic.PROFILES_FOLDER = os.path.join(project.PROJECT_FOLDER, "profiles")
    test_format(_folder(base, "format"))
    test_languages(_folder(base, "languages"))
    test_crc(_folder(base, "crc"))
    test_catalog_bin(_folder(base, "catalogo-bin"))
    test_refusals(_folder(base, "rifiuti"))
    if bundle:
        test_real_bundle(_folder(base, "vero"), bundle)
    else:
        print("(no real bundle given: only the tests with the fake bundles)")
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


def _folder(base, name):
    p = os.path.join(base, name)
    os.makedirs(p, exist_ok=True)
    return p


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None) else 1)
