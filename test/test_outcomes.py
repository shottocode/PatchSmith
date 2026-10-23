"""Structured outcome of the operations (outcome.py), interface dialogs for each outcome and "Create report".

Usage: python -I test/test_outcomes.py <empty_temp_folder>
Only fake games created here (web, GameMaker, Unity Mono/IL2CPP/Localization) and the test translator or a Google
block written in a test services.json: no network, no real game. PatchSmith's data, the user folder and %TEMP% are
all in the test folder.
"""
import base64
import contextlib
import io
import json
import os
import sys
import time
import tokenize
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import fake_web_game  # noqa: E402
import test_gamemaker  # noqa: E402  (fake GameMaker game)
import test_unity  # noqa: E402  (fake Unity game and fake components)
from patchsmith import (__version__, adapter_unity, auto as automatic, backend, outcome, ollama,
                        project, report)  # noqa: E402
from patchsmith import detection, system  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def fingerprints(folder):
    """{relative path: (size, date)} of all the files: to see that nothing was touched."""
    output = {}
    for root, _, files in os.walk(folder):
        for f in files:
            s = os.stat(os.path.join(root, f))
            output[os.path.relpath(os.path.join(root, f), folder)] = (s.st_size, s.st_mtime_ns)
    return output


def unity_il2cpp_game(folder, tables=False, crc=0):
    """Fake Unity IL2CPP game; with tabelle=True also the English Unity Localization bundle and the Addressables
    catalog (crc: the value of m_Crc in the bundle options)."""
    test_unity.create_game(folder)
    os.rename(os.path.join(folder, "Gioco_Data", "Managed"), os.path.join(folder, "Gioco_Data", "nonusata"))
    os.makedirs(os.path.join(folder, "Gioco_Data", "il2cpp_data", "Metadata"))
    open(os.path.join(folder, "Gioco_Data", "il2cpp_data", "Metadata", "global-metadata.dat"), "wb").write(
        b"\xaf\x1b\xb1\xfa" + b"\x00" * 64)
    open(os.path.join(folder, "GameAssembly.dll"), "wb").write(b"MZ finto")
    if tables:
        aa = os.path.join(folder, "Gioco_Data", "StreamingAssets", "aa")
        os.makedirs(os.path.join(aa, "StandaloneWindows64"))
        open(os.path.join(aa, "StandaloneWindows64", "localization-string-tables-english(en)_assets_all.bundle"),
             "wb").write(b"finto")
        options = '{"m_Hash":"8f2c","m_Crc":%d,"m_Timeout":0}' % crc
        json.dump({"m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIds": [],
                   "m_ExtraDataString": base64.b64encode(b"\x07\x00" + options.encode("utf-16-le")).decode("ascii")},
                  open(os.path.join(aa, "catalog.json"), "w"))


def gamemaker_game(folder, italian=False):
    os.makedirs(os.path.join(folder, "Localization", "french"))
    for rel, text in (("Localization/english_ui.txt", test_gamemaker.UI),
                       ("Localization/english_npc.txt", test_gamemaker.NPC),
                       ("Localization/french/ui.txt", test_gamemaker.FR_UI)):
        open(os.path.join(folder, *rel.split("/")), "wb").write(text.encode("utf-8"))
    latin = set(chr(c) for c in range(32, 127)) | set("àèéùÀÈÉÙíóúÍÓÚ")
    open(os.path.join(folder, "data.win"), "wb").write(test_gamemaker.data_win([("fn_testo", latin)]))
    open(os.path.join(folder, "Gioco.exe"), "wb").write(b"MZ")
    if italian:
        os.makedirs(os.path.join(folder, "Localization", "italian"))
        open(os.path.join(folder, "Localization", "italian", "ui.txt"), "wb").write(
            b"0 Combatti\r\n1 Cambia\r\n2 Cattura\r\n3 Fuggi\r\n4 Cosa deve fare \r\n5  ora?\r\n")


def outcome_of(dictionary):
    return dictionary.get("esito") if isinstance(dictionary, dict) else None


def without_smith(text):
    return "smith.py" not in text and "python -I" not in text


class HalfTranslator(backend.Fake):
    """Test translator that does not translate the texts with 'lantern': they stay untranslated."""

    def translate_lines(self, lines, origin, dest_lang, **_):
        return ["" if "lantern" in r.lower() else "[it] " + r for r in lines]


