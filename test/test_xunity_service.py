"""Tests of the new-lines translator for XUnity (xunity_service), of its installation in the game and of the
translation memory shared between several programs (shared_file), without graphics card and without network.

Usage: python -I test/test_xunity_service.py <empty_temp_folder> [cartella_Data_di_un_gioco_Unity]
- service with the fake translator on 127.0.0.1: answers, XUnity {{A}} placeholders, translation memory, diary of
  the lines, port already in use, closing at the end of the game process (503 while closing), old temp files removed;
- translators in a queue: one that does not answer is skipped, one that is paused is not called, none reachable = 500;
- lines rejected by the checks: never 500, fallback answer ("rilassata") or the original ("rifiutata");
- Google with PatchSmith's cautious margins: a long wait postpones the line, without requests;
- which translators the service would use (local, chosen online one, Google if it is not paused);
- translation memory, machine translations and shared translation memory saved together by several programs: no
  entry lost;
- XUnity configuration (CustomTranslate) and, if the _Data folder of a Unity game is given, compilation of the
  plugin that starts the service (the game's libraries are only read).
"""
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import test_local  # noqa: E402  (local translator with a simulated server)
from patchsmith import (adapter_unity, shared_file, interruption,
                        limiter, ollama, project)  # noqa: E402
from patchsmith import xunity_service  # noqa: E402
from patchsmith.backend import BackendError, Fake  # noqa: E402
from patchsmith.project import PROJECT_FOLDER, Project, shared_path, memory_path  # noqa: E402

OUTCOMES = []
XUNITY = "Hold RMB {{A}}s to leap up to {{B}} m and slam"


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ask(port, path, **params):
    url = "http://127.0.0.1:%d%s" % (port, path)
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def translate(port, text):
    return ask(port, "/translate", **{"from": "en", "to": "it", "text": text})


def make_test_profile(tmp, name, backend=None):
    profile = {"nome": name, "cartella_gioco": tmp, "motore": "unity", "lingua_origine": "en",
               "lingua_destinazione": "it", "file": []}
    if backend:
        profile["backend"] = backend
    path = os.path.join(tmp, name + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f)
    return path


def journal(name):
    try:
        with open(xunity_service.journal_path(name), encoding="utf-8") as f:
            return [json.loads(r) for r in f if r.strip()]
    except FileNotFoundError:
        return []


class Running:
    """A service listening in a thread (without a game process to follow), stopped on exit."""

    def __init__(self, tmp, name, chain, test=True):
        self.port = free_port()
        self.log_file = []
        self.service = xunity_service.Service(make_test_profile(tmp, name), self.port, None, self.log_file.append,
                                                 backend=chain, test=test)
        self.thread = threading.Thread(target=self.service.launch)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.service.httpd is not None:
                break
            time.sleep(0.05)
        self.service._ready.wait(10)
        return self

    def __exit__(self, *_):
        if self.service.httpd is not None:
            self.service.httpd.shutdown()
        self.thread.join(timeout=10)


class Faulty:
    """Translator that does not answer."""
    name = "guasto"

    def __init__(self):
        self.calls = 0

    def translate_lines(self, lines, origin, dest_lang):
        self.calls += 1
        raise BackendError("not responding (simulated)")


class Counting(Fake):
    """The test translator that counts the calls."""

    def __init__(self, prefix="[it]", pause=None):
        self.prefix, self.calls, self._pause = prefix, 0, pause

    def pause(self):
        return self._pause

    def translate_lines(self, lines, origin, dest_lang):
        self.calls += 1
        return ["%s %s" % (self.prefix, r) for r in lines]


class NoPlaceholders(Fake):
    """Like Google when it loses the placeholders: whole sentence without the codes, pieces not translated."""

    def translate_lines(self, lines, origin, dest_lang):
        return ["Tieni premuto per saltare" if "[[" in r else "" for r in lines]


class Repeats(Fake):
    """Like the local translator when it repeats the original: no translation accepted, the rejected proposal is
    the original itself."""

    def __init__(self):
        self.rejects = {}

    def translate_lines(self, lines, origin, dest_lang):
        for r in lines:
            self.rejects[r] = (r, ["abnormal length"])
        return ["" for _ in lines]


