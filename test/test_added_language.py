"""Test of the added language (GameMaker, Forager style) without network and without the real game.

Usage: python -I test/test_added_language.py <empty_temp_folder>
Builds a fake game: a minimal date.win (GEN8 with the name, STRG with the 'local/*.json' mask and the map of a sprite
font, SPRT with the font's sprite), a lowercase local/english.json with the 'EP' and 'H' icons, official
translations, and the options saved by the game (%LOCALAPPDATA%\\<nome>\\config.txt, ds_map in hexadecimal).
"""
import hashlib
import json
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import (adapter_gamemaker, auto as automatic, gamemaker, language_gamemaker,
                        official_language, project)  # noqa
from patchsmith import system  # noqa: E402

OUTCOMES = []
CHAR_MAP = "?!0123456789aàbcdeèéfghiíjklmnoópqrstuùúvwxyz/.:,_-'%X()+CHTEPUR"


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def fingerprint(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# --- minimal date.win ------------------------------------------------------------------------------------------

def data_win(strings, sprite, code=True, name="FintoGame"):
    """FORM with GEN8 (name), STRG (strings), SPRT (sprites: [(nome, fotogrammi)]) and, if 'codice', an empty CODE
    (game for the virtual machine: the code's strings are in STRG)."""
    all_items = [name] + list(strings) + [n for n, _ in sprite]
    # the chunks are built with absolute positions: first compute where STRG will be
    gen8_size, sprt_dim = 64, 4 + 4 * len(sprite) + 96 * len(sprite) + 64
    gen8_start = 8 + 8
    sprt_start = gen8_start + gen8_size + 8
    code_start = sprt_start + sprt_dim + 8
    strg_start = code_start + (0 if not code else 4 + 8) + (8 if not code else 0)
    if not code:
        strg_start = sprt_start + sprt_dim + 8
    # STRG: count, pointers (to the length), strings (length + text + 0)
    body, pointers, pos = b"", [], strg_start + 4 + 4 * len(all_items)
    text_of = {}
    for t in all_items:
        b = t.encode("utf-8")
        pointers.append(pos + len(body))
        text_of[t] = pos + len(body) + 4
        body += struct.pack("<I", len(b)) + b + b"\x00"
    strg = struct.pack("<I", len(all_items)) + struct.pack("<%dI" % len(all_items), *pointers) + body
    gen8 = bytearray(gen8_size)
    struct.pack_into("<I", gen8, 4, text_of[name])
    struct.pack_into("<I", gen8, 40, text_of[name])
    sprt = bytearray(sprt_dim)
    struct.pack_into("<I", sprt, 0, len(sprite))
    for i, (n, frames) in enumerate(sprite):
        rec = 4 + 4 * len(sprite) + 96 * i
        struct.pack_into("<I", sprt, 4 + 4 * i, sprt_start + rec)
        struct.pack_into("<I13i", sprt, rec, text_of[n], 16, 16, 0, 15, 15, 0, 0, 0, 1, 0, 0, 8, 8)
        struct.pack_into("<iIIfII", sprt, rec + 56, -1, 1, 0, 1.0, 0, frames)
    pieces = b"GEN8" + struct.pack("<I", gen8_size) + bytes(gen8) + b"SPRT" + struct.pack("<I", sprt_dim) + bytes(sprt)
    if code:
        pieces += b"CODE" + struct.pack("<I", 4) + struct.pack("<I", 0)
    pieces += b"STRG" + struct.pack("<I", len(strg)) + strg
    return b"FORM" + struct.pack("<I", len(pieces)) + pieces


# --- saved options (ds_map/ds_list in hexadecimal, like Forager) ---------------------------------------------

def _v(x):
    if isinstance(x, str):
        b = x.encode("utf-8")
        return struct.pack("<II", 1, len(b)) + b
    return struct.pack("<Id", 0, float(x))


def ds_list(values):
    return (struct.pack("<II", 0x12E, len(values)) + b"".join(_v(x) for x in values)).hex().upper()


def ds_map(entries):
    return (struct.pack("<II", 0x192, len(entries)) + b"".join(_v(k) + _v(v) for k, v in entries)).hex().upper()


EN = {"wood": "wood", "itemName1": "iron*ore", "uiPlay": "play", "configVsync": "vsync:", "configFPS": "show*fps:",
      "uiExtras": "extras", "statDesc0": "permanent H**U increase", "skillDesc0": "U gain EP when selling items",
      "skillDesc1": "U gain EP when crafting", "skillDesc2": "U gain EP when fishing",
      "boon0": "U lose 1 max H**U gain a**bunch of food", "boon1": "U lose 1 max H**U gain coins",
      "uiDelete": "Are you sure you want to delete this save?"}
for i in range(40):  # enough texts to recognize the style
    EN["itemDescription%d" % i] = "a small item number %d*for testing" % i
FR = dict(EN, configVsync="synchro*verticale :", uiPlay="jouer", uiExtras="extras", wood="bois")
ES = dict(EN, configVsync="vsync:", uiPlay="juega", uiExtras="extras", wood="madera")
PT = dict(EN, configVsync="vsync:", uiPlay="jogar", uiExtras="extras", wood="madeira")


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    for d in ("memory", "profiles", "languages"):
        os.makedirs(os.path.join(project.PROJECT_FOLDER, d))
    automatic.PROFILES_FOLDER = os.path.join(project.PROJECT_FOLDER, "profiles")
    os.environ["LOCALAPPDATA"] = os.path.join(base, "appdata")
    system.game_open = lambda c: False

    # --- 1. reading date.win: sprites, name, strings, map of the sprite font, mask of the languages
    vm = data_win(["local/*.json", CHAR_MAP], [("sprFont", len(CHAR_MAP)), ("sprPlayer", 4)])
    game = os.path.join(base, "gioco")
    os.makedirs(os.path.join(game, "local"))
    open(os.path.join(game, "data.win"), "wb").write(vm)
    check("data.win: sprite con i fotogrammi, nome del gioco, stringhe",
              gamemaker.sprite(vm) == [{"nome": "sprFont", "fotogrammi": len(CHAR_MAP)},
                                       {"nome": "sprPlayer", "fotogrammi": 4}]
              and gamemaker.names_of_game(vm) == ["FintoGame"] and "local/*.json" in gamemaker.strings(vm))
    check("font a sprite: la mappa dei caratteri (tanti quanti i fotogrammi) e la maschera 'local/*.json'",
              gamemaker.font_sprite_maps(game, vm) == [("sprFont", CHAR_MAP)]
              and gamemaker.folder_mask(game, "local", ".json", vm)
              and not gamemaker.folder_mask(game, "lang", ".json", vm))
    yyc = data_win([], [("sprFont", len(CHAR_MAP))], code=False)
    yyc_folder = os.path.join(base, "yyc")
    os.makedirs(yyc_folder)
    open(os.path.join(yyc_folder, "Gioco.exe"), "wb").write(
        b"MZ\x00" + b"local/*.json\x00" + CHAR_MAP.encode() + b"\x00")
    open(os.path.join(yyc_folder, "unins000.exe"), "wb").write(b"MZ\x00lang/*.json\x00")
    check("gioco compilato YYC: maschera e mappa cercate nell'eseguibile (non nei disinstallatori)",
              gamemaker.yyc_compiled(yyc) and gamemaker.folder_mask(yyc_folder, "local", ".json", yyc)
              and not gamemaker.folder_mask(yyc_folder, "lang", ".json", yyc)
              and gamemaker.font_sprite_maps(yyc_folder, yyc) == [("sprFont", CHAR_MAP)])

    # --- 2. rules on the texts: style, codes, lowercase, space of the lines
    texts = list(EN.values())
    check("stile 'minuscolo' e codici-icona EP e H (confermati dal font); un gioco normale non lo e'",
              adapter_gamemaker.caps_style(texts) == "minuscolo"
              and adapter_gamemaker.uppercase_codes(texts, CHAR_MAP, "U") == ["EP", "H"]
              and adapter_gamemaker.uppercase_codes(texts, None, "U") == []
              and adapter_gamemaker.caps_style(["Hello World %d, Start Game" % i for i in range(40)]) == "normale")
    check("minuscole: tutto tranne icone e segnaposto; i testi con maiuscole normali restano come sono",
              adapter_gamemaker.to_lowercase("Mostra*FPS:", "show*fps:") == "mostra*fps:"
              and adapter_gamemaker.to_lowercase("Ottieni EP per {Item}", "gain EP for {Item}", ["EP"])
              == "ottieni EP per {Item}"
              and adapter_gamemaker.to_lowercase("Sei sicuro?", "Are you sure?") == "Sei sicuro?")
    limits = adapter_gamemaker.line_limits(EN, [FR, ES, PT], "*")
    check("spazio di ogni testo dalle versioni del gioco (riga piu' lunga, righe, quante lo lasciano uguale)",
              limits["configVsync"] == [11, 2, 2] and limits["uiExtras"][2] == 3)
    check("a capo ridistribuiti solo se la riga esce dallo spazio, entro le righe delle versioni del gioco",
              adapter_gamemaker.reassemble_line_breaks("vsync:", "sincronizzazione verticale:", "*", "U", 11, 2)
              == "sincronizzazione*verticale:"
              and adapter_gamemaker.reassemble_line_breaks("vsync:", "vsync:", "*", "U", 11, 2) == "vsync:"
              and adapter_gamemaker.reassemble_line_breaks("iron*ore", "minerale di ferro", "*", "U", 30, 2)
              == "minerale di*ferro")

    # --- 3. full flow: the translation becomes a new language, English stays original
    for name, data in (("english", EN), ("french", FR), ("spanish", ES), ("portuguese", PT)):
        json.dump(data, open(os.path.join(game, "local", name + ".json"), "w", encoding="utf-8"))
    english = os.path.join(game, "local", "english.json")
    before = fingerprint(english)
    path, completed, outcome = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    profile = json.load(open(path, encoding="utf-8"))
    entry = profile["file"][0]
    italian = os.path.join(game, "local", "italiano.json")
    check("lingua nuova local/italiano.json; english.json identico; profilo con stile, icone, spazio, riferimenti",
              os.path.exists(italian) and fingerprint(english) == before
              and entry.get("lingua_nuova") == "local/italiano.json"
              and entry.get("maiuscole") == "minuscolo" and entry.get("codici") == ["EP", "H"]
              and entry.get("limiti", {}).get("configVsync") == [11, 2, 2]
              and set(entry.get("riferimenti", {})) == {"fr", "es", "pt"} and outcome.get("lingua_aggiunta"))
    local_files = os.listdir(os.path.join(game, "local"))
    check("nessun file di PatchSmith che il gioco caricherebbe come lingua (niente *.json nostri)",
              not [n for n in local_files if "patchsmith" in n and n.endswith(".json")]
              and "english.json" + project.INFO_SUFFIX in local_files)
    it = json.load(open(italian, encoding="utf-8"))
    check("traduzione: icone EP e H conservate, maiuscole tolte, caratteri del font (parentesi al posto di [ ])",
              "EP" in it["skillDesc0"] and "H" in it["statDesc0"] and not any(
                  c.isupper() for k, v in it.items() if k != "uiDelete"
                  for c in v.replace("EP", "").replace("H", "").replace("U ", ""))
              and "[" not in json.dumps({k: v for k, v in it.items() if k != "uiDelete"}))
    check("messaggio con maiuscole normali: tradotto con il font normale (non quello a immagini), maiuscole tenute",
              it["uiDelete"] != EN["uiDelete"] and "Are you sure" in it["uiDelete"])
    check("rilancio: italiano.json non e' scambiato per una traduzione ufficiale del gioco",
              not any("italiano" in r for r in adapter_gamemaker.language_json_files(game))
              and automatic.analyze_and_translate(game, "auto", "it", "finto", silent)[0] == path)

    # --- 4. language set in the game's saved options, and restore
    options_dir = os.path.join(base, "appdata", "FintoGame")
    os.makedirs(options_dir)
    config = os.path.join(options_dir, "config.txt")
    text = '{ "mvc": "1.2", "config": "%s", "altro": "x" }' % ds_map(
        [("extrasUnlocked", 10), ("options", ds_list(["english", 0, 1, "yes"])), ("totalFeats", 3)])
    open(config, "w", encoding="utf-8", newline="").write(text)
    languages = automatic.folder_languages(game, "local/english.json")
    done, lines = official_language.set_added_language(game, "italiano", languages, silent)
    after = open(config, encoding="utf-8").read()
    check("lingua impostata nelle opzioni del gioco ('english' -> 'italiano'), resto del file identico",
              done and language_gamemaker.find(after, {"italiano"}) == [("config", "options", 0, "italiano")]
              and after.replace(json.loads(after)["config"], "") == text.replace(json.loads(text)["config"], ""))
    double = '{ "a": "%s", "b": "%s" }' % (ds_map([("options", ds_list(["english"]))]),
                                          ds_map([("lang", ds_list(["french"]))]))
    open(os.path.join(options_dir, "altro.txt"), "w", encoding="utf-8").write(double)
    check("lingua in piu' punti delle opzioni: non si tocca nulla",
              language_gamemaker.search(game, languages | {"italiano"})[0] is None)
    os.remove(os.path.join(options_dir, "altro.txt"))
    check("ripristino: lingua aggiunta tolta, inglese identico, opzioni di nuovo su 'english' (file identico)",
              automatic.restore(game, silent) and not os.path.exists(italian) and fingerprint(english) == before
              and open(config, encoding="utf-8").read() == text
              and not [n for n in os.listdir(os.path.join(game, "local")) if "patchsmith" in n])

    # --- 5. from a previous version: english.json translated in place and information with the old name
    old = os.path.join(base, "vecchio")
    shutil.copytree(game, old)
    shutil.rmtree(os.path.join(old, "local"))
    os.makedirs(os.path.join(old, "local"))
    for name, data in (("english", EN), ("french", FR), ("spanish", ES), ("portuguese", PT)):
        json.dump(data, open(os.path.join(old, "local", name + ".json"), "w", encoding="utf-8"))
    old_en = os.path.join(old, "local", "english.json")
    original = fingerprint(old_en)
    info = project.create_backup(old_en)
    translated = json.dumps(dict(EN, uiPlay="gioca")).encode("utf-8")
    info["hash_scritti"] = [hashlib.sha256(translated).hexdigest()]
    os.replace(old_en + project.INFO_SUFFIX, old_en + project.OLD_INFO_SUFFIX)
    with open(old_en + project.OLD_INFO_SUFFIX, "w", encoding="utf-8") as f:
        json.dump(info, f)
    open(old_en, "wb").write(translated)
    automatic.analyze_and_translate(old, "auto", "it", "finto", silent)
    check("dalla versione precedente: english.json rimesso originale, informazioni con il nome nuovo, lingua nuova",
              fingerprint(old_en) == original and not os.path.exists(old_en + project.OLD_INFO_SUFFIX)
              and os.path.exists(old_en + project.INFO_SUFFIX)
              and os.path.exists(os.path.join(old, "local", "italiano.json")))

    # --- 6. hard cases: other people's files, fixes made by hand, options without a log, Reapply, player's choice
    other = os.path.join(base, "altro")
    shutil.copytree(old, other)
    for n in os.listdir(os.path.join(other, "local")):
        if "patchsmith" in n or n == "italiano.json":
            os.remove(os.path.join(other, "local", n))
    automatic.analyze_and_translate(other, "auto", "it", "finto", silent)
    other_italian = os.path.join(other, "local", "italiano.json")
    automatic.restore(other, silent)
    open(other_italian, "w", encoding="utf-8").write('{"uiPlay": "gioca (traduzione amatoriale)"}')
    automatic.reapply(other, silent, test=True)
    check("un italiano.json di qualcun altro non viene sovrascritto ne' cancellato",
              open(other_italian, encoding="utf-8").read() == '{"uiPlay": "gioca (traduzione amatoriale)"}'
              and automatic.restore(other, silent) is not None and os.path.exists(other_italian))
    os.remove(other_italian)
    automatic.reapply(other, silent, test=True)
    corrected = json.load(open(other_italian, encoding="utf-8"))
    corrected["uiPlay"] = "gioca!"
    json.dump(corrected, open(other_italian, "w", encoding="utf-8"))
    automatic.reapply(other, silent, test=True)
    check("lingua aggiunta corretta a mano: riscritta, la versione corretta resta da parte (.patchsmith-modificato)",
              json.load(open(other_italian + ".patchsmith-modificato", encoding="utf-8"))["uiPlay"] == "gioca!"
              and json.load(open(other_italian, encoding="utf-8"))["uiPlay"] != "gioca!")
    # options on 'italiano' chosen by the player (no log): restore puts them back before removing the file
    for n in os.listdir(os.path.join(base, "patchsmith", "languages")):
        os.remove(os.path.join(base, "patchsmith", "languages", n))
    open(config, "w", encoding="utf-8", newline="").write(text.replace(
        json.loads(text)["config"], ds_map([("extrasUnlocked", 10), ("options", ds_list(["italiano", 0, 1, "yes"])),
                                             ("totalFeats", 3)])))
    os.rename(options_dir, options_dir + "-tmp")  # the options of 'gioco', not of 'altro'
    other_options = os.path.join(base, "appdata", "FintoGame")
    os.rename(options_dir + "-tmp", other_options)
    automatic.restore(other, silent)
    check("ripristino senza registro per questa cartella (es. una copia del gioco, che condivide le opzioni con "
              "quello vero): le opzioni non si toccano",
              language_gamemaker.find(open(config, encoding="utf-8").read(), {"italiano"})
              == [("config", "options", 0, "italiano")] and not os.path.exists(other_italian))
    automatic.reapply(other, silent)  # PatchSmith sets the language for 'altro' (log)
    open(config, "w", encoding="utf-8", newline="").write(text.replace(
        json.loads(text)["config"], ds_map([("extrasUnlocked", 10), ("options", ds_list(["italiano", 0, 1, "yes"])),
                                             ("totalFeats", 3)])))
    automatic.restore(other, silent)
    check("ripristino con il registro di questa cartella: opzioni rimesse su 'english' prima di togliere il file",
              language_gamemaker.find(open(config, encoding="utf-8").read(), {"english"})
              == [("config", "options", 0, "english")] and not os.path.exists(other_italian))
    # Reapply sets the language; options deleted: restore finishes without getting stuck
    outcome = {}
    automatic.reapply(other, silent, outcome=outcome)
    is_set = language_gamemaker.find(open(config, encoding="utf-8").read(), {"italiano"})
    os.remove(config)
    check("Riapplica imposta la lingua aggiunta; con le opzioni cancellate il ripristino si chiude lo stesso",
              outcome.get("lingua_aggiunta", {}).get("impostata") and is_set
              and automatic.restore(other, silent) and not official_language.is_set(other))
    # the player chooses another language after the translation: a new 'Analyze and translate' respects it
    open(config, "w", encoding="utf-8", newline="").write(text)
    automatic.reapply(other, silent)
    choice = language_gamemaker.change(open(config, encoding="utf-8").read(),
                                     language_gamemaker.find(open(config, encoding="utf-8").read(), {"italiano"})[0],
                                     "french")
    open(config, "w", encoding="utf-8", newline="").write(choice)
    automatic.reapply(other, silent)
    check("lingua scelta dal giocatore dopo la traduzione: non viene cambiata di nuovo",
              language_gamemaker.find(open(config, encoding="utf-8").read(), {"french"})
              == [("config", "options", 0, "french")])

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
