"""Interchangeable translation backends. Each backend translates a list of short lines."""
import collections
import concurrent.futures
import datetime
import html
import http.client
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib

from . import interruption, legacy
from .text import DETAIL

MAX_BLOCK = 4000  # characters per request (margin below the roughly 5,000 accepted)


class BackendError(Exception):
    """The translation service cannot be reached or rejects the requests.

    code: the outcome for the interface (see outcome.py), e.g. TRANSLATOR_MISSING; command: what to type on the
    command line to fix it (only the command line shows it: the messages talk about the interface's buttons)."""

    def __init__(self, message="", code=None, command=None):
        super().__init__(message)
        self.code = code
        self.command = command


class Backend:
    name = "base"

    def translate_lines(self, lines, origin, dest_lang):
        """Translates each line (without line breaks). Returns a list of the same length."""
        raise NotImplementedError


class Fake(Backend):
    """Test backend: does not use the network, puts [it] before the text. Its translations end up in a separate
    translation memory (*.prova.json), so they do not pollute the real one."""

    name = "fake"

    def translate_lines(self, lines, origin, dest_lang):
        interruption.check()
        return ["[%s] %s" % (dest_lang, r) for r in lines]


# invisible characters that Google sometimes inserts (e.g."​​gli"): in games they can show up
# as little squares or, in XNA, make the game crash
_INVISIBLE = dict.fromkeys(map(ord, "​⁠﻿"))


def clean(text):
    return text.translate(_INVISIBLE)


def _split_long(line, maximum=MAX_BLOCK):
    """Splits a line that is too long into pieces by sentences (or by spaces) of at most `maximum` characters."""
    pieces, current = [], ""
    for sentence in re.split(r"(?<=[.!?。！？])\s+", line):
        if len(sentence) > maximum and current:  # the previous sentence first: the order must not change
            pieces.append(current)
            current = ""
        while len(sentence) > maximum:  # huge sentence without punctuation: cut on the spaces
            cut = sentence.rfind(" ", 0, maximum)
            cut = cut if cut > 0 else maximum
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > maximum:
            pieces.append(current)
            current = sentence
        else:
            current = (current + " " + sentence) if current else sentence
    if current:
        pieces.append(current)
    return pieces


class Blocked(BackendError):
    """The service rejected the requests (or is paused because of a previous rejection): we do not insist."""


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def _read_error(e):
    """(Short) body of an HTTP error response, always closing it."""
    try:
        return e.read(2000) or b""
    except (OSError, ValueError, http.client.HTTPException):
        return b""
    finally:
        try:
            e.close()
        except (OSError, ValueError):
            pass


UA = "Patchsmith/%s (Video Game Translator)"


def _ua():
    from . import __version__
    return UA % __version__


class GoogleFree(Backend):
    """Free web endpoint of Google Translate (unofficial, without a key).

    Google does not publish limits and can reject automated access entirely: PatchSmith goes slowly (rate limiter
    with cautious margins, shared between the interface and the command line) and at the FIRST rejection it stops
    and remembers until when not to retry, without insisting and without looking for other ways around the block.
    """

    name = "google"
    URL = "https://translate.googleapis.com/translate_a/single"
    max_chars = 3600  # a group of texts = one request (the placeholders make the text a bit longer)
    max_texts = 150
    NETWORK_WAITS = (10, 30)

    def __init__(self, log=print, opener=None, limiter=None):
        from .limiter import Limiter
        self.log = log
        self.opener = opener or _default_opener
        self.limiter = limiter or Limiter("google", log)

    def pause(self):
        return self.limiter.pause()

    def estimate(self, chars_per_request):
        return self.limiter.estimate(chars_per_request)

    def _block(self, http_code, detail):
        raise Blocked(self.limiter.blocked(http_code, detail))

    def _request(self, text, origin, dest_lang):
        from .limiter import Paused
        params = urllib.parse.urlencode({"client": "gtx", "sl": origin, "tl": dest_lang, "dt": "t"})
        body = urllib.parse.urlencode({"q": text}).encode("utf-8")
        errors = 0
        while True:
            interruption.check()
            try:
                self.limiter.before_request(len(text))
            except Paused as e:
                raise Blocked(str(e)) from e
            req = urllib.request.Request(self.URL + "?" + params, data=body, headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8", "User-Agent": _ua()})
            try:
                with self.opener(req, 30) as response:
                    final = response.geturl() or ""
                    kind = response.headers.get("Content-Type", "") if response.headers else ""
                    raw = response.read()
                if "consent.google" in final:
                    raise BackendError("Google is asking you to accept cookies (consent page): Unfortunately, this "
                                       "translator cannot be used from this computer. Please choose a different "
                                       "translator.")
                if "/sorry" in final or "html" in kind.lower():
                    self._block(200, "block page instead of the translation")
                data = json.loads(raw.decode("utf-8"))
                if not (isinstance(data, list) and data and isinstance(data[0], (list, type(None)))):
                    raise BackendError("Google replied in an unexpected format: try again later.")
                translated_text = "".join(seg[0] for seg in (data[0] or []) if isinstance(seg, list) and seg
                                         and isinstance(seg[0], str))
                self.limiter.ok()
                return clean(translated_text)
            except urllib.error.HTTPError as e:
                error_body = _read_error(e).lower()
                final = e.geturl() or ""
                error_content_type = (e.headers.get("Content-Type", "") if e.headers else "").lower()
                html_error = b"<html" in error_body or b"<!doctype html" in error_body or "html" in error_content_type
                if e.code in (429, 403) or "/sorry" in final or html_error or b"unusual traffic" in error_body:
                    self._block(e.code, 'Google "Sorry" page' if b"sorry" in error_body else None)
                if 400 <= e.code < 500:
                    raise BackendError("Google rejects the request (HTTP %d): check the chosen languages (%s -> %s)"
                                        % (e.code, origin, dest_lang)) from e
                error = "HTTP %d" % e.code
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, IndexError, TypeError) as e:
                error = e
            errors += 1
            if errors > len(self.NETWORK_WAITS):
                raise BackendError("Google is unavailable (%s): Please try again later." % error)
            wait = self.NETWORK_WAITS[errors - 1]
            self.log("    ! network error (%s): retrying in %d s (attempt %d of %d)"
                     % (error, wait, errors + 1, len(self.NETWORK_WAITS) + 1))
            interruption.wait_for(wait)

    def _translate_indices(self, parts, indices, translated_items, origin, dest_lang, depth=0):
        """Translates a block of lines; if Google merges or splits lines, retries in halves (at most 3 times), so a
        burst of requests never starts. The lines that do not line up stay untranslated."""
        text = "\n".join(parts[k][1] for k in indices)
        response = self._request(text, origin, dest_lang).split("\n")
        if len(response) == len(indices):
            for k, t in zip(indices, response):
                translated_items[k] = t.strip()
            return
        if depth >= 3 or len(indices) == 1:
            self.log("    ! %d misaligned lines in the response: left untranslated" % len(indices))
            return
        half = len(indices) // 2
        self.log("    misaligned lines: retrying in two halves (%d + %d lines)" % (half, len(indices) - half))
        self._translate_indices(parts, indices[:half], translated_items, origin, dest_lang, depth + 1)
        self._translate_indices(parts, indices[half:], translated_items, origin, dest_lang, depth + 1)

    def translate_lines(self, lines, origin, dest_lang):
        # Lines that are too long are split by sentences and then put back together.
        parts = []  # (line index, piece)
        for i, r in enumerate(lines):
            for piece in (_split_long(r) if len(r) > MAX_BLOCK else [r]):
                parts.append((i, piece))
        blocks = _blocks([p for _, p in parts], MAX_BLOCK)
        translated_items = [None] * len(parts)

        def results():
            joined = [[] for _ in lines]
            for (i, _), t in zip(parts, translated_items):
                joined[i].append(t)
            return ["" if any(t is None for t in r) else " ".join(r) for r in joined]
        try:
            for n, indices in enumerate(blocks, 1):
                self.log("    block %d/%d (%d rows)" % (n, len(blocks), len(indices)))
                self._translate_indices(parts, indices, translated_items, origin, dest_lang)
        except BaseException as e:  # block or "Stop" halfway: the lines already translated stay usable
            e.partials = results()
            raise
        return results()


