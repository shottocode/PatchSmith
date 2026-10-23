"""Test di regressione della lingua ufficiale: i problemi trovati dalla revisione della 0.8.0.

Uso: python -I test/test_language_regressions.py <cartella_temporanea_vuota>
Scrive solo nella cartella temporanea e in una chiave di registro di prova (HKCU\\Software\\PatchsmithTest2),
che alla fine viene cancellata. Steam e i giochi veri non vengono toccati.
"""
import hashlib
import os
import stat
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (auto as automatico, cli, language as lingua, official_language as lingua_ufficiale,
                        language_unity as lingua_unity, language_unreal as lingua_unreal)  # noqa: E402
from patchsmith import project as progetto, system as sistema, steam  # noqa: E402
import test_gamemaker  # noqa: E402
import test_unreal  # noqa: E402
from test_official_language import appinfo  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def scrivi(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)


def leggi(percorso):
    with open(percorso, "rb") as f:
        return f.read()


# --- pak con punto di montaggio ---------------------------------------------------------------------

def pak_v9(mount, file):
    """Pak v9 non compresso con un punto di montaggio a scelta (i nomi sono relativi a quello)."""
    corpo, voci = b"", []
    for nome, dati in file:
        h = hashlib.sha1(dati).digest()
        voce = lambda off, d=dati, h=h: struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00" + struct.pack("<I", 0)
        voci.append((nome, len(corpo), voce))
        corpo += voce(0) + dati
    indice = test_unreal.fs(mount) + struct.pack("<i", len(voci)) + b"".join(test_unreal.fs(n) + v(o)
                                                                            for n, o, v in voci)
    coda = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(corpo), len(indice))
            + hashlib.sha1(indice).digest() + b"\x00" + b"\x00" * 160)
    return corpo + indice + coda


