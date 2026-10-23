"""Prove dei caratteri dei font e dello spazio delle etichette, per tutti i motori (punto 9).

Uso: python -I test/test_font_length.py <cartella_temporanea_vuota>
Giochi, font e registri finti (test/fake_unity.py, test/data/signals): nessun gioco vero, nessuna rete, nessun
traduttore vero (traduttori finti in memoria). Copre:
- font di Unity (font_unity): tabella cmap dei .ttf; font TextMeshPro con l'albero dei campi (bundle) e senza (file di
  _Data, riconosciuti dalla forma); font dinamici con il loro .ttf; regola "almeno un font sa disegnarlo"; risultato
  tenuto in memory/<gioco>.unity/font.json finche' i file del gioco non cambiano;
- Unity Localization: una tabella scritta con è -> e' se nessun font ha è (in memoria resta è; la tabella francese non
  si tocca); un carattere che non si puo' adattare lascia l'originale e va in problemi.json; con un font che ha è non
  cambia nulla;
- XUnity: BepInEx.cfg con WriteUnityLog all'installazione (e il controllo lo rimette); i caratteri mancanti letti dai
  registri (righe vere); il controllo dopo aver giocato adatta le righe della cache scritte da PatchSmith (non quelle
  cambiate a mano), annota i caratteri nel profilo, e da li' in poi cache e traduttore dal vivo li evitano; in sola
  lettura non scrive nulla;
- Unity Localization con il Player.log (gioco IL2CPP): caratteri annotati e tabella riscritta con "Riapplica";
- Unreal: caratteri delle altre lingue latine del gioco (Latino-1 completo se le usano), nessun limite senza indizi;
- etichette troppo lunghe: richieste piu' corte al traduttore locale (contesto 'larghezza', anche nel prompt vero),
  tenuta la piu' corta valida, le altre in problemi.json con il testo originale e nell'elenco "Troppo lunghi" di
  "Correggi traduzioni"; una volta sola; non le correzioni a mano; con gli altri traduttori solo annotate; lo spazio
  delle traduzioni ufficiali; esito e messaggi.
"""
import hashlib
import json
import os
import shutil
import sys
import time

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

from patchsmith import (adapter_unity, adapter_unity_loc, adapter_unreal, auto as automatico, backend,
                        characters as caratteri, components as componenti, checks as controllo,
                        corrections as correzioni, outcome as esito, font_unity, project as progetto, scan as scansione,
                        xunity_service as servizio_xunity)
from patchsmith.project import Progetto  # noqa: E402
import fake_unity as finto  # noqa: E402

ESITI = []
SEGNALI = os.path.join(QUI, "data", "signals")
ASCII = "".join(chr(c) for c in range(32, 127))


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronta(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def leggi_json(percorso):
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def scrivi_json(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False)


def scrivi(percorso, testo, data=None):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "w", encoding="utf-8", newline="") as f:
        f.write(testo)
    if data is not None:
        os.utime(percorso, (data, data))


def istantanea(cartella):
    uscita = {}
    for radice, _, files in os.walk(cartella):
        for f in files:
            p = os.path.join(radice, f)
            st = os.stat(p)
            uscita[os.path.relpath(p, cartella)] = (st.st_size, st.st_mtime_ns)
    return uscita


EN = ["New Game", "Continue", "Options", "The door is locked. Find the key first.", "Quit to desktop"]
FR = ["Nouvelle partie", "Continuer", "Options", "La porte est fermée.", "Quitter"]
TABELLA_EN = "localization-string-tables-english(en)_assets_all.bundle"
TABELLA_FR = "localization-string-tables-french(fr)_assets_all.bundle"


# --- font di Unity -----------------------------------------------------------------------------------------------