def _blocks(pieces, maximum, texts_limit=None):
    """Indices of the pieces grouped into blocks of at most 'maximum' characters (line breaks included)."""
    blocks, block, length = [], [], 0
    for k, piece in enumerate(pieces):
        if block and (length + len(piece) + 1 > maximum or (texts_limit and len(block) >= texts_limit)):
            blocks.append(block)
            block, length = [], 0
        block.append(k)
        length += len(piece) + 1
    if block:
        blocks.append(block)
    return blocks


# --- DeepL (official API, personal key: free up to 500,000 characters a month) ----------------------

def _deepl_language(code, dest_lang):
    c = (code or "").replace("_", "-")
    base = c.split("-")[0].lower()
    if base == "zh":
        from .language import comparison_key
        return ("ZH-HANT" if comparison_key(c) == "zh-hant" else "ZH-HANS") if dest_lang else "ZH"
    if dest_lang and base == "en":
        return "EN-GB" if c.lower() in ("en-gb", "en-uk") else "EN-US"
    if dest_lang and base == "pt":
        return "PT-BR" if c.lower() == "pt-br" else "PT-PT"
    return {"no": "NB"}.get(base, base.upper())


class DeepL(Backend):
    """Official DeepL API with the user's key (free account: 500,000 characters a month). Free keys end with ':fx'
    and use api-free.deepl.com. Lists of texts are sent: no lines to line up."""

    name = "deepl"
    max_texts = 50
    max_chars = 30_000
    WAITS = (5, 15, 45)
    LONGEST_WAIT = 300.0  # seconds: at most those asked by DeepL (Retry-After) before retrying

    def __init__(self, key, log=print, opener=None, limiter=None):
        from .limiter import Limiter
        key = "".join((key or "").split()).replace("\u200b", "").replace("\ufeff", "")
        if not key:
            raise BackendError("To use DeepL you need your key (free): create a DeepL API Free account at "
                               "deepl.com/pro-api and paste it into the 'DeepL key' field (or into the "
                               "PATCHSMITH_DEEPL variable).", "TRANSLATOR_MISSING")
        if not re.fullmatch(r"[0-9A-Za-z:-]+", key):
            raise BackendError("The DeepL key contains invalid characters: copy it from the DeepL website.",
                                "TRANSLATOR_MISSING")
        self.key = key
        self.from_variable = False
        self.base = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
        self.log = log
        self.opener = opener or _default_opener
        self.limiter = limiter or Limiter("deepl", log)

    def pause(self):
        return self.limiter.pause()

    def estimate(self, chars_per_request):
        return self.limiter.estimate(chars_per_request, response_duration=1.0)

    def verify(self):
        """(characters used, limit) of this month: proves that the key works (GET /v2/usage, no text)."""
        data = self._call("/v2/usage")
        return int(data.get("character_count") or 0), int(data.get("character_limit") or 0)

    def _call(self, path, body=None):
        from .limiter import Paused
        errors = 0
        while True:
            interruption.check()
            try:
                self.limiter.before_request(len(body or b""))
            except Paused as e:
                raise Blocked(str(e)) from e
            req = urllib.request.Request(self.base + path, data=body, headers={
                "Authorization": "DeepL-Auth-Key " + self.key, "Content-Type": "application/json",
                "User-Agent": _ua()})
            try:
                try:
                    response = self.opener(req, 60)
                except (ValueError, UnicodeError) as e:  # request that cannot be built (e.g. the key): never retry
                    raise BackendError("I can't prepare the request for DeepL (%s): check the key."
                                        % type(e).__name__) from None
                with response:
                    data = json.loads(response.read().decode("utf-8"))
                self.limiter.ok()
                return data
            except urllib.error.HTTPError as e:
                detail = _read_error(e).decode("utf-8", "replace")[:200]
                if e.code == 403:
                    raise BackendError("DeepL doesn't accept the key (HTTP 403): make sure you copied all of it into "
                                       "the 'DeepL key' field%s." % (" (now taken from the PATCHSMITH_DEEPL "
                                                                         "variable)"
                                                                         if self.from_variable else "")) from e
                if e.code == 456:
                    raise BackendError("You've used up this month's free DeepL characters: the translations done stay "
                                       "saved; resume next month or choose another translator.") from e
                if e.code == 400:
                    raise BackendError("DeepL rejects the request (%s): maybe the language isn't supported."
                                        % detail) from e
                if e.code != 429 and e.code < 500:
                    raise BackendError("DeepL: HTTP error %d (%s)" % (e.code, detail)) from e
                error = "HTTP %d" % e.code
                suggested_wait = e.headers.get("Retry-After") if e.headers else None
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                error, suggested_wait = type(e).__name__, None  # never the error's text: it can contain the key
            errors += 1
            if errors > len(self.WAITS):
                raise BackendError("DeepL is currently unavailable (%s): please try again later." % error)
            try:
                wait = min(self.LONGEST_WAIT, float(suggested_wait)) if suggested_wait else \
                    self.WAITS[errors - 1]
            except ValueError:
                wait = self.WAITS[errors - 1]
            self.log("    ! DeepL: %s, I'll try again in %d seconds (attempt %d of %d)"
                     % (error, wait, errors + 1, len(self.WAITS) + 1))
            interruption.wait_for(wait)

    def usage(self):
        """(characters used, limit) of the month, or None."""
        try:
            data = self._call("/v2/usage")
            return data.get("character_count"), data.get("character_limit")
        except BackendError:
            return None

    def translate_lines(self, lines, origin, dest_lang):
        output = []
        for indices in _blocks(lines, self.max_chars, self.max_texts):
            request = {"text": [lines[k] for k in indices], "target_lang": _deepl_language(dest_lang, True)}
            if origin and origin != "auto":
                request["source_lang"] = _deepl_language(origin, False)
            self.log("    DeepL: %d texts" % len(indices))
            data = self._call("/v2/translate", json.dumps(request, ensure_ascii=False).encode("utf-8"))
            translations = [t.get("text", "") for t in data.get("translations", [])]
            if len(translations) != len(indices):
                raise BackendError("DeepL provided %d translations instead of %d"
                                   % (len(translations), len(indices)))
            output += [clean(t) for t in translations]
        return output