def pak_v11(mount, nomi):
    """Pak v11 (UE 4.26+/UE5) con solo l'indice delle cartelle (FullDirectoryIndex), senza dati."""
    cartelle = {}
    for n in nomi:
        c, _, f = n.rpartition("/")
        cartelle.setdefault((c + "/") if c else "/", []).append(f)
    fdi = struct.pack("<i", len(cartelle))
    for c, files in cartelle.items():
        fdi += test_unreal.fs(c) + struct.pack("<i", len(files))
        fdi += b"".join(test_unreal.fs(f) + struct.pack("<i", k) for k, f in enumerate(files))
    off_indice = len(fdi)
    indice = (test_unreal.fs(mount) + struct.pack("<iQ", len(nomi), 0) + struct.pack("<I", 0)
              + struct.pack("<I", 1) + struct.pack("<qq", 0, len(fdi)) + b"\x00" * 20)
    coda = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 11, off_indice, len(indice))
            + hashlib.sha1(indice).digest() + b"\x00" * 160)
    return fdi + indice + coda


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    automatico.CARTELLA_PROFILI = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatico.CARTELLA_PROFILI)
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    os.environ["LOCALAPPDATA"] = os.path.join(base, "LocalAppData")
    sistema.gioco_aperto = lambda c: False

    # === lingue: confronto con le varianti (il cinese per scrittura) =================================
    controlla("lingua: zh-TW non e' zh-CN, pt-BR e' portoghese",
              not lingua.stessa_lingua("zh-TW", "zh-CN") and lingua.stessa_lingua("zh-HK", "zh-Hant")
              and lingua.stessa_lingua("zh", "zh-CN") and lingua.stessa_lingua("pt-BR", "pt"))
    controlla("lingua: il migliore e' lo stesso codice, poi senza regione, mai l'altra scrittura",
              lingua.migliore(["it-IT", "it", "en"], "it") == "it"
              and lingua.migliore(["it-IT", "en"], "it") == "it-IT"
              and lingua.migliore(["zh-Hans", "en"], "zh-TW") is None
              and lingua.migliore(["zh-Hans", "zh-Hant"], "zh-TW") == "zh-Hant"
              and lingua.migliore(["zh-Hans", "zh-Hant"], "zh-CN") == "zh-Hans")

    # === Steam ==========================================================================================
    radice_steam = os.path.join(base, "Steam")
    libreria = os.path.join(base, "Libreria")
    os.makedirs(os.path.join(radice_steam, "appcache"))
    os.makedirs(os.path.join(libreria, "steamapps", "common"))
    steam.cartella_steam = lambda: radice_steam
    scrivi(os.path.join(radice_steam, "steamapps", "libraryfolders.vdf"),
           ('"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
            % libreria.replace("\\", "\\\\")).encode())
    ita = {"italian": {"supported": "true"}, "english": {"supported": "true"}}
    file_appinfo = os.path.join(radice_steam, "appcache", "appinfo.vdf")
    scrivi(file_appinfo, appinfo({
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
    scrivi(os.path.join(redist, "_CommonRedist", "vcredist", "vc_redist.x64.exe"), b"MZ")
    scrivi(os.path.join(redist, "MyRepack.exe"), b"MZ")
    controlla("Steam: installatori nella cartella e nomi fuori dai percorsi di avvio non riconoscono un gioco",
              steam.riconosci_da_eseguibili(redist) is None)
    protetto = os.path.join(base, "Protetto")
    scrivi(os.path.join(protetto, "Game", "start_protected_game.exe"), b"MZ")
    controlla("Steam: nome di avvio generico (start_protected_game.exe) ignorato",
              steam.riconosci_da_eseguibili(protetto) is None)
    condiviso = os.path.join(base, "Condiviso")
    scrivi(os.path.join(condiviso, "Shared.exe"), b"MZ")
    controlla("Steam: eseguibile avviato da due giochi -> nessuno", steam.riconosci_da_eseguibili(condiviso) is None)
    pari = os.path.join(base, "Pari")
    scrivi(os.path.join(pari, "Alpha.exe"), b"MZ")
    scrivi(os.path.join(pari, "Beta.exe"), b"MZ")
    controlla("Steam: pari merito -> nessun vincitore", steam.riconosci_da_eseguibili(pari) is None)
    corto = os.path.join(base, "Dungeon Short")
    scrivi(os.path.join(corto, "bin", "dds.exe"), b"MZ")
    r = steam.riconosci_da_eseguibili(corto)
    controlla("Steam: nome corto con percorso esatto, maiuscole diverse -> riconosciuto, cartella simile",
              r and r["appid"] == "506" and r["cartella_simile"])
    r = lingua_ufficiale.rileva(corto, "it")
    righe = lingua_ufficiale.messaggio(r, "it")
    controlla("cartella riconosciuta e confermata: lingua presente; menu Lingua di Steam solo se c'e'",
              r["presente"] is True and r["selezionabile_steam"] is False
              and not any("Proprieta'" in x for x in righe) and any("non e' un'installazione" in x for x in righe))
    righe = lingua_ufficiale.messaggio(r, "it", ["Ho impostato l'italiano."], True)
    controlla("messaggio: dopo un'impostazione riuscita niente istruzioni contraddittorie",
              not any("non e' un'installazione" in x for x in righe))
    caso = os.path.join(base, "CartellaACaso")
    scrivi(os.path.join(caso, "SetteGame.exe"), b"MZ")
    r = lingua_ufficiale.rileva(caso, "it")
    controlla("Steam: riconoscimento non confermato dal nome della cartella -> solo una nota, nessun blocco",
              r["presente"] is None and r["note"] and r["steam_conosciuto"]["appid"] == "507")
    gioco_steam = os.path.join(libreria, "steamapps", "common", "Gioco Steam")
    os.makedirs(gioco_steam)
    scrivi(os.path.join(libreria, "steamapps", "appmanifest_508.acf"),
           b'"AppState"\n{\n\t"appid"\t\t"508"\n\t"name"\t\t"Gioco Steam"\n\t"installdir"\t\t"Gioco Steam"\n}\n')
    collegamento = os.path.join(base, "Collegamento")
    fatto = subprocess.run(["cmd", "/c", "mklink", "/J", collegamento, gioco_steam], capture_output=True).returncode == 0
    if fatto:
        info = steam.gioco_installato(collegamento)
        controlla("Steam: gioco raggiunto da una junction riconosciuto come installato", info and info["appid"] == "508")
        os.rmdir(collegamento)
    else:
        print("SALTATO  junction non creabile qui")
    letture = []
    vecchia = steam._indicizza
    steam._indicizza = lambda d: (letture.append(1), vecchia(d))[1]
    steam._CACHE.clear()
    steam.info_app(506)
    steam.info_app(507)
    lingua_ufficiale.rileva(corto, "it")
    steam._indicizza = vecchia
    controlla("Steam: appinfo.vdf letto una volta sola", len(letture) == 1)
    integro = leggi(file_appinfo)
    for nome, dati in (("troncato", integro[:100]), ("vuoto", b""), ("tabella fuori dal file",
                                                                      integro[:8] + struct.pack("<q", 10 ** 9) + integro[16:])):
        scrivi(file_appinfo, dati)
        steam._CACHE.clear()
        try:
            steam.info_app(506)
            errore = None
        except steam.ErroreSteam as e:
            errore = e
        except Exception as e:  # noqa: BLE001 - e' proprio quello che non deve succedere
            errore = "SBAGLIATO: %r" % e
        avvisi = []
        try:
            r = lingua_ufficiale.rileva(corto, "it", log=avvisi.append)
            ok = r["presente"] is None
        except Exception:
            ok = False
        controlla("Steam: appinfo.vdf %s -> ErroreSteam, e il rilevamento continua" % nome,
                  isinstance(errore, steam.ErroreSteam) and ok)
    scrivi(file_appinfo, integro)
    steam._CACHE.clear()

    # === Unreal =========================================================================================
    ue = os.path.join(base, "GiocoUE")
    paks = os.path.join(ue, "Proj", "Content", "Paks")
    voci = [("UI", "Start", "Start")]
    scrivi(os.path.join(paks, "Proj-WindowsNoEditor.pak"), pak_v9("../../../Proj/Content/Localization/Game/", [
        ("Game.locmeta", test_unreal.locmeta("en")), ("en/Game.locres", test_unreal.locres_v2(voci)),
        ("it/Game.locres", test_unreal.locres_v2([("UI", "Start", "Inizia")]))]))
    scrivi(os.path.join(paks, "Online-Plugin.pak"), pak_v9("../../../Engine/Plugins/Online/X/", [
        ("Content/Localization/X/fr/X.locres", test_unreal.locres_v2(voci))]))
    os.makedirs(os.path.join(ue, "Engine", "Binaries", "ThirdParty"))
    scrivi(os.path.join(ue, "Engine", "Binaries", "ThirdParty", "x.dll"), b"x")
    lingue, prog = lingua_unreal.lingue_del_gioco(ue)
    controlla("Unreal: pak v9 montato in una sottocartella letto; plugin del motore esclusi",
              lingue == {"en", "it"} and prog == "Proj")
    ue11 = os.path.join(base, "GiocoUE5")
    scrivi(os.path.join(ue11, "Proj5", "Content", "Paks", "Proj5-Windows.pak"), pak_v11(
        "../../../Proj5/Content/Localization/Game/", ["en/Game.locres", "zh-Hant/Game.locres", "Game.locmeta"]))
    lingue, prog = lingua_unreal.lingue_del_gioco(ue11)
    controlla("Unreal: pak v11 (indice delle cartelle) con punto di montaggio",
              lingue == {"en", "zh-Hant"} and prog == "Proj5")
    controlla("Unreal: cinese per scrittura (zh-TW -> zh-Hant; mai zh-Hans al posto di zh-Hant)",
              lingua_unreal.tag_italiano({"zh-Hant", "en"}, "zh-TW") == "zh-Hant"
              and lingua_unreal.tag_italiano({"zh-Hans", "en"}, "zh-TW") is None
              and lingua_ufficiale.lingue_nei_file(ue11, "Unreal", "zh-CN")[1]["trovata"] is False)
    gus_gioco = os.path.join(ue, "Proj", "Saved", "Config", "Windows", "GameUserSettings.ini")
    scrivi(gus_gioco, b"[Internationalization]\r\nLanguage=en\r\n")
    controlla("Unreal: impostazioni cercate anche nella cartella del gioco",
              lingua_unreal.file_impostazioni("Proj", ue) == gus_gioco)
    os.remove(gus_gioco)
    prova = os.path.join(base, "ini", "GameUserSettings.ini")
    scrivi(prova, b"[/Script/Engine.InputSettings]\r\nKeyboardCulture=it-IT\r\n[/Script/Game.Settings]\r\n"
                  b"LanguageFilter=1\r\n")
    controlla("Unreal: KeyboardCulture e filtri non sono la lingua del gioco",
              lingua_unreal.leggi_internazionalizzazione(prova)[1] == [])
    scrivi(prova, b"[/Game/UI/BP_Settings.BP_Settings_C]\r\nCurrentLocalization=en\r\n")
    controlla("Unreal: impostazione della lingua nelle sezioni /Game/ riconosciuta",
              lingua_unreal.leggi_internazionalizzazione(prova)[1])

    def andata_e_ritorno(nome, originale, atteso=None):
        scrivi(prova, originale)
        modifiche = lingua_unreal.imposta_cultura(prova, "it")
        dopo = leggi(prova)
        tornato = lingua_unreal.ripristina_cultura(prova, modifiche, "it") and leggi(prova) == originale
        controlla("Unreal: %s -> impostato e ripristinato identico" % nome,
                  tornato and (atteso is None or atteso in dopo))
    andata_e_ritorno("a capo misti (CRLF e LF)", b"[A]\r\nx=1\n[Internationalization]\nLanguage=en\r\n[B]\r\ny=2",
                     b"[Internationalization]\nLanguage=it\r\n")
    andata_e_ritorno("chiavi doppie e con maiuscole diverse",
                     b"[Internationalization]\r\nLanguage=en\r\nlanguage=it\r\nLanguage=fr\r\n", b"Language=it\r\nlanguage=it\r\nLanguage=it\r\n")
    andata_e_ritorno("sezione aggiunta a un file senza a capo finale", b"[A]\r\nx=1", b"Culture=it")
    andata_e_ritorno("sezione aggiunta a un file vuoto", b"", b"Culture=it")
    andata_e_ritorno("riga aggiunta in fondo a una sezione senza a capo finale", b"[Internationalization]\r\nX=1",
                     b"Culture=it")
    scrivi(prova, b"[Internationalization]\r\nLanguage=en\r\nLanguage=en\r\n")
    modifiche = lingua_unreal.imposta_cultura(prova, "it")
    scrivi(prova, b"[Internationalization]\r\nLanguage=it\r\nLanguage=de\r\n")  # il giocatore cambia la seconda
    lingua_unreal.ripristina_cultura(prova, modifiche, "it")
    controlla("Unreal: ripristino riga per riga, solo dove c'e' ancora il valore scritto",
              leggi(prova) == b"[Internationalization]\r\nLanguage=en\r\nLanguage=de\r\n")
    gus = os.path.join(os.environ["LOCALAPPDATA"], "Proj", "Saved", "Config", "WindowsNoEditor", "GameUserSettings.ini")
    scrivi(gus, b"\xff\xfe" + "[Internationalization]\r\nLanguage=en\r\n".encode("utf-16-le") + b"\x00")
    rilevamento = {"motore": {"tipo": "unreal", "progetto": "Proj", "tag": "it", "trovata": True}}
    try:
        fatto, righe = lingua_ufficiale.imposta(ue, rilevamento, "it", silenzio)
        ok = not fatto and any("menu" in x for x in righe)
    except Exception:
        ok = False
    controlla("Unreal: impostazioni UTF-16 rovinate -> istruzioni, nessun errore", ok)
    originale_gus = b"[Internationalization]\r\nLanguage=en\r\n"
    scrivi(gus, originale_gus)
    fatto, _ = lingua_ufficiale.imposta(ue, rilevamento, "it", silenzio)
    sistema.gioco_aperto = lambda c: True
    esito = lingua_ufficiale.ripristina(ue, silenzio)
    controlla("Unreal: ripristino con il gioco aperto -> non scrivo e il registro resta",
              fatto and esito is False and lingua_ufficiale.impostata(ue) and b"Language=it" in leggi(gus))
    sistema.gioco_aperto = lambda c: False
    os.chmod(gus, stat.S_IREAD)
    scrivi(os.path.join(ue, "Proj", "Content", "Paks", "zz_Patchsmith_it_999_P.pak"), b"finto")
    messaggi = []
    try:
        automatico.ripristina(ue, messaggi.append)
        ok = lingua_ufficiale.impostata(ue) and any("lingua del gioco" in m for m in messaggi)
    except Exception as e:
        ok = False
        messaggi.append(repr(e))
    os.chmod(gus, stat.S_IREAD | stat.S_IWRITE)
    os.remove(os.path.join(ue, "Proj", "Content", "Paks", "zz_Patchsmith_it_999_P.pak"))
    controlla("Ripristina originali: impostazioni di sola lettura -> continua, avvisa e tiene il registro", ok)
    controlla("Ripristina da riga di comando con la cartella del gioco", cli.main(["ripristina", ue]) == 0
              and not lingua_ufficiale.impostata(ue) and leggi(gus) == originale_gus)
    os.remove(gus)
    fatto, righe = lingua_ufficiale.imposta(ue, rilevamento, "it", silenzio)
    fatto2, righe2 = lingua_ufficiale.imposta(ue, rilevamento, "it", silenzio)
    controlla("Unreal: impostazioni non trovate -> 'avvialo una volta' solo la prima volta, poi il menu",
              not fatto and any("avvialo una volta" in x for x in righe)
              and not fatto2 and not any("avvialo una volta" in x for x in righe2) and any("menu" in x for x in righe2))
    scrivi(gus, originale_gus)

    # traduci comunque: niente rilevamento, e la lingua impostata prima torna com'era
    percorso, esito = automatico.prepara(ue, "auto", "it", "finto", silenzio)
    impostata_prima = lingua_ufficiale.impostata(ue) and b"Language=it" in leggi(gus)
    vero_rileva = lingua_ufficiale.rileva

    def non_chiamare(*_a, **_k):
        raise AssertionError("rileva non deve essere chiamato")
    lingua_ufficiale.rileva = non_chiamare
    try:
        percorso, esito = automatico.prepara(ue, "auto", "it", "finto", silenzio, traduci_comunque=True)
        ok = percorso is not None and not lingua_ufficiale.impostata(ue) and leggi(gus) == originale_gus
    except AssertionError:
        ok = False
    lingua_ufficiale.rileva = vero_rileva
    controlla("Traduci comunque: niente rilevamento, lingua rimessa com'era, traduzione preparata",
              impostata_prima and ok)
    controlla("Riga di comando: 'auto' con la lingua ufficiale gestita esce con 0",
              cli.main(["auto", ue, "--a", "it", "--backend", "finto"]) == 0)
    scrivi(os.path.join(ue, "patchsmith-unreal.json"), b"{}")
    percorso, esito = automatico.prepara(ue, "auto", "it", "finto", silenzio)
    controlla("Lingua ufficiale con una traduzione di PatchSmith ancora installata: lo dice",
              esito.get("traduzione_installata") and any("ancora installata" in x for x in esito["lingua_ufficiale_righe"]))
    os.remove(os.path.join(ue, "patchsmith-unreal.json"))
    automatico.ripristina(ue, silenzio)

    # === Unity ==========================================================================================
    voci_i2 = [("English", "en"), ("Chinese (Simplified)", "zh-CN"), ("Chinese (Traditional)", "zh-TW"),
               ("Italiano", "it-IT")]
    controlla("I2: voce scelta per codice e scrittura (zh-TW -> Traditional; it -> it-IT)",
              lingua_unity.voce_i2(voci_i2, "zh-TW") == "Chinese (Traditional)"
              and lingua_unity.voce_i2(voci_i2, "zh-CN") == "Chinese (Simplified)"
              and lingua_unity.voce_i2(voci_i2, "it") == "Italiano"
              and lingua_unity.voce_i2([("English", "en"), ("Chinese", "zh-CN")], "zh-TW") is None)
    un = os.path.join(base, "GiocoUnity")
    dati = os.path.join(un, "Gioco_Data")
    scrivi(os.path.join(dati, "globalgamemanagers"), b"x")
    scrivi(os.path.join(dati, "app.info"), b"PatchsmithTest2\nGioco")
    scrivi(os.path.join(dati, "Managed", "I2Localization.dll"), b"MZ I2.Loc\x00")
    aa = os.path.join(dati, "StreamingAssets", "aa")
    scrivi(os.path.join(aa, "catalog.bin"), b"\x00\x01UnityEngine.Localization.Locale\x00\x12Italian (it).asset\x00"
                                            b"\x07Locale-itI\x00\x10French (fr).asset\x00")
    controlla("Unity Localization: catalogo binario (solo i codici con etichetta Locale-)",
              lingua_unity.lingue_unity_localization(dati) == {"it"})

    def s(t):
        b = t.encode()
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    def lista(*voci):
        return struct.pack("<i", len(voci)) + b"".join(s(n) + s(c) + b"\x00\x00\x00\x00" for n, c in voci) + \
            struct.pack("<ii", 0, 0)
    scrivi(os.path.join(dati, "resources.assets"), b"\x00" * 64 + lista(("English", "en"), ("Italiano", "it")) +
           b"\x00" * 64)
    for k in range(4):
        scrivi(os.path.join(dati, "level%d" % k), b"\x00" * 64 + lista(("English", "en"), ("Lingua%d" % k, "x%d" % k))
               + b"\x00" * 64)
    controlla("I2: dopo la prima lista si leggono solo pochi altri file",
              [n for n, _ in lingua_unity.lingue_i2(dati)] == ["English", "Italiano", "Lingua0", "Lingua1"])
    vero_i2 = lingua_unity.lingue_i2

    def rotto(_dati):
        raise struct.error("asset rovinato")
    lingua_unity.lingue_i2 = rotto
    presenti, dm = lingua_ufficiale.lingue_nei_file(un, "Unity", "it")
    lingua_unity.lingue_i2 = vero_i2
    controlla("Unity: un errore nella lettura di I2 non fa perdere Unity Localization",
              presenti == {"it"} and dm["trovata"])
    import winreg
    chiave = "Software\\PatchsmithTest2\\Gioco"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, chiave) as k:
        winreg.SetValueEx(k, lingua_unity.nome_pref("I2 Language"), 0, winreg.REG_SZ, "English")
    r = lingua_ufficiale.rileva(un, "it", "Unity")
    fatto, righe = lingua_ufficiale.imposta(un, r, "it", silenzio)
    controlla("Unity: impostazione descritta come tentativo ('dovrebbe')", fatto and any("dovrebbe" in x for x in righe))
    lingua_ufficiale.ripristina(un, silenzio)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chiave) as k:
        valore, tipo = winreg.QueryValueEx(k, lingua_unity.nome_pref("I2 Language"))
    controlla("Unity: ripristino di un valore di tipo testo (REG_SZ)", tipo == winreg.REG_SZ and valore == "English")
    scrivi(os.path.join(dati, "Managed", "I2Localization.dll"), b"MZ I2.Loc\x00" + "LastSelectedLanguage".encode("utf-16-le"))
    r = lingua_ufficiale.rileva(un, "it", "Unity")
    fatto, righe = lingua_ufficiale.imposta(un, r, "it", silenzio)
    controlla("Unity: I2 modificato dal gioco (LastSelectedLanguage) -> solo istruzioni",
              not fatto and any("menu" in x for x in righe) and not lingua_ufficiale.impostata(un))
    for k in (chiave, "Software\\PatchsmithTest2"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
        except OSError:
            pass

    # === GameMaker: traduci comunque anche se il gioco ha i file in italiano ==============================
    gm = os.path.join(base, "Gioco GM")
    scrivi(os.path.join(gm, "Localization", "english_ui.txt"), test_gamemaker.UI.encode())
    scrivi(os.path.join(gm, "Localization", "italian", "ui.txt"), b"0 Combatti\r\n1 Cambia\r\n")
    latino = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    scrivi(os.path.join(gm, "data.win"), test_gamemaker.data_win([("fn_testo", latino)]))
    scrivi(os.path.join(gm, "Gioco.exe"), b"MZ")
    percorso, esito = automatico.prepara(gm, "auto", "it", "finto", silenzio)
    percorso2, _ = automatico.prepara(gm, "auto", "it", "finto", silenzio, traduci_comunque=True)
    controlla("GameMaker: italiano nei file -> lingua ufficiale; con 'traduci comunque' si traduce",
              percorso is None and esito.get("lingua_ufficiale") and percorso2 is not None)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