def test_service(tmp):
    path = make_test_profile(tmp, "prova-servizio-xunity")
    memory = memory_path({"nome": "prova-servizio-xunity", "lingua_destinazione": "it"}, test=True)
    # a save interrupted long ago (to remove) and one just started (to leave)
    os.makedirs(os.path.dirname(memory), exist_ok=True)
    old, most_recent = memory + ".tmp", memory + ".123-4.tmp"
    for p in (old, most_recent):
        open(p, "wb").close()
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    port = free_port()
    game = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
    log_file = []
    xunity_service.GAME_CHECK = 0.3
    s = xunity_service.Service(path, port, game.pid, log_file.append, backend=Fake(), test=True)
    thread = threading.Thread(target=s.launch)
    thread.start()
    try:
        for _ in range(50):
            if s.httpd is not None:
                break
            threading.Event().wait(0.1)
        check("risponde come servizio di PatchSmith", ask(port, "/patchsmith") ==
                  (200, "patchsmith prova-servizio-xunity"))
        code, translated = translate(port, XUNITY)
        check("traduce (finto) con i segnaposto di XUnity intatti",
                  code == 200 and translated.startswith("[it]") and "{{A}}" in translated and "{{B}}" in translated)
        check("salva in memoria", s.project.memory.get(XUNITY) == translated and os.path.exists(memory))
        check("stessa frase: stessa risposta", translate(port, XUNITY) == (200, translated))
        lines = journal("prova-servizio-xunity")
        check("diario: una riga per frase (traduttore, poi memoria), con testo, lingue e risposta",
                  [(r["esito"], r["backend"]) for r in lines] == [("ok", "finto"), ("ok", "memoria")]
                  and lines[0]["testo"] == XUNITY and lines[0]["risposta"] == translated
                  and (lines[0]["da"], lines[0]["a"]) == ("en", "it") and lines[0]["ts"])
        s.project.memory["Did you know that higher level <artifact>artifacts</artifact> will earn you"] = \
            "Sai che gli <artifact>oggetti</artifact> di livello piu' alto ti fanno guadagnare"
        check("pezzo di una frase gia' tradotta (tabelle dei testi tradotte nel gioco): resta com'e', senza "
                  "traduttore", translate(port, "Sai che gli ") == (200, "Sai che gli ")
                  and journal("prova-servizio-xunity")[-1]["backend"] == "gia' tradotta")
        check("testo vuoto: risposta vuota", ask(port, "/translate", text=" ") == (200, " "))
        check("indirizzo sconosciuto: 404", ask(port, "/altro")[0] == 404)
        check("all'avvio tolto il temporaneo vecchio, lasciato quello recente",
                  not os.path.exists(old) and os.path.exists(most_recent))
        second = xunity_service.Service(path, port, os.getpid(), log_file.append, backend=Fake(), test=True)
        check("porta gia' usata dallo stesso gioco: il primo servizio segue il nuovo processo",
                  second.launch() == 0 and s.pid == os.getpid())
        s.pid = game.pid
        check("processo del gioco vivo", xunity_service.process_alive(game.pid))
        game.kill()
        game.wait()
        check("processo del gioco chiuso", not xunity_service.process_alive(game.pid))
        thread.join(timeout=10)
        check("il servizio si chiude con il gioco", not thread.is_alive())
        check("registro della chiusura", any("The game has closed" in r for r in log_file))
    finally:
        if game.poll() is None:
            game.kill()
        if thread.is_alive() and s.httpd is not None:
            s.httpd.shutdown()
        if os.path.exists(most_recent):
            os.remove(most_recent)


class Slow(Counting):
    """Slow translator (like Google waiting for its cautious margins)."""

    def translate_lines(self, lines, origin, dest_lang):
        time.sleep(4)
        return super().translate_lines(lines, origin, dest_lang)


