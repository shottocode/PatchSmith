"""Test del traduttore delle frasi nuove per XUnity (servizio_xunity), della sua installazione nel gioco e della
memoria condivisa tra piu' programmi (file_condiviso), senza scheda video e senza rete.

Uso: python -I test/prova_servizio_xunity.py <cartella_temporanea_vuota> [cartella_Data_di_un_gioco_Unity]
- servizio con il traduttore finto su 127.0.0.1: risposte, segnaposto {{A}} di XUnity, memoria, diario delle frasi,
  porta gia' in uso, chiusura alla fine del processo del gioco (503 mentre si chiude), temporanei vecchi tolti;
- traduttori in fila: uno che non risponde si salta, uno in pausa non si chiama, nessuno raggiungibile = 500;
- frasi scartate dai controlli: mai 500, risposta di ripiego ("rilassata") o l'originale ("rifiutata");
- Google con i margini prudenti di PatchSmith: un'attesa lunga rimanda la frase, senza richieste;
- quali traduttori userebbe il servizio (locale, online scelto, Google se non e' in pausa);
- memoria, traduzioni automatiche e memoria comune salvate insieme da piu' programmi: nessuna voce persa;
- configurazione di XUnity (CustomTranslate) e, se si indica la cartella _Data di un gioco Unity, compilazione del
  plugin che avvia il servizio (le librerie del gioco si leggono soltanto).
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

QUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(QUI))
sys.path.insert(0, QUI)

import prova_locale  # noqa: E402  (traduttore locale con un server simulato)
from patchsmith import adapter_unity, file_condiviso, interruzione, limitatore, ollama, progetto  # noqa: E402
from patchsmith import servizio_xunity  # noqa: E402
from patchsmith.backend import ErroreBackend, Finto  # noqa: E402
from patchsmith.progetto import CARTELLA_PROGETTO, Progetto, percorso_comune, percorso_memoria  # noqa: E402

ESITI = []
XUNITY = "Hold RMB {{A}}s to leap up to {{B}} m and slam"


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


def porta_libera():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def chiedi(porta, percorso, **parametri):
    url = "http://127.0.0.1:%d%s" % (porta, percorso)
    if parametri:
        url += "?" + urllib.parse.urlencode(parametri)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def traduci(porta, testo):
    return chiedi(porta, "/translate", **{"from": "en", "to": "it", "text": testo})


def profilo_di_prova(tmp, nome, backend=None):
    profilo = {"nome": nome, "cartella_gioco": tmp, "motore": "unity", "lingua_origine": "en",
               "lingua_destinazione": "it", "file": []}
    if backend:
        profilo["backend"] = backend
    percorso = os.path.join(tmp, nome + ".json")
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(profilo, f)
    return percorso


def diario(nome):
    try:
        with open(servizio_xunity.percorso_diario(nome), encoding="utf-8") as f:
            return [json.loads(r) for r in f if r.strip()]
    except FileNotFoundError:
        return []


class Avviato:
    """Un servizio in ascolto in un thread (senza processo del gioco da seguire), fermato all'uscita."""

    def __init__(self, tmp, nome, catena, prova=True):
        self.porta = porta_libera()
        self.registro = []
        self.servizio = servizio_xunity.Servizio(profilo_di_prova(tmp, nome), self.porta, None, self.registro.append,
                                                 backend=catena, prova=prova)
        self.filo = threading.Thread(target=self.servizio.avvia)

    def __enter__(self):
        self.filo.start()
        for _ in range(100):
            if self.servizio.httpd is not None:
                break
            time.sleep(0.05)
        self.servizio._pronto.wait(10)
        return self

    def __exit__(self, *_):
        if self.servizio.httpd is not None:
            self.servizio.httpd.shutdown()
        self.filo.join(timeout=10)


class Guasto:
    """Traduttore che non risponde."""
    nome = "guasto"

    def __init__(self):
        self.chiamate = 0

    def traduci_righe(self, righe, origine, destinazione):
        self.chiamate += 1
        raise ErroreBackend("non risponde (simulato)")


class Contato(Finto):
    """Il traduttore di prova che conta le chiamate."""

    def __init__(self, prefisso="[it]", pausa=None):
        self.prefisso, self.chiamate, self._pausa = prefisso, 0, pausa

    def pausa(self):
        return self._pausa

    def traduci_righe(self, righe, origine, destinazione):
        self.chiamate += 1
        return ["%s %s" % (self.prefisso, r) for r in righe]


class SenzaSegnaposto(Finto):
    """Come Google quando perde i segnaposto: frase intera senza i codici, pezzi non tradotti."""

    def traduci_righe(self, righe, origine, destinazione):
        return ["Tieni premuto per saltare" if "[[" in r else "" for r in righe]