def test_web(base):
    game = os.path.join(base, "web", "Lantern Keeper")
    fake_web_game.create(game)
    before = fingerprints(game)
    path, completed, d = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    e = outcome_of(d)
    check("web tradotto: esito OK, scritto, motore web, testi contati, valori di prima invariati",
              completed and path and e and e.code == "OK" and e.written and e.engine == "web"
              and e.translated_items > 10 and e.remaining == 0)
    f = outcome.window(e, "traduci")
    check("web tradotto: finestra 'Traduzione installata' (prima non c'era nessuna finestra)",
              f["titolo"] == "Translation installed" and "Start the game" in f["testo"]
              and "Translated texts: %d." % e.translated_items in f["testo"] and f["pulsanti"] == [("OK", None)])

    d = {}
    done = automatic.reapply(game, silent, test=True, outcome=d)
    check("riapplica: esito OK e finestra 'Traduzione riapplicata'", done and outcome_of(d).code == "OK"
              and outcome.window(outcome_of(d), "riapplica")["titolo"] == "Translation reapplied")

    # game open: restore refused, no file touched
    real = system.game_open
    system.game_open = lambda c: True
    translated = fingerprints(game)
    d = {}
    try:
        done = automatic.restore(game, silent, outcome=d)
    finally:
        system.game_open = real
    e = outcome_of(d)
    f = outcome.window(e, "ripristina")
    check("ripristino con il gioco aperto: GIOCO_APERTO, nessun file toccato, 'Riprova'",
              not done and e.code == "GIOCO_APERTO" and fingerprints(game) == translated
              and f["titolo"] == "The game is open" and ("Try again", "chiudi_gioco") in f["pulsanti"])

    d = {}
    check("ripristino: esito OK, 'Originali ripristinati'", automatic.restore(game, silent, outcome=d)
              and outcome_of(d).code == "OK" and outcome.window(outcome_of(d), "ripristina")["titolo"]
              == "Originals restored" and fingerprints(game).keys() == before.keys())
    d = {}
    automatic.restore(game, silent, outcome=d)
    check("di nuovo ripristino: GIA_ORIGINALE", outcome_of(d).code == "GIA_ORIGINALE"
              and outcome.window(outcome_of(d), "ripristina")["titolo"] == "Game already original")

    empty = os.path.join(base, "nessun-gioco")
    os.makedirs(empty)
    d = {}
    check("riapplica senza traduzioni salvate: NULLA_DA_FARE",
              not automatic.reapply(empty, silent, outcome=d) and outcome_of(d).code == "NULLA_DA_FARE")

    # some of the texts not translated: PARZIALE with the remaining texts to show (test translation memory from
    # scratch)
    with open(automatic.find_profile(game), encoding="utf-8") as f:
        os.remove(project.memory_path(json.load(f), test=True))
    real = automatic.create_backend
    automatic.create_backend = lambda name, log=print: HalfTranslator()
    try:
        _, completed, d = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    finally:
        automatic.create_backend = real
    e = outcome_of(d)
    f = outcome.window(e, "traduci")
    check("testi rimasti: PARZIALE, scritto, con i primi originali rimasti e 'Mostra i rimasti'",
              not completed and e.code == "PARZIALE" and e.written and e.remaining > 0
              and any("antern" in t for t in e.remaining_examples) and ("Show remaining", "mostra_rimasti")
              in f["pulsanti"] and f["titolo"] == "Translation partially installed"
              and "left untranslated: %d" % e.remaining in f["testo"])
    check("testi rimasti: elenco leggibile", "Texts left untranslated: %d" % e.remaining
              in outcome.remaining_text(e) and "- " in outcome.remaining_text(e))
    automatic.restore(game, silent)
    return game


def test_google_blocked(base, game):
    """Google paused because of a block (written in the test translators' state): no requests, clear outcome."""
    state = project.data_folder("services.json")
    now = time.time()
    with open(state, "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": [], "blocco": {
            "dal": now - 60, "fino": now + 86400, "livello": 1, "tipo": "a_freddo", "http": 429,
            "dettaglio": None, "disattivato": False}}}, f)
    # the test translator's translation memory (.test.json) does not apply to Google: requests would be needed,
    # which the pause prevents before one is sent
    _, completed, d = automatic.analyze_and_translate(game, "auto", "it", "google", silent)
    e = outcome_of(d)
    f = outcome.window(e, "traduci", local_ready=False, current="google")
    check("Google in pausa: TRADUTTORE_BLOCCATO, con il motivo e il traduttore locale da installare",
              not completed and e and e.code == "TRADUTTORE_BLOCCATO" and "Google" in e.reason
              and not e.written and ("Install the local translator", "installa_locale") in f["pulsanti"]
              and f["titolo"] == "Translation interrupted")
    check("Google in pausa: nessun riferimento alla riga di comando", without_smith(f["testo"] + e.reason))
    f = outcome.window(e, "traduci", local_ready=True, current="google")
    check("... con il traduttore locale installato: 'Usa il traduttore locale'",
              ("Use the local translator", "usa_locale") in f["pulsanti"])
    os.remove(state)
    automatic.restore(game, silent)


