"""Tests of the language file formats of the new games (not Unity) and of the limits of their engines.

Usage: python -I test/test_formats.py <empty_temp_folder>
Fake games and files built here, modeled on real ones seen read-only (no texts from real games):
- JSON with // and /* */ comments, trailing commas and sections (Another Door: localization/stringsEN.json): read,
  translated and rewritten identical byte for byte except the translated texts; language code attached to the name;
- CSV tables with one column per language (Megaloot: translations.csv, split by hand on commas; and a table with
  quotes the CSV way), also inside a GameMaker game, with the official translation recognized;
- a GameMaker game without recognized language files: the generic search's game profile has the engine, one-word
  labels are translated too and the characters are adapted to the fonts; the same for XNA;
- GameMaker fonts: those for another script and the libraries' fallback ones do not count;
- web page with the PLAY and OK buttons (test/web/index-inline.html) and JavaScript strings that are surely visible;
- Unreal without .locres and with an unreadable pak;
- skipped files written to the log.
Writes only in the temporary folder (PatchSmith's data included); no network (fake translator).
"""
import hashlib
import json
import os
import re
import shutil
import struct
import sys

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

from patchsmith import (adapter_csv, adapter_gamemaker, adapter_unreal, adapter_webjson, adapter_webui, adapter_xna,
                        auto as automatico, backend, interruption as interruzione,
                        official_language as lingua_ufficiale, project as progetto, scan as scansione, steam)
from patchsmith.characters import ASCII  # noqa: E402
import test_gamemaker  # noqa: E402  (data.win builder)
import test_unreal as unreal_finto  # noqa: E402  (pak and .locres builder)

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def leggi_byte(percorso):
    with open(percorso, "rb") as f:
        return f.read()