def port_busy(port):
    with socket.socket() as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def test_close_during_translation(tmp):
    """The game closes while a line is being translated: the port is freed at once (a reopened game starts its own
    translator, instead of relying on one that is closing) and the line in progress finishes and is saved."""
    game = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
    port, slow = free_port(), Slow()
    xunity_service.GAME_CHECK = 0.3
    s = xunity_service.Service(make_test_profile(tmp, "chiusura-in-corso"), port, game.pid, silent,
                                 backend=[("google", slow)], test=True)
    thread = threading.Thread(target=s.launch)
    thread.start()
    response = []
    try:
        for _ in range(100):
            if s.httpd is not None:
                break
            time.sleep(0.05)
        s._ready.wait(10)
        request = threading.Thread(target=lambda: response.append(translate(port, "Open the gate")))
        request.start()
        time.sleep(0.5)  # the line is being translated
        game.kill()
        game.wait()
        start = time.monotonic()
        while port_busy(port) and time.monotonic() - start < 10:
            time.sleep(0.1)
        released_after = time.monotonic() - start
        request.join(timeout=30)
        thread.join(timeout=30)
        check("gioco chiuso durante una traduzione: porta libera subito (%.1f s), la frase in corso finisce e "
                  "resta in memoria, il servizio si chiude" % released_after,
                  released_after < 2.5 and s.project.memory.get("Open the gate") == "[it] Open the gate"
                  and journal("chiusura-in-corso")[-1]["esito"] == "ok" and not thread.is_alive())
    finally:
        if game.poll() is None:
            game.kill()
        if thread.is_alive() and s.httpd is not None:
            s.httpd.shutdown()


def test_queue(tmp):
    """Several translators in a queue: we move on to the next one; 500 only if none answers."""
    faulty, good = Faulty(), Counting()
    with Running(tmp, "fila", [("locale", faulty), ("google", good)]) as a:
        code, translated = translate(a.port, "Open the gate")
        latest = journal("fila")[-1]
        check("il primo traduttore non risponde: traduce il secondo (200), il diario lo dice",
                  (code, translated) == (200, "[it] Open the gate") and faulty.calls == 1
                  and (latest["esito"], latest["backend"]) == ("ok", "google"))
    paused, after = Counting("[pausa]", pause="Google is paused (simulated)"), Counting("[mm]")
    with Running(tmp, "pausa", [("google", paused), ("mymemory", after)]) as a:
        check("traduttore in pausa: non lo si chiama nemmeno, traduce il prossimo",
                  translate(a.port, "Open the gate") == (200, "[mm] Open the gate") and paused.calls == 0)
    with Running(tmp, "nessuno", [("locale", Faulty()), ("google", Faulty())]) as a:
        code, _ = translate(a.port, "Open the gate")
        latest = journal("nessuno")[-1]
        check("nessun traduttore raggiungibile: 500 (solo in questo caso), nel diario 'errore' con i motivi",
                  code == 500 and latest["esito"] == "errore" and "not responding" in latest["motivo"])
    with Running(tmp, "vuota", []) as a:
        check("nessun traduttore disponibile: 500", translate(a.port, "Open the gate")[0] == 500)
    counting = Counting()
    with Running(tmp, "chiusura", [("google", counting)]) as a:
        a.service.closing.set()
        code, _ = translate(a.port, "Open the gate")
        check("gioco in chiusura: 503 e nessuna traduzione (traduttore non chiamato), nel diario 'interrotta'",
                  code == 503 and counting.calls == 0 and journal("chiusura")[-1]["esito"] == "interrotta")


