"""Tests of the Unity adapter with a fake game and fake packages (no downloads, no real game).

Usage: python -I test/test_unity.py <empty_temp_folder>
"""
import hashlib
import io
import json
import os
import shutil
import struct
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import adapter_unity, components, project  # noqa: E402
import fake_unity as fake  # noqa: E402

OUTCOMES = []
NAME = "prova-unity-test"


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def create_game(folder, conflict=None):
    os.makedirs(os.path.join(folder, "Gioco_Data", "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    open(os.path.join(folder, "Gioco.exe"), "wb").write(bytes(pe))
    open(os.path.join(folder, "UnityPlayer.dll"), "wb").write(b"finto")
    open(os.path.join(folder, "Gioco_Data", "globalgamemanagers"), "wb").write(b"\x00" * 48 + b"6000.3.12f1\x00")
    sentences = ["Start a new game", "Welcome to the old mine. Collect all the gems!", "Press any key to continue",
             "The door is locked. Find the key first.", "Settings and options"]
    code = b"".join(f.encode("utf-16-le") + b"\x00\x00" for f in sentences)
    open(os.path.join(folder, "Gioco_Data", "Managed", "Assembly-CSharp.dll"), "wb").write(b"MZ" + code)
    if conflict:
        open(os.path.join(folder, conflict), "wb").write(b"altro loader")


def create_zip(path, entries):
    with zipfile.ZipFile(path, "w") as z:
        for name, content in entries.items():
            z.writestr(name, content)
    data = open(path, "rb").read()
    return {"url": "file://non-usato", "file": os.path.basename(path), "dimensione": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "versione": "prova", "licenza": "-"}


def prepare_components(folder):
    components.FOLDER = folder
    os.makedirs(folder, exist_ok=True)
    components.CATALOG = {
        "bepinex5_x64": create_zip(os.path.join(folder, "bep.zip"), {
            "winhttp.dll": b"proxy", "doorstop_config.ini": b"[General]\r\nenabled=true\r\n",
            ".doorstop_version": b"4.5.0", "changelog.txt": b"log",
            "BepInEx/core/BepInEx.dll": b"core", "BepInEx/core/BepInEx.Preloader.dll": b"pre"}),
        "xunity_bepinex5": create_zip(os.path.join(folder, "xu.zip"), {
            "BepInEx/core/XUnity.Common.dll": b"common",
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin",
            "BepInEx/plugins/XUnity.AutoTranslator/Translators/GoogleTranslate.dll": b"google"}),
    }


def config(folder):
    return open(os.path.join(folder, "BepInEx", "config", "AutoTranslatorConfig.ini"), encoding="utf-8").read()


def test_bundle_extraction(game):
    """estrai_testi with the pieces of today's Unity games (fake bundles, see fake_unity.py)."""
    create_game(game)
    data = os.path.join(game, "Gioco_Data")
    managed = os.path.join(data, "Managed")

    def write(rel, byte):
        p = os.path.join(data, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(byte)
    # game code in a module of its own (like SunHaven.Core.dll) and a third-party library (not game texts)
    write("Managed/Gioco.Core.dll", fake.dll(["Harvest the golden wheat before winter", "Talk to the mayor"]))
    write("Managed/Newtonsoft.Json.dll", fake.dll(["Unexpected end when reading the JSON document"]))
    # compressed date.unity3d (LZ4 in small blocks): a text straddling two blocks; not the images (.resS)
    long_text = "The ancient bridge collapsed behind you, there is no way back now"
    resources = fake.serialized_strings(["Open the treasure chest", long_text, "Rest at the campfire"])
    cut = resources.index(long_text.encode()) + 10
    unity3d = fake.bundle([("resources.assets", resources),
                            ("sharedassets0.assets.resS", fake.serialized_strings(["Texture label here"]))],
                           compression="lz4", block=cut)
    write("data.unity3d", unity3d)
    # StreamingAssets: an Addressables bundle (LZMA) and the text tables (English is read in full, long sentences
    # included; the other languages are not)
    aa = "StreamingAssets/aa/StandaloneWindows64/"
    dialogue = "Long ago, before the kingdom fell, " + "the river carried songs from village to village. " * 10
    write(aa + "items_assets_all.bundle", fake.bundle(
        [("CAB-items", fake.serialized_strings(["Iron Pickaxe", "A sturdy pickaxe for the mines"]))],
        compression="lzma"))
    write(aa + "localization-string-tables-english_assets_all.bundle",
           fake.table_bundles([("Dialoghi_en", "en", ["Welcome home, traveler!", dialogue])]))
    write(aa + "localization-string-tables-french_assets_all.bundle",
           fake.table_bundles([("Dialoghi_fr", "fr", ["Bienvenue chez toi, voyageur !"])]))
    write(aa + "rotto.bundle", b"UnityFS\x00non proprio")
    # a large file read in pieces: a text straddling two pieces
    old_piece = adapter_unity._PIECE
    adapter_unity._PIECE = 4096
    try:
        big = bytearray(b"\x00" * 4090)
        big += fake.serialized_strings(["Across the chunk boundary we go"], padding=b"")
        write("level0", bytes(big) + b"\x00" * 9000)
        lines = []
        texts = adapter_unity.extract_texts(game, adapter_unity.analyze(game), "en", lines.append)
    finally:
        adapter_unity._PIECE = old_piece
    expected_list = ["Harvest the golden wheat before winter", "Talk to the mayor", "Open the treasure chest",
              long_text, "Rest at the campfire", "Iron Pickaxe", "A sturdy pickaxe for the mines",
              "Welcome home, traveler!", "Across the chunk boundary we go"]
    check("pre-traduzione: codice del gioco in un suo modulo, data.unity3d compresso (testo a cavallo dei "
              "blocchi), bundle LZMA, tabella inglese (anche le frasi lunghe), file grande a pezzi",
              all(t in texts for t in expected_list) and dialogue in texts)
    check("pre-traduzione: niente librerie di terzi, immagini (.resS) o tabelle di altre lingue; bundle rovinato "
              "saltato",
              not any(t in texts for t in ("Unexpected end when reading the JSON document", "Texture label here",
                                           "Bienvenue chez toi, voyageur !")))
    check("pre-traduzione: prima la tabella dei testi, poi i file del gioco",
              texts.index("Welcome home, traveler!") < texts.index("Across the chunk boundary we go")
              < texts.index("Open the treasure chest"))
    check("pre-traduzione: il registro dice cosa ha letto",
              any("Unity Localization text tables" in r for r in lines)
              and any("Game bundles: read" in r for r in lines))
    # time limit used up: the bundles are left to the live translation, the rest is read
    old = adapter_unity.MAX_BUNDLE_SECONDS
    adapter_unity.MAX_BUNDLE_SECONDS = -1
    try:
        lines = []
        texts = adapter_unity.extract_texts(game, adapter_unity.analyze(game), "en", lines.append)
    finally:
        adapter_unity.MAX_BUNDLE_SECONDS = old
    check("pre-traduzione: con il tempo per i bundle finito, niente bundle (lo dice) ma il resto si'",
              "Open the treasure chest" not in texts and "Talk to the mayor" in texts
              and any("read 0 of" in r and "live translation" in r for r in lines))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    project.DATA_ROOT = os.path.join(base, "dati")  # the fake game's translation memory: never among the real data
    adapter_unity.is_running = lambda *_: False
    prepare_components(os.path.join(base, "componenti"))
    memory = adapter_unity._memory_folder(NAME)
    if os.path.exists(memory):
        shutil.rmtree(memory)

    game = os.path.join(base, "gioco")
    create_game(game)
    before = sorted(os.listdir(game))
    info = adapter_unity.analyze(game)
    check("analisi: mono x64 Unity 6000.3.12f1", (info["backend"], info["architettura"], info["versione_unity"])
              == ("mono", "x64", "6000.3.12f1"))

    # 1) installation with detected language
    lines = []
    adapter_unity.install(game, "auto", "it", NAME, lines.append)
    check("lingua rilevata: inglese", any("English (en)" in r for r in lines))
    check("file installati", all(os.path.exists(os.path.join(game, *p.split("/"))) for p in (
        "winhttp.dll", "doorstop_config.ini", "BepInEx/core/BepInEx.dll",
        "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll")))
    c = config(game)
    check("config: Google V2, it, en, 500, niente invio a blocchi", all(x in c for x in (
        "Endpoint=GoogleTranslateV2", "Language=it", "FromLanguage=en", "MaxCharactersPerTranslation=500",
        "EnableBatching=False")))
    with open(os.path.join(game, *adapter_unity.CONFIG_BEPINEX.split("/")), encoding="utf-8") as f:
        check("Unity 6: BepInEx parte al primo MonoBehaviour (niente crash all'avvio)",
                  "Type = MonoBehaviour" in f.read())
    m = adapter_unity.read_manifest(game)
    check("manifesto completo (changelog escluso)", m and not m["in_corso"] and len(m["file"]) == 8
              and m["radice_prima"] == before and not os.path.exists(os.path.join(game, "changelog.txt")))

    # 2) simulate the game's first start
    os.makedirs(os.path.join(game, "BepInEx", "Translation", "it", "Text"))
    open(os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt"), "w",
         encoding="utf-8").write("Start a new game=Inizia una nuova partita\nSettings and options=Impostazioni e opzioni\n")
    open(os.path.join(game, "BepInEx", "LogOutput.log"), "w", encoding="utf-8").write(
        "[Message:   BepInEx] Chainloader started\n[Info   :   BepInEx] Loading [XUnity Auto Translator 5.6.3.1]\n"
        "[Message:   BepInEx] Chainloader startup complete\n")
    open(os.path.join(game, "preloader_20261013.log"), "w").write("artefatto")
    open(os.path.join(game, *adapter_unity.SERVICE_STATE.split("/")), "w", encoding="utf-8").write(
        "# Scritto dal plugin di PatchSmith a ogni avvio del gioco\r\nEsito=python-mancante\r\n"
        "Data=2026-10-18 04:29:33\r\nProcesso=1234\r\nProgramma=C:\\Python311\\pythonw.exe\r\nDettaglio=\r\n")
    # XUnity rewrites the config with all the keys: simulate an extra key and a comment
    open(os.path.join(game, "BepInEx", "config", "AutoTranslatorConfig.ini"), "w", encoding="utf-8").write(
        "[Service]\nEndpoint=GoogleTranslateV2\nFallbackEndpoint=\n\n[General]\nLanguage=it\nFromLanguage=en\n"
        "\n[Behaviour]\nMaxCharactersPerTranslation=500\nEnableBatching=True\nHandleRichText=True\n")
    lines = []
    adapter_unity.state(game, NAME, "it", lines.append)
    check("stato: BepInEx e XUnity caricati, 2 frasi", any("OK  BepInEx has been launched" in r for r in lines)
              and any("OK  XUnity loaded" in r for r in lines)
              and any("Translated phrases so far: 2" in r for r in lines))
    check("stato: com'e' andato l'avvio del traduttore di PatchSmith (scritto dal plugin)",
              adapter_unity.read_service_state(game).get("Esito") == "python-mancante"
              and any("last game start: python-mancante (2026-10-18 04:29:33)" in r for r in lines))

    # 3) already installed: language change without losing the other keys
    adapter_unity.install(game, "en", "es", NAME, silent)
    c = config(game)
    check("riconfigura: Language=es, nostre chiavi imposte, altre conservate", "Language=es" in c
              and "HandleRichText=True" in c and "EnableBatching=False" in c and "EnableBatching=True" not in c
              and "FallbackEndpoint=" in c and c.count("[General]") == 1)
    adapter_unity.install(game, "en", "it", NAME, silent)

    # 3b) translation fixed by hand in the game: a new "Reapply" does not overwrite it
    alive = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    open(alive, "w", encoding="utf-8").write("Start a new game=Nuova partita (corretta a mano)\n")
    adapter_unity.install(game, "en", "it", NAME, silent)
    check("riapplica: correzione nel gioco conservata", "corretta a mano" in open(alive, encoding="utf-8").read())

    # 3c) file removed by the antivirus: it is put back, the rest is not touched
    os.remove(os.path.join(game, "winhttp.dll"))
    adapter_unity.install(game, "en", "it", NAME, silent)
    check("antivirus: file mancante rimesso, traduzioni intatte", os.path.exists(os.path.join(game, "winhttp.dll"))
              and "corretta a mano" in open(alive, encoding="utf-8").read())

    # 3d) mod added by the user inside BepInEx: it must not be deleted
    os.makedirs(os.path.join(game, "BepInEx", "plugins", "ModUtente"))
    open(os.path.join(game, "BepInEx", "plugins", "ModUtente", "ModUtente.dll"), "wb").write(b"mod")
    adapter_unity.uninstall(game, NAME, silent)
    mod = os.path.join(game, "BepInEx", "plugins", "ModUtente", "ModUtente.dll")
    remaining = [os.path.relpath(os.path.join(r, f), game)
                 for r, _, fs in os.walk(os.path.join(game, "BepInEx")) for f in fs]
    check("mod dell'utente lasciata, tutto il resto tolto", os.path.exists(mod) and len(remaining) == 1
              and not os.path.exists(os.path.join(game, "winhttp.dll")))
    shutil.rmtree(os.path.join(game, "BepInEx"))
    adapter_unity.install(game, "en", "it", NAME, silent)

    # 4) uninstall: translations saved, folder as it was
    adapter_unity.uninstall(game, NAME, silent)
    check("disinstalla: cartella identica a prima", sorted(os.listdir(game)) == before)
    saved_count = os.path.join(memory, "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    check("disinstalla: traduzioni salvate in memoria", os.path.exists(saved_count))

    # 5) reinstall: the translations come back
    adapter_unity.install(game, "en", "it", NAME, silent)
    returned = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    check("reinstalla: traduzioni rimesse", os.path.exists(returned)
              and "corretta a mano" in open(returned, encoding="utf-8").read())

    # 6) file in the root replaced by someone else: not deleted
    open(os.path.join(game, "doorstop_config.ini"), "w").write("modificato a mano")
    adapter_unity.uninstall(game, NAME, silent)
    check("file modificato da altri: lasciato", os.path.exists(os.path.join(game, "doorstop_config.ini"))
              and not os.path.exists(os.path.join(game, "BepInEx")))
    os.remove(os.path.join(game, "doorstop_config.ini"))

    # 7) another loader already present: no writing
    game2 = os.path.join(base, "gioco-con-mod")
    create_game(game2, conflict="winhttp.dll")
    before2 = sorted(os.listdir(game2))
    try:
        adapter_unity.install(game2, "en", "it", NAME, silent)
        check("conflitto: installazione rifiutata", False)
    except adapter_unity.UnityError:
        check("conflitto: installazione rifiutata", sorted(os.listdir(game2)) == before2)

    # 8) package with a malicious path: rejected before writing
    game3 = os.path.join(base, "gioco-zip-malevolo")
    create_game(game3)
    before3 = sorted(os.listdir(game3))
    good = components.CATALOG["xunity_bepinex5"]
    components.CATALOG["xunity_bepinex5"] = create_zip(os.path.join(base, "componenti", "cattivo.zip"),
                                                      {"../fuori.dll": b"x", "BepInEx/plugins/a.dll": b"y"})
    try:
        adapter_unity.install(game3, "en", "it", NAME, silent)
        check("zip malevolo: rifiutato", False)
    except adapter_unity.UnityError:
        check("zip malevolo: rifiutato senza scrivere", sorted(os.listdir(game3)) == before3
                  and not os.path.exists(os.path.join(base, "fuori.dll")))
    components.CATALOG["xunity_bepinex5"] = good

    # 9) interruption halfway: everything undone
    original = adapter_unity.configure

    def faulty(*_, **__):
        raise OSError("disco pieno (simulato)")

    adapter_unity.configure = faulty
    try:
        adapter_unity.install(game3, "en", "it", NAME, silent)
        check("interruzione: errore segnalato", False)
    except OSError:
        check("interruzione: tutto annullato", sorted(os.listdir(game3)) == before3)
    adapter_unity.configure = original

    # 9b) process dead halfway through the installation (no undo): "Restore" cleans everything up
    real_write, real_remove = adapter_unity._write_entry, adapter_unity._remove
    writes = []

    def write_then_die(folder, rel, entry):
        if len(writes) == 3:
            raise KeyboardInterrupt("processo chiuso (simulato)")
        writes.append(rel)
        real_write(folder, rel, entry)

    adapter_unity._write_entry = write_then_die
    adapter_unity._remove = lambda *a, **k: []  # simulates the death: no undo
    try:
        adapter_unity.install(game3, "en", "it", NAME, silent)
    except KeyboardInterrupt:
        pass
    adapter_unity._write_entry, adapter_unity._remove = real_write, real_remove
    m3 = adapter_unity.read_manifest(game3)
    check("interruzione dura: manifesto con l'elenco completo", m3 and m3["in_corso"] and len(m3["file"]) == 8)
    adapter_unity.uninstall(game3, NAME, silent)
    check("interruzione dura: ripristino pulisce tutto", sorted(os.listdir(game3)) == before3)

    # 9c) tampered manifest: nothing is deleted
    game4 = os.path.join(base, "gioco-manifesto-alterato")
    create_game(game4)
    forged = {"versione": 2, "patchsmith": True, "radice_prima": [], "cartelle_create": ["Gioco_Data"],
             "file": {"../fuori.txt": "0" * 64}, "in_corso": False}
    open(os.path.join(game4, adapter_unity.MANIFEST), "w").write(json.dumps(forged))
    try:
        adapter_unity.uninstall(game4, NAME, silent)
        check("manifesto alterato: rifiutato", False)
    except adapter_unity.UnityError:
        check("manifesto alterato: nulla cancellato", os.path.isdir(os.path.join(game4, "Gioco_Data")))

    # 9d) pre-translation: extraction from the assets (length + UTF-8) with the filters
    game5 = os.path.join(base, "gioco-pretraduzione")
    create_game(game5)

    def serialize(t):
        b = t.encode("utf-8")
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    content = b"".join(serialize(t) for t in [
        "Start a new game", "Back to Game", "Paper (17)", "EyeTreeLevel_Grass_0", "Base Layer.Run",
        "저택에 오신 것을 환영합니다.", "joystick 4 analog 1",
        "Tudo vai acabar bem, não se preocupe!", "Don't forget, you still have unfinished business.", "English"])
    open(os.path.join(game5, "Gioco_Data", "resources.assets"), "wb").write(b"\x07\x00" + content)
    texts = adapter_unity.extract_texts(game5, adapter_unity.analyze(game5), "en", silent)
    expected_list = {"Start a new game", "Back to Game", "Don't forget, you still have unfinished business.", "English"}
    check("pre-traduzione: testi giusti estratti, scarti esclusi", expected_list <= set(texts)
              and not any(x in texts for x in ("Paper (17)", "EyeTreeLevel_Grass_0", "Base Layer.Run",
                                               "joystick 4 analog 1")))

    # 9f) pre-translation in today's Unity games: code in other DLLs, compressed date.unity3d, bundles, tables
    test_bundle_extraction(os.path.join(base, "gioco-bundle"))

    # 9e) XUnity cache: adds only what is missing, keeps the fixes, does not translate the language names
    cache = os.path.join(game5, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    os.makedirs(os.path.dirname(cache))
    open(cache, "wb").write("﻿Back to Game=Torna al gioco (corretto)\nPortuguês - Brasil=Português - Brasile\n".encode("utf-8"))
    new_lines = adapter_unity.write_xunity_cache(game5, "it", {
        "Back to Game": "Ritorna al gioco", "Start a new game": "Inizia una nuova partita",
        "a=b\nc": "x=y\nz", "English": "Inglese"}, silent)
    loaded = open(cache, "rb").read()
    text = loaded.decode("utf-8-sig")
    check("cache: solo nuove aggiunte, correzione conservata", new_lines == 3 and "Torna al gioco (corretto)" in text
              and "Ritorna al gioco" not in text and "Start a new game=Inizia una nuova partita" in text)
    check("cache: escape corretti, BOM conservato", "a\\=b\\nc=x\\=y\\nz" in text
              and loaded.startswith(b"\xef\xbb\xbf"))
    check("cache: nomi delle lingue invariati", "English=English" in text
              and "Português - Brasil=Português - Brasil\n" in text)

    # 10) wrong fingerprint: the component is rejected
    import pathlib
    source = components.path("xunity_bepinex5")
    components.CATALOG["xunity_bepinex5"] = dict(good, sha256="0" * 64, url=pathlib.Path(source).as_uri())
    try:
        components.obtain("xunity_bepinex5", silent)
        check("impronta sbagliata: rifiutato", False)
    except components.ComponentError:
        check("impronta sbagliata: rifiutato", True)

    shutil.rmtree(memory, ignore_errors=True)
    test_live(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


def _fake_plugin(folder, info):
    """Instead of compiling with csc (the fake game's libraries are not real): a fake plugin."""
    dest = os.path.join(folder, *adapter_unity.SERVICE_PLUGIN.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "wb").write(b"MZ plugin finto")


def test_live(base):
    """Who translates the new sentences while playing, with Analyze and translate and with Reapply (automatic):
    always PatchSmith's translator if the plugin can be prepared; otherwise XUnity with Google, but never with Google
    paused."""
    from patchsmith import (auto as automatic, backend, outcome, limiter, ollama,
                            scan)
    prepare_components(os.path.join(base, "componenti-dal-vivo"))  # the earlier ones were rejected by test 10
    automatic.PROFILES_FOLDER = os.path.join(base, "profiles")
    real_state, real_compile, real_csc, real_create = (ollama.state, adapter_unity._compile_plugin, adapter_unity._csc,
                                                     automatic.create_backend)
    automatic.create_backend = lambda name, log=print: backend.Fake()  # no network: fake translations
    state = limiter.state_path()

    def unity(name, profile=None):
        game = os.path.join(base, "dal-vivo", name)
        create_game(game)
        path = os.path.join(automatic.PROFILES_FOLDER, name + ".json")
        scan.save_json(path, dict({"nome": name, "cartella_gioco": game.replace(os.sep, "/"),
                                             "motore": "unity", "lingua_origine": "en", "lingua_destinazione": "it",
                                             "backend": "google", "file": []}, **(profile or {})))
        return game, path

    def full(path, **arguments):
        d = {}
        automatic.unity_full(path, silent, outcome=d, **arguments)
        return d.get("esito")

    try:
        ollama.state = lambda: "pronto"
        adapter_unity._compile_plugin = _fake_plugin
        game, path = unity("con-servizio", {"backend": "xunity-google"})  # game profile from an earlier version
        e = full(path, backend="google")
        c = config(game)
        check("Google scelto, traduttore locale installato: frasi nuove al traduttore di PatchSmith "
                  "(CustomTranslate, niente anti-spam, testi lunghi), esito 'locale'",
                  "Endpoint=CustomTranslate" in c and "DisableSpamChecks=True" in c
                  and "MaxCharactersPerTranslation=2500" in c and e.live == "locale"
                  and adapter_unity.service_installed(game))
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        check("profilo: il traduttore scelto al posto di 'xunity-google'", stored.get("backend") == "google")
        check("finestra: 'Frasi nuove mentre giochi: PatchSmith, con il traduttore locale'",
                  "New phrases while playing: PatchSmith, with the local translator" in outcome.window(e)["testo"])
        automatic.restore(game, silent)
        check("ripristina: niente piu' XUnity ne' plugin", not adapter_unity.manifest_present(game))
        d = {}
        automatic.reapply(game, silent, outcome=d)
        check("riapplica dopo ripristina: di nuovo il traduttore di PatchSmith, non Google (U6)",
                  "Endpoint=CustomTranslate" in config(game) and adapter_unity.service_installed(game)
                  and d["esito"].live == "locale")

        ollama.state = lambda: "non installato"
        game, path = unity("senza-locale")
        e = full(path, backend="google")
        check("senza traduttore locale: comunque il traduttore di PatchSmith (con Google e le sue pause), "
                  "esito 'online'", "Endpoint=CustomTranslate" in config(game) and e.live == "online")
        limiter.Limiter("google", log=None).blocked(429, "prova")
        e = full(path, network=False)
        check("... con Google in pausa e niente altro: esito 'nessuno' con 'Installa il traduttore locale'",
                  "Endpoint=CustomTranslate" in config(game) and e.live == "nessuno"
                  and "installa_locale" in e.actions and e.extra.get("dal_vivo_nota") == "senza_traduttore")

        adapter_unity._compile_plugin = real_compile
        adapter_unity._csc = lambda: None  # the C# compiler is missing: the plugin cannot be prepared
        game, path = unity("senza-csc-in-pausa")
        e = full(path, backend="google")
        c = config(game)
        check("senza compilatore e con Google in pausa: nessun GoogleTranslateV2 (Endpoint vuoto), esito "
                  "'nessuno' con 'Crea rapporto'", "GoogleTranslateV2" not in c and "\nEndpoint=\n" in c
                  and e.live == "nessuno" and "crea_rapporto" in e.actions
                  and not adapter_unity.service_installed(game))
        game, path = unity("installa-in-pausa")
        adapter_unity.install(game, "en", "it", "installa-in-pausa", silent, local=path)
        check("installazione senza plugin con Google in pausa: XUnity senza traduttore, mai GoogleTranslateV2",
                  "GoogleTranslateV2" not in config(game) and "\nEndpoint=\n" in config(game))
        os.remove(state)
        game, path = unity("senza-csc")
        e = full(path, backend="google")
        check("senza compilatore, Google libero: XUnity con Google da solo, esito 'google' con la spiegazione",
                  "Endpoint=GoogleTranslateV2" in config(game) and e.live == "google"
                  and e.extra.get("dal_vivo_nota") == "plugin_google")

        class BlockGoogle(backend.Fake):
            """Translates, but meanwhile Google blocks the requests (like a 429 during the pre-translation)."""

            def translate_lines(self, lines, origin, dest_lang):
                limiter.Limiter("google", log=None).blocked(429, "prova")
                return super().translate_lines(lines, origin, dest_lang)

        automatic.create_backend = lambda name, log=print: BlockGoogle()
        game, path = unity("bloccato-durante")
        open(os.path.join(game, "Gioco_Data", "resources.assets"), "wb").write(b"\x07\x00" + b"".join(
            struct.pack("<i", len(t)) + t.encode("utf-8") for t in ("Start a new game", "Back to the main menu")))
        e = full(path, backend="google")
        check("Google bloccato durante la pre-traduzione: XUnity non lo usa per le frasi nuove (Endpoint vuoto)",
                  "GoogleTranslateV2" not in config(game) and "\nEndpoint=\n" in config(game)
                  and e.live == "nessuno" and e.translated_items == 2)
        if os.path.exists(state):
            os.remove(state)

        # the game's names in the game profile, from app.info (prepara)
        game = os.path.join(base, "dal-vivo", "con-nomi")
        create_game(game)
        with open(os.path.join(game, "Gioco_Data", "app.info"), "w", encoding="utf-8") as f:
            f.write("Cup Dog Games LLC\nRubinite")
        path, _ = automatic.prepare(game, "auto", "it", "google", silent)
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        check("prepara: i nomi del gioco (azienda, anche senza 'LLC', e titolo da app.info) nel profilo, da non "
                  "tradurre", stored.get("non_tradurre", [])[:3] == ["Cup Dog Games LLC", "Cup Dog Games", "Rubinite"])
        # a name removed by hand (a title one wants translated) does not come back; one added by hand stays
        stored["non_tradurre"] = [n for n in stored["non_tradurre"] if n != "Rubinite"] + ["Lanterna"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stored, f)
        automatic.prepare(game, "auto", "it", "google", silent)
        with open(path, encoding="utf-8") as f:
            stored = json.load(f)
        check("prepara: un nome tolto a mano da 'non_tradurre' non torna, uno aggiunto a mano resta",
                  "Rubinite" not in stored["non_tradurre"] and "Lanterna" in stored["non_tradurre"]
                  and "Cup Dog Games" in stored["non_tradurre"] and "Rubinite" in stored.get("nomi_trovati", []))

        test_live_cache(base, unity, full)
    finally:
        ollama.state, adapter_unity._compile_plugin, adapter_unity._csc = real_state, real_compile, real_csc
        automatic.create_backend = real_create
        if os.path.exists(state):
            os.remove(state)


def test_live_cache(base, unity, full):
    """The XUnity cache: wrong lines written by PatchSmith (translation memory or the live translator's journal) redone
    and replaced with "Analyze and translate"; those changed by hand never."""
    from patchsmith import auto as automatic, backend, xunity_service

    class DictTranslator(backend.Backend):
        name = "prova"

        def translate_lines(self, lines, origin, dest_lang):
            return [{"Open the door to the castle": "Apri la porta del castello",
                     "Collect [[0]] coins": "Raccogli [[0]] monete",
                     "Hold [[0]] to leap": "Tieni premuto [[0]] per saltare",
                     # if the line changed by hand were redone, it would get a good translation: this must not
                     # happen
                     "Talk to the guard and the king": "Parla con la guardia e il re"}.get(r, "IT " + r)
                    for r in lines]

    automatic.create_backend = lambda name, log=print: DictTranslator()
    game, path = unity("cache-da-correggere")
    full(path, backend="google")  # installation and pre-translation
    name = "cache-da-correggere"
    memory = project.memory_path({"nome": name, "lingua_destinazione": "it"})
    saved_count = {"Collect {{A}} coins": "Raccogli monete"}  # machine-written, but it lost the {{A}}
    os.makedirs(os.path.dirname(memory), exist_ok=True)
    for p in (memory, memory[:-5] + ".automatiche.json"):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(saved_count, f)
    with open(xunity_service.journal_path(name), "w", encoding="utf-8") as f:
        for text, journal_outcome in (("Open the door to the castle", "rifiutata"),
                                      ("Hold {{A}}s to leap", "rinviata")):
            f.write(json.dumps({"testo": text, "esito": journal_outcome, "risposta": text}) + "\n")
    cache = os.path.join(game, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    with open(cache, "a", encoding="utf-8") as f:
        f.write("Open the door to the castle=Open the door to the castle\nCollect {{A}} coins=Raccogli monete\n"
                "Hold {{A}}s to leap=Hold {{A}}s to leap\nTalk to the guard and the king=Parla con the guard and "
                "the king\nXbox Series=Xbox Series\n")
    flagged = adapter_unity.check_xunity_cache(adapter_unity.xunity_cache_entries(game, "it"), "en", "it")
    check("cache: segnalate le righe uguali all'originale con piu' parole, in un'altra lingua, senza un {{A}}; "
              "'Xbox Series' (un nome) no",
              set(flagged) == {"Open the door to the castle", "Collect {{A}} coins",
                                 "Talk to the guard and the king"}
              and "different placeholders" in flagged["Collect {{A}} coins"][1])
    e = full(path, backend="google")
    entries = adapter_unity.xunity_cache_entries(game, "it")
    check("cache: righe sbagliate scritte da PatchSmith o dal traduttore dal vivo (diario: rifiutata, rinviata) "
              "rifatte e sostituite",
              entries["Open the door to the castle"] == "Apri la porta del castello"
              and entries["Collect {{A}} coins"] == "Raccogli {{A}} monete"
              and entries["Hold {{A}}s to leap"] == "Tieni premuto {{A}}s per saltare"
              and e.extra.get("cache_corrette") == 3)
    check("cache: la riga cambiata a mano (anche se sembra sbagliata) e il nome restano come sono",
              entries["Talk to the guard and the king"] == "Parla con the guard and the king"
              and entries["Xbox Series"] == "Xbox Series")
    # writing: a line is replaced only if it still has the expected translation
    with open(cache, "a", encoding="utf-8") as f:
        f.write("Quit=Esci (a mano)\n")
    counts = {}
    adapter_unity.write_xunity_cache(game, "it", {"Quit": "Esci", "Talk to the guard and the king": "Parla con la "
                                                    "guardia e il re"}, silent,
                                      substitute={"Quit": "Quit", "Talk to the guard and the king": "Parla con the "
                                                   "guard and the king"}, counts=counts)
    entries = adapter_unity.xunity_cache_entries(game, "it")
    check("cache: una riga cambiata dopo il controllo non si sostituisce; quella attesa si'",
              entries["Quit"] == "Esci (a mano)"
              and entries["Talk to the guard and the king"] == "Parla con la guardia e il re"
              and counts == {"aggiunte": 0, "sostituite": 1})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
