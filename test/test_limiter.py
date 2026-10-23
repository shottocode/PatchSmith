"""Test of the request limiter and of the online translators (free Google, DeepL, MyMemory).

Usage: python -I test/test_limiter.py <empty_temp_folder>
No network: the HTTP responses are simulated and the clock is fake (no real waiting).
"""
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import backend, interruption, limiter, project  # noqa: E402
from patchsmith.project import Project  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERRORE") + "  " + name)


def silent(_):
    pass


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.expectations = []

    def current_time(self):
        return self.t

    def sleep(self, seconds):
        self.expectations.append(seconds)
        self.t += seconds


class Response:
    def __init__(self, body, kind="application/json; charset=UTF-8", url="https://translate.googleapis.com/x"):
        self.body, self.url = body, url
        self.headers = {"Content-Type": kind}

    def read(self, *_):
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def http_error(code, body=b"", url="https://translate.googleapis.com/x", header=None):
    return urllib.error.HTTPError(url, code, "errore", header or {}, io.BytesIO(body))


class Opener:
    """Fake urlopen: each call uses the function 'risposta(req)' and counts the requests."""

    def __init__(self, response, clock=None):
        self.response = response
        self.requests = []
        self.clock = clock

    def __call__(self, req, timeout):
        self.requests.append(req)
        if self.clock:
            self.clock.t += 1.0  # response time
        outcome = self.response(req)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def google_echo(req):
    """Fake Google that 'translates' by putting [it] in front of each line."""
    q = urllib.parse.parse_qs(req.data.decode("utf-8"))["q"][0]
    lines = q.split("\n")
    return Response(json.dumps([[["\n".join("[it] " + r for r in lines), q, None, None]]]).encode())