def test_rejects(tmp):
    """Lines no translator can translate well: 200 with the fallback answer or with the original, never 500."""
    def line_len(text, _opts):  # very long translation: rejected ("lunghezza anomala"), but Italian and complete
        return "Tieni premuto " + text.replace("Hold RMB", "RMB") + " " + "per saltare in alto " * 8

    b, _ = test_local.local(line_len)
    with Running(tmp, "rilassata", [("locale", b)]) as a:
        codes, responses = [], []
        for _ in range(3):
            code, response = translate(a.port, XUNITY)
            codes.append(code)
            responses.append(response)
        lines = [r for r in journal("rilassata") if r["testo"] == XUNITY]
        check("scartata dai controlli: 200 con la traduzione migliore, segnaposto {{A}} intatti, mai 500",
                  codes == [200, 200, 200] and "{{A}}" in responses[0] and "{{B}}" in responses[0]
                  and responses[0].startswith("Tieni premuto"))
        check("risposta di ripiego: NON in memoria, nel diario come 'rilassata' (da ritradurre dopo)",
                  XUNITY not in a.service.project.memory and lines and all(r["esito"] == "rilassata" for r in lines)
                  and lines[0]["risposta"] == responses[0] and "rejected" in lines[0]["motivo"])

    def in_pieces(text, _opts):  # the sentence with codes is wrong, the pieces between codes are translated well
        if "[[" in text:
            return text + " " + "e poi ancora " * 12
        return {"Hold RMB": "Tieni premuto RMB", "to leap up to": "per saltare fino a", "m and slam": "m e schiantati"
                }.get(text, "")

    b, server = test_local.local(in_pieces)
    with Running(tmp, "a-pezzi", [("locale", b)]) as a:
        code, response = translate(a.port, XUNITY)
        check("ripiego: i pezzi tra i segnaposto tradotti a parte (una volta), poi ricomposti",
                  (code, response) == (200, "Tieni premuto RMB {{A}}s per saltare fino a {{B}} m e schiantati")
                  and journal("a-pezzi")[-1]["esito"] == "rilassata")

    def english(text, _opts):  # answers in English: no translation
        return text

    b, _ = test_local.local(english)
    with Running(tmp, "inglese", [("locale", b)]) as a:
        code, response = translate(a.port, XUNITY)
        latest = journal("inglese")[-1]
        check("proposta in un'altra lingua: 200 con l'originale, nel diario 'rifiutata'",
                  (code, response) == (200, XUNITY) and latest["esito"] == "rifiutata"
                  and latest["risposta"] == XUNITY)
    with Running(tmp, "segnaposto", [("google", NoPlaceholders())]) as a:
        code, response = translate(a.port, XUNITY)
        check("segnaposto {{A}} persi (anche a pezzi): 200 con l'originale, 'rifiutata'",
                  (code, response) == (200, XUNITY) and journal("segnaposto")[-1]["esito"] == "rifiutata")
    check("lingua della risposta: italiano si', inglese o spagnolo no",
              not xunity_service.other_language("Open the door to the castle", "Apri la porta del castello", "en", "it")
              and xunity_service.other_language("Open the door to the castle", "Open the door of the castle", "en",
                                               "it")
              and xunity_service.other_language("Open the door to the castle", "Abre la puerta de los castillos y "
                                               "las torres", "en", "it"))
    # Italian words that in the lists are among those of other languages ("se", "tu", "su", "no", "o", "a"): an
    # Italian answer must not be mistaken for Spanish or Portuguese; one left in English must (even a short one)
    italian_sentences = ["Se tu vuoi, premi {{A}} su la mappa", "Se non hai oro, tu perdi",
                "Mettilo su il tavolo se puoi", "Vuoi salvare? Sì o no", "Sali su se osi",
                "Tocca a te? Se sì, tira", "No, non puoi andare lì"]
    swapped = [t for t in italian_sentences if xunity_service.other_language("If you want, press {{A}}", t, "en", "it")]
    check("lingua della risposta: frasi italiane con 'se', 'tu', 'su', 'no' restano italiane%s" % (
        ": %s" % swapped if swapped else ""), not swapped)
    check("lingua della risposta: uguale all'originale inglese (anche con una sola parola frequente) = non tradotta",
              xunity_service.other_language("Hold RMB {{A}}s to leap", "Hold RMB {{A}}s to leap", "en", "it")
              and xunity_service.other_language("Open the door in the castle", "Open the door in the castle now", "en",
                                               "it"))
    with Running(tmp, "uguale", [("locale", Repeats())]) as a:
        code, response = translate(a.port, "Hold RMB {{A}}s to leap")
        check("proposta scartata uguale all'originale: 200 con l'originale, nel diario 'rifiutata' (non "
                  "'rilassata')", (code, response) == (200, "Hold RMB {{A}}s to leap")
                  and journal("uguale")[-1]["esito"] == "rifiutata")