# --- MyMemory (public API, no account: 5,000 characters a day, 50,000 with your own email) ------------------

def _split_bytes(line, maximum=480):
    """Pieces of at most 'maximum' UTF-8 bytes (MyMemory accepts at most 500 bytes per request)."""
    pieces = []
    for piece in _split_long(line, maximum):
        while len(piece.encode("utf-8")) > maximum:
            # how many characters really fit in 'maximum' bytes (even with mixed text, e.g. Japanese + English)
            cut = max(1, len(piece.encode("utf-8")[:maximum].decode("utf-8", "ignore")))
            space = piece.rfind(" ", 0, cut)
            cut = space if space > 0 else cut
            pieces.append(piece[:cut])
            piece = piece[cut:].lstrip()
        if piece:
            pieces.append(piece)
    return pieces


class MyMemory(Backend):
    """Public API of MyMemory (Translated): no account. Without email 5,000 characters a day, with your own email
    (parameter 'de') 50,000. One request per line (at most 500 bytes)."""

    name = "mymemory"
    URL = "https://api.mymemory.translated.net/get"
    max_texts = 10  # one request per line: small groups, so a block halfway loses little
    max_chars = 800
    QUOTA = (5_000, 50_000)  # characters a day without / with email

    def requests(self, texts):
        """Characters of each request that will be sent (one per line, several pieces for long lines)."""
        return [len(p) for t in texts for p in (_split_bytes(t) if len(t.encode("utf-8")) > 480 else [t])]

    def quota(self):
        return self.QUOTA[1] if self.email else self.QUOTA[0]

    def verify(self):
        """Translates "Hello" (5 characters) with the given email: MyMemory answers and accepts the email. There is no
        account to check: the email only serves to get more characters a day."""
        return self._request("Hello", "en", "it")

    def __init__(self, email=None, log=print, opener=None, limiter=None):
        from .limiter import Limiter
        self.email = (email or "").strip() or None
        self.log = log
        self.opener = opener or _default_opener
        self.limiter = limiter or Limiter("mymemory", log)

    def pause(self):
        block = self.limiter.current_block()
        if block and block.get("kind") == "quota" and self.email and not block.get("with_email"):
            self.limiter.reset()  # quota without email used up: with the email we can go on
            return None
        return self.limiter.pause()

    def estimate(self, chars_per_request):
        return self.limiter.estimate(chars_per_request, response_duration=0.8)

    def _quota_used_up(self):
        now = datetime.datetime.fromtimestamp(self.limiter.current_time())
        tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)
        self.limiter.blocked(429, "free daily characters have been used up", until=tomorrow.timestamp(),
                                 extra={"with_email": bool(self.email)})
        return ("You've used today's free MyMemory characters (%s): the translations done stay saved, resume "
                "tomorrow%s." % ("50,000 with your email address" if self.email else "5,000 without email",
                                        "" if self.email else " or enter your email address to reach 50,000"))

    def _request(self, text, origin, dest_lang):
        from .limiter import Paused
        params = {"q": text, "langpair": "%s|%s" % (origin, dest_lang)}
        if self.email:
            params["de"] = self.email
        errors = 0
        while True:
            interruption.check()
            try:
                self.limiter.before_request(len(text))
            except Paused as e:
                raise Blocked(str(e)) from e
            req = urllib.request.Request(self.URL + "?" + urllib.parse.urlencode(params),
                                         headers={"User-Agent": _ua()})
            try:
                with self.opener(req, 30) as response:
                    data = json.loads(response.read().decode("utf-8"))
                state = str(data.get("responseStatus"))
                translated = (data.get("responseData") or {}).get("translatedText") or ""
                if data.get("quotaFinished") or state == "429" or "MYMEMORY WARNING" in translated.upper():
                    raise Blocked(self._quota_used_up())
                if state != "200":
                    if "LENGTH" in str(data.get("responseDetails", "")).upper():
                        self.log("    ! MyMemory: text too long, left untranslated")
                        return ""  # it only concerns this line: the work goes on
                    raise BackendError("MyMemory rejects the request (%s): %s" % (state, data.get("responseDetails")))
                self.limiter.ok()
                return clean(html.unescape(translated))
            except urllib.error.HTTPError as e:
                _read_error(e)
                if e.code == 429:
                    raise Blocked(self._quota_used_up()) from e
                if e.code < 500:
                    raise BackendError("MyMemory: HTTP error %d" % e.code) from e
                error = "HTTP %d" % e.code
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                error = e
            errors += 1
            if errors > 2:
                raise BackendError("MyMemory is not responding (%s): try again later." % error)
            self.log("    ! MyMemory: %s, retrying in %d s" % (error, 10 * errors))
            interruption.wait_for(10 * errors)

    def translate_lines(self, lines, origin, dest_lang):
        output = []
        try:
            for line in lines:
                pieces = _split_bytes(line) if len(line.encode("utf-8")) > 480 else [line]
                translated_items = [self._request(p, origin, dest_lang).strip() for p in pieces]
                output.append("" if any(not t for t in translated_items) else " ".join(translated_items))
        except BaseException as e:  # quota used up or "Stop" halfway: the lines already translated are not lost
            e.partials = output + [""] * (len(lines) - len(output))
            raise
        return output


# --- Local translator (Ollama + TranslateGemma on the user's PC: no limits, no blocks) ---------------------

ENGLISH_NAMES = {
    "en": "English", "it": "Italian", "fr": "French", "de": "German", "es": "Spanish", "pt": "Portuguese",
    "pt-BR": "Brazilian Portuguese", "ru": "Russian", "pl": "Polish", "ja": "Japanese", "ko": "Korean",
    "zh-CN": "Chinese (Simplified)", "zh-TW": "Chinese (Traditional)", "zh": "Chinese", "nl": "Dutch",
    "sv": "Swedish", "da": "Danish", "no": "Norwegian", "fi": "Finnish", "cs": "Czech", "hu": "Hungarian",
    "ro": "Romanian", "el": "Greek", "tr": "Turkish", "uk": "Ukrainian", "ar": "Arabic", "he": "Hebrew",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "hi": "Hindi",
}
# official format of the TranslateGemma prompt (ollama.com/library/translategemma): two lines, two empty lines, text
# The official TranslateGemma prompt with one line of context: in the tests on Forager (October 2026) it improved
# the short names ("max hp" -> "punti vita massimi", "buffs" -> "effetti positivi") and the informal "tu" to the
# player.
PROMPT_TRANSLATEGEMMA = (
    "You are a professional {sl} ({sc}) to {tl} ({tc}) translator. Your goal is to accurately convey the meaning "
    "and nuances of the original {sl} text while adhering to {tl} grammar, vocabulary, and cultural "
    "sensitivities.\nThe text is a string from a video game (item names, skills, menus or dialogue); address the "
    "player informally.\n{context}Produce only the {tl} translation, without any additional explanations or "
    "commentary. Please translate the following {sl} text into {tl}:\n\n\n{text}")
