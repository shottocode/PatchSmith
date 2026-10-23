"""Test della lingua ufficiale: Steam finto, gioco Unreal finto e gioco Unity (I2) finto.

Uso: python -I test/prova_lingua_ufficiale.py <cartella_temporanea_vuota>
Scrive solo nella cartella temporanea e in una chiave di registro di prova (HKCU\\Software\\PatchsmithTest),
che alla fine viene cancellata. Steam e i giochi veri non vengono toccati.
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import automatico, lingua_ufficiale, lingua_unity, lingua_unreal, progetto, steam  # noqa: E402
import prova_unreal  # noqa: E402  (costruttori di pak e .locres)

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


# --- appinfo.vdf finto (v41: chiavi nella tabella delle stringhe) ------------------------------------

def appinfo(app):
    """app: {appid: albero di dizionari}. Restituisce i byte di un appinfo.vdf versione 0x07564429."""
    chiavi, indice = [], {}

    def k(nome):
        if nome not in indice:
            indice[nome] = len(chiavi)
            chiavi.append(nome)
        return struct.pack("<i", indice[nome])

    def kv(albero):
        out = bytearray()
        for nome, v in albero.items():
            if isinstance(v, dict):
                out += b"\x00" + k(nome) + kv(v) + b"\x08"
            elif isinstance(v, int):
                out += b"\x02" + k(nome) + struct.pack("<i", v)
            else:
                out += b"\x01" + k(nome) + str(v).encode() + b"\x00"
        return bytes(out)

    record = bytearray()
    for appid, albero in app.items():
        corpo = kv({"appinfo": albero}) + b"\x08"
        testa = struct.pack("<IIQ", 2, 0, 0) + b"\x00" * 20 + struct.pack("<I", 1) + hashlib.sha1(corpo).digest()
        record += struct.pack("<II", appid, len(testa) + len(corpo)) + testa + corpo
    record += struct.pack("<I", 0)
    tabella = struct.pack("<I", len(chiavi)) + b"".join(c.encode() + b"\x00" for c in chiavi)
    return struct.pack("<IIq", 0x07564429, 1, 16 + len(record)) + bytes(record) + tabella


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    radice_steam = os.path.join(base, "Steam")
    libreria = os.path.join(base, "Libreria")
    os.makedirs(os.path.join(radice_steam, "appcache"))
    os.makedirs(os.path.join(radice_steam, "steamapps"))
    os.makedirs(os.path.join(libreria, "steamapps", "common"))
    steam.cartella_steam = lambda: radice_steam
    open(os.path.join(radice_steam, "steamapps", "libraryfolders.vdf"), "w").write(
        '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n\t// commento\n\t"1"\n\t{\n\t\t"path"\t\t"%s"\n\t}\n}\n'
        % (radice_steam.replace("\\", "\\\\"), libreria.replace("\\", "\\\\")))

    # --- VDF di testo
    v = steam.leggi_vdf('"A"\n{\n\t"x"\t"1 \\"q\\""\n\t// commento\n\t"B" { "y" "2" }\n}\n')
    controlla("VDF di testo: annidamento, virgolette con escape, commenti", v == {"A": {"x": '1 "q"', "B": {"y": "2"}}})

    # --- appinfo: tre giochi
    open(os.path.join(radice_steam, "appcache", "appinfo.vdf"), "wb").write(appinfo({
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
    controlla("appinfo v41: lingue ufficiali", steam.lingue_ufficiali(100) == {
        "english": {"supported": True}, "italian": {"supported": True, "subtitles": True}}
        and steam.lingue_ufficiali(200) == {"english": {"supported": True}})
    controlla("appinfo: lingue selezionabili da Steam (baselanguages)", steam.lingue_selezionabili(100) == {"english", "italian"}
              and steam.lingue_selezionabili(200) == set())

    # --- gioco installato da Steam (appmanifest)
    gioco_steam = os.path.join(libreria, "steamapps", "common", "Gioco Steam")
    os.makedirs(os.path.join(gioco_steam, "bin"))
    open(os.path.join(libreria, "steamapps", "appmanifest_100.acf"), "w").write(
        '"AppState"\n{\n\t"appid"\t\t"100"\n\t"name"\t\t"Gioco Steam"\n\t"installdir"\t\t"Gioco Steam"\n'
        '\t"UserConfig"\n\t{\n\t\t"language"\t\t"english"\n\t}\n}\n')
    info = steam.gioco_installato(os.path.join(gioco_steam, "bin"))
    controlla("gioco installato riconosciuto (anche da una sottocartella)", info and info["appid"] == "100"
              and info["lingua"] == "english")
    r = lingua_ufficiale.rileva(gioco_steam, "it")
    righe = lingua_ufficiale.messaggio(r, "it")
    controlla("Steam: italiano ufficiale e selezionabile -> istruzioni per il menu Lingua di Steam",
              r["presente"] and r["selezionabile_steam"] and any("Generali > Lingua" in x for x in righe))

    # --- cartella fuori da Steam, riconosciuta dall'eseguibile (la demo non conta)
    fuori = os.path.join(base, "Fuori")
    os.makedirs(fuori)
    open(os.path.join(fuori, "GiocoFuoriSteam.exe"), "wb").write(b"MZ")
    conosciuto = steam.riconosci_da_eseguibili(fuori)
    controlla("gioco fuori da Steam riconosciuto dall'eseguibile, non la demo", conosciuto and conosciuto["appid"] == "300")
    r = lingua_ufficiale.rileva(fuori, "it")
    controlla("fuori da Steam: italiano ufficiale segnalato, senza istruzioni Steam per la cartella",
              r["presente"] and r["steam"] is None and
              any("non e' un'installazione di Steam" in x for x in lingua_ufficiale.messaggio(r, "it")))

    # --- Unreal: gioco con italiano, impostazione e ripristino in GameUserSettings.ini
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    automatico.CARTELLA_PROFILI = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatico.CARTELLA_PROFILI)
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    os.environ["LOCALAPPDATA"] = os.path.join(base, "LocalAppData")
    ue = os.path.join(base, "GiocoUE")
    paks = os.path.join(ue, "Proj", "Content", "Paks")
    os.makedirs(paks)
    os.makedirs(os.path.join(ue, "Engine", "Binaries", "ThirdParty"))
    open(os.path.join(ue, "Engine", "Binaries", "ThirdParty", "x.dll"), "wb").write(b"x")
    voci = [("UI", "Start", "Start")]
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(prova_unreal.pak_v9([
        ("Proj/Content/Localization/Game/Game.locmeta", prova_unreal.locmeta("en")),
        ("Proj/Content/Localization/Game/en/Game.locres", prova_unreal.locres_v2(voci)),
        ("Proj/Content/Localization/Game/it-IT/Game.locres", prova_unreal.locres_v2([("UI", "Start", "Inizia")])),
        ("Proj/Plugins/X/Content/Localization/X/fr/X.locres", prova_unreal.locres_v2(voci)),
    ]))
    lingue, prog = lingua_unreal.lingue_del_gioco(ue)
    controlla("Unreal: lingue del gioco (i plugin non contano), progetto", lingue == {"en", "it-IT"} and prog == "Proj")
    controlla("Unreal: si usa il nome esatto della cartella (it-IT)", lingua_unreal.tag_italiano(lingue) == "it-IT")
    gus_dir = os.path.join(os.environ["LOCALAPPDATA"], "Proj", "Saved", "Config", "WindowsNoEditor")
    os.makedirs(gus_dir)
    gus = os.path.join(gus_dir, "GameUserSettings.ini")
    originale_gus = b"[/Script/Engine.GameUserSettings]\r\nVersion=5\r\n\r\n[Internationalization]\r\nLanguage=en\r\n"
    open(gus, "wb").write(originale_gus)
    import patchsmith.sistema as sistema
    sistema.gioco_aperto = lambda c: False
    percorso, esito = automatico.prepara(ue, "auto", "it", "finto", silenzio)
    controlla("Unreal: niente traduzione automatica, lingua impostata", percorso is None and esito.get("lingua_ufficiale")
              and b"Language=it-IT" in open(gus, "rb").read() and lingua_ufficiale.impostata(ue))
    controlla("Unreal: messaggio con quello che e' stato fatto",
              any("Ho impostato" in x for x in esito.get("lingua_ufficiale_righe", [])))
    automatico.ripristina(ue, silenzio)
    controlla("Unreal: ripristino identico e registro tolto", open(gus, "rb").read() == originale_gus
              and not lingua_ufficiale.impostata(ue))
    open(gus, "wb").write(b"[/Script/Game.Settings]\r\nLanguageSetting=English\r\n")
    fatto, righe = lingua_ufficiale.imposta(ue, lingua_ufficiale.rileva(ue, "it", "Unreal"), "it", silenzio)
    controlla("Unreal: il gioco ha una sua impostazione della lingua -> solo istruzioni",
              not fatto and b"Culture" not in open(gus, "rb").read() and any("menu" in x for x in righe))
    sistema.gioco_aperto = lambda c: True
    open(gus, "wb").write(originale_gus)
    fatto, righe = lingua_ufficiale.imposta(ue, lingua_ufficiale.rileva(ue, "it", "Unreal"), "it", silenzio)
    controlla("Unreal: gioco aperto -> non scrivo", not fatto and open(gus, "rb").read() == originale_gus)
    sistema.gioco_aperto = lambda c: False

    # --- Unity con I2: lista di lingue negli asset, preferenza nel registro (chiave di prova)
    controlla("PlayerPrefs: nome del valore come Unity", lingua_unity.nome_pref("I2 Language") == "I2 Language_h3293684300")
    un = os.path.join(base, "GiocoUnity")
    dati = os.path.join(un, "Gioco_Data")
    os.makedirs(os.path.join(dati, "Managed"))
    open(os.path.join(dati, "globalgamemanagers"), "wb").write(b"x")
    open(os.path.join(dati, "app.info"), "w").write("PatchsmithTest\nGioco")
    open(os.path.join(dati, "Managed", "I2Localization.dll"), "wb").write(b"MZ I2.Loc\x00")

    def s(t):
        b = t.encode()
        return struct.pack("<i", len(b)) + b + b"\x00" * ((4 - len(b) % 4) % 4)

    lista = struct.pack("<i", 3) + s("English") + s("en") + b"\x00\x00\x00\x00" + s("Italiano") + s("it") + \
        b"\x00\x00\x00\x00" + s("Deutsch") + s("de") + b"\x01\x00\x00\x00" + struct.pack("<ii", 0, 0)
    open(os.path.join(dati, "resources.assets"), "wb").write(b"\x00" * 64 + lista + b"\x00" * 64)
    controlla("I2: lingue attive dagli asset (le disattivate no)",
              lingua_unity.lingue_i2(dati) == [("English", "en"), ("Italiano", "it")])
    import winreg
    chiave = "Software\\PatchsmithTest\\Gioco"
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, chiave)
    except OSError:
        pass
    r = lingua_ufficiale.rileva(un, "it", "Unity")
    fatto, righe = lingua_ufficiale.imposta(un, r, "it", silenzio)
    controlla("Unity: gioco mai avviato (nessuna chiave) -> non scrivo, istruzioni", not fatto
              and lingua_unity.leggi_pref("PatchsmithTest", "Gioco", "I2 Language") is False)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, chiave) as k:
        winreg.SetValueEx(k, lingua_unity.nome_pref("I2 Language"), 0, winreg.REG_BINARY, b"English\x00")
        winreg.SetValueEx(k, "Altro_h1", 0, winreg.REG_DWORD, 7)
    fatto, righe = lingua_ufficiale.imposta(un, r, "it", silenzio)
    controlla("Unity: preferenza I2 impostata al nome esatto ('Italiano')", fatto and
              lingua_unity.leggi_pref("PatchsmithTest", "Gioco", "I2 Language")[1] == b"Italiano\x00")
    lingua_ufficiale.ripristina(un, silenzio)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chiave) as k:
        altro = winreg.QueryValueEx(k, "Altro_h1")[0]
    controlla("Unity: ripristino del solo valore toccato",
              lingua_unity.leggi_pref("PatchsmithTest", "Gioco", "I2 Language")[1] == b"English\x00" and altro == 7)
    for k in (chiave, "Software\\PatchsmithTest"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
        except OSError:
            pass

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
