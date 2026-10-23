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

from . import interruption as interruzione
from .text import DETTAGLIO

MAX_BLOCCO = 4000  # characters per request (margin below the roughly 5,000 accepted)


class ErroreBackend(Exception):
    """The translation service cannot be reached or rejects the requests.

    codice: the outcome for the interface (see outcome.py), e.g. TRADUTTORE_ASSENTE; comando: what to type on the
    command line to fix it (only the command line shows it: the messages talk about the interface's buttons)."""

    def __init__(self, messaggio="", codice=None, comando=None):
        super().__init__(messaggio)
        self.codice = codice
        self.comando = comando


class Backend:
    nome = "base"

    def traduci_righe(self, righe, origine, destinazione):
        """Translates each line (without line breaks). Returns a list of the same length."""
        raise NotImplementedError


class Finto(Backend):
    """Test backend: does not use the network, puts [it] before the text. Its translations end up in a separate
    translation memory (*.prova.json), so they do not pollute the real one."""

    nome = "finto"

    def traduci_righe(self, righe, origine, destinazione):
        interruzione.controlla()
        return ["[%s] %s" % (destinazione, r) for r in righe]


# invisible characters that Google sometimes inserts (e.g."​​gli"): in games they can show up
# as little squares or, in XNA, make the game crash
_INVISIBILI = dict.fromkeys(map(ord, "​⁠﻿"))


def pulisci(testo):
    return testo.translate(_INVISIBILI)


def _dividi_lunga(riga, massimo=MAX_BLOCCO):
    """Splits a line that is too long into pieces by sentences (or by spaces) of at most `massimo` characters."""
    pezzi, attuale = [], ""
    for frase in re.split(r"(?<=[.!?。！？])\s+", riga):
        if len(frase) > massimo and attuale:  # the previous sentence first: the order must not change
            pezzi.append(attuale)
            attuale = ""
        while len(frase) > massimo:  # huge sentence without punctuation: cut on the spaces
            taglio = frase.rfind(" ", 0, massimo)
            taglio = taglio if taglio > 0 else massimo
            pezzi.append(frase[:taglio])
            frase = frase[taglio:].lstrip()
        if attuale and len(attuale) + 1 + len(frase) > massimo:
            pezzi.append(attuale)
            attuale = frase
        else:
            attuale = (attuale + " " + frase) if attuale else frase
    if attuale:
        pezzi.append(attuale)
    return pezzi


class Bloccato(ErroreBackend):
    """The service rejected the requests (or is paused because of a previous rejection): we do not insist."""


def _apri_predefinito(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout)


def _leggi_errore(e):
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


UA = "Patchsmith/%s (traduttore di videogiochi)"


def _ua():
    from . import __version__
    return UA % __version__


class GoogleGratis(Backend):
    """Free web endpoint of Google Translate (unofficial, without a key).

    Google does not publish limits and can reject automated access entirely: PatchSmith goes slowly (rate limiter
    with cautious margins, shared between the interface and the command line) and at the FIRST rejection it stops
    and remembers until when not to retry, without insisting and without looking for other ways around the block.
    """

    nome = "google"
    URL = "https://translate.googleapis.com/translate_a/single"
    max_caratteri = 3600  # a group of texts = one request (the placeholders make the text a bit longer)
    max_testi = 150
    ATTESE_RETE = (10, 30)

    def __init__(self, log=print, apri=None, limitatore=None):
        from .limiter import Limitatore
        self.log = log
        self.apri = apri or _apri_predefinito
        self.limitatore = limitatore or Limitatore("google", log)

    def pausa(self):
        return self.limitatore.pausa()

    def stima(self, caratteri_per_richiesta):
        return self.limitatore.stima(caratteri_per_richiesta)

    def _blocco(self, http_codice, dettaglio):
        raise Bloccato(self.limitatore.bloccato(http_codice, dettaglio))

    def _richiesta(self, testo, origine, destinazione):
        from .limiter import InPausa
        parametri = urllib.parse.urlencode({"client": "gtx", "sl": origine, "tl": destinazione, "dt": "t"})
        corpo = urllib.parse.urlencode({"q": testo}).encode("utf-8")
        errori = 0
        while True:
            interruzione.controlla()
            try:
                self.limitatore.prima_di(len(testo))
            except InPausa as e:
                raise Bloccato(str(e)) from e
            req = urllib.request.Request(self.URL + "?" + parametri, data=corpo, headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=utf-8", "User-Agent": _ua()})
            try:
                with self.apri(req, 30) as risposta:
                    finale = risposta.geturl() or ""
                    tipo = risposta.headers.get("Content-Type", "") if risposta.headers else ""
                    grezzo = risposta.read()
                if "consent.google" in finale:
                    raise ErroreBackend("Google chiede di accettare i cookie (pagina di consenso): da questo PC il "
                                        "traduttore gratuito non e' utilizzabile. Scegli un altro traduttore.")
                if "/sorry" in finale or "html" in tipo.lower():
                    self._blocco(200, "pagina di blocco al posto della traduzione")
                dati = json.loads(grezzo.decode("utf-8"))
                if not (isinstance(dati, list) and dati and isinstance(dati[0], (list, type(None)))):
                    raise ErroreBackend("Google ha risposto in un formato inatteso: riprova piu' tardi.")
                testo_tradotto = "".join(seg[0] for seg in (dati[0] or []) if isinstance(seg, list) and seg
                                         and isinstance(seg[0], str))
                self.limitatore.ok()
                return pulisci(testo_tradotto)
            except urllib.error.HTTPError as e:
                corpo_errore = _leggi_errore(e).lower()
                finale = e.geturl() or ""
                tipo_errore = (e.headers.get("Content-Type", "") if e.headers else "").lower()
                html_errore = b"<html" in corpo_errore or b"<!doctype html" in corpo_errore or "html" in tipo_errore
                if e.code in (429, 403) or "/sorry" in finale or html_errore or b"unusual traffic" in corpo_errore:
                    self._blocco(e.code, "pagina \"Sorry\" di Google" if b"sorry" in corpo_errore else None)
                if 400 <= e.code < 500:
                    raise ErroreBackend("Google rifiuta la richiesta (HTTP %d): controlla le lingue scelte (%s -> %s)"
                                        % (e.code, origine, destinazione)) from e
                errore = "HTTP %d" % e.code
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError, IndexError, TypeError) as e:
                errore = e
            errori += 1
            if errori > len(self.ATTESE_RETE):
                raise ErroreBackend("Google non risponde (%s): riprova piu' tardi." % errore)
            attesa = self.ATTESE_RETE[errori - 1]
            self.log("    ! errore di rete (%s): riprovo tra %d s (tentativo %d di %d)"
                     % (errore, attesa, errori + 1, len(self.ATTESE_RETE) + 1))
            interruzione.attendi(attesa)

    def _traduci_indici(self, parti, indici, tradotti, origine, destinazione, profondita=0):
        """Translates a block of lines; if Google merges or splits lines, retries in halves (at most 3 times), so a
        burst of requests never starts. The lines that do not line up stay untranslated."""
        testo = "\n".join(parti[k][1] for k in indici)
        risposta = self._richiesta(testo, origine, destinazione).split("\n")
        if len(risposta) == len(indici):
            for k, t in zip(indici, risposta):
                tradotti[k] = t.strip()
            return
        if profondita >= 3 or len(indici) == 1:
            self.log("    ! %d righe non allineate nella risposta: restano in originale" % len(indici))
            return
        meta = len(indici) // 2
        self.log("    righe non allineate: riprovo in due meta' (%d + %d righe)" % (meta, len(indici) - meta))
        self._traduci_indici(parti, indici[:meta], tradotti, origine, destinazione, profondita + 1)
        self._traduci_indici(parti, indici[meta:], tradotti, origine, destinazione, profondita + 1)

    def traduci_righe(self, righe, origine, destinazione):
        # Lines that are too long are split by sentences and then put back together.
        parti = []  # (line index, piece)
        for i, r in enumerate(righe):
            for pezzo in (_dividi_lunga(r) if len(r) > MAX_BLOCCO else [r]):
                parti.append((i, pezzo))
        blocchi = _blocchi([p for _, p in parti], MAX_BLOCCO)
        tradotti = [None] * len(parti)

        def risultati():
            uniti = [[] for _ in righe]
            for (i, _), t in zip(parti, tradotti):
                uniti[i].append(t)
            return ["" if any(t is None for t in r) else " ".join(r) for r in uniti]
        try:
            for n, indici in enumerate(blocchi, 1):
                self.log("    blocco %d/%d (%d righe)" % (n, len(blocchi), len(indici)))
                self._traduci_indici(parti, indici, tradotti, origine, destinazione)
        except BaseException as e:  # block or "Stop" halfway: the lines already translated stay usable
            e.parziali = risultati()
            raise
        return risultati()


