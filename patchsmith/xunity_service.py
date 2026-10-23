"""Translator of new lines for XUnity.AutoTranslator: a small server on the PC (127.0.0.1 only) that answers like
XUnity's "CustomTranslate" endpoint (GET /translate?from=en&to=it&text=... -> only the translated text).

All the lines the game shows during play that are not already translated (e.g. "Hold RMB {{A}}s to leap up to
{{B}} m": XUnity removes the numbers and puts them back afterwards) go through here, whatever translator was chosen:
the game's translation memory and the shared one apply, as do the checks on translations and the pauses of the online
translators (PatchSmith does not insist with a service that has blocked it and does not wait more than MAX_WAIT for
its cautious margins). The translators are tried in a row (expected_translators): the local translator if it is
installed (the Fast model, if present: the graphics card is needed by the game too), then the chosen online one
(DeepL with the key, MyMemory), then free Google if it is not paused.

XUnity stops using an endpoint after 5 errors in a row: that is why a line that no translator can translate well is
NOT an error. The answer (200) is the best translation if it has the {{A}} placeholders intact and is in the right
language ("rilassata"), otherwise the original ("rifiutata"); a line that an online translator could send only much
later ("rinviata") also gets the original. Error (500) only if no translator answers, 503 while the game is closing
(no translation with the translator that is shutting down).

Every requested line ends up in the journal memory/<gioco>.requests.jsonl (see journal_path): a JSON line with ts,
testo, da, a, esito (JOURNAL_OUTCOMES), backend, motivo and risposta. Lines with an outcome other than "ok" are not in
the translation memory: they can be re-translated with the game closed and fixed in XUnity's cache.

PatchSmith's plugin starts it when the game starts (BepInEx/plugins/Patchsmith) and it closes by itself when the
game closes, freeing the graphics card."""
import collections
import ctypes
import datetime
import http.server
import json
import os
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
import zlib

from . import shared_file, interruption, language
from .backend import BackendError, _unchanged, translation_problems
from .project import Project, _to_translate, data_folder, capitalize_like
from .text import reassemble_lines, restore_placeholders

URL_PATH = "/translate"
GROUP_URL_PATH = "/traduci-gruppo"  # several lines together (live translation of web games)
MAX_GROUP = 60
MAX_BODY = 1_000_000
GAME_CHECK = 3.0  # seconds between one check of the game's process and the next
MAX_WAIT = 60.0  # seconds: XUnity waits at most 150 s for an answer, the online translators sometimes longer
MAX_STARTUP = 140.0  # seconds of waiting for translators that are starting, then the line is postponed
MAX_CLOSING = 180.0  # seconds: on closing, a translation in progress (even with the online waits) finishes first
MAX_JOURNAL = 2_000_000  # bytes: then the journal becomes <nome>.requests.jsonl.1 and a new one is started
# outcomes in the journal: translated (and saved in the translation memory); fallback answer (not in the translation
# memory); original because rejected by all translators; original because the online translators ask to wait too
# long; no translator reachable (500); game closing (503)
JOURNAL_OUTCOMES = ("ok", "rilassata", "rifiutata", "rinviata", "errore", "interrotta")
NAMES = {"locale": "local translator", "google": "Google", "deepl": "DeepL", "mymemory": "MyMemory",
        "finto": "test translator"}
_XUNITY = re.compile(r"\{\{[^{}]*\}\}")  # XUnity's placeholders ({{A}}): they must all come back
# problems (backend.translation_problems) saying that the answer is not a translation in the target language
_UNTRANSLATED = {"vuota", "wrong script", "letters from another language", "not translated", "model refusal"}
# frequent words of the target language that in the lists of language._WORDS are only among those of other languages:
# in an Italian answer "se", "tu", "su", "no" do not mean that it is Spanish (see language.obvious_language)
_ALSO_IN_TARGET = language.ALSO_IN


def port_for(profile_name):
    """Fixed port for each game (47000-47999): two games do not swap translations."""
    return 47000 + zlib.crc32(profile_name.encode("utf-8")) % 1000


def address(port):
    return "http://127.0.0.1:%d%s" % (port, URL_PATH)