def scrivi_byte(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(dati)


def impronte(cartella):
    return {os.path.relpath(os.path.join(r, n), cartella): hashlib.sha256(leggi_byte(os.path.join(r, n))).hexdigest()
            for r, _, fs in os.walk(cartella) for n in fs if "patchsmith" not in n}


# --- fake translator -------------------------------------------------------------------------------

ITALIANO = {
    "Play": "Gioca", "QUIT": "ESCI", "quit": "esci", "OK": "Va bene", "return": "indietro", "English": "Italiano",
    "Invite \"friends\"": "Invita gli \"amici\"",
    "Push a button to join the game.\n(Esc. or Start to leave the game)":
        "Premi un pulsante per entrare: è così.\n(Esc. o Start per uscire, può capitare)",
    "Push a button to join the game.": "Premi un pulsante per entrare: è così.",
    "(Esc. or Start to leave the game)": "(Esc. o Start per uscire, può capitare)",
    "VULTURE TOKEN": "GETTONE AVVOLTOIO",
    "Each time a player dies, receive [[0]][[1]][[2]] HP.":
        "Ogni volta che un giocatore muore, ricevi [[0]][[1]][[2]] PS.",
    "Hello traveler; welcome to the shop": "Ciao viaggiatore, benvenuto nel negozio",
    "Your bag is full": "La tua borsa è piena", "wood": "legno", "stone": "pietra", "PLAY": "GIOCA",
    "Coal": "Carbone", "Everything is saved": "È tutto salvato",
    "Open the old chest": "Apri il\nvecchio forziere",  # an extra line break: it cannot be written in a table
}


def finta(r):
    """Recognizable fake translation: words reversed (no common English words, placeholders intact)."""
    return re.sub(r"[A-Za-z]{2,}", lambda m: m.group(0)[::-1].lower() + "o", r)


class Traduttore(backend.Backend):
    nome = "dizionario"

    def __init__(self):
        self.ricevuti = []

    def traduci_righe(self, righe, origine, destinazione, *_a, **_k):
        interruzione.controlla()
        self.ricevuti += list(righe)
        return [ITALIANO.get(r, finta(r)) for r in righe]


def prepara_dati(base):
    progetto.RADICE_DATI = os.path.join(base, "dati")  # never the real data
    automatico.CARTELLA_PROFILI = None
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    steam.cartella_steam = lambda: None
    steam.gioco_installato = lambda cartella, steam=None: None
    steam.riconosci_da_eseguibili = lambda cartella: None
    automatico.crea_backend = lambda nome, log=print: Traduttore()


# --- 1. Lenient JSON --------------------------------------------------------------------------------

def _porta(valori, lingua_nome):
    """A language file in the style of Another Door (CRLF, tabs, comments, trailing commas, sections). 'valori':
    {(sezione, chiave): testo}; the literals written as json.dumps, except an identifier written by hand with an
    unusual escape (it must stay identical) and some numbers written in a particular way (1.50, 1e5)."""
    def s(sezione, chiave):
        return json.dumps(valori[(sezione, chiave)], ensure_ascii=False)
    righe = ["{", "", "\"meta\": {", "\t\"_langNameLocal\":%s," % json.dumps(lingua_nome, ensure_ascii=False), "},", "",
             "// Menu principale: i nomi dei tasti restano come sono (grazie ad Ana \u00c1valos)", "\"menu\": {"]
    for chiave in ("_play", "_quit", "_ok", "_return", "_inviteFriends", "_clickToJoin", "_site"):
        righe.append("\t\"%s\":%s," % (chiave, s("menu", chiave)))
    righe += ["\t\"_id\":\"Structure\\u002ENuclearFurnace\",", "\t\"_scala\": [1.50, 1e5, true, null],", "},",
              "/* sezione delle carte", "   (commento su piu' righe, con \"virgolette\" e // dentro) */",
              "\"cards\": {"]
    for chiave in sorted(k for (sez, k) in valori if sez == "cards"):
        righe.append("\t\"%s\":%s," % (chiave, s("cards", chiave)))
    righe += ["\t}", "}", "", "", "//Comments like this technically violate the JSON standard, but the game filters "
              "them out", ""]
    return "\r\n".join(righe).encode("utf-8")


EN_PORTA = {("menu", "_play"): "Play", ("menu", "_quit"): "QUIT", ("menu", "_ok"): "OK", ("menu", "_return"): "return",
            ("menu", "_inviteFriends"): "Invite \"friends\"",
            ("menu", "_clickToJoin"): "Push a button to join the game.\n(Esc. or Start to leave the game)",
            ("menu", "_site"): "Visit https://example.com // not a comment",
            ("cards", "_vulture"): "VULTURE TOKEN",
            ("cards", "_vultureDesc"): "Each time a player dies, receive [yellow1]{0}[/c] HP."}
EN_PORTA.update({("cards", "_card%02d" % i): "Card number %d does something to the dungeon" % i for i in range(15)})
FR_PORTA = {k: "Le texte français %d de la carte" % n for n, k in enumerate(EN_PORTA)}
DE_PORTA = {k: "Der deutsche Text %d der Karte" % n for n, k in enumerate(EN_PORTA)}
IT_PORTA = {k: "Il testo italiano %d della carta è qui" % n for n, k in enumerate(EN_PORTA)}


def _valori_porta(dati):
    return {(sez, k): v for sez in ("menu", "cards") for k, v in dati[sez].items() if isinstance(v, str)
            and not k.startswith("_id")}


def prova_json_tollerante(base):
    cartella = os.path.join(base, "json")
    p = os.path.join(cartella, "stringsEN.json")
    originale = _porta(EN_PORTA, "English")
    scrivi_byte(p, originale)
    try:
        json.loads(originale.decode("utf-8"))
        rigoroso = True
    except ValueError:
        rigoroso = False
    f = adapter_webjson.FileJSON(p)
    controlla("JSON con commenti e virgole in fondo: non e' JSON rigoroso, ma si legge (sezioni comprese)",
              not rigoroso and f.tollerante and f.dati["menu"]["_play"] == "Play"
              and f.dati["menu"]["_site"] == "Visit https://example.com // not a comment"
              and f.dati["menu"]["_scala"] == [1.5, 100000.0, True, None]
              and f.dati["menu"]["_id"] == "Structure.NuclearFurnace")
    controlla("riscritto senza cambiamenti: identico byte per byte", f.serializza(f.dati) == originale)
    nuovi = json.loads(json.dumps(f.dati))
    nuovi["menu"]["_play"] = "Gioca \"ora\""
    nuovi["cards"]["_vulture"] = "GETTONE è così"
    scritto = f.serializza(nuovi)
    atteso = originale.replace(b'"_play":"Play"', b'"_play":"Gioca \\"ora\\""').replace(
        b'"_vulture":"VULTURE TOKEN"', '"_vulture":"GETTONE è così"'.encode("utf-8"))
    controlla("riscritto con due testi cambiati: identico tranne quei due letterali (commenti, virgole in fondo, "
              "numeri 1.50 e 1e5, escape \\u002E, CRLF)", scritto == atteso and atteso != originale)
    controlla("riletto con le regole del gioco (commenti tolti, niente NaN)",
              adapter_webjson.rileggi_rigoroso(scritto, True)["menu"]["_play"] == "Gioca \"ora\"")
    bom = os.path.join(cartella, "bom.json")
    scrivi_byte(bom, b"\xef\xbb\xbf" + originale)
    fb = adapter_webjson.FileJSON(bom)
    controlla("con il BOM: resta il BOM", fb.bom and fb.serializza(fb.dati) == b"\xef\xbb\xbf" + originale)
    solo_ascii = os.path.join(cartella, "ascii.json")
    scrivi_byte(solo_ascii, b'// solo ASCII\n{"a": "Caf\\u00e9", "b": "Tea",}\n')
    fa = adapter_webjson.FileJSON(solo_ascii)
    controlla("file tutto ASCII con gli escape \\u: il testo nuovo si scrive allo stesso modo",
              fa.serializza({"a": "Caf\u00e9", "b": "T\u00e8"})
              == b'// solo ASCII\n{"a": "Caf\\u00e9", "b": "T\\u00e8",}\n')
    rotto = json.loads(json.dumps(f.dati))
    rotto["menu"]["_scala"][0] = 2
    try:
        f.serializza(rotto)
        rifiuta = False
    except ValueError:
        rifiuta = True
    controlla("un valore che non e' una stringa cambiato: non si scrive (ValueError)", rifiuta)
    for nome, testo in (("vero.json", b"{\"a\": [1, 2,], \"b\": \"x\" // fine\n}"),
                        ("rotto.json", b"{\"a\": \"senza chiusura\""), ("non_json.json", b"<html>no</html>")):
        scrivi_byte(os.path.join(cartella, nome), testo)
    letto = adapter_webjson.FileJSON(os.path.join(cartella, "vero.json")).dati
    errori = 0
    for nome in ("rotto.json", "non_json.json"):
        try:
            adapter_webjson.FileJSON(os.path.join(cartella, nome))
        except ValueError:
            errori += 1
    controlla("virgola in fondo a una lista e commento a fine riga; un file rovinato resta un errore",
              letto == {"a": [1, 2], "b": "x"} and errori == 2)
    rigido = os.path.join(cartella, "rigido.json")
    scrivi_byte(rigido, b'{"a": "x", "b": 1.50}')
    controlla("un JSON rigoroso si legge e si scrive come prima (non tollerante)",
              not adapter_webjson.FileJSON(rigido).tollerante)


# --- 2. GameMaker: files per language in the style of Another Door -------------------------------------

LATINO = set(chr(c) for c in range(32, 127)) | set("àèéìòùÀÈÉÌÒÙçâêôûœëïüöÁáíóúñ")
GIAPPONESE = set(chr(c) for c in range(32, 127)) | set("çêñŒœ") | {chr(0x4E00 + i) for i in range(300)}


def gioco_porta(cartella, lingue=("EN", "FR", "DE")):
    valori = {"EN": EN_PORTA, "FR": FR_PORTA, "DE": DE_PORTA, "IT": IT_PORTA}
    nomi = {"EN": "English", "FR": "Français", "DE": "Deutsch", "IT": "Italiano"}
    for codice in lingue:
        scrivi_byte(os.path.join(cartella, "localization", "strings%s.json" % codice),
                    _porta(valori[codice], nomi[codice]))
    scrivi_byte(os.path.join(cartella, "localization", "availableLanguages.json"),
                b'//note\r\n{"langs": [{"stringsFile": "stringsEN.json",},],}\r\n')
    scrivi_byte(os.path.join(cartella, "data.win"), test_gamemaker.data_win(
        [("ftText", LATINO), ("ftFusionJP", GIAPPONESE), ("scribble_fallback_font", set(ASCII))]))
    scrivi_byte(os.path.join(cartella, "Porta.exe"), b"MZ")


def _scheletro(testo):
    """The text with every string literal replaced by S: what must stay identical."""
    return re.sub(rb'"(?:[^"\\]|\\.)*"', b"S", testo)


def prova_porta(base):
    gioco = os.path.join(base, "Porta finta")
    gioco_porta(gioco)
    loc = os.path.join(gioco, "localization")
    controlla("codice attaccato al nome: stringsEN -> en, stringsBR -> pt, stringsSC -> zh, stringsTC -> zh-TW",
              adapter_gamemaker.lingua_del_percorso("localization/stringsEN.json") == "en"
              and adapter_gamemaker.lingua_del_percorso("localization/stringsBR.json") == "pt"
              and adapter_gamemaker.lingua_del_percorso("localization/stringsSC.json") == "zh"
              and adapter_gamemaker.lingua_del_percorso("localization/stringsTC.json") == "zh-TW"
              and adapter_gamemaker.lingua_del_percorso("data/levelUP.json") is None
              and adapter_gamemaker.lingua_del_percorso("data/strings.json") is None)
    controlla("stessa famiglia: localization/strings{L}.json",
              adapter_gamemaker.famiglia("localization/stringsEN.json") == "localization/strings{L}.json"
              == adapter_gamemaker.famiglia("localization/stringsIT.json")
              and adapter_gamemaker.famiglia("localization/stringsEN.json") != adapter_gamemaker.famiglia(
                  "localization/textEN.json"))
    trovati = adapter_gamemaker.file_json_lingue(gioco)
    controlla("file di lingua con commenti, virgole in fondo e sezioni riconosciuti (en, fr, de)",
              trovati == {"localization/stringsEN.json": "en", "localization/stringsFR.json": "fr",
                          "localization/stringsDE.json": "de"})
    controlla("lingue nei file: senza italiano non c'e' la lingua ufficiale",
              lingua_ufficiale.lingue_nei_file(gioco, "GameMaker", "it")[1]["trovata"] is False)
    righe = []
    ammessi = adapter_gamemaker.caratteri_font(gioco, righe.append)
    controlla("font: non contano quello giapponese (con ç ê ñ Œ œ) ne' scribble_fallback_font: è e ò ammesse",
              ammessi is not None and "è" in ammessi and "ò" in ammessi and "ì" in ammessi
              and any("scribble_fallback_font" in r for r in righe))
    prima = impronte(gioco)
    originale = leggi_byte(os.path.join(loc, "stringsEN.json"))

    traduttore = Traduttore()
    automatico.crea_backend = lambda nome, log=print: traduttore
    registro = []
    percorso, completato, esito = automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", registro.append)
    profilo = json.load(open(percorso, encoding="utf-8"))
    voce = profilo["file"][0] if profilo.get("file") else {}
    controlla("profilo GameMaker: solo stringsEN.json, file di lingua con le chiavi delle sezioni",
              profilo.get("motore") == "gamemaker" and [v["percorso"] for v in profilo["file"]]
              == ["localization/stringsEN.json"] and voce.get("localizzazione")
              and "menu._play" in voce.get("includi", []) and "menu._id" not in voce.get("includi", []))
    scritto = leggi_byte(os.path.join(loc, "stringsEN.json"))
    dati = adapter_webjson.leggi_tollerante(scritto.decode("utf-8"))
    controlla("tradotte anche le etichette di una parola (Play, QUIT, OK, return) e il nome della lingua",
              dati["menu"]["_play"] == "Gioca" and dati["menu"]["_quit"] == "ESCI" and dati["menu"]["_ok"] == "Va bene"
              and dati["menu"]["_return"] == "indietro" and dati["meta"]["_langNameLocal"] == "Italiano")
    controlla("tag di Scribble e segnaposto rimessi; accenti è e ò scritti (i font li hanno)",
              dati["cards"]["_vultureDesc"] == "Ogni volta che un giocatore muore, ricevi [yellow1]{0}[/c] PS."
              and "è così" in dati["menu"]["_clickToJoin"] and "può" in dati["menu"]["_clickToJoin"]
              and "Each time a player dies, receive [[0]][[1]][[2]] HP." in traduttore.ricevuti)
    controlla("riscritto identico tranne i letterali tradotti: commenti, virgole in fondo, CRLF, numeri, escape",
              _scheletro(scritto) == _scheletro(originale) and scritto != originale
              and b"// Menu principale" in scritto and b"/* sezione delle carte" in scritto
              and b"[1.50, 1e5, true, null]" in scritto and b'"_id":"Structure\\u002ENuclearFurnace"' in scritto
              and _porta(dict(_valori_porta(dati)), dati["meta"]["_langNameLocal"]) == scritto)
    cambiati = sorted(k for k in impronte(gioco) if impronte(gioco)[k] != prima.get(k))
    controlla("solo stringsEN.json cambiato (le traduzioni ufficiali e i dati del gioco intatti)",
              cambiati == [os.path.join("localization", "stringsEN.json")])
    automatico.ripristina(gioco, silenzio)
    controlla("ripristino: identico all'originale", leggi_byte(os.path.join(loc, "stringsEN.json")) == originale
              and impronte(gioco) == prima)
    automatico.crea_backend = lambda nome, log=print: Traduttore()

    con_it = os.path.join(base, "Porta con italiano")
    gioco_porta(con_it, ("EN", "FR", "IT"))
    _, dati_motore = lingua_ufficiale.lingue_nei_file(con_it, "GameMaker", "it")
    percorso, _, esito = automatico.analizza_e_traduci(con_it, "auto", "it", "dizionario", silenzio)
    controlla("stringsIT.json (anche con commenti): lingua ufficiale trovata, nessuna traduzione automatica",
              dati_motore.get("trovata") is True and percorso is None and esito["esito"].codice == "UFFICIALE")


# --- 3. CSV tables ---------------------------------------------------------------------------------

MEGALOOT = ("id,english,french,german,koreana,schinese,tchinese,italian\n"
            "\"?\"-label,\"?\",,,,,,\n"
            "menu-play,Play,Jouer,Spielen,플레이,开始,開始,\n"
            "menu-quit,QUIT,QUITTER,BEENDEN,종료,退出,退出,\n"
            "shop-greeting,Hello traveler; welcome to the shop,Bonjour voyageur; bienvenue,Hallo Reisender; willkommen,"
            "안녕,你好,你好,\n"
            "bag-full,Your bag is full,Ton sac est plein,Deine Tasche ist voll,가방이 가득,包满了,包滿了,\n"
            "lore-1,The old king|\\n|was never seen again,Le vieux roi|\\n|disparut,Der alte König|\\n|verschwand,"
            "왕,国王,國王,")


def gioco_megaloot(cartella, italiano=False):
    testo = MEGALOOT
    if italiano:  # full Italian column: official translation
        testo = "\n".join(r + ("Testo italiano" if i else "") for i, r in enumerate(testo.split("\n")))
    scrivi_byte(os.path.join(cartella, "translations.csv"), testo.encode("utf-8"))
    scrivi_byte(os.path.join(cartella, "content.csv"), b"Name,Value,Growth\nArmor,1,1.25\nGold,1,1\n")
    scrivi_byte(os.path.join(cartella, "data.win"), test_gamemaker.data_win([("fntMain", LATINO)]))
    scrivi_byte(os.path.join(cartella, "Megaloot.exe"), b"MZ")


RFC = ("\ufeffkey;English;Français;Deutsch\r\n"
       "title;\"Hello; world\";\"Bonjour; monde\";\"Hallo; Welt\"\r\n"
       "quote;\"He said \"\"hi\"\"\";\"Il a dit \"\"salut\"\"\";\"Er sagte \"\"hallo\"\"\"\r\n"
       "multi;\"Line one\r\nLine two\";\"Ligne un\r\nLigne deux\";\"Zeile eins\r\nZeile zwei\"\r\n"
       "plain;Start;Démarrer;Starten\r\n"
       "hero;\"Hero\";\"Héros\";\"Held\"\r\n")


def prova_csv(base):
    cartella = os.path.join(base, "csv")
    p = os.path.join(cartella, "translations.csv")
    scrivi_byte(p, MEGALOOT.encode("utf-8"))
    f = adapter_csv.leggi(p)
    controlla("tabella alla Megaloot: colonne riconosciute (koreana, schinese, tchinese), chiave 'id', divisa a mano",
              f.tabella() and f.chiave == 0 and f.lingue == {1: "en", 2: "fr", 3: "de", 4: "ko", 5: "zh", 6: "zh-TW",
                                                             7: "it"}
              and f.separatore == "," and not f.virgolette and f.sorgente() == (1, "en"))
    controlla("lingue piene: la colonna italiana vuota non e' una traduzione ufficiale",
              f.lingue_piene() == {"en", "fr", "de", "ko", "zh", "zh-TW"})
    controlla("testi della colonna inglese ('\"?\"' senza lettere no) e riscrittura senza cambiamenti identica",
              [t for _, t in f.testi(1)][:2] == ["Play", "QUIT"] and len(f.testi(1)) == 5
              and f.serializza({}) == MEGALOOT.encode("utf-8"))
    controlla("tabella divisa sulle virgole: ',' e '\"' non si possono scrivere e si adattano come fa il gioco (';')",
              f.non_scrivibile('Ciao, "amico"') == ',"' and f.adatta('Ciao, "amico"') == "Ciao; 'amico'")
    sost = {(2, 1): "Gioca", (4, 1): "Ciao viaggiatore; benvenuto"}
    atteso = MEGALOOT.replace("menu-play,Play,", "menu-play,Gioca,").replace(
        "shop-greeting,Hello traveler; welcome to the shop,", "shop-greeting,Ciao viaggiatore; benvenuto,")
    controlla("celle sostituite: il resto identico byte per byte (niente a capo finale, come l'originale)",
              f.serializza(sost) == atteso.encode("utf-8"))
    try:
        f.serializza({(2, 1): "Gioca, ora"})
        rifiuta = False
    except adapter_csv.ErroreCSV:
        rifiuta = True
    controlla("una virgola non adattata in una tabella divisa a mano: non si scrive", rifiuta)

    r = os.path.join(cartella, "rfc.csv")
    scrivi_byte(r, RFC.encode("utf-8"))
    g = adapter_csv.leggi(r)
    controlla("tabella CSV vera (BOM, ';', CRLF, virgolette, \"\" e a capo dentro le celle)",
              g.tabella() and g.bom and g.separatore == ";" and g.virgolette and g.speciali and g.doppie
              and g.lingue == {1: "en", 2: "fr", 3: "de"} and g.serializza({}) == RFC.encode("utf-8")
              and dict(g.testi(1))[(3, 1)] == "Line one\nLine two")
    nuovo = g.serializza({(1, 1): "Ciao; mondo", (2, 1): 'Ha detto "ciao"', (3, 1): "Riga uno\nRiga due",
                          (4, 1): "Avvia", (5, 1): "Eroe"})
    atteso = RFC.replace('title;"Hello; world";', 'title;"Ciao; mondo";').replace(
        'quote;"He said ""hi""";', 'quote;"Ha detto ""ciao""";').replace(
        'multi;"Line one\r\nLine two";', 'multi;"Riga uno\r\nRiga due";').replace("plain;Start;", "plain;Avvia;") \
        .replace('hero;"Hero";', 'hero;"Eroe";')
    controlla("tabella CSV vera riscritta: virgolette, \"\" e a capo CRLF come l'originale, il resto identico",
              nuovo == atteso.encode("utf-8"))
    scrivi_byte(os.path.join(cartella, "senza_chiave.csv"),
                b"English,French\nHello there,Bonjour\nGood bye,Au revoir\nSee you,A plus\nThanks,Merci\n")
    scrivi_byte(os.path.join(cartella, "una_lingua.csv"), b"id,english\na,Hello there\nb,Good bye\nc,See you\n")
    scrivi_byte(os.path.join(cartella, "latino1.csv"), "id,english,french\na,Caf\u00e9,Caf\u00e9\n".encode("latin-1"))
    controlla("non sono tabelle: senza colonna delle chiavi, con una lingua sola, non UTF-8",
              adapter_csv.tabella(os.path.join(cartella, "senza_chiave.csv")) is None
              and adapter_csv.tabella(os.path.join(cartella, "una_lingua.csv")) is None
              and adapter_csv.tabella(os.path.join(cartella, "latino1.csv")) is None
              and sorted(adapter_csv.tabelle(cartella)) == ["rfc.csv", "translations.csv"])
    # table split by hand where the only cell that is not valid CSV is in the translated column: after the
    # translation the file would look like a CSV with quotes, but it is read back the way the game reads it
    a_mano = adapter_csv.FileCSV(b'id,english,french\nhero,"Hero" rises,Le heros\nbye,Good bye,Au revoir\n'
                                 b'see,"Free",Gratuit\n')
    try:
        scritto = a_mano.serializza({(1, 1): a_mano.adatta("\"L'eroe\" sorge"), (2, 1): "Arrivederci"})
    except adapter_csv.ErroreCSV:
        scritto = None
    controlla("tabella divisa a mano con una cella '\"Hero\" rises' tradotta: scritta, il resto identico",
              not a_mano.virgolette and [t for _, t in a_mano.testi(1)][0] == '"Hero" rises'
              and scritto == b"id,english,french\nhero,'L'eroe' sorge,Le heros\nbye,Arrivederci,Au revoir\n"
                             b'see,"Free",Gratuit\n')
    # empty lines between groups of texts (and at the end): the key column is recognized anyway
    gruppi = adapter_csv.FileCSV(b"key,English,German\n\nmenu_play,Play,Spielen\nmenu_quit,Quit,Beenden\n\n\n"
                                 b"shop_hi,Hello there,Hallo\nshop_bye,Good bye,Tschuss\n\n")
    controlla("righe vuote tra i gruppi: chiave riconosciuta, tabella valida, riscrittura identica",
              gruppi.chiave == 0 and gruppi.tabella() and len(gruppi.testi(1)) == 4
              and gruppi.serializza({(3, 1): "Esci"}) == b"key,English,German\n\nmenu_play,Play,Spielen\n"
                                                          b"menu_quit,Esci,Beenden\n\n\nshop_hi,Hello there,Hallo\n"
                                                          b"shop_bye,Good bye,Tschuss\n\n")

    # GameMaker with translations.csv
    gioco = os.path.join(base, "Megaloot finto")
    gioco_megaloot(gioco)
    prima = impronte(gioco)
    originale = leggi_byte(os.path.join(gioco, "translations.csv"))
    percorso, completato, esito = automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
    profilo = json.load(open(percorso, encoding="utf-8")) if percorso else {}
    controlla("GameMaker con tabella CSV: profilo con la colonna english (content.csv non e' una tabella di lingua)",
              profilo.get("motore") == "gamemaker" and profilo.get("file") == [
                  {"percorso": "translations.csv", "formato": "csv", "localizzazione": True, "colonna": "english"}])
    scritto = leggi_byte(os.path.join(gioco, "translations.csv")).decode("utf-8")
    righe_o, righe_s = MEGALOOT.split("\n"), scritto.split("\n")
    altre = all(a.split(",")[:1] + a.split(",")[2:] == b.split(",")[:1] + b.split(",")[2:]
                for a, b in zip(righe_o, righe_s))
    controlla("tradotta la colonna english (la virgola della traduzione diventa ';'), le altre colonne identiche",
              "menu-play,Gioca," in scritto and "Ciao viaggiatore; benvenuto nel negozio" in scritto
              and len(righe_s) == len(righe_o) and altre and not scritto.endswith("\n")
              and "\"?\"-label,\"?\"," in scritto and "La tua borsa è piena" in scritto)
    controlla("solo translations.csv cambiato; ripristino identico",
              sorted(k for k in impronte(gioco) if impronte(gioco)[k] != prima.get(k)) == ["translations.csv"]
              and automatico.ripristina(gioco, silenzio) and leggi_byte(os.path.join(gioco, "translations.csv"))
              == originale)
    # a translation with an extra line break is left untranslated (the game reads one line per text), the others
    # are written; the comma becomes ';' here too
    a_capo = os.path.join(base, "Tabella a capo")
    scrivi_byte(os.path.join(a_capo, "translations.csv"), (
        "id,english,french\nchest,Open the old chest,Ouvrir le vieux coffre\nbag,Your bag is full,Ton sac est plein\n"
        "play,Play,Jouer\nquit,QUIT,QUITTER\nshop,Hello traveler; welcome to the shop,Bonjour voyageur\n").encode())
    scrivi_byte(os.path.join(a_capo, "data.win"), test_gamemaker.data_win([("fntMain", LATINO)]))
    scrivi_byte(os.path.join(a_capo, "Tabella.exe"), b"MZ")
    registro = []
    automatico.analizza_e_traduci(a_capo, "auto", "it", "dizionario", registro.append)
    scritto = leggi_byte(os.path.join(a_capo, "translations.csv")).decode("utf-8")
    controlla("tabella: la traduzione con un a capo in piu' resta in originale (scritto nel registro), le altre si "
              "scrivono", scritto == "id,english,french\nchest,Open the old chest,Ouvrir le vieux coffre\n"
              "bag,La tua borsa è piena,Ton sac est plein\nplay,Gioca,Jouer\nquit,ESCI,QUITTER\n"
              "shop,Ciao viaggiatore; benvenuto nel negozio,Bonjour voyageur\n"
              and any("lasciate in originale" in r and "Open the old chest" in r for r in registro))

    con_it = os.path.join(base, "Megaloot con italiano")
    gioco_megaloot(con_it, italiano=True)
    percorso, _, esito = automatico.analizza_e_traduci(con_it, "auto", "it", "dizionario", silenzio)
    controlla("colonna italian piena: lingua ufficiale (nessuna traduzione automatica)",
              percorso is None and esito["esito"].codice == "UFFICIALE"
              and lingua_ufficiale.lingue_nei_file(con_it, "GameMaker", "it")[1]["trovata"] is True)

    # generic search (web game): the table becomes an entry of the game profile
    web = os.path.join(base, "web con csv")
    scrivi_byte(os.path.join(web, "index.html"), b"<html><body><button>Start</button></body></html>")
    scrivi_byte(os.path.join(web, "lang", "translations.csv"), MEGALOOT.encode("utf-8"))
    scrivi_byte(os.path.join(web, "lang", "sorgente.csv"), MEGALOOT.encode("utf-8"))
    scrivi_byte(os.path.join(web, "lang", "sorgente.csv.import"), b"[remap]\n")  # Godot source: not touched
    candidati, altri = scansione.scansiona(web, silenzio)
    profilo = scansione.crea_profilo("web-csv", web, candidati, "auto", "it", "finto")
    controlla("ricerca generica: tabella CSV come voce del profilo (il sorgente di Godot no)",
              [v for v in profilo["file"] if v.get("formato") == "csv"] == [
                  {"percorso": "lang/translations.csv", "formato": "csv", "colonna": "english", "localizzazione": True}]
              and "lang/sorgente.csv" not in [c["percorso"] for c in candidati]
              and "lang/sorgente.csv" not in [p for elenco in altri.values() for p in elenco])


# --- 4. GameMaker and XNA from the generic search -------------------------------------------------------

UI = {"btn_play": "PLAY", "btn_quit": "quit", "item_wood": "wood", "item_stone": "stone", "item_coal": "Coal",
      "msg_bag": "Your bag is full", "msg_saved": "Everything is saved", "icon": "icons/wood.png"}
UI.update({"msg_%02d" % i: "The lantern number %d is now lit" % i for i in range(20)})


def prova_generico(base):
    gioco = os.path.join(base, "Isola GM")
    scrivi_byte(os.path.join(gioco, "texts", "ui.json"), json.dumps(UI, indent=1).encode("utf-8"))
    senza_e = set(chr(c) for c in range(32, 127)) | set("àéìòù")  # game font without è
    scrivi_byte(os.path.join(gioco, "data.win"), test_gamemaker.data_win([("fntMain", senza_e)]))
    scrivi_byte(os.path.join(gioco, "Isola.exe"), b"MZ")
    _, esito = automatico.prepara(gioco, "auto", "it", "dizionario", silenzio)
    controlla("GameMaker senza file di lingua riconosciuti: si propone la traduzione sperimentale",
              esito["esito"].codice == "NON_SUPPORTATO" and "sperimentale" in esito["esito"].azioni)
    registro = []
    percorso, esito = automatico.prepara(gioco, "auto", "it", "dizionario", registro.append, forza=True)
    profilo = json.load(open(percorso, encoding="utf-8"))
    voce = profilo["file"][0]
    controlla("profilo generico con il motore (gamemaker) e il file {chiave: testo} come file di lingua",
              profilo.get("motore") == "gamemaker" and profilo.get("motore_gioco") == "gamemaker"
              and voce["percorso"] == "texts/ui.json" and voce.get("localizzazione")
              and "item_wood" in voce["includi"] and "btn_play" in voce["includi"] and "icon" not in voce["includi"])
    p = progetto.Progetto(percorso, silenzio)
    p.traduci(Traduttore())
    dati = json.load(open(os.path.join(gioco, "texts", "ui.json"), encoding="utf-8"))
    controlla("tradotte le etichette di una parola (wood, stone, PLAY, quit) e lasciati gli identificativi",
              dati["item_wood"] == "legno" and dati["item_stone"] == "pietra" and dati["btn_play"] == "GIOCA"
              and dati["btn_quit"] == "esci" and dati["icon"] == "icons/wood.png")
    controlla("caratteri adattati ai font del gioco (è -> e', È -> E'; à resta)",
              dati["msg_bag"] == "La tua borsa e' piena" and dati["msg_saved"] == "E' tutto salvato")

    # before point 8 a generic game profile did not have the engine: it gets it at the next analysis
    vecchio = dict(profilo)
    vecchio.pop("motore")
    vecchio.pop("motore_gioco")
    scansione.salva_json(percorso, vecchio)
    automatico.prepara(gioco, "auto", "it", "dizionario", silenzio, forza=True)
    controlla("profilo generico vecchio senza motore: aggiornato", json.load(open(percorso, encoding="utf-8")).get(
        "motore") == "gamemaker")

    # a data file with a list of objects (like the PluginInfo.json of Wwise sounds: name, type and library of each
    # effect) is not a text table even if many values look like words: 'Effect' or 'Windows' translated would break
    # the audio. A table with sections ({"menu": {"_play": "Play"}}) instead is
    dati_audio = os.path.join(base, "Dati audio")
    librerie = [{"LibName": "Sound %s" % n, "LibId": str(1000 + i), "Type": "Effect", "DLL": "Snd%s" % n,
                 "StaticLib": "Snd%sFX" % n} for i, n in enumerate(("Echo", "Delay", "Chorus", "Limiter"))]
    librerie += [{"LibName": n, "LibId": str(2000 + i), "Type": "AudioDevice"}
                 for i, n in enumerate(("System", "Main Speaker", "No Output"))]
    scrivi_byte(os.path.join(dati_audio, "Audio", "PluginInfo.json"), json.dumps(
        {"PluginInfo": {"Platform": "Windows", "BasePlatform": "Windows", "PluginLibs": librerie,
                        "FileHash": "{0000-1111}"}}, indent=1).encode("utf-8"))
    scrivi_byte(os.path.join(dati_audio, "text", "menu.json"), json.dumps(
        {"menu": {"_play": "Play", "_quit": "Quit", "_ok": "OK"}, "shop": {"_k%d" % i: "Buy the item number %d" % i
                                                                           for i in range(20)}}).encode("utf-8"))
    # a family of files per language with a list of objects: the identifiers ('sword') stay out
    for codice, nome in (("en", "The sharp sword with a golden handle number %d"),
                         ("fr", "L'epee tranchante avec une poignee en or numero %d")):
        scrivi_byte(os.path.join(dati_audio, "items", codice + ".json"), json.dumps(
            {"items": [{"id": "sword%d" % i, "kind": "weapon", "name": nome % i} for i in range(12)]}).encode("utf-8"))
    scrivi_byte(os.path.join(dati_audio, "Saves", "progress.json"), json.dumps(
        {"slot%02d" % i: ("Forest Camp", "High", "On", "level_%d" % i)[i % 4] for i in range(24)}).encode("utf-8"))
    candidati, _ = scansione.scansiona(dati_audio, silenzio)
    profilo = scansione.crea_profilo("audio", dati_audio, candidati, "auto", "it", "finto")
    voci = {v["percorso"]: v for v in profilo["file"]}
    controlla("ricerca generica: un elenco di oggetti (PluginInfo.json) e un salvataggio {chiave: testo} non sono "
              "file di lingua e restano fuori; la tabella a sezioni si' (anche Play, Quit, OK)",
              "Audio/PluginInfo.json" not in voci and "Saves/progress.json" not in voci
              and voci.get("text/menu.json", {}).get("localizzazione")
              and {"menu._play", "menu._ok"} <= set(voci["text/menu.json"]["includi"]))
    controlla("famiglia di file per lingua con un elenco di oggetti: non e' un file di lingua (gli id e i tipi non si "
              "traducono, i nomi si')", not adapter_gamemaker.file_json_lingue(dati_audio)
              and not voci.get("items/en.json", {}).get("localizzazione")
              and voci.get("items/en.json", {}).get("includi") == ["items[].name"])

    # XNA: generic game profile with the detected engine (the game's XNB fonts are simulated: ASCII only)
    xna = os.path.join(base, "Isola XNA")
    scrivi_byte(os.path.join(xna, "Content", "lang.json"), json.dumps(UI).encode("utf-8"))
    candidati, _ = scansione.scansiona(xna, silenzio)
    profilo = scansione.crea_profilo("isola-xna", xna, candidati, "auto", "it", "dizionario", motore="XNA")
    controlla("profilo generico XNA: motore_gioco xna, senza motore (non e' il percorso delle risorse .NET)",
              profilo.get("motore_gioco") == "xna" and "motore" not in profilo)
    percorso = os.path.join(automatico.cartella_profili(), "isola-xna.json")
    scansione.salva_json(percorso, profilo)
    vero = adapter_xna.caratteri_font
    adapter_xna.caratteri_font = lambda cartella, log=None: set(ASCII)
    try:
        p = progetto.Progetto(percorso, silenzio)
        p.traduci(Traduttore())
    finally:
        adapter_xna.caratteri_font = vero
    dati = json.load(open(os.path.join(xna, "Content", "lang.json"), encoding="utf-8"))
    controlla("XNA: un carattere che i font non hanno (crash del gioco) viene adattato: è -> e', à no",
              p._font_del_gioco() == "xna" and dati["msg_bag"] == "La tua borsa e' piena"
              and dati["item_wood"] == "legno" and all(c in ASCII for v in dati.values() for c in v))


# --- 5. GameMaker fonts -----------------------------------------------------------------------------

def prova_font(base):
    gioco = os.path.join(base, "Font GM")
    cirillico = set(chr(c) for c in range(32, 127)) | {chr(0x410 + i) for i in range(64)} | set("é")
    scrivi_byte(os.path.join(gioco, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATINO), ("ftFusionJP", GIAPPONESE), ("scribble_fallback_font", set(ASCII)),
         ("fntChat", set(ASCII))]))
    righe = []
    ufficiali = "àâçèéêëîïôùûü"  # letters of the official translations (French)
    ammessi = adapter_gamemaker.caratteri_font(gioco, righe.append, ufficiali)
    controlla("font esclusi: giapponese con ç ê ñ (un'altra scrittura), di ripiego, e quello senza nessuna lettera "
              "delle traduzioni ufficiali (chat)",
              ammessi is not None and set("àèéìòù") <= ammessi and any("fntChat" in r for r in righe)
              and any("scribble_fallback_font" in r for r in righe) and not any("ftFusionJP" in r for r in righe))
    # like Another Door's ftPixeloidSans: ASCII, Cyrillic and only the accented letters of the English file (Á ç é ô).
    # It may be the font of the English texts, so it counts even with the official translations: è not allowed
    scrivi_byte(os.path.join(gioco, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATINO), ("fntPixel", cirillico | set("Áçô"))]))
    righe = []
    ammessi = adapter_gamemaker.caratteri_font(gioco, righe.append, ufficiali)
    controlla("un font con il cirillico e poche lettere accentate conta anche con le traduzioni ufficiali (è no, é si')",
              ammessi is not None and "è" not in ammessi and "é" in ammessi and not righe)
    ammessi = adapter_gamemaker.caratteri_font(gioco)
    controlla("senza traduzioni ufficiali il font cirillico conta ancora (per sicurezza: è no, é si')",
              ammessi is not None and "è" not in ammessi and "é" in ammessi)
    scrivi_byte(os.path.join(gioco, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATINO), ("fntPiccolo", set(ASCII) | set("é"))]))
    ammessi = adapter_gamemaker.caratteri_font(gioco)
    controlla("un font latino senza accenti conta ancora (per sicurezza: è non ammessa)",
              ammessi is not None and "è" not in ammessi and "é" in ammessi)