class Ripete(Finto):
    """Come il traduttore locale quando ripete l'originale: nessuna traduzione accettata, la proposta scartata e'
    l'originale stesso."""

    def __init__(self):
        self.scarti = {}

    def traduci_righe(self, righe, origine, destinazione):
        for r in righe:
            self.scarti[r] = (r, ["lunghezza anomala"])
        return ["" for _ in righe]


def prova_servizio(tmp):
    percorso = profilo_di_prova(tmp, "prova-servizio-xunity")
    memoria = percorso_memoria({"nome": "prova-servizio-xunity", "lingua_destinazione": "it"}, prova=True)
    # un salvataggio interrotto di tanto tempo fa (da togliere) e uno appena cominciato (da lasciare)
    os.makedirs(os.path.dirname(memoria), exist_ok=True)
    vecchio, recente = memoria + ".tmp", memoria + ".123-4.tmp"
    for p in (vecchio, recente):
        open(p, "wb").close()
    os.utime(vecchio, (time.time() - 3600, time.time() - 3600))
    porta = porta_libera()
    gioco = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
    registro = []
    servizio_xunity.CONTROLLO_GIOCO = 0.3
    s = servizio_xunity.Servizio(percorso, porta, gioco.pid, registro.append, backend=Finto(), prova=True)
    filo = threading.Thread(target=s.avvia)
    filo.start()
    try:
        for _ in range(50):
            if s.httpd is not None:
                break
            threading.Event().wait(0.1)
        controlla("risponde come servizio di PatchSmith", chiedi(porta, "/patchsmith") ==
                  (200, "patchsmith prova-servizio-xunity"))
        codice, tradotto = traduci(porta, XUNITY)
        controlla("traduce (finto) con i segnaposto di XUnity intatti",
                  codice == 200 and tradotto.startswith("[it]") and "{{A}}" in tradotto and "{{B}}" in tradotto)
        controlla("salva in memoria", s.progetto.memoria.get(XUNITY) == tradotto and os.path.exists(memoria))
        controlla("stessa frase: stessa risposta", traduci(porta, XUNITY) == (200, tradotto))
        righe = diario("prova-servizio-xunity")
        controlla("diario: una riga per frase (traduttore, poi memoria), con testo, lingue e risposta",
                  [(r["esito"], r["backend"]) for r in righe] == [("ok", "finto"), ("ok", "memoria")]
                  and righe[0]["testo"] == XUNITY and righe[0]["risposta"] == tradotto
                  and (righe[0]["da"], righe[0]["a"]) == ("en", "it") and righe[0]["ts"])
        s.progetto.memoria["Did you know that higher level <artifact>artifacts</artifact> will earn you"] = \
            "Sai che gli <artifact>oggetti</artifact> di livello piu' alto ti fanno guadagnare"
        controlla("pezzo di una frase gia' tradotta (tabelle dei testi tradotte nel gioco): resta com'e', senza "
                  "traduttore", traduci(porta, "Sai che gli ") == (200, "Sai che gli ")
                  and diario("prova-servizio-xunity")[-1]["backend"] == "gia' tradotta")
        controlla("testo vuoto: risposta vuota", chiedi(porta, "/translate", text=" ") == (200, " "))
        controlla("indirizzo sconosciuto: 404", chiedi(porta, "/altro")[0] == 404)
        controlla("all'avvio tolto il temporaneo vecchio, lasciato quello recente",
                  not os.path.exists(vecchio) and os.path.exists(recente))
        secondo = servizio_xunity.Servizio(percorso, porta, os.getpid(), registro.append, backend=Finto(), prova=True)
        controlla("porta gia' usata dallo stesso gioco: il primo servizio segue il nuovo processo",
                  secondo.avvia() == 0 and s.pid == os.getpid())
        s.pid = gioco.pid
        controlla("processo del gioco vivo", servizio_xunity.processo_vivo(gioco.pid))
        gioco.kill()
        gioco.wait()
        controlla("processo del gioco chiuso", not servizio_xunity.processo_vivo(gioco.pid))
        filo.join(timeout=10)
        controlla("il servizio si chiude con il gioco", not filo.is_alive())
        controlla("registro della chiusura", any("si e' chiuso" in r for r in registro))
    finally:
        if gioco.poll() is None:
            gioco.kill()
        if filo.is_alive() and s.httpd is not None:
            s.httpd.shutdown()
        if os.path.exists(recente):
            os.remove(recente)


class Lento(Contato):
    """Traduttore lento (come Google che aspetta i suoi margini prudenti)."""

    def traduci_righe(self, righe, origine, destinazione):
        time.sleep(4)
        return super().traduci_righe(righe, origine, destinazione)


