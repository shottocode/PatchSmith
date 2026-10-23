"""Test della lingua aggiunta (GameMaker, stile Forager) senza rete e senza il gioco vero.

Uso: python -I test/test_added_language.py <cartella_temporanea_vuota>
Costruisce un gioco finto: data.win minimo (GEN8 con il nome, STRG con la maschera 'local/*.json' e la mappa di un
font a sprite, SPRT con lo sprite del font), local/english.json in minuscolo con le icone 'EP' e 'H', traduzioni
ufficiali, e le opzioni salvate dal gioco (%LOCALAPPDATA%\\<nome>\\config.txt, ds_map in esadecimale).
"""
import hashlib
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_gamemaker, auto as automatico, gamemaker, language_gamemaker as lingua_gamemaker,
                        official_language as lingua_ufficiale, project as progetto)  # noqa
from patchsmith import system as sistema  # noqa: E402

ESITI = []
MAPPA = "?!0123456789aàbcdeèéfghiíjklmnoópqrstuùúvwxyz/.:,_-'%X()+CHTEPUR"


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronta(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# --- data.win minimo -------------------------------------------------------------------------------------------

def data_win(stringhe, sprite, codice=True, nome="FintoGame"):
    """FORM con GEN8 (nome), STRG (stringhe), SPRT (sprite: [(nome, fotogrammi)]) e, se 'codice', un CODE vuoto
    (gioco per la macchina virtuale: le stringhe del codice stanno in STRG)."""
    tutte = [nome] + list(stringhe) + [n for n, _ in sprite]
    # i pezzi si costruiscono con le posizioni assolute: prima calcolo dove stara' STRG
    gen8_dim, sprt_dim = 64, 4 + 4 * len(sprite) + 96 * len(sprite) + 64
    inizio_gen8 = 8 + 8
    inizio_sprt = inizio_gen8 + gen8_dim + 8
    inizio_code = inizio_sprt + sprt_dim + 8
    inizio_strg = inizio_code + (0 if not codice else 4 + 8) + (8 if not codice else 0)
    if not codice:
        inizio_strg = inizio_sprt + sprt_dim + 8
    # STRG: numero, puntatori (alla lunghezza), stringhe (lunghezza + testo + 0)
    corpo, puntatori, pos = b"", [], inizio_strg + 4 + 4 * len(tutte)
    testo_di = {}
    for t in tutte:
        b = t.encode("utf-8")
        puntatori.append(pos + len(corpo))
        testo_di[t] = pos + len(corpo) + 4
        corpo += struct.pack("<I", len(b)) + b + b"\x00"
    strg = struct.pack("<I", len(tutte)) + struct.pack("<%dI" % len(tutte), *puntatori) + corpo
    gen8 = bytearray(gen8_dim)
    struct.pack_into("<I", gen8, 4, testo_di[nome])
    struct.pack_into("<I", gen8, 40, testo_di[nome])
    sprt = bytearray(sprt_dim)
    struct.pack_into("<I", sprt, 0, len(sprite))
    for i, (n, fotogrammi) in enumerate(sprite):
        rec = 4 + 4 * len(sprite) + 96 * i
        struct.pack_into("<I", sprt, 4 + 4 * i, inizio_sprt + rec)
        struct.pack_into("<I13i", sprt, rec, testo_di[n], 16, 16, 0, 15, 15, 0, 0, 0, 1, 0, 0, 8, 8)
        struct.pack_into("<iIIfII", sprt, rec + 56, -1, 1, 0, 1.0, 0, fotogrammi)
    pezzi = b"GEN8" + struct.pack("<I", gen8_dim) + bytes(gen8) + b"SPRT" + struct.pack("<I", sprt_dim) + bytes(sprt)
    if codice:
        pezzi += b"CODE" + struct.pack("<I", 4) + struct.pack("<I", 0)
    pezzi += b"STRG" + struct.pack("<I", len(strg)) + strg
    return b"FORM" + struct.pack("<I", len(pezzi)) + pezzi


# --- opzioni salvate (ds_map/ds_list in esadecimale, come Forager) -------------------------------------------

def _v(x):
    if isinstance(x, str):
        b = x.encode("utf-8")
        return struct.pack("<II", 1, len(b)) + b
    return struct.pack("<Id", 0, float(x))


def ds_list(valori):
    return (struct.pack("<II", 0x12E, len(valori)) + b"".join(_v(x) for x in valori)).hex().upper()


def ds_map(voci):
    return (struct.pack("<II", 0x192, len(voci)) + b"".join(_v(k) + _v(v) for k, v in voci)).hex().upper()


EN = {"wood": "wood", "itemName1": "iron*ore", "uiPlay": "play", "configVsync": "vsync:", "configFPS": "show*fps:",
      "uiExtras": "extras", "statDesc0": "permanent H**U increase", "skillDesc0": "U gain EP when selling items",
      "skillDesc1": "U gain EP when crafting", "skillDesc2": "U gain EP when fishing",
      "boon0": "U lose 1 max H**U gain a**bunch of food", "boon1": "U lose 1 max H**U gain coins",
      "uiDelete": "Are you sure you want to delete this save?"}
for i in range(40):  # abbastanza testi per riconoscere lo stile
    EN["itemDescription%d" % i] = "a small item number %d*for testing" % i
FR = dict(EN, configVsync="synchro*verticale :", uiPlay="jouer", uiExtras="extras", wood="bois")
ES = dict(EN, configVsync="vsync:", uiPlay="juega", uiExtras="extras", wood="madera")
PT = dict(EN, configVsync="vsync:", uiPlay="jogar", uiExtras="extras", wood="madeira")


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    for d in ("memory", "profiles", "languages"):
        os.makedirs(os.path.join(progetto.CARTELLA_PROGETTO, d))
    automatico.CARTELLA_PROFILI = os.path.join(progetto.CARTELLA_PROGETTO, "profiles")
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    sistema.gioco_aperto = lambda c: False

    # --- 1. lettura di data.win: sprite, nome, stringhe, mappa del font a sprite, maschera delle lingue
    vm = data_win(["local/*.json", MAPPA], [("sprFont", len(MAPPA)), ("sprPlayer", 4)])
    gioco = os.path.join(base, "gioco")
    os.makedirs(os.path.join(gioco, "local"))
    open(os.path.join(gioco, "data.win"), "wb").write(vm)
    controlla("data.win: sprite con i fotogrammi, nome del gioco, stringhe",
              gamemaker.sprite(vm) == [{"nome": "sprFont", "fotogrammi": len(MAPPA)},
                                       {"nome": "sprPlayer", "fotogrammi": 4}]
              and gamemaker.nomi_del_gioco(vm) == ["FintoGame"] and "local/*.json" in gamemaker.stringhe(vm))
    controlla("font a sprite: la mappa dei caratteri (tanti quanti i fotogrammi) e la maschera 'local/*.json'",
              gamemaker.mappe_font_sprite(gioco, vm) == [("sprFont", MAPPA)]
              and gamemaker.cartella_maschera(gioco, "local", ".json", vm)
              and not gamemaker.cartella_maschera(gioco, "lang", ".json", vm))
    yyc = data_win([], [("sprFont", len(MAPPA))], codice=False)
    cartella_yyc = os.path.join(base, "yyc")
    os.makedirs(cartella_yyc)
    open(os.path.join(cartella_yyc, "Gioco.exe"), "wb").write(b"MZ\x00" + b"local/*.json\x00" + MAPPA.encode() + b"\x00")
    open(os.path.join(cartella_yyc, "unins000.exe"), "wb").write(b"MZ\x00lang/*.json\x00")
    controlla("gioco compilato YYC: maschera e mappa cercate nell'eseguibile (non nei disinstallatori)",
              gamemaker.compilato_yyc(yyc) and gamemaker.cartella_maschera(cartella_yyc, "local", ".json", yyc)
              and not gamemaker.cartella_maschera(cartella_yyc, "lang", ".json", yyc)
              and gamemaker.mappe_font_sprite(cartella_yyc, yyc) == [("sprFont", MAPPA)])

    # --- 2. regole sui testi: stile, codici, minuscole, spazio delle righe
    testi = list(EN.values())
    controlla("stile 'minuscolo' e codici-icona EP e H (confermati dal font); un gioco normale non lo e'",
              adapter_gamemaker.stile_maiuscole(testi) == "minuscolo"
              and adapter_gamemaker.codici_maiuscoli(testi, MAPPA, "U") == ["EP", "H"]
              and adapter_gamemaker.codici_maiuscoli(testi, None, "U") == []
              and adapter_gamemaker.stile_maiuscole(["Hello World %d, Start Game" % i for i in range(40)]) == "normale")
    controlla("minuscole: tutto tranne icone e segnaposto; i testi con maiuscole normali restano come sono",
              adapter_gamemaker.in_minuscolo("Mostra*FPS:", "show*fps:") == "mostra*fps:"
              and adapter_gamemaker.in_minuscolo("Ottieni EP per {Item}", "gain EP for {Item}", ["EP"])
              == "ottieni EP per {Item}"
              and adapter_gamemaker.in_minuscolo("Sei sicuro?", "Are you sure?") == "Sei sicuro?")
    limiti = adapter_gamemaker.limiti_righe(EN, [FR, ES, PT], "*")
    controlla("spazio di ogni testo dalle versioni del gioco (riga piu' lunga, righe, quante lo lasciano uguale)",
              limiti["configVsync"] == [11, 2, 2] and limiti["uiExtras"][2] == 3)
    controlla("a capo ridistribuiti solo se la riga esce dallo spazio, entro le righe delle versioni del gioco",
              adapter_gamemaker.ricomponi_a_capo("vsync:", "sincronizzazione verticale:", "*", "U", 11, 2)
              == "sincronizzazione*verticale:"
              and adapter_gamemaker.ricomponi_a_capo("vsync:", "vsync:", "*", "U", 11, 2) == "vsync:"
              and adapter_gamemaker.ricomponi_a_capo("iron*ore", "minerale di ferro", "*", "U", 30, 2)
              == "minerale di*ferro")

    # --- 3. flusso completo: la traduzione diventa una lingua nuova, l'inglese resta originale
    for nome, dati in (("english", EN), ("french", FR), ("spanish", ES), ("portuguese", PT)):
        json.dump(dati, open(os.path.join(gioco, "local", nome + ".json"), "w", encoding="utf-8"))
    inglese = os.path.join(gioco, "local", "english.json")
    prima = impronta(inglese)
    percorso, completato, esito = automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)
    profilo = json.load(open(percorso, encoding="utf-8"))
    voce = profilo["file"][0]
    italiano = os.path.join(gioco, "local", "italiano.json")
    controlla("lingua nuova local/italiano.json; english.json identico; profilo con stile, icone, spazio, riferimenti",
              os.path.exists(italiano) and impronta(inglese) == prima and voce.get("lingua_nuova") == "local/italiano.json"
              and voce.get("maiuscole") == "minuscolo" and voce.get("codici") == ["EP", "H"]
              and voce.get("limiti", {}).get("configVsync") == [11, 2, 2]
              and set(voce.get("riferimenti", {})) == {"fr", "es", "pt"} and esito.get("lingua_aggiunta"))
    locali = os.listdir(os.path.join(gioco, "local"))
    controlla("nessun file di PatchSmith che il gioco caricherebbe come lingua (niente *.json nostri)",
              not [n for n in locali if "patchsmith" in n and n.endswith(".json")]
              and "english.json" + progetto.SUFFISSO_INFO in locali)
    it = json.load(open(italiano, encoding="utf-8"))
    controlla("traduzione: icone EP e H conservate, maiuscole tolte, caratteri del font (parentesi al posto di [ ])",
              "EP" in it["skillDesc0"] and "H" in it["statDesc0"] and not any(
                  c.isupper() for k, v in it.items() if k != "uiDelete"
                  for c in v.replace("EP", "").replace("H", "").replace("U ", ""))
              and "[" not in json.dumps({k: v for k, v in it.items() if k != "uiDelete"}))
    controlla("messaggio con maiuscole normali: tradotto con il font normale (non quello a immagini), maiuscole tenute",
              it["uiDelete"] != EN["uiDelete"] and "Are you sure" in it["uiDelete"])
    controlla("rilancio: italiano.json non e' scambiato per una traduzione ufficiale del gioco",
              not any("italiano" in r for r in adapter_gamemaker.file_json_lingue(gioco))
              and automatico.analizza_e_traduci(gioco, "auto", "it", "finto", silenzio)[0] == percorso)

    # --- 4. lingua impostata nelle opzioni salvate del gioco, e ripristino
    opzioni_dir = os.path.join(base, "appdata", "FintoGame")
    os.makedirs(opzioni_dir)
    config = os.path.join(opzioni_dir, "config.txt")
    testo = '{ "mvc": "1.2", "config": "%s", "altro": "x" }' % ds_map(
        [("extrasUnlocked", 10), ("options", ds_list(["english", 0, 1, "yes"])), ("totalFeats", 3)])
    open(config, "w", encoding="utf-8", newline="").write(testo)
    lingue = automatico.lingue_della_cartella(gioco, "local/english.json")
    fatto, righe = lingua_ufficiale.imposta_lingua_aggiunta(gioco, "italiano", lingue, silenzio)
    dopo = open(config, encoding="utf-8").read()
    controlla("lingua impostata nelle opzioni del gioco ('english' -> 'italiano'), resto del file identico",
              fatto and lingua_gamemaker.trova(dopo, {"italiano"}) == [("config", "options", 0, "italiano")]
              and dopo.replace(json.loads(dopo)["config"], "") == testo.replace(json.loads(testo)["config"], ""))
    doppio = '{ "a": "%s", "b": "%s" }' % (ds_map([("options", ds_list(["english"]))]),
                                          ds_map([("lang", ds_list(["french"]))]))
    open(os.path.join(opzioni_dir, "altro.txt"), "w", encoding="utf-8").write(doppio)
    controlla("lingua in piu' punti delle opzioni: non si tocca nulla",
              lingua_gamemaker.cerca(gioco, lingue | {"italiano"})[0] is None)
    os.remove(os.path.join(opzioni_dir, "altro.txt"))
    controlla("ripristino: lingua aggiunta tolta, inglese identico, opzioni di nuovo su 'english' (file identico)",
              automatico.ripristina(gioco, silenzio) and not os.path.exists(italiano) and impronta(inglese) == prima
              and open(config, encoding="utf-8").read() == testo
              and not [n for n in os.listdir(os.path.join(gioco, "local")) if "patchsmith" in n])

    # --- 5. da una versione precedente: english.json tradotto al suo posto e informazioni con il nome vecchio
    vecchio = os.path.join(base, "vecchio")
    shutil.copytree(gioco, vecchio)
    shutil.rmtree(os.path.join(vecchio, "local"))
    os.makedirs(os.path.join(vecchio, "local"))
    for nome, dati in (("english", EN), ("french", FR), ("spanish", ES), ("portuguese", PT)):
        json.dump(dati, open(os.path.join(vecchio, "local", nome + ".json"), "w", encoding="utf-8"))
    en_vecchio = os.path.join(vecchio, "local", "english.json")
    originale = impronta(en_vecchio)
    info = progetto.crea_backup(en_vecchio)
    tradotto = json.dumps(dict(EN, uiPlay="gioca")).encode("utf-8")
    info["hash_scritti"] = [hashlib.sha256(tradotto).hexdigest()]
    os.replace(en_vecchio + progetto.SUFFISSO_INFO, en_vecchio + progetto.SUFFISSO_INFO_VECCHIO)
    with open(en_vecchio + progetto.SUFFISSO_INFO_VECCHIO, "w", encoding="utf-8") as f:
        json.dump(info, f)
    open(en_vecchio, "wb").write(tradotto)
    automatico.analizza_e_traduci(vecchio, "auto", "it", "finto", silenzio)
    controlla("dalla versione precedente: english.json rimesso originale, informazioni con il nome nuovo, lingua nuova",
              impronta(en_vecchio) == originale and not os.path.exists(en_vecchio + progetto.SUFFISSO_INFO_VECCHIO)
              and os.path.exists(en_vecchio + progetto.SUFFISSO_INFO)
              and os.path.exists(os.path.join(vecchio, "local", "italiano.json")))

    # --- 6. casi difficili: file di altri, correzioni a mano, opzioni senza registro, Riapplica, scelta del giocatore
    altro = os.path.join(base, "altro")
    shutil.copytree(vecchio, altro)
    for n in os.listdir(os.path.join(altro, "local")):
        if "patchsmith" in n or n == "italiano.json":
            os.remove(os.path.join(altro, "local", n))
    automatico.analizza_e_traduci(altro, "auto", "it", "finto", silenzio)
    italiano_altro = os.path.join(altro, "local", "italiano.json")
    automatico.ripristina(altro, silenzio)
    open(italiano_altro, "w", encoding="utf-8").write('{"uiPlay": "gioca (traduzione amatoriale)"}')
    automatico.riapplica(altro, silenzio, prova=True)
    controlla("un italiano.json di qualcun altro non viene sovrascritto ne' cancellato",
              open(italiano_altro, encoding="utf-8").read() == '{"uiPlay": "gioca (traduzione amatoriale)"}'
              and automatico.ripristina(altro, silenzio) is not None and os.path.exists(italiano_altro))
    os.remove(italiano_altro)
    automatico.riapplica(altro, silenzio, prova=True)
    corretto = json.load(open(italiano_altro, encoding="utf-8"))
    corretto["uiPlay"] = "gioca!"
    json.dump(corretto, open(italiano_altro, "w", encoding="utf-8"))
    automatico.riapplica(altro, silenzio, prova=True)
    controlla("lingua aggiunta corretta a mano: riscritta, la versione corretta resta da parte (.patchsmith-modificato)",
              json.load(open(italiano_altro + ".patchsmith-modificato", encoding="utf-8"))["uiPlay"] == "gioca!"
              and json.load(open(italiano_altro, encoding="utf-8"))["uiPlay"] != "gioca!")
    # opzioni su 'italiano' scelte dal giocatore (nessun registro): il ripristino le rimette prima di togliere il file
    for n in os.listdir(os.path.join(base, "patchsmith", "languages")):
        os.remove(os.path.join(base, "patchsmith", "languages", n))
    open(config, "w", encoding="utf-8", newline="").write(testo.replace(
        json.loads(testo)["config"], ds_map([("extrasUnlocked", 10), ("options", ds_list(["italiano", 0, 1, "yes"])),
                                             ("totalFeats", 3)])))
    os.rename(opzioni_dir, opzioni_dir + "-tmp")  # le opzioni di 'gioco', non di 'altro'
    opzioni_altro = os.path.join(base, "appdata", "FintoGame")
    os.rename(opzioni_dir + "-tmp", opzioni_altro)
    automatico.ripristina(altro, silenzio)
    controlla("ripristino senza registro per questa cartella (es. una copia del gioco, che condivide le opzioni con "
              "quello vero): le opzioni non si toccano",
              lingua_gamemaker.trova(open(config, encoding="utf-8").read(), {"italiano"})
              == [("config", "options", 0, "italiano")] and not os.path.exists(italiano_altro))
    automatico.riapplica(altro, silenzio)  # PatchSmith imposta la lingua per 'altro' (registro)
    open(config, "w", encoding="utf-8", newline="").write(testo.replace(
        json.loads(testo)["config"], ds_map([("extrasUnlocked", 10), ("options", ds_list(["italiano", 0, 1, "yes"])),
                                             ("totalFeats", 3)])))
    automatico.ripristina(altro, silenzio)
    controlla("ripristino con il registro di questa cartella: opzioni rimesse su 'english' prima di togliere il file",
              lingua_gamemaker.trova(open(config, encoding="utf-8").read(), {"english"})
              == [("config", "options", 0, "english")] and not os.path.exists(italiano_altro))
    # Riapplica imposta la lingua; opzioni cancellate: il ripristino si chiude senza bloccarsi
    esito = {}
    automatico.riapplica(altro, silenzio, esito=esito)
    impostata = lingua_gamemaker.trova(open(config, encoding="utf-8").read(), {"italiano"})
    os.remove(config)
    controlla("Riapplica imposta la lingua aggiunta; con le opzioni cancellate il ripristino si chiude lo stesso",
              esito.get("lingua_aggiunta", {}).get("impostata") and impostata
              and automatico.ripristina(altro, silenzio) and not lingua_ufficiale.impostata(altro))
    # il giocatore sceglie un'altra lingua dopo la traduzione: un nuovo 'Analizza e traduci' la rispetta
    open(config, "w", encoding="utf-8", newline="").write(testo)
    automatico.riapplica(altro, silenzio)
    scelta = lingua_gamemaker.cambia(open(config, encoding="utf-8").read(),
                                     lingua_gamemaker.trova(open(config, encoding="utf-8").read(), {"italiano"})[0],
                                     "french")
    open(config, "w", encoding="utf-8", newline="").write(scelta)
    automatico.riapplica(altro, silenzio)
    controlla("lingua scelta dal giocatore dopo la traduzione: non viene cambiata di nuovo",
              lingua_gamemaker.trova(open(config, encoding="utf-8").read(), {"french"})
              == [("config", "options", 0, "french")])

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