def test_gamemaker_open(base):
    game = os.path.join(base, "gm", "Gioco GM")
    gamemaker_game(game)
    before = fingerprints(game)
    actual = system.game_open, project.game_open
    system.game_open = project.game_open = lambda c: True
    try:
        _, completed, d = automatic.analyze_and_translate(game, "auto", "it", "finto", silent)
    finally:
        system.game_open, project.game_open = actual
    e = outcome_of(d)
    f = outcome.window(e, "traduci")
    check("GameMaker con il gioco aperto: GIOCO_APERTO, niente scritto, nessun file del gioco cambiato",
              not completed and e.code == "GIOCO_APERTO" and not e.written and fingerprints(game) == before
              and "riapplica" in e.actions and f["titolo"] == "The game is open")

    italian = os.path.join(base, "gm-it", "Gioco GM")
    gamemaker_game(italian, italian=True)
    before = fingerprints(italian)
    path, completed, d = automatic.analyze_and_translate(italian, "auto", "it", "finto", silent)
    e = outcome_of(d)
    check("GameMaker con l'italiano ufficiale: UFFICIALE (con le righe per la domanda sulla lingua ufficiale), "
              "nulla toccato", not path and e.code == "UFFICIALE" and e.extra.get("lingua_ufficiale")
              and e.extra.get("righe") and fingerprints(italian) == before and "Italian" in e.reason)
    # if the official language escapes the first check, the search of the GameMaker files finds it
    _, d = automatic._prepare_gamemaker(italian, None, "auto", "it", detection.detect(italian), silent)
    e = outcome_of(d)
    f = outcome.window(e, "traduci")
    check("GameMaker, file della lingua di arrivo: UFFICIALE con 'Traduci comunque'", e.code == "UFFICIALE"
              and ("Translate anyway", "traduci_comunque") in f["pulsanti"] and fingerprints(italian) == before)


def test_unity_games(base):
    test_unity.prepare_components(os.path.join(base, "componenti"))
    adapter_unity.is_running = lambda *_: False

    il2cpp = os.path.join(base, "il2cpp", "Gioco")
    unity_il2cpp_game(il2cpp)
    before = fingerprints(il2cpp)
    path, _, d = automatic.analyze_and_translate(il2cpp, "auto", "it", "finto", silent)
    e = outcome_of(d)
    f = outcome.window(e, "traduci")
    check("Unity IL2CPP senza tabelle: NON_SUPPORTATO con 'IL2CPP' nel motivo, nulla toccato, 'Crea rapporto'",
              not path and e.code == "NON_SUPPORTATO" and "IL2CPP" in e.reason and fingerprints(il2cpp) == before
              and ("Create report", "crea_rapporto") in f["pulsanti"] and "I haven't made any changes" in f["testo"])

    crc = os.path.join(base, "crc", "Gioco")
    unity_il2cpp_game(crc, tables=True, crc=2737410034)
    before = fingerprints(crc)
    path, _, d = automatic.analyze_and_translate(crc, "auto", "it", "finto", silent)
    e = outcome_of(d)
    check("Unity Localization con CRC: NON_SUPPORTATO per l'impronta, non 'serve BepInEx 6', nulla toccato",
              not path and e.code == "NON_SUPPORTATO" and "BepInEx 6" not in e.reason and "CRC" in e.reason
              and e.engine == "unity-localization" and fingerprints(crc) == before)

    anticheat = os.path.join(base, "anticheat", "Gioco")
    test_unity.create_game(anticheat)
    open(os.path.join(anticheat, "BEService.exe"), "wb").write(b"MZ")
    before = fingerprints(anticheat)
    path, _, d = automatic.analyze_and_translate(anticheat, "auto", "it", "finto", silent)
    e = outcome_of(d)
    check("Unity con anti-cheat: ANTICHEAT, nulla toccato", not path and e.code == "ANTICHEAT"
              and fingerprints(anticheat) == before and outcome.window(e)["titolo"] == "Game with anti-cheat")

    mono = os.path.join(base, "mono", "Gioco")
    test_unity.create_game(mono)
    path, completed, d = automatic.analyze_and_translate(mono, "auto", "it", "finto", silent)
    e = outcome_of(d)
    f = outcome.window(e, "traduci")
    check("Unity Mono (prova): OK, XUnity installato, frasi nuove a Google, finestra con ALT+T e 'Frasi nuove'",
              completed and e.code == "OK" and e.written and e.engine == "unity" and e.live == "google"
              and "ALT+T" in f["testo"] and "New phrases while playing: XUnity, with free Google access" in f["testo"])
    with open(path, encoding="utf-8") as fp:
        profile = json.load(fp)
    check("profilo Unity: il traduttore scelto (non piu' 'xunity-google') e le frasi nuove al traduttore di "
              "PatchSmith", profile.get("backend") == "finto" and profile.get("dal_vivo") == "servizio")
    return mono


