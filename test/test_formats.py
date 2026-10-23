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

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from patchsmith import (adapter_csv, adapter_gamemaker, adapter_unreal, adapter_webjson, adapter_webui, adapter_xna,
                        auto as automatic, backend, interruption,
                        official_language, project, scan, steam)
from patchsmith.characters import ASCII  # noqa: E402
import test_gamemaker  # noqa: E402  (date.win builder)
import test_unreal as fake_unreal  # noqa: E402  (pak and .locres builder)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


def write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def fingerprints(folder):
    return {os.path.relpath(os.path.join(r, n), folder): hashlib.sha256(read_bytes(os.path.join(r, n))).hexdigest()
            for r, _, fs in os.walk(folder) for n in fs if "patchsmith" not in n}


# --- fake translator -------------------------------------------------------------------------------

ITALIAN = {
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


def mock(r):
    """Recognizable fake translation: words reversed (no common English words, placeholders intact)."""
    return re.sub(r"[A-Za-z]{2,}", lambda m: m.group(0)[::-1].lower() + "o", r)


class Translator(backend.Backend):
    name = "dizionario"

    def __init__(self):
        self.received = []

    def translate_lines(self, lines, origin, dest_lang, *_a, **_k):
        interruption.check()
        self.received += list(lines)
        return [ITALIAN.get(r, mock(r)) for r in lines]


def prepare_data(base):
    project.DATA_ROOT = os.path.join(base, "dati")  # never the real data
    automatic.PROFILES_FOLDER = None
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    steam.steam_folder = lambda: None
    steam.game_installed = lambda folder, steam=None: None
    steam.identify_from_executables = lambda folder: None
    automatic.create_backend = lambda name, log=print: Translator()


# --- 1. Lenient JSON --------------------------------------------------------------------------------

def _door(values, language_name):
    """A language file in the style of Another Door (CRLF, tabs, comments, trailing commas, sections). 'valori':
    {(sezione, chiave): testo}; the literals written as json.dumps, except an identifier written by hand with an
    unusual escape (it must stay identical) and some numbers written in a particular way (1.50, 1e5)."""
    def s(section, key):
        return json.dumps(values[(section, key)], ensure_ascii=False)
    lines = ["{", "", "\"meta\": {", "\t\"_langNameLocal\":%s," % json.dumps(language_name, ensure_ascii=False),
             "},", "",
             "// Menu principale: i nomi dei tasti restano come sono (grazie ad Ana \u00c1valos)", "\"menu\": {"]
    for key in ("_play", "_quit", "_ok", "_return", "_inviteFriends", "_clickToJoin", "_site"):
        lines.append("\t\"%s\":%s," % (key, s("menu", key)))
    lines += ["\t\"_id\":\"Structure\\u002ENuclearFurnace\",", "\t\"_scala\": [1.50, 1e5, true, null],", "},",
              "/* sezione delle carte", "   (commento su piu' righe, con \"virgolette\" e // dentro) */",
              "\"cards\": {"]
    for key in sorted(k for (sec, k) in values if sec == "cards"):
        lines.append("\t\"%s\":%s," % (key, s("cards", key)))
    lines += ["\t}", "}", "", "", "//Comments like this technically violate the JSON standard, but the game filters "
              "them out", ""]
    return "\r\n".join(lines).encode("utf-8")


EN_DOOR = {("menu", "_play"): "Play", ("menu", "_quit"): "QUIT", ("menu", "_ok"): "OK", ("menu", "_return"): "return",
            ("menu", "_inviteFriends"): "Invite \"friends\"",
            ("menu", "_clickToJoin"): "Push a button to join the game.\n(Esc. or Start to leave the game)",
            ("menu", "_site"): "Visit https://example.com // not a comment",
            ("cards", "_vulture"): "VULTURE TOKEN",
            ("cards", "_vultureDesc"): "Each time a player dies, receive [yellow1]{0}[/c] HP."}
EN_DOOR.update({("cards", "_card%02d" % i): "Card number %d does something to the dungeon" % i for i in range(15)})
FR_DOOR = {k: "Le texte français %d de la carte" % n for n, k in enumerate(EN_DOOR)}
DE_DOOR = {k: "Der deutsche Text %d der Karte" % n for n, k in enumerate(EN_DOOR)}
IT_DOOR = {k: "Il testo italiano %d della carta è qui" % n for n, k in enumerate(EN_DOOR)}


def _port_values(data):
    return {(sec, k): v for sec in ("menu", "cards") for k, v in data[sec].items() if isinstance(v, str)
            and not k.startswith("_id")}


def test_lenient_json(base):
    folder = os.path.join(base, "json")
    p = os.path.join(folder, "stringsEN.json")
    original = _door(EN_DOOR, "English")
    write_bytes(p, original)
    try:
        json.loads(original.decode("utf-8"))
        strict = True
    except ValueError:
        strict = False
    f = adapter_webjson.JSONFile(p)
    check("JSON con commenti e virgole in fondo: non e' JSON rigoroso, ma si legge (sezioni comprese)",
              not strict and f.lenient and f.data["menu"]["_play"] == "Play"
              and f.data["menu"]["_site"] == "Visit https://example.com // not a comment"
              and f.data["menu"]["_scala"] == [1.5, 100000.0, True, None]
              and f.data["menu"]["_id"] == "Structure.NuclearFurnace")
    check("riscritto senza cambiamenti: identico byte per byte", f.serialize(f.data) == original)
    new_items = json.loads(json.dumps(f.data))
    new_items["menu"]["_play"] = "Gioca \"ora\""
    new_items["cards"]["_vulture"] = "GETTONE è così"
    written = f.serialize(new_items)
    expected = original.replace(b'"_play":"Play"', b'"_play":"Gioca \\"ora\\""').replace(
        b'"_vulture":"VULTURE TOKEN"', '"_vulture":"GETTONE è così"'.encode("utf-8"))
    check("riscritto con due testi cambiati: identico tranne quei due letterali (commenti, virgole in fondo, "
              "numeri 1.50 e 1e5, escape \\u002E, CRLF)", written == expected and expected != original)
    check("riletto con le regole del gioco (commenti tolti, niente NaN)",
              adapter_webjson.reread_strict(written, True)["menu"]["_play"] == "Gioca \"ora\"")
    bom = os.path.join(folder, "bom.json")
    write_bytes(bom, b"\xef\xbb\xbf" + original)
    fb = adapter_webjson.JSONFile(bom)
    check("con il BOM: resta il BOM", fb.bom and fb.serialize(fb.data) == b"\xef\xbb\xbf" + original)
    ascii_only = os.path.join(folder, "ascii.json")
    write_bytes(ascii_only, b'// solo ASCII\n{"a": "Caf\\u00e9", "b": "Tea",}\n')
    fa = adapter_webjson.JSONFile(ascii_only)
    check("file tutto ASCII con gli escape \\u: il testo nuovo si scrive allo stesso modo",
              fa.serialize({"a": "Caf\u00e9", "b": "T\u00e8"})
              == b'// solo ASCII\n{"a": "Caf\\u00e9", "b": "T\\u00e8",}\n')
    broken = json.loads(json.dumps(f.data))
    broken["menu"]["_scala"][0] = 2
    try:
        f.serialize(broken)
        refuse = False
    except ValueError:
        refuse = True
    check("un valore che non e' una stringa cambiato: non si scrive (ValueError)", refuse)
    for name, text in (("vero.json", b"{\"a\": [1, 2,], \"b\": \"x\" // fine\n}"),
                        ("rotto.json", b"{\"a\": \"senza chiusura\""), ("non_json.json", b"<html>no</html>")):
        write_bytes(os.path.join(folder, name), text)
    was_read = adapter_webjson.JSONFile(os.path.join(folder, "vero.json")).data
    errors = 0
    for name in ("rotto.json", "non_json.json"):
        try:
            adapter_webjson.JSONFile(os.path.join(folder, name))
        except ValueError:
            errors += 1
    check("virgola in fondo a una lista e commento a fine riga; un file rovinato resta un errore",
              was_read == {"a": [1, 2], "b": "x"} and errors == 2)
    strict_file = os.path.join(folder, "rigido.json")
    write_bytes(strict_file, b'{"a": "x", "b": 1.50}')
    check("un JSON rigoroso si legge e si scrive come prima (non tollerante)",
              not adapter_webjson.JSONFile(strict_file).lenient)


# --- 2. GameMaker: files per language in the style of Another Door -------------------------------------

LATIN = set(chr(c) for c in range(32, 127)) | set("àèéìòùÀÈÉÌÒÙçâêôûœëïüöÁáíóúñ")
JAPANESE = set(chr(c) for c in range(32, 127)) | set("çêñŒœ") | {chr(0x4E00 + i) for i in range(300)}


def door_game(folder, languages=("EN", "FR", "DE")):
    values = {"EN": EN_DOOR, "FR": FR_DOOR, "DE": DE_DOOR, "IT": IT_DOOR}
    names = {"EN": "English", "FR": "Français", "DE": "Deutsch", "IT": "Italiano"}
    for code in languages:
        write_bytes(os.path.join(folder, "localization", "strings%s.json" % code),
                    _door(values[code], names[code]))
    write_bytes(os.path.join(folder, "localization", "availableLanguages.json"),
                b'//note\r\n{"langs": [{"stringsFile": "stringsEN.json",},],}\r\n')
    write_bytes(os.path.join(folder, "data.win"), test_gamemaker.data_win(
        [("ftText", LATIN), ("ftFusionJP", JAPANESE), ("scribble_fallback_font", set(ASCII))]))
    write_bytes(os.path.join(folder, "Porta.exe"), b"MZ")


def _skeleton(text):
    """The text with every string literal replaced by S: what must stay identical."""
    return re.sub(rb'"(?:[^"\\]|\\.)*"', b"S", text)


def test_door(base):
    game = os.path.join(base, "Porta finta")
    door_game(game)
    loc = os.path.join(game, "localization")
    check("codice attaccato al nome: stringsEN -> en, stringsBR -> pt, stringsSC -> zh, stringsTC -> zh-TW",
              adapter_gamemaker.path_language("localization/stringsEN.json") == "en"
              and adapter_gamemaker.path_language("localization/stringsBR.json") == "pt"
              and adapter_gamemaker.path_language("localization/stringsSC.json") == "zh"
              and adapter_gamemaker.path_language("localization/stringsTC.json") == "zh-TW"
              and adapter_gamemaker.path_language("data/levelUP.json") is None
              and adapter_gamemaker.path_language("data/strings.json") is None)
    check("stessa famiglia: localization/strings{L}.json",
              adapter_gamemaker.family("localization/stringsEN.json") == "localization/strings{L}.json"
              == adapter_gamemaker.family("localization/stringsIT.json")
              and adapter_gamemaker.family("localization/stringsEN.json") != adapter_gamemaker.family(
                  "localization/textEN.json"))
    found = adapter_gamemaker.language_json_files(game)
    check("file di lingua con commenti, virgole in fondo e sezioni riconosciuti (en, fr, de)",
              found == {"localization/stringsEN.json": "en", "localization/stringsFR.json": "fr",
                          "localization/stringsDE.json": "de"})
    check("lingue nei file: senza italiano non c'e' la lingua ufficiale",
              official_language.languages_in_files(game, "GameMaker", "it")[1]["trovata"] is False)
    lines = []
    allowed = adapter_gamemaker.font_chars(game, lines.append)
    check("font: non contano quello giapponese (con ç ê ñ Œ œ) ne' scribble_fallback_font: è e ò ammesse",
              allowed is not None and "è" in allowed and "ò" in allowed and "ì" in allowed
              and any("scribble_fallback_font" in r for r in lines))
    before = fingerprints(game)
    original = read_bytes(os.path.join(loc, "stringsEN.json"))

    translator = Translator()
    automatic.create_backend = lambda name, log=print: translator
    log_file = []
    path, completed, outcome = automatic.analyze_and_translate(game, "auto", "it", "dizionario", log_file.append)
    profile = json.load(open(path, encoding="utf-8"))
    entry = profile["file"][0] if profile.get("file") else {}
    check("profilo GameMaker: solo stringsEN.json, file di lingua con le chiavi delle sezioni",
              profile.get("motore") == "gamemaker" and [v["percorso"] for v in profile["file"]]
              == ["localization/stringsEN.json"] and entry.get("localizzazione")
              and "menu._play" in entry.get("includi", []) and "menu._id" not in entry.get("includi", []))
    written = read_bytes(os.path.join(loc, "stringsEN.json"))
    data = adapter_webjson.read_lenient(written.decode("utf-8"))
    check("tradotte anche le etichette di una parola (Play, QUIT, OK, return) e il nome della lingua",
              data["menu"]["_play"] == "Gioca" and data["menu"]["_quit"] == "ESCI" and data["menu"]["_ok"] == "Va bene"
              and data["menu"]["_return"] == "indietro" and data["meta"]["_langNameLocal"] == "Italiano")
    check("tag di Scribble e segnaposto rimessi; accenti è e ò scritti (i font li hanno)",
              data["cards"]["_vultureDesc"] == "Ogni volta che un giocatore muore, ricevi [yellow1]{0}[/c] PS."
              and "è così" in data["menu"]["_clickToJoin"] and "può" in data["menu"]["_clickToJoin"]
              and "Each time a player dies, receive [[0]][[1]][[2]] HP." in translator.received)
    check("riscritto identico tranne i letterali tradotti: commenti, virgole in fondo, CRLF, numeri, escape",
              _skeleton(written) == _skeleton(original) and written != original
              and b"// Menu principale" in written and b"/* sezione delle carte" in written
              and b"[1.50, 1e5, true, null]" in written and b'"_id":"Structure\\u002ENuclearFurnace"' in written
              and _door(dict(_port_values(data)), data["meta"]["_langNameLocal"]) == written)
    changed_files = sorted(k for k in fingerprints(game) if fingerprints(game)[k] != before.get(k))
    check("solo stringsEN.json cambiato (le traduzioni ufficiali e i dati del gioco intatti)",
              changed_files == [os.path.join("localization", "stringsEN.json")])
    automatic.restore(game, silent)
    check("ripristino: identico all'originale", read_bytes(os.path.join(loc, "stringsEN.json")) == original
              and fingerprints(game) == before)
    automatic.create_backend = lambda name, log=print: Translator()

    with_it = os.path.join(base, "Porta con italiano")
    door_game(with_it, ("EN", "FR", "IT"))
    _, engine_data = official_language.languages_in_files(with_it, "GameMaker", "it")
    path, _, outcome = automatic.analyze_and_translate(with_it, "auto", "it", "dizionario", silent)
    check("stringsIT.json (anche con commenti): lingua ufficiale trovata, nessuna traduzione automatica",
              engine_data.get("trovata") is True and path is None and outcome["esito"].code == "UFFICIALE")


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


def megaloot_game(folder, italian=False):
    text = MEGALOOT
    if italian:  # full Italian column: official translation
        text = "\n".join(r + ("Testo italiano" if i else "") for i, r in enumerate(text.split("\n")))
    write_bytes(os.path.join(folder, "translations.csv"), text.encode("utf-8"))
    write_bytes(os.path.join(folder, "content.csv"), b"Name,Value,Growth\nArmor,1,1.25\nGold,1,1\n")
    write_bytes(os.path.join(folder, "data.win"), test_gamemaker.data_win([("fntMain", LATIN)]))
    write_bytes(os.path.join(folder, "Megaloot.exe"), b"MZ")


RFC = ("\ufeffkey;English;Français;Deutsch\r\n"
       "title;\"Hello; world\";\"Bonjour; monde\";\"Hallo; Welt\"\r\n"
       "quote;\"He said \"\"hi\"\"\";\"Il a dit \"\"salut\"\"\";\"Er sagte \"\"hallo\"\"\"\r\n"
       "multi;\"Line one\r\nLine two\";\"Ligne un\r\nLigne deux\";\"Zeile eins\r\nZeile zwei\"\r\n"
       "plain;Start;Démarrer;Starten\r\n"
       "hero;\"Hero\";\"Héros\";\"Held\"\r\n")


def test_csv(base):
    folder = os.path.join(base, "csv")
    p = os.path.join(folder, "translations.csv")
    write_bytes(p, MEGALOOT.encode("utf-8"))
    f = adapter_csv.read_file(p)
    check("tabella alla Megaloot: colonne riconosciute (koreana, schinese, tchinese), chiave 'id', divisa a mano",
              f.table() and f.key == 0 and f.languages == {1: "en", 2: "fr", 3: "de", 4: "ko", 5: "zh", 6: "zh-TW",
                                                             7: "it"}
              and f.separator == "," and not f.quotes and f.source() == (1, "en"))
    check("lingue piene: la colonna italiana vuota non e' una traduzione ufficiale",
              f.full_languages() == {"en", "fr", "de", "ko", "zh", "zh-TW"})
    check("testi della colonna inglese ('\"?\"' senza lettere no) e riscrittura senza cambiamenti identica",
              [t for _, t in f.texts(1)][:2] == ["Play", "QUIT"] and len(f.texts(1)) == 5
              and f.serialize({}) == MEGALOOT.encode("utf-8"))
    check("tabella divisa sulle virgole: ',' e '\"' non si possono scrivere e si adattano come fa il gioco (';')",
              f.unwritable('Ciao, "amico"') == ',"' and f.adapt('Ciao, "amico"') == "Ciao; 'amico'")
    subst = {(2, 1): "Gioca", (4, 1): "Ciao viaggiatore; benvenuto"}
    expected = MEGALOOT.replace("menu-play,Play,", "menu-play,Gioca,").replace(
        "shop-greeting,Hello traveler; welcome to the shop,", "shop-greeting,Ciao viaggiatore; benvenuto,")
    check("celle sostituite: il resto identico byte per byte (niente a capo finale, come l'originale)",
              f.serialize(subst) == expected.encode("utf-8"))
    try:
        f.serialize({(2, 1): "Gioca, ora"})
        refuse = False
    except adapter_csv.CSVError:
        refuse = True
    check("una virgola non adattata in una tabella divisa a mano: non si scrive", refuse)

    r = os.path.join(folder, "rfc.csv")
    write_bytes(r, RFC.encode("utf-8"))
    g = adapter_csv.read_file(r)
    check("tabella CSV vera (BOM, ';', CRLF, virgolette, \"\" e a capo dentro le celle)",
              g.table() and g.bom and g.separator == ";" and g.quotes and g.has_special and g.doubled
              and g.languages == {1: "en", 2: "fr", 3: "de"} and g.serialize({}) == RFC.encode("utf-8")
              and dict(g.texts(1))[(3, 1)] == "Line one\nLine two")
    new = g.serialize({(1, 1): "Ciao; mondo", (2, 1): 'Ha detto "ciao"', (3, 1): "Riga uno\nRiga due",
                          (4, 1): "Avvia", (5, 1): "Eroe"})
    expected = RFC.replace('title;"Hello; world";', 'title;"Ciao; mondo";').replace(
        'quote;"He said ""hi""";', 'quote;"Ha detto ""ciao""";').replace(
        'multi;"Line one\r\nLine two";', 'multi;"Riga uno\r\nRiga due";').replace("plain;Start;", "plain;Avvia;") \
        .replace('hero;"Hero";', 'hero;"Eroe";')
    check("tabella CSV vera riscritta: virgolette, \"\" e a capo CRLF come l'originale, il resto identico",
              new == expected.encode("utf-8"))
    write_bytes(os.path.join(folder, "senza_chiave.csv"),
                b"English,French\nHello there,Bonjour\nGood bye,Au revoir\nSee you,A plus\nThanks,Merci\n")
    write_bytes(os.path.join(folder, "una_lingua.csv"), b"id,english\na,Hello there\nb,Good bye\nc,See you\n")
    write_bytes(os.path.join(folder, "latino1.csv"), "id,english,french\na,Caf\u00e9,Caf\u00e9\n".encode("latin-1"))
    check("non sono tabelle: senza colonna delle chiavi, con una lingua sola, non UTF-8",
              adapter_csv.table(os.path.join(folder, "senza_chiave.csv")) is None
              and adapter_csv.table(os.path.join(folder, "una_lingua.csv")) is None
              and adapter_csv.table(os.path.join(folder, "latino1.csv")) is None
              and sorted(adapter_csv.tables(folder)) == ["rfc.csv", "translations.csv"])
    # table split by hand where the only cell that is not valid CSV is in the translated column: after the
    # translation the file would look like a CSV with quotes, but it is read back the way the game reads it
    by_hand = adapter_csv.CSVFile(b'id,english,french\nhero,"Hero" rises,Le heros\nbye,Good bye,Au revoir\n'
                                 b'see,"Free",Gratuit\n')
    try:
        written = by_hand.serialize({(1, 1): by_hand.adapt("\"L'eroe\" sorge"), (2, 1): "Arrivederci"})
    except adapter_csv.CSVError:
        written = None
    check("tabella divisa a mano con una cella '\"Hero\" rises' tradotta: scritta, il resto identico",
              not by_hand.quotes and [t for _, t in by_hand.texts(1)][0] == '"Hero" rises'
              and written == b"id,english,french\nhero,'L'eroe' sorge,Le heros\nbye,Arrivederci,Au revoir\n"
                             b'see,"Free",Gratuit\n')
    # empty lines between groups of texts (and at the end): the key column is recognized anyway
    groups = adapter_csv.CSVFile(b"key,English,German\n\nmenu_play,Play,Spielen\nmenu_quit,Quit,Beenden\n\n\n"
                                 b"shop_hi,Hello there,Hallo\nshop_bye,Good bye,Tschuss\n\n")
    check("righe vuote tra i gruppi: chiave riconosciuta, tabella valida, riscrittura identica",
              groups.key == 0 and groups.table() and len(groups.texts(1)) == 4
              and groups.serialize({(3, 1): "Esci"}) == b"key,English,German\n\nmenu_play,Play,Spielen\n"
                                                          b"menu_quit,Esci,Beenden\n\n\nshop_hi,Hello there,Hallo\n"
                                                          b"shop_bye,Good bye,Tschuss\n\n")

    # GameMaker with translations.csv
    game = os.path.join(base, "Megaloot finto")
    megaloot_game(game)
    before = fingerprints(game)
    original = read_bytes(os.path.join(game, "translations.csv"))
    path, completed, outcome = automatic.analyze_and_translate(game, "auto", "it", "dizionario", silent)
    profile = json.load(open(path, encoding="utf-8")) if path else {}
    check("GameMaker con tabella CSV: profilo con la colonna english (content.csv non e' una tabella di lingua)",
              profile.get("motore") == "gamemaker" and profile.get("file") == [
                  {"percorso": "translations.csv", "formato": "csv", "localizzazione": True, "colonna": "english"}])
    written = read_bytes(os.path.join(game, "translations.csv")).decode("utf-8")
    lines_o, lines_s = MEGALOOT.split("\n"), written.split("\n")
    other_ones = all(a.split(",")[:1] + a.split(",")[2:] == b.split(",")[:1] + b.split(",")[2:]
                for a, b in zip(lines_o, lines_s))
    check("tradotta la colonna english (la virgola della traduzione diventa ';'), le altre colonne identiche",
              "menu-play,Gioca," in written and "Ciao viaggiatore; benvenuto nel negozio" in written
              and len(lines_s) == len(lines_o) and other_ones and not written.endswith("\n")
              and "\"?\"-label,\"?\"," in written and "La tua borsa è piena" in written)
    check("solo translations.csv cambiato; ripristino identico",
              sorted(k for k in fingerprints(game) if fingerprints(game)[k] != before.get(k)) == ["translations.csv"]
              and automatic.restore(game, silent) and read_bytes(os.path.join(game, "translations.csv"))
              == original)
    # a translation with an extra line break is left untranslated (the game reads one line per text), the others
    # are written; the comma becomes ';' here too
    line_break = os.path.join(base, "Tabella a capo")
    write_bytes(os.path.join(line_break, "translations.csv"), (
        "id,english,french\nchest,Open the old chest,Ouvrir le vieux coffre\nbag,Your bag is full,Ton sac est plein\n"
        "play,Play,Jouer\nquit,QUIT,QUITTER\nshop,Hello traveler; welcome to the shop,Bonjour voyageur\n").encode())
    write_bytes(os.path.join(line_break, "data.win"), test_gamemaker.data_win([("fntMain", LATIN)]))
    write_bytes(os.path.join(line_break, "Tabella.exe"), b"MZ")
    log_file = []
    automatic.analyze_and_translate(line_break, "auto", "it", "dizionario", log_file.append)
    written = read_bytes(os.path.join(line_break, "translations.csv")).decode("utf-8")
    check("tabella: la traduzione con un a capo in piu' resta in originale (scritto nel registro), le altre si "
              "scrivono", written == "id,english,french\nchest,Open the old chest,Ouvrir le vieux coffre\n"
              "bag,La tua borsa è piena,Ton sac est plein\nplay,Gioca,Jouer\nquit,ESCI,QUITTER\n"
              "shop,Ciao viaggiatore; benvenuto nel negozio,Bonjour voyageur\n"
              and any("lasciate in originale" in r and "Open the old chest" in r for r in log_file))

    with_it = os.path.join(base, "Megaloot con italiano")
    megaloot_game(with_it, italian=True)
    path, _, outcome = automatic.analyze_and_translate(with_it, "auto", "it", "dizionario", silent)
    check("colonna italian piena: lingua ufficiale (nessuna traduzione automatica)",
              path is None and outcome["esito"].code == "UFFICIALE"
              and official_language.languages_in_files(with_it, "GameMaker", "it")[1]["trovata"] is True)

    # generic search (web game): the table becomes an entry of the game profile
    web = os.path.join(base, "web con csv")
    write_bytes(os.path.join(web, "index.html"), b"<html><body><button>Start</button></body></html>")
    write_bytes(os.path.join(web, "lang", "translations.csv"), MEGALOOT.encode("utf-8"))
    write_bytes(os.path.join(web, "lang", "sorgente.csv"), MEGALOOT.encode("utf-8"))
    write_bytes(os.path.join(web, "lang", "sorgente.csv.import"), b"[remap]\n")  # Godot source: not touched
    candidates, others = scan.scan_folder(web, silent)
    profile = scan.create_profile("web-csv", web, candidates, "auto", "it", "finto")
    check("ricerca generica: tabella CSV come voce del profilo (il sorgente di Godot no)",
              [v for v in profile["file"] if v.get("formato") == "csv"] == [
                  {"percorso": "lang/translations.csv", "formato": "csv", "colonna": "english", "localizzazione": True}]
              and "lang/sorgente.csv" not in [c["percorso"] for c in candidates]
              and "lang/sorgente.csv" not in [p for items in others.values() for p in items])


# --- 4. GameMaker and XNA from the generic search -------------------------------------------------------

UI = {"btn_play": "PLAY", "btn_quit": "quit", "item_wood": "wood", "item_stone": "stone", "item_coal": "Coal",
      "msg_bag": "Your bag is full", "msg_saved": "Everything is saved", "icon": "icons/wood.png"}
UI.update({"msg_%02d" % i: "The lantern number %d is now lit" % i for i in range(20)})


def test_generic(base):
    game = os.path.join(base, "Isola GM")
    write_bytes(os.path.join(game, "texts", "ui.json"), json.dumps(UI, indent=1).encode("utf-8"))
    without_e = set(chr(c) for c in range(32, 127)) | set("àéìòù")  # game font without è
    write_bytes(os.path.join(game, "data.win"), test_gamemaker.data_win([("fntMain", without_e)]))
    write_bytes(os.path.join(game, "Isola.exe"), b"MZ")
    _, outcome = automatic.prepare(game, "auto", "it", "dizionario", silent)
    check("GameMaker senza file di lingua riconosciuti: si propone la traduzione sperimentale",
              outcome["esito"].code == "NON_SUPPORTATO" and "sperimentale" in outcome["esito"].actions)
    log_file = []
    path, outcome = automatic.prepare(game, "auto", "it", "dizionario", log_file.append, force=True)
    profile = json.load(open(path, encoding="utf-8"))
    entry = profile["file"][0]
    check("profilo generico con il motore (gamemaker) e il file {chiave: testo} come file di lingua",
              profile.get("motore") == "gamemaker" and profile.get("motore_gioco") == "gamemaker"
              and entry["percorso"] == "texts/ui.json" and entry.get("localizzazione")
              and "item_wood" in entry["includi"] and "btn_play" in entry["includi"] and "icon" not in entry["includi"])
    p = project.Project(path, silent)
    p.translate(Translator())
    data = json.load(open(os.path.join(game, "texts", "ui.json"), encoding="utf-8"))
    check("tradotte le etichette di una parola (wood, stone, PLAY, quit) e lasciati gli identificativi",
              data["item_wood"] == "legno" and data["item_stone"] == "pietra" and data["btn_play"] == "GIOCA"
              and data["btn_quit"] == "esci" and data["icon"] == "icons/wood.png")
    check("caratteri adattati ai font del gioco (è -> e', È -> E'; à resta)",
              data["msg_bag"] == "La tua borsa e' piena" and data["msg_saved"] == "E' tutto salvato")

    # before point 8 a generic game profile did not have the engine: it gets it at the next analysis
    old = dict(profile)
    old.pop("motore")
    old.pop("motore_gioco")
    scan.save_json(path, old)
    automatic.prepare(game, "auto", "it", "dizionario", silent, force=True)
    check("profilo generico vecchio senza motore: aggiornato", json.load(open(path, encoding="utf-8")).get(
        "motore") == "gamemaker")

    # a data file with a list of objects (like the PluginInfo.json of Wwise sounds: name, type and library of each
    # effect) is not a text table even if many values look like words: 'Effect' or 'Windows' translated would break
    # the audio. A table with sections ({"menu": {"_play": "Play"}}) instead is
    audio_data = os.path.join(base, "Dati audio")
    libraries = [{"LibName": "Sound %s" % n, "LibId": str(1000 + i), "Type": "Effect", "DLL": "Snd%s" % n,
                 "StaticLib": "Snd%sFX" % n} for i, n in enumerate(("Echo", "Delay", "Chorus", "Limiter"))]
    libraries += [{"LibName": n, "LibId": str(2000 + i), "Type": "AudioDevice"}
                 for i, n in enumerate(("System", "Main Speaker", "No Output"))]
    write_bytes(os.path.join(audio_data, "Audio", "PluginInfo.json"), json.dumps(
        {"PluginInfo": {"Platform": "Windows", "BasePlatform": "Windows", "PluginLibs": libraries,
                        "FileHash": "{0000-1111}"}}, indent=1).encode("utf-8"))
    write_bytes(os.path.join(audio_data, "text", "menu.json"), json.dumps(
        {"menu": {"_play": "Play", "_quit": "Quit", "_ok": "OK"}, "shop": {"_k%d" % i: "Buy the item number %d" % i
                                                                           for i in range(20)}}).encode("utf-8"))
    # a family of files per language with a list of objects: the identifiers ('sword') stay out
    for code, name in (("en", "The sharp sword with a golden handle number %d"),
                         ("fr", "L'epee tranchante avec une poignee en or numero %d")):
        write_bytes(os.path.join(audio_data, "items", code + ".json"), json.dumps(
            {"items": [{"id": "sword%d" % i, "kind": "weapon", "name": name % i} for i in range(12)]}).encode("utf-8"))
    write_bytes(os.path.join(audio_data, "Saves", "progress.json"), json.dumps(
        {"slot%02d" % i: ("Forest Camp", "High", "On", "level_%d" % i)[i % 4] for i in range(24)}).encode("utf-8"))
    candidates, _ = scan.scan_folder(audio_data, silent)
    profile = scan.create_profile("audio", audio_data, candidates, "auto", "it", "finto")
    entries = {v["percorso"]: v for v in profile["file"]}
    check("ricerca generica: un elenco di oggetti (PluginInfo.json) e un salvataggio {chiave: testo} non sono "
              "file di lingua e restano fuori; la tabella a sezioni si' (anche Play, Quit, OK)",
              "Audio/PluginInfo.json" not in entries and "Saves/progress.json" not in entries
              and entries.get("text/menu.json", {}).get("localizzazione")
              and {"menu._play", "menu._ok"} <= set(entries["text/menu.json"]["includi"]))
    check("famiglia di file per lingua con un elenco di oggetti: non e' un file di lingua (gli id e i tipi non si "
              "traducono, i nomi si')", not adapter_gamemaker.language_json_files(audio_data)
              and not entries.get("items/en.json", {}).get("localizzazione")
              and entries.get("items/en.json", {}).get("includi") == ["items[].name"])

    # XNA: generic game profile with the detected engine (the game's XNB fonts are simulated: ASCII only)
    xna = os.path.join(base, "Isola XNA")
    write_bytes(os.path.join(xna, "Content", "lang.json"), json.dumps(UI).encode("utf-8"))
    candidates, _ = scan.scan_folder(xna, silent)
    profile = scan.create_profile("isola-xna", xna, candidates, "auto", "it", "dizionario", engine="XNA")
    check("profilo generico XNA: motore_gioco xna, senza motore (non e' il percorso delle risorse .NET)",
              profile.get("motore_gioco") == "xna" and "motore" not in profile)
    path = os.path.join(automatic.profiles_folder(), "isola-xna.json")
    scan.save_json(path, profile)
    real = adapter_xna.font_chars
    adapter_xna.font_chars = lambda folder, log=None: set(ASCII)
    try:
        p = project.Project(path, silent)
        p.translate(Translator())
    finally:
        adapter_xna.font_chars = real
    data = json.load(open(os.path.join(xna, "Content", "lang.json"), encoding="utf-8"))
    check("XNA: un carattere che i font non hanno (crash del gioco) viene adattato: è -> e', à no",
              p._game_fonts() == "xna" and data["msg_bag"] == "La tua borsa e' piena"
              and data["item_wood"] == "legno" and all(c in ASCII for v in data.values() for c in v))


# --- 5. GameMaker fonts -----------------------------------------------------------------------------

def test_font(base):
    game = os.path.join(base, "Font GM")
    cyrillic = set(chr(c) for c in range(32, 127)) | {chr(0x410 + i) for i in range(64)} | set("é")
    write_bytes(os.path.join(game, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATIN), ("ftFusionJP", JAPANESE), ("scribble_fallback_font", set(ASCII)),
         ("fntChat", set(ASCII))]))
    lines = []
    officials = "àâçèéêëîïôùûü"  # letters of the official translations (French)
    allowed = adapter_gamemaker.font_chars(game, lines.append, officials)
    check("font esclusi: giapponese con ç ê ñ (un'altra scrittura), di ripiego, e quello senza nessuna lettera "
              "delle traduzioni ufficiali (chat)",
              allowed is not None and set("àèéìòù") <= allowed and any("fntChat" in r for r in lines)
              and any("scribble_fallback_font" in r for r in lines) and not any("ftFusionJP" in r for r in lines))
    # like Another Door's ftPixeloidSans: ASCII, Cyrillic and only the accented letters of the English file (Á ç é ô).
    # It may be the font of the English texts, so it counts even with the official translations: è not allowed
    write_bytes(os.path.join(game, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATIN), ("fntPixel", cyrillic | set("Áçô"))]))
    lines = []
    allowed = adapter_gamemaker.font_chars(game, lines.append, officials)
    check("un font con il cirillico e poche lettere accentate conta anche con le traduzioni ufficiali (è no, é si')",
              allowed is not None and "è" not in allowed and "é" in allowed and not lines)
    allowed = adapter_gamemaker.font_chars(game)
    check("senza traduzioni ufficiali il font cirillico conta ancora (per sicurezza: è no, é si')",
              allowed is not None and "è" not in allowed and "é" in allowed)
    write_bytes(os.path.join(game, "data.win"), test_gamemaker.data_win(
        [("fntMain", LATIN), ("fntPiccolo", set(ASCII) | set("é"))]))
    allowed = adapter_gamemaker.font_chars(game)
    check("un font latino senza accenti conta ancora (per sicurezza: è non ammessa)",
              allowed is not None and "è" not in allowed and "é" in allowed)