def prova_font(base):
    controlla("font .ttf: caratteri della tabella cmap (formato 4)",
              font_unity.caratteri_ttf(finto.ttf("abcè€")) == set("abcè€")
              and font_unity.caratteri_ttf(b"non e' un font") is None)
    dati = finto.dati_font_tmp("Pixel SDF", ASCII + "é", 0)
    controlla("font TextMeshPro senza albero dei campi: tabella dei caratteri riconosciuta dalla forma",
              {chr(c) for c in font_unity.tabella_tmp(b"\x07" * 37 + dati)} == set(ASCII + "é")
              and font_unity._nome_oggetto(dati) == "Pixel SDF" and font_unity.tabella_tmp(b"\x01\x00" * 99) is None)
    # gioco con un font nel bundle (con l'albero) e uno in resources.assets (senza), uno dinamico con il suo .ttf
    gioco = os.path.join(base, "font")
    finto.gioco_il2cpp(gioco, {"ui_assets_all.bundle": finto.bundle([("CAB-f", finto.file_font(
        [("Titoli SDF", ASCII + "ü", 0)]))], compressione="lz4", blocco=4096)}, extra={
        "Gioco_Data/resources.assets": finto.file_font([("Testi SDF", ASCII + "à", 0)], alberi=False)})
    dati_gioco = os.path.join(gioco, "Gioco_Data")
    trovati = font_unity.font_del_gioco(dati_gioco)
    nomi = sorted((f["nome"], f["modo"]) for f in trovati["tmp"])
    controlla("font del gioco: quello del bundle (con il modo) e quello di resources.assets (senza albero)",
              nomi == [("Testi SDF", None), ("Titoli SDF", 0)] and trovati["completa"])
    ammessi, motivo = font_unity.caratteri_ammessi(dati_gioco)
    controlla("regola: un carattere va bene se almeno un font lo ha (à da uno, ü dall'altro), è no",
              ammessi is not None and {"à", "ü"} <= ammessi and "è" not in ammessi and "2 font" in motivo)
    # font dinamico: conta il suo .ttf
    dinamico = os.path.join(base, "dinamico")
    finto.gioco_il2cpp(dinamico, {"fonts_assets_all.bundle": finto.bundle([("CAB-d", finto.file_font(
        [("Corpo SDF", "", 1), ("Pixel SDF", ASCII, 0)], [("Corpo", ASCII + "àèéìòù")]))])})
    ammessi, motivo = font_unity.caratteri_ammessi(os.path.join(dinamico, "Gioco_Data"))
    controlla("font dinamico: valgono anche i caratteri del suo .ttf (è si disegna)",
              ammessi is not None and "è" in ammessi and "dinamici" in motivo)
    senza_ttf = os.path.join(base, "senza-ttf")
    finto.gioco_il2cpp(senza_ttf, {"fonts_assets_all.bundle": finto.bundle([("CAB-d", finto.file_font(
        [("Corpo SDF", "", 1), ("Pixel SDF", ASCII, 0)]))])})
    vuoto = os.path.join(base, "senza-font")
    finto.gioco_il2cpp(vuoto, {TABELLA_EN: finto.bundle_tabelle([("UI_en", "en", EN)])})
    controlla("non si sa (nessun limite): font dinamici senza .ttf, nessun font TextMeshPro",
              font_unity.caratteri_ammessi(os.path.join(senza_ttf, "Gioco_Data"))[0] is None
              and font_unity.caratteri_ammessi(os.path.join(vuoto, "Gioco_Data"))[0] is None)
    # il risultato resta nel file della cache finche' i file del gioco non cambiano
    cache = os.path.join(base, "cache", "font.json")
    primo = font_unity.caratteri_ammessi(dati_gioco, cache=cache)
    vero = font_unity.font_del_gioco
    font_unity.font_del_gioco = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("letti di nuovo"))
    try:
        secondo = font_unity.caratteri_ammessi(dati_gioco, cache=cache)
        riletto = True
    except AssertionError:
        secondo, riletto = None, False
    finally:
        font_unity.font_del_gioco = vero
    bundle = os.path.join(dati_gioco, "StreamingAssets", "aa", "StandaloneWindows64", "ui_assets_all.bundle")
    with open(bundle, "wb") as f:  # un aggiornamento del gioco: il font ora ha anche è
        f.write(finto.bundle([("CAB-f", finto.file_font([("Titoli SDF", ASCII + "üè", 0)]))]))
    terzo = font_unity.caratteri_ammessi(dati_gioco, cache=cache)
    controlla("cache: la seconda volta non rilegge i font; dopo un aggiornamento del gioco si, e ora è va bene",
              riletto and secondo == primo and "è" not in primo[0] and terzo[0] is not None and "è" in terzo[0]
              and leggi_json(cache)["intervalli"])


def _memoria_prova(profilo):
    return progetto.percorso_memoria(leggi_json(profilo), prova=True)