def porta_occupata(porta):
    with socket.socket() as s:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            s.bind(("127.0.0.1", porta))
            return False
        except OSError:
            return True


def prova_chiusura_con_traduzione_in_corso(tmp):
    """Il gioco si chiude mentre una frase si sta traducendo: la porta si libera subito (un gioco riaperto avvia il
    suo traduttore, invece di affidarsi a uno che si sta chiudendo) e la frase in corso finisce e si salva."""
    gioco = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(60)"])
    porta, lento = porta_libera(), Lento()
    servizio_xunity.CONTROLLO_GIOCO = 0.3
    s = servizio_xunity.Servizio(profilo_di_prova(tmp, "chiusura-in-corso"), porta, gioco.pid, silenzio,
                                 backend=[("google", lento)], prova=True)
    filo = threading.Thread(target=s.avvia)
    filo.start()
    risposta = []
    try:
        for _ in range(100):
            if s.httpd is not None:
                break
            time.sleep(0.05)
        s._pronto.wait(10)
        richiesta = threading.Thread(target=lambda: risposta.append(traduci(porta, "Open the gate")))
        richiesta.start()
        time.sleep(0.5)  # la frase e' in traduzione
        gioco.kill()
        gioco.wait()
        inizio = time.monotonic()
        while porta_occupata(porta) and time.monotonic() - inizio < 10:
            time.sleep(0.1)
        liberata = time.monotonic() - inizio
        richiesta.join(timeout=30)
        filo.join(timeout=30)
        controlla("gioco chiuso durante una traduzione: porta libera subito (%.1f s), la frase in corso finisce e "
                  "resta in memoria, il servizio si chiude" % liberata,
                  liberata < 2.5 and s.progetto.memoria.get("Open the gate") == "[it] Open the gate"
                  and diario("chiusura-in-corso")[-1]["esito"] == "ok" and not filo.is_alive())
    finally:
        if gioco.poll() is None:
            gioco.kill()
        if filo.is_alive() and s.httpd is not None:
            s.httpd.shutdown()


def prova_fila(tmp):
    """Piu' traduttori in fila: si va avanti con il prossimo; 500 solo se nessuno risponde."""
    guasto, buono = Guasto(), Contato()
    with Avviato(tmp, "fila", [("locale", guasto), ("google", buono)]) as a:
        codice, tradotto = traduci(a.porta, "Open the gate")
        ultima = diario("fila")[-1]
        controlla("il primo traduttore non risponde: traduce il secondo (200), il diario lo dice",
                  (codice, tradotto) == (200, "[it] Open the gate") and guasto.chiamate == 1
                  and (ultima["esito"], ultima["backend"]) == ("ok", "google"))
    in_pausa, dopo = Contato("[pausa]", pausa="Google e' in pausa (simulato)"), Contato("[mm]")
    with Avviato(tmp, "pausa", [("google", in_pausa), ("mymemory", dopo)]) as a:
        controlla("traduttore in pausa: non lo si chiama nemmeno, traduce il prossimo",
                  traduci(a.porta, "Open the gate") == (200, "[mm] Open the gate") and in_pausa.chiamate == 0)
    with Avviato(tmp, "nessuno", [("locale", Guasto()), ("google", Guasto())]) as a:
        codice, _ = traduci(a.porta, "Open the gate")
        ultima = diario("nessuno")[-1]
        controlla("nessun traduttore raggiungibile: 500 (solo in questo caso), nel diario 'errore' con i motivi",
                  codice == 500 and ultima["esito"] == "errore" and "non risponde" in ultima["motivo"])
    with Avviato(tmp, "vuota", []) as a:
        controlla("nessun traduttore disponibile: 500", traduci(a.porta, "Open the gate")[0] == 500)
    contato = Contato()
    with Avviato(tmp, "chiusura", [("google", contato)]) as a:
        a.servizio.chiusura.set()
        codice, _ = traduci(a.porta, "Open the gate")
        controlla("gioco in chiusura: 503 e nessuna traduzione (traduttore non chiamato), nel diario 'interrotta'",
                  codice == 503 and contato.chiamate == 0 and diario("chiusura")[-1]["esito"] == "interrotta")