# --- 6. web: one-word buttons ----------------------------------------------------------------------

def test_web(base):
    web = os.path.join(base, "web")
    os.makedirs(web)
    shutil.copyfile(os.path.join(HERE, "web", "index-inline.html"), os.path.join(web, "index.html"))
    texts = adapter_webui.extract_texts(web)
    check("test/web/index-inline.html: i pulsanti PLAY e OK sono nel dizionario (con gli altri testi)",
              "PLAY" in texts and "OK" in texts and "Quit Game" in texts and "Delete" in texts)
    code = ('function m(){var b=document.getElementById("b");b.textContent="QUIT";ctx.fillText("GO",1,2);'
              'var x={label:"Back",id:"left"};var k="left";el.className="NONE";'
              'this.add.text(1,2,"MENU",{fontFamily:"Arial",align:"center"});b.innerText="ok";'
              'if(a.value=="yes")b.title="Options";s.setAttribute("title","x");var t="Continue";var n="retry";}')
    write_bytes(os.path.join(web, "gioco.js"), code.encode("utf-8"))
    texts = adapter_webui.extract_texts(web)
    check("JavaScript: parole sole solo dove di sicuro si vedono (textContent, fillText, label:, add.text, "
              "innerText, title=)", all(t in texts for t in ("QUIT", "GO", "Back", "MENU", "ok", "Options")))
    check("JavaScript: restano fuori identificativi, classi, font, confronti e attributi",
              not any(t in texts for t in ("left", "NONE", "Arial", "center", "yes", "title", "retry"))
              and "Continue" in texts)
    check("lettore del JavaScript: senza contesto le stringhe sono le stesse di prima",
              list(adapter_webui.js_literals(code)) == [s for s, _ in adapter_webui.js_literals(code, True)])
    check("parole sole mostrate: PLAY, OK, quit, Co-op, Quit? si'; camelCase, snake_case, numeri, tasti no",
              all(adapter_webui.is_shown_text(t) for t in ("PLAY", "OK", "quit", "Co-op", "Quit?"))
              and not any(adapter_webui.is_shown_text(t) for t in ("onClick", "btn_ok", "x2", "Esc", "a", "F5")))
    real = adapter_webui.MAX_SIZE
    adapter_webui.MAX_SIZE = 200
    lines = []
    try:
        adapter_webui.extract_texts(web, lines.append)
    finally:
        adapter_webui.MAX_SIZE = real
    check("file troppo grandi saltati: scritti nel registro con quanti sono",
              any("troppo grandi" in r and "index.html" in r for r in lines))


