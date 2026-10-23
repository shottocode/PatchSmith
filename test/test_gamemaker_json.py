"""Tests of GameMaker's JSON localization files (e.g. Forager) and of the "Stop" button.

Usage: python -I test/test_gamemaker_json.py <empty_temp_folder>
Fake game: local/english.json + official translations, with Forager's conventions ('*' = line break, "U " =
symbol at the start of a paragraph), date.win with an accented Latin font and ASCII-only fonts (chat, numbers).
Writes only to the temporary folder; no network (fake translator, simulated HTTP requests).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (adapter_gamemaker, auto as automatic, backend, interruption,
                        official_language, project, steam)
import test_gamemaker  # noqa: E402  (date.win builder)

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def fingerprints(folder):
    return {n: hashlib.sha256(open(os.path.join(folder, n), "rb").read()).hexdigest()
            for n in sorted(os.listdir(folder)) if "patchsmith" not in n}


EN = {"itemName0": "wood", "itemName1": "iron*ore", "itemName2": "bottled*torchbug", "itemName3": "demon*horn",
      "itemName4": "royal*steel", "itemName5": "golden*egg", "itemName6": "coal",
      "skillDesc0": "U unlocks steel**U unlocks glass**U gain EP when building structures",
      "skillDesc1": "U unlocks carpentry stations**U structures cost 25%*less wood",
      "skillDesc2": "U unlocks masonry tables**U unlocks braziers",
      "skillDesc3": "U unlocks the forge**U faster smelting",
      "skillDesc4": "U unlocks the bank**U coins drop more often",
      "playing": "Playing {game mode}", "warning": "WARNING:\nAre you sure you want to continue?",
      "structureDesc9": "Structure.NuclearFurnace"}
EN.update({"extra%d" % i: "plain text number %d" % i for i in range(20)})
FR = {"itemName0": "bois", "itemName1": "minerai*de fer", "itemName2": "luciole*en bouteille",
      "itemName3": "corne*de démon", "itemName4": "acier*royal", "itemName5": "œuf*doré", "itemName6": "charbon",
      "skillDesc0": "U débloque l'acier**U  débloque le verre**U  gain d'exp",
      "skillDesc1": "U débloque les menuiseries**U  les structures coûtent 25 %*de bois en moins",
      "skillDesc2": "U débloque les tables**U  débloque les braseros",
      "skillDesc3": "U débloque la forge**U  fonte plus rapide",
      "skillDesc4": "U débloque la banque**U  plus de pièces",
      "playing": "Joue à {game mode}", "warning": "ATTENTION :\nVoulez-vous continuer ?"}
FR.update({"extra%d" % i: "texte simple numéro %d" % i for i in range(20)})
ES = {k: v.replace("débloque", "desbloquea").replace("é", "é") for k, v in FR.items()}
DE = {k: v.replace("U débloque", "Schaltet").replace("*", " ") for k, v in FR.items()}  # German: without '*' or U

ITALIAN = {"wood": "legno", "iron ore": "minerale di ferro", "bottled torchbug": "lucciola in bottiglia",
            "demon horn": "corno di demone", "royal steel": "acciaio reale", "golden egg": "uovo d'oro",
            "coal": "carbone", "unlocks steel": "sblocca l'acciaio", "unlocks glass": "sblocca il vetro",
            "gain EP when building structures": "ottieni PE quando costruisci strutture",
            "unlocks carpentry stations": "sblocca le falegnamerie",
            "structures cost 25% less wood": "le strutture costano il 25% di legno in meno",
            "Playing [[0]]": "Sta giocando a [[0]]", "WARNING:": "ATTENZIONE:",
            "Are you sure you want to continue?": "Sei sicuro di voler continuare? Così sarà più",
            "unlocks the forge": "sblocca la forgia", "faster smelting": "fusione più veloce"}


class Translator(backend.Backend):
    """Fake translator: a small dictionary, the rest "[it] ..."."""
    name = "dictionary"

    def __init__(self, stop_after=None):
        self.calls = 0
        self.stop_after = stop_after

    def translate_lines(self, lines, origin, dest_lang):
        interruption.check()
        self.calls += 1
        if self.stop_after and self.calls >= self.stop_after:
            interruption.request_stop()  # like the "Stop" button pressed while translating
        return [ITALIAN.get(r, "[it] " + r) for r in lines]


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    automatic.PROFILES_FOLDER = os.path.join(base, "patchsmith", "profiles")
    os.makedirs(automatic.PROFILES_FOLDER)
    os.makedirs(os.path.join(base, "patchsmith", "memory"))
    steam.steam_folder = lambda: None

    game = os.path.join(base, "Forager finto")
    local = os.path.join(game, "local")
    os.makedirs(local)
    for name, data in (("english", EN), ("french", FR), ("spanish", ES), ("german", DE)):
        with open(os.path.join(local, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    latin = set(chr(c) for c in range(32, 127)) | set("àèéìíòóùÀÈÉÌÒÙçâêôûœëïüö")
    ascii_ = set(chr(c) for c in range(32, 127))
    with open(os.path.join(game, "data.win"), "wb") as f:
        f.write(test_gamemaker.data_win([("fntText", latin), ("fntChat", ascii_), ("fnt_game", ascii_)]))
    open(os.path.join(game, "Forager.exe"), "wb").write(b"MZ")
    before = fingerprints(local)

    # --- recognition
    found = adapter_gamemaker.language_json_files(game)
    check("file JSON per lingua riconosciuti", sorted(found.values()) == ["de", "en", "es", "fr"])
    check("lingua dal nome: cinese tradizionale e thai",
              adapter_gamemaker.path_language("local/chinese_traditional.json") == "zh-TW"
              and adapter_gamemaker.path_language("local/thai.json") == "th"
              and adapter_gamemaker.path_language("local/chinese.json") == "zh")
    others = [FR, ES, DE]
    check("convenzioni confermate dalle traduzioni ufficiali: '*' a capo, 'U' simbolo",
              adapter_gamemaker.markup(EN, others) == {"line_break": "*", "bullet": "U"})
    articles = {"k%d" % i: "A cat number %d**A dog" % i for i in range(20)}
    check("un articolo ('A cat') non e' un simbolo a inizio paragrafo",
              adapter_gamemaker.markup(articles, [{k: "Un chat**Un chien" for k in articles}])["bullet"] is None)
    check("nei file di localizzazione anche le parole sole sono testo, gli identificativi no",
              adapter_gamemaker.localization_text("wood") and
              not adapter_gamemaker.localization_text("Structure.NuclearFurnace")
              and not adapter_gamemaker.localization_text("menu_title_2"))
    check("normalizzazione: frase intera, paragrafi su righe, simbolo tolto",
              adapter_gamemaker.normalize_line_breaks(EN["skillDesc1"], "*", "U")
              == "unlocks carpentry stations\n\nstructures cost 25% less wood")
    check("ricomposizione: a capo bilanciati, simboli e paragrafi rimessi",
              adapter_gamemaker.reassemble_line_breaks("iron*ore", "minerale di ferro", "*", "U") == "minerale di*ferro"
              and adapter_gamemaker.reassemble_line_breaks("wood", "legno", "*", "U") == "legno"
              and adapter_gamemaker.reassemble_line_breaks(EN["skillDesc0"], "a\n\nb c\n\nd", "*", "U")
              == "U a**U b c**U d")
    wrapped = "U gain EP when selling items**U items discarded from your**inventory are instantly**sold instead"
    check("frase spezzata da '**' (senza simbolo, minuscola, senza punto prima): tradotta intera, poi rimessa "
              "sulle stesse righe",
              adapter_gamemaker.normalize_line_breaks(wrapped, "*", "U")
              == "gain EP when selling items\n\nitems discarded from your inventory are instantly sold instead"
              and adapter_gamemaker.reassemble_line_breaks(wrapped, "a b c\n\nd e f g h i", "*", "U")
              == "U a b c**U d e f**g h**i"
              and adapter_gamemaker.normalize_line_breaks("**awarded only to those**who beat the tomb!", "*", "U")
              == "\n\nawarded only to those who beat the tomb!"
              and adapter_gamemaker.normalize_line_breaks("U unlocks steel.**then glass**U Gain more", "*", "U")
              == "unlocks steel.\n\nthen glass\n\nGain more"
              and adapter_gamemaker.reassemble_line_breaks("U gain a**bunch of food", "ottieni", "*", "U")
              == "U ottieni")
    check("titoli ed elenchi senza simbolo non si uniscono; '* *' (riga vuota con uno spazio) resta un a capo solo",
              adapter_gamemaker.normalize_line_breaks("Controls**move with WASD", "*", "U")
              == "Controls\n\nmove with WASD"
              and adapter_gamemaker.normalize_line_breaks("wood**stone**iron ore", "*", "U")
              == "wood\n\nstone\n\niron ore"
              and adapter_gamemaker.reassemble_line_breaks("Welcome to the game!* *Press start to begin",
                                                     "Benvenuto nel gioco! Premi start per iniziare", "*", "U")
              == "Benvenuto nel gioco!* *Premi start per iniziare")
    check("ricomposizione impossibile (paragrafi diversi o '*' lasciato dal traduttore) -> resta l'originale",
              adapter_gamemaker.reassemble_line_breaks(EN["skillDesc0"], "a\n\nb", "*", "U") is None
              and adapter_gamemaker.reassemble_line_breaks("iron*ore", "ferro*minerale", "*", "U") is None)
    official_found = adapter_gamemaker.official_letters(game, found, ["local/english.json"])
    font = adapter_gamemaker.font_chars(game, None, official_found)
    check("font: quelli solo ASCII (chat, numeri) non contano se le traduzioni ufficiali usano accenti",
              font is not None and "ò" in font and "ì" in font
              and adapter_gamemaker.font_chars(game) == ascii_)

    # --- full translation (only english.json, with backup) and restore
    automatic.create_backend = lambda name, log=print: Translator()
    path, completed, outcome = automatic.analyze_and_translate(game, "auto", "it", "dictionary", silent)
    profile = json.load(open(path, encoding="utf-8"))
    check("profilo: solo english.json, con convenzioni e lettere ufficiali",
              [v["path"] for v in profile["file"]] == ["local/english.json"]
              and profile["file"][0]["line_break"] == "*" and profile["file"][0]["bullet"] == "U"
              and "é" in profile["official_letters"])
    translated = json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))
    check("tradotti: nomi con a capo bilanciato, paragrafi con simbolo, segnaposto, accenti",
              translated["itemName0"] == "legno" and translated["itemName1"] == "minerale di*ferro"
              and translated["skillDesc0"] == "U sblocca l'acciaio**U sblocca il vetro**U ottieni PE quando costruisci "
                                            "strutture"
              and translated["playing"] == "Sta giocando a {game mode}"
              and "Così sarà più" in translated["warning"])
    check("tradotti: 25%*less -> a capo dentro la frase, nessun '*' doppio inventato",
              translated["skillDesc1"].startswith("U sblocca le falegnamerie**U ")
              and translated["skillDesc1"].count("*") == EN["skillDesc1"].count("*"))
    check("identificativo rimasto al posto di un testo: non tradotto",
              translated["structureDesc9"] == "Structure.NuclearFurnace")
    changed_files = [n for n in before if fingerprints(local)[n] != before[n]]
    check("solo english.json cambiato, le traduzioni ufficiali intatte", changed_files == ["english.json"])
    automatic.restore(game, silent)
    check("ripristino identico", fingerprints(local) == before)

    # --- generic search: among per-language files (same family) only the original language is kept
    web = os.path.join(base, "gioco web")
    texts = {"english": ["The castle is dark and the door is closed", "You have found a key in the room"],
             "french": ["Le château est sombre et la porte est fermée", "Vous avez trouvé une clé dans la salle"],
             "german": ["Das Schloss ist dunkel und die Tür ist zu", "Du hast einen Schlüssel im Raum gefunden"],
             "italian": ["Il castello è buio e la porta è chiusa", "Hai trovato una chiave nella stanza"]}
    for rel_folder, names in (("locales", ("english", "french", "german")), ("lang/en", ("ui",)),
                               ("lang/fr", ("ui",)), ("data", ("items",)), ("nazioni", ("english", "french"))):
        os.makedirs(os.path.join(web, rel_folder), exist_ok=True)
        for n in names:
            texts_language = "french" if rel_folder == "lang/fr" else n if n in texts else "english"
            if rel_folder == "nazioni":
                texts_language = "english"  # language names in the file name, but the texts are all English
            with open(os.path.join(web, rel_folder, n + ".json"), "w", encoding="utf-8") as f:
                json.dump({"t%d" % i: t for i, t in enumerate(texts[texts_language] * 5)}, f, ensure_ascii=False)

    def cand(rel, n=10):
        return {"path": rel, "readable_strings": n}
    candidates = [cand("locales/english.json"), cand("locales/french.json"), cand("locales/german.json"),
                 cand("lang/en/ui.json"), cand("lang/fr/ui.json"), cand("data/items.json"),
                 cand("nazioni/english.json"), cand("nazioni/french.json")]
    kept_items, dropped, target_files = automatic._original_language_only(candidates, web, "auto", "it", silent)
    check("ricerca generica: delle famiglie di lingue resta solo l'inglese (anche lang/{L}/ui.json)",
              sorted(dropped) == ["lang/fr/ui.json", "locales/french.json", "locales/german.json"] and not target_files)
    check("ricerca generica: un file col nome di una lingua ma testi inglesi non viene tolto",
              "nazioni/french.json" in [c["path"] for c in kept_items])
    _, dropped, _ = automatic._original_language_only(
        [cand("locales/english.json", 2), cand("locales/french.json", 10)], web, "auto", "it", silent)
    check("ricerca generica: un inglese molto piu' piccolo (parziale) non fa togliere le altre lingue", not dropped)
    with open(os.path.join(web, "locales", "italian.json"), "w", encoding="utf-8") as f:
        json.dump({"t%d" % i: t for i, t in enumerate(texts["italian"] * 5)}, f, ensure_ascii=False)
    _, _, target_files = automatic._original_language_only(
        candidates + [cand("locales/italian.json")], web, "auto", "it", silent)
    check("ricerca generica: c'e' gia' italian.json -> segnalato (lingua ufficiale)",
              target_files == ["locales/italian.json"])
    check("famiglie: english_credits.json da solo non e' un file di localizzazione",
              adapter_gamemaker.family("english_credits.json") != adapter_gamemaker.family("english.json")
              and adapter_gamemaker.path_language("data/merchant.json") is None)
    check("ricomposizione: '*' e spazi in testa e in coda restano fermi",
              adapter_gamemaker.reassemble_line_breaks("*attacks burn!", "gli attacchi bruciano!", "*", "U")
              == "*gli attacchi bruciano!"
              and adapter_gamemaker.reassemble_line_breaks("your time: ", "il tuo tempo:", "*", "U")
              == "il tuo tempo: ")

    # --- official language in the JSON files
    with open(os.path.join(local, "italian.json"), "w", encoding="utf-8") as f:
        json.dump({k: "it " + v for k, v in EN.items()}, f)
    r = official_language.detect(game, "it", "GameMaker")
    check("italian.json presente -> lingua ufficiale", r["present"] is True)
    os.remove(os.path.join(local, "italian.json"))

    # --- Stop button during the translation: nothing written to the game, translations made so far saved
    project.TEXTS_PER_GROUP = 5
    memory = os.path.join(base, "patchsmith", "memory")
    for f in os.listdir(memory):
        os.remove(os.path.join(memory, f))
    automatic.create_backend = lambda name, log=print: Translator(stop_after=2)
    interruption.reset()
    try:
        automatic.analyze_and_translate(game, "auto", "it", "dictionary", silent)
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    saved_count = sum(len(json.load(open(os.path.join(memory, f), encoding="utf-8"))) for f in os.listdir(memory))
    check("Ferma durante la traduzione: si ferma, gioco non toccato, traduzioni fatte salvate",
              stopped and fingerprints(local) == before and 0 < saved_count < len(EN))
    automatic.create_backend = lambda name, log=print: Translator()
    automatic.analyze_and_translate(game, "auto", "it", "dictionary", silent)
    check("dopo Ferma si riparte da dove ci si era fermati e si completa",
              json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))["itemName0"] == "legno")
    # Stop pressed when the translation memory is already complete: nothing is written to the game anyway
    automatic.restore(game, silent)
    interruption.request_stop()
    try:
        automatic.analyze_and_translate(game, "auto", "it", "dictionary", silent)
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    check("Ferma con le traduzioni gia' tutte in memoria: si ferma prima di scrivere", stopped
              and fingerprints(local) == before)

    # file fixed by hand after the translation (with letters adapted to the fonts): it is not a game update
    automatic.analyze_and_translate(game, "auto", "it", "dictionary", silent)
    translated = json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))
    translated["itemName6"] = "carbone fossile"
    with open(os.path.join(local, "english.json"), "w", encoding="utf-8") as f:
        json.dump(translated, f, ensure_ascii=False, indent=2)
    messages = []
    automatic.analyze_and_translate(game, "auto", "it", "dictionary", messages.append)
    check("file tradotto e poi corretto a mano: riconosciuto (non scambiato per un aggiornamento)",
              any("modified by hand" in m for m in messages) and not any("new original" in m for m in messages))
    automatic.restore(game, silent)
    check("... e il ripristino rimette l'inglese originale", fingerprints(local) == before)

    # old experimental game profile that had also translated french.json: replaced, french.json goes back to the
    # original
    import shutil
    french = os.path.join(local, "french.json")
    shutil.copyfile(french, french + ".orig-test")
    project.create_backup(french)
    info = project.read_info(french)
    with open(french, "w", encoding="utf-8") as f:
        json.dump({k: "[it] " + v for k, v in FR.items()}, f, ensure_ascii=False)
    info["written_hashes"] = [hashlib.sha256(open(french, "rb").read()).hexdigest()]
    project.save_info(french, info)
    old_profile = automatic.find_profile(game)
    old = json.load(open(old_profile, encoding="utf-8"))
    old["file"].append({"path": "local/french.json", "format": "json", "include": list(FR), "exclude": []})
    with open(old_profile, "w", encoding="utf-8") as f:
        json.dump(old, f)
    messages = []
    automatic.analyze_and_translate(game, "auto", "it", "dictionary", messages.append)
    check("profilo precedente con french.json tradotto: rimesso l'originale, non piu' nel profilo",
              open(french, "rb").read() == open(french + ".orig-test", "rb").read()
              and any("original put back" in m for m in messages)
              and [v["path"] for v in json.load(open(old_profile, encoding="utf-8"))["file"]]
              == ["local/english.json"])
    os.remove(french + ".orig-test")
    automatic.restore(game, silent)

    # (Google's behavior on 429 and the interruptible waits are in test/test_limiter.py)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