def prova_unity_localization(base):
    font = finto.bundle([("CAB-f", finto.file_font([("Testi SDF", ASCII + "é", 0)]))])
    gioco = os.path.join(base, "loc")
    cartella = finto.gioco_il2cpp(gioco, {TABELLA_EN: finto.bundle_tabelle([("UI_en", "en", EN)]),
                                          TABELLA_FR: finto.bundle_tabelle([("UI_fr", "fr", FR)]),
                                          "fonts_assets_all.bundle": font})
    fr_prima = impronta(os.path.join(cartella, TABELLA_FR))
    profilo, _completo, e = automatico.analizza_e_traduci(gioco, "en", "it", backend="finto", log=silenzio)
    nome = leggi_json(profilo)["nome"]
    cache = progetto.cartella_dati("memory", "%s.unity" % nome, "font.json")
    controlla("Unity Localization: traduzione senza accenti scritta com'e'; font letti e tenuti in font.json",
              e["esito"].codice == "OK" and os.path.exists(cache) and [t for _, t in adapter_unity_loc.FileTabelle(
                  os.path.join(cartella, TABELLA_EN)).testi()] == ["[it] " + t for t in EN])
    memoria = _memoria_prova(profilo)
    dati = leggi_json(memoria)
    dati.update({EN[3]: "La porta è chiusa. Trova prima la chiave, perché serve.", EN[4]: "Esci → desktop"})
    scrivi_json(memoria, dati)
    righe = []
    d = {}
    automatico.riapplica(gioco, righe.append, prova=True, esito=d)
    testi = [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella, TABELLA_EN)).testi()]
    controlla("font senza è: nella tabella è diventa e' (é resta: il font ce l'ha); in memoria resta è; la tabella "
              "francese non si tocca", testi[3] == "La porta e' chiusa. Trova prima la chiave, perché serve."
              and leggi_json(memoria)[EN[3]].startswith("La porta è") and impronta(os.path.join(cartella, TABELLA_FR))
              == fr_prima and any("non hanno" in r and "è" in r and "é" not in r for r in righe))
    problemi = leggi_json(progetto.percorso_problemi(leggi_json(profilo), prova=True))
    controlla("un carattere che non si puo' adattare (→): resta l'originale, in problemi.json con il carattere da "
              "non usare; l'esito lo dice (testo adattato e traduzione scartata)",
              testi[4] == EN[4] and problemi.get(EN[4], {}).get("dove") == "gioco"
              and "→" in problemi[EN[4]].get("non_usare", "") and d["esito"].extra.get("adattate_font") == 1
              and "è diventa e'" in "\n".join(esito._testo_tentativi(d["esito"])))
    # caratteri che il gioco ha detto di non saper disegnare (registro di Unity): valgono anche se il font li ha
    p = leggi_json(profilo)
    p["caratteri_mancanti"] = "é"
    scansione.salva_json(profilo, p)
    automatico.riapplica(gioco, silenzio, prova=True, esito={})
    testi = [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella, TABELLA_EN)).testi()]
    controlla("caratteri mancanti nel profilo: adattati anche se un font li ha (perché -> perche')",
              testi[3] == "La porta e' chiusa. Trova prima la chiave, perche' serve.")
    # con un font che ha tutte le lettere: nessun cambiamento
    buono = os.path.join(base, "loc-buono")
    cartella_b = finto.gioco_il2cpp(buono, {TABELLA_EN: finto.bundle_tabelle([("UI_en", "en", EN)]),
                                            "fonts_assets_all.bundle": finto.bundle([("CAB-f", finto.file_font(
                                                [("Testi SDF", ASCII + "àèéìòù", 0)]))])})
    profilo_b, _c, _e = automatico.analizza_e_traduci(buono, "en", "it", backend="finto", log=silenzio)
    memoria_b = _memoria_prova(profilo_b)
    dati = leggi_json(memoria_b)
    dati[EN[3]] = "La porta è chiusa. Trova prima la chiave, perché serve."
    scrivi_json(memoria_b, dati)
    automatico.riapplica(buono, silenzio, prova=True, esito={})
    controlla("font con tutte le lettere italiane: la traduzione resta com'e'",
              [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella_b, TABELLA_EN)).testi()][3]
              == dati[EN[3]])
    # un errore imprevisto nella lettura dei font non ferma la scrittura: nessun limite, come prima
    vero = font_unity.caratteri_ammessi
    font_unity.caratteri_ammessi = lambda *_a, **_k: (_ for _ in ()).throw(KeyError("bundle strano"))
    d = {}
    try:
        fatto = automatico.riapplica(buono, silenzio, prova=True, esito=d)
    except KeyError:
        fatto = False
    finally:
        font_unity.caratteri_ammessi = vero
    controlla("errore imprevisto nella lettura dei font: le traduzioni si scrivono lo stesso, senza limiti",
              fatto and d["esito"].codice == "OK" and [t for _, t in adapter_unity_loc.FileTabelle(
                  os.path.join(cartella_b, TABELLA_EN)).testi()][3] == dati[EN[3]])


# --- XUnity: registro di BepInEx e controllo dopo aver giocato ------------------------------------------------------

def _gioco_unity_mono(cartella, nome_exe, azienda, prodotto):
    import struct
    dati = os.path.join(cartella, nome_exe + "_Data")
    os.makedirs(os.path.join(dati, "Managed"))
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    with open(os.path.join(cartella, nome_exe + ".exe"), "wb") as f:
        f.write(bytes(pe))
    with open(os.path.join(dati, "globalgamemanagers"), "wb") as f:
        f.write(b"\x00" * 48 + b"2022.3.10f1\x00")
    with open(os.path.join(dati, "Managed", "Assembly-CSharp.dll"), "wb") as f:
        f.write(b"MZ" + "Start a new game".encode("utf-16-le") + b"\x00\x00")
    scrivi(os.path.join(dati, "app.info"), "%s\n%s" % (azienda, prodotto))


def _componenti_finti(cartella):
    import zipfile

    def _zip(percorso, voci):
        with zipfile.ZipFile(percorso, "w") as z:
            for nome, contenuto in voci.items():
                z.writestr(nome, contenuto)
        with open(percorso, "rb") as f:
            dati = f.read()
        return {"url": "file://non-usato", "file": os.path.basename(percorso), "dimensione": len(dati),
                "sha256": hashlib.sha256(dati).hexdigest(), "versione": "prova", "licenza": "-"}
    componenti.CARTELLA = cartella
    os.makedirs(cartella, exist_ok=True)
    componenti.CATALOGO = {
        "bepinex5_x64": _zip(os.path.join(cartella, "bep.zip"), {
            "winhttp.dll": b"proxy", "BepInEx/core/BepInEx.dll": b"core"}),
        "xunity_bepinex5": _zip(os.path.join(cartella, "xu.zip"), {
            "BepInEx/plugins/XUnity.AutoTranslator/XUnity.AutoTranslator.Plugin.BepInEx.dll": b"plugin"})}