def prova_scarti(tmp):
    """Frasi che nessun traduttore sa tradurre bene: 200 con la risposta di ripiego o con l'originale, mai 500."""
    def lunga(testo, _opzioni):  # traduzione lunghissima: scartata ("lunghezza anomala"), ma italiana e completa
        return "Tieni premuto " + testo.replace("Hold RMB", "RMB") + " " + "per saltare in alto " * 8

    b, _ = prova_locale.locale(lunga)
    with Avviato(tmp, "rilassata", [("locale", b)]) as a:
        codici, risposte = [], []
        for _ in range(3):
            codice, risposta = traduci(a.porta, XUNITY)
            codici.append(codice)
            risposte.append(risposta)
        righe = [r for r in diario("rilassata") if r["testo"] == XUNITY]
        controlla("scartata dai controlli: 200 con la traduzione migliore, segnaposto {{A}} intatti, mai 500",
                  codici == [200, 200, 200] and "{{A}}" in risposte[0] and "{{B}}" in risposte[0]
                  and risposte[0].startswith("Tieni premuto"))
        controlla("risposta di ripiego: NON in memoria, nel diario come 'rilassata' (da ritradurre dopo)",
                  XUNITY not in a.servizio.progetto.memoria and righe and all(r["esito"] == "rilassata" for r in righe)
                  and righe[0]["risposta"] == risposte[0] and "scartata" in righe[0]["motivo"])

    def a_pezzi(testo, _opzioni):  # la frase con i codici e' sbagliata, i pezzi tra i codici sono tradotti bene
        if "[[" in testo:
            return testo + " " + "e poi ancora " * 12
        return {"Hold RMB": "Tieni premuto RMB", "to leap up to": "per saltare fino a", "m and slam": "m e schiantati"
                }.get(testo, "")

    b, server = prova_locale.locale(a_pezzi)
    with Avviato(tmp, "a-pezzi", [("locale", b)]) as a:
        codice, risposta = traduci(a.porta, XUNITY)
        controlla("ripiego: i pezzi tra i segnaposto tradotti a parte (una volta), poi ricomposti",
                  (codice, risposta) == (200, "Tieni premuto RMB {{A}}s per saltare fino a {{B}} m e schiantati")
                  and diario("a-pezzi")[-1]["esito"] == "rilassata")

    def inglese(testo, _opzioni):  # risponde in inglese: nessuna traduzione
        return testo

    b, _ = prova_locale.locale(inglese)
    with Avviato(tmp, "inglese", [("locale", b)]) as a:
        codice, risposta = traduci(a.porta, XUNITY)
        ultima = diario("inglese")[-1]
        controlla("proposta in un'altra lingua: 200 con l'originale, nel diario 'rifiutata'",
                  (codice, risposta) == (200, XUNITY) and ultima["esito"] == "rifiutata"
                  and ultima["risposta"] == XUNITY)
    with Avviato(tmp, "segnaposto", [("google", SenzaSegnaposto())]) as a:
        codice, risposta = traduci(a.porta, XUNITY)
        controlla("segnaposto {{A}} persi (anche a pezzi): 200 con l'originale, 'rifiutata'",
                  (codice, risposta) == (200, XUNITY) and diario("segnaposto")[-1]["esito"] == "rifiutata")
    controlla("lingua della risposta: italiano si', inglese o spagnolo no",
              not servizio_xunity.altra_lingua("Open the door to the castle", "Apri la porta del castello", "en", "it")
              and servizio_xunity.altra_lingua("Open the door to the castle", "Open the door of the castle", "en",
                                               "it")
              and servizio_xunity.altra_lingua("Open the door to the castle", "Abre la puerta de los castillos y "
                                               "las torres", "en", "it"))
    # parole italiane che negli elenchi stanno tra quelle di altre lingue ("se", "tu", "su", "no", "o", "a"): una
    # risposta italiana non va scambiata per spagnola o portoghese; una rimasta in inglese si' (anche breve)
    italiane = ["Se tu vuoi, premi {{A}} su la mappa", "Se non hai oro, tu perdi", "Mettilo su il tavolo se puoi",
                "Vuoi salvare? Sì o no", "Sali su se osi", "Tocca a te? Se sì, tira", "No, non puoi andare lì"]
    scambiate = [t for t in italiane if servizio_xunity.altra_lingua("If you want, press {{A}}", t, "en", "it")]
    controlla("lingua della risposta: frasi italiane con 'se', 'tu', 'su', 'no' restano italiane%s" % (
        ": %s" % scambiate if scambiate else ""), not scambiate)
    controlla("lingua della risposta: uguale all'originale inglese (anche con una sola parola frequente) = non tradotta",
              servizio_xunity.altra_lingua("Hold RMB {{A}}s to leap", "Hold RMB {{A}}s to leap", "en", "it")
              and servizio_xunity.altra_lingua("Open the door in the castle", "Open the door in the castle now", "en",
                                               "it"))
    with Avviato(tmp, "uguale", [("locale", Ripete())]) as a:
        codice, risposta = traduci(a.porta, "Hold RMB {{A}}s to leap")
        controlla("proposta scartata uguale all'originale: 200 con l'originale, nel diario 'rifiutata' (non "
                  "'rilassata')", (codice, risposta) == (200, "Hold RMB {{A}}s to leap")
                  and diario("uguale")[-1]["esito"] == "rifiutata")