def _blocchi(pezzi, massimo, massimo_testi=None):
    """Indices of the pieces grouped into blocks of at most 'massimo' characters (line breaks included)."""
    blocchi, blocco, lunghezza = [], [], 0
    for k, pezzo in enumerate(pezzi):
        if blocco and (lunghezza + len(pezzo) + 1 > massimo or (massimo_testi and len(blocco) >= massimo_testi)):
            blocchi.append(blocco)
            blocco, lunghezza = [], 0
        blocco.append(k)
        lunghezza += len(pezzo) + 1
    if blocco:
        blocchi.append(blocco)
    return blocchi


# --- DeepL (official API, personal key: free up to 500,000 characters a month) ----------------------

def _lingua_deepl(codice, destinazione):
    c = (codice or "").replace("_", "-")
    base = c.split("-")[0].lower()
    if base == "zh":
        from .language import chiave_confronto
        return ("ZH-HANT" if chiave_confronto(c) == "zh-hant" else "ZH-HANS") if destinazione else "ZH"
    if destinazione and base == "en":
        return "EN-GB" if c.lower() in ("en-gb", "en-uk") else "EN-US"
    if destinazione and base == "pt":
        return "PT-BR" if c.lower() == "pt-br" else "PT-PT"
    return {"no": "NB"}.get(base, base.upper())


class DeepL(Backend):
    """Official DeepL API with the user's key (free account: 500,000 characters a month). Free keys end with ':fx'
    and use api-free.deepl.com. Lists of texts are sent: no lines to line up."""

    nome = "deepl"
    max_testi = 50
    max_caratteri = 30_000
    ATTESE = (5, 15, 45)
    ATTESA_MASSIMA = 300.0  # seconds: at most those asked by DeepL (Retry-After) before retrying

    def __init__(self, chiave, log=print, apri=None, limitatore=None):
        from .limiter import Limitatore
        chiave = "".join((chiave or "").split()).replace("\u200b", "").replace("\ufeff", "")
        if not chiave:
            raise ErroreBackend("Per usare DeepL serve la tua chiave (gratuita): crea un account DeepL API Free su "
                                "deepl.com/pro-api e incollala nel campo 'Chiave DeepL' dell'interfaccia (oppure "
                                "nella variabile PATCHSMITH_DEEPL).", "TRADUTTORE_ASSENTE")
        if not re.fullmatch(r"[0-9A-Za-z:-]+", chiave):
            raise ErroreBackend("La chiave DeepL contiene caratteri non validi: ricopiala dal sito di DeepL.",
                                "TRADUTTORE_ASSENTE")
        self.chiave = chiave
        self.da_variabile = False
        self.base = "https://api-free.deepl.com" if chiave.endswith(":fx") else "https://api.deepl.com"
        self.log = log
        self.apri = apri or _apri_predefinito
        self.limitatore = limitatore or Limitatore("deepl", log)

    def pausa(self):
        return self.limitatore.pausa()

    def stima(self, caratteri_per_richiesta):
        return self.limitatore.stima(caratteri_per_richiesta, durata_risposta=1.0)

    def verifica(self):
        """(characters used, limit) of this month: proves that the key works (GET /v2/usage, no text)."""
        dati = self._chiama("/v2/usage")
        return int(dati.get("character_count") or 0), int(dati.get("character_limit") or 0)

    def _chiama(self, percorso, corpo=None):
        from .limiter import InPausa
        errori = 0
        while True:
            interruzione.controlla()
            try:
                self.limitatore.prima_di(len(corpo or b""))
            except InPausa as e:
                raise Bloccato(str(e)) from e
            req = urllib.request.Request(self.base + percorso, data=corpo, headers={
                "Authorization": "DeepL-Auth-Key " + self.chiave, "Content-Type": "application/json",
                "User-Agent": _ua()})
            try:
                try:
                    risposta = self.apri(req, 60)
                except (ValueError, UnicodeError) as e:  # request that cannot be built (e.g. the key): never retry
                    raise ErroreBackend("Non riesco a preparare la richiesta per DeepL (%s): controlla la chiave."
                                        % type(e).__name__) from None
                with risposta:
                    dati = json.loads(risposta.read().decode("utf-8"))
                self.limitatore.ok()
                return dati
            except urllib.error.HTTPError as e:
                dettaglio = _leggi_errore(e).decode("utf-8", "replace")[:200]
                if e.code == 403:
                    raise ErroreBackend("DeepL non accetta la chiave (HTTP 403): controlla di averla copiata intera "
                                        "nel campo 'Chiave DeepL'%s." % (" (ora viene dalla variabile PATCHSMITH_DEEPL)"
                                                                         if self.da_variabile else "")) from e
                if e.code == 456:
                    raise ErroreBackend("Hai finito i caratteri gratuiti di DeepL di questo mese: le traduzioni fatte "
                                        "restano salvate, riprendi il mese prossimo o scegli un altro traduttore.") from e
                if e.code == 400:
                    raise ErroreBackend("DeepL rifiuta la richiesta (%s): forse la lingua non e' supportata."
                                        % dettaglio) from e
                if e.code != 429 and e.code < 500:
                    raise ErroreBackend("DeepL: errore HTTP %d (%s)" % (e.code, dettaglio)) from e
                errore = "HTTP %d" % e.code
                attesa_suggerita = e.headers.get("Retry-After") if e.headers else None
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                errore, attesa_suggerita = type(e).__name__, None  # never the error's text: it can contain the key
            errori += 1
            if errori > len(self.ATTESE):
                raise ErroreBackend("DeepL non risponde (%s): riprova piu' tardi." % errore)
            try:
                attesa = min(self.ATTESA_MASSIMA, float(attesa_suggerita)) if attesa_suggerita else \
                    self.ATTESE[errori - 1]
            except ValueError:
                attesa = self.ATTESE[errori - 1]
            self.log("    ! DeepL: %s, riprovo tra %d s (tentativo %d di %d)"
                     % (errore, attesa, errori + 1, len(self.ATTESE) + 1))
            interruzione.attendi(attesa)

    def utilizzo(self):
        """(characters used, limit) of the month, or None."""
        try:
            dati = self._chiama("/v2/usage")
            return dati.get("character_count"), dati.get("character_limit")
        except ErroreBackend:
            return None

    def traduci_righe(self, righe, origine, destinazione):
        uscita = []
        for indici in _blocchi(righe, self.max_caratteri, self.max_testi):
            richiesta = {"text": [righe[k] for k in indici], "target_lang": _lingua_deepl(destinazione, True)}
            if origine and origine != "auto":
                richiesta["source_lang"] = _lingua_deepl(origine, False)
            self.log("    DeepL: %d testi" % len(indici))
            dati = self._chiama("/v2/translate", json.dumps(richiesta, ensure_ascii=False).encode("utf-8"))
            traduzioni = [t.get("text", "") for t in dati.get("translations", [])]
            if len(traduzioni) != len(indici):
                raise ErroreBackend("DeepL ha risposto con %d traduzioni invece di %d" % (len(traduzioni), len(indici)))
            uscita += [pulisci(t) for t in traduzioni]
        return uscita


