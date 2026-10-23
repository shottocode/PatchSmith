"""Test dei file di localizzazione JSON di GameMaker (es. Forager) e del tasto "Ferma".

Uso: python -I test/test_gamemaker_json.py <cartella_temporanea_vuota>
Gioco finto: local/english.json + traduzioni ufficiali, con le convenzioni di Forager ('*' = a capo, "U " =
simbolo a inizio paragrafo), data.win con un font latino accentato e font solo ASCII (chat, numeri).
Scrive solo nella cartella temporanea; nessuna rete (traduttore finto, richieste HTTP simulate).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from patchsmith import (adapter_gamemaker, auto as automatico, backend, interruption as interruzione,
                        official_language as lingua_ufficiale, project as progetto, steam)
import test_gamemaker  # noqa: E402  (costruttore di data.win)

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def impronte(cartella):
    return {n: hashlib.sha256(open(os.path.join(cartella, n), "rb").read()).hexdigest()
            for n in sorted(os.listdir(cartella)) if "patchsmith" not in n}


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
DE = {k: v.replace("U débloque", "Schaltet").replace("*", " ") for k, v in FR.items()}  # tedesco: senza '*' ne' U

ITALIANO = {"wood": "legno", "iron ore": "minerale di ferro", "bottled torchbug": "lucciola in bottiglia",
            "demon horn": "corno di demone", "royal steel": "acciaio reale", "golden egg": "uovo d'oro",
            "coal": "carbone", "unlocks steel": "sblocca l'acciaio", "unlocks glass": "sblocca il vetro",
            "gain EP when building structures": "ottieni PE quando costruisci strutture",
            "unlocks carpentry stations": "sblocca le falegnamerie",
            "structures cost 25% less wood": "le strutture costano il 25% di legno in meno",
            "Playing [[0]]": "Sta giocando a [[0]]", "WARNING:": "ATTENZIONE:",
            "Are you sure you want to continue?": "Sei sicuro di voler continuare? Così sarà più",
            "unlocks the forge": "sblocca la forgia", "faster smelting": "fusione più veloce"}


class Traduttore(backend.Backend):
    """Traduttore finto: un piccolo dizionario, il resto "[it] ..."."""
    nome = "dizionario"

    def __init__(self, ferma_dopo=None):
        self.chiamate = 0
        self.ferma_dopo = ferma_dopo

    def traduci_righe(self, righe, origine, destinazione):
        interruzione.controlla()
        self.chiamate += 1
        if self.ferma_dopo and self.chiamate >= self.ferma_dopo:
            interruzione.richiedi()  # come il tasto "Ferma" premuto mentre si traduce
        return [ITALIANO.get(r, "[it] " + r) for r in righe]


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
    steam.cartella_steam = lambda: None

    gioco = os.path.join(base, "Forager finto")
    local = os.path.join(gioco, "local")
    os.makedirs(local)
    for nome, dati in (("english", EN), ("french", FR), ("spanish", ES), ("german", DE)):
        with open(os.path.join(local, nome + ".json"), "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)
    latino = set(chr(c) for c in range(32, 127)) | set("àèéìíòóùÀÈÉÌÒÙçâêôûœëïüö")
    ascii_ = set(chr(c) for c in range(32, 127))
    with open(os.path.join(gioco, "data.win"), "wb") as f:
        f.write(test_gamemaker.data_win([("fntText", latino), ("fntChat", ascii_), ("fnt_game", ascii_)]))
    open(os.path.join(gioco, "Forager.exe"), "wb").write(b"MZ")
    prima = impronte(local)

    # --- riconoscimento
    trovati = adapter_gamemaker.file_json_lingue(gioco)
    controlla("file JSON per lingua riconosciuti", sorted(trovati.values()) == ["de", "en", "es", "fr"])
    controlla("lingua dal nome: cinese tradizionale e thai",
              adapter_gamemaker.lingua_del_percorso("local/chinese_traditional.json") == "zh-TW"
              and adapter_gamemaker.lingua_del_percorso("local/thai.json") == "th"
              and adapter_gamemaker.lingua_del_percorso("local/chinese.json") == "zh")
    altri = [FR, ES, DE]
    controlla("convenzioni confermate dalle traduzioni ufficiali: '*' a capo, 'U' simbolo",
              adapter_gamemaker.markup(EN, altri) == {"a_capo": "*", "punto_elenco": "U"})
    articoli = {"k%d" % i: "A cat number %d**A dog" % i for i in range(20)}
    controlla("un articolo ('A cat') non e' un simbolo a inizio paragrafo",
              adapter_gamemaker.markup(articoli, [{k: "Un chat**Un chien" for k in articoli}])["punto_elenco"] is None)
    controlla("nei file di localizzazione anche le parole sole sono testo, gli identificativi no",
              adapter_gamemaker.testo_localizzazione("wood") and
              not adapter_gamemaker.testo_localizzazione("Structure.NuclearFurnace")
              and not adapter_gamemaker.testo_localizzazione("menu_title_2"))
    controlla("normalizzazione: frase intera, paragrafi su righe, simbolo tolto",
              adapter_gamemaker.normalizza_a_capo(EN["skillDesc1"], "*", "U")
              == "unlocks carpentry stations\n\nstructures cost 25% less wood")
    controlla("ricomposizione: a capo bilanciati, simboli e paragrafi rimessi",
              adapter_gamemaker.ricomponi_a_capo("iron*ore", "minerale di ferro", "*", "U") == "minerale di*ferro"
              and adapter_gamemaker.ricomponi_a_capo("wood", "legno", "*", "U") == "legno"
              and adapter_gamemaker.ricomponi_a_capo(EN["skillDesc0"], "a\n\nb c\n\nd", "*", "U")
              == "U a**U b c**U d")
    spezzata = "U gain EP when selling items**U items discarded from your**inventory are instantly**sold instead"
    controlla("frase spezzata da '**' (senza simbolo, minuscola, senza punto prima): tradotta intera, poi rimessa "
              "sulle stesse righe",
              adapter_gamemaker.normalizza_a_capo(spezzata, "*", "U")
              == "gain EP when selling items\n\nitems discarded from your inventory are instantly sold instead"
              and adapter_gamemaker.ricomponi_a_capo(spezzata, "a b c\n\nd e f g h i", "*", "U")
              == "U a b c**U d e f**g h**i"
              and adapter_gamemaker.normalizza_a_capo("**awarded only to those**who beat the tomb!", "*", "U")
              == "\n\nawarded only to those who beat the tomb!"
              and adapter_gamemaker.normalizza_a_capo("U unlocks steel.**then glass**U Gain more", "*", "U")
              == "unlocks steel.\n\nthen glass\n\nGain more"
              and adapter_gamemaker.ricomponi_a_capo("U gain a**bunch of food", "ottieni", "*", "U") == "U ottieni")
    controlla("titoli ed elenchi senza simbolo non si uniscono; '* *' (riga vuota con uno spazio) resta un a capo solo",
              adapter_gamemaker.normalizza_a_capo("Controls**move with WASD", "*", "U") == "Controls\n\nmove with WASD"
              and adapter_gamemaker.normalizza_a_capo("wood**stone**iron ore", "*", "U") == "wood\n\nstone\n\niron ore"
              and adapter_gamemaker.ricomponi_a_capo("Welcome to the game!* *Press start to begin",
                                                     "Benvenuto nel gioco! Premi start per iniziare", "*", "U")
              == "Benvenuto nel gioco!* *Premi start per iniziare")
    controlla("ricomposizione impossibile (paragrafi diversi o '*' lasciato dal traduttore) -> resta l'originale",
              adapter_gamemaker.ricomponi_a_capo(EN["skillDesc0"], "a\n\nb", "*", "U") is None
              and adapter_gamemaker.ricomponi_a_capo("iron*ore", "ferro*minerale", "*", "U") is None)
    uff = adapter_gamemaker.lettere_ufficiali(gioco, trovati, ["local/english.json"])
    font = adapter_gamemaker.caratteri_font(gioco, None, uff)
    controlla("font: quelli solo ASCII (chat, numeri) non contano se le traduzioni ufficiali usano accenti",
              font is not None and "ò" in font and "ì" in font
              and adapter_gamemaker.caratteri_font(gioco) == ascii_)

    # --- traduzione completa (solo english.json, con backup) e ripristino
    automatico.crea_backend = lambda nome, log=print: Traduttore()
    percorso, completato, esito = automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
    profilo = json.load(open(percorso, encoding="utf-8"))
    controlla("profilo: solo english.json, con convenzioni e lettere ufficiali",
              [v["percorso"] for v in profilo["file"]] == ["local/english.json"]
              and profilo["file"][0]["a_capo"] == "*" and profilo["file"][0]["punto_elenco"] == "U"
              and "é" in profilo["lettere_ufficiali"])
    tradotto = json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))
    controlla("tradotti: nomi con a capo bilanciato, paragrafi con simbolo, segnaposto, accenti",
              tradotto["itemName0"] == "legno" and tradotto["itemName1"] == "minerale di*ferro"
              and tradotto["skillDesc0"] == "U sblocca l'acciaio**U sblocca il vetro**U ottieni PE quando costruisci "
                                            "strutture"
              and tradotto["playing"] == "Sta giocando a {game mode}"
              and "Così sarà più" in tradotto["warning"])
    controlla("tradotti: 25%*less -> a capo dentro la frase, nessun '*' doppio inventato",
              tradotto["skillDesc1"].startswith("U sblocca le falegnamerie**U ")
              and tradotto["skillDesc1"].count("*") == EN["skillDesc1"].count("*"))
    controlla("identificativo rimasto al posto di un testo: non tradotto",
              tradotto["structureDesc9"] == "Structure.NuclearFurnace")
    cambiati = [n for n in prima if impronte(local)[n] != prima[n]]
    controlla("solo english.json cambiato, le traduzioni ufficiali intatte", cambiati == ["english.json"])
    automatico.ripristina(gioco, silenzio)
    controlla("ripristino identico", impronte(local) == prima)

    # --- ricerca generica: tra file per lingua (stessa famiglia) si tiene solo la lingua originale
    web = os.path.join(base, "gioco web")
    testi = {"english": ["The castle is dark and the door is closed", "You have found a key in the room"],
             "french": ["Le château est sombre et la porte est fermée", "Vous avez trouvé une clé dans la salle"],
             "german": ["Das Schloss ist dunkel und die Tür ist zu", "Du hast einen Schlüssel im Raum gefunden"],
             "italian": ["Il castello è buio e la porta è chiusa", "Hai trovato una chiave nella stanza"]}
    for cartella_rel, nomi in (("locales", ("english", "french", "german")), ("lang/en", ("ui",)),
                               ("lang/fr", ("ui",)), ("data", ("items",)), ("nazioni", ("english", "french"))):
        os.makedirs(os.path.join(web, cartella_rel), exist_ok=True)
        for n in nomi:
            lingua_testi = "french" if cartella_rel == "lang/fr" else n if n in testi else "english"
            if cartella_rel == "nazioni":
                lingua_testi = "english"  # nomi di lingua nel file, ma i testi sono tutti inglesi
            with open(os.path.join(web, cartella_rel, n + ".json"), "w", encoding="utf-8") as f:
                json.dump({"t%d" % i: t for i, t in enumerate(testi[lingua_testi] * 5)}, f, ensure_ascii=False)

    def cand(rel, n=10):
        return {"percorso": rel, "stringhe_leggibili": n}
    candidati = [cand("locales/english.json"), cand("locales/french.json"), cand("locales/german.json"),
                 cand("lang/en/ui.json"), cand("lang/fr/ui.json"), cand("data/items.json"),
                 cand("nazioni/english.json"), cand("nazioni/french.json")]
    tenuti, tolti, arrivo = automatico._solo_lingua_originale(candidati, web, "auto", "it", silenzio)
    controlla("ricerca generica: delle famiglie di lingue resta solo l'inglese (anche lang/{L}/ui.json)",
              sorted(tolti) == ["lang/fr/ui.json", "locales/french.json", "locales/german.json"] and not arrivo)
    controlla("ricerca generica: un file col nome di una lingua ma testi inglesi non viene tolto",
              "nazioni/french.json" in [c["percorso"] for c in tenuti])
    _, tolti, _ = automatico._solo_lingua_originale(
        [cand("locales/english.json", 2), cand("locales/french.json", 10)], web, "auto", "it", silenzio)
    controlla("ricerca generica: un inglese molto piu' piccolo (parziale) non fa togliere le altre lingue", not tolti)
    with open(os.path.join(web, "locales", "italian.json"), "w", encoding="utf-8") as f:
        json.dump({"t%d" % i: t for i, t in enumerate(testi["italian"] * 5)}, f, ensure_ascii=False)
    _, _, arrivo = automatico._solo_lingua_originale(candidati + [cand("locales/italian.json")], web, "auto", "it",
                                                     silenzio)
    controlla("ricerca generica: c'e' gia' italian.json -> segnalato (lingua ufficiale)",
              arrivo == ["locales/italian.json"])
    controlla("famiglie: english_credits.json da solo non e' un file di localizzazione",
              adapter_gamemaker.famiglia("english_credits.json") != adapter_gamemaker.famiglia("english.json")
              and adapter_gamemaker.lingua_del_percorso("data/merchant.json") is None)
    controlla("ricomposizione: '*' e spazi in testa e in coda restano fermi",
              adapter_gamemaker.ricomponi_a_capo("*attacks burn!", "gli attacchi bruciano!", "*", "U")
              == "*gli attacchi bruciano!"
              and adapter_gamemaker.ricomponi_a_capo("your time: ", "il tuo tempo:", "*", "U") == "il tuo tempo: ")

    # --- lingua ufficiale nei file JSON
    with open(os.path.join(local, "italian.json"), "w", encoding="utf-8") as f:
        json.dump({k: "it " + v for k, v in EN.items()}, f)
    r = lingua_ufficiale.rileva(gioco, "it", "GameMaker")
    controlla("italian.json presente -> lingua ufficiale", r["presente"] is True)
    os.remove(os.path.join(local, "italian.json"))

    # --- tasto Ferma durante la traduzione: niente scritto nel gioco, traduzioni fatte salvate
    progetto.TESTI_PER_GRUPPO = 5
    memoria = os.path.join(base, "patchsmith", "memory")
    for f in os.listdir(memoria):
        os.remove(os.path.join(memoria, f))
    automatico.crea_backend = lambda nome, log=print: Traduttore(ferma_dopo=2)
    interruzione.azzera()
    try:
        automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    salvate = sum(len(json.load(open(os.path.join(memoria, f), encoding="utf-8"))) for f in os.listdir(memoria))
    controlla("Ferma durante la traduzione: si ferma, gioco non toccato, traduzioni fatte salvate",
              fermato and impronte(local) == prima and 0 < salvate < len(EN))
    automatico.crea_backend = lambda nome, log=print: Traduttore()
    automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
    controlla("dopo Ferma si riparte da dove ci si era fermati e si completa",
              json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))["itemName0"] == "legno")
    # Ferma premuto quando la memoria e' gia' completa: non si scrive comunque nel gioco
    automatico.ripristina(gioco, silenzio)
    interruzione.richiedi()
    try:
        automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    controlla("Ferma con le traduzioni gia' tutte in memoria: si ferma prima di scrivere", fermato
              and impronte(local) == prima)

    # file corretto a mano dopo la traduzione (con lettere adattate ai font): non e' un aggiornamento del gioco
    automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", silenzio)
    tradotto = json.load(open(os.path.join(local, "english.json"), encoding="utf-8"))
    tradotto["itemName6"] = "carbone fossile"
    with open(os.path.join(local, "english.json"), "w", encoding="utf-8") as f:
        json.dump(tradotto, f, ensure_ascii=False, indent=2)
    messaggi = []
    automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", messaggi.append)
    controlla("file tradotto e poi corretto a mano: riconosciuto (non scambiato per un aggiornamento)",
              any("modificato a mano" in m for m in messaggi) and not any("nuovo originale" in m for m in messaggi))
    automatico.ripristina(gioco, silenzio)
    controlla("... e il ripristino rimette l'inglese originale", impronte(local) == prima)

    # profilo sperimentale vecchio che aveva tradotto anche french.json: sostituito, french.json torna originale
    import shutil
    francese = os.path.join(local, "french.json")
    shutil.copyfile(francese, francese + ".orig-test")
    progetto.crea_backup(francese)
    info = progetto.leggi_info(francese)
    with open(francese, "w", encoding="utf-8") as f:
        json.dump({k: "[it] " + v for k, v in FR.items()}, f, ensure_ascii=False)
    info["hash_scritti"] = [hashlib.sha256(open(francese, "rb").read()).hexdigest()]
    progetto.salva_info(francese, info)
    profilo_vecchio = automatico.trova_profilo(gioco)
    vecchio = json.load(open(profilo_vecchio, encoding="utf-8"))
    vecchio["file"].append({"percorso": "local/french.json", "formato": "json", "includi": list(FR), "escludi": []})
    with open(profilo_vecchio, "w", encoding="utf-8") as f:
        json.dump(vecchio, f)
    messaggi = []
    automatico.analizza_e_traduci(gioco, "auto", "it", "dizionario", messaggi.append)
    controlla("profilo precedente con french.json tradotto: rimesso l'originale, non piu' nel profilo",
              open(francese, "rb").read() == open(francese + ".orig-test", "rb").read()
              and any("rimesso l'originale" in m for m in messaggi)
              and [v["percorso"] for v in json.load(open(profilo_vecchio, encoding="utf-8"))["file"]]
              == ["local/english.json"])
    os.remove(francese + ".orig-test")
    automatico.ripristina(gioco, silenzio)

    # (il comportamento di Google sul 429 e le attese interrompibili sono in test/test_limiter.py)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