def prova_google(tmp):
    """Google nel servizio passa dal limitatore di PatchSmith: pause rispettate, attese lunghe = frase rimandata."""
    stato = limitatore.percorso_stato()
    adesso = time.time()
    os.makedirs(os.path.dirname(stato), exist_ok=True)
    with open(stato, "w", encoding="utf-8") as f:  # 30 richieste nell'ultimo minuto: la prossima tra ~10 minuti
        json.dump({"versione": 1, "google": {"richieste": [[adesso - 50 + i, 100] for i in range(30)]}}, f)
    g = servizio_xunity.crea_traduttore("google", silenzio)
    richieste = []
    g.apri = lambda req, timeout: richieste.append(req) or (_ for _ in ()).throw(OSError("niente rete nella prova"))
    controlla("Google nel servizio: attese brevi del limitatore e un solo nuovo tentativo dopo un errore di rete",
              g.limitatore.dormi is servizio_xunity._attesa_breve and g.ATTESE_RETE == (5,))
    with Avviato(tmp, "rinviata", [("google", g)]) as a:
        codice, risposta = traduci(a.porta, "Open the gate")
        ultima = diario("rinviata")[-1]
        controlla("Google con i margini prudenti pieni: frase rimandata (200 con l'originale, 'rinviata'), nessuna "
                  "richiesta", (codice, risposta) == (200, "Open the gate") and ultima["esito"] == "rinviata"
                  and not richieste)
    limitatore.Limitatore("google", log=None).bloccato(429, "prova")
    with Avviato(tmp, "google-in-pausa", [("google", g), ("mymemory", Contato("[mm]"))]) as a:
        controlla("Google in pausa per un blocco: non lo si chiama, traduce il prossimo",
                  traduci(a.porta, "Open the gate") == (200, "[mm] Open the gate") and not richieste)
    os.remove(stato)

    # DeepL nel servizio: anche se chiede di aspettare due minuti (Retry-After) si riprova presto una volta sola e poi
    # si passa al traduttore successivo (XUnity non aspetta tanto)
    attese, vero_attendi = [], interruzione.attendi
    vecchia_chiave = os.environ.get("PATCHSMITH_DEEPL")
    os.environ["PATCHSMITH_DEEPL"] = "chiave-di-prova:fx"
    try:
        interruzione.attendi = lambda s: attese.append(s) or (time.sleep(s) if s < 1 else None)
        d = servizio_xunity.crea_traduttore("deepl", silenzio)
        chiamate = []

        def troppe(req, timeout):
            chiamate.append(req)
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {"Retry-After": "120"},
                                         io.BytesIO(b""))
        d.apri = troppe
        try:
            d.traduci_righe(["Open the gate"], "en", "it")
            fermato = False
        except ErroreBackend:
            fermato = True
        controlla("DeepL nel servizio: un solo nuovo tentativo dopo al massimo 5 s, anche con Retry-After di 120 s "
                  "(%d richieste, attesa massima %s s)" % (len(chiamate), max(attese or [0])),
                  fermato and len(chiamate) == 2 and max(attese) <= 5)
    finally:
        interruzione.attendi = vero_attendi
        if vecchia_chiave is None:
            os.environ.pop("PATCHSMITH_DEEPL", None)
        else:
            os.environ["PATCHSMITH_DEEPL"] = vecchia_chiave
        if os.path.exists(stato):
            os.remove(stato)