def prova_registro(base):
    trovati = controllo.caratteri_mancanti([os.path.join(SEGNALI, "unity-tmp-caratteri-mancanti.log")])
    controlla("registro vero: caratteri mancanti nei font (anche fuori dal piano base) con il nome del font",
              set(trovati) == {"\u00d9", "\u2011", "\U0001F41A", "\u10e6"} and trovati["\u00d9"] == {"slkscr"})
    vecchio = os.path.join(base, "vecchio.log")
    scrivi(vecchio, "Character with ASCII value of 232 was not found in the Font Asset Glyph Table.\n")
    controlla("registro di TextMeshPro vecchio: 'ASCII value of 232' -> è",
              set(controllo.caratteri_mancanti([vecchio])) == {"è"})
    controlla("lettere mancanti sostituite solo con caratteri semplici: è -> e', ò -> o' (non ó: del font non si sa), "
              "trattino ‑ -> '-'; un'emoji non si puo' sostituire",
              caratteri.adatta_senza("Può è pre‑ordine", {"ò", "è", "‑"}, "it") == "Puo' e' pre-ordine"
              and caratteri.adatta_senza("ok \U0001F41A", {"\U0001F41A"}, "it") is None
              and caratteri.adatta_senza("niente", {"è"}, "it") == "niente")
    copia = os.path.join(base, "copia")
    testo = open(os.path.join(SEGNALI, "unity-tmp-caratteri-mancanti.log"), encoding="utf-8").read()
    controlla("copia del gioco riconosciuta anche senza 'Mono path' (giochi IL2CPP: 'Discovering subsystems')",
              controllo.di_questa_copia(testo.replace("C:/Giochi/Cast n Chill",
                                                      copia.replace("\\", "/")), copia) is True
              and controllo.di_questa_copia(testo, copia) is False)

    nome = "pixel-prova"
    gioco = os.path.join(base, "giochi", "Pixel")
    _gioco_unity_mono(gioco, "Pixel", "Studio Pixel", "Pixel")
    percorso = os.path.join(automatico.cartella_profili(), nome + ".json")
    scansione.salva_json(percorso, {"nome": nome, "cartella_gioco": gioco.replace(os.sep, "/"), "motore": "unity",
                                    "lingua_origine": "en", "lingua_destinazione": "it", "backend": "google",
                                    "dal_vivo": "google", "file": []})
    adapter_unity.installa(gioco, "en", "it", nome, silenzio, locale=False)
    cfg = os.path.join(gioco, *adapter_unity.CONFIG_BEPINEX.split("/"))
    controlla("installazione: BepInEx scrive nel suo registro anche i messaggi di Unity (WriteUnityLog = true)",
              adapter_unity.registro_unity_attivo(gioco) and "WriteUnityLog = true" in open(cfg).read())
    prima = time.time() - 600
    dopo = time.time() - 120
    for rel in (adapter_unity.MANIFESTO,):
        os.utime(os.path.join(gioco, *rel.split("/")), (prima, prima))
    # il primo avvio: BepInEx completa la sua configurazione (tiene il valore scritto da PatchSmith)
    scrivi(cfg, "[Logging.Disk]\n\n## Include unity log messages in log file output.\nWriteUnityLog = true\n\n"
                "AppendLog = false\n", prima)
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    nostre = {"The door is locked": "La porta è chiusa", "Hand line": "Riga è nostra", "Start": "Inizia"}
    scrivi_json(memoria, nostre)
    scrivi_json(memoria[:-len(".json")] + ".automatiche.json", nostre)
    cache = os.path.join(gioco, "BepInEx", "Translation", "it", "Text", "_AutoGeneratedTranslations.txt")
    scrivi(cache, "The door is locked=La porta è chiusa\nHand line=Riga è cambiata a mano\nStart=Inizia\n", prima)
    log = os.path.join(gioco, *adapter_unity.LOG_BEPINEX.split("/"))
    scrivi(log, "[Info   :   BepInEx] Chainloader startup complete\n[Warning: Unity Log] The character with Unicode "
                "value \\u00E8 was not found in the [Pixel SDF] font asset or any potential fallbacks. It was replaced "
                "by Unicode character \\u25A1 in text object [Text (TMP)].\n", dopo)
    tutto = istantanea(base)
    problemi = controllo.controlla(percorso, applica=False, log=silenzio)
    font = next((p for p in problemi if p.codice == "FONT_MANCANTI"), None)
    controlla("controllo in sola lettura: lettere mancanti trovate (si correggerebbero da sole), nulla scritto",
              font is not None and font.stato == "da_correggere" and "«è»" in font.messaggio
              and "Una frase con quelle lettere" in font.messaggio and istantanea(base) == tutto
              and "caratteri_mancanti" not in leggi_json(percorso))
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    font = next((p for p in problemi if p.codice == "FONT_MANCANTI"), None)
    righe = open(cache, encoding="utf-8").read()
    controlla("correzione: la riga scritta da PatchSmith adattata (è -> e'), quella cambiata a mano no, caratteri "
              "annotati nel profilo; in memoria resta è",
              font is not None and font.stato == "corretto" and "The door is locked=La porta e' chiusa" in righe
              and "Hand line=Riga è cambiata a mano" in righe and leggi_json(percorso)["caratteri_mancanti"] == "è"
              and leggi_json(memoria)["The door is locked"] == "La porta è chiusa")
    problemi = controllo.controlla(percorso, applica=True, log=silenzio)
    controlla("secondo controllo: nulla da rifare", not any(p.codice == "FONT_MANCANTI" for p in problemi))
    # da adesso: le righe nuove della cache e le risposte del traduttore dal vivo evitano è
    adapter_unity.scrivi_cache_xunity(gioco, "it", {"New line": "Novità è qui"}, silenzio,
                                      esclusi=leggi_json(percorso)["caratteri_mancanti"])
    s = servizio_xunity.Servizio(percorso, porta=1, log=silenzio, backend=backend.Finto())
    nostre_ora, _ = automatico.valori_nostri(leggi_json(percorso), leggi_json(memoria), {})
    controlla("da adesso: nuove righe della cache e risposte dal vivo senza è (é, à e ì restano); la riga adattata "
              "e' riconosciuta come scritta da PatchSmith",
              "New line=Novità e' qui" in open(cache, encoding="utf-8").read()
              and s.adatta("Perché è così", "it") == "Perché e' così"
              and "La porta e' chiusa" in nostre_ora["The door is locked"])
    # una correzione fatta in "Correggi traduzioni" con una lettera che i font non hanno: con "Riapplica" va nella
    # cache adattata, e conta come scritta (non "lasciata com'era")
    dati = leggi_json(memoria)
    dati["Quit"] = "Esci è"
    scrivi_json(memoria, dati)
    correzioni.segna_da_scrivere(leggi_json(percorso), {"Quit": (None, "Esci è")})
    righe_log, d = [], {}
    automatico.riapplica(gioco, righe_log.append, esito=d)
    controlla("Riapplica con le lettere mancanti: la correzione a mano scritta adattata nella cache e contata come "
              "scritta", "Quit=Esci e'" in open(cache, encoding="utf-8").read()
              and d["esito"].extra.get("correzioni_scritte") == 1
              and not any("lasciate com'erano" in r for r in righe_log))
    # un errore imprevisto nel controllo dei font non nasconde gli altri problemi del gioco
    vero = adapter_unity.voci_cache_xunity
    adapter_unity.voci_cache_xunity = lambda *_a, **_k: (_ for _ in ()).throw(KeyError("cache strana"))
    scrivi(log, "[Info   :   BepInEx] Chainloader startup complete\n[Warning: Unity Log] The character with Unicode "
                "value \\u00F9 was not found in the [Pixel SDF] font asset or any potential fallbacks.\n")
    scrivi(cfg, "[Logging.Disk]\nWriteUnityLog = false\n")
    righe_log = []
    try:
        problemi = controllo.controlla(percorso, applica=False, log=righe_log.append)
        ok = True
    except KeyError:
        problemi, ok = [], False
    finally:
        adapter_unity.voci_cache_xunity = vero
    controlla("errore imprevisto nel controllo delle lettere: solo una riga nel registro, gli altri problemi restano",
              ok and any(p.codice == "CONFIGURAZIONE" for p in problemi)
              and any("lettere dei font non riuscito" in r for r in righe_log))
    # una configurazione di prima (WriteUnityLog spento): il controllo la rimette
    scrivi(cfg, "[Logging.Disk]\nWriteUnityLog = false\n")
    problemi = controllo.controlla(percorso, applica=False, log=silenzio)
    conf = next((p for p in problemi if p.codice == "CONFIGURAZIONE"), None)
    controlla("installazione di prima con WriteUnityLog spento: il controllo lo riaccende (CONFIGURAZIONE)",
              conf is not None and "WriteUnityLog" in conf.dettaglio and "lettere mancanti" in conf.messaggio)
    # BepInEx.cfg con byte non UTF-8 (un commento scritto a mano): si accende lo stesso, il resto resta uguale
    with open(cfg, "wb") as f:
        f.write(b"[General]\n# commento \xe8 a mano\n\n[Logging.Disk]\nWriteUnityLog = false\n")
    cambiato = adapter_unity.attiva_registro_unity(gioco)
    with open(cfg, "rb") as f:
        grezzo = f.read()
    controlla("BepInEx.cfg con byte non UTF-8: WriteUnityLog acceso, il commento resta byte per byte",
              cambiato and b"# commento \xe8 a mano" in grezzo and b"WriteUnityLog = true" in grezzo
              and b"WriteUnityLog = false" not in grezzo)