# --- 7. Unreal -----------------------------------------------------------------------------------

def test_unreal(base):
    adapter_unreal.game_open = lambda c: False
    unlabeled = os.path.join(base, "Unreal senza locres")
    paks = os.path.join(unlabeled, "Proj", "Content", "Paks")
    write_bytes(os.path.join(paks, "Proj-WindowsNoEditor.pak"),
                fake_unreal.pak_v9([("Proj/Content/Maps/Level.umap", b"mappa" * 50)]))
    os.makedirs(os.path.join(unlabeled, "Engine", "Binaries"))
    profiles_before = sorted(os.listdir(automatic.profiles_folder())) if os.path.isdir(
        automatic.profiles_folder()) else []
    log_file = []
    result = automatic._prepare_unreal(unlabeled, None, "auto", "it", {"risultati": []}, log_file.append)
    reason = result[1]["esito"].reason
    check("Unreal senza .locres: non supportato con il motivo, nessun profilo lasciato",
              result[0] is None and result[1]["esito"].code == "NON_SUPPORTATO" and ".locres" in reason
              and "non sa tradurre" in reason and sorted(os.listdir(automatic.profiles_folder()) if os.path.isdir(
                  automatic.profiles_folder()) else []) == profiles_before)
    mixed = os.path.join(base, "Unreal con pak rotto")
    fake_unreal.create_game(mixed)
    write_bytes(os.path.join(mixed, "Proj", "Content", "Paks", "Proj-Rotto.pak"), b"non e' un pak" * 20)
    skipped = []
    sources = adapter_unreal.find_locres(mixed, skipped)
    log_file = []
    path, outcome = automatic._prepare_unreal(mixed, None, "auto", "it", {"risultati": []}, log_file.append)
    check("un pak illeggibile si salta: il .locres degli altri si trova e il profilo si crea",
              [s[1] for s in sources] == ["Proj/Content/Localization/Game/en/Game.locres"]
              and len(skipped) == 1 and "Proj-Rotto.pak" in skipped[0] and path and os.path.exists(path)
              and any("archivi non letti" in r and "Proj-Rotto.pak" in r for r in log_file))

    # small pak mounted in a deeper folder (Remnant: '../../../Remnant/Content/'), language in the .locmeta of
    # another pak
    mounted = os.path.join(base, "Unreal montato")
    paks = os.path.join(mounted, "Proj", "Content", "Paks")
    os.makedirs(os.path.join(mounted, "Engine", "Binaries"))
    entries = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit")]
    write_bytes(os.path.join(paks, "pakchunk0-WindowsNoEditor.pak"), fake_unreal.pak_v9(
        [("Proj/Content/Localization/Game/Game.locmeta", fake_unreal.locmeta("en"))]))
    write_bytes(os.path.join(paks, "pakchunk1-WindowsNoEditor.pak"), mounted_pak("../../../Proj/Content/", [
        ("Localization/Game/en/Game.locres", fake_unreal.locres_v2(entries)),
        ("Localization/Game/de/Game.locres", fake_unreal.locres_v2([("UI", "Start", "Spiel starten")]))]))
    sources = adapter_unreal.find_locres(mounted)
    _, texts, culture = adapter_unreal.texts_to_translate(mounted, silent)
    n = adapter_unreal.install(mounted, "it", "montato", {"Start Game": "Inizia la partita", "Quit": "Esci"}, silent)
    patch = adapter_unreal.Pak(os.path.join(paks, adapter_unreal.pak_name("it")))
    translated = adapter_unreal.read_locres(patch.read_file("Proj/Content/Localization/Game/en/Game.locres"))
    check("pak montato in una cartella piu' profonda: .locres trovato con il nome completo, tradotto in un pak di "
              "patch con il punto di montaggio della radice",
              [s[1] for s in sources] == ["Proj/Content/Localization/Game/en/Game.locres"] and culture == "en"
              and sorted(texts) == ["Quit", "Start Game"] and n == 2 and patch.mount == "../../../"
              and sorted(patch.entries) == ["Proj/Content/Localization/Game/en/Game.locres"]
              and [s for s, _ in translated["stringhe"]] == ["Inizia la partita", "Esci"])
    german = os.path.join(base, "Unreal tedesco")
    write_bytes(os.path.join(german, "Proj", "Content", "Paks", "pakchunk0-WindowsNoEditor.pak"), fake_unreal.pak_v9(
        [("Proj/Content/Localization/Game/Game.locmeta", fake_unreal.locmeta("de"))]))
    write_bytes(os.path.join(german, "Proj", "Content", "Paks", "pakchunk1-WindowsNoEditor.pak"),
                mounted_pak("../../../Proj/Content/Localization/Game/", [
                    ("en/Game.locres", fake_unreal.locres_v2(entries)),
                    ("de/Game.locres", fake_unreal.locres_v2([("UI", "Start", "Spiel starten")]))]))
    check("lingua originale dal .locmeta di un altro pak (de): si traduce il .locres tedesco",
              [(s[1], s[3]) for s in adapter_unreal.find_locres(german)]
              == [("Proj/Content/Localization/Game/de/Game.locres", "de")])
    loose = os.path.join(base, "Unreal sciolto")
    write_bytes(os.path.join(loose, "Proj", "Content", "Paks", "Proj-WindowsNoEditor.pak"),
                fake_unreal.pak_v9([("Proj/Content/Maps/Level.umap", b"mappa" * 50)]))
    write_bytes(os.path.join(loose, "Proj", "Content", "Localization", "Game", "en", "Game.locres"),
                fake_unreal.locres_v2(entries))
    _, outcome = automatic._prepare_unreal(loose, None, "auto", "it", {"risultati": []}, silent)
    check("Unreal con i .locres fuori dagli archivi: il motivo non dice che non ci sono testi localizzati",
              outcome["esito"].code == "NON_SUPPORTATO" and "non sa ancora tradurre" in outcome["esito"].reason)