# --- MyMemory (public API, no account: 5,000 characters a day, 50,000 with your own email) ------------------

def _dividi_byte(riga, massimo=480):
    """Pieces of at most 'massimo' UTF-8 bytes (MyMemory accepts at most 500 bytes per request)."""
    pezzi = []
    for pezzo in _dividi_lunga(riga, massimo):
        while len(pezzo.encode("utf-8")) > massimo:
            # how many characters really fit in 'massimo' bytes (even with mixed text, e.g. Japanese + English)
            taglio = max(1, len(pezzo.encode("utf-8")[:massimo].decode("utf-8", "ignore")))
            spazio = pezzo.rfind(" ", 0, taglio)
            taglio = spazio if spazio > 0 else taglio
            pezzi.append(pezzo[:taglio])
            pezzo = pezzo[taglio:].lstrip()
        if pezzo:
            pezzi.append(pezzo)
    return pezzi


class MyMemory(Backend):
    """Public API of MyMemory (Translated): no account. Without email 5,000 characters a day, with your own email
    (parameter 'de') 50,000. One request per line (at most 500 bytes)."""

    nome = "mymemory"
    URL = "https://api.mymemory.translated.net/get"
    max_testi = 10  # one request per line: small groups, so a block halfway loses little
    max_caratteri = 800
    QUOTA = (5_000, 50_000)  # characters a day without / with email

    def richieste(self, testi):
        """Characters of each request that will be sent (one per line, several pieces for long lines)."""
        return [len(p) for t in testi for p in (_dividi_byte(t) if len(t.encode("utf-8")) > 480 else [t])]

    def quota(self):
        return self.QUOTA[1] if self.email else self.QUOTA[0]

    def verifica(self):
        """Translates "Hello" (5 characters) with the given email: MyMemory answers and accepts the email. There is no
        account to check: the email only serves to get more characters a day."""
        return self._richiesta("Hello", "en", "it")

    def __init__(self, email=None, log=print, apri=None, limitatore=None):
        from .limiter import Limitatore
        self.email = (email or "").strip() or None
        self.log = log
        self.apri = apri or _apri_predefinito
        self.limitatore = limitatore or Limitatore("mymemory", log)

    def pausa(self):
        blocco = self.limitatore.blocco_attuale()
        if blocco and blocco.get("tipo") == "quota" and self.email and not blocco.get("con_email"):
            self.limitatore.azzera()  # quota without email used up: with the email we can go on
            return None
        return self.limitatore.pausa()

    def stima(self, caratteri_per_richiesta):
        return self.limitatore.stima(caratteri_per_richiesta, durata_risposta=0.8)

    def _quota_finita(self):
        adesso = datetime.datetime.fromtimestamp(self.limitatore.ora())
        domani = (adesso + datetime.timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)
        self.limitatore.bloccato(429, "caratteri gratuiti del giorno finiti", fino=domani.timestamp(),
                                 extra={"con_email": bool(self.email)})
        return ("Hai usato i caratteri gratuiti di oggi di MyMemory (%s): le traduzioni fatte restano salvate, "
                "riprendi domani%s." % ("50.000 con la tua email" if self.email else "5.000 senza email",
                                        "" if self.email else " o indica la tua email per arrivare a 50.000"))

    def _richiesta(self, testo, origine, destinazione):
        from .limiter import InPausa
        parametri = {"q": testo, "langpair": "%s|%s" % (origine, destinazione)}
        if self.email:
            parametri["de"] = self.email
        errori = 0
        while True:
            interruzione.controlla()
            try:
                self.limitatore.prima_di(len(testo))
            except InPausa as e:
                raise Bloccato(str(e)) from e
            req = urllib.request.Request(self.URL + "?" + urllib.parse.urlencode(parametri),
                                         headers={"User-Agent": _ua()})
            try:
                with self.apri(req, 30) as risposta:
                    dati = json.loads(risposta.read().decode("utf-8"))
                stato = str(dati.get("responseStatus"))
                tradotto = (dati.get("responseData") or {}).get("translatedText") or ""
                if dati.get("quotaFinished") or stato == "429" or "MYMEMORY WARNING" in tradotto.upper():
                    raise Bloccato(self._quota_finita())
                if stato != "200":
                    if "LENGTH" in str(dati.get("responseDetails", "")).upper():
                        self.log("    ! MyMemory: testo troppo lungo, resta in originale")
                        return ""  # it only concerns this line: the work goes on
                    raise ErroreBackend("MyMemory rifiuta la richiesta (%s): %s" % (stato, dati.get("responseDetails")))
                self.limitatore.ok()
                return pulisci(html.unescape(tradotto))
            except urllib.error.HTTPError as e:
                _leggi_errore(e)
                if e.code == 429:
                    raise Bloccato(self._quota_finita()) from e
                if e.code < 500:
                    raise ErroreBackend("MyMemory: errore HTTP %d" % e.code) from e
                errore = "HTTP %d" % e.code
            except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
                errore = e
            errori += 1
            if errori > 2:
                raise ErroreBackend("MyMemory non risponde (%s): riprova piu' tardi." % errore)
            self.log("    ! MyMemory: %s, riprovo tra %d s" % (errore, 10 * errori))
            interruzione.attendi(10 * errori)

    def traduci_righe(self, righe, origine, destinazione):
        uscita = []
        try:
            for riga in righe:
                pezzi = _dividi_byte(riga) if len(riga.encode("utf-8")) > 480 else [riga]
                tradotti = [self._richiesta(p, origine, destinazione).strip() for p in pezzi]
                uscita.append("" if any(not t for t in tradotti) else " ".join(tradotti))
        except BaseException as e:  # quota used up or "Stop" halfway: the lines already translated are not lost
            e.parziali = uscita + [""] * (len(righe) - len(uscita))
            raise
        return uscita


# --- Local translator (Ollama + TranslateGemma on the user's PC: no limits, no blocks) ---------------------

NOMI_INGLESI = {
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
    "player informally.\n{contesto}Produce only the {tl} translation, without any additional explanations or "
    "commentary. Please translate the following {sl} text into {tl}:\n\n\n{testo}")
# Context per line (tests on Forager, October 2026: 4B from 43% to 64% good translations, 12B from 73% to 80%):
# the text's key in the file (without the final number) and the game's official translations, not to be copied.
CONTESTO_CHIAVE = "In the game's files this text has the identifier \"%s\", a hint of where it appears.\n"
CONTESTO_RIFERIMENTI = ("The game's official translations of this text into other languages, only to understand its "
                        "meaning (write your answer in %s, do not copy them; names of people and games stay as in "
                        "the original): %s\n")
# second attempt after a rejection because of the numbers (tests: '200% more' -> 'il doppio' fixed into
# 'il 200% in piu'')
CORREZIONE_SEGNAPOSTO = ("Copy every marker like [[0]], [[1]] exactly as written, around the same words: they are "
                         "game codes.\n")