# --- 6. web: one-word buttons ----------------------------------------------------------------------

def prova_web(base):
    web = os.path.join(base, "web")
    os.makedirs(web)
    shutil.copyfile(os.path.join(QUI, "web", "index-inline.html"), os.path.join(web, "index.html"))
    testi = adapter_webui.estrai_testi(web)
    controlla("test/web/index-inline.html: i pulsanti PLAY e OK sono nel dizionario (con gli altri testi)",
              "PLAY" in testi and "OK" in testi and "Quit Game" in testi and "Delete" in testi)
    codice = ('function m(){var b=document.getElementById("b");b.textContent="QUIT";ctx.fillText("GO",1,2);'
              'var x={label:"Back",id:"left"};var k="left";el.className="NONE";'
              'this.add.text(1,2,"MENU",{fontFamily:"Arial",align:"center"});b.innerText="ok";'
              'if(a.value=="yes")b.title="Options";s.setAttribute("title","x");var t="Continue";var n="retry";}')
    scrivi_byte(os.path.join(web, "gioco.js"), codice.encode("utf-8"))
    testi = adapter_webui.estrai_testi(web)
    controlla("JavaScript: parole sole solo dove di sicuro si vedono (textContent, fillText, label:, add.text, "
              "innerText, title=)", all(t in testi for t in ("QUIT", "GO", "Back", "MENU", "ok", "Options")))
    controlla("JavaScript: restano fuori identificativi, classi, font, confronti e attributi",
              not any(t in testi for t in ("left", "NONE", "Arial", "center", "yes", "title", "retry"))
              and "Continue" in testi)
    controlla("lettore del JavaScript: senza contesto le stringhe sono le stesse di prima",
              list(adapter_webui.letterali_js(codice)) == [s for s, _ in adapter_webui.letterali_js(codice, True)])
    controlla("parole sole mostrate: PLAY, OK, quit, Co-op, Quit? si'; camelCase, snake_case, numeri, tasti no",
              all(adapter_webui.e_testo_mostrato(t) for t in ("PLAY", "OK", "quit", "Co-op", "Quit?"))
              and not any(adapter_webui.e_testo_mostrato(t) for t in ("onClick", "btn_ok", "x2", "Esc", "a", "F5")))
    vero = adapter_webui.MAX_DIMENSIONE
    adapter_webui.MAX_DIMENSIONE = 200
    righe = []
    try:
        adapter_webui.estrai_testi(web, righe.append)
    finally:
        adapter_webui.MAX_DIMENSIONE = vero
    controlla("file troppo grandi saltati: scritti nel registro con quanti sono",
              any("troppo grandi" in r and "index.html" in r for r in righe))