def mounted_pak(mount, file):
    """Uncompressed pak v9 with a mount point of choice (the names are relative to it), like pak_v9 of
    test/test_unreal.py."""
    body, entries = b"", []
    for name, data in file:
        h = hashlib.sha1(data).digest()
        def entry(off, d=data, h=h):
            return struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00" + struct.pack("<I", 0)
        entries.append((name, len(body), entry))
        body += entry(0) + data
    idx = fake_unreal.fs(mount) + struct.pack("<i", len(entries)) + b"".join(fake_unreal.fs(n) + v(o)
                                                                            for n, o, v in entries)
    tail = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(body), len(idx))
            + hashlib.sha1(idx).digest() + b"\x00" + b"\x00" * 160)
    return body + idx + tail


# --- 8. log of the files skipped by the generic search -----------------------------------------------

def test_log(base):
    folder = os.path.join(base, "saltati")
    write_bytes(os.path.join(folder, "rotto.json"), b"{non json")
    write_bytes(os.path.join(folder, "pochi.json"), json.dumps({"a": "Hello there friend", "b": "x"}).encode())
    write_bytes(os.path.join(folder, "testi.json"), json.dumps(
        {"t%d" % i: "This is the sentence number %d of the game" % i for i in range(30)}).encode())
    real = scan.MAX_JSON_SIZE
    scan.MAX_JSON_SIZE = 1000
    lines = []
    try:
        candidates, _ = scan.scan_folder(folder, lines.append)
    finally:
        scan.MAX_JSON_SIZE = real
    check("ricerca generica: JSON non validi e troppo grandi scritti nel registro con quanti sono",
              any("non sono JSON validi" in r and "rotto.json" in r for r in lines)
              and any("troppo grandi" in r and "testi.json" in r for r in lines))
    candidates, _ = scan.scan_folder(folder, silent)
    lines = []
    profile = scan.create_profile("saltati", folder, candidates, "auto", "it", "finto", log=lines.append)
    check("file con pochi testi fuori dal profilo, scritti nel registro con i numeri",
              [v["percorso"] for v in profile["file"]] == ["testi.json"]
              and any("pochi.json (1 testi" in r for r in lines))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    prepare_data(base)
    test_lenient_json(base)
    test_door(base)
    test_csv(base)
    test_generic(base)
    test_font(base)
    test_web(base)
    test_unreal(base)
    test_log(base)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