def fake_limiter(folder, service="google", clock=None, name="stato.json"):
    clock = clock or Clock()
    return limiter.Limiter(service, silent, os.path.join(folder, name), clock.current_time, clock.sleep,
                                 lambda: 0.5), clock


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    os.makedirs(os.path.join(project.PROJECT_FOLDER, "memory"))
    network_waits = []
    real_sleep = interruption.wait_for
    backend.interruption.wait_for = lambda s: network_waits.append(s)  # waits for network errors: fake

    # --- 1. windows and pauses over 200 requests
    lim, clock = fake_limiter(base, name="finestre.json")
    timestamps = []
    for _ in range(200):
        lim.before_request(3600)
        timestamps.append(clock.t)
        clock.t += 1.0
    def max_in(duration):
        return max(sum(1 for x in timestamps if t <= x < t + duration) for t in timestamps)
    check("limitatore: mai piu' di 8 richieste al minuto, 30 ogni 10 minuti, 60 all'ora",
              max_in(60) <= 8 and max_in(600) <= 30 and max_in(3600) <= 60)
    check("limitatore: almeno 4 s (+ variazione) tra due richieste",
              min(b - a for a, b in zip(timestamps, timestamps[1:])) >= 4.0)
    check("limitatore: caratteri per minuto entro il margine",
              max(sum(3600 for x in timestamps if t <= x < t + 60) for t in timestamps) <= 32_000)

    # --- 2. first 429 "Sorry": a single request, no waiting, pause remembered for 24 hours
    lim, clock = fake_limiter(base, name="blocco.json")
    opener = Opener(lambda req: http_error(429, b"<html><title>Sorry...</title>automated queries</html>"), clock)
    g = backend.GoogleFree(log=silent, opener=opener, limiter=lim)
    try:
        g.translate_lines(["hello", "world"], "en", "it")
        message = ""
    except backend.Blocked as e:
        message = str(e)
    state = json.load(open(os.path.join(base, "blocco.json"), encoding="utf-8"))["google"]["blocco"]
    check("Google 429 alla prima richiesta: una sola richiesta, nessuna attesa 30/60/120 s",
              len(opener.requests) == 1 and not network_waits and sum(clock.expectations) < 10)
    check("... blocco 'a freddo' ricordato per 24 ore, messaggio onesto (non 'troppe richieste')",
              state["tipo"] == "a_freddo" and abs(state["fino"] - state["dal"] - 86400) < 1
              and "non e' un limite superato" in message and "troppe richieste" not in message)
    lim2, _ = fake_limiter(base, name="blocco.json", clock=clock)
    opener2 = Opener(google_echo, clock)
    try:
        backend.GoogleFree(log=silent, opener=opener2, limiter=lim2).translate_lines(["hello"], "en", "it")
        pause = ""
    except backend.Blocked as e:
        pause = str(e)
    check("persistenza: un nuovo avvio durante la pausa non manda nessuna richiesta e dice fino a quando",
              not opener2.requests and "in pausa fino" in pause)
    clock.t += 86400 + 1
    check("dopo la pausa si riprova", backend.GoogleFree(log=silent, opener=opener2, limiter=lim2)
              .translate_lines(["hello"], "en", "it") == ["[it] hello"])

    # --- 3. levels: cold 1 -> 3 -> 7 days -> disabled; after traffic 1 hour
    lim, clock = fake_limiter(base, name="livelli.json")
    ends = []
    for _ in range(4):
        lim.blocked(429)
        s = json.load(open(os.path.join(base, "livelli.json"), encoding="utf-8"))["google"]["blocco"]
        ends.append((round((s["fino"] - s["dal"]) / 86400), s["disattivato"]))
        clock.t = s["fino"] + 1
    check("blocchi a freddo ripetuti: 1, 3, 7 giorni, poi disattivato finche' non lo riattivi tu",
              ends[:3] == [(1, False), (3, False), (7, False)] and ends[3][1] is True and lim.pause())
    lim.reset()
    check("riattivazione manuale ('smith servizi --azzera google')", lim.pause() is None)
    lim, clock = fake_limiter(base, name="traffico.json")
    for _ in range(3):
        lim.before_request(100)
        lim.ok()
    lim.blocked(429)
    s = json.load(open(os.path.join(base, "traffico.json"), encoding="utf-8"))["google"]["blocco"]
    check("blocco dopo richieste riuscite: 'dopo traffico', pausa di un'ora",
              s["tipo"] == "dopo_traffico" and abs(s["fino"] - s["dal"] - 3600) < 1)

    # --- 4. other signals: HTML instead of JSON, /sorry/, cookie consent, 400
    for name, response, expected in (
            ("pagina HTML con 200", lambda r: Response(b"<html>Sorry</html>", "text/html"), backend.Blocked),
            ("redirect a /sorry/", lambda r: Response(b"x", "text/html", "https://www.google.com/sorry/index"),
             backend.Blocked),
            ("consenso cookie", lambda r: Response(b"x", "text/html", "https://consent.google.com/m"),
             backend.BackendError),
            ("400 (lingue)", lambda r: http_error(400, b"bad"), backend.BackendError)):
        lim, clock = fake_limiter(base, name="segnali-%s.json" % len(name))
        try:
            backend.GoogleFree(log=silent, opener=Opener(response, clock), limiter=lim).translate_lines(
                ["hello"], "en", "it")
            acquired = None
        except backend.BackendError as e:
            acquired = type(e)
        check("segnale '%s' riconosciuto correttamente" % name, acquired is expected
                  and (expected is backend.Blocked) == bool(lim.pause()))

    # --- 5. misaligned lines: split in half, no burst
    lim, clock = fake_limiter(base, name="allinea.json")

    def misaligned(req):
        q = urllib.parse.parse_qs(req.data.decode("utf-8"))["q"][0]
        lines = q.split("\n")
        if len(lines) > 1:
            lines = lines[:-1]  # Google always merges two lines
        return Response(json.dumps([[["\n".join(lines), q, None, None]]]).encode())
    opener = Opener(misaligned, clock)
    outcome = backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(
        ["riga numero %d" % i for i in range(150)], "en", "it")
    check("righe non allineate: al massimo 15 richieste (non 151), le righe restano non tradotte",
              len(opener.requests) <= 15 and all(t == "" for t in outcome))

    # --- 6. network errors: 2 retries (10 s, 30 s) and then stop
    lim, clock = fake_limiter(base, name="rete.json")
    opener = Opener(lambda r: urllib.error.URLError("rete assente"), clock)
    del network_waits[:]
    try:
        backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(["hello"], "en", "it")
        acquired = False
    except backend.BackendError:
        acquired = True
    check("errori di rete: 3 tentativi (attese 10 e 30 s), poi stop", acquired and len(opener.requests) == 3
              and network_waits == [10, 30] and lim.pause() is None)

    # --- 7. Stop during a long pause of the limiter (real wait, interrupted)
    lim = limiter.Limiter("google", silent, os.path.join(base, "ferma.json"), sleep=real_sleep)
    lim.before_request(100)
    for _ in range(7):
        s = lim._read()
        s["google"]["richieste"].append([time.time(), 100])
        lim._write(s)
    threading.Timer(0.3, interruption.request_stop).start()
    start = time.monotonic()
    try:
        lim.before_request(100)
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    check("Ferma durante una pausa del limitatore: si ferma subito", stopped and time.monotonic() - start < 3)

    # --- 8. estimate
    lim, _ = fake_limiter(base, name="stima.json")
    forager = lim.estimate([3600] * 12)
    big = lim.estimate([3600] * 113)
    check("stima: Forager (~42.000 caratteri, 12 richieste) in pochi minuti; 406.000 caratteri oltre un'ora",
              60 <= forager <= 300 and big >= 3600)

    # --- 9. state file damaged or with absurd dates
    broken = os.path.join(base, "rotto.json")
    open(broken, "w").write("{non json")
    lim, clock = fake_limiter(base, name="rotto.json")
    lim.before_request(10)
    future = os.path.join(base, "futuro.json")
    json.dump({"google": {"richieste": [], "blocco": {"dal": 1, "fino": 4_000_000_000, "livello": 1,
                                                      "tipo": "a_freddo"}}}, open(future, "w"))
    lim, clock = fake_limiter(base, name="futuro.json")
    lim.pause()
    data = json.load(open(future, encoding="utf-8")) if os.path.exists(future) else {}
    check("stato rovinato: nessun errore; data assurda ridotta a 8 giorni",
              lim.pause() is not None and lim._own_state(lim._read(), clock.t)["blocco"]["fino"] <= clock.t + 8 * 86400
              and data is not None)

    # --- 10. two limiters on the same file count together (interface + command line)
    clock = Clock()
    a, _ = fake_limiter(base, name="condiviso.json", clock=clock)
    b, _ = fake_limiter(base, name="condiviso.json", clock=clock)
    a.before_request(100)
    b.before_request(100)
    check("stato condiviso: il secondo limitatore aspetta la pausa del primo", clock.expectations and
              clock.expectations[0] >= 4.0)

    # --- 11. DeepL
    lim, clock = fake_limiter(base, "deepl", name="deepl.json")
    received_calls = []

    def deepl_ok(req):
        received_calls.append((req.full_url, dict(req.header_items()), json.loads(req.data.decode("utf-8"))))
        texts = json.loads(req.data.decode("utf-8"))["text"]
        return Response(json.dumps({"translations": [{"text": "IT " + t} for t in texts]}).encode())
    d = backend.DeepL("abc-123:fx", log=silent, opener=Opener(deepl_ok, clock), limiter=lim)
    outcome = d.translate_lines(["one", "two", "three"], "en", "it")
    url, header, body = received_calls[0]
    check("DeepL: chiave gratuita -> api-free, chiave nell'header, lista di testi, lingue giuste",
              url.startswith("https://api-free.deepl.com/v2/translate") and outcome == ["IT one", "IT two", "IT three"]
              and header.get("Authorization") == "DeepL-Auth-Key abc-123:fx" and body["target_lang"] == "IT"
              and body["source_lang"] == "EN")
    check("DeepL: codici delle lingue (zh-TW, pt-BR, en)",
              backend._deepl_language("zh-TW", True) == "ZH-HANT" and backend._deepl_language("pt-BR", True) == "PT-BR"
              and backend._deepl_language("en", True) == "EN-US" and backend._deepl_language("zh-CN", False) == "ZH")
    for code, text in ((456, "finito"), (403, "chiave")):
        try:
            backend.DeepL("x:fx", log=silent, opener=Opener(lambda r, c=code: http_error(c), clock),
                          limiter=lim).translate_lines(["one"], "en", "it")
            message = ""
        except backend.BackendError as e:
            message = str(e)
        check("DeepL %d: messaggio chiaro (%s)" % (code, text), text in message)
    sequence = [http_error(429, header={"Retry-After": "2"}), None]

    def first_429(req):
        e = sequence.pop(0)
        return e if e else deepl_ok(req)
    del network_waits[:]
    check("DeepL 429: aspetta quanto chiede (Retry-After) e riprova",
              backend.DeepL("x:fx", log=silent, opener=Opener(first_429, clock), limiter=lim)
              .translate_lines(["one"], "en", "it") == ["IT one"] and network_waits == [2.0])
    try:
        backend.DeepL("", log=silent)
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL senza chiave: spiega come averla", "deepl.com" in message)

    # --- 12. MyMemory
    lim, clock = fake_limiter(base, "mymemory", name="mymemory.json")
    url_mm = []

    def mymemory(req):
        url_mm.append(req.full_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        return Response(json.dumps({"responseData": {"translatedText": "IT " + q["q"][0]},
                                    "responseStatus": 200, "quotaFinished": False}).encode())
    m = backend.MyMemory("io@example.com", log=silent, opener=Opener(mymemory, clock), limiter=lim)
    line_len = "Frase numero uno. " * 60
    outcome = m.translate_lines(["hello", line_len], "en", "it")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url_mm[0]).query)
    check("MyMemory: una richiesta per riga, email inviata, righe lunghe divise sotto i 500 byte",
              outcome[0] == "IT hello" and params.get("de") == ["io@example.com"] and params["langpair"] == ["en|it"]
              and all(len(urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["q"][0].encode()) <= 500
                      for u in url_mm) and len(url_mm) > 2)
    used_up = Opener(lambda r: Response(json.dumps({"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL "
                                                                                     "AVAILABLE FREE TRANSLATIONS"},
                                                 "responseStatus": 429, "quotaFinished": True}).encode()), clock)
    try:
        backend.MyMemory(None, log=silent, opener=used_up, limiter=lim).translate_lines(["hello"], "en", "it")
        message = ""
    except backend.Blocked as e:
        message = str(e)
    check("MyMemory: caratteri del giorno finiti -> stop chiaro e pausa fino a domani",
              "domani" in message and lim.pause() is not None)

    # --- 13. credentials from settings.json
    with open(os.path.join(project.PROJECT_FOLDER, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"chiave_deepl": "k:fx", "email_mymemory": "a@b.it"}, f)
    check("credenziali lette dalle impostazioni",
              backend.create_backend("deepl", silent).key == "k:fx"
              and backend.create_backend("mymemory", silent).email == "a@b.it")

    # --- 14. project: service paused -> no request; groups by characters; fallbacks in one request
    profile = os.path.join(base, "profilo.json")
    json.dump({"nome": "prova", "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
               "file": []}, open(profile, "w"))
    p = Project(profile, silent)
    lim, clock = fake_limiter(base, name="progetto.json")
    lim.blocked(429)
    opener = Opener(google_echo, clock)
    failed = p.translate_texts(["hello", "world"], backend.GoogleFree(log=silent, opener=opener, limiter=lim),
                              "en", "it")
    check("progetto: Google in pausa -> traduzione non avviata, zero richieste", failed == 2
              and not opener.requests)
    lim, clock = fake_limiter(base, name="progetto2.json")
    opener = Opener(google_echo, clock)
    texts = ["A sentence that is quite long, number %d, to fill the groups." % i for i in range(1690)]
    lines = []
    p.log = lines.append
    p.translate_texts(texts, backend.GoogleFree(log=silent, opener=opener, limiter=lim), "en", "it")
    check("progetto: gruppi da una richiesta ciascuno (circa 3.600 caratteri), stima mostrata prima",
              # ("  testi " lines: detail, only on disk)
              len(opener.requests) == len([r for r in lines if r.lstrip("").startswith("  testi ")])
              and all(len(urllib.parse.parse_qs(r.data.decode())["q"][0]) <= 4000 for r in opener.requests)
              and any("Richieste previste" in r and "tempo stimato" in r for r in lines))

    class Damager(backend.Backend):
        name = "rovina"

        def __init__(self):
            self.calls = 0

        def translate_lines(self, lines, origin, dest_lang):
            self.calls += 1
            return [r.replace("[[0]]", "[0]") + " it" for r in lines]  # damages the placeholders
    damager = Damager()
    p.translate_texts(["Hello {name}, welcome %d" % i for i in range(20)], damager, "en", "it")
    check("segnaposto rovinati: i ripieghi di tutto il gruppo in una sola richiesta (non una per testo)",
              damager.calls <= 2)

    # --- 15. cases found by the review
    line = "Thanks! " + " ".join("tester%d" % i for i in range(120)) + ". Bye now."
    check("righe lunghe: divise senza cambiare l'ordine delle frasi",
              " ".join(backend._split_long(line, 480)) == line and " ".join(backend._split_long(line, 100)) == line)
    mixed = "あ" * 170 + " Press A to continue." + "い" * 200
    check("MyMemory: pezzi sempre entro 480 byte anche con giapponese + inglese",
              all(len(p.encode("utf-8")) <= 480 for p in backend._split_bytes(mixed)))
    lim, clock = fake_limiter(base, name="orologio.json")
    lim.blocked(429, now=clock.t + 365 * 86400)  # clock one year ahead when the block arrives
    lim.pause()
    clock.t += 9 * 86400
    check("blocco registrato con l'orologio avanti: dura al massimo 8 giorni, poi finisce davvero",
              lim.pause() is None)
    clean_deepl = backend.DeepL("abcd-1234\r\nefgh:fx", log=silent)
    try:
        backend.DeepL("abcd-SEGRETA:fxé", log=silent)
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL: chiave incollata con un a capo ripulita; caratteri strani -> errore senza mostrarla",
              clean_deepl.key == "abcd-1234efgh:fx" and "non validi" in message and "SEGRETA" not in message)

    def broken_urlopen(req, timeout):
        raise ValueError("Invalid header value b'DeepL-Auth-Key abcd-SEGRETA:fx'")
    lim, clock = fake_limiter(base, "deepl", name="deepl2.json")
    del network_waits[:]
    try:
        backend.DeepL("abcd-SEGRETA:fx", log=silent, opener=broken_urlopen,
                      limiter=lim).translate_lines(["one"], "en", "it")
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL: errore nel preparare la richiesta -> nessun nuovo tentativo e chiave mai nel messaggio",
              message and "SEGRETA" not in message and not network_waits)
    for name, response in (("503 con pagina HTML", http_error(503, b"<html>Our systems have detected unusual traffic",
                                                               header={"Content-Type": "text/html"})),
                           ("403 senza HTML", http_error(403, b"Forbidden"))):
        lim, clock = fake_limiter(base, name="html-%d.json" % len(name))
        opener = Opener(lambda r, x=response: x, clock)
        try:
            backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(["hello"], "en", "it")
            acquired = None
        except backend.BackendError as e:
            acquired = type(e)
        check("Google %s: blocco, una sola richiesta" % name, acquired is backend.Blocked and len(opener.requests) == 1)
    lim, clock = fake_limiter(base, name="formato.json")
    try:
        backend.GoogleFree(log=silent, opener=Opener(lambda r: Response(b'{"x": 1}'), clock),
                             limiter=lim).translate_lines(["hello"], "en", "it")
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("Google: risposta in un formato inatteso -> stop con messaggio", "formato inatteso" in message)

    # MyMemory: quota used up halfway through a group -> the lines already translated stay in the translation memory
    lim, clock = fake_limiter(base, "mymemory", name="mm-quota.json")
    count = [0]

    def quota_after_3(req):
        count[0] += 1
        if count[0] > 3:
            return Response(json.dumps({"responseData": {"translatedText": "MYMEMORY WARNING"},
                                        "responseStatus": 429, "quotaFinished": True}).encode())
        return mymemory(req)
    p.log = silent
    p.memory = {}
    p.translate_texts(["frase %d" % i for i in range(8)],
                    backend.MyMemory(None, log=silent, opener=Opener(quota_after_3, clock), limiter=lim), "en", "it")
    check("MyMemory: quota finita a meta' gruppo -> le 3 frasi gia' tradotte restano salvate",
              sorted(p.memory) == ["frase 0", "frase 1", "frase 2"])
    check("MyMemory: quota finita senza email -> con l'email si puo' continuare subito",
              backend.MyMemory("io@example.com", log=silent, limiter=lim).pause() is None)
    lines = []
    p.log = lines.append
    lim, clock = fake_limiter(base, "mymemory", name="mm-stima.json")
    p.translate_texts(["frase lunga numero %d con parecchie parole dentro" % i for i in range(150)],
                    backend.MyMemory(None, log=silent, opener=Opener(mymemory, clock), limiter=lim), "en", "it")
    check("MyMemory: richieste previste = una per riga, avviso se si supera la quota del giorno",
              any("Richieste previste: circa 150" in r for r in lines)
              and any("al giorno" in r for r in lines))
    lim, clock = fake_limiter(base, name="messaggio.json")
    lim.before_request(10)
    lim.ok()
    clock.t += 3 * 3600
    lim2, _ = fake_limiter(base, name="messaggio.json", clock=clock)
    check("messaggio di blocco senza 'dopo 0 traduzioni'", "dopo 0" not in lim2.blocked(429))

    from patchsmith import cli
    cli_profile = os.path.join(project.PROJECT_FOLDER, "profilo-cli.json")
    json.dump({"nome": "cli", "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
               "file": [], "backend": "deepl"}, open(cli_profile, "w"))
    os.remove(os.path.join(project.PROJECT_FOLDER, "settings.json"))
    os.environ.pop("PATCHSMITH_DEEPL", None)
    try:
        code = cli.main(["traduci", cli_profile, "--backend", "deepl"])
    except Exception:
        code = "eccezione"
    check("riga di comando: DeepL senza chiave -> errore spiegato (uscita 1), niente traceback", code == 1)

    # --- "Check" button: DeepL key (monthly usage, no text) and MyMemory email
    asked = []

    def fake_usage(req):
        asked.append(req.full_url)
        return Response(json.dumps({"character_count": 1234, "character_limit": 500000}).encode())
    ok, message = backend.verify_translator("deepl", "abc-123:fx", opener=Opener(fake_usage))
    check("Verifica DeepL: chiave buona -> consumo del mese (1.234 su 500.000), con /v2/usage",
              ok and "1.234" in message and "500.000" in message and asked == ["https://api-free.deepl.com/v2/usage"])
    ok, message = backend.verify_translator("deepl", "sbagliata:fx", opener=Opener(lambda r: http_error(403)))
    check("Verifica DeepL: chiave rifiutata -> spiegato (HTTP 403)", not ok and "non accetta la chiave" in message)
    ok, message = backend.verify_translator("mymemory", "nome@", opener=Opener(lambda r: 1 / 0))
    check("Verifica MyMemory: email scritta male -> detto subito, senza rete", not ok and "non sembra" in message)
    response_mm = Response(json.dumps({"responseStatus": 200, "responseData": {"translatedText": "Ciao"}}).encode())
    ok, message = backend.verify_translator("mymemory", "nome@esempio.it", opener=Opener(lambda r: response_mm))
    check("Verifica MyMemory: email accettata -> 50.000 caratteri al giorno, nessun account",
              ok and "50.000" in message)
    ok, message = backend.verify_translator("mymemory", "", opener=Opener(lambda r: OSError("rete")))
    check("Verifica MyMemory senza rete: spiegato, niente eccezioni",
              not ok and ("connessione" in message or "non risponde" in message))

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