# --- 7. Unreal -----------------------------------------------------------------------------------

def test_unreal(base):
    adapter_unreal.gioco_aperto = lambda c: False
    senza = os.path.join(base, "Unreal senza locres")
    paks = os.path.join(senza, "Proj", "Content", "Paks")
    scrivi_byte(os.path.join(paks, "Proj-WindowsNoEditor.pak"),
                unreal_finto.pak_v9([("Proj/Content/Maps/Level.umap", b"mappa" * 50)]))
    os.makedirs(os.path.join(senza, "Engine", "Binaries"))
    profili_prima = sorted(os.listdir(automatico.cartella_profili())) if os.path.isdir(
        automatico.cartella_profili()) else []
    registro = []
    risultato = automatico._prepara_unreal(senza, None, "auto", "it", {"risultati": []}, registro.append)
    motivo = risultato[1]["esito"].motivo
    controlla("Unreal senza .locres: non supportato con il motivo, nessun profilo lasciato",
              risultato[0] is None and risultato[1]["esito"].codice == "NON_SUPPORTATO" and ".locres" in motivo
              and "non sa tradurre" in motivo and sorted(os.listdir(automatico.cartella_profili()) if os.path.isdir(
                  automatico.cartella_profili()) else []) == profili_prima)
    misto = os.path.join(base, "Unreal con pak rotto")
    unreal_finto.crea_gioco(misto)
    scrivi_byte(os.path.join(misto, "Proj", "Content", "Paks", "Proj-Rotto.pak"), b"non e' un pak" * 20)
    saltati = []
    sorgenti = adapter_unreal.trova_locres(misto, saltati)
    registro = []
    percorso, esito = automatico._prepara_unreal(misto, None, "auto", "it", {"risultati": []}, registro.append)
    controlla("un pak illeggibile si salta: il .locres degli altri si trova e il profilo si crea",
              [s[1] for s in sorgenti] == ["Proj/Content/Localization/Game/en/Game.locres"]
              and len(saltati) == 1 and "Proj-Rotto.pak" in saltati[0] and percorso and os.path.exists(percorso)
              and any("archivi non letti" in r and "Proj-Rotto.pak" in r for r in registro))

    # small pak mounted in a deeper folder (Remnant: '../../../Remnant/Content/'), language in the .locmeta of
    # another pak
    montato = os.path.join(base, "Unreal montato")
    paks = os.path.join(montato, "Proj", "Content", "Paks")
    os.makedirs(os.path.join(montato, "Engine", "Binaries"))
    voci = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit")]
    scrivi_byte(os.path.join(paks, "pakchunk0-WindowsNoEditor.pak"), unreal_finto.pak_v9(
        [("Proj/Content/Localization/Game/Game.locmeta", unreal_finto.locmeta("en"))]))
    scrivi_byte(os.path.join(paks, "pakchunk1-WindowsNoEditor.pak"), pak_montato("../../../Proj/Content/", [
        ("Localization/Game/en/Game.locres", unreal_finto.locres_v2(voci)),
        ("Localization/Game/de/Game.locres", unreal_finto.locres_v2([("UI", "Start", "Spiel starten")]))]))
    sorgenti = adapter_unreal.trova_locres(montato)
    _, testi, cultura = adapter_unreal.testi_da_tradurre(montato, silenzio)
    n = adapter_unreal.installa(montato, "it", "montato", {"Start Game": "Inizia la partita", "Quit": "Esci"}, silenzio)
    patch = adapter_unreal.Pak(os.path.join(paks, adapter_unreal.nome_pak("it")))
    tradotto = adapter_unreal.leggi_locres(patch.leggi("Proj/Content/Localization/Game/en/Game.locres"))
    controlla("pak montato in una cartella piu' profonda: .locres trovato con il nome completo, tradotto in un pak di "
              "patch con il punto di montaggio della radice",
              [s[1] for s in sorgenti] == ["Proj/Content/Localization/Game/en/Game.locres"] and cultura == "en"
              and sorted(testi) == ["Quit", "Start Game"] and n == 2 and patch.mount == "../../../"
              and sorted(patch.voci) == ["Proj/Content/Localization/Game/en/Game.locres"]
              and [s for s, _ in tradotto["stringhe"]] == ["Inizia la partita", "Esci"])
    tedesco = os.path.join(base, "Unreal tedesco")
    scrivi_byte(os.path.join(tedesco, "Proj", "Content", "Paks", "pakchunk0-WindowsNoEditor.pak"), unreal_finto.pak_v9(
        [("Proj/Content/Localization/Game/Game.locmeta", unreal_finto.locmeta("de"))]))
    scrivi_byte(os.path.join(tedesco, "Proj", "Content", "Paks", "pakchunk1-WindowsNoEditor.pak"),
                pak_montato("../../../Proj/Content/Localization/Game/", [
                    ("en/Game.locres", unreal_finto.locres_v2(voci)),
                    ("de/Game.locres", unreal_finto.locres_v2([("UI", "Start", "Spiel starten")]))]))
    controlla("lingua originale dal .locmeta di un altro pak (de): si traduce il .locres tedesco",
              [(s[1], s[3]) for s in adapter_unreal.trova_locres(tedesco)]
              == [("Proj/Content/Localization/Game/de/Game.locres", "de")])
    sciolti = os.path.join(base, "Unreal sciolto")
    scrivi_byte(os.path.join(sciolti, "Proj", "Content", "Paks", "Proj-WindowsNoEditor.pak"),
                unreal_finto.pak_v9([("Proj/Content/Maps/Level.umap", b"mappa" * 50)]))
    scrivi_byte(os.path.join(sciolti, "Proj", "Content", "Localization", "Game", "en", "Game.locres"),
                unreal_finto.locres_v2(voci))
    _, esito = automatico._prepara_unreal(sciolti, None, "auto", "it", {"risultati": []}, silenzio)
    controlla("Unreal con i .locres fuori dagli archivi: il motivo non dice che non ci sono testi localizzati",
              esito["esito"].codice == "NON_SUPPORTATO" and "non sa ancora tradurre" in esito["esito"].motivo)