# Context per line (tests on Forager, October 2026: 4B from 43% to 64% good translations, 12B from 73% to 80%):
# the text's key in the file (without the final number) and the game's official translations, not to be copied.
KEY_CONTEXT = "In the game's files this text has the identifier \"%s\", a hint of where it appears.\n"
REFERENCES_CONTEXT = ("The game's official translations of this text into other languages, only to understand its "
                        "meaning (write your answer in %s, do not copy them; names of people and games stay as in "
                        "the original): %s\n")
# second attempt after a rejection because of the numbers (tests: '200% more' -> 'il doppio' fixed into
# 'il 200% in piu'')
PLACEHOLDER_FIX = ("Copy every marker like [[0]], [[1]] exactly as written, around the same words: they are "
                         "game codes.\n")
NUMBERS_FIX = ("Keep every number exactly as written in the original: digits stay digits (200% stays 200%, "
                     "1 stays 1) and numbers written in words stay words.\n")
# text re-translated because the previous translation could not be written to the game (see Project.apply)
CHARS_FIX = "The game's font cannot draw these characters, so do not use them: %s\n"
# short label requested again because the translation was too long for its space (Project._shorten)
WIDTH_CONTEXT = ("In the game this text is a short label with little space: the %s translation must be at most %d "
                      "characters long. Use a shorter word or synonym if needed, keeping the meaning.\n")
_CONTROL_CHARS = re.compile(r"<\s*/?\s*(?:end_of_turn|start_of_turn)\s*>|<\|[^|>]*\|>|<｜[^｜>]*｜?>")
_PREAMBLE = re.compile(r"^\s*(?:translation|translated text|traduzione|testo tradotto|here is the translation|"
                        r"here's the translation|ecco la traduzione|note|nota)\b[^:\n]{0,40}:", re.I)
_REFUSAL = re.compile(r"^\s*(?:i can'?t|i cannot|i'm sorry|i am sorry|i'm unable|i am unable|as an ai|mi dispiace|"
                      r"non posso|non sono in grado)\b.{0,80}?\b(?:translat\w*|tradu\w*|assist\w*|"
                      r"this (?:text|request|content)|questo (?:testo|contenuto))", re.I | re.S)