def test_exceptions():
    cases = [
        (adapter_unity.UnityError("The game is open: close it and try again.", "GIOCO_APERTO"), "GIOCO_APERTO"),
        (backend.BackendError(backend.NOT_INSTALLED, "TRADUTTORE_ASSENTE", command=backend.INSTALL_COMMAND),
         "TRADUTTORE_ASSENTE"),
        (backend.BackendError("Google is unavailable"), "TRADUTTORE_BLOCCATO"),
        (PermissionError(13, "Accesso negato", "D:/gioco/file.json"), "PERMESSI"),
        (system.SysError("I'm having trouble checking if the game is open"), "GIOCO_APERTO"),
        (RuntimeError("The backup doesn't match"), "ERRORE"),
        (KeyError("cartella_gioco"), "ERRORE"),
    ]
    codes = [outcome.from_exception(err).code for err, _ in cases]
    check("errori -> esiti: gioco aperto, traduttore assente/fermo, permessi, errori noti e imprevisti",
              codes == [c for _, c in cases])
    unexpected = outcome.from_exception(KeyError("cartella_gioco"))
    f = outcome.window(unexpected, "traduci")
    check("errore imprevisto: 'Errore imprevisto (KeyError)' e 'Crea rapporto'",
              f["titolo"] == "Unexpected error" and "Unexpected error (KeyError)" in f["testo"]
              and ("Create report", "crea_rapporto") in f["pulsanti"] and f["tipo"] == "errore")
    known_code = outcome.from_exception(RuntimeError("The backup doesn't match"))
    check("errore noto: il suo messaggio, senza il tipo", not known_code.extra.get("imprevisto")
              and "RuntimeError" not in outcome.window(known_code)["testo"]
              and "backup" in outcome.window(known_code)["testo"])
    absent = outcome.from_exception(cases[1][0])
    f = outcome.window(absent, "traduci", local_ready=False, current="locale")
    check("traduttore locale non installato: 'Installa il traduttore locale', niente comandi",
              ("Install the local translator", "installa_locale") in f["pulsanti"] and without_smith(f["testo"]))
    # DeepL without a key with the local translator already installed: "Install" would do nothing, we offer to use it
    no_key = outcome.from_exception(backend.BackendError("To use DeepL you need your key", "TRADUTTORE_ASSENTE"))
    f = outcome.window(no_key, "traduci", local_ready=True, current="deepl")
    check("traduttore scelto non pronto, locale installato: 'Usa il traduttore locale' (non 'Installa')",
              ("Use the local translator", "usa_locale") in f["pulsanti"]
              and not any(a == "installa_locale" for _, a in f["pulsanti"]))
    f = outcome.window(absent, "traduci", local_ready=True, current="locale")
    check("traduttore locale scelto ma il suo modello manca: resta 'Installa il traduttore locale'",
              ("Install the local translator", "installa_locale") in f["pulsanti"])
    # operations that do not touch the game: no "Try again" (it would repeat the last operation on the game) nor advice
    # about the open game or the Program Files folder
    permissions = outcome.from_exception(PermissionError(13, "Accesso negato", "C:/Utente/Desktop/rapporto.zip"))
    f = outcome.window(permissions, "rapporto")
    check("rapporto non creato: finestra sua, con il motivo, senza 'Riprova' ne' 'Crea rapporto'",
              f["titolo"] == "Report not created" and "Accesso negato" in f["testo"] and f["tipo"] == "errore"
              and f["pulsanti"] == [("OK", None)] and "game" not in f["testo"])
    f = outcome.window(permissions, "installa")
    check("installazione del traduttore locale con file bloccati: senza 'Riprova' e senza consigli sul gioco",
              not any(a == "chiudi_gioco" for _, a in f["pulsanti"]) and "Program Files" not in f["testo"]
              and "Accesso negato" in f["testo"])