def pak_montato(mount, file):
    """Uncompressed pak v9 with a mount point of choice (the names are relative to it), like pak_v9 of
    test/test_unreal.py."""
    corpo, voci = b"", []
    for nome, dati in file:
        h = hashlib.sha1(dati).digest()
        def voce(off, d=dati, h=h):
            return struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00" + struct.pack("<I", 0)
        voci.append((nome, len(corpo), voce))
        corpo += voce(0) + dati
    indice = unreal_finto.fs(mount) + struct.pack("<i", len(voci)) + b"".join(unreal_finto.fs(n) + v(o)
                                                                            for n, o, v in voci)
    coda = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(corpo), len(indice))
            + hashlib.sha1(indice).digest() + b"\x00" + b"\x00" * 160)
    return corpo + indice + coda


# --- 8. log of the files skipped by the generic search -----------------------------------------------

def prova_registro(base):
    cartella = os.path.join(base, "saltati")
    scrivi_byte(os.path.join(cartella, "rotto.json"), b"{non json")
    scrivi_byte(os.path.join(cartella, "pochi.json"), json.dumps({"a": "Hello there friend", "b": "x"}).encode())
    scrivi_byte(os.path.join(cartella, "testi.json"), json.dumps(
        {"t%d" % i: "This is the sentence number %d of the game" % i for i in range(30)}).encode())
    vero = scansione.MAX_DIMENSIONE_JSON
    scansione.MAX_DIMENSIONE_JSON = 1000
    righe = []
    try:
        candidati, _ = scansione.scansiona(cartella, righe.append)
    finally:
        scansione.MAX_DIMENSIONE_JSON = vero
    controlla("ricerca generica: JSON non validi e troppo grandi scritti nel registro con quanti sono",
              any("non sono JSON validi" in r and "rotto.json" in r for r in righe)
              and any("troppo grandi" in r and "testi.json" in r for r in righe))
    candidati, _ = scansione.scansiona(cartella, silenzio)
    righe = []
    profilo = scansione.crea_profilo("saltati", cartella, candidati, "auto", "it", "finto", log=righe.append)
    controlla("file con pochi testi fuori dal profilo, scritti nel registro con i numeri",
              [v["percorso"] for v in profilo["file"]] == ["testi.json"]
              and any("pochi.json (1 testi" in r for r in righe))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    prepara_dati(base)
    prova_json_tollerante(base)
    prova_porta(base)
    prova_csv(base)
    prova_generico(base)
    prova_font(base)
    prova_web(base)
    test_unreal(base)
    prova_registro(base)
    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
