"""Traduttore delle frasi nuove per XUnity.AutoTranslator: un piccolo server sul PC (solo 127.0.0.1) che risponde come
l'indirizzo "CustomTranslate" di XUnity (GET /translate?from=en&to=it&text=... -> solo il testo tradotto).

Tutte le frasi che il gioco mostra mentre si gioca e che non sono gia' tradotte (es. "Hold RMB {{A}}s to leap up to
{{B}} m": XUnity toglie i numeri e li rimette dopo) passano da qui, qualunque traduttore si sia scelto: valgono la
memoria del gioco e quella comune, i controlli sulle traduzioni e le pause dei traduttori online (PatchSmith non
insiste con un servizio che lo ha bloccato e non aspetta piu' di MAX_ATTESA i suoi margini prudenti). I traduttori si
provano in fila (traduttori_previsti): il traduttore locale se e' installato (il modello Veloce, se c'e': la scheda
video serve anche al gioco), poi quello online scelto (DeepL con la chiave, MyMemory), poi Google gratuito se non e'
in pausa.

XUnity smette di usare un indirizzo dopo 5 errori di fila: per questo una frase che nessun traduttore sa tradurre
bene NON e' un errore. Si risponde (200) con la traduzione migliore se ha i segnaposto {{A}} intatti ed e' nella
lingua giusta ("rilassata"), altrimenti con l'originale ("rifiutata"); anche una frase che un traduttore online
potrebbe mandare solo tra molto tempo ("rinviata") riceve l'originale. Errore (500) solo se nessun traduttore
risponde, 503 mentre il gioco si chiude (nessuna traduzione con il traduttore che si sta spegnendo).

Ogni frase chiesta finisce nel diario memory/<gioco>.richieste.jsonl (vedi percorso_diario): una riga JSON con ts,
testo, da, a, esito (ESITI_DIARIO), backend, motivo e risposta. Le frasi con esito diverso da "ok" non sono nella
memoria: si possono ritradurre a gioco chiuso e correggere nella cache di XUnity.

Lo avvia il plugin di PatchSmith quando parte il gioco (BepInEx/plugins/Patchsmith) e si chiude da solo quando il
gioco si chiude, liberando la scheda video."""
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
PERCORSO_GRUPPO = "/traduci-gruppo"  # piu' frasi insieme (traduzione dal vivo dei giochi web)
MAX_GRUPPO = 60
MAX_CORPO = 1_000_000
CONTROLLO_GIOCO = 3.0  # secondi tra un controllo e l'altro del processo del gioco
MAX_ATTESA = 60.0  # secondi: XUnity aspetta una risposta al massimo 150 s, i traduttori online a volte di piu'
MAX_AVVIO = 140.0  # secondi di attesa dei traduttori che si avviano, poi la frase si rimanda
MAX_CHIUSURA = 180.0  # secondi: alla chiusura, una traduzione in corso (anche con le attese online) finisce prima
MAX_DIARIO = 2_000_000  # byte: poi il diario diventa <nome>.richieste.jsonl.1 e se ne comincia uno nuovo
# esiti nel diario: tradotta (e salvata in memoria); risposta di ripiego (non in memoria); originale perche' scartata
# da tutti i traduttori; originale perche' i traduttori online chiedono di aspettare troppo; nessun traduttore
# raggiungibile (500); gioco in chiusura (503)
ESITI_DIARIO = ("ok", "rilassata", "rifiutata", "rinviata", "errore", "interrotta")
NOMI = {"locale": "traduttore locale", "google": "Google", "deepl": "DeepL", "mymemory": "MyMemory",
        "finto": "traduttore di prova"}
_XUNITY = re.compile(r"\{\{[^{}]*\}\}")  # i segnaposto di XUnity ({{A}}): devono tornare tutti
# problemi (backend.problemi_traduzione) che dicono che la risposta non e' una traduzione nella lingua di arrivo
_NON_TRADOTTA = {"vuota", "scrittura sbagliata", "lettere di un'altra lingua", "non tradotta", "rifiuto del modello"}
# parole frequenti della lingua di arrivo che negli elenchi di lingua._PAROLE stanno solo tra quelle di altre lingue:
# in una risposta italiana "se", "tu", "su", "no" non vogliono dire che e' spagnola (vedi lingua.lingua_evidente)
_ANCHE_DI_ARRIVO = lingua.ANCHE_DI


def porta_per(nome_profilo):
    """Porta fissa per ogni gioco (47000-47999): due giochi non si scambiano le traduzioni."""
    return 47000 + zlib.crc32(nome_profilo.encode("utf-8")) % 1000