def prova_registro_localizzazione(base):
    """Unity Localization (IL2CPP, senza BepInEx): le lettere mancanti dal Player.log, poi Riapplica."""
    gioco = os.path.join(base, "loc-registro")
    cartella = finto.gioco_il2cpp(gioco, {TABELLA_EN: finto.bundle_tabelle([("UI_en", "en", EN)])})
    profilo, _c, _e = automatico.analizza_e_traduci(gioco, "en", "it", backend="finto", log=silenzio)
    p = leggi_json(profilo)
    memoria = progetto.percorso_memoria(p)  # la memoria vera (di prova: nella cartella dei dati di prova)
    scrivi_json(memoria, {EN[0]: "Nuova partita", EN[1]: "Può continuare", EN[2]: "Opzioni"})
    automatico.riapplica(gioco, silenzio, esito={})
    vecchio = time.time() - 600
    for n in os.listdir(cartella):
        os.utime(os.path.join(cartella, n), (vecchio, vecchio))
    testo = open(os.path.join(SEGNALI, "unity-tmp-caratteri-mancanti.log"), encoding="utf-8").read()
    testo = testo.replace("C:/Giochi/Cast n Chill/castnchill_Data",
                          os.path.join(gioco, "Gioco_Data").replace("\\", "/"))
    testo += ("The character with Unicode value \\u00F2 was not found in the [Testi SDF] font asset or any potential "
              "fallbacks. It was replaced by Unicode character \\u25A1 in text object [Text (TMP)].\n")
    registro = os.path.join(os.environ["USERPROFILE"], "AppData", "LocalLow", "Studio Finto", "Gioco Finto",
                            "Player.log")
    scrivi(registro, testo, time.time() - 60)
    controlla("Player.log nei segnali dei giochi con Unity Localization (il controllo all'apertura lo vede)",
              "unity:Player.log" in controllo.segnali(profilo))
    problemi = controllo.controlla(profilo, applica=True, log=silenzio)
    font = next((x for x in problemi if x.codice == "FONT_MANCANTI"), None)
    testi = [t for _, t in adapter_unity_loc.FileTabelle(os.path.join(cartella, TABELLA_EN)).testi()]
    controlla("Unity Localization: ò mancante annotato nel profilo e tabella riscritta (Può -> Puo'); i caratteri che "
              "ci sono solo nei testi del gioco (Ù, ღ) non contano",
              font is not None and font.stato == "corretto" and "«ò»" in font.messaggio and "Ù" not in font.messaggio
              and "ò" in leggi_json(profilo)["caratteri_mancanti"] and testi[1] == "Puo' continuare")