CORREZIONE_NUMERI = ("Keep every number exactly as written in the original: digits stay digits (200% stays 200%, "
                     "1 stays 1) and numbers written in words stay words.\n")
# text re-translated because the previous translation could not be written to the game (see Progetto.applica)
CORREZIONE_CARATTERI = "The game's font cannot draw these characters, so do not use them: %s\n"
# short label requested again because the translation was too long for its space (Progetto._accorcia)
CONTESTO_LARGHEZZA = ("In the game this text is a short label with little space: the %s translation must be at most %d "
                      "characters long. Use a shorter word or synonym if needed, keeping the meaning.\n")
_CONTROLLO = re.compile(r"<\s*/?\s*(?:end_of_turn|start_of_turn)\s*>|<\|[^|>]*\|>|<｜[^｜>]*｜?>")
_PREAMBOLO = re.compile(r"^\s*(?:translation|translated text|traduzione|testo tradotto|here is the translation|"
                        r"here's the translation|ecco la traduzione|note|nota)\b[^:\n]{0,40}:", re.I)
_RIFIUTO = re.compile(r"^\s*(?:i can'?t|i cannot|i'm sorry|i am sorry|i'm unable|i am unable|as an ai|mi dispiace|"
                      r"non posso|non sono in grado)\b.{0,80}?\b(?:translat\w*|tradu\w*|assist\w*|"
                      r"this (?:text|request|content)|questo (?:testo|contenuto))", re.I | re.S)
