"""Translator of new lines for XUnity.AutoTranslator: a small server on the PC (127.0.0.1 only) that answers like
XUnity's "CustomTranslate" endpoint (GET /translate?from=en&to=it&text=... -> only the translated text).

All the lines the game shows during play that are not already translated (e.g. "Hold RMB {{A}}s to leap up to
{{B}} m": XUnity removes the numbers and puts them back afterwards) go through here, whatever translator was chosen:
the game's translation memory and the shared one apply, as do the checks on translations and the pauses of the online
translators (PatchSmith does not insist with a service that has blocked it and does not wait more than MAX_ATTESA for
its cautious margins). The translators are tried in a row (traduttori_previsti): the local translator if it is
installed (the Fast model, if present: the graphics card is needed by the game too), then the chosen online one
(DeepL with the key, MyMemory), then free Google if it is not paused.

XUnity stops using an endpoint after 5 errors in a row: that is why a line that no translator can translate well is
NOT an error. The answer (200) is the best translation if it has the {{A}} placeholders intact and is in the right
language ("rilassata"), otherwise the original ("rifiutata"); a line that an online translator could send only much
later ("rinviata") also gets the original. Error (500) only if no translator answers, 503 while the game is closing
(no translation with the translator that is shutting down).

Every requested line ends up in the journal memory/<gioco>.richieste.jsonl (see percorso_diario): a JSON line with ts,
testo, da, a, esito (ESITI_DIARIO), backend, motivo and risposta. Lines with an outcome other than "ok" are not in the
translation memory: they can be re-translated with the game closed and fixed in XUnity's cache.

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

from . import shared_file as file_condiviso, interruption as interruzione, language as lingua
from .backend import ErroreBackend, _uguale, problemi_traduzione
from .project import Progetto, _da_tradurre, cartella_dati, maiuscola_come
from .text import ricomponi_righe, ripristina_segnaposto

PERCORSO = "/translate"
PERCORSO_GRUPPO = "/traduci-gruppo"  # several lines together (live translation of web games)
MAX_GRUPPO = 60
MAX_CORPO = 1_000_000
CONTROLLO_GIOCO = 3.0  # seconds between one check of the game's process and the next
MAX_ATTESA = 60.0  # seconds: XUnity waits at most 150 s for an answer, the online translators sometimes longer
MAX_AVVIO = 140.0  # seconds of waiting for translators that are starting, then the line is postponed
MAX_CHIUSURA = 180.0  # seconds: on closing, a translation in progress (even with the online waits) finishes first
MAX_DIARIO = 2_000_000  # bytes: then the journal becomes <nome>.richieste.jsonl.1 and a new one is started
# outcomes in the journal: translated (and saved in the translation memory); fallback answer (not in the translation
# memory); original because rejected by all translators; original because the online translators ask to wait too
# long; no translator reachable (500); game closing (503)
ESITI_DIARIO = ("ok", "rilassata", "rifiutata", "rinviata", "errore", "interrotta")
NOMI = {"locale": "traduttore locale", "google": "Google", "deepl": "DeepL", "mymemory": "MyMemory",
        "finto": "traduttore di prova"}
_XUNITY = re.compile(r"\{\{[^{}]*\}\}")  # XUnity's placeholders ({{A}}): they must all come back
# problems (backend.problemi_traduzione) saying that the answer is not a translation in the target language
_NON_TRADOTTA = {"vuota", "scrittura sbagliata", "lettere di un'altra lingua", "non tradotta", "rifiuto del modello"}
# frequent words of the target language that in the lists of lingua._PAROLE are only among those of other languages:
# in an Italian answer "se", "tu", "su", "no" do not mean that it is Spanish (see lingua.lingua_evidente)
_ANCHE_DI_ARRIVO = lingua.ANCHE_DI


def porta_per(nome_profilo):
    """Fixed port for each game (47000-47999): two games do not swap translations."""
    return 47000 + zlib.crc32(nome_profilo.encode("utf-8")) % 1000


def indirizzo(porta):
    return "http://127.0.0.1:%d%s" % (porta, PERCORSO)


def acceso(nome_profilo, attesa=0.3):
    """True if this game's translator is listening (the game is open and has started it)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/patchsmith" % porta_per(nome_profilo), timeout=attesa) as r:
            return r.read().decode("utf-8", "replace") == "patchsmith " + nome_profilo
    except (OSError, ValueError):
        return False