_OTHER_SCRIPT = re.compile(r"[Ѐ-ӿ぀-ヿ㐀-䶿一-鿿가-힯]")
_ENGLISH_WORDS = re.compile(r"\b(the|of|and|you|your|to|is|are|with|for|this|that)\b", re.I)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_ENDS_SENTENCE = re.compile(r"[.!?:;\u2026\u3002\uff01\uff1f][\"')\]\u201d\u00bb\u300d\u300f\uff09]*$")
# numbers in words accepted in place of digits ("1 or more" -> "uno o piu'"): only unambiguous words (no articles
# like 'un'/'a', no 'sei' which is also 'tu sei')
_NUMBERS_IN_WORDS = {
    "it": {1: "uno|una", 2: "due", 3: "tre", 4: "quattro", 5: "cinque", 7: "sette", 8: "otto", 9: "nove", 10: "dieci"},
    "en": {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"},
    "fr": {2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit", 9: "neuf", 10: "dix"},
    "es": {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez"},
    "de": {2: "zwei", 3: "drei", 4: "vier", 5: "f\u00fcnf", 6: "sechs", 7: "sieben", 8: "acht", 9: "neun", 10: "zehn"},
    "pt": {2: "dois|duas", 3: "tr\u00eas", 4: "quatro", 5: "cinco", 6: "seis", 7: "sete", 8: "oito", 9: "nove",
           10: "dez"},
}


_NUMBER_WORDS = re.compile(r"\b(?:one|two|twice|double|pair|three|thrice|triple|four|five|six|seven|eight|nine|"
                            r"ten)\b", re.I)
_WORD_VALUE = {"one": 1, "two": 2, "twice": 2, "double": 2, "pair": 2, "three": 3, "thrice": 3, "triple": 3,
                  "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# accented letters of each target language: an accented Latin letter that does not belong to this language (and is
# not in the original) comes from a copied official translation ('crânio' from Portuguese)
_LANGUAGE_LETTERS = {"it": set("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ")}


def _numbers_in_words(o, t, dest_lang):
    """True if the difference in numbers between original and translation is explained by numbers written in words:
    - a digit missing from the translation is there as a word ('1 or more' -> 'uno o piu''), counting only the words
      beyond those that translate words of the original ('twice' -> 'due volte' does not count for a '2');
    - an extra digit in the translation translates a number in words of the original ('restore one' -> 'ripristina
      1')."""
    in_words = collections.Counter(_WORD_VALUE[w.lower()] for w in _NUMBER_WORDS.findall(o))
    articles = len(re.findall(r"\b(?:a|an)\b", o, re.I))  # 'a'/'an' are translated 'un'/'una'
    no, nt = collections.Counter(_numbers(o)), collections.Counter(_numbers(t))
    missing, additional = no - nt, nt - no
    for number, count in additional.items():
        if not number.isdigit() or in_words.get(int(number), 0) < count:
            return False
    if not missing:
        return bool(additional)
    words = _NUMBERS_IN_WORDS.get(dest_lang.split("-")[0])
    if not words:
        return False
    for number, count in missing.items():
        if not number.isdigit():  # '2.5': no words for decimals
            return False
        word = words.get(int(number))
        already = in_words.get(int(number), 0) + (articles if number == "1" else 0)
        if not word or len(re.findall(r"(?<!\w)(?:%s)(?!\w)" % word, t, re.I)) < count + already:
            return False
    return True


_BROKEN_TOKEN = re.compile(r"\[\[?\s*(\d{1,2})\s*[\]\u3015\u3011]{0,2}")
_QUOTES = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e\u300c\u300e"
_THOUSANDS = re.compile(r"(?<=\d)[.,\u00a0\u202f ](?=\d{3}(?!\d))")


# "50k", "1.5M", "2B": thousands, millions, billions (M and B only uppercase: "5m" is meters or minutes)
_NUMBER_SUFFIX = re.compile(r"(\d+(?:\.\d+)?)\s?([kK]|M|B)(?![A-Za-z])")
_BY_SUFFIX = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}


def _with_suffix(m):
    v = float(m.group(1)) * _BY_SUFFIX[m.group(2)]
    return str(int(v)) if v == int(v) else repr(v)


def _numbers(text):
    """The numbers of a text as values comparable across languages: digits of every script ("\uff11\uff10" = 10),
    without thousands separators ("1.000", "1,000", "1 000" = 1000), decimal comma as a point, "50k" = 50000."""
    t = "".join(str(unicodedata.decimal(c)) if not c.isascii() and unicodedata.decimal(c, None) is not None else c
                for c in text)
    t = re.sub(r"(?<=\d),(?=\d)", ".", _THOUSANDS.sub("", t))
    t = _NUMBER_SUFFIX.sub(_with_suffix, t)
    return sorted(re.findall(r"\d+(?:\.\d+)?", t))


def numbers_difference(original, translated):
    """(absent, additional): the numbers of the original that are not in the translation and the extra ones (as values:
    "50k" and "50.000" are the same number), written as in the texts."""
    def values(text):
        output = collections.Counter()
        for m in re.finditer(r"\d+(?:[.,\u00a0\u202f ]\d+)*(?:\s?(?:[kK]|M|B)(?![A-Za-z]))?", text):
            for v in _numbers(m.group()):
                output[(v, m.group())] += 1
        return output
    o, t = values(original), values(translated)
    vo = collections.Counter(v for v, _ in o.elements())
    vt = collections.Counter(v for v, _ in t.elements())
    absent = [s for (v, s) in o if v in vo - vt]
    additional = [s for (v, s) in t if v in vt - vo]
    return absent, additional


def _weight(text):
    """Visual length: a Chinese, Japanese or Korean character is worth about two and a half letters."""
    from .text import _CJK
    return len(text) + 1.5 * len(_CJK.findall(text))
_REPETITION = re.compile(r"(\b\S+\b)(?:\W+\1\b){3,}", re.I)


def translation_problems(original, translated, dest_lang="it"):
    """List of the reasons to reject a translation of the local model (empty = good)."""
    from .text import _TOKEN
    problems = []
    if not translated.strip():
        return ["empty"]
    if "\n" in translated and "\n" not in original:
        problems.append("line break added")
    elif translated.count("\n") != original.count("\n"):  # more or fewer lines: the label overflows its space
        problems.append("different line breaks")
    if _PREAMBLE.match(translated) and not _PREAMBLE.match(original):
        problems.append("surrounding text")
    if _REFUSAL.match(translated) and not _REFUSAL.match(original):
        problems.append("model refusal")
    if dest_lang.split("-")[0] not in ("ru", "uk", "ja", "zh", "ko", "bg") and \
            _OTHER_SCRIPT.search(translated) and not _OTHER_SCRIPT.search(original):
        problems.append("wrong script")
    lo, lt = _weight(original), _weight(translated)
    if lt > 2.5 * lo + 15 or (lo > 20 and lt < 0.3 * lo) or (lo > 80 and lt < 0.5 * lo):
        problems.append("abnormal length")
    if _REPETITION.search(translated) and not _REPETITION.search(original):
        problems.append("ripetizioni")
    if translated.strip() == original.strip() and len(_ENGLISH_WORDS.findall(original)) >= 2:
        problems.append("not translated")
    o, t = _TOKEN.sub(" ", original), _TOKEN.sub(" ", translated)  # the digits of the [[0]] codes are not numbers
    if _numbers(o) != _numbers(t) and sorted(re.findall(r"\d+", o)) != sorted(re.findall(r"\d+", t)) \
            and not _numbers_in_words(o, t, dest_lang):
        problems.append("different numbers")
    if collections.Counter(_TOKEN.findall(original)) != collections.Counter(_TOKEN.findall(translated)):
        problems.append("placeholders")
    own_letters = _LANGUAGE_LETTERS.get(dest_lang.split("-")[0])
    if own_letters and any(c not in own_letters and c not in original and c.isalpha() and not c.isascii()
                       and unicodedata.normalize("NFKD", c)[0].isascii() for c in translated):
        problems.append("letters from another language")
    return problems


class _SlowLine(Exception):
    """A single line went over the maximum time, but the server is alive: that line stays untranslated."""


def _unchanged(original, translated):
    def norm(x):
        return re.sub(r"[\s.!?\u2026]+$", "", x.strip()).casefold()
    return norm(original) == norm(translated)


def seed_for(text, number):
    """The seed of attempt 'number' for a text: always the same for the same pair (even from one run to the next:
    no Python hash(), which changes at every run), different from one attempt to the next and never equal to those
    of the first round (Local.ATTEMPTS: 42, 7, 11)."""
    return zlib.crc32(("%d\x00%s" % (number, text)).encode("utf-8")) % 2_000_000_000 + 100


NOT_INSTALLED = ("The local translator is not installed: choose 'Local translator (on your PC)' and press 'Install...' "
                 "next to it.")
INSTALL_COMMAND = "python -I smith.py local --install 12b (or 4b)"


class Local(Backend):
    """Translator on the user's PC: Ollama + TranslateGemma. No quota and no blocks: it goes at the pace of the
    graphics card. One request per line (the TranslateGemma format expects one text at a time), with checks on
    every answer and a second attempt with different parameters; the lines that do not pass the checks stay
    untranslated (Project retries them right away in another round: see 'rounds' in translate_lines). The server
    starts at the first line and is closed with close()."""

    name = "local"
    limited = False
    uses_context = True  # accepts for each line the key and the game's official translations (see Project)
    # accepts for each line the round (see translate_lines): an already rejected text changes seeds
    varying_seeds = True
    max_texts = 40
    max_chars = 8000
    NUM_CTX = 2048
    ATTEMPTS = ((0.0, 42), (0.3, 7), (0.6, 11))  # first round; the third only for texts rejected twice
    # from the second round (text already rejected, in this run or an earlier one): new seeds at every round (seed_for)
    # and higher temperatures. At temperature 0 the seed does not matter: the same question would give the same answer
    ROUND_TEMPERATURES = (0.3, 0.6, 0.9)

    def __init__(self, model="4b", log=print, server=None, parallel=None):
        from . import ollama
        if model not in ollama.MODELS:
            model = "4b"
        self.key = model
        self.model = ollama.MODELS[model]["name"]
        self.log = log
        self._server = server
        self._owned = server is None
        self._ready = False
        self.token, self.seconds, self.lines_done = 0, 0.0, 0
        self.lines_elapsed = 0.0  # real seconds spent translating (without startup): for the estimate
        self._warned_slow = False
        # lines translated together: with PatchSmith's server as many as the model on the graphics card can handle;
        # with a server given from outside (tests) one at a time, unless told otherwise
        self.parallel = max(1, parallel or (ollama.MODELS[model].get("parallel", 1) if server is None else 1))
        self._lock = threading.Lock()
        # {text: (translation, problems)}: the best proposal for the texts rejected by the checks. Only if it is a
        # dictionary (the live translator, xunity_service, prepares it to answer XUnity anyway)
        self.rejects = None

    def pause(self):
        return None

    def requests(self, texts):
        return [len(t) for t in texts]

    def estimate(self, chars_per_request):
        """Expected seconds: startup (detection of the graphics card and loading of the model) plus the texts, at the
        pace measured on this PC after the first lines (before that: the one from the tests on an RTX 3060 Ti 8 GB)."""
        from . import ollama
        per_text = ollama.MODELS[self.key]["seconds_per_text"]
        if self.lines_done >= 10:
            per_text = self.lines_elapsed / self.lines_done
        startup = 0 if self._ready else ollama.MODELS[self.key]["startup_seconds"]  # graphics card + model
        return startup + sum(per_text * (0.5 + c / 80) for c in chars_per_request)

    def _ensure_server(self):
        from . import ollama
        if self._ready:
            return
        if self._server is None:
            if not ollama.installed():
                raise BackendError(NOT_INSTALLED, "TRANSLATOR_MISSING", command=INSTALL_COMMAND)
            if not ollama.model_present(self.key):
                raise BackendError("The %s model is not downloaded: press 'Install...' next to the local translator."
                                    % ollama.MODELS[self.key]["label"], "TRANSLATOR_MISSING",
                                    command="python -I smith.py local --install %s" % self.key)
            self._server = ollama.Server(self.log, parallel=self.parallel)
        try:
            self._server.launch()
            self.log("    Local translator: loading the model into the graphics card (the first time it can take a "
                     "minute)...")
            self.log(DETAIL + "    (model %s)" % self.model)
            self._server.call("/api/generate", {"model": self.model, "keep_alive": "30m"}, timeout=600)
            loaded = self._server.call("/api/ps").get("models", [])
        except ollama.LocalError as e:
            raise BackendError(str(e)) from None
        except urllib.error.HTTPError as e:
            raise BackendError(self._explain(e)) from None
        except (OSError, ValueError) as e:
            raise BackendError("The local translator is not responding (%s)." % type(e).__name__) from None
        for m in loaded:
            if m.get("name") == self.model and m.get("size"):
                quota = m.get("size_vram", 0) / m["size"]
                self.log((DETAIL if quota > 0.95 else "") +  # fully ready: detail; otherwise the warning is shown
                         "    Local translator ready: %.0f%% of the model in the graphics card%s" % (
                    100 * quota, "" if quota > 0.95 else (" (the rest in memory: slower)" if quota > 0 else
                                                         " - WARNING: using only the processor, much slower: check the "
                                                         "NVIDIA driver")))
        self._ready = True

    def _explain(self, e):
        body = _read_error(e).decode("utf-8", "replace").lower()
        if e.code == 404 or "not found" in body:
            return "The %s model is not downloaded: press 'Install' next to the local translator." % self.model
        if "memory" in body or "out of memory" in body:
            return ("Insufficient memory for the local translator: close the game and other heavy programs, or choose "
                    "the Fast (4B) model.")
        if "cuda" in body:
            return "Graphics card error (CUDA): update the NVIDIA driver and try again."
        return "The local translator encountered an error (HTTP %d)." % e.code

    @staticmethod
    def _english_name(code):
        return ENGLISH_NAMES.get(code, ENGLISH_NAMES.get(code.split("-")[0], code))

    def _context_text(self, context, dest_lang):
        """The prompt's context lines for one line (empty if there is no context: prompt as before)."""
        if not context:
            return ""
        lines = ""
        key = (context.get("key") or "").replace("\n", " ").strip()[:80]
        if key:
            lines += KEY_CONTEXT % key
        references = [(c, t.replace("\n", " ").strip()) for c, t in (context.get("references") or {}).items()
                       if t and t.strip()]
        if references:
            lines += REFERENCES_CONTEXT % (self._english_name(dest_lang), "; ".join(
                "%s: %s" % (self._english_name(c), t) for c, t in references))
        do_not_use = "".join(c for c in dict.fromkeys(context.get("do_not_use") or "") if not c.isspace())
        if do_not_use:
            lines += CHARS_FIX % " ".join(do_not_use)
        width = context.get("width")
        if isinstance(width, int) and width > 0:  # (only when a label is requested shorter)
            lines += WIDTH_CONTEXT % (self._english_name(dest_lang), width)
        return lines

    def _attempts(self, text, round_):
        """[(temperature, seed)] of the attempts for a text: at the first round (0) the fixed ones; from the others,
        seeds derived from the text and the attempt number (seed_for), so every round asks new questions even from
        one run to the next (Project remembers the rounds done in memory/<game>.problemi.json)."""
        if not round_:
            return list(self.ATTEMPTS)
        n = len(self.ROUND_TEMPERATURES)
        return [(t, seed_for(text, round_ * n + i)) for i, t in enumerate(self.ROUND_TEMPERATURES)]

    def other_model(self):
        """A local translator with the other model (Fast <-> Quality), if it is downloaded, for the texts this one
        rejected; None if there is none. This translator's model leaves the graphics card (and, if the server is
        PatchSmith's, the server closes: the new translator starts one with the parameters of its model)."""
        from . import ollama
        other = {"4b": "12b", "12b": "4b"}.get(self.key)
        try:
            if not other or not ollama.model_present(other):
                return None
        except (OSError, KeyError):
            return None
        if self._owned:
            self.close()
            return Local(other, log=self.log)
        if self._server is not None and self._ready:  # server given from outside (the tests): it stays on
            try:
                self._server.call("/api/generate", {"model": self.model, "keep_alive": 0}, timeout=30)
            except (OSError, ValueError):
                pass
        self._ready = False
        return Local(other, log=self.log, server=self._server, parallel=self.parallel)

    def _chat(self, text, origin, dest_lang, temperature, seed, num_predict, context=""):
        sc, tc = origin, dest_lang
        prompt = PROMPT_TRANSLATEGEMMA.format(sl=self._english_name(sc), sc=sc, tl=self._english_name(tc), tc=tc,
                                              context=context, text=text)
        data = {"model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "keep_alive": "30m", "options": {"temperature": temperature, "seed": seed, "num_ctx": self.NUM_CTX,
                                                 "num_predict": num_predict, "top_k": 64, "top_p": 0.95}}
        try:  # maximum time in proportion to how much it can write (even at 2-3 words a second)
            response = self._server.call("/api/chat", data, timeout=120 + num_predict // 2)
        except urllib.error.HTTPError as e:
            raise BackendError(self._explain(e)) from None
        except (OSError, ValueError) as e:
            timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
            if timed_out and self._alive():
                raise _SlowLine() from None
            raise BackendError("The local translator is not responding (%s): try again." % type(e).__name__) from None
        with self._lock:
            self.token += response.get("eval_count", 0)
            self.seconds += response.get("eval_duration", 0) / 1e9
        used = response.get("prompt_eval_count", 0) + response.get("eval_count", 0)
        return (response.get("message") or {}).get("content", ""), response.get("done_reason"), used

    def _alive(self):
        try:
            self._server.call("/api/version", timeout=10)
            return True
        except (OSError, ValueError):
            return False

    def _clean(self, translated, original, dest_lang="it"):
        """Removes what the model adds on its own: quotes, notes in parentheses, several alternative translations
        for a name, final period or ellipsis where the original has none; lowercase initial like the original."""
        t = _CONTROL_CHARS.sub("", translated).strip()
        o = original.strip()
        from .text import _CJK, _TOKEN
        valid_spots = {int(n) for n in _TOKEN.findall(o)}
        if valid_spots:
            # placeholders damaged by the model ('[[4]Spine', '[0]Colpo', '[[2]〕'): they are restored, if the number
            # is one of those of the original and the original does not have that spelling on its own
            rest = _TOKEN.sub(" ", o)  # the original without the correct placeholders

            def repair(m):
                n = int(m.group(1))
                return "[[%d]]" % n if n in valid_spots and m.group(0).strip() not in rest else m.group(0)
            t = _BROKEN_TOKEN.sub(repair, t)
        if "\n" not in o:  # line breaks added by the model (e.g. before each button code): spaces
            t = re.sub(r"[ \t]*\n+[ \t]*", " ", t)
        for open_quote, close_quote in ('""', "''", "\u201c\u201d", "\u00ab\u00bb", "\u300c\u300d", "\u300e\u300f"):
            if len(t) > 1 and t[0] == open_quote and t[-1] == close_quote and o[:1] not in _QUOTES:
                t = t[1:-1].strip()
        if "(" not in o and "\uff08" not in o:  # "corna (di cervo, alce, ecc.)", "punti esperienza (EP)"
            t = re.sub(r"\s*[(\uff08][^()\uff08\uff09\n]{1,40}[)\uff09]", "", t).strip() or t
        if re.fullmatch(r"[^\W_]+(?: [^\W_]+){0,2}", o) and not _CJK.search(o) and "\n" not in t and \
                (";" in t or " " not in o):
            # a name translated with several alternatives: "edificio; costruzione", "incantare, affascinare"
            parts = [x for x in re.split(r"\s*;\s*" if ";" in t else r"\s*,\s*", t.rstrip(".")) if x]
            if len(parts) > 1 and all(len(x.split()) <= 3 for x in parts):
                t = parts[0]
        if not _ENDS_SENTENCE.search(o):
            if re.search(r"(?:\.\.\.|\u2026)$", t):  # ellipsis added to a split sentence
                t = re.sub(r"\s*(?:\.\.\.|\u2026)$", "", t) or t
            elif re.search(r"(?<!\.)[.;,]$", t) and not re.search(r"(?:\b\w\.\w\.|\b(?:ecc|etc|ca)\.)$", t, re.I):
                # "scarpe dorate." for "golden boots" (abbreviations like "U.S.A." stay)
                t = t[:-1].rstrip()
        language = dest_lang.split("-")[0]
        if language != "de" and o[:1].isalpha() and o[:1].islower() and t[:1].isupper() \
                and (t[1:2].islower() or (t[1:2] == " " and language != "en")):  # games all in lowercase (Forager)
            t = t[:1].lower() + t[1:]
        if original[:1].isspace() or original[-1:].isspace():
            t = original[:len(original) - len(original.lstrip())] + t + original[len(original.rstrip()):]
        return t

    def _translate_one(self, text, origin, dest_lang, context=None, round_=0):
        if len(text) > 1500:
            pieces = [self._translate_one(p, origin, dest_lang, round_=round_) for p in _split_long(text, 1500)]
            return "" if any(not p for p in pieces) else " ".join(pieces)
        from .text import _TOKEN
        last, echo, echoes_seen = "", None, 0
        same_language = origin.split("-")[0] == dest_lang.split("-")[0]
        current = dict(context or {})
        key = re.sub(r"[\W_]+", "", current.get("key") or "").casefold()
        references = [r for r in (current.get("references") or {}).values() if r and r.strip()]
        attempts, fix = self._attempts(text, round_), ""
        proposal = None  # (translation, problems) rejected with the fewest problems: for self.rejects
        for n, (temperature, seed) in enumerate(attempts):
            base = self._context_text(current, dest_lang)
            num_predict = min(1024, 64 + 2 * (len(text) // 3 + 1))
            for _ in range(2):
                try:
                    raw, end, _used = self._chat(text, origin, dest_lang, temperature, seed, num_predict,
                                                      base + fix)
                except _SlowLine:
                    self.log('    ! A text is taking the local translator too long: it stays untranslated ("%s")'
                             % text[:50])
                    return ""
                more_room = min(2 * num_predict, self.NUM_CTX - 256)  # truncated: more room, within the context
                if end != "length" or more_room <= num_predict:
                    break
                num_predict = more_room
            translated = self._clean(clean(raw), text, dest_lang)
            problems = translation_problems(text, translated, dest_lang)
            if len(key) >= 6 and key in re.sub(r"[\W_]+", "", translated).casefold() \
                    and key not in re.sub(r"[\W_]+", "", text).casefold():
                problems.append("copied key")  # the model wrote the hint instead of the translation
                current["key"] = None  # the next attempt, without the hint
            if references and not _unchanged(text, translated) and any(_unchanged(r, translated) for r in references):
                problems.append("copied reference")  # an official translation of another language, copied
                current["references"] = {}  # next attempt, without the references (if the same again, it is right)
                references = []
            fix = ""
            if "different numbers" in problems and re.search(r"\d", text):
                fix += NUMBERS_FIX
            if "placeholders" in problems:
                fix += PLACEHOLDER_FIX
            if fix and n == 0:
                attempts[1] = attempts[0]  # the same question, with the correction (then the third attempt remains)
            if not problems:
                if same_language or not _unchanged(text, translated):
                    return translated
                # equal to the original: we believe it only if it repeats
                echo, echoes_seen = translated, echoes_seen + 1
                if echoes_seen >= 2:
                    return translated  # a text that stayed the same twice is a name or a word that does not change
            elif problems == ["placeholders"] and _TOKEN.search(text):
                last = translated
            if translated.strip() and (proposal is None or len(problems) < len(proposal[1])):
                proposal = (translated, problems)
        if self.rejects is not None and proposal is not None:
            self.rejects[text] = proposal
        # with only the placeholders wrong the translation goes back to PatchSmith, which re-translates the pieces
        # between the codes
        return last

    def translate_lines(self, lines, origin, dest_lang, contexts=None, rounds=None):
        """Translates the lines, 'parallel' at a time (the graphics card processes several together); the order stays
        that of the lines. With 'Stop' or an error, those already translated are in e.partials. rounds: for each line
        how many times it has already been tried (0 or missing: first round, see _attempts)."""
        self._ensure_server()
        contexts = list(contexts or [])
        rounds = list(rounds or [])
        output = [None] * len(lines)
        start = time.monotonic()

        def one(i):
            interruption.check()
            return self._translate_one(lines[i], origin, dest_lang, contexts[i] if i < len(contexts) else None,
                                     rounds[i] if i < len(rounds) else 0)
        try:
            if self.parallel <= 1 or len(lines) <= 1:
                for i in range(len(lines)):
                    output[i] = one(i)
                    self._line_done()
            else:
                pool = concurrent.futures.ThreadPoolExecutor(self.parallel)
                futures = {pool.submit(one, i): i for i in range(len(lines))}
                try:
                    for f in concurrent.futures.as_completed(futures):
                        output[futures[f]] = f.result()
                        self._line_done()
                except BaseException:
                    pool.shutdown(wait=True, cancel_futures=True)  # the requests already sent finish
                    for f, i in futures.items():
                        if output[i] is None and f.done() and not f.cancelled() and f.exception() is None:
                            output[i] = f.result()
                    raise
                pool.shutdown()
        except BaseException as e:  # "Stop" or error halfway: the lines already translated are not lost
            e.partials = [u if u is not None else "" for u in output]
            raise
        finally:
            self.lines_elapsed += time.monotonic() - start
        return output

    def _line_done(self):
        self.lines_done += 1
        if self.lines_done == 20 and self.seconds > 0 and not self._warned_slow:
            per_second = self.token / self.seconds
            self._warned_slow = True
            self.log(DETAIL + "    Local translator: %.0f tokens per second%s" % (
                per_second, ", %d lines together" % self.parallel if self.parallel > 1 else ""))
            if per_second < 6:
                self.log("    ! It's slow: if the game is open, close it; otherwise try the Fast model (4B).")

    def close(self):
        """Frees the graphics card and closes the server (if this translator started it)."""
        if self._server is not None and self._ready:
            try:
                self._server.call("/api/generate", {"model": self.model, "keep_alive": 0}, timeout=30)
            except (OSError, ValueError):
                pass
        if self._server is not None and self._owned:
            self._server.close()
            self._server = None
        self._ready = False


BACKEND = {"local": Local, "google": GoogleFree, "deepl": DeepL, "mymemory": MyMemory, "fake": Fake}
DESCRIPTIONS = {
    "local": "Local translator (on your PC: no limits, no blocks)",
    "google": "Free Google (unofficial, often blocked: Google may reject it)",
    "deepl": "DeepL (free with your key, 500,000 characters per month)",
    "mymemory": "MyMemory (without an account, 50,000 characters per day with your email)",
}


def credentials():
    """DeepL key and email for MyMemory: from the environment variables or from settings.json."""
    from . import project
    data = {}
    try:
        with open(project.data_folder("settings.json"), encoding="utf-8") as f:
            data = legacy.load(f)
    except (OSError, ValueError):
        pass
    return {"deepl_key": data.get("deepl_key") or os.environ.get("PATCHSMITH_DEEPL") or "",
            "email_mymemory": data.get("email_mymemory") or os.environ.get("PATCHSMITH_EMAIL") or "",
            "deepl_from_env": not data.get("deepl_key") and bool(os.environ.get("PATCHSMITH_DEEPL")),
            "local_model": data.get("local_model") or os.environ.get("PATCHSMITH_MODEL")
            or os.environ.get("PATCHSMITH_MODELLO") or ""}


def default():
    """The proposed translator: always the local one (no limits, no blocks), even if it is not installed yet.
    On a new PC the first step is to install it ("to install" and the "Install..." button next to it): free Google
    often rejects PatchSmith's requests from the very first one."""
    return "local"


def probe_local(model=None, log=print, text="Start game", a="it", server=None):
    """Test of the local translator (after installation): starts the server, loads the model and translates a
    sentence, measuring the times. {'ok', 'translation', 'startup' (seconds to start the server and load the model),
    'seconds' (for the sentence), 'gpu' (how much of the model is on the graphics card, from 0 to 1; None if
    unknown), 'message' (for the player)}. Does not raise translator errors: if the test fails, the message says so.
    server: for the tests."""
    from . import ollama
    key = model if model in ollama.MODELS and (server is not None or ollama.model_present(model)) \
        else ollama.default_model()
    a = "it" if (a or "en").split("-")[0].lower() == "en" else a  # (in English the sentence would stay the same)
    translator = Local(key, log=log, server=server)
    output = {"ok": False, "translation": None, "startup": None, "seconds": None, "gpu": None, "model": key}
    start = time.monotonic()
    try:
        translator._ensure_server()
        output["startup"] = time.monotonic() - start
        try:
            loaded = translator._server.call("/api/ps").get("models", [])
        except (OSError, ValueError, AttributeError):
            loaded = []
        for m in loaded:
            if m.get("name") == translator.model and m.get("size"):
                output["gpu"] = max(0.0, min(1.0, m.get("size_vram", 0) / m["size"]))
        before = time.monotonic()
        response = translator.translate_lines([text], "en", a)[0]
        output["seconds"] = time.monotonic() - before
    except Exception as e:  # noqa: BLE001 - the installation succeeded: here we only say how the test went
        output["message"] = "the translation test failed: %s" % (
            e if isinstance(e, BackendError) else "%s: %s" % (type(e).__name__, e))
        return output
    finally:
        translator.close()
    output["translation"] = response
    if not response or _unchanged(response, text):
        output["message"] = ('the translation test failed: "%s" stayed untranslated. Try again; if it happens again, '
                               "create a report." % text)
        return output
    output["ok"] = True
    sheet = output["gpu"]
    where = ("" if sheet is None else ", on the graphics card" if sheet > 0.95 else
            ", partly on the graphics card (a bit slower)" if sheet > 0 else
            ", but only with the processor, without a graphics card: it will be slow (update the graphics card driver, "
            'or choose the "Fast" model)')
    output["message"] = "ready%s: \"%s\" -> \"%s\" in %s (startup and model loading: %s)." % (
        where, text, response, _seconds(output["seconds"]), _seconds(output["startup"]))
    return output


def _seconds(value_):
    return "%.1f s" % value_ if value_ < 10 else "%d s" % round(value_)


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verify_translator(name, credential, opener=None):
    """(ok, message) for the "Check" button: does the DeepL key or the MyMemory email really work?"""
    try:
        if name == "deepl":
            used, limit = DeepL(credential, log=lambda _m: None, opener=opener).verify()
            if limit:
                return True, ("The DeepL key works. This month you've used %s of %s characters (%s left)."
                              % tuple(format(n, ",d") for n in (used, limit, max(0, limit - used))))
            return True, "The DeepL key is working."
        if name == "mymemory":
            email = (credential or "").strip()
            if email and not EMAIL.match(email):
                return False, "\"%s\" doesn't seem to be a valid email address: please check that it's written " \
                    "correctly (e.g., name@example.it)." % email
            MyMemory(email or None, log=lambda _m: None, opener=opener).verify()
            if email:
                return True, ("MyMemory responds and accepts the email: you can translate up to 50,000 characters per "
                              "day. MyMemory doesn't use accounts and doesn't send confirmations: the email only "
                              "raises the limit (without it: 5,000 characters per day).")
            return True, ("MyMemory responds: Without email, you can translate 5,000 characters per day. With your "
                          "email, you can translate up to 50,000 (no account required).")
    except BackendError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 - no network or odd answer: explain it, no exceptions in the window
        return False, ("I'm unable to reach the service (%s). Please check your connection and try again."
                       % type(e).__name__)
    return False, "This translator has nothing to verify."


def create_backend(name, log=print):
    if name not in BACKEND:
        raise ValueError("Unknown backend: %s (available: %s)" % (name, ", ".join(BACKEND)))
    if name == "google":
        return GoogleFree(log=log)
    if name == "deepl":
        c = credentials()
        d = DeepL(c["deepl_key"], log=log)
        d.from_variable = c["deepl_from_env"]
        return d
    if name == "mymemory":
        return MyMemory(credentials()["email_mymemory"], log=log)
    if name == "local":
        from . import ollama
        if ollama.state() != "ready":  # right away: before installing or changing anything in the game
            raise BackendError(NOT_INSTALLED, "TRANSLATOR_MISSING", command=INSTALL_COMMAND)
        model = credentials()["local_model"]
        if model not in ollama.MODELS or not ollama.model_present(model):
            if model in ollama.MODELS:
                log("  The %s model is not downloaded: using %s." % (ollama.MODELS[model]["label"],
                                                                  ollama.MODELS[ollama.default_model()]["label"]))
            model = ollama.default_model()
        return Local(model, log=log)
    return Fake()
