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

from patchsmith import adapter_unity, components as componenti, project as progetto  # noqa: E402
import fake_unity as finto  # noqa: E402

ESITI = []
NOME = "prova-unity-test"


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def crea_gioco(cartella, conflitto=None):
    os.makedirs(os.path.join(cartella, "Gioco_Data", "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    open(os.path.join(cartella, "Gioco.exe"), "wb").write(bytes(pe))
    open(os.path.join(cartella, "UnityPlayer.dll"), "wb").write(b"finto")
    open(os.path.join(cartella, "Gioco_Data", "globalgamemanagers"), "wb").write(b"\x00" * 48 + b"6000.3.12f1\x00")
    frasi = ["Start a new game", "Welcome to the old mine. Collect all the gems!", "Press any key to continue",
             "The door is locked. Find the key first.", "Settings and options"]
    codice = b"".join(f.encode("utf-16-le") + b"\x00\x00" for f in frasi)
    open(os.path.join(cartella, "Gioco_Data", "Managed", "Assembly-CSharp.dll"), "wb").write(b"MZ" + codice)
    if conflitto:
        open(os.path.join(cartella, conflitto), "wb").write(b"altro loader")


def crea_zip(percorso, voci):
    with zipfile.ZipFile(percorso, "w") as z:
        for nome, contenuto in voci.items():
            z.writestr(nome, contenuto)
    dati = open(percorso, "rb").read()
    return {"url": "file://non-usato", "file": os.path.basename(percorso), "dimensione": len(dati),
            "sha256": hashlib.sha256(dati).hexdigest(), "versione": "prova", "licenza": "-"}


def prepara_componenti(cartella):
    componenti.CARTELLA = cartella
    os.makedirs(cartella, exist_ok=True)
    componenti.CATALOGO = {
        "bepinex5_x64": crea_zip(os.path.join(cartella, "bep.zip"), {
            "winhttp.dll": b"proxy", "doorstop_config.ini": b"[General]\r\nenabled=true\r\n",
            ".doorstop_version": b"4.5.0", "changelog.txt": b"log",
            "BepInEx/core/BepInEx.dll": b"core", "BepInEx/core/BepInEx.Preloader.dll": b"pre"}),
        "xunity_bepinex5": crea_zip(os.path.join(cartella, "xu.zip"), {
            "BepInEx/core/XUnity.Common.dll": b"common",
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin",
            "BepInEx/plugins/XUnity.AutoTranslator/Translators/GoogleTranslate.dll": b"google"}),
    }


def config(cartella):
    return open(os.path.join(cartella, "BepInEx", "config", "AutoTranslatorConfig.ini"), encoding="utf-8").read()


def prova_estrazione_bundle(gioco):
    """estrai_testi with the pieces of today's Unity games (fake bundles, see fake_unity.py)."""
    crea_gioco(gioco)
    dati = os.path.join(gioco, "Gioco_Data")
    managed = os.path.join(dati, "Managed")

    def scrivi(rel, byte):
        p = os.path.join(dati, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(byte)
    # game code in a module of its own (like SunHaven.Core.dll) and a third-party library (not game texts)
    scrivi("Managed/Gioco.Core.dll", finto.dll(["Harvest the golden wheat before winter", "Talk to the mayor"]))
    scrivi("Managed/Newtonsoft.Json.dll", finto.dll(["Unexpected end when reading the JSON document"]))
    # compressed data.unity3d (LZ4 in small blocks): a text straddling two blocks; not the images (.resS)
    lungo = "The ancient bridge collapsed behind you, there is no way back now"
    risorse = finto.stringhe_serializzate(["Open the treasure chest", lungo, "Rest at the campfire"])
    taglio = risorse.index(lungo.encode()) + 10
    unity3d = finto.bundle([("resources.assets", risorse),
                            ("sharedassets0.assets.resS", finto.stringhe_serializzate(["Texture label here"]))],
                           compressione="lz4", blocco=taglio)
    scrivi("data.unity3d", unity3d)
    # StreamingAssets: an Addressables bundle (LZMA) and the text tables (English is read in full, long sentences
    # included; the other languages are not)
    aa = "StreamingAssets/aa/StandaloneWindows64/"
    dialogo = "Long ago, before the kingdom fell, " + "the river carried songs from village to village. " * 10
    scrivi(aa + "items_assets_all.bundle", finto.bundle(
        [("CAB-items", finto.stringhe_serializzate(["Iron Pickaxe", "A sturdy pickaxe for the mines"]))],
        compressione="lzma"))
    scrivi(aa + "localization-string-tables-english_assets_all.bundle",
           finto.bundle_tabelle([("Dialoghi_en", "en", ["Welcome home, traveler!", dialogo])]))
    scrivi(aa + "localization-string-tables-french_assets_all.bundle",
           finto.bundle_tabelle([("Dialoghi_fr", "fr", ["Bienvenue chez toi, voyageur !"])]))
    scrivi(aa + "rotto.bundle", b"UnityFS\x00non proprio")
    # a large file read in pieces: a text straddling two pieces
    vecchio_pezzo = adapter_unity._PEZZO
    adapter_unity._PEZZO = 4096
    try:
        grande = bytearray(b"\x00" * 4090)
        grande += finto.stringhe_serializzate(["Across the chunk boundary we go"], riempimento=b"")
        scrivi("level0", bytes(grande) + b"\x00" * 9000)
        righe = []
        testi = adapter_unity.estrai_testi(gioco, adapter_unity.analizza(gioco), "en", righe.append)
    finally:
        adapter_unity._PEZZO = vecchio_pezzo
    attesi = ["Harvest the golden wheat before winter", "Talk to the mayor", "Open the treasure chest", lungo,
              "Rest at the campfire", "Iron Pickaxe", "A sturdy pickaxe for the mines", "Welcome home, traveler!",
              "Across the chunk boundary we go"]
    controlla("pre-traduzione: codice del gioco in un suo modulo, data.unity3d compresso (testo a cavallo dei "
              "blocchi), bundle LZMA, tabella inglese (anche le frasi lunghe), file grande a pezzi",
              all(t in testi for t in attesi) and dialogo in testi)
    controlla("pre-traduzione: niente librerie di terzi, immagini (.resS) o tabelle di altre lingue; bundle rovinato "
              "saltato",
              not any(t in testi for t in ("Unexpected end when reading the JSON document", "Texture label here",
                                           "Bienvenue chez toi, voyageur !")))
    controlla("pre-traduzione: prima la tabella dei testi, poi i file del gioco",
              testi.index("Welcome home, traveler!") < testi.index("Across the chunk boundary we go")
              < testi.index("Open the treasure chest"))
    controlla("pre-traduzione: il registro dice cosa ha letto",
              any("Tabelle dei testi di Unity Localization" in r for r in righe)
              and any("Bundle del gioco: letti" in r for r in righe))
    # time limit used up: the bundles are left to the live translation, the rest is read
    vecchio = adapter_unity.MAX_SECONDI_BUNDLE
    adapter_unity.MAX_SECONDI_BUNDLE = -1
    try:
        righe = []
        testi = adapter_unity.estrai_testi(gioco, adapter_unity.analizza(gioco), "en", righe.append)
    finally:
        adapter_unity.MAX_SECONDI_BUNDLE = vecchio
    controlla("pre-traduzione: con il tempo per i bundle finito, niente bundle (lo dice) ma il resto si'",
              "Open the treasure chest" not in testi and "Talk to the mayor" in testi
              and any("letti 0 su" in r and "dal vivo" in r for r in righe))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    progetto.RADICE_DATI = os.path.join(base, "dati")  # the fake game's translation memory: never among the real data
    adapter_unity.in_esecuzione = lambda *_: False
    prepara_componenti(os.path.join(base, "componenti"))
    memoria = adapter_unity._cartella_memoria(NOME)
    if os.path.exists(memoria):
        shutil.rmtree(memoria)

    gioco = os.path.join(base, "gioco")
    crea_gioco(gioco)
    prima = sorted(os.listdir(gioco))
    info = adapter_unity.analizza(gioco)
    controlla("analisi: mono x64 Unity 6000.3.12f1", (info["backend"], info["architettura"], info["versione_unity"])
              == ("mono", "x64", "6000.3.12f1"))

    # 1) installation with detected language
    righe = []
    adapter_unity.installa(gioco, "auto", "it", NOME, righe.append)
    controlla("lingua rilevata: inglese", any("Inglese (en)" in r for r in righe))
    controlla("file installati", all(os.path.exists(os.path.join(gioco, *p.split("/"))) for p in (
        "winhttp.dll", "doorstop_config.ini", "BepInEx/core/BepInEx.dll",
        "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll")))
    c = config(gioco)
    controlla("config: Google V2, it, en, 500, niente invio a blocchi", all(x in c for x in (
        "Endpoint=GoogleTranslateV2", "Language=it", "FromLanguage=en", "MaxCharactersPerTranslation=500",
        "EnableBatching=False")))
    with open(os.path.join(gioco, *adapter_unity.CONFIG_BEPINEX.split("/")), encoding="utf-8") as f:
        controlla("Unity 6: BepInEx parte al primo MonoBehaviour (niente crash all'avvio)",
                  "Type = MonoBehaviour" in f.read())
    m = adapter_unity.leggi_manifesto(gioco)
    controlla("manifesto completo (changelog escluso)", m and not m["in_corso"] and len(m["file"]) == 8
              and m["radice_prima"] == prima and not os.path.exists(os.path.join(gioco, "changelog.txt")))

    # 2) simulate the game's first start
    os.makedirs(os.path.join(gioco, "BepInEx", "Translation", "it", "Text"))
    open(os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt"), "w",
         encoding="utf-8").write("Start a new game=Inizia una nuova partita\nSettings and options=Impostazioni e opzioni\n")
    open(os.path.join(gioco, "BepInEx", "LogOutput.log"), "w", encoding="utf-8").write(
        "[Message:   BepInEx] Chainloader started\n[Info   :   BepInEx] Loading [XUnity Auto Translator 5.6.3.1]\n"
        "[Message:   BepInEx] Chainloader startup complete\n")
    open(os.path.join(gioco, "preloader_20261013.log"), "w").write("artefatto")
    open(os.path.join(gioco, *adapter_unity.STATO_SERVIZIO.split("/")), "w", encoding="utf-8").write(
        "# Scritto dal plugin di PatchSmith a ogni avvio del gioco\r\nEsito=python-mancante\r\n"
        "Data=2026-10-18 04:29:33\r\nProcesso=1234\r\nProgramma=C:\\Python311\\pythonw.exe\r\nDettaglio=\r\n")
    # XUnity rewrites the config with all the keys: simulate an extra key and a comment
    open(os.path.join(gioco, "BepInEx", "config", "AutoTranslatorConfig.ini"), "w", encoding="utf-8").write(
        "[Service]\nEndpoint=GoogleTranslateV2\nFallbackEndpoint=\n\n[General]\nLanguage=it\nFromLanguage=en\n"
        "\n[Behaviour]\nMaxCharactersPerTranslation=500\nEnableBatching=True\nHandleRichText=True\n")
    righe = []
    adapter_unity.stato(gioco, NOME, "it", righe.append)
    controlla("stato: BepInEx e XUnity caricati, 2 frasi", any("OK  BepInEx avviato" in r for r in righe)
              and any("OK  XUnity caricato" in r for r in righe) and any("tradotte finora: 2" in r for r in righe))
    controlla("stato: com'e' andato l'avvio del traduttore di PatchSmith (scritto dal plugin)",
              adapter_unity.leggi_stato_servizio(gioco).get("Esito") == "python-mancante"
              and any("ultimo avvio del gioco: python-mancante (2026-10-18 04:29:33)" in r for r in righe))

    # 3) already installed: language change without losing the other keys
    adapter_unity.installa(gioco, "en", "es", NOME, silenzio)
    c = config(gioco)
    controlla("riconfigura: Language=es, nostre chiavi imposte, altre conservate", "Language=es" in c
              and "HandleRichText=True" in c and "EnableBatching=False" in c and "EnableBatching=True" not in c
              and "FallbackEndpoint=" in c and c.count("[General]") == 1)
    adapter_unity.installa(gioco, "en", "it", NOME, silenzio)

    # 3b) translation fixed by hand in the game: a new "Reapply" does not overwrite it
    vivo = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    open(vivo, "w", encoding="utf-8").write("Start a new game=Nuova partita (corretta a mano)\n")
    adapter_unity.installa(gioco, "en", "it", NOME, silenzio)
    controlla("riapplica: correzione nel gioco conservata", "corretta a mano" in open(vivo, encoding="utf-8").read())

    # 3c) file removed by the antivirus: it is put back, the rest is not touched
    os.remove(os.path.join(gioco, "winhttp.dll"))
    adapter_unity.installa(gioco, "en", "it", NOME, silenzio)
    controlla("antivirus: file mancante rimesso, traduzioni intatte", os.path.exists(os.path.join(gioco, "winhttp.dll"))
              and "corretta a mano" in open(vivo, encoding="utf-8").read())

    # 3d) mod added by the user inside BepInEx: it must not be deleted
    os.makedirs(os.path.join(gioco, "BepInEx", "plugins", "ModUtente"))
    open(os.path.join(gioco, "BepInEx", "plugins", "ModUtente", "ModUtente.dll"), "wb").write(b"mod")
    adapter_unity.disinstalla(gioco, NOME, silenzio)
    mod = os.path.join(gioco, "BepInEx", "plugins", "ModUtente", "ModUtente.dll")
    rimasti = [os.path.relpath(os.path.join(r, f), gioco) for r, _, fs in os.walk(os.path.join(gioco, "BepInEx")) for f in fs]
    controlla("mod dell'utente lasciata, tutto il resto tolto", os.path.exists(mod) and len(rimasti) == 1
              and not os.path.exists(os.path.join(gioco, "winhttp.dll")))
    shutil.rmtree(os.path.join(gioco, "BepInEx"))
    adapter_unity.installa(gioco, "en", "it", NOME, silenzio)

    # 4) uninstall: translations saved, folder as it was
    adapter_unity.disinstalla(gioco, NOME, silenzio)
    controlla("disinstalla: cartella identica a prima", sorted(os.listdir(gioco)) == prima)
    salvate = os.path.join(memoria, "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    controlla("disinstalla: traduzioni salvate in memoria", os.path.exists(salvate))

    # 5) reinstall: the translations come back
    adapter_unity.installa(gioco, "en", "it", NOME, silenzio)
    tornate = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    controlla("reinstalla: traduzioni rimesse", os.path.exists(tornate) and "corretta a mano" in open(tornate, encoding="utf-8").read())

    # 6) file in the root replaced by someone else: not deleted
    open(os.path.join(gioco, "doorstop_config.ini"), "w").write("modificato a mano")
    adapter_unity.disinstalla(gioco, NOME, silenzio)
    controlla("file modificato da altri: lasciato", os.path.exists(os.path.join(gioco, "doorstop_config.ini"))
              and not os.path.exists(os.path.join(gioco, "BepInEx")))
    os.remove(os.path.join(gioco, "doorstop_config.ini"))

    # 7) another loader already present: no writing
    gioco2 = os.path.join(base, "gioco-con-mod")
    crea_gioco(gioco2, conflitto="winhttp.dll")
    prima2 = sorted(os.listdir(gioco2))
    try:
        adapter_unity.installa(gioco2, "en", "it", NOME, silenzio)
        controlla("conflitto: installazione rifiutata", False)
    except adapter_unity.ErroreUnity:
        controlla("conflitto: installazione rifiutata", sorted(os.listdir(gioco2)) == prima2)

    # 8) package with a malicious path: rejected before writing
    gioco3 = os.path.join(base, "gioco-zip-malevolo")
    crea_gioco(gioco3)
    prima3 = sorted(os.listdir(gioco3))
    buono = componenti.CATALOGO["xunity_bepinex5"]
    componenti.CATALOGO["xunity_bepinex5"] = crea_zip(os.path.join(base, "componenti", "cattivo.zip"),
                                                      {"../fuori.dll": b"x", "BepInEx/plugins/a.dll": b"y"})
    try:
        adapter_unity.installa(gioco3, "en", "it", NOME, silenzio)
        controlla("zip malevolo: rifiutato", False)
    except adapter_unity.ErroreUnity:
        controlla("zip malevolo: rifiutato senza scrivere", sorted(os.listdir(gioco3)) == prima3
                  and not os.path.exists(os.path.join(base, "fuori.dll")))
    componenti.CATALOGO["xunity_bepinex5"] = buono

    # 9) interruption halfway: everything undone
    originale = adapter_unity.configura

    def guasto(*_, **__):
        raise OSError("disco pieno (simulato)")

    adapter_unity.configura = guasto
    try:
        adapter_unity.installa(gioco3, "en", "it", NOME, silenzio)
        controlla("interruzione: errore segnalato", False)
    except OSError:
        controlla("interruzione: tutto annullato", sorted(os.listdir(gioco3)) == prima3)
    adapter_unity.configura = originale

    # 9b) process dead halfway through the installation (no undo): "Restore" cleans everything up
    vera_scrivi, vera_rimuovi = adapter_unity._scrivi_voce, adapter_unity._rimuovi
    scritti = []

    def scrivi_poi_muori(cartella, rel, voce):
        if len(scritti) == 3:
            raise KeyboardInterrupt("processo chiuso (simulato)")
        scritti.append(rel)
        vera_scrivi(cartella, rel, voce)

    adapter_unity._scrivi_voce = scrivi_poi_muori
    adapter_unity._rimuovi = lambda *a, **k: []  # simulates the death: no undo
    try:
        adapter_unity.installa(gioco3, "en", "it", NOME, silenzio)
    except KeyboardInterrupt:
        pass
    adapter_unity._scrivi_voce, adapter_unity._rimuovi = vera_scrivi, vera_rimuovi
    m3 = adapter_unity.leggi_manifesto(gioco3)
    controlla("interruzione dura: manifesto con l'elenco completo", m3 and m3["in_corso"] and len(m3["file"]) == 8)
    adapter_unity.disinstalla(gioco3, NOME, silenzio)
    controlla("interruzione dura: ripristino pulisce tutto", sorted(os.listdir(gioco3)) == prima3)

    # 9c) tampered manifest: nothing is deleted
    gioco4 = os.path.join(base, "gioco-manifesto-alterato")
    crea_gioco(gioco4)
    falso = {"versione": 2, "patchsmith": True, "radice_prima": [], "cartelle_create": ["Gioco_Data"],
             "file": {"../fuori.txt": "0" * 64}, "in_corso": False}
    open(os.path.join(gioco4, adapter_unity.MANIFESTO), "w").write(json.dumps(falso))
    try:
        adapter_unity.disinstalla(gioco4, NOME, silenzio)
        controlla("manifesto alterato: rifiutato", False)
    except adapter_unity.ErroreUnity:
        controlla("manifesto alterato: nulla cancellato", os.path.isdir(os.path.join(gioco4, "Gioco_Data")))

    # 9d) pre-translation: extraction from the assets (length + UTF-8) with the filters
    gioco5 = os.path.join(base, "gioco-pretraduzione")
    crea_gioco(gioco5)

    def serializza(t):
        b = t.encode("utf-8")
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    contenuto = b"".join(serializza(t) for t in [
        "Start a new game", "Back to Game", "Paper (17)", "EyeTreeLevel_Grass_0", "Base Layer.Run",
        "저택에 오신 것을 환영합니다.", "joystick 4 analog 1",
        "Tudo vai acabar bem, não se preocupe!", "Don't forget, you still have unfinished business.", "English"])
    open(os.path.join(gioco5, "Gioco_Data", "resources.assets"), "wb").write(b"\x07\x00" + contenuto)
    testi = adapter_unity.estrai_testi(gioco5, adapter_unity.analizza(gioco5), "en", silenzio)
    attesi = {"Start a new game", "Back to Game", "Don't forget, you still have unfinished business.", "English"}
    controlla("pre-traduzione: testi giusti estratti, scarti esclusi", attesi <= set(testi)
              and not any(x in testi for x in ("Paper (17)", "EyeTreeLevel_Grass_0", "Base Layer.Run", "joystick 4 analog 1")))

    # 9f) pre-translation in today's Unity games: code in other DLLs, compressed data.unity3d, bundles, tables
    prova_estrazione_bundle(os.path.join(base, "gioco-bundle"))

    # 9e) XUnity cache: adds only what is missing, keeps the fixes, does not translate the language names
    cache = os.path.join(gioco5, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    os.makedirs(os.path.dirname(cache))
    open(cache, "wb").write("﻿Back to Game=Torna al gioco (corretto)\nPortuguês - Brasil=Português - Brasile\n".encode("utf-8"))
    nuove = adapter_unity.scrivi_cache_xunity(gioco5, "it", {
        "Back to Game": "Ritorna al gioco", "Start a new game": "Inizia una nuova partita",
        "a=b\nc": "x=y\nz", "English": "Inglese"}, silenzio)
    letta = open(cache, "rb").read()
    testo = letta.decode("utf-8-sig")
    controlla("cache: solo nuove aggiunte, correzione conservata", nuove == 3 and "Torna al gioco (corretto)" in testo
              and "Ritorna al gioco" not in testo and "Start a new game=Inizia una nuova partita" in testo)
    controlla("cache: escape corretti, BOM conservato", "a\\=b\\nc=x\\=y\\nz" in testo and letta.startswith(b"\xef\xbb\xbf"))
    controlla("cache: nomi delle lingue invariati", "English=English" in testo
              and "Português - Brasil=Português - Brasil\n" in testo)

    # 10) wrong fingerprint: the component is rejected
    import pathlib
    sorgente = componenti.percorso("xunity_bepinex5")
    componenti.CATALOGO["xunity_bepinex5"] = dict(buono, sha256="0" * 64, url=pathlib.Path(sorgente).as_uri())
    try:
        componenti.ottieni("xunity_bepinex5", silenzio)
        controlla("impronta sbagliata: rifiutato", False)
    except componenti.ErroreComponente:
        controlla("impronta sbagliata: rifiutato", True)

    shutil.rmtree(memoria, ignore_errors=True)
    prova_dal_vivo(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


def _plugin_finto(cartella, info):
    """Instead of compiling with csc (the fake game's libraries are not real): a fake plugin."""
    dest = os.path.join(cartella, *adapter_unity.PLUGIN_SERVIZIO.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    open(dest, "wb").write(b"MZ plugin finto")


def prova_dal_vivo(base):
    """Who translates the new sentences while playing, with Analyze and translate and with Reapply (automatic):
    always PatchSmith's translator if the plugin can be prepared; otherwise XUnity with Google, but never with Google
    paused."""
    from patchsmith import (auto as automatico, backend, outcome as esito, limiter as limitatore, ollama,
                            scan as scansione)
    prepara_componenti(os.path.join(base, "componenti-dal-vivo"))  # the earlier ones were rejected by test 10
    automatico.CARTELLA_PROFILI = os.path.join(base, "profiles")
    vero_stato, vera_compila, vero_csc, vero_crea = (ollama.stato, adapter_unity._compila_plugin, adapter_unity._csc,
                                                     automatico.crea_backend)
    automatico.crea_backend = lambda nome, log=print: backend.Finto()  # no network: fake translations
    stato = limitatore.percorso_stato()

    def unity(nome, profilo=None):
        gioco = os.path.join(base, "dal-vivo", nome)
        crea_gioco(gioco)
        percorso = os.path.join(automatico.CARTELLA_PROFILI, nome + ".json")
        scansione.salva_json(percorso, dict({"nome": nome, "cartella_gioco": gioco.replace(os.sep, "/"),
                                             "motore": "unity", "lingua_origine": "en", "lingua_destinazione": "it",
                                             "backend": "google", "file": []}, **(profilo or {})))
        return gioco, percorso

    def completo(percorso, **argomenti):
        d = {}
        automatico.unity_completo(percorso, silenzio, esito=d, **argomenti)
        return d.get("esito")

    try:
        ollama.stato = lambda: "pronto"
        adapter_unity._compila_plugin = _plugin_finto
        gioco, percorso = unity("con-servizio", {"backend": "xunity-google"})  # game profile from an earlier version
        e = completo(percorso, backend="google")
        c = config(gioco)
        controlla("Google scelto, traduttore locale installato: frasi nuove al traduttore di PatchSmith "
                  "(CustomTranslate, niente anti-spam, testi lunghi), esito 'locale'",
                  "Endpoint=CustomTranslate" in c and "DisableSpamChecks=True" in c
                  and "MaxCharactersPerTranslation=2500" in c and e.dal_vivo == "locale"
                  and adapter_unity.servizio_attivo(gioco))
        with open(percorso, encoding="utf-8") as f:
            salvato = json.load(f)
        controlla("profilo: il traduttore scelto al posto di 'xunity-google'", salvato.get("backend") == "google")
        controlla("finestra: 'Frasi nuove mentre giochi: PatchSmith, con il traduttore locale'",
                  "Frasi nuove mentre giochi: PatchSmith, con il traduttore locale" in esito.finestra(e)["testo"])
        automatico.ripristina(gioco, silenzio)
        controlla("ripristina: niente piu' XUnity ne' plugin", not adapter_unity.manifesto_presente(gioco))
        d = {}
        automatico.riapplica(gioco, silenzio, esito=d)
        controlla("riapplica dopo ripristina: di nuovo il traduttore di PatchSmith, non Google (U6)",
                  "Endpoint=CustomTranslate" in config(gioco) and adapter_unity.servizio_attivo(gioco)
                  and d["esito"].dal_vivo == "locale")

        ollama.stato = lambda: "non installato"
        gioco, percorso = unity("senza-locale")
        e = completo(percorso, backend="google")
        controlla("senza traduttore locale: comunque il traduttore di PatchSmith (con Google e le sue pause), "
                  "esito 'online'", "Endpoint=CustomTranslate" in config(gioco) and e.dal_vivo == "online")
        limitatore.Limitatore("google", log=None).bloccato(429, "prova")
        e = completo(percorso, rete=False)
        controlla("... con Google in pausa e niente altro: esito 'nessuno' con 'Installa il traduttore locale'",
                  "Endpoint=CustomTranslate" in config(gioco) and e.dal_vivo == "nessuno"
                  and "installa_locale" in e.azioni and e.extra.get("dal_vivo_nota") == "senza_traduttore")

        adapter_unity._compila_plugin = vera_compila
        adapter_unity._csc = lambda: None  # the C# compiler is missing: the plugin cannot be prepared
        gioco, percorso = unity("senza-csc-in-pausa")
        e = completo(percorso, backend="google")
        c = config(gioco)
        controlla("senza compilatore e con Google in pausa: nessun GoogleTranslateV2 (Endpoint vuoto), esito "
                  "'nessuno' con 'Crea rapporto'", "GoogleTranslateV2" not in c and "\nEndpoint=\n" in c
                  and e.dal_vivo == "nessuno" and "crea_rapporto" in e.azioni
                  and not adapter_unity.servizio_attivo(gioco))
        gioco, percorso = unity("installa-in-pausa")
        adapter_unity.installa(gioco, "en", "it", "installa-in-pausa", silenzio, locale=percorso)
        controlla("installazione senza plugin con Google in pausa: XUnity senza traduttore, mai GoogleTranslateV2",
                  "GoogleTranslateV2" not in config(gioco) and "\nEndpoint=\n" in config(gioco))
        os.remove(stato)
        gioco, percorso = unity("senza-csc")
        e = completo(percorso, backend="google")
        controlla("senza compilatore, Google libero: XUnity con Google da solo, esito 'google' con la spiegazione",
                  "Endpoint=GoogleTranslateV2" in config(gioco) and e.dal_vivo == "google"
                  and e.extra.get("dal_vivo_nota") == "plugin_google")

        class BloccaGoogle(backend.Finto):
            """Translates, but meanwhile Google blocks the requests (like a 429 during the pre-translation)."""

            def traduci_righe(self, righe, origine, destinazione):
                limitatore.Limitatore("google", log=None).bloccato(429, "prova")
                return super().traduci_righe(righe, origine, destinazione)

        automatico.crea_backend = lambda nome, log=print: BloccaGoogle()
        gioco, percorso = unity("bloccato-durante")
        open(os.path.join(gioco, "Gioco_Data", "resources.assets"), "wb").write(b"\x07\x00" + b"".join(
            struct.pack("<i", len(t)) + t.encode("utf-8") for t in ("Start a new game", "Back to the main menu")))
        e = completo(percorso, backend="google")
        controlla("Google bloccato durante la pre-traduzione: XUnity non lo usa per le frasi nuove (Endpoint vuoto)",
                  "GoogleTranslateV2" not in config(gioco) and "\nEndpoint=\n" in config(gioco)
                  and e.dal_vivo == "nessuno" and e.tradotti == 2)
        if os.path.exists(stato):
            os.remove(stato)

        # the game's names in the game profile, from app.info (prepara)
        gioco = os.path.join(base, "dal-vivo", "con-nomi")
        crea_gioco(gioco)
        with open(os.path.join(gioco, "Gioco_Data", "app.info"), "w", encoding="utf-8") as f:
            f.write("Cup Dog Games LLC\nRubinite")
        percorso, _ = automatico.prepara(gioco, "auto", "it", "google", silenzio)
        with open(percorso, encoding="utf-8") as f:
            salvato = json.load(f)
        controlla("prepara: i nomi del gioco (azienda, anche senza 'LLC', e titolo da app.info) nel profilo, da non "
                  "tradurre", salvato.get("non_tradurre", [])[:3] == ["Cup Dog Games LLC", "Cup Dog Games", "Rubinite"])
        # a name removed by hand (a title one wants translated) does not come back; one added by hand stays
        salvato["non_tradurre"] = [n for n in salvato["non_tradurre"] if n != "Rubinite"] + ["Lanterna"]
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(salvato, f)
        automatico.prepara(gioco, "auto", "it", "google", silenzio)
        with open(percorso, encoding="utf-8") as f:
            salvato = json.load(f)
        controlla("prepara: un nome tolto a mano da 'non_tradurre' non torna, uno aggiunto a mano resta",
                  "Rubinite" not in salvato["non_tradurre"] and "Lanterna" in salvato["non_tradurre"]
                  and "Cup Dog Games" in salvato["non_tradurre"] and "Rubinite" in salvato.get("nomi_trovati", []))

        prova_cache_dal_vivo(base, unity, completo)
    finally:
        ollama.stato, adapter_unity._compila_plugin, adapter_unity._csc = vero_stato, vera_compila, vero_csc
        automatico.crea_backend = vero_crea
        if os.path.exists(stato):
            os.remove(stato)


def prova_cache_dal_vivo(base, unity, completo):
    """The XUnity cache: wrong lines written by PatchSmith (translation memory or the live translator's journal) redone
    and replaced with "Analyze and translate"; those changed by hand never."""
    from patchsmith import auto as automatico, backend, xunity_service as servizio_xunity

    class Traduce(backend.Backend):
        nome = "prova"

        def traduci_righe(self, righe, origine, destinazione):
            return [{"Open the door to the castle": "Apri la porta del castello",
                     "Collect [[0]] coins": "Raccogli [[0]] monete",
                     "Hold [[0]] to leap": "Tieni premuto [[0]] per saltare",
                     # if the line changed by hand were redone, it would get a good translation: this must not
                     # happen
                     "Talk to the guard and the king": "Parla con la guardia e il re"}.get(r, "IT " + r)
                    for r in righe]

    automatico.crea_backend = lambda nome, log=print: Traduce()
    gioco, percorso = unity("cache-da-correggere")
    completo(percorso, backend="google")  # installation and pre-translation
    nome = "cache-da-correggere"
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    salvate = {"Collect {{A}} coins": "Raccogli monete"}  # machine-written, but it lost the {{A}}
    os.makedirs(os.path.dirname(memoria), exist_ok=True)
    for p in (memoria, memoria[:-5] + ".automatiche.json"):
        with open(p, "w", encoding="utf-8") as f:
            json.dump(salvate, f)
    with open(servizio_xunity.percorso_diario(nome), "w", encoding="utf-8") as f:
        for testo, esito_diario in (("Open the door to the castle", "rifiutata"), ("Hold {{A}}s to leap", "rinviata")):
            f.write(json.dumps({"testo": testo, "esito": esito_diario, "risposta": testo}) + "\n")
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    with open(cache, "a", encoding="utf-8") as f:
        f.write("Open the door to the castle=Open the door to the castle\nCollect {{A}} coins=Raccogli monete\n"
                "Hold {{A}}s to leap=Hold {{A}}s to leap\nTalk to the guard and the king=Parla con the guard and "
                "the king\nXbox Series=Xbox Series\n")
    segnalate = adapter_unity.controlla_cache_xunity(adapter_unity.voci_cache_xunity(gioco, "it"), "en", "it")
    controlla("cache: segnalate le righe uguali all'originale con piu' parole, in un'altra lingua, senza un {{A}}; "
              "'Xbox Series' (un nome) no",
              set(segnalate) == {"Open the door to the castle", "Collect {{A}} coins",
                                 "Talk to the guard and the king"}
              and "segnaposto diversi" in segnalate["Collect {{A}} coins"][1])
    e = completo(percorso, backend="google")
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    controlla("cache: righe sbagliate scritte da PatchSmith o dal traduttore dal vivo (diario: rifiutata, rinviata) "
              "rifatte e sostituite",
              voci["Open the door to the castle"] == "Apri la porta del castello"
              and voci["Collect {{A}} coins"] == "Raccogli {{A}} monete"
              and voci["Hold {{A}}s to leap"] == "Tieni premuto {{A}}s per saltare"
              and e.extra.get("cache_corrette") == 3)
    controlla("cache: la riga cambiata a mano (anche se sembra sbagliata) e il nome restano come sono",
              voci["Talk to the guard and the king"] == "Parla con the guard and the king"
              and voci["Xbox Series"] == "Xbox Series")
    # writing: a line is replaced only if it still has the expected translation
    with open(cache, "a", encoding="utf-8") as f:
        f.write("Quit=Esci (a mano)\n")
    conteggi = {}
    adapter_unity.scrivi_cache_xunity(gioco, "it", {"Quit": "Esci", "Talk to the guard and the king": "Parla con la "
                                                    "guardia e il re"}, silenzio,
                                      sostituisci={"Quit": "Quit", "Talk to the guard and the king": "Parla con the "
                                                   "guard and the king"}, conteggi=conteggi)
    voci = adapter_unity.voci_cache_xunity(gioco, "it")
    controlla("cache: una riga cambiata dopo il controllo non si sostituisce; quella attesa si'",
              voci["Quit"] == "Esci (a mano)"
              and voci["Talk to the guard and the king"] == "Parla con la guardia e il re"
              and conteggi == {"aggiunte": 0, "sostituite": 1})


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