def ferma(nome_profilo):
    """Stops this game's translator (it restarts with the game). True if it was running."""
    if not acceso(nome_profilo):
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ferma" % porta_per(nome_profilo), timeout=5) as r:
            r.read()
        return True
    except (OSError, ValueError):
        return False


def percorso_diario(nome_profilo):
    """The journal of the lines requested during play (one JSON line per line of text), among the user's data."""
    return cartella_dati("memory", "%s.richieste.jsonl" % nome_profilo)


def leggi_diario(nome_profilo):
    """The journal's lines (the old one's too, .1), oldest first: [{ts, testo, esito, risposta...}]. Unreadable lines
    (e.g. half written by a game closed abruptly) are skipped."""
    righe = []
    for percorso in (percorso_diario(nome_profilo) + ".1", percorso_diario(nome_profilo)):
        try:
            with open(percorso, encoding="utf-8", errors="replace") as f:
                for riga in f:
                    try:
                        dati = json.loads(riga)
                    except ValueError:
                        continue
                    if isinstance(dati, dict) and isinstance(dati.get("testo"), str):
                        righe.append(dati)
        except OSError:
            continue
    return righe


class Rinviata(ErroreBackend):
    """An online translator asks to wait more than MAX_ATTESA to stay within the cautious margins: XUnity does not
    wait that long, the line is postponed (answer with the original, in the journal as 'rinviata')."""


def _attesa_breve(secondi):
    """Instead of the limiter's wait (which can last minutes): a short one is waited, a long one is not."""
    if secondi > MAX_ATTESA:
        raise Rinviata("per restare nei margini prudenti la prossima richiesta potrebbe partire solo tra %d "
                       "secondi" % secondi)
    interruzione.attendi(secondi)