# --- Unreal --------------------------------------------------------------------------------------------------------

def test_unreal(base):
    from test_unreal import locmeta, locres_v2, pak_v9
    voci = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit"), ("UI", "Next", "Next -> page")]

    def gioco(nome, altre):
        g = os.path.join(base, nome)
        paks = os.path.join(g, "Proj", "Content", "Paks")
        os.makedirs(paks)
        file = [("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
                ("Proj/Content/Localization/Game/en/Game.locres", locres_v2(voci))]
        file += [("Proj/Content/Localization/Game/%s/Game.locres" % c, locres_v2([("UI", "Start", t)]))
                 for c, t in altre]
        with open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb") as f:
            f.write(pak_v9(file))
        return g, paks
    g, paks = gioco("unreal-fr", [("fr", "Démarrer"), ("ja", "ゲーム開始")])
    ammessi = adapter_unreal.caratteri_ammessi(g, set("Start Game"), {"en"})
    controlla("Unreal: con il francese (é) il font ha il Latino-1 (anche ì, ò); il giapponese non conta",
              ammessi is not None and set("àèìòù") <= ammessi and "ゲ" not in ammessi and "→" not in ammessi)
    memoria = {"Start Game": "Inizia la partita è ò", "Quit": "Esci", "Next -> page": "Pagina → successiva ★"}
    respinte = {}
    adapter_unreal.installa(g, "it", "prova-ue", memoria, silenzio, respinte=respinte)
    letto = adapter_unreal.Pak(os.path.join(paks, adapter_unreal.nome_pak("it")))
    testi = [s for s, _ in adapter_unreal.leggi_locres(letto.leggi(
        "Proj/Content/Localization/Game/en/Game.locres"))["stringhe"]]
    controlla("Unreal: lettere italiane scritte; un carattere che nessuna lingua del gioco usa e non si puo' "
              "adattare (★) lascia l'originale e torna a chi chiama (problemi.json)",
              "Inizia la partita è ò" in testi and "Next -> page" in testi and "★" in respinte.get("Next -> page", ""))
    g2, _ = gioco("unreal-en", [("de", "Spiel starten")])
    controlla("Unreal: solo inglese, o altre lingue senza lettere accentate: nessun indizio, nessun limite",
              adapter_unreal.caratteri_ammessi(g2, set("Start Game"), {"en"}) is None
              and adapter_unreal._adatta("Novità ★", set(), None, "it") == "Novità ★")


# --- etichette troppo lunghe -----------------------------------------------------------------------------------

LUNGHE = {"Credits": "Riconoscimenti", "Main Menu": "Menu principale del gioco", "Settings": "Impostazioni",
          "Open the chest to find the gold": "Apri il forziere per trovare tutto l'oro", "Back": "Indietro",
          "Quest Log": "Diario delle missioni"}
CORTE = {"Credits": "Crediti", "Main Menu": "Menu principale gioco", "Quest Log": "Missioni"}


class Etichette(backend.Backend):
    """Traduttore finto che sa di uno spazio massimo (come quello locale): lungo la prima volta, corto se lo chiede
    il contesto 'larghezza'."""
    nome = "prova-etichette"
    usa_contesto = True
    max_testi = 40

    def __init__(self):
        self.richieste = []

    def traduci_righe(self, righe, origine, destinazione, contesti=None, giri=None):
        uscita = []
        for i, r in enumerate(righe):
            c = (contesti or [None] * len(righe))[i] or {}
            self.richieste.append((r, c.get("larghezza")))
            uscita.append(CORTE.get(r, LUNGHE.get(r, "IT " + r)) if c.get("larghezza") else LUNGHE.get(r, "IT " + r))
        return uscita


class SenzaContesto(backend.Backend):
    nome = "prova-online"

    def __init__(self):
        self.richieste = []

    def traduci_righe(self, righe, origine, destinazione):
        self.richieste += righe
        return [LUNGHE.get(r, "IT " + r) for r in righe]


def _profilo(base, nome):
    percorso = os.path.join(base, nome + ".json")
    scrivi_json(percorso, {"nome": nome, "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
                           "file": []})
    return percorso


def _senza_comune():
    """Come un PC nuovo: senza la memoria comune (le prove prima ci hanno messo 'Credits' -> 'Crediti')."""
    for p in (progetto.percorso_comune("en", "it"), progetto.percorso_origine_comune("en", "it")):
        if os.path.exists(p):
            os.remove(p)


def prova_lunghezza(base):
    controlla("spazio di un'etichetta: con le traduzioni ufficiali (o lo spazio del gioco) 1,3 volte l'originale + 2 "
              "o la piu' lunga di quelle; senza, solo i casi evidenti (doppio e 7 caratteri in piu': 'Back' 10, "
              "'Credits' 13); frasi lunghe e testi su piu' righe esclusi",
              progetto.spazio_etichetta("Back") == 10 and progetto.spazio_etichetta("Credits") == 13
              and progetto.spazio_etichetta("Credits", ["Crédits"]) == 12
              and progetto.spazio_etichetta("Credits", ["Mitwirkende und Danksagungen"]) == 28
              and progetto.spazio_etichetta("Credits", (), 20) == 20 and progetto.etichetta_corta("Main Menu")
              and not progetto.etichetta_corta("Open the chest to find the gold")
              and not progetto.etichetta_corta("Two\nlines"))
    percorso = _profilo(base, "etichette")
    p = Progetto(percorso, silenzio)
    b = Etichette()
    testi = list(LUNGHE)
    p.traduci_testi(testi, b, "en", "it")
    chieste = [(t, n) for t, n in b.richieste if n]
    problemi = p.problemi()
    automatiche = leggi_json(p._percorso_automatiche())
    controlla("etichette troppo lunghe chieste di nuovo con il numero massimo di caratteri (non le altre, non la "
              "frase lunga): resta la piu' corta valida, anche tra le traduzioni automatiche",
              sorted(chieste) == [("Credits", 13), ("Main Menu", 17), ("Quest Log", 17)]
              and p.memoria["Credits"] == "Crediti" and p.memoria["Quest Log"] == "Missioni"
              and automatiche["Credits"] == "Crediti" and p.memoria["Back"] == "Indietro"
              and p.memoria["Settings"] == "Impostazioni" and p.accorciate == 3)
    extra = automatico._extra_tentativi(p)
    controlla("ancora troppo lunga: in problemi.json con il testo originale come chiave ('lunghezza', con la "
              "traduzione e lo spazio); l'esito conta le accorciate e quella annotata adesso",
              extra.get("accorciate") == 3 and extra.get("troppo_lunghe") == 1
              and problemi.get("Main Menu", {}).get("dove") == "lunghezza"
              and problemi["Main Menu"].get("valore") == "Menu principale gioco" and problemi["Main Menu"]["spazio"] == 17
              and "Credits" not in problemi and "Crediti" not in json.dumps(problemi))
    b2 = Etichette()
    p2 = Progetto(percorso, silenzio)
    p2.traduci_testi(testi, b2, "en", "it")
    controlla("una volta sola: la volta dopo nessuna richiesta (la troppo lunga resta annotata, e l'esito non la "
              "ridice a ogni traduzione)", b2.richieste == [] and "troppo_lunghe" not in automatico._extra_tentativi(p2)
              and Progetto(percorso, silenzio).problemi()["Main Menu"]["dove"] == "lunghezza")
    # scritta nel gioco resta annotata finche' la traduzione e' quella
    p = Progetto(percorso, silenzio)
    p._respinte, p._applicati = {}, {"Main Menu", "Credits"}
    p._registra_applica()
    controlla("scritta nel gioco: resta nell'elenco finche' la traduzione non cambia",
              p.problemi()["Main Menu"]["dove"] == "lunghezza")
    elenco = correzioni.carica(percorso)
    voce = next(x for x in elenco.voci if x.testo == "Main Menu")
    controlla("'Correggi traduzioni': nell'elenco 'Troppo lunghi' con il testo originale",
              "lunghi" in voce.problemi and "troppo lunga" in voce.problemi["lunghi"]
              and voce in elenco.filtra("lunghi"))
    # corretta a mano: non si chiede di nuovo
    p = Progetto(percorso, silenzio)
    p.correggi({"Back": "Torna indietro alla schermata"})
    b3 = Etichette()
    Progetto(percorso, silenzio).traduci_testi(testi, b3, "en", "it")
    controlla("una correzione a mano lunga non si chiede di nuovo al traduttore", b3.richieste == [])
    # traduttori che non sanno di uno spazio massimo: solo annotate, nessuna richiesta in piu'
    _senza_comune()
    percorso2 = _profilo(base, "etichette-online")
    online = SenzaContesto()
    p2 = Progetto(percorso2, silenzio)
    p2.traduci_testi(testi, online, "en", "it")
    controlla("con un traduttore online: nessuna richiesta in piu', le etichette lunghe annotate per 'Troppo lunghi'",
              sorted(online.richieste) == sorted(testi) and p2.problemi().get("Credits", {}).get("dove") == "lunghezza")
    # traduzioni ufficiali piu' lunghe: lo spazio c'e'
    percorso3 = _profilo(base, "etichette-ufficiali")
    b4 = Etichette()
    Progetto(percorso3, silenzio).traduci_testi(["Credits"], b4, "en", "it", contesti={
        "Credits": {"chiave": "menu_credits", "riferimenti": {"de": "Mitwirkende Personen"}}})
    controlla("con una traduzione ufficiale piu' lunga ('Mitwirkende Personen') 'Riconoscimenti' ci sta",
              [n for _, n in b4.richieste if n] == [])
    # il prompt vero del traduttore locale
    loc = backend.Locale("4b", log=silenzio, server=object())
    controlla("traduttore locale: il numero massimo di caratteri nel prompt solo quando lo si chiede",
              "at most 12 characters" in loc._testo_contesto({"larghezza": 12}, "it")
              and "at most" not in loc._testo_contesto({"chiave": "menu", "spazio": 12}, "it"))
    # l'esito
    e = esito.Esito(codice="OK", extra={"accorciate": 2, "troppo_lunghe": 1})
    testo = "\n".join(esito._testo_tentativi(e))
    controlla("esito: etichette accorciate e quella ancora lunga, con 'Correggi traduzioni' > 'Troppo lunghi'",
              "2 etichette erano troppo lunghe per lo spazio che hanno" in testo
              and "Un'etichetta potrebbe uscire" in testo
              and esito.da_correggere(e) and esito.filtro_correzioni(e) == "lunghi")
    uno = "\n".join(esito._testo_tentativi(esito.Esito(codice="OK", extra={"accorciate": 1})))
    controlla("esito con un'etichetta sola: 'lo spazio che ha nel gioco' (non 'che hanno')",
              "Un'etichetta era troppo lunga per lo spazio che ha nel gioco: l'ho chiesta più corta." in uno)
    controlla("'Correggi traduzioni': il motivo si legge bene (caratteri e spazio)",
              "troppo lunga: 21 caratteri, di solito ce ne stanno circa 17" in voce.problemi["lunghi"])
    # annotate con un traduttore online, poi con quello locale: chieste piu' corte una volta
    _senza_comune()
    percorso4 = _profilo(base, "etichette-poi-locale")
    Progetto(percorso4, silenzio).traduci_testi(testi, SenzaContesto(), "en", "it")
    b5 = Etichette()
    p5 = Progetto(percorso4, silenzio)
    p5.traduci_testi(testi, b5, "en", "it")
    b6 = Etichette()
    Progetto(percorso4, silenzio).traduci_testi(testi, b6, "en", "it")
    controlla("annotate con un traduttore online: con il traduttore locale si chiedono piu' corte, una volta sola",
              sorted(t for t, n in b5.richieste if n) == ["Credits", "Main Menu", "Quest Log"]
              and p5.memoria["Credits"] == "Crediti" and b6.richieste == []
              and Progetto(percorso4, silenzio).problemi()["Main Menu"].get("chiesta") is True)
    # "Ritraduci" di un'etichetta dell'elenco "Troppo lunghi": chiesta con il suo spazio
    b7 = Etichette()
    p7 = Progetto(percorso4, silenzio)
    p7.ritraduci(["Main Menu"], b7, "en", "it", giro=3)
    controlla("'Ritraduci' di un'etichetta troppo lunga: chiesta con il suo spazio (17 caratteri)",
              b7.richieste == [("Main Menu", 17)])
    # un errore imprevisto mentre si accorciano le etichette non ferma la traduzione (e' un di piu')

    class Rotto(Etichette):
        def traduci_righe(self, righe, origine, destinazione, contesti=None, giri=None):
            if any((c or {}).get("larghezza") for c in contesti or []):
                return [object()] * len(righe)  # risposte senza senso: errore nel ricomporle
            return super().traduci_righe(righe, origine, destinazione, contesti, giri)
    _senza_comune()
    righe_log = []
    p8 = Progetto(_profilo(base, "etichette-errore"), righe_log.append)
    rimasti = p8.traduci_testi(testi, Rotto(), "en", "it")
    controlla("errore imprevisto mentre si accorciano le etichette: traduzione finita, traduzioni salvate, una riga "
              "nel registro", rimasti == 0 and p8.memoria["Credits"] == "Riconoscimenti"
              and any("etichette piu' corte non riuscite" in r for r in righe_log))


class ServerPrompt:
    """Al posto di ollama.Server: risponde in base al prompt (lungo, o corto se chiede 'at most N characters')."""

    def __init__(self):
        self.prompt = []

    def avvia(self):
        pass

    def chiama(self, percorso, dati=None, timeout=180):
        if percorso == "/api/chat":
            prompt = dati["messages"][0]["content"]
            testo = prompt.split("\n\n\n", 1)[1]
            self.prompt.append(prompt)
            corta = "at most" in prompt
            risposta = {"Credits": ("Crediti" if corta else "Riconoscimenti")}.get(testo, "IT " + testo)
            return {"message": {"content": risposta}, "done_reason": "stop", "eval_count": 5,
                    "eval_duration": 100_000_000, "prompt_eval_count": 50}
        if percorso == "/api/ps":
            return {"models": []}
        return {}

    def chiudi(self):
        pass


def prova_lunghezza_locale(base):
    _senza_comune()
    server = ServerPrompt()
    b = backend.Locale("4b", log=silenzio, server=server)
    p = Progetto(_profilo(base, "etichette-locale"), silenzio)
    p.traduci_testi(["Credits", "Play"], b, "en", "it")
    controlla("traduttore locale vero (server finto): 'Credits' chiesto di nuovo con il massimo di 13 caratteri nel "
              "prompt, resta 'Crediti'", p.memoria["Credits"] == "Crediti"
              and sum("at most 13 characters" in x for x in server.prompt) == 1)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    for variabile, nome in (("TEMP", "temp"), ("TMP", "temp"), ("USERPROFILE", "utente"),
                            ("LOCALAPPDATA", "appdata"), ("APPDATA", "appdata-roaming")):
        os.environ[variabile] = os.path.join(base, nome)
        os.makedirs(os.environ[variabile], exist_ok=True)
    progetto.RADICE_DATI = os.path.join(base, "dati")
    os.environ["PATCHSMITH_DATI"] = progetto.RADICE_DATI
    for d in ("memory", "profiles"):
        os.makedirs(os.path.join(progetto.RADICE_DATI, d), exist_ok=True)
    automatico.CARTELLA_PROFILI = None
    adapter_unity.in_esecuzione = lambda *_: False
    from patchsmith import ollama
    ollama.cartella = lambda: os.path.join(base, "ollama")  # registro del traduttore dal vivo di prova
    _componenti_finti(os.path.join(base, "componenti"))
    for nome, prova in (("font", prova_font), ("loc", prova_unity_localization), ("registro", prova_registro),
                        ("registro-loc", prova_registro_localizzazione), ("unreal", test_unreal),
                        ("lunghezza", prova_lunghezza), ("lunghezza-locale", prova_lunghezza_locale)):
        cartella = os.path.join(base, nome)
        os.makedirs(cartella)
        try:
            prova(cartella)
        except Exception as e:  # noqa: BLE001 - una prova rotta non ferma le altre
            import traceback
            traceback.print_exc()
            controlla("%s: errore imprevisto (%s: %s)" % (nome, type(e).__name__, e), False)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