def test_windows():
    """Every code has its own dialog: its own title, text without command line, valid buttons."""
    titles = {}
    for code in outcome.CODES:
        e = outcome.Outcome(code, "Motivo di prova.", engine="gamemaker", written=code in ("OK", "PARZIALE"),
                            translated_items=10, remaining=2 if code == "PARZIALE" else 0,
                            remaining_examples=["Start", "Quit"], actions=outcome._actions_for(code))
        f = outcome.window(e, "traduci")
        titles[code] = f["titolo"]
        ok = f["testo"] and without_smith(f["testo"]) and f["pulsanti"][0] == ("OK", None) and \
            all(a is None or a in outcome.ACTIONS for _, a in f["pulsanti"]) and f["tipo"] in (
                "info", "avviso", "errore")
        if not ok:
            check("finestra per %s" % code, False)
    check("una finestra per ogni codice, con titoli tutti diversi", len(set(titles.values())) == len(titles))
    e = outcome.Outcome("PARZIALE", engine="gamemaker", written=True, translated_items=5, remaining=1,
                        remaining_examples=["Quit"],
                        extra={"lingua_aggiunta": {"nome": "italiano", "impostata": True}})
    check("GameMaker con lingua aggiunta: la finestra lo dice", "new language for the game" in
              outcome.window(e)["testo"] and "italiano" in outcome.window(e)["testo"])
    e = outcome.Outcome("OK", engine="unity-localization", written=True, translated_items=3)
    check("Unity Localization: finestra con 'lascia selezionata la lingua English'",
              "English" in outcome.window(e)["testo"] and outcome.window(e)["titolo"] == "Translation installed")
    try:
        outcome.Outcome("INVENTATO")
        check("codice sconosciuto rifiutato", False)
    except ValueError:
        check("codice sconosciuto rifiutato", True)
    e = outcome.Outcome("PARZIALE", remaining_examples=["t%d" % i for i in range(50)], remaining=50)
    check("al massimo 20 esempi rimasti; riassunto per il registro", len(e.remaining_examples) == 20
              and e.summary().startswith("Result: PARZIALE"))
    # revision 0.16: left out of the game only translations the game could not show (rejected while writing):
    # "Fix translations" opens "Not written to the game" and the text names that list, not "Left untranslated"
    e = outcome.Outcome("PARZIALE", "A translation was not written because the game would not display it well.",
                        engine="gamemaker", written=True, translated_items=24, remaining=1,
                        remaining_examples=["VULTURE TOKEN"], actions=[], extra={"scartate": 1})
    f = outcome.window(e)
    check("solo traduzioni scartate dal gioco: 'Correggi traduzioni' su 'Non scritte nel gioco', e il testo lo dice",
              outcome.fixes_filter(e) == "gioco" and ("Fix translations", "correggi") in f["pulsanti"]
              and "\"Not written to the game\"" in f["testo"] and "\"Left untranslated\"" not in f["testo"])
    e = outcome.Outcome("PARZIALE", engine="gamemaker", written=True, translated_items=20, remaining=3,
                        remaining_examples=["A", "B"], actions=["usa_locale"], extra={"scartate": 1, "ritentati": 1})
    f = outcome.window(e, "riapplica", local_ready=True, current="locale")
    check("testi rimasti e traduzioni scartate dal gioco: 'Rimasti in originale', e il testo nomina anche l'altro "
              "elenco; un testo riprovato al singolare", outcome.fixes_filter(e) == "rimasti"
              and "Not written to the game" in f["testo"]
              and "One text rejected by the checks has already been retried" in f["testo"])