def indirizzo(porta):
    return "http://127.0.0.1:%d%s" % (porta, PERCORSO)


def acceso(nome_profilo, attesa=0.3):
    """True se il traduttore di questo gioco e' in ascolto (il gioco e' aperto e lo ha avviato)."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/patchsmith" % porta_per(nome_profilo), timeout=attesa) as r:
            return r.read().decode("utf-8", "replace") == "patchsmith " + nome_profilo
    except (OSError, ValueError):
        return False


def ferma(nome_profilo):
    """Ferma il traduttore di questo gioco (si riavvia con il gioco). True se era acceso."""
    if not acceso(nome_profilo):
        return False
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/ferma" % porta_per(nome_profilo), timeout=5) as r:
            r.read()
        return True
    except (OSError, ValueError):
        return False


def percorso_diario(nome_profilo):
    """Il diario delle frasi chieste mentre si gioca (una riga JSON per frase), tra i dati dell'utente."""
    return cartella_dati("memory", "%s.richieste.jsonl" % nome_profilo)


def leggi_diario(nome_profilo):
    """Le righe del diario (anche di quello vecchio, .1), dalla piu' vecchia: [{ts, testo, esito, risposta...}]. Le
    righe illeggibili (es. scritte a meta' da un gioco chiuso di colpo) si saltano."""
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
    """Un traduttore online chiede di aspettare piu' di MAX_ATTESA per restare nei margini prudenti: XUnity non
    aspetta tanto, la frase si rimanda (risposta con l'originale, nel diario come 'rinviata')."""


def _attesa_breve(secondi):
    """Al posto dell'attesa del limitatore (che puo' durare minuti): breve si aspetta, lunga no."""
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
    """DeepL o MyMemory, se sono il traduttore scelto per il gioco o quello scelto nell'interfaccia."""
    scelti = []
    for nome in (profilo.get("backend"), _impostazioni().get("backend")):
        if nome in ("deepl", "mymemory") and nome not in scelti:
            scelti.append(nome)
    return scelti


def traduttori_previsti(profilo):
    """I traduttori che il servizio userebbe adesso, in ordine: 'locale' se e' installato, il traduttore online
    scelto (DeepL solo con la chiave) se non e' in pausa, 'google' se non e' in pausa. Elenco vuoto: nessuno."""
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
    """Un traduttore della fila, preparato per tradurre mentre si gioca: il modello Veloce (4B) se c'e', attese
    brevi per i traduttori online (vedi _attesa_breve) e un solo nuovo tentativo dopo un errore di rete."""
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
    if hasattr(traduttore, "ATTESE"):  # DeepL: anche quando chiede di aspettare (Retry-After) si riprova presto una
        traduttore.ATTESE = (5,)  # volta sola, poi si passa al traduttore successivo
        traduttore.ATTESA_MASSIMA = 5.0
    return traduttore


def altra_lingua(originale, tradotto, origine, destinazione):
    """True se 'tradotto' non e' una traduzione nella lingua di arrivo: vuota, uguale all'originale, rifiutata dal
    modello, con un'altra scrittura o lettere di un'altra lingua, o con le parole frequenti di un'altra lingua."""
    if set(problemi_traduzione(originale, tradotto, destinazione)) & _NON_TRADOTTA:
        return True
    if _uguale(originale, tradotto) and re.search(r"[^\W\d_]", _XUNITY.sub(" ", originale)):
        return True  # rimasta uguale all'originale (anche con una sola parola frequente: "Hold {{A}}s to leap")
    return lingua.lingua_evidente(_XUNITY.sub(" ", tradotto), destinazione) is not None


class _SenzaChiusura:
    """Il traduttore resta acceso tra una frase e l'altra: Progetto lo chiuderebbe alla fine di ogni traduzione
    (chiudi) e farebbe le stime dei tempi (stima), che per una frase alla volta non servono. La pausa la controlla
    il servizio prima di provarlo."""

    def __init__(self, backend):
        self._backend = backend

    def __getattr__(self, nome):
        if nome in ("chiudi", "stima", "richieste", "pausa"):
            raise AttributeError(nome)
        return getattr(self._backend, nome)


