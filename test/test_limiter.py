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
    print(("OK    " if condition else "ERROR ") + "  " + name)


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
    return urllib.error.HTTPError(url, code, "error", header or {}, io.BytesIO(body))


class Opener:
    """Fake urlopen: each call uses the function 'response(req)' and counts the requests."""

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


def fake_limiter(folder, service="google", clock=None, name="state.json"):
    clock = clock or Clock()
    return limiter.Limiter(service, silent, os.path.join(folder, name), clock.current_time, clock.sleep,
                                 lambda: 0.5), clock


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base)
    project.PROJECT_FOLDER = os.path.join(base, "patchsmith")
    project.DATA_ROOT = project.PROJECT_FOLDER  # the user's data here too
    os.makedirs(os.path.join(project.PROJECT_FOLDER, "memory"))
    network_waits = []
    real_sleep = interruption.wait_for
    backend.interruption.wait_for = lambda s: network_waits.append(s)  # waits for network errors: fake

    # --- 1. windows and pauses over 200 requests
    lim, clock = fake_limiter(base, name="windows.json")
    timestamps = []
    for _ in range(200):
        lim.before_request(3600)
        timestamps.append(clock.t)
        clock.t += 1.0
    def max_in(duration):
        return max(sum(1 for x in timestamps if t <= x < t + duration) for t in timestamps)
    check("limiter: never more than 8 requests per minute, 30 every 10 minutes, 60 per hour",
              max_in(60) <= 8 and max_in(600) <= 30 and max_in(3600) <= 60)
    check("limiter: at least 4 s (+ jitter) between two requests",
              min(b - a for a, b in zip(timestamps, timestamps[1:])) >= 4.0)
    check("limiter: characters per minute within the margin",
              max(sum(3600 for x in timestamps if t <= x < t + 60) for t in timestamps) <= 32_000)

    # --- 2. first 429 "Sorry": a single request, no waiting, pause remembered for 24 hours
    lim, clock = fake_limiter(base, name="block.json")
    opener = Opener(lambda req: http_error(429, b"<html><title>Sorry...</title>automated queries</html>"), clock)
    g = backend.GoogleFree(log=silent, opener=opener, limiter=lim)
    try:
        g.translate_lines(["hello", "world"], "en", "it")
        message = ""
    except backend.Blocked as e:
        message = str(e)
    state = json.load(open(os.path.join(base, "block.json"), encoding="utf-8"))["google"]["block"]
    check("Google 429 on the first request: a single request, no 30/60/120 s wait",
              len(opener.requests) == 1 and not network_waits and sum(clock.expectations) < 10)
    check("... 'cold' block remembered for 24 hours, honest message (not 'too many requests')",
              state["kind"] == "cold" and abs(state["until"] - state["since"] - 86400) < 1
              and "it's not an exceeded limit" in message and "too many requests" not in message)
    lim2, _ = fake_limiter(base, name="block.json", clock=clock)
    opener2 = Opener(google_echo, clock)
    try:
        backend.GoogleFree(log=silent, opener=opener2, limiter=lim2).translate_lines(["hello"], "en", "it")
        pause = ""
    except backend.Blocked as e:
        pause = str(e)
    check("persistence: a new start during the pause sends no request and says until when",
              not opener2.requests and "is paused until" in pause)
    clock.t += 86400 + 1
    check("after the pause it tries again", backend.GoogleFree(log=silent, opener=opener2, limiter=lim2)
              .translate_lines(["hello"], "en", "it") == ["[it] hello"])

    # --- 3. levels: cold 1 -> 3 -> 7 days -> disabled; after traffic 1 hour
    lim, clock = fake_limiter(base, name="levels.json")
    ends = []
    for _ in range(4):
        lim.blocked(429)
        s = json.load(open(os.path.join(base, "levels.json"), encoding="utf-8"))["google"]["block"]
        ends.append((round((s["until"] - s["since"]) / 86400), s["disabled"]))
        clock.t = s["until"] + 1
    check("repeated cold blocks: 1, 3, 7 days, then disabled until you re-enable it",
              ends[:3] == [(1, False), (3, False), (7, False)] and ends[3][1] is True and lim.pause())
    lim.reset()
    check("manual re-enabling ('smith services --reset google')", lim.pause() is None)
    lim, clock = fake_limiter(base, name="traffic.json")
    for _ in range(3):
        lim.before_request(100)
        lim.ok()
    lim.blocked(429)
    s = json.load(open(os.path.join(base, "traffic.json"), encoding="utf-8"))["google"]["block"]
    check("block after successful requests: 'after traffic', one-hour pause",
              s["kind"] == "after_traffic" and abs(s["until"] - s["since"] - 3600) < 1)

    # --- 4. other signals: HTML instead of JSON, /sorry/, cookie consent, 400
    for name, response, expected in (
            ("HTML page, code 200", lambda r: Response(b"<html>Sorry</html>", "text/html"), backend.Blocked),
            ("redirect to /sorry", lambda r: Response(b"x", "text/html", "https://www.google.com/sorry/index"),
             backend.Blocked),
            ("consent webpage", lambda r: Response(b"x", "text/html", "https://consent.google.com/m"),
             backend.BackendError),
            ("400 (locale)", lambda r: http_error(400, b"bad"), backend.BackendError)):
        lim, clock = fake_limiter(base, name="signals-%s.json" % len(name))
        try:
            backend.GoogleFree(log=silent, opener=Opener(response, clock), limiter=lim).translate_lines(
                ["hello"], "en", "it")
            acquired = None
        except backend.BackendError as e:
            acquired = type(e)
        check("signal '%s' recognized correctly" % name, acquired is expected
                  and (expected is backend.Blocked) == bool(lim.pause()))

    # --- 5. misaligned lines: split in half, no burst
    lim, clock = fake_limiter(base, name="align.json")

    def misaligned(req):
        q = urllib.parse.parse_qs(req.data.decode("utf-8"))["q"][0]
        lines = q.split("\n")
        if len(lines) > 1:
            lines = lines[:-1]  # Google always merges two lines
        return Response(json.dumps([[["\n".join(lines), q, None, None]]]).encode())
    opener = Opener(misaligned, clock)
    outcome = backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(
        ["riga numero %d" % i for i in range(150)], "en", "it")
    check("misaligned lines: at most 15 requests (not 151), the lines stay untranslated",
              len(opener.requests) <= 15 and all(t == "" for t in outcome))

    # --- 6. network errors: 2 retries (10 s, 30 s) and then stop
    lim, clock = fake_limiter(base, name="network.json")
    opener = Opener(lambda r: urllib.error.URLError("network down"), clock)
    del network_waits[:]
    try:
        backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(["hello"], "en", "it")
        acquired = False
    except backend.BackendError:
        acquired = True
    check("network errors: 3 attempts (waits of 10 and 30 s), then stop", acquired and len(opener.requests) == 3
              and network_waits == [10, 30] and lim.pause() is None)

    # --- 7. Stop during a long pause of the limiter (real wait, interrupted)
    lim = limiter.Limiter("google", silent, os.path.join(base, "stop.json"), sleep=real_sleep)
    lim.before_request(100)
    for _ in range(7):
        s = lim._read()
        s["google"]["requests"].append([time.time(), 100])
        lim._write(s)
    threading.Timer(0.3, interruption.request_stop).start()
    start = time.monotonic()
    try:
        lim.before_request(100)
        stopped = False
    except interruption.Interrupted:
        stopped = True
    interruption.reset()
    check("Stop during a limiter pause: it stops at once", stopped and time.monotonic() - start < 3)

    # --- 8. estimate
    lim, _ = fake_limiter(base, name="estimate.json")
    forager = lim.estimate([3600] * 12)
    big = lim.estimate([3600] * 113)
    check("estimate: Forager (~42,000 characters, 12 requests) in a few minutes; 406,000 characters over an hour",
              60 <= forager <= 300 and big >= 3600)

    # --- 9. state file damaged or with absurd dates
    broken = os.path.join(base, "broken.json")
    open(broken, "w").write("{not json")
    lim, clock = fake_limiter(base, name="broken.json")
    lim.before_request(10)
    future = os.path.join(base, "future.json")
    json.dump({"google": {"requests": [], "block": {"since": 1, "until": 4_000_000_000, "level": 1,
                                                      "kind": "cold"}}}, open(future, "w"))
    lim, clock = fake_limiter(base, name="future.json")
    lim.pause()
    data = json.load(open(future, encoding="utf-8")) if os.path.exists(future) else {}
    check("damaged state: no error; absurd date reduced to 8 days",
              lim.pause() is not None and lim._own_state(lim._read(), clock.t)["block"]["until"] <= clock.t + 8 * 86400
              and data is not None)

    # --- 10. two limiters on the same file count together (interface + command line)
    clock = Clock()
    a, _ = fake_limiter(base, name="shared.json", clock=clock)
    b, _ = fake_limiter(base, name="shared.json", clock=clock)
    a.before_request(100)
    b.before_request(100)
    check("shared state: the second limiter waits for the pause of the first", clock.expectations and
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
    check("DeepL: free key -> api-free, key in the header, list of texts, right languages",
              url.startswith("https://api-free.deepl.com/v2/translate") and outcome == ["IT one", "IT two", "IT three"]
              and header.get("Authorization") == "DeepL-Auth-Key abc-123:fx" and body["target_lang"] == "IT"
              and body["source_lang"] == "EN")
    check("DeepL: language codes (zh-TW, pt-BR, en)",
              backend._deepl_language("zh-TW", True) == "ZH-HANT" and backend._deepl_language("pt-BR", True) == "PT-BR"
              and backend._deepl_language("en", True) == "EN-US" and backend._deepl_language("zh-CN", False) == "ZH")
    for code, text, expected_text in ((456, "quota used up", "used up"), (403, "key", "doesn't accept the key")):
        try:
            backend.DeepL("x:fx", log=silent, opener=Opener(lambda r, c=code: http_error(c), clock),
                          limiter=lim).translate_lines(["one"], "en", "it")
            message = ""
        except backend.BackendError as e:
            message = str(e)
        check("DeepL %d: clear message (%s)" % (code, text), expected_text in message)
    sequence = [http_error(429, header={"Retry-After": "2"}), None]

    def first_429(req):
        e = sequence.pop(0)
        return e if e else deepl_ok(req)
    del network_waits[:]
    check("DeepL 429: waits as long as asked (Retry-After) and tries again",
              backend.DeepL("x:fx", log=silent, opener=Opener(first_429, clock), limiter=lim)
              .translate_lines(["one"], "en", "it") == ["IT one"] and network_waits == [2.0])
    try:
        backend.DeepL("", log=silent)
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL without a key: explains how to get one", "deepl.com" in message)

    # --- 12. MyMemory
    lim, clock = fake_limiter(base, "mymemory", name="mymemory.json")
    url_mm = []

    def mymemory(req):
        url_mm.append(req.full_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        return Response(json.dumps({"responseData": {"translatedText": "IT " + q["q"][0]},
                                    "responseStatus": 200, "quotaFinished": False}).encode())
    m = backend.MyMemory("me@example.com", log=silent, opener=Opener(mymemory, clock), limiter=lim)
    line_len = "Frase numero uno. " * 60
    outcome = m.translate_lines(["hello", line_len], "en", "it")
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url_mm[0]).query)
    check("MyMemory: one request per line, email sent, long lines split under 500 bytes",
              outcome[0] == "IT hello" and params.get("de") == ["me@example.com"] and params["langpair"] == ["en|it"]
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
    check("MyMemory: characters of the day used up -> clear stop and pause until tomorrow",
              "tomorrow" in message and lim.pause() is not None)

    # --- 13. credentials from settings.json
    with open(os.path.join(project.PROJECT_FOLDER, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"deepl_key": "k:fx", "email_mymemory": "a@b.it"}, f)
    check("credentials read from the settings",
              backend.create_backend("deepl", silent).key == "k:fx"
              and backend.create_backend("mymemory", silent).email == "a@b.it")

    # --- 14. project: service paused -> no request; groups by characters; fallbacks in one request
    profile = os.path.join(base, "profile.json")
    json.dump({"name": "test", "game_folder": base, "source_language": "en", "target_language": "it",
               "file": []}, open(profile, "w"))
    p = Project(profile, silent)
    lim, clock = fake_limiter(base, name="project.json")
    lim.blocked(429)
    opener = Opener(google_echo, clock)
    failed = p.translate_texts(["hello", "world"], backend.GoogleFree(log=silent, opener=opener, limiter=lim),
                              "en", "it")
    check("project: Google paused -> translation not started, zero requests", failed == 2
              and not opener.requests)
    lim, clock = fake_limiter(base, name="project2.json")
    opener = Opener(google_echo, clock)
    texts = ["A sentence that is quite long, number %d, to fill the groups." % i for i in range(1690)]
    lines = []
    p.log = lines.append
    p.translate_texts(texts, backend.GoogleFree(log=silent, opener=opener, limiter=lim), "en", "it")
    check("project: groups of one request each (about 3,600 characters), estimate shown first",
              # ("  texts " lines: detail, only on disk)
              len(opener.requests) == len([r for r in lines if r.lstrip("").startswith("  texts ")])
              and all(len(urllib.parse.parse_qs(r.data.decode())["q"][0]) <= 4000 for r in opener.requests)
              and any("Expected requests" in r and "estimated time" in r for r in lines))

    class Damager(backend.Backend):
        name = "damager"

        def __init__(self):
            self.calls = 0

        def translate_lines(self, lines, origin, dest_lang):
            self.calls += 1
            return [r.replace("[[0]]", "[0]") + " it" for r in lines]  # damages the placeholders
    damager = Damager()
    p.translate_texts(["Hello {name}, welcome %d" % i for i in range(20)], damager, "en", "it")
    check("damaged placeholders: the fallbacks of the whole group in a single request (not one per text)",
              damager.calls <= 2)

    # --- 15. cases found by the review
    line = "Thanks! " + " ".join("tester%d" % i for i in range(120)) + ". Bye now."
    check("long lines: split without changing the order of the sentences",
              " ".join(backend._split_long(line, 480)) == line and " ".join(backend._split_long(line, 100)) == line)
    mixed = "あ" * 170 + " Press A to continue." + "い" * 200
    check("MyMemory: pieces always within 480 bytes even with Japanese + English",
              all(len(p.encode("utf-8")) <= 480 for p in backend._split_bytes(mixed)))
    lim, clock = fake_limiter(base, name="clock.json")
    lim.blocked(429, now=clock.t + 365 * 86400)  # clock one year ahead when the block arrives
    lim.pause()
    clock.t += 9 * 86400
    check("block recorded with the clock ahead: lasts at most 8 days, then really ends",
              lim.pause() is None)
    clean_deepl = backend.DeepL("abcd-1234\r\nefgh:fx", log=silent)
    try:
        backend.DeepL("abcd-SECRET:fxé", log=silent)
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL: key pasted with a line break cleaned up; strange characters -> error without showing it",
              clean_deepl.key == "abcd-1234efgh:fx" and "invalid characters" in message and "SECRET" not in message)

    def broken_urlopen(req, timeout):
        raise ValueError("Invalid header value b'DeepL-Auth-Key abcd-SECRET:fx'")
    lim, clock = fake_limiter(base, "deepl", name="deepl2.json")
    del network_waits[:]
    try:
        backend.DeepL("abcd-SECRET:fx", log=silent, opener=broken_urlopen,
                      limiter=lim).translate_lines(["one"], "en", "it")
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("DeepL: error preparing the request -> no new attempt and the key never in the message",
              message and "SECRET" not in message and not network_waits)
    for name, response in (("503 (HTML web page)", http_error(503, b"<html>Our systems have detected unusual traffic",
                                                               header={"Content-Type": "text/html"})),
                           ("403 (non-HTML)", http_error(403, b"Forbidden"))):
        lim, clock = fake_limiter(base, name="html-%d.json" % len(name))
        opener = Opener(lambda r, x=response: x, clock)
        try:
            backend.GoogleFree(log=silent, opener=opener, limiter=lim).translate_lines(["hello"], "en", "it")
            acquired = None
        except backend.BackendError as e:
            acquired = type(e)
        check("Google %s: block, a single request" % name, acquired is backend.Blocked and len(opener.requests) == 1)
    lim, clock = fake_limiter(base, name="format.json")
    try:
        backend.GoogleFree(log=silent, opener=Opener(lambda r: Response(b'{"x": 1}'), clock),
                             limiter=lim).translate_lines(["hello"], "en", "it")
        message = ""
    except backend.BackendError as e:
        message = str(e)
    check("Google: response in an unexpected format -> stop with a message", "unexpected format" in message)

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
    check("MyMemory: quota used up halfway through a group -> the 3 sentences already translated stay saved",
              sorted(p.memory) == ["frase 0", "frase 1", "frase 2"])
    check("MyMemory: quota used up without email -> with the email you can continue at once",
              backend.MyMemory("me@example.com", log=silent, limiter=lim).pause() is None)
    lines = []
    p.log = lines.append
    lim, clock = fake_limiter(base, "mymemory", name="mm-estimate.json")
    p.translate_texts(["frase lunga numero %d con parecchie parole dentro" % i for i in range(150)],
                    backend.MyMemory(None, log=silent, opener=Opener(mymemory, clock), limiter=lim), "en", "it")
    check("MyMemory: expected requests = one per line, warning if the daily quota is exceeded",
              any("Expected requests: about 150" in r for r in lines)
              and any("per day" in r for r in lines))
    lim, clock = fake_limiter(base, name="message.json")
    lim.before_request(10)
    lim.ok()
    clock.t += 3 * 3600
    lim2, _ = fake_limiter(base, name="message.json", clock=clock)
    check("block message without 'after 0 translations'", "after 0" not in lim2.blocked(429))

    from patchsmith import cli
    cli_profile = os.path.join(project.PROJECT_FOLDER, "profile-cli.json")
    json.dump({"name": "cli", "game_folder": base, "source_language": "en", "target_language": "it",
               "file": [], "backend": "deepl"}, open(cli_profile, "w"))
    os.remove(os.path.join(project.PROJECT_FOLDER, "settings.json"))
    os.environ.pop("PATCHSMITH_DEEPL", None)
    try:
        code = cli.main(["translate", cli_profile, "--backend", "deepl"])
    except Exception:
        code = "exception"
    check("command line: DeepL without a key -> explained error (exit 1), no traceback", code == 1)

    # --- "Check" button: DeepL key (monthly usage, no text) and MyMemory email
    asked = []

    def fake_usage(req):
        asked.append(req.full_url)
        return Response(json.dumps({"character_count": 1234, "character_limit": 500000}).encode())
    ok, message = backend.verify_translator("deepl", "abc-123:fx", opener=Opener(fake_usage))
    check("Check DeepL: good key -> usage of the month (1,234 of 500,000), with /v2/usage",
              ok and "1,234" in message and "500,000" in message and asked == ["https://api-free.deepl.com/v2/usage"])
    ok, message = backend.verify_translator("deepl", "wrong:fx", opener=Opener(lambda r: http_error(403)))
    check("Check DeepL: key rejected -> explained (HTTP 403)", not ok and "doesn't accept the key" in message)
    ok, message = backend.verify_translator("mymemory", "name@", opener=Opener(lambda r: 1 / 0))
    check("Check MyMemory: badly written email -> said at once, without network", not ok and "doesn't seem" in message)
    response_mm = Response(json.dumps({"responseStatus": 200, "responseData": {"translatedText": "Ciao"}}).encode())
    ok, message = backend.verify_translator("mymemory", "name@example.it", opener=Opener(lambda r: response_mm))
    check("Check MyMemory: email accepted -> 50,000 characters per day, no account",
              ok and "50,000" in message)
    ok, message = backend.verify_translator("mymemory", "", opener=Opener(lambda r: OSError("network")))
    check("Check MyMemory without network: explained, no exceptions",
              not ok and ("connection" in message or "not responding" in message))

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