def test_google(tmp):
    """Google in the service goes through PatchSmith's rate limiter: pauses respected, long waits = line postponed."""
    state = limiter.state_path()
    now = time.time()
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:  # 30 requests in the last minute: the next one in ~10 minutes
        json.dump({"versione": 1, "google": {"richieste": [[now - 50 + i, 100] for i in range(30)]}}, f)
    g = xunity_service.create_translator("google", silent)
    requests = []
    g.opener = lambda req, timeout: requests.append(req) or (_ for _ in ()).throw(OSError("niente rete nella prova"))
    check("Google nel servizio: attese brevi del limitatore e un solo nuovo tentativo dopo un errore di rete",
              g.limiter.sleep is xunity_service._short_wait and g.NETWORK_WAITS == (5,))
    with Running(tmp, "rinviata", [("google", g)]) as a:
        code, response = translate(a.port, "Open the gate")
        latest = journal("rinviata")[-1]
        check("Google con i margini prudenti pieni: frase rimandata (200 con l'originale, 'rinviata'), nessuna "
                  "richiesta", (code, response) == (200, "Open the gate") and latest["esito"] == "rinviata"
                  and not requests)
    limiter.Limiter("google", log=None).blocked(429, "prova")
    with Running(tmp, "google-in-pausa", [("google", g), ("mymemory", Counting("[mm]"))]) as a:
        check("Google in pausa per un blocco: non lo si chiama, traduce il prossimo",
                  translate(a.port, "Open the gate") == (200, "[mm] Open the gate") and not requests)
    os.remove(state)

    # DeepL in the service: even if it asks to wait two minutes (Retry-After) we retry soon only once and then
    # move on to the next translator (XUnity does not wait that long)
    expectations, real_wait = [], interruption.wait_for
    old_key = os.environ.get("PATCHSMITH_DEEPL")
    os.environ["PATCHSMITH_DEEPL"] = "chiave-di-prova:fx"
    try:
        interruption.wait_for = lambda s: expectations.append(s) or (time.sleep(s) if s < 1 else None)
        d = xunity_service.create_translator("deepl", silent)
        calls = []

        def too_many(req, timeout):
            calls.append(req)
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "120"},
                                         io.BytesIO(b""))
        d.opener = too_many
        try:
            d.translate_lines(["Open the gate"], "en", "it")
            stopped = False
        except BackendError:
            stopped = True
        check("DeepL nel servizio: un solo nuovo tentativo dopo al massimo 5 s, anche con Retry-After di 120 s "
                  "(%d richieste, attesa massima %s s)" % (len(calls), max(expectations or [0])),
                  stopped and len(calls) == 2 and max(expectations) <= 5)
    finally:
        interruption.wait_for = real_wait
        if old_key is None:
            os.environ.pop("PATCHSMITH_DEEPL", None)
        else:
            os.environ["PATCHSMITH_DEEPL"] = old_key
        if os.path.exists(state):
            os.remove(state)


