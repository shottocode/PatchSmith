"""Test of the local translator (Ollama + TranslateGemma) without network and without a graphics card.

Usage: python -I test/test_local.py <empty_temp_folder>
- level 1: Local backend with an in-memory simulated server (scripted answers);
- level 2: ollama.Server that starts and closes a simulated Ollama (test/fake_ollama.py) on 127.0.0.1.
"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import backend, interruption, ollama, project  # noqa: E402
from patchsmith.project import Project  # noqa: E402

OUTCOMES = []
HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


class FakeServer:
    """Instead of ollama.Server: answers /api/chat with the function 'translate(text, options)'."""

    def __init__(self, translate, error=None):
        self.translate = translate
        self.error = error
        self.requests = []
        self.closed = False
        self.unloaded = False  # model removed from the graphics card (keep_alive 0)
        self.active = False

    def launch(self):
        self.active = True

    def call(self, path, data=None, timeout=180):
        if self.error:
            raise self.error
        if path == "/api/chat":
            text = data["messages"][0]["content"].split("\n\n\n", 1)[1]
            self.requests.append((text, data))
            response = self.translate(text, data["options"])
            if isinstance(response, BaseException):
                raise response
            if isinstance(response, tuple):
                content, end = response
            else:
                content, end = response, "stop"
            return {"message": {"content": content}, "done_reason": end, "eval_count": 20,
                    "eval_duration": 500_000_000, "prompt_eval_count": 60}
        if path == "/api/generate" and data.get("keep_alive") == 0:
            self.unloaded = True
        if path == "/api/ps":
            return {"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": 100}]}
        return {}

    def close(self):
        self.closed = True


def http_error(code, body):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://127.0.0.1", code, "x", {}, io.BytesIO(body))


def local(translate, error=None):
    server = FakeServer(translate, error)
    b = backend.Local("4b", log=silent, server=server)
    return b, server


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    os.makedirs(os.path.join(project.PROJECT_FOLDER, "memory"))

    # --- 1. normal translation: one request per line, TranslateGemma's official prompt
    b, server = local(lambda t, o: "IT " + t)
    outcome = b.translate_lines(["Start game", "Quit"], "en", "it")
    text, data = server.requests[0]
    check("una richiesta per riga, prompt ufficiale (English (en) to Italian (it)), temperatura 0",
              outcome == ["IT Start game", "IT Quit"] and len(server.requests) == 2
              and "English (en) to Italian (it) translator" in data["messages"][0]["content"]
              and data["options"]["temperature"] == 0 and data["options"]["num_ctx"] == 2048
              and data["stream"] is False)

    # --- 2. checks and second attempt
    cases = {
        "testo di contorno": (lambda t, o: "Translation: Inizia" if o["temperature"] == 0 else "Inizia", "Inizia"),
        "scrittura sbagliata": (lambda t, o: "开始游戏", ""),
        "rifiuto del modello": (lambda t, o: "I cannot translate this.", ""),
        "ripetizioni": (lambda t, o: "gioco gioco gioco gioco gioco", ""),
        "a capo aggiunto": (lambda t, o: "Inizia\nla partita", "Inizia la partita"),
        "non tradotta": (lambda t, o: t, ""),
    }
    for name, (translate, expected) in cases.items():
        b, server = local(translate)
        original = {"numeri diversi": "Deal 50 damage", "non tradotta": "Start the game with your friends"}.get(
            name, "Start the game")
        check("controllo '%s': %s" % (name, "risposta usata (corretta o ritentata)" if expected
                                           else "resta in originale"),
                  b.translate_lines([original], "en", "it") == [expected])
    responses = iter(["Infliggi 5 danni", "Infliggi 50 danni"])
    b, server = local(lambda t, o: next(responses))
    numbers = b.translate_lines(["Deal 50 damage"], "en", "it")
    check("numeri cambiati dal modello: secondo tentativo con la correzione sui numeri nel prompt",
              numbers == ["Infliggi 50 danni"]
              and backend.NUMBERS_FIX in server.requests[1][1]["messages"][0]["content"]
              and backend.NUMBERS_FIX not in server.requests[0][1]["messages"][0]["content"])
    check("numeri scritti in lettere: '1 or more' -> 'uno o piu'' accettato; 'un', 'sei' (ambigui) no",
              not backend.translation_problems("You have 1 or more mods", "Hai uno o più mod", "it")
              and backend.translation_problems("lose 1 max H", "perdi un po' di salute", "it")
              and backend.translation_problems("gain 6 slots", "sei più forte", "it"))
    b, server = local(lambda t, o: ("Inizia la parti", "length") if o["num_predict"] < 100 else "Inizia la partita")
    outcome = b.translate_lines(["Start the game"], "en", "it")
    check("risposta troncata (done_reason 'length'): ritenta con piu' spazio",
              outcome == ["Inizia la partita"] and server.requests[-1][1]["options"]["num_predict"] >= 100)
    b, server = local(lambda t, o: '"Inizia"')
    check("virgolette aggiunte dal modello tolte", b.translate_lines(["Start"], "en", "it") == ["Inizia"])
    cleanup_cases = [("building", "edificio; costruzione; costruire.", "edificio"),
               ("spellbind", "incantare, affascinare, rapire.", "incantare"),
               ("swords and stuff", "spade, scudi, ecc.", "spade, scudi, ecc."),
               ("antlers", "corna (di cervo, alce, ecc.)", "corna"),
               ("golden boots", "scarpe dorate.", "scarpe dorate"),
               ("awarded only to those", "conferito esclusivamente a coloro che...", "conferito esclusivamente a coloro che"),
               ("why?!", "Perché?!", "perché?!"), ("uh oh!", "Oh, no!", "oh, no!"), ("Start", "Inizia.", "Inizia"),
               ("loading...", "caricamento...", "caricamento..."), ("U.S. army", "esercito U.S.A.", "esercito U.S.A."),
               ("Press (A) to jump.", "Premi (A) per saltare.", "Premi (A) per saltare.")]
    b, server = local(lambda t, o: "x")
    wrong = [(o, b._clean(t, o)) for o, t, expected in cleanup_cases if b._clean(t, o) != expected]
    check("pulizia di quello che il modello aggiunge (alternative, note, punto, puntini, maiuscola) senza "
              "toccare il resto%s" % (": %s" % wrong if wrong else ""), not wrong
              and b._clean("Schwert", "sword", "de") == "Schwert")

    # --- 2b. no false rejections: ordinary sentences, Asian languages, numbers written in the local style, quotes
    good_ones = [("Here, take this.", "Ecco, prendi questo.", "it"), ("Sorry, the shop is closed.",
             "Mi dispiace, il negozio è chiuso.", "it"), ("Can't carry more!", "Non posso portarne altri!", "it"),
             ("Notes", "Note", "it"), ("Start the game with your friends", "与朋友一起开始游戏", "zh"),
             ("暴击率", "Probabilità di colpo critico", "it"), ("Costs 1000 gold", "Costa 1.000 monete d'oro", "it"),
             ("Costs 1,000 gold", "Costa 1000 monete", "it"), ("Reach level 10", "レベル１０に到達", "ja"),
             ("Level 1.5", "Livello 1,5", "it")]
    rejected = [(o, backend.translation_problems(o, t, d)) for o, t, d in good_ones
                if backend.translation_problems(o, t, d)]
    check("controlli: nessun falso scarto (Ecco/Mi dispiace, cinese e giapponese, 1.000, cifre larghe)%s"
              % (": %s" % rejected if rejected else ""), not rejected)
    b, server = local(lambda t, o: "x")
    kept = [("\"Stay a while.\"", "«Resta un po'.»", "«Resta un po'.»"), ("你好朋友", "Ciao, amico", "Ciao, amico"),
              ("攻撃力（ATK）", "Attacco (ATK)", "Attacco (ATK)"), ("「こんにちは。」", "\"Ciao.\"", "\"Ciao.\"")]
    altered = [(o, b._clean(t, o)) for o, t, expected in kept if b._clean(t, o) != expected]
    check("pulizia: virgolette di altro stile e testi cinesi/giapponesi lasciati intatti%s"
              % (": %s" % altered if altered else ""), not altered)

    # --- 2c. text left unchanged: retried once; if it stays unchanged it is a name and it is kept
    b, server = local(lambda t, o: t if o["temperature"] == 0 else "Apri la porta")
    first = b.translate_lines(["Open the door"], "en", "it")
    b, server = local(lambda t, o: t)
    name = b.translate_lines(["Katherine"], "en", "it")
    check("eco dell'originale: secondo tentativo; uguale due volte (un nome) si tiene",
              first == ["Apri la porta"] and name == ["Katherine"] and len(server.requests) == 2)

    # --- 2d. a single line too slow (time runs out) but server alive: only that one is left untranslated
    b, server = local(lambda t, o: TimeoutError("timed out") if t == "Long dialogue" else "IT " + t)
    lines = b.translate_lines(["Alpha", "Long dialogue", "Omega"], "en", "it")
    check("riga che va oltre il tempo massimo: resta in originale, le altre si traducono",
              lines == ["IT Alpha", "", "IT Omega"])
    b, server = local(lambda t, o: "x", ConnectionResetError())
    try:
        b.translate_lines(["Alpha"], "en", "it")
        went_down = False
    except backend.BackendError:
        went_down = True
    check("server caduto: errore (la traduzione si ferma e riprende al prossimo avvio)", went_down)

    # --- 3. lost placeholders: the last attempt goes back to PatchSmith, which re-translates the pieces between codes
    profile = os.path.join(base, "profilo.json")
    json.dump({"name": "test", "game_folder": base, "source_language": "en", "target_language": "it",
               "file": []}, open(profile, "w"))
    p = Project(profile, silent)

    def loses(text, options):
        return text.replace("[[0]]", "").replace("Hello", "Ciao").replace("welcome", "benvenuto").strip()
    b, server = local(loses)
    p.translate_texts(["Hello {name}, welcome"], b, "en", "it")
    check("segnaposto perso dal modello: la traduzione finale ha comunque {name}",
              p.memory.get("Hello {name}, welcome") == "Ciao {name}, benvenuto" and server.unloaded)

    # --- 3a. context per line: key (without number) and official translations, line by line if the lines match
    b, server = local(lambda t, o: "meteo" if t == "weather" else "IT " + t)
    p.memory = {}
    contexts = {"weather": {"key": "configWeather", "references": {"fr": "météo", "es": "clima"}},
                "first line\nsecond line": {"key": "dialogue", "references": {"fr": "une seule ligne"}},
                "a\nb": {"key": "x", "references": {"fr": "riga a\nriga b"}}}
    p.translate_texts(list(contexts), b, "en", "it", contexts)
    prompt = {t: d["messages"][0]["content"] for t, d in server.requests}
    check("contesto nel prompt: chiave e traduzioni ufficiali; righe diverse -> nessun riferimento",
              'identifier "configWeather"' in prompt["weather"] and "French: météo; Spanish: clima" in prompt["weather"]
              and "French" not in prompt["first line"] and 'identifier "dialogue"' in prompt["first line"]
              and "French: riga a" in prompt["a"] and "French: riga b" in prompt["b"]
              and p.memory["weather"] == "meteo")
    b, server = local(lambda t, o: "configWeather")
    check("il modello scrive la chiave invece di tradurre -> scartato",
              b.translate_lines(["weather"], "en", "it", [{"key": "configWeather"}]) == [""])
    seen_prompts = []

    class ServerPrompt(FakeServer):
        def call(self, path, data=None, timeout=180):
            if path == "/api/chat":
                seen_prompts.append(data["messages"][0]["content"])
            return FakeServer.call(self, path, data, timeout)

    def second_prompt(responses):
        server = ServerPrompt(lambda t, o: responses[min(len(seen_prompts), len(responses)) - 1])
        seen_prompts.clear()
        return backend.Local("4b", log=silent, server=server)
    b = second_prompt(["saque", "saccheggio"])
    copy_dir = b.translate_lines(["looting"], "en", "it", [{"key": "skillName", "references": {"pt": "saque"}}])
    check("traduzione ufficiale copiata (es. dal portoghese): scartata, nuovo tentativo senza riferimenti",
              copy_dir == ["saccheggio"] and "saque" in seen_prompts[0] and "saque" not in seen_prompts[1])
    b = second_prompt(["configWeather", "meteo"])
    key = b.translate_lines(["weather"], "en", "it", [{"key": "configWeather"}])
    check("chiave copiata: nuovo tentativo senza la chiave nel prompt",
              key == ["meteo"] and "configWeather" in seen_prompts[0] and "configWeather" not in seen_prompts[1])
    b = second_prompt(["le monete valgono il doppio!", "coins are worth 200% more!", "valgono il triplo!"])
    check("testo rimasto uguale solo all'ultimo tentativo (dopo uno scarto): resta da rifare, non e' una traduzione",
              b.translate_lines(["coins are worth 200% more!"], "en", "it") == [""])
    b = second_prompt(["[[0]]Compagno Sigillo", "[[0]]Compagno[[1]] Sigillo"])
    markers = b.translate_lines(["[[0]]Companion[[1]] Seal"], "en", "it")
    check("codici persi dal modello: secondo tentativo con l'istruzione sui codici",
              markers == ["[[0]]Compagno[[1]] Sigillo"] and backend.PLACEHOLDER_FIX in seen_prompts[1])
    b, server = local(lambda t, o: "[[0]] Attacchi [[1]] ti danno [[2]\u3015evitare [[3]\u3015 danni")
    check("codici rovinati dal modello ('[[2]\u3015', '[0]'): rimessi; quelli scritti nell'originale no",
              b.translate_lines(["[[0]]Backstabs[[1]] grant [[2]]Evade[[3]] damage"], "en", "it")
              == ["[[0]] Attacchi [[1]] ti danno [[2]]evitare [[3]] danni"]
              and b._clean("Premi [1] per saltare [[0]]", "Press [1] to jump [[0]]") == "Premi [1] per saltare [[0]]")
    check("numeri: 'una' per 1 se l'originale non ha un articolo che la spieghi; cifra che traduce 'one'",
              not backend.translation_problems("appear 1 rarity higher", "appaiono con una rarità superiore", "it")
              and backend.translation_problems("deals 1 damage to a target", "infligge danni a una creatura", "it")
              and not backend.translation_problems("restore one [[7]] on hit", "ripristina 1 [[7]] al colpo", "it"))
    check("lettere che non sono dell'italiano (es. 'crânio' dal portoghese): scartata",
              "letters from another language" in backend.translation_problems("great skull", "crânio grande", "it")
              and not backend.translation_problems("so is it", "così è", "it"))

    # --- 3b. Stop during the fallback: the good translations of the same group stay in the translation memory
    def loses_and_stops(text, options):
        if "[[0]]" not in text and text in ("Hello", ", welcome"):  # we are in the fallback
            interruption.request_stop()
        return loses(text, options) if "[[0]]" in text else "IT " + text
    b, server = local(loses_and_stops)
    p.memory = {}
    if os.path.exists(project.shared_path("en", "it")):  # short texts already seen in the earlier cases
        os.remove(project.shared_path("en", "it"))
    try:
        p.translate_texts(["Start game", "Quit", "Hello {name}, welcome"], b, "en", "it")
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    check("Ferma durante il ripiego dei segnaposto: le altre traduzioni del gruppo sono salvate",
              stopped and p.memory.get("Start game") == "IT Start game" and p.memory.get("Quit") == "IT Quit")

    # --- 3c. lines in parallel: same order of the lines; with Stop the lines already done stay
    b, server = local(lambda t, o: "IT " + t)
    b.parallel = 3
    lines = ["riga %d" % i for i in range(20)]
    check("righe tradotte 3 alla volta: risultato nello stesso ordine", b.translate_lines(lines, "en", "it")
              == ["IT " + r for r in lines])

    def stop_at_fifth(text, options):
        if text == "riga 5":
            interruption.request_stop()
        return "IT " + text
    b, server = local(stop_at_fifth)
    b.parallel = 3
    try:
        b.translate_lines(lines, "en", "it")
        partials = None
    except interruption.Interrupted as e:
        partials = e.partials
    interruption.reset()
    check("Ferma con le righe in parallelo: si ferma, le righe gia' tradotte restano al loro posto",
              partials is not None and len(partials) == 20 and partials[0] == "IT riga 0" and partials[-1] == ""
              and all(p in ("", "IT " + r) for p, r in zip(partials, lines)))

    # --- 3d. shared translation memory: the short texts translated for one game serve the others; so do hand fixes
    def game_profile(name):
        path = os.path.join(base, name + ".json")
        json.dump({"name": name, "game_folder": base, "source_language": "en", "target_language": "it",
                   "file": []}, open(path, "w"))
        return Project(path, silent)
    first = game_profile("gioco-uno")
    b, server = local(lambda t, o: "IT " + t)
    first.translate_texts(["options", "back", "a long sentence that only this game has"], b, "en", "it")
    second = game_profile("gioco-due")
    b, server = local(lambda t, o: "IT " + t)
    second.translate_texts(["options", "back", "new game"], b, "en", "it")
    check("memoria comune: i testi brevi gia' tradotti per un altro gioco non si ritraducono (le frasi si')",
              [t for t, _ in server.requests] == ["new game"] and second.memory["options"] == "IT options")
    first.memory["options"] = "opzioni"  # as with 'Fix translations'
    first._save_memory()
    first = game_profile("gioco-uno")
    b, server = local(lambda t, o: "IT " + t)
    first.translate_texts(["options"], b, "en", "it")
    third = game_profile("gioco-tre")
    b, server = local(lambda t, o: "IT " + t)
    third.translate_texts(["options"], b, "en", "it")
    check("correzione a mano di un testo breve: passa nella memoria comune e la usano i giochi successivi",
              third.memory["options"] == "opzioni" and not server.requests)

    # --- 4. understandable errors
    for name, error, word in (("modello mancante (404)", http_error(404, b'{"error":"model not found"}'),
                                  "is not downloaded"),
                                 ("memoria insufficiente", http_error(500, b'{"error":"requires more system memory"}'),
                                  "Insufficient memory")):
        b, server = local(lambda t, o: "x", error)
        try:
            b.translate_lines(["Start"], "en", "it")
            message = ""
        except backend.BackendError as e:
            message = str(e)
        check("errore '%s' spiegato" % name, word in message)

    # --- 5. Stop halfway: lines already translated saved, server closed
    tally = [0]

    def stop_at_third(text, options):
        tally[0] += 1
        if tally[0] == 3:
            interruption.request_stop()
        return "IT " + text
    b, server = local(stop_at_third)
    p.memory = {}
    try:
        p.translate_texts(["uno", "due", "tre", "quattro", "cinque"], b, "en", "it")
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    check("Ferma a meta': si ferma, le righe gia' tradotte restano, la scheda video viene liberata",
              stopped and set(p.memory) == {"uno", "due", "tre"} and server.unloaded)

    # --- 5b. server started by the translator itself: close() shuts it down
    b, server = local(lambda t, o: "IT " + t)
    b._owned = True
    b.translate_lines(["Start"], "en", "it")
    b.close()
    check("chiudi(): con il server avviato da PatchSmith, modello scaricato e server spento",
              server.unloaded and server.closed and b._server is None)

    # --- 6. estimate from the measured speed
    b, server = local(lambda t, o: "IT " + t)
    before = b.estimate([40] * 100)  # pace of the trials (0.25 s per text for the 4B) + 30 s of startup
    b.translate_lines(["Start"] * 12, "en", "it")  # fake server: instant
    check("stima: prima delle prove circa un minuto per 100 testi col 4B, poi il ritmo misurato "
              "su questo PC", 45 <= before <= 65 and b.estimate([40] * 100) < 5)
    check("prompt: formato ufficiale di TranslateGemma con il contesto 'videogioco, del tu'",
              "video game" in server.requests[0][1]["messages"][0]["content"]
              and server.requests[0][1]["messages"][0]["content"].endswith("\n\n\nStart"))

    # --- 7. simulated Ollama: startup, environment, closing, server that does not start
    os.environ["OLLAMA_ORIGINS"] = r"C:\non\usare"  # the user's configuration: it must not reach the server
    environment = os.path.join(base, "ambiente.json")
    os.environ["PATCHSMITH_FINTO_AMBIENTE"] = environment
    command = [sys.executable, "-I", os.path.join(HERE, "fake_ollama.py")]
    server = ollama.Server(silent, command=command, startup_wait=20)
    server.launch()
    version = server.call("/api/version")
    translated = backend.Local("4b", log=silent, server=server).translate_lines(["Start game"], "en", "it")
    env = json.load(open(environment, encoding="utf-8"))
    pid = server.process.pid
    server.close()
    import subprocess
    alive = str(pid).encode() in subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True).stdout
    check("Ollama simulato: avvio, traduzione, chiusura del processo",
              version.get("version") == "0.40.4" and translated == ["IT: Start game"] and not alive)
    check("ambiente del server: modelli nella cartella di PatchSmith, porta propria, niente variabili dell'utente",
              env.get("OLLAMA_MODELS") == ollama.models_folder() and env.get("OLLAMA_NO_CLOUD") == "1"
              and env.get("OLLAMA_HOST", "").startswith("127.0.0.1:114") and "OLLAMA_ORIGINS" not in env
              and r"C:\non\usare" not in env.values())
    del os.environ["OLLAMA_ORIGINS"]
    os.environ["PATCHSMITH_FINTO_MUORI"] = "1"
    try:
        ollama.Server(silent, command=command, startup_wait=20).launch()
        message = ""
    except ollama.LocalError as e:
        message = str(e)
    del os.environ["PATCHSMITH_FINTO_MUORI"]
    check("server che non parte: errore con le ultime righe del suo registro",
              "didn't start" in message and "errore simulato" in message)

    # Stop while the server is starting: the process just launched does not stay running
    import threading
    started_pids = []
    real_bind = ollama._bind_to_job
    ollama._bind_to_job = lambda pid: started_pids.append(pid) or real_bind(pid)
    os.environ["PATCHSMITH_FINTO_LENTO"] = "8"
    threading.Timer(1.0, interruption.request_stop).start()
    try:
        with ollama.Server(silent, command=command, startup_wait=20):
            pass
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    del os.environ["PATCHSMITH_FINTO_LENTO"]
    ollama._bind_to_job = real_bind
    alive = bool(started_pids) and str(started_pids[0]).encode() in subprocess.run(
        ["tasklist", "/FI", "PID eq %d" % started_pids[0]], capture_output=True).stdout
    check("Ferma durante l'avvio del server: nessun server lasciato acceso", stopped and started_pids and not alive)

    # a port already used by another program (even listening on 0.0.0.0) is not chosen
    import socket
    occupier = socket.socket()
    occupier.bind(("0.0.0.0", 0))
    occupier.listen(1)
    other_port = occupier.getsockname()[1]
    busy = ollama._port_busy(other_port)
    occupier.close()
    check("porta occupata da un altro programma riconosciuta", busy and not ollama._port_busy(other_port))

    # --- 8. installation: zip with dangerous paths rejected, status without installation
    dangerous = os.path.join(base, "pericoloso.zip")
    with zipfile.ZipFile(dangerous, "w") as z:
        z.writestr("../fuori.txt", "x")
    try:
        ollama._extract(dangerous, os.path.join(base, "estratto"))
        was_refused = False
    except ollama.LocalError:
        was_refused = True
    check("installazione: zip con percorsi fuori cartella rifiutato, nulla estratto",
              was_refused and not os.path.exists(os.path.join(base, "fuori.txt"))
              and not os.path.exists(os.path.join(base, "estratto")))
    # damaged installation (ollama.exe removed, e.g. by the antivirus): reinstalling replaces it
    good = os.path.join(base, "buono.zip")
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("ollama.exe", "nuovo")
        z.writestr("lib/ollama/x.dll", "x")
    corrupted = os.path.join(base, "version")
    os.makedirs(os.path.join(corrupted, "lib", "ollama"))
    open(os.path.join(corrupted, "lib", "ollama", "vecchio.dll"), "w").close()
    ollama._extract(good, corrupted)
    check("installazione rovinata: la cartella viene rifatta da zero",
              open(os.path.join(corrupted, "ollama.exe")).read() == "nuovo"
              and not os.path.exists(os.path.join(corrupted, "lib", "ollama", "vecchio.dll"))
              and not os.path.exists(corrupted + ".tmp"))

    # Ollama download: Stop or a dropped network do not throw away the part already downloaded, it resumes with Range
    content = bytes(range(256)) * 12  # 3072 bytes instead of 1.47 GB
    requests = []

    class Response:
        def __init__(self, data, state, break_after=None):
            self.data, self.status, self.break_at, self.read_items = data, state, break_after, 0

        def read(self, n):
            if self.break_at is not None and self.read_items >= self.break_at:
                raise ConnectionResetError("rete interrotta")
            block = self.data[self.read_items:self.read_items + min(n, 1024)]
            self.read_items += len(block)
            return block

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(req, timeout=None):
        byte_range = req.get_header("Range")
        requests.append(byte_range)
        if byte_range:
            start = int(byte_range.split("=")[1].rstrip("-"))
            return Response(content[start:], 206)
        return Response(content, 200, break_after=1024)
    originals = (ollama.ZIP_SIZE, ollama.urllib.request.urlopen)
    ollama.ZIP_SIZE, ollama.urllib.request.urlopen = len(content), fake_urlopen
    partial = os.path.join(base, "ollama.zip.scaricamento")
    try:
        try:
            ollama._download_zip(partial, silent)
        except ConnectionResetError:
            pass
        half = os.path.getsize(partial)
        ollama._download_zip(partial, silent)
    finally:
        ollama.ZIP_SIZE, ollama.urllib.request.urlopen = originals
    check("scaricamento interrotto: il pezzo resta e si riprende da li' (Range), file finale identico",
              half == 1024 and requests == [None, "bytes=1024-"] and open(partial, "rb").read() == content)
    check("stato senza installazione: 'non installato' e descrizione dei download con fonte e dimensione",
              ollama.state() == "not installed" and any("github.com" in r for r in ollama.download_description(["4b"]))
              and any("Gemma Terms" in r for r in ollama.download_description(["4b"])))
    try:
        backend.Local("4b", log=silent).translate_lines(["Start"], "en", "it")
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("traduttore locale non installato: messaggio con cosa fare", "Install" in message)
    try:
        backend.create_backend("local", silent)
        message, code, command = "", None, ""
    except backend.BackendError as e:
        message, code, command = str(e), e.code, e.command or ""
    check("crea_backend('locale') senza installazione: errore subito (prima di toccare il gioco), il messaggio "
              "parla del pulsante (niente comandi), il comando e' a parte per la riga di comando",
              "Install" in message and "smith.py" not in message and code == "TRANSLATOR_MISSING"
              and "smith.py local --install" in command)
    # installed, but the chosen model (12B) is not downloaded: the one present (4B) is used
    os.makedirs(os.path.join(ollama.folder(), ollama.VERSION))
    open(ollama.program(), "w").close()
    manifest = os.path.join(ollama.models_folder(), "manifests", "registry.ollama.ai", "library", "translategemma")
    os.makedirs(manifest)
    open(os.path.join(manifest, ollama.MODELS["4b"]["name"].split(":")[1]), "w").close()
    json.dump({"local_model": "12b"}, open(os.path.join(project.PROJECT_FOLDER, "settings.json"), "w"))
    lines = []
    chosen = backend.create_backend("local", lines.append)
    check("modello scelto non scaricato: si usa quello scaricato, e lo si dice",
              chosen.key == "4b" and any("is not downloaded" in r for r in lines))

    test_recovery(base)
    test_memory(base)
    test_apply(base)
    test_limited_redos(base)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


def profile_of(base, name, **other):
    """A test game profile (game in the base folder, English -> Italian), with the given extra fields."""
    path = os.path.join(base, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict({"name": name, "game_folder": base, "source_language": "en", "target_language": "it",
                        "file": []}, **other), f)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


class ServerWithPrompt(FakeServer):
    """Like FakeServer, but remembers the prompt and the model of each request (the answer can depend on them)."""

    def __init__(self, translate):
        FakeServer.__init__(self, lambda t, o: translate(t, o, self.prompt, self.model))
        self.prompt, self.model, self.models = "", None, []

    def call(self, path, data=None, timeout=180):
        if path == "/api/chat":
            self.prompt, self.model = data["messages"][0]["content"], data["model"]
            self.models.append(data["model"])
        return FakeServer.call(self, path, data, timeout)


def test_recovery(base):
    """Texts rejected by the checks: new rounds right away (separate sentences, new seeds, the other model),
    problemi.json and different seeds at each run (before, the same question was repeated with 42, 7, 11 and the
    answer was the same)."""
    from patchsmith import auto as automatic, outcome

    # 9a. the translator gets it wrong with the seeds of the first round (42, 7, 11) and answers well with the others
    seen = []

    def fixed_seeds(text, options):
        seen.append(options["seed"])
        return text if options["seed"] in (42, 7, 11) else "IT " + text
    p = Project(profile_of(base, "recupero"), silent)
    b, server = local(fixed_seeds)
    failed = p.translate_texts(["Open the door to the castle"], b, "en", "it")
    check("scartato con i semi del primo giro (42, 7, 11): tradotto nello stesso avvio, con un seme nuovo",
              failed == 0 and p.memory.get("Open the door to the castle") == "IT Open the door to the castle"
              and seen[:3] == [42, 7, 11] and len(seen) == 4 and seen[3] not in (42, 7, 11) and not p.problems())

    # 9b. a text always rejected: problemi.json, not in the translation memory; the second run uses all new seeds
    text = "Close the window of the house"
    p = Project(profile_of(base, "sempre-scartato"), silent)
    b, server = local(lambda t, o: t)
    failed = p.translate_texts([text], b, "en", "it")
    first_seeds = [d["options"]["seed"] for _, d in server.requests]
    problems = p.problems()
    check("testo sempre scartato: in problemi.json con il motivo e i passaggi fatti, non in memoria",
              failed == 1 and text not in p.memory and os.path.exists(p.problems_path)
              and problems.get(text, {}).get("where") == "translation" and "not translated" in problems[text]["reason"]
              and problems[text]["attempts"] == 2 and p.last_failed == [text])
    e = outcome.Outcome("PARTIAL", engine="web", written=True, translated_items=3, remaining=1,
                        remaining_examples=[text], extra=automatic._extra_attempts(p))
    log_file = []
    automatic._after_attempts({"outcome": e}, log_file.append)
    window = outcome.window(e)["text"]
    check("messaggio finale: niente 'rilancia'; dice cosa ha gia' provato da solo e nomina 'Correggi traduzioni'",
              "Fix translations" in " ".join(log_file) and "retried automatically" in window
              and "Show remaining" in window and "relaunch" not in (" ".join(log_file) + window).lower())
    p = Project(profile_of(base, "sempre-scartato"), silent)
    b, server = local(lambda t, o: t)
    p.translate_texts([text], b, "en", "it")
    second_seeds = [d["options"]["seed"] for _, d in server.requests]
    check("secondo avvio: semi tutti diversi da quelli del primo (registro delle richieste), passaggi annotati",
              len(second_seeds) == 6 and not set(first_seeds) & set(second_seeds)
              and p.problems()[text]["attempts"] == 4)
    check("semi: stessi per lo stesso testo e tentativo (anche in un altro avvio), mai 42, 7, 11",
              backend.seed_for(text, 3) == backend.seed_for(text, 3) != backend.seed_for(text, 4)
              and all(backend.seed_for("x%d" % i, i) >= 100 for i in range(200)))

    # 9c. the same with the real simulated Ollama (HTTP), which gets it wrong with seeds 42, 7, 11 and logs the requests
    requests_log = os.path.join(base, "richieste-ollama.jsonl")
    command = [sys.executable, "-I", os.path.join(HERE, "fake_ollama.py")]
    os.environ["PATCHSMITH_FINTO_SEMI_SBAGLIATI"] = "42,7,11"
    os.environ["PATCHSMITH_FINTO_RICHIESTE"] = requests_log
    try:
        server = ollama.Server(silent, command=command, startup_wait=20)
        server.launch()
        try:
            p = Project(profile_of(base, "ollama-finto"), silent)
            p.translate_texts(["Find the key in the old house"], backend.Local("4b", log=silent, server=server),
                            "en", "it")
        finally:
            server.close()
    finally:
        del os.environ["PATCHSMITH_FINTO_SEMI_SBAGLIATI"], os.environ["PATCHSMITH_FINTO_RICHIESTE"]
    with open(requests_log, encoding="utf-8") as f:
        seeds = [json.loads(r)["seed"] for r in f]
    check("Ollama simulato che sbaglia con 42, 7, 11: tradotto nello stesso avvio con un seme nuovo (dal suo "
              "registro delle richieste)",
              p.memory.get("Find the key in the old house") == "IT: Find the key in the old house"
              and seeds[:3] == [42, 7, 11] and len(seeds) == 4 and seeds[3] not in (42, 7, 11))

    # 9d. rejected by the Fast model in the new rounds too: Quality (12B) is NOT loaded on its own, even if present
    twelve = ollama.MODELS["12b"]["name"]
    manifest = os.path.join(ollama.models_folder(), "manifests", "registry.ollama.ai", "library",
                             "translategemma", twelve.split(":")[1])
    os.makedirs(os.path.dirname(manifest), exist_ok=True)
    open(manifest, "w").close()
    try:
        server = ServerWithPrompt(lambda t, o, prompt, model: ("IT " + t) if model == twelve else t)
        p = Project(profile_of(base, "altro-modello"), silent)
        p.translate_texts(["Talk to the guard at the gate"], backend.Local("4b", log=silent, server=server),
                        "en", "it")
        check("scartato dal modello Veloce anche nei nuovi giri: resta il modello scelto (niente 12B caricato da "
                  "solo), il testo va tra i problemi",
                  "Talk to the guard at the gate" not in p.memory and twelve not in server.models
                  and "Talk to the guard at the gate" in p.problems())
    finally:
        os.remove(manifest)


def test_memory(base):
    """Game names protected, shared translation memory with the source, saved translations redone if they are wrong."""
    from patchsmith import names

    # 10a. game names: Unity's app.info and the version information of an executable
    game = os.path.join(base, "gioco-nomi")
    os.makedirs(os.path.join(game, "Gioco_Data"))
    open(os.path.join(game, "Gioco_Data", "globalgamemanagers"), "wb").write(b"x")
    with open(os.path.join(game, "Gioco_Data", "app.info"), "w", encoding="utf-8") as f:
        f.write("Cup Dog Games\nRubinite")
    found = names.names_of_game(game)
    info = names.exe_version(sys.executable)
    check("nomi del gioco: azienda e prodotto da app.info; CompanyName dalle informazioni di un eseguibile",
              found[:2] == ["Cup Dog Games", "Rubinite"] and info.get("CompanyName") == "Python Software Foundation")
    check("nomi: anche senza la forma societaria ('LLC'); nomi generici e sigle corte scartati",
              names._variants("Cup Dog Games LLC") == ["Cup Dog Games LLC", "Cup Dog Games"]
              and names._variants("DefaultCompany") == [] and names._variants("GM") == []
              # several forms in a row; generic names also with the final period, and utility programs next to the game
              and names._variants("SQUARE ENIX CO., LTD.") == ["SQUARE ENIX CO., LTD", "SQUARE ENIX"]
              and names._variants("Epic Games, Inc.") == [] and names._variants("Microsoft Game Launch Helper") == [])
    shared = project.shared_path("en", "it")
    old_folder = read_json(shared) if os.path.exists(shared) else {}
    old_folder.update({"Cup Dog Games": "Giochi di cani da coppa", "Deal 5 damage": "Infliggi 50 danni",
                    "Load game": "Carica partita"})  # entries from a previous version, without a source
    write_json(shared, old_folder)
    b, server = local(lambda t, o: t.replace("Cup Dog Games", "Giochi di cani da coppa").replace("Made by",
                                                                                                   "Fatto da"))
    p = Project(profile_of(base, "rubinite", game_folder=game, do_not_translate=found), silent)
    p.translate_texts(["Cup Dog Games", "Made by Cup Dog Games"], b, "en", "it")
    to_ask = [t for t, _ in server.requests]
    check("app.info con 'Cup Dog Games': la traduzione e' uguale all'originale (nessuna richiesta; la memoria "
              "comune vecchia 'Giochi di cani da coppa' non vale), il nome resta anche dentro le frasi",
              p.memory.get("Cup Dog Games") == "Cup Dog Games" and to_ask == ["Made by [[0]]"]
              and p.memory.get("Made by Cup Dog Games") == "Fatto da Cup Dog Games")
    check("nome di una sola parola che nei testi c'e' anche in minuscolo ('Control'): e' una parola, si traduce",
              Project(profile_of(base, "word", do_not_translate=["Control"]), silent).active_names(
                  ["Control", "Hold control to run"]) == ()
              and Project(profile_of(base, "name", do_not_translate=["Rubinite"]), silent).active_names(
                  ["Welcome to Rubinite"]) == ("Rubinite",))

    # 10b. shared translation memory: only the texts translated now go in, with their source; old entries without a
    # source that do not pass the checks are skipped (and replaced)
    path = profile_of(base, "comune-a")
    write_json(project.memory_path({"name": "comune-a", "target_language": "it"}),
                {"Save game": "Salva partita"})  # old entry of the game
    p = Project(path, silent)
    b, server = local(lambda t, o: {"Deal 5 damage": "Infliggi 5 danni", "New quest": "Nuova missione"}.get(
        t, "IT " + t))
    p.translate_texts(["Deal 5 damage", "Load game", "New quest", "Save game"], b, "en", "it")
    after = read_json(shared)
    origin = read_json(project.shared_origin_path("en", "it"))
    check("memoria comune: voce vecchia con i numeri sbagliati saltata e sostituita (con la provenienza: "
              "traduttore, modello, versione); voce vecchia buona usata",
              [t for t, _ in server.requests] == ["Deal 5 damage", "New quest"]
              and p.memory["Load game"] == "Carica partita" and after["Deal 5 damage"] == "Infliggi 5 danni"
              and origin["Deal 5 damage"]["backend"] == "local" and "4b" in origin["Deal 5 damage"]["model"]
              and origin["Deal 5 damage"]["version"])
    check("memoria comune: le voci vecchie della memoria del gioco non ci entrano (solo quelle tradotte adesso)",
              "Save game" not in after and after.get("New quest") == "Nuova missione")
    p.memory["New quest"] = "Missione nuova"  # as with 'Fix translations'
    p._save_memory()
    p = Project(path, silent)
    b, server = local(lambda t, o: "IT " + t)
    p.translate_texts(["New quest"], b, "en", "it")
    after, origin = read_json(shared), read_json(project.shared_origin_path("en", "it"))
    check("correzione a mano: va nella memoria comune (provenienza 'a mano'); in .automatiche.json resta la "
              "traduzione automatica, cosi' la correzione si riconosce sempre",
              after["New quest"] == "Missione nuova" and origin["New quest"].get("by_hand")
              and read_json(p._machine_path())["New quest"] == "Nuova missione"
              and p.memory["New quest"] == "Missione nuova" and not server.requests)

    # 10c. saved translations redone: only the machine translations that do not pass the current checks
    name = "rivalida"
    path = profile_of(base, name)
    saved_count = {"Collect 5 coins": "Raccogli 50 monete", "Find 3 gems": "Trova 30 gemme",
               "Open the chest of the house": "Abre el cofre de la casa y el mapa"}
    write_json(project.memory_path({"name": name, "target_language": "it"}), saved_count)
    write_json(project.memory_path({"name": name, "target_language": "it"})[:-5] + ".automatiche.json",
                dict(saved_count, **{"Find 3 gems": "Trova 3 gemme"}))  # 'Trova 30 gemme' is a fix made by hand
    p = Project(path, silent)
    b, server = local(lambda t, o: {"Collect 5 coins": "Raccogli 5 monete"}.get(t, t))
    p.translate_texts(list(saved_count), b, "en", "it")
    to_ask = {t for t, _ in server.requests}
    problem = p.problems().get("Open the chest of the house", {})
    check("traduzione automatica con i numeri sbagliati: rifatta; correzione a mano: non toccata",
              p.memory["Collect 5 coins"] == "Raccogli 5 monete" and "Find 3 gems" not in to_ask
              and p.memory["Find 3 gems"] == "Trova 30 gemme" and p.redone == 1)
    check("traduzione automatica in un'altra lingua: se rifarla non riesce resta quella di prima, annotata",
              p.memory["Open the chest of the house"] == "Abre el cofre de la casa y el mapa"
              and problem.get("where") == "memory" and "in another language" in problem.get("reason", ""))
    p._problems["Open the chest of the house"] = dict(problem, attempts=project.MAX_ATTEMPTS)
    p._save_problems()
    p = Project(path, silent)
    b, server = local(lambda t, o: t)
    p.translate_texts(list(saved_count), b, "en", "it")
    check("dopo %d passaggi una traduzione sospetta si lascia com'e' (non si ritraduce a ogni avvio)"
              % project.MAX_ATTEMPTS, not server.requests)
    from patchsmith import language
    check("controlli: 'a capo diversi' (righe in piu' o in meno), 'a capo aggiunto' per un testo di una riga",
              "different line breaks" in backend.translation_problems("NEW\n\nText", "NUOVO\n\n\nTesto")
              and "line break added" in backend.translation_problems("Start", "Inizia\nora")
              and not backend.translation_problems("A\nB c", "A\nB d"))
    check("lingua: niente falsi allarmi su 'Qui, qui!' o su titoli inglesi tra virgolette e tag",
              not project.entry_problems("Over here, over here!", "Qui, qui!", "it")
              and not project.entry_problems('Unlock the "Lambs to the Slaughter" Accolade',
                                             'Sblocca l\'insegna "Lambs to the Slaughter"', "it")
              and not project.entry_problems('Use <sprite="Crystal Shard" name="Crystal Shard"> to gain power',
                                             'Usa <sprite="Crystal Shard" name="Crystal Shard"> per avere potere',
                                             "it")
              and language.obvious_language("Open the door of the castle", "it") == "en")


def test_apply(base):
    """Translations that cannot be written to the game: in problemi.json; the machine translations are redone right away
    with the right instruction, those fixed by hand stay."""
    from patchsmith import auto as automatic
    game = os.path.join(base, "gioco-righe")
    os.makedirs(game)
    with open(os.path.join(game, "english.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("0 Start a brand new game now\n1 Quit\n2 Options\n")  # long sentence: not in the shared memory
    name = "lines"
    path = profile_of(base, name, game_folder=game, engine="gamemaker",
                          file=[{"path": "english.txt", "format": "lines"}])
    memory = project.memory_path({"name": name, "target_language": "it"})
    saved_count = {"Start a brand new game now": "Inizia → partita", "Quit": "Esci ★", "Options": "Opzioni"}
    write_json(memory, saved_count)
    write_json(memory[:-5] + ".automatiche.json", dict(saved_count, Quit="Esci"))  # 'Esci ★' fixed by hand
    server = ServerWithPrompt(lambda t, o, prompt, model: "Inizia la partita" if "do not use them: →" in prompt
                             else "Inizia → partita")
    p = Project(path, silent)
    p.translate(backend.Local("4b", log=silent, server=server))
    with open(os.path.join(game, "english.txt"), encoding="utf-8") as f:
        written = f.read()
    problems = p.problems()
    check("traduzione automatica con un carattere che il font non ha: rifatta subito con l'istruzione di non "
              "usarlo, e scritta nel gioco",
              "0 Inizia la partita" in written and p.memory["Start a brand new game now"] == "Inizia la partita"
              and "Start a brand new game now" not in problems and len(server.requests) == 1)
    check("correzione a mano che il gioco non sa mostrare: resta in memoria e in originale nel gioco, annotata "
              "(motivo e caratteri) e detta nell'esito",
              "1 Quit" in written and p.memory["Quit"] == "Esci ★"
              and problems.get("Quit", {}).get("where") == "game"
              and problems["Quit"].get("is_machine") is False and "★" in problems["Quit"]["reason"]
              and automatic._extra_attempts(p).get("hand_fixes_discarded") == 1)
    # 'Reapply' (no network): an unsuitable machine translation is recorded and redone at the next translation
    p.memory["Options"] = "Opzioni ★"
    p._save_memory()
    machine = read_json(p._machine_path())
    machine["Options"] = "Opzioni ★"
    write_json(p._machine_path(), machine)
    p = Project(path, silent)
    p.apply()
    check("riapplica: traduzione automatica non adatta annotata con i caratteri da non usare; si rifara' alla "
              "prossima traduzione",
              p.problems().get("Options", {}).get("is_machine") is True
              and p.problems()["Options"].get("do_not_use") == "★"
              and "Options" in p._to_redo(["Options", "Quit"], "it") and "Quit" not in p._to_redo(["Quit"], "it"))


def lines_game(base, name, lines):
    """A game with a line file ('0 text') and its game profile; returns (folder, game profile, translation memory)."""
    game = os.path.join(base, "gioco-" + name)
    os.makedirs(game)
    with open(os.path.join(game, "english.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("".join("%d %s\n" % (i, r) for i, r in enumerate(lines)))
    path = profile_of(base, name, game_folder=game, file=[{"path": "english.txt", "format": "lines"}])
    return game, path, project.memory_path({"name": name, "target_language": "it"})


def test_limited_redos(base):
    """Saved translations redone: a redone one that is still wrong counts as an attempt (before, it was redone at every
    run, always with seeds 42, 7, 11, and writing the game removed it from problemi.json), the new rounds do not stop
    writing to the game, the shared translation memory does not take translations that fail the checks."""
    suspect = {"Open the chest of the house": "Abre el cofre de la casa y el mapa"}

    # 11a. the local translator gives it back unchanged (it passes its own checks, not the language one): new seeds at
    # every run, recorded in problemi.json, and after MAX_ATTEMPTS it is left alone
    path = profile_of(base, "rifatta-uguale")
    memory = project.memory_path({"name": "rifatta-uguale", "target_language": "it"})
    write_json(memory, suspect)
    write_json(memory[:-5] + ".automatiche.json", suspect)
    seeds = []
    for _ in range(project.MAX_ATTEMPTS + 2):
        p = Project(path, silent)
        b, server = local(lambda t, o: "Abre el cofre de la casa y el mapa")
        p.translate_texts(list(suspect), b, "en", "it")
        seeds.append([d["options"]["seed"] for _, d in server.requests])
    all_ = [s for round_ in seeds for s in round_]
    problem = p.problems().get("Open the chest of the house", {})
    check("traduzione salvata rifatta uguale (non passa i controlli di adesso): semi nuovi a ogni avvio (mai 42, "
              "7, 11), annotata come sospetta, lasciata dopo %d tentativi" % project.MAX_ATTEMPTS,
              all_ and not set(all_) & {42, 7, 11} and len(set(all_)) == len(all_) and not seeds[-1]
              and problem.get("where") == "memory" and problem.get("attempts", 0) >= project.MAX_ATTEMPTS
              and p.redone == 0)

    # 11b. the same with a full 'Analyze and translate' (it writes to the game): the note in problemi.json stays, the
    # attempts grow and after MAX_ATTEMPTS nothing more is requested
    game, path, memory = lines_game(base, "rifatta-gioco", ["Open the chest of the house", "Quit"])
    write_json(memory, dict(suspect, Quit="Esci"))
    write_json(memory[:-5] + ".automatiche.json", dict(suspect, Quit="Esci"))
    requests, attempts = [], []
    for _ in range(5):
        p = Project(path, silent)
        b, server = local(lambda t, o: t)  # redoing it never succeeds
        p.translate(b)
        requests.append(len(server.requests))
        attempts.append(p.problems().get("Open the chest of the house", {}).get("attempts"))
    with open(os.path.join(game, "english.txt"), encoding="utf-8") as f:
        written = f.read()
    check("con la scrittura nel gioco: la traduzione sospetta resta annotata (i tentativi crescono) e dopo %d "
              "tentativi non si rifa' piu' (prima si rifaceva a ogni avvio)" % project.MAX_ATTEMPTS,
              requests[0] > 0 and requests[-1] == 0 and attempts == sorted(attempts) and attempts[0]
              and attempts[-1] >= project.MAX_ATTEMPTS and "0 Abre el cofre de la casa y el mapa" in written)

    # 11c. an unexpected error of the translator in the new rounds: the first round's translations are written anyway
    game, path, memory = lines_game(base, "giro-rotto", ["Light the lamp in the tower", "Close the gate of the house"])

    def broken(text, options):
        if options["seed"] not in (42, 7, 11):
            return RuntimeError("guasto imprevisto")
        return text if text == "Close the gate of the house" else "IT " + text
    p = Project(path, silent)
    b, server = local(broken)
    completed = p.translate(b)
    with open(os.path.join(game, "english.txt"), encoding="utf-8") as f:
        written = f.read()
    check("errore imprevisto nei nuovi giri: la traduzione non si ferma, il resto e' scritto nel gioco e il testo "
              "rimasto va in problemi.json",
              completed is False and "0 IT Light the lamp in the tower" in written
              and "1 Close the gate of the house" in written
              and p.problems().get("Close the gate of the house", {}).get("where") == "translation")

    # 11d. shared translation memory: a short text translated now that fails the current checks does not go in
    shared = project.shared_path("en", "it")
    p = Project(profile_of(base, "comune-spagnolo"), silent)
    b, server = local(lambda t, o: {"Open the house chest": "Abre el cofre y el mapa",
                                     "Light the torch": "Accendi la torcia"}.get(t, t))
    p.translate_texts(["Open the house chest", "Light the torch"], b, "en", "it")
    after = read_json(shared)
    check("memoria comune: una traduzione nuova in un'altra lingua resta nel gioco ma non va agli altri giochi",
              p.memory.get("Open the house chest") == "Abre el cofre y el mapa" and "Open the house chest" not in after
              and after.get("Light the torch") == "Accendi la torcia")

    # 11f. a text that is only a game name, machine-translated: the name comes back, without requests, and then it is
    # not rechecked at every run ('not translated' does not apply to a name left as it is on purpose)
    name = "Cult Of The Lamb"
    path = profile_of(base, "solo-nome", do_not_translate=[name])
    memory = project.memory_path({"name": "solo-nome", "target_language": "it"})
    write_json(memory, {name: "Culto dell'Agnello"})
    write_json(memory[:-5] + ".automatiche.json", {name: "Culto dell'Agnello"})
    requests = []
    for _ in range(2):
        p = Project(path, silent)
        b, server = local(lambda t, o: "IT " + t)
        p.translate_texts([name], b, "en", "it")
        requests.append(len(server.requests))
    check("testo che e' solo un nome del gioco, tradotto: torna il nome senza richieste, e non si rifa' a ogni avvio",
              p.memory[name] == name and requests == [0, 0] and not p.problems() and not p._to_redo([name], "it"))

    # 11e. official translation copied: only for sentences (a word that is the same in two languages is normal)
    check("'riferimento copiato' solo per le frasi: 'Oro' per 'Gold' (come in spagnolo) va bene",
              not project.entry_problems("Gold", "Oro", "it", references=["Oro"])
              and project.entry_problems("Open the big chest now", "Abre el gran cofre ahora", "it",
                                         references=["Abre el gran cofre ahora"]) == ["copied reference"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
