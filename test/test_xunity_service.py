"""Tests of the new-lines translator for XUnity (xunity_service), of its installation in the game and of the
translation memory shared between several programs (shared_file), without graphics card and without network.

Usage: python -I test/test_xunity_service.py <empty_temp_folder> [Unity_game_Data_folder]
- service with the fake translator on 127.0.0.1: answers, XUnity {{A}} placeholders, translation memory, diary of
  the lines, port already in use, closing at the end of the game process (503 while closing), old temp files removed;
- translators in a queue: one that does not answer is skipped, one that is paused is not called, none reachable = 500;
- lines rejected by the checks: never 500, fallback answer ("relaxed") or the original ("rejected");
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
    print(("OK    " if condition else "ERROR ") + "  " + name)


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
    profile = {"name": name, "game_folder": tmp, "engine": "unity", "source_language": "en",
               "target_language": "it", "file": []}
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
    memory = memory_path({"name": "prova-servizio-xunity", "target_language": "it"}, test=True)
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
        check("answers as PatchSmith's service", ask(port, "/patchsmith") ==
                  (200, "patchsmith prova-servizio-xunity"))
        code, translated = translate(port, XUNITY)
        check("translates (fake) with the XUnity placeholders intact",
                  code == 200 and translated.startswith("[it]") and "{{A}}" in translated and "{{B}}" in translated)
        check("saves to memory", s.project.memory.get(XUNITY) == translated and os.path.exists(memory))
        check("same line: same answer", translate(port, XUNITY) == (200, translated))
        lines = journal("prova-servizio-xunity")
        check("diary: one row per line (translator, then memory), with text, languages and answer",
                  [(r["outcome"], r["backend"]) for r in lines] == [("ok", "fake"), ("ok", "memory")]
                  and lines[0]["text"] == XUNITY and lines[0]["response"] == translated
                  and (lines[0]["from"], lines[0]["to"]) == ("en", "it") and lines[0]["ts"])
        s.project.memory["Did you know that higher level <artifact>artifacts</artifact> will earn you"] = \
            "Sai che gli <artifact>oggetti</artifact> di livello piu' alto ti fanno guadagnare"
        check("piece of an already translated line (text tables translated in the game): stays as it is, without "
                  "translator", translate(port, "Sai che gli ") == (200, "Sai che gli ")
                  and journal("prova-servizio-xunity")[-1]["backend"] == "already_translated")
        check("empty text: empty answer", ask(port, "/translate", text=" ") == (200, " "))
        check("unknown address: 404", ask(port, "/altro")[0] == 404)
        check("at startup the old temp file is removed, the recent one is left",
                  not os.path.exists(old) and os.path.exists(most_recent))
        second = xunity_service.Service(path, port, os.getpid(), log_file.append, backend=Fake(), test=True)
        check("port already used by the same game: the first service follows the new process",
                  second.launch() == 0 and s.pid == os.getpid())
        s.pid = game.pid
        check("game process alive", xunity_service.process_alive(game.pid))
        game.kill()
        game.wait()
        check("game process closed", not xunity_service.process_alive(game.pid))
        thread.join(timeout=10)
        check("the service closes with the game", not thread.is_alive())
        check("closing logged", any("The game has closed" in r for r in log_file))
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
        check("game closed during a translation: port free at once (%.1f s), the line in progress finishes and "
                  "stays in memory, the service closes" % released_after,
                  released_after < 2.5 and s.project.memory.get("Open the gate") == "[it] Open the gate"
                  and journal("chiusura-in-corso")[-1]["outcome"] == "ok" and not thread.is_alive())
    finally:
        if game.poll() is None:
            game.kill()
        if thread.is_alive() and s.httpd is not None:
            s.httpd.shutdown()


def test_queue(tmp):
    """Several translators in a queue: we move on to the next one; 500 only if none answers."""
    faulty, good = Faulty(), Counting()
    with Running(tmp, "fila", [("local", faulty), ("google", good)]) as a:
        code, translated = translate(a.port, "Open the gate")
        latest = journal("fila")[-1]
        check("the first translator does not answer: the second one translates (200), the diary says so",
                  (code, translated) == (200, "[it] Open the gate") and faulty.calls == 1
                  and (latest["outcome"], latest["backend"]) == ("ok", "google"))
    paused, after = Counting("[pausa]", pause="Google is paused (simulated)"), Counting("[mm]")
    with Running(tmp, "pause", [("google", paused), ("mymemory", after)]) as a:
        check("paused translator: not even called, the next one translates",
                  translate(a.port, "Open the gate") == (200, "[mm] Open the gate") and paused.calls == 0)
    with Running(tmp, "none", [("local", Faulty()), ("google", Faulty())]) as a:
        code, _ = translate(a.port, "Open the gate")
        latest = journal("none")[-1]
        check("no translator reachable: 500 (only in this case), 'error' in the diary with the reasons",
                  code == 500 and latest["outcome"] == "error" and "not responding" in latest["reason"])
    with Running(tmp, "empty", []) as a:
        check("no translator available: 500", translate(a.port, "Open the gate")[0] == 500)
    counting = Counting()
    with Running(tmp, "chiusura", [("google", counting)]) as a:
        a.service.closing.set()
        code, _ = translate(a.port, "Open the gate")
        check("game closing: 503 and no translation (translator not called), 'interrupted' in the diary",
                  code == 503 and counting.calls == 0 and journal("chiusura")[-1]["outcome"] == "interrupted")


def test_rejects(tmp):
    """Lines no translator can translate well: 200 with the fallback answer or with the original, never 500."""
    def line_len(text, _opts):  # very long translation: rejected ("abnormal length"), but Italian and complete
        return "Tieni premuto " + text.replace("Hold RMB", "RMB") + " " + "per saltare in alto " * 8

    b, _ = test_local.local(line_len)
    with Running(tmp, "relaxed", [("local", b)]) as a:
        codes, responses = [], []
        for _ in range(3):
            code, response = translate(a.port, XUNITY)
            codes.append(code)
            responses.append(response)
        lines = [r for r in journal("relaxed") if r["text"] == XUNITY]
        check("rejected by the checks: 200 with the best translation, {{A}} placeholders intact, never 500",
                  codes == [200, 200, 200] and "{{A}}" in responses[0] and "{{B}}" in responses[0]
                  and responses[0].startswith("Tieni premuto"))
        check("fallback answer: NOT in memory, in the diary as 'relaxed' (to translate again later)",
                  XUNITY not in a.service.project.memory and lines and all(r["outcome"] == "relaxed" for r in lines)
                  and lines[0]["response"] == responses[0] and "rejected" in lines[0]["reason"])

    def in_pieces(text, _opts):  # the sentence with codes is wrong, the pieces between codes are translated well
        if "[[" in text:
            return text + " " + "e poi ancora " * 12
        return {"Hold RMB": "Tieni premuto RMB", "to leap up to": "per saltare fino a", "m and slam": "m e schiantati"
                }.get(text, "")

    b, server = test_local.local(in_pieces)
    with Running(tmp, "a-pezzi", [("local", b)]) as a:
        code, response = translate(a.port, XUNITY)
        check("fallback: the pieces between the placeholders translated separately (once), then put back together",
                  (code, response) == (200, "Tieni premuto RMB {{A}}s per saltare fino a {{B}} m e schiantati")
                  and journal("a-pezzi")[-1]["outcome"] == "relaxed")

    def english(text, _opts):  # answers in English: no translation
        return text

    b, _ = test_local.local(english)
    with Running(tmp, "inglese", [("local", b)]) as a:
        code, response = translate(a.port, XUNITY)
        latest = journal("inglese")[-1]
        check("proposal in another language: 200 with the original, 'rejected' in the diary",
                  (code, response) == (200, XUNITY) and latest["outcome"] == "rejected"
                  and latest["response"] == XUNITY)
    with Running(tmp, "placeholders", [("google", NoPlaceholders())]) as a:
        code, response = translate(a.port, XUNITY)
        check("{{A}} placeholders lost (also in pieces): 200 with the original, 'rejected'",
                  (code, response) == (200, XUNITY) and journal("placeholders")[-1]["outcome"] == "rejected")
    check("answer language: Italian yes, English or Spanish no",
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
    check("answer language: Italian lines with 'se', 'tu', 'su', 'no' stay Italian%s" % (
        ": %s" % swapped if swapped else ""), not swapped)
    check("answer language: same as the English original (even with a single frequent word) = not translated",
              xunity_service.other_language("Hold RMB {{A}}s to leap", "Hold RMB {{A}}s to leap", "en", "it")
              and xunity_service.other_language("Open the door in the castle", "Open the door in the castle now", "en",
                                               "it"))
    with Running(tmp, "uguale", [("local", Repeats())]) as a:
        code, response = translate(a.port, "Hold RMB {{A}}s to leap")
        check("rejected proposal equal to the original: 200 with the original, 'rejected' in the diary (not "
                  "'relaxed')", (code, response) == (200, "Hold RMB {{A}}s to leap")
                  and journal("uguale")[-1]["outcome"] == "rejected")


def test_google(tmp):
    """Google in the service goes through PatchSmith's rate limiter: pauses respected, long waits = line postponed."""
    state = limiter.state_path()
    now = time.time()
    os.makedirs(os.path.dirname(state), exist_ok=True)
    with open(state, "w", encoding="utf-8") as f:  # 30 requests in the last minute: the next one in ~10 minutes
        json.dump({"version": 1, "google": {"requests": [[now - 50 + i, 100] for i in range(30)]}}, f)
    g = xunity_service.create_translator("google", silent)
    requests = []
    g.opener = lambda req, timeout: requests.append(req) or (_ for _ in ()).throw(OSError("no network in the test"))
    check("Google in the service: short limiter waits and a single retry after a network error",
              g.limiter.sleep is xunity_service._short_wait and g.NETWORK_WAITS == (5,))
    with Running(tmp, "deferred", [("google", g)]) as a:
        code, response = translate(a.port, "Open the gate")
        latest = journal("deferred")[-1]
        check("Google with the cautious margins full: line postponed (200 with the original, 'deferred'), no "
                  "request", (code, response) == (200, "Open the gate") and latest["outcome"] == "deferred"
                  and not requests)
    limiter.Limiter("google", log=None).blocked(429, "test")
    with Running(tmp, "google-in-pausa", [("google", g), ("mymemory", Counting("[mm]"))]) as a:
        check("Google paused for a block: not called, the next one translates",
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
        check("DeepL in the service: a single retry after at most 5 s, even with a Retry-After of 120 s "
                  "(%d requests, longest wait %s s)" % (len(calls), max(expectations or [0])),
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
        ollama.state = lambda: "ready"
        first = xunity_service.expected_translators({"backend": "google"})
        with_deepl = xunity_service.expected_translators({"backend": "deepl"})  # without a key: no
        with open(settings, "w", encoding="utf-8") as f:
            json.dump({"backend": "mymemory"}, f)
        with_mymemory = xunity_service.expected_translators({"backend": "local"})
        os.remove(settings)
        limiter.Limiter("google", log=None).blocked(429, "test")
        paused = xunity_service.expected_translators({"backend": "google"})
        ollama.state = lambda: "not installed"
        nothing = xunity_service.expected_translators({"backend": "google"})
        os.remove(limiter.state_path())  # Google free again
        s = xunity_service.Service(make_test_profile(tmp, "fila-vera", "google"), free_port(), None, silent,
                                     test=True)
        s._prepare()  # without starting the server: the queue the service started by the game would use
        check("service started by the game without a local translator: PatchSmith's Google, with the short waits",
                  [n for n, _ in s.chain] == ["google"] and s._ready.is_set()
                  and s.chain[0][1].limiter.sleep is xunity_service._short_wait)
        check("translators in the queue: local and Google; DeepL only with the key; MyMemory if chosen; paused Google "
                  "excluded; none if there is nothing else (%s)" % [first, with_deepl, with_mymemory, paused, nothing],
                  first == ["local", "google"] and with_deepl == ["local", "google"]
                  and with_mymemory == ["local", "mymemory", "google"] and paused == ["local"] and nothing == [])
    finally:
        ollama.state = real
        if os.path.exists(limiter.state_path()):
            os.remove(limiter.state_path())
    b, _ = test_local.local(lambda t, o: "IT " + t)
    check("local translator: remembers the rejected proposal only when asked to (service)", b.rejects is None)


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
    memory = memory_path({"name": "condivisa", "target_language": "it"})
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
    check("two programs save different entries together: both stay", on_disk == {"Start": "Inizia",
                                                                                         "Options": "Opzioni"})
    del first.memory["Start"]  # removed by one (e.g. a translation to redo)...
    first._save_memory()
    second.memory["Back"] = "Indietro"  # ...while the other, which still had it, adds another one
    second._save_memory()
    with open(memory, encoding="utf-8") as f:
        on_disk = json.load(f)
    check("an entry removed by one does not come back with the other's save; its new entries stay",
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
    check("4 processes x %d saves together: no entry lost, no temp file left%s" % (
        how_many, "" if outputs == [0] * 4 and not lost_items and not remaining else
        " (exit codes %s, lost %s, left %s)" % (outputs, lost_items[:5], remaining)),
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
    check("shared memory: the new entries of two programs both stay",
              shared.get("Load") == "Carica" and shared.get("Save") == "Salva")

    # damaged file (edited by hand with an error): it is kept aside, not lost
    with open(memory, "w", encoding="utf-8") as f:
        f.write('{"Options": "Opzioni",')
    second.memory["Exit"] = "Esci"
    second._save_memory()
    with open(memory, encoding="utf-8") as f:
        after = json.load(f)
    check("memory damaged by hand: copied to .illeggibile and rewritten with all the entries of this program",
              os.path.exists(memory + ".illeggibile") and after.get("Exit") == "Esci"
              and after.get("Back") == "Indietro" and second.memory == after)
    unlocked = not os.path.exists(memory + ".lock")
    with shared_file.Lock(memory, wait=1) as lock:
        acquired = lock.acquired
        with shared_file.Lock(memory, wait=0.3) as other:  # another program waits, then goes on
            is_busy = not other.acquired
    check("lock: free after use (the .lock file does not stay), exclusive while held",
              unlocked and acquired and is_busy and not os.path.exists(memory + ".lock"))
    # a .lock that another program is removing cannot be opened for a moment (Windows: "delete pending"): before, we
    # saved without a lock, and a save together with another one lost entries
    real_open, refusals = shared_file.os.open, [0]

    def open_after_a_moment(path, *arguments):
        if str(path).endswith(".lock") and refusals[0] < 3:
            refusals[0] += 1
            raise PermissionError(13, "file being deleted")
        return real_open(path, *arguments)
    shared_file.os.open = open_after_a_moment
    try:
        with shared_file.Lock(memory, wait=5) as lock:
            acquired = lock.acquired
    finally:
        shared_file.os.open = real_open
    check("lock: a .lock that cannot be opened for a moment is retried (no saving without a lock)",
              acquired and refusals[0] == 3)


def test_group(tmp):
    """POST /translate-group (live translation of web games): several lines in a single group translation, those in the
    translation memory at once, the pieces already in Italian left alone. The scripts installed by earlier versions
    (POST /traduci-gruppo with "testi", answer read from "traduzioni") still get their translations."""
    counting = Counting()
    with Running(tmp, "gruppo", [("fake", counting)]) as av:
        av.service.project.memory["Settings"] = "Impostazioni"
        body = json.dumps({"from": "en", "to": "it", "texts": ["Start the adventure now", "Quit to the main menu",
                                                              "Settings", "Impostazioni"]}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/translate-group" % av.port, data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            response = json.loads(r.read().decode("utf-8"))
        old_body = json.dumps({"from": "en", "to": "it", "testi": ["Settings"]}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/traduci-gruppo" % av.port, data=old_body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            old_response = json.loads(r.read().decode("utf-8"))
    t = response["translations"]
    check("group: new lines translated together (one call to the translator), the one in memory at once, the piece "
              "already in Italian left alone (%s)" % response,
              t.get("Start the adventure now", "").startswith("[it]") and t.get("Quit to the main menu", "").startswith("[it]")
              and t.get("Settings") == "Impostazioni" and "Impostazioni" not in t and not response["retry"]
              and counting.calls == 1)
    check("group from the scripts of earlier versions (%s)" % old_response,
              old_response.get("traduzioni") == {"Settings": "Impostazioni"} and old_response.get("riprova") == [])


def test_stop(tmp):
    """The "Stop the game translator" button: is_on() sees it listening, stop() closes it."""
    name = "prova-ferma-traduttore"
    port = xunity_service.port_for(name)
    if port_busy(port):
        print("(port %d busy: Stop button test skipped)" % port)
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
    check("Stop the game translator: it was on, it closes, then it is off",
              before and stopped and not thread.is_alive() and not xunity_service.is_on(name))


def test_configuration(tmp, game_data):
    folder = os.path.join(tmp, "game")
    os.makedirs(folder)
    url = xunity_service.address(47123)
    p = adapter_unity.configure(folder, "en", "it", imgui=False, url=url)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("XUnity uses CustomTranslate towards PatchSmith's translator, without anti-spam checks, long texts",
              "Endpoint=CustomTranslate" in text and "[Custom]" in text and "Url=" + url in text
              and "EnableShortDelay=True" in text and "DisableSpamChecks=True" in text
              and "MaxCharactersPerTranslation=2500" in text and re.search(r"^FallbackEndpoint=$", text, re.M))
    adapter_unity.configure(folder, "en", "it", imgui=False)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("without PatchSmith's translator it goes back to Google (500 characters)",
              "Endpoint=GoogleTranslateV2" in text and "MaxCharactersPerTranslation=500" in text)
    adapter_unity.configure(folder, "en", "it", imgui=False, google=False)
    with open(p, encoding="utf-8") as f:
        text = f.read()
    check("Google paused: no translator (empty Endpoint)", re.search(r"^Endpoint=$", text, re.M)
              and "GoogleTranslateV2" not in text)

    if not game_data:
        print("(plugin compilation not tested: give the _Data folder of a Unity Mono game)")
        return
    zip_bepinex = project.components_folder("BepInEx_win_x64_5.4.23.5.zip")
    with zipfile.ZipFile(zip_bepinex) as z:
        z.extract("BepInEx/core/BepInEx.dll", folder)
    profile = os.path.join(tmp, "profilo.json")
    url = adapter_unity.enable_service(folder, {"data_folder": game_data}, profile, "prova-servizio-xunity")
    check("plugin compiled", os.path.getsize(os.path.join(folder, *adapter_unity.SERVICE_PLUGIN.split("/")))
              > 2000)
    with open(os.path.join(folder, *adapter_unity.SERVICE_CONFIG.split("/")), encoding="utf-8") as f:
        cfg = f.read()
    check("service command", "xunity-service" in cfg and profile in cfg and "Program=" in cfg
              and os.path.exists(cfg.split("Program=")[1].splitlines()[0]))
    check("address read from the command", adapter_unity.service_url(folder) == url
              and url == xunity_service.address(xunity_service.port_for("prova-servizio-xunity")))
    check("the service files are removed by the uninstall",
              adapter_unity._created_by_them("config/patchsmith-servizio.cfg")
              and adapter_unity._created_by_them("plugins/Patchsmith/Patchsmith.Servizio.dll")
              and adapter_unity._created_by_them("patchsmith-servizio-stato.txt"))
    check("disabled", adapter_unity.disable_service(folder) and not adapter_unity.service_installed(folder)
              and adapter_unity.service_url(folder) is None)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tmp = os.path.abspath(sys.argv[1])
    os.makedirs(tmp, exist_ok=True)
    if os.listdir(tmp):
        raise SystemExit("The temporary folder must be empty: %s" % tmp)
    project.DATA_ROOT = os.path.join(tmp, "data_folder")  # the test service's memory and diary: never in the real data
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
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