def test_expected(tmp):
    """Which translators the service uses: local (if installed), the chosen online one, Google if not paused."""
    real = ollama.state
    settings = project.data_folder("settings.json")
    os.environ.pop("PATCHSMITH_DEEPL", None)  # the DeepL key of whoever runs the test must not count
    try:
        ollama.state = lambda: "pronto"
        first = xunity_service.expected_translators({"backend": "google"})
        with_deepl = xunity_service.expected_translators({"backend": "deepl"})  # without a key: no
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"backend": "mymemory"}, f)
        with_mymemory = xunity_service.expected_translators({"backend": "locale"})
        os.remove(settings)
        limiter.Limiter("google", log=None).blocked(429, "prova")
        paused = xunity_service.expected_translators({"backend": "google"})
        ollama.state = lambda: "not installed"
        nothing = xunity_service.expected_translators({"backend": "google"})
        os.remove(limiter.state_path())  # Google free again
        s = xunity_service.Service(make_test_profile(tmp, "fila-vera", "google"), free_port(), None, silent,
                                     test=True)
        s._prepare()  # without starting the server: the queue the service started by the game would use
        check("servizio avviato dal gioco senza traduttore locale: Google di PatchSmith, con le attese brevi",
                  [n for n, _ in s.chain] == ["google"] and s._ready.is_set()
                  and s.chain[0][1].limiter.sleep is xunity_service._short_wait)
        check("traduttori in fila: locale e Google; DeepL solo con la chiave; MyMemory se scelto; Google in pausa "
                  "escluso; nessuno se non c'e' altro (%s)" % [first, with_deepl, with_mymemory, paused, nothing],
                  first == ["locale", "google"] and with_deepl == ["locale", "google"]
                  and with_mymemory == ["locale", "mymemory", "google"] and paused == ["locale"] and nothing == [])
    finally:
        ollama.state = real
        if os.path.exists(limiter.state_path()):
            os.remove(limiter.state_path())
    b, _ = test_local.local(lambda t, o: "IT " + t)
    check("traduttore locale: ricorda la proposta scartata solo se richiesto (servizio)", b.rejects is None)


# --- translation memory shared between several programs ---------------------------------------------

CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from patchsmith import project
project.DATA_ROOT = sys.argv[2]
p = project.Project(sys.argv[3], lambda _: None)
while time.time() < float(sys.argv[5]):
    time.sleep(0.005)
for i in range(int(sys.argv[6])):
    p.memory["%s-%d" % (sys.argv[4], i)] = "tradotto %s %d" % (sys.argv[4], i)
    p._save_memory()