def _impostazioni():
    try:
        with open(cartella_dati("settings.json"), encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        return {}
    return dati if isinstance(dati, dict) else {}


def _online_scelti(profilo):
    """DeepL or MyMemory, if they are the translator chosen for the game or the one chosen in the interface."""
    scelti = []
    for nome in (profilo.get("backend"), _impostazioni().get("backend")):
        if nome in ("deepl", "mymemory") and nome not in scelti:
            scelti.append(nome)
    return scelti


def traduttori_previsti(profilo):
    """The translators the service would use now, in order: 'locale' if it is installed, the chosen online translator
    (DeepL only with the key) if it is not paused, 'google' if it is not paused. Empty list: none."""
    from . import ollama
    from .backend import credenziali
    from .limiter import Limitatore
    nomi = []
    try:
        if ollama.stato() == "pronto":
            nomi.append("locale")
    except OSError:
        pass
    for nome in _online_scelti(profilo):
        if nome == "deepl" and not credenziali()["chiave_deepl"]:
            continue
        if Limitatore(nome, log=None).pausa() is None:
            nomi.append(nome)
    if Limitatore("google", log=None).pausa() is None:
        nomi.append("google")
    return nomi


def crea_traduttore(nome, log=print):
    """A translator of the row, set up to translate during play: the Fast model (4B) if present, short waits for the
    online translators (see _attesa_breve) and a single retry after a network error."""
    from . import ollama
    from .backend import Locale, crea_backend
    if nome == "locale":
        if ollama.stato() != "pronto":
            raise ErroreBackend("Il traduttore locale non e' installato.")
        return Locale("4b" if ollama.modello_presente("4b") else ollama.modello_predefinito(), log=log)
    traduttore = crea_backend(nome, log)
    if hasattr(traduttore, "limitatore"):
        traduttore.limitatore.dormi = _attesa_breve
    if hasattr(traduttore, "ATTESE_RETE"):  # Google
        traduttore.ATTESE_RETE = (5,)
    if hasattr(traduttore, "ATTESE"):  # DeepL: even when it asks to wait (Retry-After) it retries soon, only
        traduttore.ATTESE = (5,)  # once, then it moves on to the next translator
        traduttore.ATTESA_MASSIMA = 5.0
    return traduttore


def altra_lingua(originale, tradotto, origine, destinazione):
    """True if 'tradotto' is not a translation in the target language: empty, equal to the original, refused by the
    model, with another script or letters of another language, or with the frequent words of another language."""
    if set(problemi_traduzione(originale, tradotto, destinazione)) & _NON_TRADOTTA:
        return True
    if _uguale(originale, tradotto) and re.search(r"[^\W\d_]", _XUNITY.sub(" ", originale)):
        return True  # left equal to the original (even with a single frequent word: "Hold {{A}}s to leap")
    return lingua.lingua_evidente(_XUNITY.sub(" ", tradotto), destinazione) is not None


class _SenzaChiusura:
    """The translator stays on between one line and the next: Progetto would close it at the end of each translation
    (chiudi) and would make time estimates (stima), which are not needed for one line at a time. The service checks
    the pause before trying it."""

    def __init__(self, backend):
        self._backend = backend

    def __getattr__(self, nome):
        if nome in ("chiudi", "stima", "richieste", "pausa"):
            raise AttributeError(nome)
        return getattr(self._backend, nome)


def processo_vivo(pid):
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


class _ServerHttp(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows it would let two servers take the same port

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Gestore(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """POST /traduci-gruppo {"from", "to", "testi": [...]} -> {"traduzioni": {testo: traduzione}, "riprova": [...]}:
        several lines together, for the live translation of web games (adapter_web_vivo)."""
        servizio = self.server.servizio
        if urllib.parse.urlsplit(self.path).path != PERCORSO_GRUPPO:
            return self._rispondi(404, "")
        try:
            lunghezza = int(self.headers.get("Content-Length") or 0)
            dati = json.loads(self.rfile.read(min(lunghezza, MAX_CORPO)).decode("utf-8"))
            testi = [t for t in dati.get("testi", []) if isinstance(t, str)][:MAX_GRUPPO]
            da, a = str(dati.get("from") or "en"), str(dati.get("to") or "it")
        except (ValueError, AttributeError, UnicodeDecodeError):
            return self._rispondi(400, "richiesta non valida")
        try:
            traduzioni, riprova = servizio.rispondi_gruppo(testi, da, a)
        except Exception as e:  # unexpected: the script retries later
            servizio.log("! gruppo di %d frasi: %s: %s" % (len(testi), type(e).__name__, e))
            traduzioni, riprova = {}, testi
        corpo = json.dumps({"traduzioni": {t: servizio.adatta(v, a) for t, v in traduzioni.items()},
                            "riprova": riprova}, ensure_ascii=False)
        self._rispondi(200, corpo, "application/json; charset=utf-8")

    def do_GET(self):
        servizio = self.server.servizio
        url = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(url.query, keep_blank_values=True)
        if url.path == "/patchsmith":
            return self._rispondi(200, "patchsmith " + servizio.nome)
        if url.path == "/ferma":  # "Stop the game translator" in PatchSmith's window
            servizio.log("Fermato dalla finestra di PatchSmith (frasi tradotte: %d)" % servizio.tradotte)
            servizio.chiusura.set()
            threading.Thread(target=servizio.httpd.shutdown, daemon=True).start()
            return self._rispondi(200, "ok")
        if url.path == "/adotta":  # the game was reopened: from now on the new process is followed
            try:
                servizio.pid = int(q["pid"][0])
            except (KeyError, ValueError):
                return self._rispondi(400, "pid?")
            servizio.log("Il gioco e' stato riaperto (processo %d): continuo" % servizio.pid)
            return self._rispondi(200, "ok")
        if url.path != PERCORSO:
            return self._rispondi(404, "")
        testo = q.get("text", [""])[0]
        if not testo.strip():
            return self._rispondi(200, testo)
        da, a = q.get("from", ["en"])[0], q.get("to", ["it"])[0]
        try:
            codice, risposta = servizio.rispondi(testo, da, a)
            if codice == 200:
                risposta = servizio.adatta(risposta, a)
        except Exception as e:  # unexpected: XUnity retries the line later
            servizio.log("! %r: %s: %s" % (testo[:80], type(e).__name__, e))
            servizio.annota(testo, da, a, "errore", motivo="%s: %s" % (type(e).__name__, e))
            codice, risposta = 500, "non tradotto"
        self._rispondi(codice, risposta)

    def _rispondi(self, codice, testo, tipo="text/plain; charset=utf-8"):
        dati = testo.encode("utf-8")
        self.send_response(codice)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(dati)))
        self.end_headers()
        self.wfile.write(dati)

    def log_message(self, *argomenti):  # no lines for each request
        pass


class Servizio:
    """porta=None: the game's port (porta_per). backend: for the tests, a ready translator or a list of
    (nome, traduttore) to try in a row; None = the row of traduttori_previsti. pid: the game's process to
    follow (when it closes the service stops)."""

    def __init__(self, percorso_profilo, porta=None, pid=None, log=print, backend=None, prova=False):
        self.progetto = Progetto(percorso_profilo, log, prova=prova)
        # one line at a time during play: no second rounds (the other model would load the game's graphics card)
        # and no problemi.json; the journal records the rejected lines and they are re-translated with the game closed
        self.progetto.recupero = False
        self.nome = self.progetto.profilo["nome"]
        self.porta = porta or porta_per(self.nome)
        self.pid = pid
        self.log = log
        self._backend = backend
        self.catena = []  # [(nome, traduttore)] ready, in the order they are tried
        self._lucchetto = threading.Lock()
        self._lucchetto_diario = threading.Lock()
        self._pronto = threading.Event()
        self.chiusura = threading.Event()  # the game is closing: no more translations (503)
        self._errore = None
        self.httpd = None
        self.tradotte = 0
        self.diario = percorso_diario(self.nome)

    # --- translators -------------------------------------------------------------------------------

    def _prepara(self):
        """Prepares the row of translators and loads the local model right away: the first line does not wait for the
        graphics card to start. A translator that does not start is skipped."""
        try:
            if self._backend is None:
                catena = []
                for nome in traduttori_previsti(self.progetto.profilo):
                    try:
                        catena.append((nome, crea_traduttore(nome, self.log)))
                    except (ErroreBackend, ValueError, OSError) as e:
                        self.log("! %s non disponibile: %s" % (NOMI.get(nome, nome), e))
            elif isinstance(self._backend, (list, tuple)):
                catena = list(self._backend)
            else:
                catena = [(getattr(self._backend, "nome", "prova"), self._backend)]
            pronti = []
            for nome, traduttore in catena:
                if hasattr(traduttore, "_assicura_server"):
                    try:
                        traduttore._assicura_server()
                    except ErroreBackend as e:
                        self.log("! %s non disponibile: %s" % (NOMI.get(nome, nome).capitalize(), e))
                        continue
                if hasattr(traduttore, "scarti"):
                    traduttore.scarti = {}  # the rejected proposals are needed for the fallback answer
                pronti.append((nome, traduttore))
            self.catena = pronti
            self.log("Traduttori per le frasi nuove: %s" % (", ".join(NOMI.get(n, n) for n, _ in pronti) or
                                                            "nessuno (le frasi nuove restano in originale)"))
        except Exception as e:
            self._errore = e
            self.log("! Traduttori non pronti: %s: %s" % (type(e).__name__, e))
        finally:
            self._pronto.set()

    def rispondi(self, testo, da, a):
        """(HTTP code, answer text) for a line requested by XUnity. Every line ends up in the journal."""
        if self.chiusura.is_set():
            return self._in_chiusura(testo, da, a)
        memoria = self.progetto.memoria
        gia = memoria.get(testo)
        if isinstance(gia, str) and gia:  # already translated: without waiting for the translators
            self.annota(testo, da, a, "ok", "memoria", risposta=gia)
            return 200, gia
        if self._parte_di_traduzione(testo):
            self.annota(testo, da, a, "ok", "gia' tradotta", risposta=testo)
            return 200, testo
        if not self._pronto.wait(MAX_AVVIO):
            self.annota(testo, da, a, "rinviata", motivo="i traduttori si stanno ancora avviando", risposta=testo)
            return 200, testo
        with self._lucchetto:  # one line at a time (XUnity sends one at a time, but someone else might send more)
            if self.chiusura.is_set():
                return self._in_chiusura(testo, da, a)
            gia = memoria.get(testo)
            if isinstance(gia, str) and gia:
                self.annota(testo, da, a, "ok", "memoria", risposta=gia)
                return 200, gia
            return self._traduci(testo, da, a)

    def _parte_di_traduzione(self, testo):
        """True if the text is (a piece of) a translation already made: the game already shows the Italian (e.g. the
        translated text tables in the bundles, which XUnity splits at the colors and requests as if they were
        English). Re-translating it from English would ruin it."""
        pezzo = testo.strip()
        memoria = self.progetto.memoria
        if len(pezzo) < 4 or pezzo in memoria:
            return False
        if getattr(self, "_tradotte_n", -1) != len(memoria):
            self._tradotte = "\n".join(v for v in memoria.values() if isinstance(v, str))
            self._tradotte_n = len(memoria)
        return pezzo in self._tradotte

    def rispondi_gruppo(self, testi, da, a):
        """({testo: traduzione}, [texts to request later]) for several lines together: those already in the
        translation memory right away, the others with a single group translation per translator (the local
        translator does more than one at a time on the graphics card); those the group does not translate go one at a
        time, like rispondi()."""
        traduzioni, riprova, mancanti = {}, [], []
        memoria = self.progetto.memoria
        for t in dict.fromkeys(testi):
            if not t.strip():
                continue
            if self.chiusura.is_set():
                riprova.append(t)
                continue
            gia = memoria.get(t)
            if isinstance(gia, str) and gia:
                self.annota(t, da, a, "ok", "memoria", risposta=gia)
                traduzioni[t] = gia
            elif self._parte_di_traduzione(t):
                self.annota(t, da, a, "ok", "gia' tradotta", risposta=t)
            else:
                mancanti.append(t)
        if not mancanti:
            return traduzioni, riprova
        if not self._pronto.wait(MAX_AVVIO):
            return traduzioni, riprova + mancanti
        with self._lucchetto:
            p = self.progetto
            for nome, traduttore in self.catena:
                rimasti = [t for t in mancanti if t not in traduzioni]
                if not rimasti or self.chiusura.is_set():
                    break
                if hasattr(traduttore, "pausa") and traduttore.pausa():
                    continue
                p.esito_traduzione, p.ultimo_errore, p.ultimo_gruppo = None, None, None
                p.traduci_testi(rimasti, _SenzaChiusura(traduttore), da, a)
                for t in rimasti:
                    v = memoria.get(t)
                    if isinstance(v, str) and v:
                        self.tradotte += 1
                        self.annota(t, da, a, "ok", nome if p.ultimo_gruppo is not None else "comune", risposta=v)
                        traduzioni[t] = v
                if isinstance(p.ultimo_errore, Rinviata) or p.esito_traduzione == "TRADUTTORE_BLOCCATO":
                    continue
        for t in [t for t in mancanti if t not in traduzioni]:  # one at a time: fallback, rejects and journal
            if self.chiusura.is_set():
                riprova.append(t)
                continue
            with self._lucchetto:
                codice, risposta = self._traduci(t, da, a)
            if codice == 200 and risposta != t:
                traduzioni[t] = risposta
            else:  # left untranslated or translators stopped: the script requests it again later (a few times)
                riprova.append(t)
        return traduzioni, riprova

    def _in_chiusura(self, testo, da, a):
        self.annota(testo, da, a, "interrotta", motivo="il gioco si sta chiudendo")
        return 503, "in chiusura"

    def adatta(self, risposta, a):
        """The answer for XUnity without the characters the game's fonts cannot draw (profilo
        ["caratteri_mancanti"], found by the after-play check): è -> e'. The translation memory and the journal keep
        the real translation."""
        from .characters import adatta_senza
        esclusi = self.progetto.profilo.get("caratteri_mancanti")
        return (adatta_senza(risposta, set(esclusi), a) or risposta) if esclusi else risposta

    def _traduci(self, testo, da, a):
        """Tries the translators in a row; if they all reject the line, the fallback answer (see _rilassata)."""
        memoria, p = self.progetto.memoria, self.progetto
        raggiunti, rinviati, errori = [], [], []
        for _, traduttore in self.catena:
            if getattr(traduttore, "scarti", None) is not None:
                traduttore.scarti.clear()
        for nome, traduttore in self.catena:
            pausa = traduttore.pausa() if hasattr(traduttore, "pausa") else None
            if pausa:  # paused because of a block (even one that arrived during play): no insisting
                errori.append((nome, pausa))
                continue
            p.esito_traduzione, p.ultimo_errore, p.ultimo_gruppo = None, None, None
            p.traduci_testi([testo], _SenzaChiusura(traduttore), da, a)
            tradotto = memoria.get(testo)
            if isinstance(tradotto, str) and tradotto:
                self.tradotte += 1
                self.annota(testo, da, a, "ok", nome if p.ultimo_gruppo is not None else "comune",
                            risposta=tradotto)
                return 200, tradotto
            if isinstance(p.ultimo_errore, Rinviata):
                rinviati.append((nome, str(p.ultimo_errore)))
            elif p.ultimo_errore is not None or p.esito_traduzione == "TRADUTTORE_BLOCCATO":
                errori.append((nome, str(p.ultimo_errore or p.motivo_traduzione)))
            else:  # it answered, but the translation did not pass the checks
                raggiunti.append((nome, traduttore, p.ultimo_gruppo))
        motivi = ["%s: %s" % (NOMI.get(n, n), m) for n, m in errori + rinviati]
        if raggiunti:
            nome, traduttore, gruppo = raggiunti[0]
            scartata = "scartata dai controlli (%s)" % ", ".join(NOMI.get(n, n) for n, _, _ in raggiunti)
            motivo = "; ".join([scartata] + motivi)
            rilassata = self._rilassata(traduttore, gruppo, testo, da, a)
            if rilassata:
                self.log("  ~ frase tradotta senza tutti i controlli (da ricontrollare): %r" % testo[:80])
                self.annota(testo, da, a, "rilassata", nome, motivo, risposta=rilassata)
                return 200, rilassata
            self.log("  ! frase lasciata in originale (nessuna traduzione accettabile): %r" % testo[:80])
            self.annota(testo, da, a, "rifiutata", nome, motivo, risposta=testo)
            return 200, testo
        if rinviati:
            self.annota(testo, da, a, "rinviata", rinviati[0][0], "; ".join(motivi), risposta=testo)
            return 200, testo
        motivo = "; ".join(motivi) or ("traduttori non pronti (%s)" % self._errore if self._errore else
                                       "nessun traduttore disponibile")
        self.log("! %r non tradotta: %s" % (testo[:80], motivo))
        self.annota(testo, da, a, "errore", motivo=motivo)
        return 500, "non tradotto"

    def _rilassata(self, traduttore, gruppo, testo, da, a):
        """The fallback answer for a line that all translators rejected: the pieces between the placeholders
        translated separately (only once, only where it has not been done already) and, for the lines still without a
        translation, the local translator's rejected proposal. None if an XUnity {{A}} placeholder is missing or if
        the answer is not in the target language: then the answer is the original."""
        if not gruppo or testo not in gruppo["divisi"]:
            return None
        parti, protetti, mappa = gruppo["divisi"][testo], gruppo["protetti"], dict(gruppo["mappa"])
        mancanti = [n for _, n, _ in parti if n and _da_tradurre(n) and n not in mappa]
        a_pezzi = [n for n in mancanti if protetti[n][1] and n not in gruppo.get("ripiegati", ())]
        if a_pezzi:
            try:
                for n, r in self.progetto._traduci_a_pezzi(_SenzaChiusura(traduttore), a_pezzi, da, a).items():
                    if r:
                        mappa[n] = maiuscola_come(n, r)
            except Exception as e:  # noqa: BLE001 - the fallback is optional
                self.log("  ! ripiego a pezzi non riuscito (%s: %s)" % (type(e).__name__, e))
        scarti = getattr(traduttore, "scarti", None) or {}
        for n in mancanti:
            if n in mappa:
                continue
            proposta = scarti.get(protetti[n][0])
            ripristinata = ripristina_segnaposto(proposta[0], protetti[n][1]) if proposta else None
            if not ripristinata:
                return None
            mappa[n] = maiuscola_come(n, ripristinata)
        risposta = ricomponi_righe(parti, mappa)
        if collections.Counter(_XUNITY.findall(testo)) != collections.Counter(_XUNITY.findall(risposta)):
            return None
        if altra_lingua(testo, risposta, da, a):
            return None
        return risposta

    # --- journal -----------------------------------------------------------------------------------

    def annota(self, testo, da, a, esito, backend=None, motivo="", risposta=None):
        """A line in the journal of the requested lines (see percorso_diario)."""
        riga = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "testo": testo, "da": da, "a": a,
                "esito": esito, "backend": backend, "motivo": motivo}
        if risposta is not None:
            riga["risposta"] = risposta
        dati = (json.dumps(riga, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lucchetto_diario:
            try:
                os.makedirs(os.path.dirname(self.diario), exist_ok=True)
                try:
                    if os.path.getsize(self.diario) > MAX_DIARIO:
                        os.replace(self.diario, self.diario + ".1")
                except OSError:
                    pass
                with open(self.diario, "ab") as f:
                    f.write(dati)
            except OSError as e:
                self.log("! Diario delle frasi non scritto (%s)" % e)

    # --- service lifetime --------------------------------------------------------------------------

    def _segui_gioco(self):
        while not self.chiusura.is_set():
            time.sleep(CONTROLLO_GIOCO)
            if self.pid and not processo_vivo(self.pid):
                self.log("Il gioco si e' chiuso (frasi tradotte: %d): chiudo il traduttore" % self.tradotte)
                self.chiusura.set()  # from now on lines get 503: no translation with the translator off
                # stop listening right away, without waiting for a translation in progress (with an online translator
                # it can take a minute): the port is freed and a game reopened right away starts its own translator
                # instead of relying on this one, which is closing. The translation in progress finishes and is saved
                # anyway: avvia() waits for it before closing the translators
                self.httpd.shutdown()
                return

    def _adotta(self):
        """Port already in use: if it is a PatchSmith service for this game, it hands it the new process."""
        base = "http://127.0.0.1:%d" % self.porta
        try:
            with urllib.request.urlopen(base + "/patchsmith", timeout=5) as r:
                if r.read().decode("utf-8", "replace") != "patchsmith " + self.nome:
                    return False
            if self.pid:
                with urllib.request.urlopen(base + "/adotta?pid=%d" % self.pid, timeout=5) as r:
                    r.read()
            return True
        except (OSError, ValueError):
            return False

    def avvia(self):
        """Listens until the game closes (or Ctrl+C). 0 if everything went well."""
        try:
            self.httpd = _ServerHttp(("127.0.0.1", self.porta), _Gestore)
        except OSError as e:
            if self._adotta():
                self.log("Il traduttore di questo gioco e' gia' attivo: continua quello")
                return 0
            self.log("! La porta %d e' occupata da un altro programma (%s): traduttore non avviato" % (self.porta, e))
            return 1
        self.httpd.servizio = self
        tolti = file_condiviso.pulisci_temporanei(os.path.dirname(self.progetto.percorso_memoria))
        if tolti:
            self.log("Tolti %d file temporanei rimasti da un salvataggio interrotto" % tolti)
        self.log("Traduttore per XUnity in ascolto su %s (gioco: %s)" % (indirizzo(self.porta), self.nome))
        threading.Thread(target=self._prepara, daemon=True).start()
        if self.pid:
            threading.Thread(target=self._segui_gioco, daemon=True).start()
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.chiusura.set()
            self.httpd.server_close()
            self._pronto.wait(timeout=300)  # if the translators are still starting, they are closed afterwards
            preso = self._lucchetto.acquire(timeout=MAX_CHIUSURA)  # a translation in progress finishes first
            try:
                for nome, traduttore in self.catena:
                    if hasattr(traduttore, "chiudi"):
                        try:
                            traduttore.chiudi()
                        except Exception as e:  # noqa: BLE001 - the others are closed anyway
                            self.log("! %s non chiuso bene (%s)" % (NOMI.get(nome, nome), e))
            finally:
                if preso:
                    self._lucchetto.release()
        return 0