def processo_vivo(pid):
    """True se il processo esiste ancora (Windows; se non si riesce a controllarlo si considera vivo)."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    h = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5  # accesso negato: esiste, ma e' di un altro utente
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(h), 0) == 0x102  # WAIT_TIMEOUT: ancora in esecuzione
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(h))


class _ServerHttp(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # su Windows permetterebbe a due server di prendersi la stessa porta

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class _Gestore(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """POST /traduci-gruppo {"from", "to", "testi": [...]} -> {"traduzioni": {testo: traduzione}, "riprova": [...]}:
        piu' frasi insieme, per la traduzione dal vivo dei giochi web (adapter_web_vivo)."""
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
        except Exception as e:  # imprevisto: lo script riprova piu' tardi
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
        if url.path == "/ferma":  # "Ferma il traduttore del gioco" nella finestra di PatchSmith
            servizio.log("Fermato dalla finestra di PatchSmith (frasi tradotte: %d)" % servizio.tradotte)
            servizio.chiusura.set()
            threading.Thread(target=servizio.httpd.shutdown, daemon=True).start()
            return self._rispondi(200, "ok")
        if url.path == "/adotta":  # il gioco e' stato riaperto: da ora si segue il nuovo processo
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
        except Exception as e:  # imprevisto: XUnity riprova la frase piu' avanti
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

    def log_message(self, *argomenti):  # niente righe per ogni richiesta
        pass