"""


def test_shared_memory(tmp):
    path = make_test_profile(tmp, "condivisa")
    memory = memory_path({"nome": "condivisa", "lingua_destinazione": "it"})
    first, second = Project(path, silent), Project(path, silent)
    first.memory["Start"] = "Inizia"
    second.memory["Options"] = "Opzioni"
    gate = threading.Barrier(2)

    def save(p):
        gate.wait()
        p._save_memory()
    threads = [threading.Thread(target=save, args=(p,)) for p in (first, second)]
    for f in threads:
        f.start()
    for f in threads:
        f.join()
    with open(memory, encoding="utf-8") as f:
        on_disk = json.load(f)
    check("due programmi salvano insieme voci diverse: restano entrambe", on_disk == {"Start": "Inizia",
                                                                                         "Options": "Opzioni"})
    del first.memory["Start"]  # removed by one (e.g. a translation to redo)...
    first._save_memory()
    second.memory["Back"] = "Indietro"  # ...while the other, which still had it, adds another one
    second._save_memory()
    with open(memory, encoding="utf-8") as f:
        on_disk = json.load(f)
    check("una voce tolta da uno non torna con il salvataggio dell'altro; le sue voci nuove restano",
              on_disk == {"Options": "Opzioni", "Back": "Indietro"} and second.memory == on_disk)

    # several real processes together, many writes each (like the interface and the translator started by the game)
    children, start, how_many = [], time.time() + 1.5, 25
    for name in ("a", "b", "c", "d"):
        children.append(subprocess.Popen([sys.executable, "-I", "-c", CHILD, os.path.dirname(HERE),
                                       project.data_folder(), path, name, str(start), str(how_many)],
                                      stderr=subprocess.PIPE, text=True))
    outputs = []
    for f in children:
        _, errors = f.communicate(timeout=120)
        outputs.append(f.returncode)
        if f.returncode:
            print(errors)
    with open(memory, encoding="utf-8") as f:
        on_disk = json.load(f)
    expectations = {"%s-%d" % (n, i) for n in "abcd" for i in range(how_many)} | {"Options", "Back"}
    remaining = [n for n in os.listdir(os.path.dirname(memory)) if n.endswith(".tmp")]
    lost_items = sorted(expectations - set(on_disk))
    check("4 processi x %d salvataggi insieme: nessuna voce persa, nessun file temporaneo rimasto%s" % (
        how_many, "" if outputs == [0] * 4 and not lost_items and not remaining else
        " (uscite %s, perse %s, rimasti %s)" % (outputs, lost_items[:5], remaining)),
        outputs == [0] * 4 and set(on_disk) == expectations and not remaining)

    # shared translation memory and machine translations: same rules
    third = Project(make_test_profile(tmp, "condivisa-due"), silent)
    fourth = Project(make_test_profile(tmp, "condivisa-tre"), silent)
    shared_a = third._shared_memory("en", "it")
    shared_b = fourth._shared_memory("en", "it")
    third.memory["Load"] = "Carica"
    fourth.memory["Save"] = "Salva"
    third._update_shared(shared_a, "en", "it", ["Load"])
    fourth._update_shared(shared_b, "en", "it", ["Save"])
    with open(shared_path("en", "it"), encoding="utf-8") as f:
        shared = json.load(f)
    check("memoria comune: le voci nuove di due programmi restano entrambe",
              shared.get("Load") == "Carica" and shared.get("Save") == "Salva")

    # damaged file (edited by hand with an error): it is kept aside, not lost
    with open(memory, "w", encoding="utf-8") as f:
        f.write('{"Options": "Opzioni",')
    second.memory["Exit"] = "Esci"
    second._save_memory()
    with open(memory, encoding="utf-8") as f:
        after = json.load(f)
    check("memoria rovinata a mano: copiata in .illeggibile e riscritta con tutte le voci di questo programma",
              os.path.exists(memory + ".illeggibile") and after.get("Exit") == "Esci"
              and after.get("Back") == "Indietro" and second.memory == after)
    unlocked = not os.path.exists(memory + ".lock")
    with shared_file.Lock(memory, wait=1) as lock:
        acquired = lock.acquired
        with shared_file.Lock(memory, wait=0.3) as other:  # another program waits, then goes on
            is_busy = not other.acquired
    check("lucchetto: libero dopo l'uso (il file .lock non resta), esclusivo mentre lo si tiene",
              unlocked and acquired and is_busy and not os.path.exists(memory + ".lock"))
    # a .lock that another program is removing cannot be opened for a moment (Windows: "delete pending"): before, we
    # saved without a lock, and a save together with another one lost entries
    real_open, refusals = shared_file.os.open, [0]

    def open_after_a_moment(path, *arguments):
        if str(path).endswith(".lock") and refusals[0] < 3:
            refusals[0] += 1
            raise PermissionError(13, "file in cancellazione")
        return real_open(path, *arguments)
    shared_file.os.open = open_after_a_moment
    try:
        with shared_file.Lock(memory, wait=5) as lock:
            acquired = lock.acquired
    finally:
        shared_file.os.open = real_open
    check("lucchetto: un .lock che non si apre per un attimo si riprova (non si salva senza lucchetto)",
              acquired and refusals[0] == 3)


def test_group(tmp):
    """POST /traduci-gruppo (live translation of web games): several lines in a single group translation, those in
    the translation memory at once, the pieces already in Italian left alone."""
    counting = Counting()
    with Running(tmp, "gruppo", [("finto", counting)]) as av:
        av.service.project.memory["Settings"] = "Impostazioni"
        body = json.dumps({"from": "en", "to": "it", "testi": ["Start the adventure now", "Quit to the main menu",
                                                              "Settings", "Impostazioni"]}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/traduci-gruppo" % av.port, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            response = json.loads(r.read().decode("utf-8"))
    t = response["traduzioni"]
    check("gruppo: frasi nuove tradotte insieme (una chiamata al traduttore), quella in memoria subito, il pezzo gia' "
              "in italiano lasciato stare (%s)" % response,
              t.get("Start the adventure now", "").startswith("[it]") and t.get("Quit to the main menu", "").startswith("[it]")
              and t.get("Settings") == "Impostazioni" and "Impostazioni" not in t and not response["riprova"]
              and counting.calls == 1)


def test_stop(tmp):
    """The "Stop the game translator" button: acceso() sees it listening, ferma() closes it."""
    name = "prova-ferma-traduttore"
    port = xunity_service.port_for(name)
    if port_busy(port):
        print("(porta %d occupata: prova del pulsante Ferma saltata)" % port)
        return
    s = xunity_service.Service(make_test_profile(tmp, name), port, None, silent, backend=Fake(), test=True)
    thread = threading.Thread(target=s.launch)
    thread.start()
    for _ in range(100):
        if s.httpd is not None:
            break
        time.sleep(0.05)
    before = xunity_service.is_on(name, wait=2)
    stopped = xunity_service.stop(name)
    thread.join(timeout=15)
    check("Ferma il traduttore del gioco: era acceso, si chiude, poi risulta spento",
              before and stopped and not thread.is_alive() and not xunity_service.is_on(name))


def test_configuration(tmp, game_data):
    folder = os.path.join(tmp, "gioco")
    os.makedirs(folder)
    url = xunity_service.address(47123)
    p = adapter_unity.configure(folder, "en", "it", imgui=False, url=url)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("XUnity usa CustomTranslate verso il traduttore di PatchSmith, senza controllo anti-spam, testi lunghi",
              "Endpoint=CustomTranslate" in text and "[Custom]" in text and "Url=" + url in text
              and "EnableShortDelay=True" in text and "DisableSpamChecks=True" in text
              and "MaxCharactersPerTranslation=2500" in text and re.search(r"^FallbackEndpoint=$", text, re.M))
    adapter_unity.configure(folder, "en", "it", imgui=False)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("senza traduttore di PatchSmith torna a Google (500 caratteri)",
              "Endpoint=GoogleTranslateV2" in text and "MaxCharactersPerTranslation=500" in text)
    adapter_unity.configure(folder, "en", "it", imgui=False, google=False)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("Google in pausa: nessun traduttore (Endpoint vuoto)", re.search(r"^Endpoint=$", text, re.M)
              and "GoogleTranslateV2" not in text)

    if not game_data:
        print("(compilazione del plugin non provata: indica la cartella _Data di un gioco Unity Mono)")
        return
    zip_bepinex = project.components_folder("BepInEx_win_x64_5.4.23.5.zip")
    with zipfile.ZipFile(zip_bepinex) as z:
        z.extract("BepInEx/core/BepInEx.dll", folder)
    profile = os.path.join(tmp, "profilo.json")
    url = adapter_unity.enable_service(folder, {"dati": game_data}, profile, "prova-servizio-xunity")
    check("plugin compilato", os.path.getsize(os.path.join(folder, *adapter_unity.SERVICE_PLUGIN.split("/")))
              > 2000)
    with open(os.path.join(folder, *adapter_unity.SERVICE_CONFIG.split("/")), encoding="utf-8") as f:
        cfg = f.read()
    check("comando del servizio", "servizio-xunity" in cfg and profile in cfg and "Programma=" in cfg
              and os.path.exists(cfg.split("Programma=")[1].splitlines()[0]))
    check("indirizzo letto dal comando", adapter_unity.service_url(folder) == url
              and url == xunity_service.address(xunity_service.port_for("prova-servizio-xunity")))
    check("i file del servizio si tolgono con la disinstallazione",
              adapter_unity._created_by_them("config/patchsmith-servizio.cfg")
              and adapter_unity._created_by_them("plugins/Patchsmith/Patchsmith.Servizio.dll")
              and adapter_unity._created_by_them("patchsmith-servizio-stato.txt"))
    check("disattivato", adapter_unity.disable_service(folder) and not adapter_unity.service_installed(folder)
              and adapter_unity.service_url(folder) is None)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tmp = os.path.abspath(sys.argv[1])
    os.makedirs(tmp, exist_ok=True)
    if os.listdir(tmp):
        raise SystemExit("La cartella temporanea deve essere vuota: %s" % tmp)
    project.DATA_ROOT = os.path.join(tmp, "dati")  # the test service's memory and diary: never in the real data
    test_service(tmp)
    test_close_during_translation(tmp)
    test_queue(tmp)
    test_rejects(tmp)
    test_google(tmp)
    test_expected(tmp)
    test_shared_memory(tmp)
    test_group(tmp)
    test_stop(tmp)
    test_configuration(tmp, sys.argv[2] if len(sys.argv) > 2 else None)
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\n%d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