def prova_previsti(tmp):
    """Quali traduttori usa il servizio: locale (se installato), quello online scelto, Google se non in pausa."""
    vero = ollama.stato
    impostazioni = progetto.cartella_dati("settings.json")
    os.environ.pop("PATCHSMITH_DEEPL", None)  # la chiave DeepL di chi lancia la prova non deve contare
    try:
        ollama.stato = lambda: "pronto"
        primo = servizio_xunity.traduttori_previsti({"backend": "google"})
        con_deepl = servizio_xunity.traduttori_previsti({"backend": "deepl"})  # senza chiave: no
        with open(impostazioni, "w", encoding="utf-8") as f:
            json.dump({"backend": "mymemory"}, f)
        con_mymemory = servizio_xunity.traduttori_previsti({"backend": "locale"})
        os.remove(impostazioni)
        limitatore.Limitatore("google", log=None).bloccato(429, "prova")
        in_pausa = servizio_xunity.traduttori_previsti({"backend": "google"})
        ollama.stato = lambda: "non installato"
        niente = servizio_xunity.traduttori_previsti({"backend": "google"})
        os.remove(limitatore.percorso_stato())  # Google di nuovo libero
        s = servizio_xunity.Servizio(profilo_di_prova(tmp, "fila-vera", "google"), porta_libera(), None, silenzio,
                                     prova=True)
        s._prepara()  # senza avviare il server: la fila che userebbe il servizio avviato dal gioco
        controlla("servizio avviato dal gioco senza traduttore locale: Google di PatchSmith, con le attese brevi",
                  [n for n, _ in s.catena] == ["google"] and s._pronto.is_set()
                  and s.catena[0][1].limitatore.dormi is servizio_xunity._attesa_breve)
        controlla("traduttori in fila: locale e Google; DeepL solo con la chiave; MyMemory se scelto; Google in pausa "
                  "escluso; nessuno se non c'e' altro (%s)" % [primo, con_deepl, con_mymemory, in_pausa, niente],
                  primo == ["locale", "google"] and con_deepl == ["locale", "google"]
                  and con_mymemory == ["locale", "mymemory", "google"] and in_pausa == ["locale"] and niente == [])
    finally:
        ollama.stato = vero
        if os.path.exists(limitatore.percorso_stato()):
            os.remove(limitatore.percorso_stato())
    b, _ = prova_locale.locale(lambda t, o: "IT " + t)
    controlla("traduttore locale: ricorda la proposta scartata solo se richiesto (servizio)", b.scarti is None)


# --- memoria condivisa tra piu' programmi ------------------------------------------------------------

FIGLIO = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from patchsmith import progetto
progetto.RADICE_DATI = sys.argv[2]
p = progetto.Progetto(sys.argv[3], lambda _: None)
while time.time() < float(sys.argv[5]):
    time.sleep(0.005)
for i in range(int(sys.argv[6])):
    p.memoria["%s-%d" % (sys.argv[4], i)] = "tradotto %s %d" % (sys.argv[4], i)
    p._salva_memoria()