_ALTRA_SCRITTURA = re.compile(r"[Ѐ-ӿ぀-ヿ㐀-䶿一-鿿가-힯]")
_PAROLE_INGLESI = re.compile(r"\b(the|of|and|you|your|to|is|are|with|for|this|that)\b", re.I)
_NUMERO = re.compile(r"\d+(?:[.,]\d+)?")
_CHIUDE_FRASE = re.compile(r"[.!?:;\u2026\u3002\uff01\uff1f][\"')\]\u201d\u00bb\u300d\u300f\uff09]*$")
# numbers in words accepted in place of digits ("1 or more" -> "uno o piu'"): only unambiguous words (no articles
# like 'un'/'a', no 'sei' which is also 'tu sei')
_NUMERI_IN_LETTERE = {
    "it": {1: "uno|una", 2: "due", 3: "tre", 4: "quattro", 5: "cinque", 7: "sette", 8: "otto", 9: "nove", 10: "dieci"},
    "en": {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"},
    "fr": {2: "deux", 3: "trois", 4: "quatre", 5: "cinq", 6: "six", 7: "sept", 8: "huit", 9: "neuf", 10: "dix"},
    "es": {2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis", 7: "siete", 8: "ocho", 9: "nueve", 10: "diez"},
    "de": {2: "zwei", 3: "drei", 4: "vier", 5: "f\u00fcnf", 6: "sechs", 7: "sieben", 8: "acht", 9: "neun", 10: "zehn"},
    "pt": {2: "dois|duas", 3: "tr\u00eas", 4: "quatro", 5: "cinco", 6: "seis", 7: "sete", 8: "oito", 9: "nove",
           10: "dez"},
}


_PAROLE_NUMERO = re.compile(r"\b(?:one|two|twice|double|pair|three|thrice|triple|four|five|six|seven|eight|nine|"
                            r"ten)\b", re.I)
_VALORE_PAROLA = {"one": 1, "two": 2, "twice": 2, "double": 2, "pair": 2, "three": 3, "thrice": 3, "triple": 3,
                  "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# accented letters of each target language: an accented Latin letter that does not belong to this language (and is
# not in the original) comes from a copied official translation ('crânio' from Portuguese)
_LETTERE_DELLA_LINGUA = {"it": set("àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ")}


def _numeri_in_lettere(o, t, destinazione):
    """True if the difference in numbers between original and translation is explained by numbers written in words:
    - a digit missing from the translation is there as a word ('1 or more' -> 'uno o piu''), counting only the words
      beyond those that translate words of the original ('twice' -> 'due volte' does not count for a '2');
    - an extra digit in the translation translates a number in words of the original ('restore one' -> 'ripristina
      1')."""
    in_lettere = collections.Counter(_VALORE_PAROLA[w.lower()] for w in _PAROLE_NUMERO.findall(o))
    articoli = len(re.findall(r"\b(?:a|an)\b", o, re.I))  # 'a'/'an' are translated 'un'/'una'
    no, nt = collections.Counter(_numeri(o)), collections.Counter(_numeri(t))
    mancanti, in_piu = no - nt, nt - no
    for numero, quanti in in_piu.items():
        if not numero.isdigit() or in_lettere.get(int(numero), 0) < quanti:
            return False
    if not mancanti:
        return bool(in_piu)
    parole = _NUMERI_IN_LETTERE.get(destinazione.split("-")[0])
    if not parole:
        return False
    for numero, quanti in mancanti.items():
        if not numero.isdigit():  # '2.5': no words for decimals
            return False
        parola = parole.get(int(numero))
        gia = in_lettere.get(int(numero), 0) + (articoli if numero == "1" else 0)
        if not parola or len(re.findall(r"(?<!\w)(?:%s)(?!\w)" % parola, t, re.I)) < quanti + gia:
            return False
    return True


_TOKEN_ROVINATO = re.compile(r"\[\[?\s*(\d{1,2})\s*[\]\u3015\u3011]{0,2}")
_VIRGOLETTE = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e\u300c\u300e"
_MIGLIAIA = re.compile(r"(?<=\d)[.,\u00a0\u202f ](?=\d{3}(?!\d))")


# "50k", "1.5M", "2B": thousands, millions, billions (M and B only uppercase: "5m" is meters or minutes)
_SUFFISSO_NUMERO = re.compile(r"(\d+(?:\.\d+)?)\s?([kK]|M|B)(?![A-Za-z])")
_PER_SUFFISSO = {"k": 1e3, "K": 1e3, "M": 1e6, "B": 1e9}


def _con_suffisso(m):
    v = float(m.group(1)) * _PER_SUFFISSO[m.group(2)]
    return str(int(v)) if v == int(v) else repr(v)


def _numeri(testo):
    """The numbers of a text as values comparable across languages: digits of every script ("\uff11\uff10" = 10),
    without thousands separators ("1.000", "1,000", "1 000" = 1000), decimal comma as a point, "50k" = 50000."""
    t = "".join(str(unicodedata.decimal(c)) if not c.isascii() and unicodedata.decimal(c, None) is not None else c
                for c in testo)
    t = re.sub(r"(?<=\d),(?=\d)", ".", _MIGLIAIA.sub("", t))
    t = _SUFFISSO_NUMERO.sub(_con_suffisso, t)
    return sorted(re.findall(r"\d+(?:\.\d+)?", t))


def differenza_numeri(originale, tradotto):
    """(mancano, in_piu): the numbers of the original that are not in the translation and the extra ones (as values:
    "50k" and "50.000" are the same number), written as in the texts."""
    def valori(testo):
        uscita = collections.Counter()
        for m in re.finditer(r"\d+(?:[.,\u00a0\u202f ]\d+)*(?:\s?(?:[kK]|M|B)(?![A-Za-z]))?", testo):
            for v in _numeri(m.group()):
                uscita[(v, m.group())] += 1
        return uscita
    o, t = valori(originale), valori(tradotto)
    vo = collections.Counter(v for v, _ in o.elements())
    vt = collections.Counter(v for v, _ in t.elements())
    mancano = [s for (v, s) in o if v in vo - vt]
    in_piu = [s for (v, s) in t if v in vt - vo]
    return mancano, in_piu


def _peso(testo):
    """Visual length: a Chinese, Japanese or Korean character is worth about two and a half letters."""
    from .text import _CJK
    return len(testo) + 1.5 * len(_CJK.findall(testo))
_RIPETIZIONE = re.compile(r"(\b\S+\b)(?:\W+\1\b){3,}", re.I)


def problemi_traduzione(originale, tradotto, destinazione="it"):
    """List of the reasons to reject a translation of the local model (empty = good)."""
    from .text import _TOKEN
    problemi = []
    if not tradotto.strip():
        return ["vuota"]
    if "\n" in tradotto and "\n" not in originale:
        problemi.append("a capo aggiunto")
    elif tradotto.count("\n") != originale.count("\n"):  # more or fewer lines: the label overflows its space
        problemi.append("a capo diversi")
    if _PREAMBOLO.match(tradotto) and not _PREAMBOLO.match(originale):
        problemi.append("testo di contorno")
    if _RIFIUTO.match(tradotto) and not _RIFIUTO.match(originale):
        problemi.append("rifiuto del modello")
    if destinazione.split("-")[0] not in ("ru", "uk", "ja", "zh", "ko", "bg") and \
            _ALTRA_SCRITTURA.search(tradotto) and not _ALTRA_SCRITTURA.search(originale):
        problemi.append("scrittura sbagliata")
    lo, lt = _peso(originale), _peso(tradotto)
    if lt > 2.5 * lo + 15 or (lo > 20 and lt < 0.3 * lo) or (lo > 80 and lt < 0.5 * lo):
        problemi.append("lunghezza anomala")
    if _RIPETIZIONE.search(tradotto) and not _RIPETIZIONE.search(originale):
        problemi.append("ripetizioni")
    if tradotto.strip() == originale.strip() and len(_PAROLE_INGLESI.findall(originale)) >= 2:
        problemi.append("non tradotta")
    o, t = _TOKEN.sub(" ", originale), _TOKEN.sub(" ", tradotto)  # the digits of the [[0]] codes are not numbers
    if _numeri(o) != _numeri(t) and sorted(re.findall(r"\d+", o)) != sorted(re.findall(r"\d+", t)) \
            and not _numeri_in_lettere(o, t, destinazione):
        problemi.append("numeri diversi")
    if collections.Counter(_TOKEN.findall(originale)) != collections.Counter(_TOKEN.findall(tradotto)):
        problemi.append("segnaposto")
    proprie = _LETTERE_DELLA_LINGUA.get(destinazione.split("-")[0])
    if proprie and any(c not in proprie and c not in originale and c.isalpha() and not c.isascii()
                       and unicodedata.normalize("NFKD", c)[0].isascii() for c in tradotto):
        problemi.append("lettere di un'altra lingua")
    return problemi


class _RigaLenta(Exception):
    """A single line went over the maximum time, but the server is alive: that line stays untranslated."""


def _uguale(originale, tradotto):
    def norma(x):
        return re.sub(r"[\s.!?\u2026]+$", "", x.strip()).casefold()
    return norma(originale) == norma(tradotto)


def seme_per(testo, numero):
    """The seed of attempt 'numero' for a text: always the same for the same pair (even from one run to the next:
    no Python hash(), which changes at every run), different from one attempt to the next and never equal to those
    of the first round (Locale.TENTATIVI: 42, 7, 11)."""
    return zlib.crc32(("%d\x00%s" % (numero, testo)).encode("utf-8")) % 2_000_000_000 + 100


NON_INSTALLATO = ("Il traduttore locale non e' installato: scegli 'Traduttore locale (sul tuo PC)' e premi "
                  "'Installa...' accanto.")
COMANDO_INSTALLA = "python -I smith.py local --install 12b (o 4b)"


class Locale(Backend):
    """Translator on the user's PC: Ollama + TranslateGemma. No quota and no blocks: it goes at the pace of the
    graphics card. One request per line (the TranslateGemma format expects one text at a time), with checks on
    every answer and a second attempt with different parameters; the lines that do not pass the checks stay
    untranslated (Progetto retries them right away in another round: see 'giri' in traduci_righe). The server
    starts at the first line and is closed with chiudi()."""

    nome = "locale"
    limitato = False
    usa_contesto = True  # accepts for each line the key and the game's official translations (see Progetto)
    semi_variabili = True  # accepts for each line the round (see traduci_righe): an already rejected text changes seeds
    max_testi = 40
    max_caratteri = 8000
    NUM_CTX = 2048
    TENTATIVI = ((0.0, 42), (0.3, 7), (0.6, 11))  # first round; the third only for texts rejected twice
    # from the second round (text already rejected, in this run or an earlier one): new seeds at every round (seme_per)
    # and higher temperatures. At temperature 0 the seed does not matter: the same question would give the same answer
    TEMPERATURE_GIRI = (0.3, 0.6, 0.9)

    def __init__(self, modello="4b", log=print, server=None, parallele=None):
        from . import ollama
        if modello not in ollama.MODELLI:
            modello = "4b"
        self.chiave = modello
        self.modello = ollama.MODELLI[modello]["nome"]
        self.log = log
        self._server = server
        self._proprio = server is None
        self._pronto = False
        self.token, self.secondi, self.righe_fatte = 0, 0.0, 0
        self.tempo_righe = 0.0  # real seconds spent translating (without startup): for the estimate
        self._avvisato_lento = False
        # lines translated together: with PatchSmith's server as many as the model on the graphics card can handle;
        # with a server given from outside (tests) one at a time, unless told otherwise
        self.parallele = max(1, parallele or (ollama.MODELLI[modello].get("parallele", 1) if server is None else 1))
        self._lucchetto = threading.Lock()
        # {testo: (traduzione, problemi)}: the best proposal for the texts rejected by the checks. Only if it is a
        # dictionary (the live translator, servizio_xunity, prepares it to answer XUnity anyway)
        self.scarti = None

    def pausa(self):
        return None

    def richieste(self, testi):
        return [len(t) for t in testi]

    def stima(self, caratteri_per_richiesta):
        """Expected seconds: startup (detection of the graphics card and loading of the model) plus the texts, at the
        pace measured on this PC after the first lines (before that: the one from the tests on an RTX 3060 Ti 8 GB)."""
        from . import ollama
        per_testo = ollama.MODELLI[self.chiave]["secondi_per_testo"]
        if self.righe_fatte >= 10:
            per_testo = self.tempo_righe / self.righe_fatte
        avvio = 0 if self._pronto else ollama.MODELLI[self.chiave]["secondi_avvio"]  # graphics card + model
        return avvio + sum(per_testo * (0.5 + c / 80) for c in caratteri_per_richiesta)

    def _assicura_server(self):
        from . import ollama
        if self._pronto:
            return
        if self._server is None:
            if not ollama.installato():
                raise ErroreBackend(NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=COMANDO_INSTALLA)
            if not ollama.modello_presente(self.chiave):
                raise ErroreBackend("Il modello %s non e' scaricato: premi 'Installa...' accanto al traduttore locale."
                                    % ollama.MODELLI[self.chiave]["etichetta"], "TRADUTTORE_ASSENTE",
                                    comando="python -I smith.py local --install %s" % self.chiave)
            self._server = ollama.Server(self.log, parallele=self.parallele)
        try:
            self._server.avvia()
            self.log("    Traduttore locale: carico il modello nella scheda video (la prima volta puo' volerci un "
                     "minuto)...")
            self.log(DETTAGLIO + "    (modello %s)" % self.modello)
            self._server.chiama("/api/generate", {"model": self.modello, "keep_alive": "30m"}, timeout=600)
            caricati = self._server.chiama("/api/ps").get("models", [])
        except ollama.ErroreLocale as e:
            raise ErroreBackend(str(e)) from None
        except urllib.error.HTTPError as e:
            raise ErroreBackend(self._spiega(e)) from None
        except (OSError, ValueError) as e:
            raise ErroreBackend("Il traduttore locale non risponde (%s)." % type(e).__name__) from None
        for m in caricati:
            if m.get("name") == self.modello and m.get("size"):
                quota = m.get("size_vram", 0) / m["size"]
                self.log((DETTAGLIO if quota > 0.95 else "") +  # fully ready: detail; otherwise the warning is shown
                         "    Traduttore locale pronto: %.0f%% del modello nella scheda video%s" % (
                    100 * quota, "" if quota > 0.95 else (" (il resto in memoria: piu' lento)" if quota > 0 else
                                                         " - ATTENZIONE: usa solo il processore, molto piu' lento: "
                                                         "controlla il driver NVIDIA")))
        self._pronto = True

    def _spiega(self, e):
        corpo = _leggi_errore(e).decode("utf-8", "replace").lower()
        if e.code == 404 or "not found" in corpo:
            return "Il modello %s non e' scaricato: premi 'Installa' accanto al traduttore locale." % self.modello
        if "memory" in corpo or "out of memory" in corpo:
            return ("Memoria insufficiente per il traduttore locale: chiudi il gioco e gli altri programmi pesanti, "
                    "oppure scegli il modello Veloce (4B).")
        if "cuda" in corpo:
            return "Errore della scheda video (CUDA): aggiorna il driver NVIDIA e riprova."
        return "Il traduttore locale ha dato un errore (HTTP %d)." % e.code

    @staticmethod
    def _nome_inglese(codice):
        return NOMI_INGLESI.get(codice, NOMI_INGLESI.get(codice.split("-")[0], codice))

    def _testo_contesto(self, contesto, destinazione):
        """The prompt's context lines for one line (empty if there is no context: prompt as before)."""
        if not contesto:
            return ""
        righe = ""
        chiave = (contesto.get("chiave") or "").replace("\n", " ").strip()[:80]
        if chiave:
            righe += CONTESTO_CHIAVE % chiave
        riferimenti = [(c, t.replace("\n", " ").strip()) for c, t in (contesto.get("riferimenti") or {}).items()
                       if t and t.strip()]
        if riferimenti:
            righe += CONTESTO_RIFERIMENTI % (self._nome_inglese(destinazione), "; ".join(
                "%s: %s" % (self._nome_inglese(c), t) for c, t in riferimenti))
        non_usare = "".join(c for c in dict.fromkeys(contesto.get("non_usare") or "") if not c.isspace())
        if non_usare:
            righe += CORREZIONE_CARATTERI % " ".join(non_usare)
        larghezza = contesto.get("larghezza")
        if isinstance(larghezza, int) and larghezza > 0:  # (only when a label is requested shorter)
            righe += CONTESTO_LARGHEZZA % (self._nome_inglese(destinazione), larghezza)
        return righe

    def _tentativi(self, testo, giro):
        """[(temperature, seed)] of the attempts for a text: at the first round (0) the fixed ones; from the others,
        seeds derived from the text and the attempt number (seme_per), so every round asks new questions even from
        one run to the next (Progetto remembers the rounds done in memory/<gioco>.problemi.json)."""
        if not giro:
            return list(self.TENTATIVI)
        n = len(self.TEMPERATURE_GIRI)
        return [(t, seme_per(testo, giro * n + i)) for i, t in enumerate(self.TEMPERATURE_GIRI)]

    def altro_modello(self):
        """A local translator with the other model (Fast <-> Quality), if it is downloaded, for the texts this one
        rejected; None if there is none. This translator's model leaves the graphics card (and, if the server is
        PatchSmith's, the server closes: the new translator starts one with the parameters of its model)."""
        from . import ollama
        altro = {"4b": "12b", "12b": "4b"}.get(self.chiave)
        try:
            if not altro or not ollama.modello_presente(altro):
                return None
        except (OSError, KeyError):
            return None
        if self._proprio:
            self.chiudi()
            return Locale(altro, log=self.log)
        if self._server is not None and self._pronto:  # server given from outside (the tests): it stays on
            try:
                self._server.chiama("/api/generate", {"model": self.modello, "keep_alive": 0}, timeout=30)
            except (OSError, ValueError):
                pass
        self._pronto = False
        return Locale(altro, log=self.log, server=self._server, parallele=self.parallele)

    def _chat(self, testo, origine, destinazione, temperatura, seme, num_predict, contesto=""):
        sc, tc = origine, destinazione
        prompt = PROMPT_TRANSLATEGEMMA.format(sl=self._nome_inglese(sc), sc=sc, tl=self._nome_inglese(tc), tc=tc,
                                              contesto=contesto, testo=testo)
        dati = {"model": self.modello, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "keep_alive": "30m", "options": {"temperature": temperatura, "seed": seme, "num_ctx": self.NUM_CTX,
                                                 "num_predict": num_predict, "top_k": 64, "top_p": 0.95}}
        try:  # maximum time in proportion to how much it can write (even at 2-3 words a second)
            risposta = self._server.chiama("/api/chat", dati, timeout=120 + num_predict // 2)
        except urllib.error.HTTPError as e:
            raise ErroreBackend(self._spiega(e)) from None
        except (OSError, ValueError) as e:
            scaduto = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
            if scaduto and self._vivo():
                raise _RigaLenta() from None
            raise ErroreBackend("Il traduttore locale non risponde (%s): riprova." % type(e).__name__) from None
        with self._lucchetto:
            self.token += risposta.get("eval_count", 0)
            self.secondi += risposta.get("eval_duration", 0) / 1e9
        usati = risposta.get("prompt_eval_count", 0) + risposta.get("eval_count", 0)
        return (risposta.get("message") or {}).get("content", ""), risposta.get("done_reason"), usati

    def _vivo(self):
        try:
            self._server.chiama("/api/version", timeout=10)
            return True
        except (OSError, ValueError):
            return False

    def _pulisci(self, tradotto, originale, destinazione="it"):
        """Removes what the model adds on its own: quotes, notes in parentheses, several alternative translations
        for a name, final period or ellipsis where the original has none; lowercase initial like the original."""
        t = _CONTROLLO.sub("", tradotto).strip()
        o = originale.strip()
        from .text import _CJK, _TOKEN
        validi = {int(n) for n in _TOKEN.findall(o)}
        if validi:
            # placeholders damaged by the model ('[[4]Spine', '[0]Colpo', '[[2]〕'): they are restored, if the number
            # is one of those of the original and the original does not have that spelling on its own
            resto = _TOKEN.sub(" ", o)  # the original without the correct placeholders

            def ripara(m):
                n = int(m.group(1))
                return "[[%d]]" % n if n in validi and m.group(0).strip() not in resto else m.group(0)
            t = _TOKEN_ROVINATO.sub(ripara, t)
        if "\n" not in o:  # line breaks added by the model (e.g. before each button code): spaces
            t = re.sub(r"[ \t]*\n+[ \t]*", " ", t)
        for apre, chiude in ('""', "''", "\u201c\u201d", "\u00ab\u00bb", "\u300c\u300d", "\u300e\u300f"):
            if len(t) > 1 and t[0] == apre and t[-1] == chiude and o[:1] not in _VIRGOLETTE:
                t = t[1:-1].strip()
        if "(" not in o and "\uff08" not in o:  # "corna (di cervo, alce, ecc.)", "punti esperienza (EP)"
            t = re.sub(r"\s*[(\uff08][^()\uff08\uff09\n]{1,40}[)\uff09]", "", t).strip() or t
        if re.fullmatch(r"[^\W_]+(?: [^\W_]+){0,2}", o) and not _CJK.search(o) and "\n" not in t and \
                (";" in t or " " not in o):
            # a name translated with several alternatives: "edificio; costruzione", "incantare, affascinare"
            parti = [x for x in re.split(r"\s*;\s*" if ";" in t else r"\s*,\s*", t.rstrip(".")) if x]
            if len(parti) > 1 and all(len(x.split()) <= 3 for x in parti):
                t = parti[0]
        if not _CHIUDE_FRASE.search(o):
            if re.search(r"(?:\.\.\.|\u2026)$", t):  # ellipsis added to a split sentence
                t = re.sub(r"\s*(?:\.\.\.|\u2026)$", "", t) or t
            elif re.search(r"(?<!\.)[.;,]$", t) and not re.search(r"(?:\b\w\.\w\.|\b(?:ecc|etc|ca)\.)$", t, re.I):
                # "scarpe dorate." for "golden boots" (abbreviations like "U.S.A." stay)
                t = t[:-1].rstrip()
        lingua = destinazione.split("-")[0]
        if lingua != "de" and o[:1].isalpha() and o[:1].islower() and t[:1].isupper() \
                and (t[1:2].islower() or (t[1:2] == " " and lingua != "en")):  # games all in lowercase (Forager)
            t = t[:1].lower() + t[1:]
        if originale[:1].isspace() or originale[-1:].isspace():
            t = originale[:len(originale) - len(originale.lstrip())] + t + originale[len(originale.rstrip()):]
        return t

    def _traduci_una(self, testo, origine, destinazione, contesto=None, giro=0):
        if len(testo) > 1500:
            pezzi = [self._traduci_una(p, origine, destinazione, giro=giro) for p in _dividi_lunga(testo, 1500)]
            return "" if any(not p for p in pezzi) else " ".join(pezzi)
        from .text import _TOKEN
        ultimo, eco, eco_viste = "", None, 0
        stessa_lingua = origine.split("-")[0] == destinazione.split("-")[0]
        attuale = dict(contesto or {})
        chiave = re.sub(r"[\W_]+", "", attuale.get("chiave") or "").casefold()
        riferimenti = [r for r in (attuale.get("riferimenti") or {}).values() if r and r.strip()]
        tentativi, correzione = self._tentativi(testo, giro), ""
        proposta = None  # (translation, problems) rejected with the fewest problems: for self.scarti
        for n, (temperatura, seme) in enumerate(tentativi):
            base = self._testo_contesto(attuale, destinazione)
            num_predict = min(1024, 64 + 2 * (len(testo) // 3 + 1))
            for _ in range(2):
                try:
                    grezzo, fine, _usati = self._chat(testo, origine, destinazione, temperatura, seme, num_predict,
                                                      base + correzione)
                except _RigaLenta:
                    self.log("    ! Un testo richiede troppo tempo al traduttore locale: resta in originale (\"%s\")"
                             % testo[:50])
                    return ""
                piu_spazio = min(2 * num_predict, self.NUM_CTX - 256)  # truncated: more room, within the context
                if fine != "length" or piu_spazio <= num_predict:
                    break
                num_predict = piu_spazio
            tradotto = self._pulisci(pulisci(grezzo), testo, destinazione)
            problemi = problemi_traduzione(testo, tradotto, destinazione)
            if len(chiave) >= 6 and chiave in re.sub(r"[\W_]+", "", tradotto).casefold() \
                    and chiave not in re.sub(r"[\W_]+", "", testo).casefold():
                problemi.append("chiave copiata")  # the model wrote the hint instead of the translation
                attuale["chiave"] = None  # the next attempt, without the hint
            if riferimenti and not _uguale(testo, tradotto) and any(_uguale(r, tradotto) for r in riferimenti):
                problemi.append("riferimento copiato")  # an official translation of another language, copied
                attuale["riferimenti"] = {}  # next attempt, without the references (if the same again, it is right)
                riferimenti = []
            correzione = ""
            if "numeri diversi" in problemi and re.search(r"\d", testo):
                correzione += CORREZIONE_NUMERI
            if "segnaposto" in problemi:
                correzione += CORREZIONE_SEGNAPOSTO
            if correzione and n == 0:
                tentativi[1] = tentativi[0]  # the same question, with the correction (then the third attempt remains)
            if not problemi:
                if stessa_lingua or not _uguale(testo, tradotto):
                    return tradotto
                eco, eco_viste = tradotto, eco_viste + 1  # equal to the original: we believe it only if it repeats
                if eco_viste >= 2:
                    return tradotto  # a text that stayed the same twice is a name or a word that does not change
            elif problemi == ["segnaposto"] and _TOKEN.search(testo):
                ultimo = tradotto
            if tradotto.strip() and (proposta is None or len(problemi) < len(proposta[1])):
                proposta = (tradotto, problemi)
        if self.scarti is not None and proposta is not None:
            self.scarti[testo] = proposta
        # with only the placeholders wrong the translation goes back to PatchSmith, which re-translates the pieces
        # between the codes
        return ultimo

    def traduci_righe(self, righe, origine, destinazione, contesti=None, giri=None):
        """Translates the lines, 'parallele' at a time (the graphics card processes several together); the order stays
        that of the lines. With 'Stop' or an error, those already translated are in e.parziali. giri: for each line
        how many times it has already been tried (0 or missing: first round, see _tentativi)."""
        self._assicura_server()
        contesti = list(contesti or [])
        giri = list(giri or [])
        uscita = [None] * len(righe)
        inizio = time.monotonic()

        def una(i):
            interruzione.controlla()
            return self._traduci_una(righe[i], origine, destinazione, contesti[i] if i < len(contesti) else None,
                                     giri[i] if i < len(giri) else 0)
        try:
            if self.parallele <= 1 or len(righe) <= 1:
                for i in range(len(righe)):
                    uscita[i] = una(i)
                    self._riga_fatta()
            else:
                pool = concurrent.futures.ThreadPoolExecutor(self.parallele)
                futuri = {pool.submit(una, i): i for i in range(len(righe))}
                try:
                    for f in concurrent.futures.as_completed(futuri):
                        uscita[futuri[f]] = f.result()
                        self._riga_fatta()
                except BaseException:
                    pool.shutdown(wait=True, cancel_futures=True)  # the requests already sent finish
                    for f, i in futuri.items():
                        if uscita[i] is None and f.done() and not f.cancelled() and f.exception() is None:
                            uscita[i] = f.result()
                    raise
                pool.shutdown()
        except BaseException as e:  # "Stop" or error halfway: the lines already translated are not lost
            e.parziali = [u if u is not None else "" for u in uscita]
            raise
        finally:
            self.tempo_righe += time.monotonic() - inizio
        return uscita

    def _riga_fatta(self):
        self.righe_fatte += 1
        if self.righe_fatte == 20 and self.secondi > 0 and not self._avvisato_lento:
            al_secondo = self.token / self.secondi
            self._avvisato_lento = True
            self.log(DETTAGLIO + "    Traduttore locale: %.0f parole-unita' (token) al secondo%s" % (
                al_secondo, ", %d righe insieme" % self.parallele if self.parallele > 1 else ""))
            if al_secondo < 6:
                self.log("    ! E' lento: se il gioco e' aperto chiudilo; altrimenti prova il modello Veloce (4B).")

    def chiudi(self):
        """Frees the graphics card and closes the server (if this translator started it)."""
        if self._server is not None and self._pronto:
            try:
                self._server.chiama("/api/generate", {"model": self.modello, "keep_alive": 0}, timeout=30)
            except (OSError, ValueError):
                pass
        if self._server is not None and self._proprio:
            self._server.chiudi()
            self._server = None
        self._pronto = False


BACKEND = {"locale": Locale, "google": GoogleGratis, "deepl": DeepL, "mymemory": MyMemory, "finto": Finto}
DESCRIZIONI = {
    "locale": "Traduttore locale (sul tuo PC: nessun limite, nessun blocco)",
    "google": "Google gratuito (non ufficiale, spesso bloccato: Google puo' rifiutarlo)",
    "deepl": "DeepL (gratis con la tua chiave, 500.000 caratteri al mese)",
    "mymemory": "MyMemory (senza account, 50.000 caratteri al giorno con la tua email)",
}


def credenziali():
    """DeepL key and email for MyMemory: from the environment variables or from settings.json."""
    from . import project as progetto
    dati = {}
    try:
        with open(progetto.cartella_dati("settings.json"), encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        pass
    return {"chiave_deepl": dati.get("chiave_deepl") or os.environ.get("PATCHSMITH_DEEPL") or "",
            "email_mymemory": dati.get("email_mymemory") or os.environ.get("PATCHSMITH_EMAIL") or "",
            "deepl_da_variabile": not dati.get("chiave_deepl") and bool(os.environ.get("PATCHSMITH_DEEPL")),
            "modello_locale": dati.get("modello_locale") or os.environ.get("PATCHSMITH_MODELLO") or ""}


def predefinito():
    """The proposed translator: always the local one (no limits, no blocks), even if it is not installed yet.
    On a new PC the first step is to install it ("to install" and the "Install..." button next to it): free Google
    often rejects PatchSmith's requests from the very first one."""
    return "locale"


def prova_locale(modello=None, log=print, testo="Start game", a="it", server=None):
    """Test of the local translator (after installation): starts the server, loads the model and translates a
    sentence, measuring the times. {'ok', 'traduzione', 'avvio' (seconds to start the server and load the model),
    'secondi' (for the sentence), 'scheda' (how much of the model is on the graphics card, from 0 to 1; None if
    unknown), 'messaggio' (for the player)}. Does not raise translator errors: if the test fails, the message says so.
    server: for the tests."""
    from . import ollama
    chiave = modello if modello in ollama.MODELLI and (server is not None or ollama.modello_presente(modello)) \
        else ollama.modello_predefinito()
    a = "it" if (a or "en").split("-")[0].lower() == "en" else a  # (in English the sentence would stay the same)
    traduttore = Locale(chiave, log=log, server=server)
    uscita = {"ok": False, "traduzione": None, "avvio": None, "secondi": None, "scheda": None, "modello": chiave}
    inizio = time.monotonic()
    try:
        traduttore._assicura_server()
        uscita["avvio"] = time.monotonic() - inizio
        try:
            caricati = traduttore._server.chiama("/api/ps").get("models", [])
        except (OSError, ValueError, AttributeError):
            caricati = []
        for m in caricati:
            if m.get("name") == traduttore.modello and m.get("size"):
                uscita["scheda"] = max(0.0, min(1.0, m.get("size_vram", 0) / m["size"]))
        prima = time.monotonic()
        risposta = traduttore.traduci_righe([testo], "en", a)[0]
        uscita["secondi"] = time.monotonic() - prima
    except Exception as e:  # noqa: BLE001 - the installation succeeded: here we only say how the test went
        uscita["messaggio"] = "la prova di traduzione non e' riuscita: %s" % (
            e if isinstance(e, ErroreBackend) else "%s: %s" % (type(e).__name__, e))
        return uscita
    finally:
        traduttore.chiudi()
    uscita["traduzione"] = risposta
    if not risposta or _uguale(risposta, testo):
        uscita["messaggio"] = ("la prova di traduzione non e' riuscita: \"%s\" e' rimasto in originale. Riprova; se "
                               "succede ancora, crea un rapporto." % testo)
        return uscita
    uscita["ok"] = True
    scheda = uscita["scheda"]
    dove = ("" if scheda is None else ", sulla scheda video" if scheda > 0.95 else
            ", in parte sulla scheda video (un po' piu' lento)" if scheda > 0 else
            ", ma solo con il processore, senza scheda video: sara' lento (aggiorna il driver della scheda video, "
            "oppure scegli il modello Veloce)")
    uscita["messaggio"] = "pronto%s: \"%s\" -> \"%s\" in %s (avvio e caricamento del modello: %s)." % (
        dove, testo, risposta, _secondi(uscita["secondi"]), _secondi(uscita["avvio"]))
    return uscita


def _secondi(valore):
    return ("%.1f s" % valore).replace(".", ",") if valore < 10 else "%d s" % round(valore)


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def verifica_traduttore(nome, credenziale, apri=None):
    """(ok, messaggio) for the "Check" button: does the DeepL key or the MyMemory email really work?"""
    try:
        if nome == "deepl":
            usati, limite = DeepL(credenziale, log=lambda _m: None, apri=apri).verifica()
            if limite:
                return True, ("La chiave DeepL funziona. Questo mese hai usato %s caratteri su %s (ne restano %s)."
                              % tuple(format(n, ",d").replace(",", ".") for n in (usati, limite, max(0, limite - usati))))
            return True, "La chiave DeepL funziona."
        if nome == "mymemory":
            email = (credenziale or "").strip()
            if email and not EMAIL.match(email):
                return False, "\"%s\" non sembra un indirizzo email: controlla che sia scritto bene (es. nome@esempio.it)." \
                    % email
            MyMemory(email or None, log=lambda _m: None, apri=apri).verifica()
            if email:
                return True, ("MyMemory risponde e accetta l'email: puoi tradurre fino a 50.000 caratteri al giorno. "
                              "MyMemory non usa account e non manda conferme: l'email serve solo ad alzare il limite "
                              "(senza: 5.000 caratteri al giorno).")
            return True, ("MyMemory risponde: senza email puoi tradurre 5.000 caratteri al giorno. Con la tua email "
                          "arrivi a 50.000 (non serve un account).")
    except ErroreBackend as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 - no network or odd answer: explain it, no exceptions in the window
        return False, "Non riesco a contattare il servizio (%s). Controlla la connessione e riprova." % type(e).__name__
    return False, "Questo traduttore non ha niente da verificare."


def crea_backend(nome, log=print):
    if nome not in BACKEND:
        raise ValueError("Backend sconosciuto: %s (disponibili: %s)" % (nome, ", ".join(BACKEND)))
    if nome == "google":
        return GoogleGratis(log=log)
    if nome == "deepl":
        c = credenziali()
        d = DeepL(c["chiave_deepl"], log=log)
        d.da_variabile = c["deepl_da_variabile"]
        return d
    if nome == "mymemory":
        return MyMemory(credenziali()["email_mymemory"], log=log)
    if nome == "locale":
        from . import ollama
        if ollama.stato() != "pronto":  # right away: before installing or changing anything in the game
            raise ErroreBackend(NON_INSTALLATO, "TRADUTTORE_ASSENTE", comando=COMANDO_INSTALLA)
        modello = credenziali()["modello_locale"]
        if modello not in ollama.MODELLI or not ollama.modello_presente(modello):
            if modello in ollama.MODELLI:
                log("  Il modello %s non e' scaricato: uso %s." % (ollama.MODELLI[modello]["etichetta"],
                                                                  ollama.MODELLI[ollama.modello_predefinito()]["etichetta"]))
            modello = ollama.modello_predefinito()
        return Locale(modello, log=log)
    return Finto()