def is_on(profile_name, wait=0.3):
    """True if this game's translator is listening (the game is open and has started it)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/patchsmith" % port_for(profile_name), timeout=wait) as r:
            return r.read().decode("utf-8", "replace") == "patchsmith " + profile_name
    except (OSError, ValueError):
        return False


def stop(profile_name):
    """Stops this game's translator (it restarts with the game). True if it was running."""
    if not is_on(profile_name):
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ferma" % port_for(profile_name), timeout=5) as r:
            r.read()
        return True
    except (OSError, ValueError):
        return False


def journal_path(profile_name):
    """The journal of the lines requested during play (one JSON line per line of text), among the user's data."""
    return data_folder("memory", "%s.richieste.jsonl" % profile_name)


def read_journal(profile_name):
    """The journal's lines (the old one's too, .1), oldest first: [{ts, testo, esito, risposta...}]. Unreadable lines
    (e.g. half written by a game closed abruptly) are skipped."""
    lines = []
    for path in (journal_path(profile_name) + ".1", journal_path(profile_name)):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(data, dict) and isinstance(data.get("testo"), str):
                        lines.append(data)
        except OSError:
            continue
    return lines


class Deferred(BackendError):
    """An online translator asks to wait more than MAX_WAIT to stay within the cautious margins: XUnity does not
    wait that long, the line is postponed (answer with the original, in the journal as 'rinviata')."""


def _short_wait(seconds):
    """Instead of the limiter's wait (which can last minutes): a short one is waited, a long one is not."""
    if seconds > MAX_WAIT:
        raise Deferred("to remain within safe margins, the next request may only begin in %d seconds" % seconds)
    interruption.wait_for(seconds)