"""


def prova_memoria_condivisa(tmp):
    percorso = profilo_di_prova(tmp, "condivisa")
    memoria = percorso_memoria({"nome": "condivisa", "lingua_destinazione": "it"})
    primo, secondo = Progetto(percorso, silenzio), Progetto(percorso, silenzio)
    primo.memoria["Start"] = "Inizia"
    secondo.memoria["Options"] = "Opzioni"
    via = threading.Barrier(2)

    def salva(p):
        via.wait()
        p._salva_memoria()
    fili = [threading.Thread(target=salva, args=(p,)) for p in (primo, secondo)]
    for f in fili:
        f.start()
    for f in fili:
        f.join()
    with open(memoria, encoding="utf-8") as f:
        su_disco = json.load(f)
    controlla("due programmi salvano insieme voci diverse: restano entrambe", su_disco == {"Start": "Inizia",
                                                                                         "Options": "Opzioni"})
    del primo.memoria["Start"]  # tolta da uno (es. una traduzione da rifare)...
    primo._salva_memoria()
    secondo.memoria["Back"] = "Indietro"  # ...mentre l'altro, che la aveva ancora, ne aggiunge un'altra
    secondo._salva_memoria()
    with open(memoria, encoding="utf-8") as f:
        su_disco = json.load(f)
    controlla("una voce tolta da uno non torna con il salvataggio dell'altro; le sue voci nuove restano",
              su_disco == {"Options": "Opzioni", "Back": "Indietro"} and secondo.memoria == su_disco)

    # piu' processi veri insieme, molte scritture ciascuno (come interfaccia e traduttore avviato dal gioco)
    figli, inizio, quante = [], time.time() + 1.5, 25
    for nome in ("a", "b", "c", "d"):
        figli.append(subprocess.Popen([sys.executable, "-I", "-c", FIGLIO, os.path.dirname(QUI),
                                       progetto.cartella_dati(), percorso, nome, str(inizio), str(quante)],
                                      stderr=subprocess.PIPE, text=True))
    uscite = []
    for f in figli:
        _, errori = f.communicate(timeout=120)
        uscite.append(f.returncode)
        if f.returncode:
            print(errori)
    with open(memoria, encoding="utf-8") as f:
        su_disco = json.load(f)
    attese = {"%s-%d" % (n, i) for n in "abcd" for i in range(quante)} | {"Options", "Back"}
    rimasti = [n for n in os.listdir(os.path.dirname(memoria)) if n.endswith(".tmp")]
    persi = sorted(attese - set(su_disco))
    controlla("4 processi x %d salvataggi insieme: nessuna voce persa, nessun file temporaneo rimasto%s" % (
        quante, "" if uscite == [0] * 4 and not persi and not rimasti else
        " (uscite %s, perse %s, rimasti %s)" % (uscite, persi[:5], rimasti)),
        uscite == [0] * 4 and set(su_disco) == attese and not rimasti)

    # memoria comune e traduzioni automatiche: stesse regole
    terzo = Progetto(profilo_di_prova(tmp, "condivisa-due"), silenzio)
    quarto = Progetto(profilo_di_prova(tmp, "condivisa-tre"), silenzio)
    comune_a = terzo._memoria_comune("en", "it")
    comune_b = quarto._memoria_comune("en", "it")
    terzo.memoria["Load"] = "Carica"
    quarto.memoria["Save"] = "Salva"
    terzo._aggiorna_comune(comune_a, "en", "it", ["Load"])
    quarto._aggiorna_comune(comune_b, "en", "it", ["Save"])
    with open(percorso_comune("en", "it"), encoding="utf-8") as f:
        comune = json.load(f)
    controlla("memoria comune: le voci nuove di due programmi restano entrambe",
              comune.get("Load") == "Carica" and comune.get("Save") == "Salva")

    # file rovinato (corretto a mano con un errore): si conserva a parte, non si perde
    with open(memoria, "w", encoding="utf-8") as f:
        f.write('{"Options": "Opzioni",')
    secondo.memoria["Exit"] = "Esci"
    secondo._salva_memoria()
    with open(memoria, encoding="utf-8") as f:
        dopo = json.load(f)
    controlla("memoria rovinata a mano: copiata in .illeggibile e riscritta con tutte le voci di questo programma",
              os.path.exists(memoria + ".illeggibile") and dopo.get("Exit") == "Esci"
              and dopo.get("Back") == "Indietro" and secondo.memoria == dopo)
    libero = not os.path.exists(memoria + ".lock")
    with file_condiviso.Lucchetto(memoria, attesa=1) as lucchetto:
        preso = lucchetto.preso
        with file_condiviso.Lucchetto(memoria, attesa=0.3) as altro:  # un altro programma aspetta, poi va avanti
            occupato = not altro.preso
    controlla("lucchetto: libero dopo l'uso (il file .lock non resta), esclusivo mentre lo si tiene",
              libero and preso and occupato and not os.path.exists(memoria + ".lock"))
    # un .lock che un altro programma sta togliendo non si apre per un attimo (Windows: "in cancellazione"): prima si
    # salvava senza lucchetto, e un salvataggio insieme a un altro perdeva voci
    vero_open, rifiuti = file_condiviso.os.open, [0]

    def apri_dopo_un_attimo(percorso, *argomenti):
        if str(percorso).endswith(".lock") and rifiuti[0] < 3:
            rifiuti[0] += 1
            raise PermissionError(13, "file in cancellazione")
        return vero_open(percorso, *argomenti)
    file_condiviso.os.open = apri_dopo_un_attimo
    try:
        with file_condiviso.Lucchetto(memoria, attesa=5) as lucchetto:
            preso = lucchetto.preso
    finally:
        file_condiviso.os.open = vero_open
    controlla("lucchetto: un .lock che non si apre per un attimo si riprova (non si salva senza lucchetto)",
              preso and rifiuti[0] == 3)


def prova_gruppo(tmp):
    """POST /traduci-gruppo (traduzione dal vivo dei giochi web): piu' frasi in una sola traduzione di gruppo, quelle in
    memoria subito, i pezzi gia' in italiano lasciati stare."""
    contato = Contato()
    with Avviato(tmp, "gruppo", [("finto", contato)]) as av:
        av.servizio.progetto.memoria["Settings"] = "Impostazioni"
        corpo = json.dumps({"from": "en", "to": "it", "testi": ["Start the adventure now", "Quit to the main menu",
                                                              "Settings", "Impostazioni"]}).encode("utf-8")
        req = urllib.request.Request("http://127.0.0.1:%d/traduci-gruppo" % av.porta, data=corpo,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            risposta = json.loads(r.read().decode("utf-8"))
    t = risposta["traduzioni"]
    controlla("gruppo: frasi nuove tradotte insieme (una chiamata al traduttore), quella in memoria subito, il pezzo gia' "
              "in italiano lasciato stare (%s)" % risposta,
              t.get("Start the adventure now", "").startswith("[it]") and t.get("Quit to the main menu", "").startswith("[it]")
              and t.get("Settings") == "Impostazioni" and "Impostazioni" not in t and not risposta["riprova"]
              and contato.chiamate == 1)


def prova_ferma(tmp):
    """Il pulsante "Ferma il traduttore del gioco": acceso() lo vede in ascolto, ferma() lo chiude."""
    nome = "prova-ferma-traduttore"
    porta = servizio_xunity.porta_per(nome)
    if porta_occupata(porta):
        print("(porta %d occupata: prova del pulsante Ferma saltata)" % porta)
        return
    s = servizio_xunity.Servizio(profilo_di_prova(tmp, nome), porta, None, silenzio, backend=Finto(), prova=True)
    filo = threading.Thread(target=s.avvia)
    filo.start()
    for _ in range(100):
        if s.httpd is not None:
            break
        time.sleep(0.05)
    prima = servizio_xunity.acceso(nome, attesa=2)
    fermato = servizio_xunity.ferma(nome)
    filo.join(timeout=15)
    controlla("Ferma il traduttore del gioco: era acceso, si chiude, poi risulta spento",
              prima and fermato and not filo.is_alive() and not servizio_xunity.acceso(nome))


def prova_configurazione(tmp, dati_gioco):
    cartella = os.path.join(tmp, "gioco")
    os.makedirs(cartella)
    url = servizio_xunity.indirizzo(47123)
    p = adapter_unity.configura(cartella, "en", "it", imgui=False, url=url)
    with open(p, encoding="utf-8") as f:
        testo = f.read()
    controlla("XUnity usa CustomTranslate verso il traduttore di PatchSmith, senza controllo anti-spam, testi lunghi",
              "Endpoint=CustomTranslate" in testo and "[Custom]" in testo and "Url=" + url in testo
              and "EnableShortDelay=True" in testo and "DisableSpamChecks=True" in testo
              and "MaxCharactersPerTranslation=2500" in testo and re.search(r"^FallbackEndpoint=$", testo, re.M))
    adapter_unity.configura(cartella, "en", "it", imgui=False)
    with open(p, encoding="utf-8") as f:
        testo = f.read()
    controlla("senza traduttore di PatchSmith torna a Google (500 caratteri)",
              "Endpoint=GoogleTranslateV2" in testo and "MaxCharactersPerTranslation=500" in testo)
    adapter_unity.configura(cartella, "en", "it", imgui=False, google=False)
    with open(p, encoding="utf-8") as f:
        testo = f.read()
    controlla("Google in pausa: nessun traduttore (Endpoint vuoto)", re.search(r"^Endpoint=$", testo, re.M)
              and "GoogleTranslateV2" not in testo)

    if not dati_gioco:
        print("(compilazione del plugin non provata: indica la cartella _Data di un gioco Unity Mono)")
        return
    zip_bepinex = progetto.cartella_componenti("BepInEx_win_x64_5.4.23.5.zip")
    with zipfile.ZipFile(zip_bepinex) as z:
        z.extract("BepInEx/core/BepInEx.dll", cartella)
    profilo = os.path.join(tmp, "profilo.json")
    url = adapter_unity.attiva_servizio(cartella, {"dati": dati_gioco}, profilo, "prova-servizio-xunity")
    controlla("plugin compilato", os.path.getsize(os.path.join(cartella, *adapter_unity.PLUGIN_SERVIZIO.split("/")))
              > 2000)
    with open(os.path.join(cartella, *adapter_unity.CONFIG_SERVIZIO.split("/")), encoding="utf-8") as f:
        cfg = f.read()
    controlla("comando del servizio", "servizio-xunity" in cfg and profilo in cfg and "Programma=" in cfg
              and os.path.exists(cfg.split("Programma=")[1].splitlines()[0]))
    controlla("indirizzo letto dal comando", adapter_unity.url_servizio(cartella) == url
              and url == servizio_xunity.indirizzo(servizio_xunity.porta_per("prova-servizio-xunity")))
    controlla("i file del servizio si tolgono con la disinstallazione",
              adapter_unity._creato_da_loro("config/patchsmith-servizio.cfg")
              and adapter_unity._creato_da_loro("plugins/Patchsmith/Patchsmith.Servizio.dll")
              and adapter_unity._creato_da_loro("patchsmith-servizio-stato.txt"))
    controlla("disattivato", adapter_unity.disattiva_servizio(cartella) and not adapter_unity.servizio_attivo(cartella)
              and adapter_unity.url_servizio(cartella) is None)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tmp = os.path.abspath(sys.argv[1])
    os.makedirs(tmp, exist_ok=True)
    if os.listdir(tmp):
        raise SystemExit("La cartella temporanea deve essere vuota: %s" % tmp)
    progetto.RADICE_DATI = os.path.join(tmp, "dati")  # memoria e diario del servizio di prova: mai tra i dati veri
    prova_servizio(tmp)
    prova_chiusura_con_traduzione_in_corso(tmp)
    prova_fila(tmp)
    prova_scarti(tmp)
    prova_google(tmp)
    prova_previsti(tmp)
    prova_memoria_condivisa(tmp)
    prova_gruppo(tmp)
    prova_ferma(tmp)
    prova_configurazione(tmp, sys.argv[2] if len(sys.argv) > 2 else None)
    falliti = [n for n, ok in ESITI if not ok]
    print("\n%d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