def test_report(base, mono):
    """Create report: the useful files are there, DeepL key and email are not (not even if they ended up in a log)."""
    key, email = "chiave-segreta-1234567:fx", "giocatore.segreto@example.com"
    with open(project.data_folder("settings.json"), "w", encoding="utf-8") as f:
        json.dump({"versione": 2, "backend": "deepl", "chiave_deepl": key, "email_mymemory": email,
                   "tema": "auto", "cartella": mono}, f)
    with open(project.data_folder("services.json"), "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": []}}, f)
    logs = project.data_folder("logs")
    os.makedirs(logs, exist_ok=True)
    name = os.path.splitext(os.path.basename(automatic.find_profile(mono)))[0]
    log_file = os.path.join(logs, "20260101-120000-%s.log" % name)
    with open(log_file, "w", encoding="utf-8") as f:
        f.write("PatchSmith - Analysis and translation\nchiave usata per sbaglio: %s (%s)\nResult: OK\n"
                % (key, email.upper()))
    with open(automatic.find_profile(mono), encoding="utf-8") as f:
        text_problems = project.problems_path(json.load(f))  # the texts left untranslated, with the reason
    os.makedirs(os.path.dirname(text_problems), exist_ok=True)
    with open(text_problems, "w", encoding="utf-8") as f:
        json.dump({"Quit": {"motivo": "rejected by the checks (not translated)", "dove": "traduzione",
                            "tentativi": 2}}, f)
    # logs of the game, of BepInEx and of Unity (company and product from app.info), crashes, live translator
    open(os.path.join(mono, "BepInEx", "LogOutput.log"), "w", encoding="utf-8").write(
        "[Message:   BepInEx] Chainloader startup complete\n")
    open(os.path.join(mono, "Gioco_Data", "app.info"), "w", encoding="utf-8").write("Azienda Finta\nGioco Finto")
    user = os.environ["USERPROFILE"]
    locallow = os.path.join(user, "AppData", "LocalLow", "Azienda Finta", "Gioco Finto")
    os.makedirs(locallow)
    open(os.path.join(locallow, "Player.log"), "w", encoding="utf-8").write("Mono path[0] = '%s'\n" % mono)
    open(os.path.join(locallow, "Player-prev.log"), "w", encoding="utf-8").write("NullReferenceException\n")
    crash = os.path.join(os.environ["TEMP"], "Azienda Finta", "Gioco Finto", "Crashes")
    for n, (folder, text) in enumerate((("Crash_vecchio", "vecchio"), ("Crash_nuovo", "crash nuovo"))):
        os.makedirs(os.path.join(crash, folder))
        open(os.path.join(crash, folder, "Player.log"), "w").write(text)
        os.utime(os.path.join(crash, folder), (time.time() - 100 + n * 50,) * 2)
    work = os.path.join(base, "ollama")
    os.makedirs(work)
    open(os.path.join(work, "servizio-xunity.log"), "w", encoding="utf-8").write(
        "x" * (report.MAX_BYTES + 1000) + "\n2026-10-18 ! traduzione interrotta\n")
    real = ollama.folder
    ollama.folder = lambda: work  # never the real logs of the local translator
    dest_lang = os.path.join(base, "Desktop")
    try:
        last = outcome.Outcome("PARZIALE", "Motivo", engine="unity", written=True, remaining=1,
                               remaining_examples=["Quit"])
        zip_ = report.create(mono, dest_lang, outcome=last)
        zip2 = report.create("", dest_lang)
    finally:
        ollama.folder = real
    check("rapporto: uno zip nella cartella scelta", os.path.isfile(zip_) and os.path.dirname(zip_) == dest_lang
              and os.path.basename(zip_).startswith("PatchSmith-report-%s-" % name))
    with zipfile.ZipFile(zip_) as z:
        names = set(z.namelist())
        content = {n: z.read(n).decode("utf-8") for n in names}
    expected_list = {"riepilogo.txt", "settings.json", "services.json", "rilevamento.txt",
              "registri/" + os.path.basename(log_file), "profilo/%s.json" % name, "gioco/BepInEx/LogOutput.log",
              "gioco/" + adapter_unity.CONFIG_XUNITY, "gioco/" + adapter_unity.CONFIG_BEPINEX,
              "gioco/patchsmith-unity.json", "traduttore-locale/servizio-xunity.log", "unity/Player.log",
              "unity/Player-prev.log", "unity/crash-Crash_nuovo-Player.log",
              "traduzioni/" + os.path.basename(text_problems)}
    check("rapporto: registro, profilo, impostazioni, stato, rilevamento, BepInEx/XUnity, Unity, crash, "
              "manifesto, testi rimasti con il motivo (problemi.json)", expected_list <= names)
    whole = "\n".join(content.values())
    check("rapporto: nessuna chiave DeepL ne' email (nemmeno dentro un registro, anche in maiuscolo)",
              key not in whole and email not in whole.lower() and "<tolto>" in content["registri/" +
                                                                                       os.path.basename(log_file)])
    prefs = json.loads(content["settings.json"])
    check("rapporto: impostazioni senza chiave_deepl ed email, il resto c'e'",
              "chiave_deepl" not in prefs and "email_mymemory" not in prefs and prefs.get("backend") == "deepl")
    check("rapporto: versione e ultimo esito nel riepilogo, crash piu' recente, fine del registro lungo",
              __version__ in content["riepilogo.txt"] and "PARZIALE" in content["riepilogo.txt"]
              and content["unity/crash-Crash_nuovo-Player.log"] == "crash nuovo"
              and "traduzione interrotta" in content["traduttore-locale/servizio-xunity.log"]
              and len(content["traduttore-locale/servizio-xunity.log"]) < report.MAX_BYTES + 200)
    check("rapporto: la cartella dell'utente diventa %USERPROFILE% (anche dentro un JSON)",
              report.clean("log: %s\\AppData" % user, []) == "log: %USERPROFILE%\\AppData"
              and report.clean(json.dumps({"c": user}), []) == '{"c": "%USERPROFILE%"}'
              and user.lower() not in whole.lower())
    check("rapporto anche senza cartella del gioco (solo dati generali)", os.path.isfile(zip2))
    cleaned = report.clean("GET https://api.mymemory.translated.net/get?q=Start&de=%s&x=%s" % (
        email.replace("@", "%40"), email.replace("@", "%40").upper()), [email, key])
    check("rapporto: l'email sparisce anche scritta come in un indirizzo web (%40)",
              "segreto" not in cleaned.lower() and cleaned.count("<tolto>") == 2)