class Servizio:
    """porta=None: quella del gioco (porta_per). backend: per le prove, un traduttore gia' pronto o un elenco di
    (nome, traduttore) da provare in fila; None = la fila di traduttori_previsti. pid: processo del gioco da
    seguire (alla sua chiusura il servizio si ferma)."""

    def __init__(self, percorso_profilo, porta=None, pid=None, log=print, backend=None, prova=False):
        self.progetto = Progetto(percorso_profilo, log, prova=prova)
        # una frase alla volta mentre si gioca: niente secondi giri (l'altro modello caricherebbe la scheda video del
        # gioco) ne' problemi.json; le frasi scartate le annota il diario e si ritraducono a gioco chiuso
        self.progetto.recupero = False
        self.nome = self.progetto.profilo["nome"]
        self.porta = porta or porta_per(self.nome)
        self.pid = pid
        self.log = log
        self._backend = backend
        self.catena = []  # [(nome, traduttore)] pronti, nell'ordine in cui si provano
        self._lucchetto = threading.Lock()
        self._lucchetto_diario = threading.Lock()
        self._pronto = threading.Event()
        self.chiusura = threading.Event()  # il gioco si chiude: niente piu' traduzioni (503)
        self._errore = None
        self.httpd = None
        self.tradotte = 0
        self.diario = percorso_diario(self.nome)

    # --- traduttori --------------------------------------------------------------------------------

    def _prepara(self):
        """Prepara la fila dei traduttori e carica subito il modello locale: la prima frase non aspetta l'avvio della
        scheda video. Un traduttore che non parte si salta."""
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
                    traduttore.scarti = {}  # le proposte scartate servono per la risposta di ripiego
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
        """(codice HTTP, testo della risposta) per una frase chiesta da XUnity. Ogni frase finisce nel diario."""
        if self.chiusura.is_set():
            return self._in_chiusura(testo, da, a)
        memoria = self.progetto.memoria
        gia = memoria.get(testo)
        if isinstance(gia, str) and gia:  # gia' tradotta: senza aspettare i traduttori
            self.annota(testo, da, a, "ok", "memoria", risposta=gia)
            return 200, gia
        if self._parte_di_traduzione(testo):
            self.annota(testo, da, a, "ok", "gia' tradotta", risposta=testo)
            return 200, testo
        if not self._pronto.wait(MAX_AVVIO):
            self.annota(testo, da, a, "rinviata", motivo="i traduttori si stanno ancora avviando", risposta=testo)
            return 200, testo
        with self._lucchetto:  # una frase alla volta (XUnity ne manda una alla volta, ma qualcun altro potrebbe)
            if self.chiusura.is_set():
                return self._in_chiusura(testo, da, a)
            gia = memoria.get(testo)
            if isinstance(gia, str) and gia:
                self.annota(testo, da, a, "ok", "memoria", risposta=gia)
                return 200, gia
            return self._traduci(testo, da, a)

    def _parte_di_traduzione(self, testo):
        """True se il testo e' (un pezzo di) una traduzione gia' fatta: il gioco mostra gia' l'italiano (es. le tabelle
        dei testi tradotte nei bundle, che XUnity spezza tra i colori e chiede come se fossero in inglese). Ritradurlo
        dall'inglese lo rovinerebbe."""
        pezzo = testo.strip()
        memoria = self.progetto.memoria
        if len(pezzo) < 4 or pezzo in memoria:
            return False
        if getattr(self, "_tradotte_n", -1) != len(memoria):
            self._tradotte = "\n".join(v for v in memoria.values() if isinstance(v, str))
            self._tradotte_n = len(memoria)
        return pezzo in self._tradotte

    def rispondi_gruppo(self, testi, da, a):
        """({testo: traduzione}, [testi da richiedere piu' tardi]) per piu' frasi insieme: quelle gia' in memoria
        subito, le altre con una sola traduzione di gruppo per traduttore (il traduttore locale ne fa piu' d'una alla
        volta nella scheda video); quelle che il gruppo non traduce passano da una alla volta, come rispondi()."""
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
        for t in [t for t in mancanti if t not in traduzioni]:  # una alla volta: ripiego, scarti e diario
            if self.chiusura.is_set():
                riprova.append(t)
                continue
            with self._lucchetto:
                codice, risposta = self._traduci(t, da, a)
            if codice == 200 and risposta != t:
                traduzioni[t] = risposta
            else:  # lasciata in originale o traduttori fermi: lo script la richiede piu' tardi (poche volte)
                riprova.append(t)
        return traduzioni, riprova

    def _in_chiusura(self, testo, da, a):
        self.annota(testo, da, a, "interrotta", motivo="il gioco si sta chiudendo")
        return 503, "in chiusura"

    def adatta(self, risposta, a):
        """La risposta per XUnity senza i caratteri che i font del gioco non sanno disegnare (profilo
        ["caratteri_mancanti"], trovati dal controllo dopo aver giocato): è -> e'. In memoria e nel diario resta la
        traduzione vera."""
        from .characters import adatta_senza
        esclusi = self.progetto.profilo.get("caratteri_mancanti")
        return (adatta_senza(risposta, set(esclusi), a) or risposta) if esclusi else risposta

    def _traduci(self, testo, da, a):
        """Prova i traduttori in fila; se tutti scartano la frase, la risposta di ripiego (vedi _rilassata)."""
        memoria, p = self.progetto.memoria, self.progetto
        raggiunti, rinviati, errori = [], [], []
        for _, traduttore in self.catena:
            if getattr(traduttore, "scarti", None) is not None:
                traduttore.scarti.clear()
        for nome, traduttore in self.catena:
            pausa = traduttore.pausa() if hasattr(traduttore, "pausa") else None
            if pausa:  # in pausa per un blocco (anche arrivato mentre si gioca): non si insiste
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
            else:  # ha risposto, ma la traduzione non ha superato i controlli
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
        """La risposta di ripiego per una frase che tutti i traduttori hanno scartato: i pezzi tra i segnaposto
        tradotti a parte (una volta sola, solo dove non e' gia' stato fatto) e, per le righe ancora senza traduzione,
        la proposta scartata del traduttore locale. None se un segnaposto {{A}} di XUnity manca o se la risposta non
        e' nella lingua di arrivo: allora si risponde con l'originale."""
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
            except Exception as e:  # noqa: BLE001 - il ripiego e' facoltativo
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

    # --- diario ------------------------------------------------------------------------------------

    def annota(self, testo, da, a, esito, backend=None, motivo="", risposta=None):
        """Una riga nel diario delle frasi chieste (vedi percorso_diario)."""
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

    # --- vita del servizio -------------------------------------------------------------------------

    def _segui_gioco(self):
        while not self.chiusura.is_set():
            time.sleep(CONTROLLO_GIOCO)
            if self.pid and not processo_vivo(self.pid):
                self.log("Il gioco si e' chiuso (frasi tradotte: %d): chiudo il traduttore" % self.tradotte)
                self.chiusura.set()  # da adesso le frasi ricevono 503: nessuna traduzione a traduttore spento
                # si smette subito di ascoltare, senza aspettare una traduzione in corso (con un traduttore online
                # puo' durare un minuto): la porta si libera e un gioco riaperto subito avvia il suo traduttore invece
                # di affidarsi a questo, che si sta chiudendo. La traduzione in corso finisce e si salva lo stesso:
                # avvia() la aspetta prima di chiudere i traduttori
                self.httpd.shutdown()
                return

    def _adotta(self):
        """Porta gia' occupata: se e' un servizio di PatchSmith per questo gioco gli passa il nuovo processo."""
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
        """Ascolta fino alla chiusura del gioco (o a Ctrl+C). 0 se e' andato tutto bene."""
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
            self._pronto.wait(timeout=300)  # se i traduttori si stanno ancora avviando, li si chiude dopo
            preso = self._lucchetto.acquire(timeout=MAX_CHIUSURA)  # una traduzione in corso finisce prima
            try:
                for nome, traduttore in self.catena:
                    if hasattr(traduttore, "chiudi"):
                        try:
                            traduttore.chiudi()
                        except Exception as e:  # noqa: BLE001 - si chiudono comunque gli altri
                            self.log("! %s non chiuso bene (%s)" % (NOMI.get(nome, nome), e))
            finally:
                if preso:
                    self._lucchetto.release()
        return 0