def _settings():
    try:
        with open(data_folder("settings.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _chosen_online(profile):
    """DeepL or MyMemory, if they are the translator chosen for the game or the one chosen in the interface."""
    selected = []
    for name in (profile.get("backend"), _settings().get("backend")):
        if name in ("deepl", "mymemory") and name not in selected:
            selected.append(name)
    return selected


def expected_translators(profile):
    """The translators the service would use now, in order: 'locale' if it is installed, the chosen online translator
    (DeepL only with the key) if it is not paused, 'google' if it is not paused. Empty list: none."""
    from . import ollama
    from .backend import credentials
    from .limiter import Limiter
    names = []
    try:
        if ollama.state() == "pronto":
            names.append("locale")
    except OSError:
        pass
    for name in _chosen_online(profile):
        if name == "deepl" and not credentials()["chiave_deepl"]:
            continue
        if Limiter(name, log=None).pause() is None:
            names.append(name)
    if Limiter("google", log=None).pause() is None:
        names.append("google")
    return names


def create_translator(name, log=print):
    """A translator of the row, set up to translate during play: the Fast model (4B) if present, short waits for the
    online translators (see _short_wait) and a single retry after a network error."""
    from . import ollama
    from .backend import Local, create_backend
    if name == "locale":
        if ollama.state() != "pronto":
            raise BackendError("The local translator is not installed.")
        return Local("4b" if ollama.model_present("4b") else ollama.default_model(), log=log)
    translator = create_backend(name, log)
    if hasattr(translator, "limiter"):
        translator.limiter.sleep = _short_wait
    if hasattr(translator, "NETWORK_WAITS"):  # Google
        translator.NETWORK_WAITS = (5,)
    if hasattr(translator, "WAITS"):  # DeepL: even when it asks to wait (Retry-After) it retries soon, only
        translator.WAITS = (5,)  # once, then it moves on to the next translator
        translator.LONGEST_WAIT = 5.0
    return translator


def other_language(original, translated, origin, dest_lang):
    """True if 'tradotto' is not a translation in the target language: empty, equal to the original, refused by the
    model, with another script or letters of another language, or with the frequent words of another language."""
    if set(translation_problems(original, translated, dest_lang)) & _UNTRANSLATED:
        return True
    if _unchanged(original, translated) and re.search(r"[^\W\d_]", _XUNITY.sub(" ", original)):
        return True  # left equal to the original (even with a single frequent word: "Hold {{A}}s to leap")
    return language.obvious_language(_XUNITY.sub(" ", translated), dest_lang) is not None


class _NoClose:
    """The translator stays on between one line and the next: Project would close it at the end of each translation
    (chiudi) and would make time estimates (stima), which are not needed for one line at a time. The service checks
    the pause before trying it."""

    def __init__(self, backend):
        self._backend = backend

    def __getattr__(self, name):
        if name in ("close", "estimate", "requests", "pause"):
            raise AttributeError(name)
        return getattr(self._backend, name)


def process_alive(pid):
    """True if the process still exists (Windows; if it cannot be checked it is considered alive)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    h = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5  # access denied: it exists, but belongs to another user
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(h))


class _HttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows it would let two servers take the same port

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """POST /traduci-gruppo {"from", "to", "testi": [...]} -> {"traduzioni": {testo: traduzione}, "riprova": [...]}:
        several lines together, for the live translation of web games (adapter_web_live)."""
        service = self.server.service
        if urllib.parse.urlsplit(self.path).path != GROUP_URL_PATH:
            return self._reply(404, "")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(length, MAX_BODY)).decode("utf-8"))
            texts = [t for t in data.get("testi", []) if isinstance(t, str)][:MAX_GROUP]
            src_lang, a = str(data.get("from") or "en"), str(data.get("to") or "it")
        except (ValueError, AttributeError, UnicodeDecodeError):
            return self._reply(400, "invalid request")
        try:
            translations, to_retry = service.answer_group(texts, src_lang, a)
        except Exception as e:  # unexpected: the script retries later
            service.log("! group of %d phrases: %s: %s" % (len(texts), type(e).__name__, e))
            translations, to_retry = {}, texts
        body = json.dumps({"traduzioni": {t: service.adapt(v, a) for t, v in translations.items()},
                            "riprova": to_retry}, ensure_ascii=False)
        self._reply(200, body, "application/json; charset=utf-8")

    def do_GET(self):
        service = self.server.service
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        if url.path == "/patchsmith":
            return self._reply(200, "patchsmith " + service.name)
        if url.path == "/ferma":  # "Stop the game translator" in PatchSmith's window
            service.log("Stopped from the PatchSmith window (phrases translated: %d)" % service.translated_count)
            service.closing.set()
            threading.Thread(target=service.httpd.shutdown, daemon=True).start()
            return self._reply(200, "ok")
        if url.path == "/adotta":  # the game was reopened: from now on the new process is followed
            try:
                service.pid = int(q["pid"][0])
            except (KeyError, ValueError):
                return self._reply(400, "pid?")
            service.log("The game has been reopened (process %d): continuing" % service.pid)
            return self._reply(200, "ok")
        if url.path != URL_PATH:
            return self._reply(404, "")
        text = q.get("text", [""])[0]
        if not text.strip():
            return self._reply(200, text)
        src_lang, a = q.get("from", ["en"])[0], q.get("to", ["it"])[0]
        try:
            code, response = service.answer(text, src_lang, a)
            if code == 200:
                response = service.adapt(response, a)
        except Exception as e:  # unexpected: XUnity retries the line later
            service.log("! %r: %s: %s" % (text[:80], type(e).__name__, e))
            service.record(text, src_lang, a, "errore", reason="%s: %s" % (type(e).__name__, e))
            code, response = 500, "not translated"
        self._reply(code, response)

    def _reply(self, code, text, kind="text/plain; charset=utf-8"):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *arguments):  # no lines for each request
        pass


class Service:
    """porta=None: the game's port (port_for). backend: for the tests, a ready translator or a list of
    (nome, traduttore) to try in a row; None = the row of expected_translators. pid: the game's process to
    follow (when it closes the service stops)."""

    def __init__(self, profile_path, port=None, pid=None, log=print, backend=None, test=False):
        self.project = Project(profile_path, log, test=test)
        # one line at a time during play: no second rounds (the other model would load the game's graphics card)
        # and no problems.json; the journal records the rejected lines and they are re-translated with the game closed
        self.project.recovery = False
        self.name = self.project.profile["nome"]
        self.port = port or port_for(self.name)
        self.pid = pid
        self.log = log
        self._backend = backend
        self.chain = []  # [(nome, traduttore)] ready, in the order they are tried
        self._lock = threading.Lock()
        self._journal_lock = threading.Lock()
        self._ready = threading.Event()
        self.closing = threading.Event()  # the game is closing: no more translations (503)
        self._error = None
        self.httpd = None
        self.translated_count = 0
        self.journal = journal_path(self.name)

    # --- translators -------------------------------------------------------------------------------

    def _prepare(self):
        """Prepares the row of translators and loads the local model right away: the first line does not wait for the
        graphics card to start. A translator that does not start is skipped."""
        try:
            if self._backend is None:
                chain = []
                for name in expected_translators(self.project.profile):
                    try:
                        chain.append((name, create_translator(name, self.log)))
                    except (BackendError, ValueError, OSError) as e:
                        self.log("! %s not available: %s" % (NAMES.get(name, name), e))
            elif isinstance(self._backend, (list, tuple)):
                chain = list(self._backend)
            else:
                chain = [(getattr(self._backend, "name", "prova"), self._backend)]
            ready = []
            for name, translator in chain:
                if hasattr(translator, "_ensure_server"):
                    try:
                        translator._ensure_server()
                    except BackendError as e:
                        self.log("! %s not available: %s" % (NAMES.get(name, name).capitalize(), e))
                        continue
                if hasattr(translator, "rejects"):
                    translator.rejects = {}  # the rejected proposals are needed for the fallback answer
                ready.append((name, translator))
            self.chain = ready
            self.log("Translators for new phrases: %s" % (", ".join(NAMES.get(n, n) for n, _ in ready) or
                                                            "nobody (the new phrases remain in the original language)"))
        except Exception as e:
            self._error = e
            self.log("! Translators not ready: %s: %s" % (type(e).__name__, e))
        finally:
            self._ready.set()

    def answer(self, text, src_lang, a):
        """(HTTP code, answer text) for a line requested by XUnity. Every line ends up in the journal."""
        if self.closing.is_set():
            return self._while_closing(text, src_lang, a)
        memory = self.project.memory
        already = memory.get(text)
        if isinstance(already, str) and already:  # already translated: without waiting for the translators
            self.record(text, src_lang, a, "ok", "memoria", response=already)
            return 200, already
        if self._part_of_translation(text):
            self.record(text, src_lang, a, "ok", "gia' tradotta", response=text)
            return 200, text
        if not self._ready.wait(MAX_STARTUP):
            self.record(text, src_lang, a, "rinviata", reason="the translators are still starting up", response=text)
            return 200, text
        with self._lock:  # one line at a time (XUnity sends one at a time, but someone else might send more)
            if self.closing.is_set():
                return self._while_closing(text, src_lang, a)
            already = memory.get(text)
            if isinstance(already, str) and already:
                self.record(text, src_lang, a, "ok", "memoria", response=already)
                return 200, already
            return self._translate(text, src_lang, a)

    def _part_of_translation(self, text):
        """True if the text is (a piece of) a translation already made: the game already shows the Italian (e.g. the
        translated text tables in the bundles, which XUnity splits at the colors and requests as if they were
        English). Re-translating it from English would ruin it."""
        piece = text.strip()
        memory = self.project.memory
        if len(piece) < 4 or piece in memory:
            return False
        if getattr(self, "_translated_n", -1) != len(memory):
            self._translated = "\n".join(v for v in memory.values() if isinstance(v, str))
            self._translated_n = len(memory)
        return piece in self._translated

    def answer_group(self, texts, src_lang, a):
        """({testo: traduzione}, [texts to request later]) for several lines together: those already in the
        translation memory right away, the others with a single group translation per translator (the local
        translator does more than one at a time on the graphics card); those the group does not translate go one at a
        time, like answer()."""
        translations, to_retry, missing = {}, [], []
        memory = self.project.memory
        for t in dict.fromkeys(texts):
            if not t.strip():
                continue
            if self.closing.is_set():
                to_retry.append(t)
                continue
            already = memory.get(t)
            if isinstance(already, str) and already:
                self.record(t, src_lang, a, "ok", "memoria", response=already)
                translations[t] = already
            elif self._part_of_translation(t):
                self.record(t, src_lang, a, "ok", "gia' tradotta", response=t)
            else:
                missing.append(t)
        if not missing:
            return translations, to_retry
        if not self._ready.wait(MAX_STARTUP):
            return translations, to_retry + missing
        with self._lock:
            p = self.project
            for name, translator in self.chain:
                remaining = [t for t in missing if t not in translations]
                if not remaining or self.closing.is_set():
                    break
                if hasattr(translator, "pause") and translator.pause():
                    continue
                p.translation_outcome, p.last_error, p.last_group = None, None, None
                p.translate_texts(remaining, _NoClose(translator), src_lang, a)
                for t in remaining:
                    v = memory.get(t)
                    if isinstance(v, str) and v:
                        self.translated_count += 1
                        self.record(t, src_lang, a, "ok", name if p.last_group is not None else "comune", response=v)
                        translations[t] = v
                if isinstance(p.last_error, Deferred) or p.translation_outcome == "TRADUTTORE_BLOCCATO":
                    continue
        for t in [t for t in missing if t not in translations]:  # one at a time: fallback, rejects and journal
            if self.closing.is_set():
                to_retry.append(t)
                continue
            with self._lock:
                code, response = self._translate(t, src_lang, a)
            if code == 200 and response != t:
                translations[t] = response
            else:  # left untranslated or translators stopped: the script requests it again later (a few times)
                to_retry.append(t)
        return translations, to_retry

    def _while_closing(self, text, src_lang, a):
        self.record(text, src_lang, a, "interrotta", reason="the game is closing")
        return 503, "closing"

    def adapt(self, response, a):
        """The answer for XUnity without the characters the game's fonts cannot draw (profilo
        ["caratteri_mancanti"], found by the after-play check): è -> e'. The translation memory and the journal keep
        the real translation."""
        from .characters import adapt_without
        excluded = self.project.profile.get("caratteri_mancanti")
        return (adapt_without(response, set(excluded), a) or response) if excluded else response

    def _translate(self, text, src_lang, a):
        """Tries the translators in a row; if they all reject the line, the fallback answer (see _rilassata)."""
        memory, p = self.project.memory, self.project
        answered, deferred, errors = [], [], []
        for _, translator in self.chain:
            if getattr(translator, "rejects", None) is not None:
                translator.rejects.clear()
        for name, translator in self.chain:
            pause = translator.pause() if hasattr(translator, "pause") else None
            if pause:  # paused because of a block (even one that arrived during play): no insisting
                errors.append((name, pause))
                continue
            p.translation_outcome, p.last_error, p.last_group = None, None, None
            p.translate_texts([text], _NoClose(translator), src_lang, a)
            translated = memory.get(text)
            if isinstance(translated, str) and translated:
                self.translated_count += 1
                self.record(text, src_lang, a, "ok", name if p.last_group is not None else "comune",
                            response=translated)
                return 200, translated
            if isinstance(p.last_error, Deferred):
                deferred.append((name, str(p.last_error)))
            elif p.last_error is not None or p.translation_outcome == "TRADUTTORE_BLOCCATO":
                errors.append((name, str(p.last_error or p.translation_reason)))
            else:  # it answered, but the translation did not pass the checks
                answered.append((name, translator, p.last_group))
        reasons = ["%s: %s" % (NAMES.get(n, n), m) for n, m in errors + deferred]
        if answered:
            name, translator, group = answered[0]
            rejected_note = "rejected by the checks (%s)" % ", ".join(NAMES.get(n, n) for n, _, _ in answered)
            reason = "; ".join([rejected_note] + reasons)
            relaxed = self._relaxed(translator, group, text, src_lang, a)
            if relaxed:
                self.log("  ~ phrase translated without all the checks (to be rechecked): %r" % text[:80])
                self.record(text, src_lang, a, "rilassata", name, reason, response=relaxed)
                return 200, relaxed
            self.log("  ! phrase left untranslated (no acceptable translation): %r" % text[:80])
            self.record(text, src_lang, a, "rifiutata", name, reason, response=text)
            return 200, text
        if deferred:
            self.record(text, src_lang, a, "rinviata", deferred[0][0], "; ".join(reasons), response=text)
            return 200, text
        reason = "; ".join(reasons) or ("translators not ready (%s)" % self._error if self._error else
                                       "no translator available")
        self.log("! %r not translated: %s" % (text[:80], reason))
        self.record(text, src_lang, a, "errore", reason=reason)
        return 500, "not translated"

    def _relaxed(self, translator, group, text, src_lang, a):
        """The fallback answer for a line that all translators rejected: the pieces between the placeholders
        translated separately (only once, only where it has not been done already) and, for the lines still without a
        translation, the local translator's rejected proposal. None if an XUnity {{A}} placeholder is missing or if
        the answer is not in the target language: then the answer is the original."""
        if not group or text not in group["divisi"]:
            return None
        parts, protected, mapping = group["divisi"][text], group["protetti"], dict(group["mappa"])
        missing = [n for _, n, _ in parts if n and _to_translate(n) and n not in mapping]
        in_pieces = [n for n in missing if protected[n][1] and n not in group.get("ripiegati", ())]
        if in_pieces:
            try:
                for n, r in self.project._translate_in_pieces(_NoClose(translator), in_pieces, src_lang, a).items():
                    if r:
                        mapping[n] = capitalize_like(n, r)
            except Exception as e:  # noqa: BLE001 - the fallback is optional
                self.log("  ! piece-by-piece fallback failed (%s: %s)" % (type(e).__name__, e))
        rejects = getattr(translator, "rejects", None) or {}
        for n in missing:
            if n in mapping:
                continue
            proposal = rejects.get(protected[n][0])
            restored = restore_placeholders(proposal[0], protected[n][1]) if proposal else None
            if not restored:
                return None
            mapping[n] = capitalize_like(n, restored)
        response = reassemble_lines(parts, mapping)
        if collections.Counter(_XUNITY.findall(text)) != collections.Counter(_XUNITY.findall(response)):
            return None
        if other_language(text, response, src_lang, a):
            return None
        return response

    # --- journal -----------------------------------------------------------------------------------

    def record(self, text, src_lang, a, outcome, backend=None, reason="", response=None):
        """A line in the journal of the requested lines (see percorso_diario)."""
        line = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "testo": text, "da": src_lang, "a": a,
                "esito": outcome, "backend": backend, "motivo": reason}
        if response is not None:
            line["risposta"] = response
        data = (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8")
        with self._journal_lock:
            try:
                os.makedirs(os.path.dirname(self.journal), exist_ok=True)
                try:
                    if os.path.getsize(self.journal) > MAX_JOURNAL:
                        os.replace(self.journal, self.journal + ".1")
                except OSError:
                    pass
                with open(self.journal, "ab") as f:
                    f.write(data)
            except OSError as e:
                self.log("! Phrase log not written (%s)" % e)

    # --- service lifetime --------------------------------------------------------------------------

    def _watch_game(self):
        while not self.closing.is_set():
            time.sleep(GAME_CHECK)
            if self.pid and not process_alive(self.pid):
                self.log("The game has closed (translated phrases: %d): closing the translator" % self.translated_count)
                self.closing.set()  # from now on lines get 503: no translation with the translator off
                # stop listening right away, without waiting for a translation in progress (with an online translator
                # it can take a minute): the port is freed and a game reopened right away starts its own translator
                # instead of relying on this one, which is closing. The translation in progress finishes and is saved
                # anyway: launch() waits for it before closing the translators
                self.httpd.shutdown()
                return

    def _adopt(self):
        """Port already in use: if it is a PatchSmith service for this game, it hands it the new process."""
        base = "http://127.0.0.1:%d" % self.port
        try:
            with urllib.request.urlopen(base + "/patchsmith", timeout=5) as r:
                if r.read().decode("utf-8", "replace") != "patchsmith " + self.name:
                    return False
            if self.pid:
                with urllib.request.urlopen(base + "/adotta?pid=%d" % self.pid, timeout=5) as r:
                    r.read()
            return True
        except (OSError, ValueError):
            return False

    def launch(self):
        """Listens until the game closes (or Ctrl+C). 0 if everything went well."""
        try:
            self.httpd = _HttpServer(("127.0.0.1", self.port), _Handler)
        except OSError as e:
            if self._adopt():
                self.log("The translator for this game is already running: that one carries on")
                return 0
            self.log("! Port %d is in use by another program (%s): translator not started" % (self.port, e))
            return 1
        self.httpd.service = self
        dropped = shared_file.clean_temp_files(os.path.dirname(self.project.memory_path))
        if dropped:
            self.log("Removed %d temporary files left over from an interrupted save" % dropped)
        self.log("Translator for XUnity is listening on %s (game: %s)" % (address(self.port), self.name))
        threading.Thread(target=self._prepare, daemon=True).start()
        if self.pid:
            threading.Thread(target=self._watch_game, daemon=True).start()
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.closing.set()
            self.httpd.server_close()
            self._ready.wait(timeout=300)  # if the translators are still starting, they are closed afterwards
            acquired = self._lock.acquire(timeout=MAX_CLOSING)  # a translation in progress finishes first
            try:
                for name, translator in self.chain:
                    if hasattr(translator, "close"):
                        try:
                            translator.close()
                        except Exception as e:  # noqa: BLE001 - the others are closed anyway
                            self.log("! %s is not properly closed (%s)" % (NAMES.get(name, name), e))
            finally:
                if acquired:
                    self._lock.release()
        return 0