def test_live(mono):
    """Who receives the new lines while playing (Unity): Google, PatchSmith's translator (command of PatchSmith's
    plugin) or nobody (XUnity without a translator that answers)."""
    config = os.path.join(mono, *adapter_unity.CONFIG_XUNITY.split("/"))
    service = os.path.join(mono, *adapter_unity.SERVICE_CONFIG.split("/"))
    plugin = os.path.join(mono, *adapter_unity.SERVICE_PLUGIN.split("/"))
    with open(config, encoding="utf-8-sig") as f:
        original = f.read()
    results = [adapter_unity.live_translator(mono)]
    os.makedirs(os.path.dirname(plugin), exist_ok=True)
    open(plugin, "wb").write(b"finto")
    with open(service, "w", encoding="utf-8") as f:
        f.write('Argomenti=-I "smith.py" servizio-xunity --profilo "prova.json" --porta 51234\n')
    results.append(adapter_unity.live_translator(mono))
    os.remove(plugin)
    with open(config, "w", encoding="utf-8") as f:
        f.write(original.replace("Endpoint=GoogleTranslateV2", "Endpoint=CustomTranslate"))
    results.append(adapter_unity.live_translator(mono))
    with open(config, "w", encoding="utf-8") as f:
        f.write(original)
    os.remove(service)
    results.append(adapter_unity.live_translator(os.path.dirname(mono)))  # folder without XUnity
    e = outcome.Outcome("OK", engine="unity", written=True, translated_items=5, live="locale")
    check("frasi nuove mentre giochi: Google, traduttore di PatchSmith, nessuno (e None senza XUnity)%s" % (
        "" if results == ["google", "servizio", "nessuno", None] else ": %s" % results),
              results == ["google", "servizio", "nessuno", None]
              and "New phrases while playing: PatchSmith, with the local translator (on your PC)."
              in outcome.window(e)["testo"])
    e = outcome.Outcome("OK", engine="unity", written=True, translated_items=5, live="nessuno",
                        actions=["installa_locale"], extra={"dal_vivo_nota": "senza_traduttore"})
    f = outcome.window(e, local_ready=False, current="google")
    check("frasi nuove senza traduttore: la finestra lo spiega e propone 'Installa il traduttore locale'",
              "New phrases while playing: none" in f["testo"]
              and outcome.LIVE_NOTES["senza_traduttore"] in f["testo"]
              and ("Install the local translator", "installa_locale") in f["pulsanti"])


def test_command_line(base):
    """The messages talk about the interface's buttons: only the command line writes the command to fix it
    (under the error, and in 'smith servizi' for a disabled translator)."""
    from patchsmith import cli
    real = automatic.analyze_and_translate

    def without_local(*_a, **_k):
        raise backend.BackendError(backend.NOT_INSTALLED, "TRADUTTORE_ASSENTE", command=backend.INSTALL_COMMAND)

    automatic.analyze_and_translate = without_local
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = cli.main(["auto", base, "--backend", "finto"])
    finally:
        automatic.analyze_and_translate = real
    text = output.getvalue()
    check("riga di comando: l'errore e sotto il comando per rimediare (codice di uscita 1)",
              code == 1 and "ERROR: The local translator is not installed" in text
              and "From the command line: python -I smith.py local --install" in text)
    state = project.data_folder("services.json")
    now = time.time()
    with open(state, "w", encoding="utf-8") as f:
        json.dump({"versione": 1, "google": {"richieste": [], "blocco": {
            "dal": now - 60, "fino": now + 86400, "livello": 3, "tipo": "a_freddo", "http": 429,
            "dettaglio": None, "disattivato": True}}}, f)
    output = io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            code = cli.main(["servizi"])
    finally:
        os.remove(state)
    text = output.getvalue()
    check("'smith servizi': per Google disattivato il comando per riattivarlo (e il pulsante nel messaggio)",
              code == 0 and "python -I smith.py services --reset google" in text and "Reactivate" in text
              and "--reset deepl" not in text)


def test_corrupted_profiles(base):
    """A file in profiles/ that is not a game profile (e.g. a list) is skipped like an unreadable one: it does not
    block the search for the game profile (nor the interface, which does it before every operation)."""
    broken = os.path.join(automatic.profiles_folder(), "rotto.json")
    with open(broken, "w", encoding="utf-8") as f:
        json.dump(["non", "un", "profilo"], f)
    try:
        names = [os.path.basename(p) for p, _ in automatic.list_profiles()]
        located = automatic.find_profile(os.path.join(base, "web", "Lantern Keeper"))
        check("profilo rovinato (una lista): saltato, gli altri si trovano ancora",
                  "rotto.json" not in names and names and located)
    except Exception as e:  # noqa: BLE001
        check("profilo rovinato (una lista): saltato (%s: %s)" % (type(e).__name__, e), False)
    finally:
        os.remove(broken)


def test_ui_texts():
    """No text that reaches the interface refers to the command line: 'smith.py' only in cli.py, in the path of the
    service for XUnity and in the commands reserved for the command line (attribute 'comando')."""
    folder = os.path.join(os.path.dirname(HERE), "patchsmith")
    found = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py") or name == "cli.py":
            continue
        with open(os.path.join(folder, name), "rb") as f:
            source = f.read()
        lines = source.decode("utf-8").splitlines()
        for t in tokenize.tokenize(io.BytesIO(source).readline):
            if t.type != tokenize.STRING or ("smith.py" not in t.string and "python -I" not in t.string):
                continue
            line = lines[t.start[0] - 1]
            if t.string.strip("\"'") == "smith.py" or "command" in line.lower():
                continue
            if t.start[0] > 1 and lines[t.start[0] - 2].strip().startswith(("def ", "class ")):
                continue  # docstring
            found.append("%s:%d" % (name, t.start[0]))
    check("testi per l'interfaccia senza 'smith.py' (fuori da cli.py)%s" % (
        ": " + ", ".join(found) if found else ""), not found)
    from patchsmith import limiter
    lim = limiter.Limiter("google", log=None, path=os.path.join(project.data_folder(), "stato-prova.json"),
                                current_time=lambda: 1_000_000.0)
    for _ in range(4):
        message = lim.blocked(429)
    check("limitatore: messaggi di blocco e disattivazione con il pulsante 'Riattiva', senza comandi",
              "Reactivate" in message and "Reactivate" in lim.pause() and without_smith(message + lim.pause()))


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base, exist_ok=True)
    # PatchSmith's data, the user folder and %TEMP% in the test folder (even when run on its own)
    project.DATA_ROOT = os.path.join(base, "dati")
    automatic.PROFILES_FOLDER = None
    for d in ("profiles", "memory"):
        os.makedirs(os.path.join(project.DATA_ROOT, d))
    for env_var, rel in (("USERPROFILE", "utente"), ("TEMP", "utente/Temp"), ("TMP", "utente/Temp"),
                           ("LOCALAPPDATA", "utente/AppData/Local"), ("APPDATA", "utente/AppData/Roaming")):
        os.environ[env_var] = os.path.join(base, *rel.split("/"))
        os.makedirs(os.environ[env_var], exist_ok=True)
    for v in ("PATCHSMITH_DEEPL", "PATCHSMITH_EMAIL"):
        os.environ.pop(v, None)
    report.desktop_folder = lambda: os.path.join(base, "Desktop")  # never the real Desktop
    game = test_web(base)
    test_google_blocked(base, game)
    test_gamemaker_open(base)
    mono = test_unity_games(base)
    test_exceptions()
    test_windows()
    test_report(base, mono)
    test_live(mono)
    test_corrupted_profiles(base)
    test_command_line(os.path.join(base, "web", "Lantern Keeper"))
    test_ui_texts()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
