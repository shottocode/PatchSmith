"""Test del limitatore delle richieste e dei traduttori online (Google gratuito, DeepL, MyMemory).

Uso: python -I test/test_limiter.py <cartella_temporanea_vuota>
Nessuna rete: le risposte HTTP sono simulate e l'orologio e' finto (nessuna attesa vera).
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

from patchsmith import backend, interruption as interruzione, limiter as limitatore, project as progetto  # noqa: E402
from patchsmith.project import Progetto  # noqa: E402

ESITI = []


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


class Orologio:
    def __init__(self, t=1_800_000_000.0):
        self.t = t
        self.attese = []

    def ora(self):
        return self.t

    def dormi(self, secondi):
        self.attese.append(secondi)
        self.t += secondi


class Risposta:
    def __init__(self, corpo, tipo="application/json; charset=UTF-8", url="https://translate.googleapis.com/x"):
        self.corpo, self.url = corpo, url
        self.headers = {"Content-Type": tipo}

    def read(self, *_):
        return self.corpo

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def errore_http(codice, corpo=b"", url="https://translate.googleapis.com/x", header=None):
    return urllib.error.HTTPError(url, codice, "errore", header or {}, io.BytesIO(corpo))


class Apri:
    """urlopen finto: ogni chiamata usa la funzione 'risposta(req)' e conta le richieste."""

    def __init__(self, risposta, orologio=None):
        self.risposta = risposta
        self.richieste = []
        self.orologio = orologio

    def __call__(self, req, timeout):
        self.richieste.append(req)
        if self.orologio:
            self.orologio.t += 1.0  # tempo di risposta
        esito = self.risposta(req)
        if isinstance(esito, Exception):
            raise esito
        return esito


def google_eco(req):
    """Google finto che 'traduce' anteponendo [it] a ogni riga."""
    q = urllib.parse.parse_qs(req.data.decode("utf-8"))["q"][0]
    righe = q.split("\n")
    return Risposta(json.dumps([[["\n".join("[it] " + r for r in righe), q, None, None]]]).encode())


def limitatore_finto(cartella, servizio="google", orologio=None, nome="stato.json"):
    orologio = orologio or Orologio()
    return limitatore.Limitatore(servizio, silenzio, os.path.join(cartella, nome), orologio.ora, orologio.dormi,
                                 lambda: 0.5), orologio


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    os.makedirs(os.path.join(progetto.CARTELLA_PROGETTO, "memory"))
    attese_rete = []
    attesa_vera = interruzione.attendi
    backend.interruzione.attendi = lambda s: attese_rete.append(s)  # attese per errori di rete: finte

    # --- 1. finestre e pause su 200 richieste
    lim, orologio = limitatore_finto(base, nome="finestre.json")
    tempi = []
    for _ in range(200):
        lim.prima_di(3600)
        tempi.append(orologio.t)
        orologio.t += 1.0
    def massimo_in(durata):
        return max(sum(1 for x in tempi if t <= x < t + durata) for t in tempi)
    controlla("limitatore: mai piu' di 8 richieste al minuto, 30 ogni 10 minuti, 60 all'ora",
              massimo_in(60) <= 8 and massimo_in(600) <= 30 and massimo_in(3600) <= 60)
    controlla("limitatore: almeno 4 s (+ variazione) tra due richieste",
              min(b - a for a, b in zip(tempi, tempi[1:])) >= 4.0)
    controlla("limitatore: caratteri per minuto entro il margine",
              max(sum(3600 for x in tempi if t <= x < t + 60) for t in tempi) <= 32_000)

    # --- 2. primo 429 "Sorry": una sola richiesta, nessuna attesa, pausa ricordata 24 ore
    lim, orologio = limitatore_finto(base, nome="blocco.json")
    apri = Apri(lambda req: errore_http(429, b"<html><title>Sorry...</title>automated queries</html>"), orologio)
    g = backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim)
    try:
        g.traduci_righe(["hello", "world"], "en", "it")
        messaggio = ""
    except backend.Bloccato as e:
        messaggio = str(e)
    stato = json.load(open(os.path.join(base, "blocco.json"), encoding="utf-8"))["google"]["blocco"]
    controlla("Google 429 alla prima richiesta: una sola richiesta, nessuna attesa 30/60/120 s",
              len(apri.richieste) == 1 and not attese_rete and sum(orologio.attese) < 10)
    controlla("... blocco 'a freddo' ricordato per 24 ore, messaggio onesto (non 'troppe richieste')",
              stato["tipo"] == "a_freddo" and abs(stato["fino"] - stato["dal"] - 86400) < 1
              and "non e' un limite superato" in messaggio and "troppe richieste" not in messaggio)
    lim2, _ = limitatore_finto(base, nome="blocco.json", orologio=orologio)
    apri2 = Apri(google_eco, orologio)
    try:
        backend.GoogleGratis(log=silenzio, apri=apri2, limitatore=lim2).traduci_righe(["hello"], "en", "it")
        pausa = ""
    except backend.Bloccato as e:
        pausa = str(e)
    controlla("persistenza: un nuovo avvio durante la pausa non manda nessuna richiesta e dice fino a quando",
              not apri2.richieste and "in pausa fino" in pausa)
    orologio.t += 86400 + 1
    controlla("dopo la pausa si riprova", backend.GoogleGratis(log=silenzio, apri=apri2, limitatore=lim2)
              .traduci_righe(["hello"], "en", "it") == ["[it] hello"])

    # --- 3. livelli: a freddo 1 -> 3 -> 7 giorni -> disattivato; dopo traffico 1 ora
    lim, orologio = limitatore_finto(base, nome="livelli.json")
    fini = []
    for _ in range(4):
        lim.bloccato(429)
        s = json.load(open(os.path.join(base, "livelli.json"), encoding="utf-8"))["google"]["blocco"]
        fini.append((round((s["fino"] - s["dal"]) / 86400), s["disattivato"]))
        orologio.t = s["fino"] + 1
    controlla("blocchi a freddo ripetuti: 1, 3, 7 giorni, poi disattivato finche' non lo riattivi tu",
              fini[:3] == [(1, False), (3, False), (7, False)] and fini[3][1] is True and lim.pausa())
    lim.azzera()
    controlla("riattivazione manuale ('smith servizi --azzera google')", lim.pausa() is None)
    lim, orologio = limitatore_finto(base, nome="traffico.json")
    for _ in range(3):
        lim.prima_di(100)
        lim.ok()
    lim.bloccato(429)
    s = json.load(open(os.path.join(base, "traffico.json"), encoding="utf-8"))["google"]["blocco"]
    controlla("blocco dopo richieste riuscite: 'dopo traffico', pausa di un'ora",
              s["tipo"] == "dopo_traffico" and abs(s["fino"] - s["dal"] - 3600) < 1)

    # --- 4. altri segnali: HTML al posto del JSON, /sorry/, consenso cookie, 400
    for nome, risposta, atteso in (
            ("pagina HTML con 200", lambda r: Risposta(b"<html>Sorry</html>", "text/html"), backend.Bloccato),
            ("redirect a /sorry/", lambda r: Risposta(b"x", "text/html", "https://www.google.com/sorry/index"),
             backend.Bloccato),
            ("consenso cookie", lambda r: Risposta(b"x", "text/html", "https://consent.google.com/m"),
             backend.ErroreBackend),
            ("400 (lingue)", lambda r: errore_http(400, b"bad"), backend.ErroreBackend)):
        lim, orologio = limitatore_finto(base, nome="segnali-%s.json" % len(nome))
        try:
            backend.GoogleGratis(log=silenzio, apri=Apri(risposta, orologio), limitatore=lim).traduci_righe(
                ["hello"], "en", "it")
            preso = None
        except backend.ErroreBackend as e:
            preso = type(e)
        controlla("segnale '%s' riconosciuto correttamente" % nome, preso is atteso
                  and (atteso is backend.Bloccato) == bool(lim.pausa()))

    # --- 5. righe non allineate: divisione a meta', niente raffica
    lim, orologio = limitatore_finto(base, nome="allinea.json")

    def sfasata(req):
        q = urllib.parse.parse_qs(req.data.decode("utf-8"))["q"][0]
        righe = q.split("\n")
        if len(righe) > 1:
            righe = righe[:-1]  # Google unisce sempre due righe
        return Risposta(json.dumps([[["\n".join(righe), q, None, None]]]).encode())
    apri = Apri(sfasata, orologio)
    esito = backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim).traduci_righe(
        ["riga numero %d" % i for i in range(150)], "en", "it")
    controlla("righe non allineate: al massimo 15 richieste (non 151), le righe restano non tradotte",
              len(apri.richieste) <= 15 and all(t == "" for t in esito))

    # --- 6. errori di rete: 2 nuovi tentativi (10 s, 30 s) e poi stop
    lim, orologio = limitatore_finto(base, nome="rete.json")
    apri = Apri(lambda r: urllib.error.URLError("rete assente"), orologio)
    del attese_rete[:]
    try:
        backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim).traduci_righe(["hello"], "en", "it")
        preso = False
    except backend.ErroreBackend:
        preso = True
    controlla("errori di rete: 3 tentativi (attese 10 e 30 s), poi stop", preso and len(apri.richieste) == 3
              and attese_rete == [10, 30] and lim.pausa() is None)

    # --- 7. Ferma durante una pausa lunga del limitatore (attesa vera, interrotta)
    lim = limitatore.Limitatore("google", silenzio, os.path.join(base, "ferma.json"), dormi=attesa_vera)
    lim.prima_di(100)
    for _ in range(7):
        s = lim._leggi()
        s["google"]["richieste"].append([time.time(), 100])
        lim._scrivi(s)
    threading.Timer(0.3, interruzione.richiedi).start()
    inizio = time.monotonic()
    try:
        lim.prima_di(100)
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    controlla("Ferma durante una pausa del limitatore: si ferma subito", fermato and time.monotonic() - inizio < 3)

    # --- 8. stima
    lim, _ = limitatore_finto(base, nome="stima.json")
    forager = lim.stima([3600] * 12)
    grande = lim.stima([3600] * 113)
    controlla("stima: Forager (~42.000 caratteri, 12 richieste) in pochi minuti; 406.000 caratteri oltre un'ora",
              60 <= forager <= 300 and grande >= 3600)

    # --- 9. file di stato rovinato o con date assurde
    rotto = os.path.join(base, "rotto.json")
    open(rotto, "w").write("{non json")
    lim, orologio = limitatore_finto(base, nome="rotto.json")
    lim.prima_di(10)
    futuro = os.path.join(base, "futuro.json")
    json.dump({"google": {"richieste": [], "blocco": {"dal": 1, "fino": 4_000_000_000, "livello": 1,
                                                      "tipo": "a_freddo"}}}, open(futuro, "w"))
    lim, orologio = limitatore_finto(base, nome="futuro.json")
    lim.pausa()
    dati = json.load(open(futuro, encoding="utf-8")) if os.path.exists(futuro) else {}
    controlla("stato rovinato: nessun errore; data assurda ridotta a 8 giorni",
              lim.pausa() is not None and lim._mio(lim._leggi(), orologio.t)["blocco"]["fino"] <= orologio.t + 8 * 86400
              and dati is not None)

    # --- 10. due limitatori sullo stesso file contano insieme (interfaccia + riga di comando)
    orologio = Orologio()
    a, _ = limitatore_finto(base, nome="condiviso.json", orologio=orologio)
    b, _ = limitatore_finto(base, nome="condiviso.json", orologio=orologio)
    a.prima_di(100)
    b.prima_di(100)
    controlla("stato condiviso: il secondo limitatore aspetta la pausa del primo", orologio.attese and
              orologio.attese[0] >= 4.0)

    # --- 11. DeepL
    lim, orologio = limitatore_finto(base, "deepl", nome="deepl.json")
    ricevute = []

    def deepl_ok(req):
        ricevute.append((req.full_url, dict(req.header_items()), json.loads(req.data.decode("utf-8"))))
        testi = json.loads(req.data.decode("utf-8"))["text"]
        return Risposta(json.dumps({"translations": [{"text": "IT " + t} for t in testi]}).encode())
    d = backend.DeepL("abc-123:fx", log=silenzio, apri=Apri(deepl_ok, orologio), limitatore=lim)
    esito = d.traduci_righe(["one", "two", "three"], "en", "it")
    url, header, corpo = ricevute[0]
    controlla("DeepL: chiave gratuita -> api-free, chiave nell'header, lista di testi, lingue giuste",
              url.startswith("https://api-free.deepl.com/v2/translate") and esito == ["IT one", "IT two", "IT three"]
              and header.get("Authorization") == "DeepL-Auth-Key abc-123:fx" and corpo["target_lang"] == "IT"
              and corpo["source_lang"] == "EN")
    controlla("DeepL: codici delle lingue (zh-TW, pt-BR, en)",
              backend._lingua_deepl("zh-TW", True) == "ZH-HANT" and backend._lingua_deepl("pt-BR", True) == "PT-BR"
              and backend._lingua_deepl("en", True) == "EN-US" and backend._lingua_deepl("zh-CN", False) == "ZH")
    for codice, testo in ((456, "finito"), (403, "chiave")):
        try:
            backend.DeepL("x:fx", log=silenzio, apri=Apri(lambda r, c=codice: errore_http(c), orologio),
                          limitatore=lim).traduci_righe(["one"], "en", "it")
            messaggio = ""
        except backend.ErroreBackend as e:
            messaggio = str(e)
        controlla("DeepL %d: messaggio chiaro (%s)" % (codice, testo), testo in messaggio)
    sequenza = [errore_http(429, header={"Retry-After": "2"}), None]

    def prima_429(req):
        e = sequenza.pop(0)
        return e if e else deepl_ok(req)
    del attese_rete[:]
    controlla("DeepL 429: aspetta quanto chiede (Retry-After) e riprova",
              backend.DeepL("x:fx", log=silenzio, apri=Apri(prima_429, orologio), limitatore=lim)
              .traduci_righe(["one"], "en", "it") == ["IT one"] and attese_rete == [2.0])
    try:
        backend.DeepL("", log=silenzio)
        messaggio = ""
    except backend.ErroreBackend as e:
        messaggio = str(e)
    controlla("DeepL senza chiave: spiega come averla", "deepl.com" in messaggio)

    # --- 12. MyMemory
    lim, orologio = limitatore_finto(base, "mymemory", nome="mymemory.json")
    url_mm = []

    def mymemory(req):
        url_mm.append(req.full_url)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
        return Risposta(json.dumps({"responseData": {"translatedText": "IT " + q["q"][0]},
                                    "responseStatus": 200, "quotaFinished": False}).encode())
    m = backend.MyMemory("io@example.com", log=silenzio, apri=Apri(mymemory, orologio), limitatore=lim)
    lunga = "Frase numero uno. " * 60
    esito = m.traduci_righe(["hello", lunga], "en", "it")
    parametri = urllib.parse.parse_qs(urllib.parse.urlparse(url_mm[0]).query)
    controlla("MyMemory: una richiesta per riga, email inviata, righe lunghe divise sotto i 500 byte",
              esito[0] == "IT hello" and parametri.get("de") == ["io@example.com"] and parametri["langpair"] == ["en|it"]
              and all(len(urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["q"][0].encode()) <= 500
                      for u in url_mm) and len(url_mm) > 2)
    finita = Apri(lambda r: Risposta(json.dumps({"responseData": {"translatedText": "MYMEMORY WARNING: YOU USED ALL "
                                                                                     "AVAILABLE FREE TRANSLATIONS"},
                                                 "responseStatus": 429, "quotaFinished": True}).encode()), orologio)
    try:
        backend.MyMemory(None, log=silenzio, apri=finita, limitatore=lim).traduci_righe(["hello"], "en", "it")
        messaggio = ""
    except backend.Bloccato as e:
        messaggio = str(e)
    controlla("MyMemory: caratteri del giorno finiti -> stop chiaro e pausa fino a domani",
              "domani" in messaggio and lim.pausa() is not None)

    # --- 13. credenziali da settings.json
    with open(os.path.join(progetto.CARTELLA_PROGETTO, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"chiave_deepl": "k:fx", "email_mymemory": "a@b.it"}, f)
    controlla("credenziali lette dalle impostazioni",
              backend.crea_backend("deepl", silenzio).chiave == "k:fx"
              and backend.crea_backend("mymemory", silenzio).email == "a@b.it")

    # --- 14. progetto: servizio in pausa -> nessuna richiesta; gruppi per caratteri; ripieghi in una richiesta
    profilo = os.path.join(base, "profilo.json")
    json.dump({"nome": "prova", "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
               "file": []}, open(profilo, "w"))
    p = Progetto(profilo, silenzio)
    lim, orologio = limitatore_finto(base, nome="progetto.json")
    lim.bloccato(429)
    apri = Apri(google_eco, orologio)
    falliti = p.traduci_testi(["hello", "world"], backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim),
                              "en", "it")
    controlla("progetto: Google in pausa -> traduzione non avviata, zero richieste", falliti == 2
              and not apri.richieste)
    lim, orologio = limitatore_finto(base, nome="progetto2.json")
    apri = Apri(google_eco, orologio)
    testi = ["A sentence that is quite long, number %d, to fill the groups." % i for i in range(1690)]
    righe = []
    p.log = righe.append
    p.traduci_testi(testi, backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim), "en", "it")
    controlla("progetto: gruppi da una richiesta ciascuno (circa 3.600 caratteri), stima mostrata prima",
              len(apri.richieste) == len([r for r in righe if r.lstrip("").startswith("  testi ")])  # (dettaglio: solo su disco)
              and all(len(urllib.parse.parse_qs(r.data.decode())["q"][0]) <= 4000 for r in apri.richieste)
              and any("Richieste previste" in r and "tempo stimato" in r for r in righe))

    class Rovina(backend.Backend):
        nome = "rovina"

        def __init__(self):
            self.chiamate = 0

        def traduci_righe(self, righe, origine, destinazione):
            self.chiamate += 1
            return [r.replace("[[0]]", "[0]") + " it" for r in righe]  # rovina i segnaposto
    rov = Rovina()
    p.traduci_testi(["Hello {name}, welcome %d" % i for i in range(20)], rov, "en", "it")
    controlla("segnaposto rovinati: i ripieghi di tutto il gruppo in una sola richiesta (non una per testo)",
              rov.chiamate <= 2)

    # --- 15. casi trovati dalla revisione
    riga = "Thanks! " + " ".join("tester%d" % i for i in range(120)) + ". Bye now."
    controlla("righe lunghe: divise senza cambiare l'ordine delle frasi",
              " ".join(backend._dividi_lunga(riga, 480)) == riga and " ".join(backend._dividi_lunga(riga, 100)) == riga)
    misto = "あ" * 170 + " Press A to continue." + "い" * 200
    controlla("MyMemory: pezzi sempre entro 480 byte anche con giapponese + inglese",
              all(len(p.encode("utf-8")) <= 480 for p in backend._dividi_byte(misto)))
    lim, orologio = limitatore_finto(base, nome="orologio.json")
    lim.bloccato(429, adesso=orologio.t + 365 * 86400)  # orologio avanti di un anno quando arriva il blocco
    lim.pausa()
    orologio.t += 9 * 86400
    controlla("blocco registrato con l'orologio avanti: dura al massimo 8 giorni, poi finisce davvero",
              lim.pausa() is None)
    pulita = backend.DeepL("abcd-1234\r\nefgh:fx", log=silenzio)
    try:
        backend.DeepL("abcd-SEGRETA:fxé", log=silenzio)
        messaggio = ""
    except backend.ErroreBackend as e:
        messaggio = str(e)
    controlla("DeepL: chiave incollata con un a capo ripulita; caratteri strani -> errore senza mostrarla",
              pulita.chiave == "abcd-1234efgh:fx" and "non validi" in messaggio and "SEGRETA" not in messaggio)

    def rotta(req, timeout):
        raise ValueError("Invalid header value b'DeepL-Auth-Key abcd-SEGRETA:fx'")
    lim, orologio = limitatore_finto(base, "deepl", nome="deepl2.json")
    del attese_rete[:]
    try:
        backend.DeepL("abcd-SEGRETA:fx", log=silenzio, apri=rotta, limitatore=lim).traduci_righe(["one"], "en", "it")
        messaggio = ""
    except backend.ErroreBackend as e:
        messaggio = str(e)
    controlla("DeepL: errore nel preparare la richiesta -> nessun nuovo tentativo e chiave mai nel messaggio",
              messaggio and "SEGRETA" not in messaggio and not attese_rete)
    for nome, risposta in (("503 con pagina HTML", errore_http(503, b"<html>Our systems have detected unusual traffic",
                                                               header={"Content-Type": "text/html"})),
                           ("403 senza HTML", errore_http(403, b"Forbidden"))):
        lim, orologio = limitatore_finto(base, nome="html-%d.json" % len(nome))
        apri = Apri(lambda r, x=risposta: x, orologio)
        try:
            backend.GoogleGratis(log=silenzio, apri=apri, limitatore=lim).traduci_righe(["hello"], "en", "it")
            preso = None
        except backend.ErroreBackend as e:
            preso = type(e)
        controlla("Google %s: blocco, una sola richiesta" % nome, preso is backend.Bloccato and len(apri.richieste) == 1)
    lim, orologio = limitatore_finto(base, nome="formato.json")
    try:
        backend.GoogleGratis(log=silenzio, apri=Apri(lambda r: Risposta(b'{"x": 1}'), orologio),
                             limitatore=lim).traduci_righe(["hello"], "en", "it")
        messaggio = ""
    except backend.ErroreBackend as e:
        messaggio = str(e)
    controlla("Google: risposta in un formato inatteso -> stop con messaggio", "formato inatteso" in messaggio)

    # MyMemory: quota finita a meta' gruppo -> le righe gia' tradotte restano in memoria
    lim, orologio = limitatore_finto(base, "mymemory", nome="mm-quota.json")
    conteggio = [0]

    def quota_dopo_3(req):
        conteggio[0] += 1
        if conteggio[0] > 3:
            return Risposta(json.dumps({"responseData": {"translatedText": "MYMEMORY WARNING"},
                                        "responseStatus": 429, "quotaFinished": True}).encode())
        return mymemory(req)
    p.log = silenzio
    p.memoria = {}
    p.traduci_testi(["frase %d" % i for i in range(8)],
                    backend.MyMemory(None, log=silenzio, apri=Apri(quota_dopo_3, orologio), limitatore=lim), "en", "it")
    controlla("MyMemory: quota finita a meta' gruppo -> le 3 frasi gia' tradotte restano salvate",
              sorted(p.memoria) == ["frase 0", "frase 1", "frase 2"])
    controlla("MyMemory: quota finita senza email -> con l'email si puo' continuare subito",
              backend.MyMemory("io@example.com", log=silenzio, limitatore=lim).pausa() is None)
    righe = []
    p.log = righe.append
    lim, orologio = limitatore_finto(base, "mymemory", nome="mm-stima.json")
    p.traduci_testi(["frase lunga numero %d con parecchie parole dentro" % i for i in range(150)],
                    backend.MyMemory(None, log=silenzio, apri=Apri(mymemory, orologio), limitatore=lim), "en", "it")
    controlla("MyMemory: richieste previste = una per riga, avviso se si supera la quota del giorno",
              any("Richieste previste: circa 150" in r for r in righe)
              and any("al giorno" in r for r in righe))
    lim, orologio = limitatore_finto(base, nome="messaggio.json")
    lim.prima_di(10)
    lim.ok()
    orologio.t += 3 * 3600
    lim2, _ = limitatore_finto(base, nome="messaggio.json", orologio=orologio)
    controlla("messaggio di blocco senza 'dopo 0 traduzioni'", "dopo 0" not in lim2.bloccato(429))

    from patchsmith import cli
    profilo_cli = os.path.join(progetto.CARTELLA_PROGETTO, "profilo-cli.json")
    json.dump({"nome": "cli", "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
               "file": [], "backend": "deepl"}, open(profilo_cli, "w"))
    os.remove(os.path.join(progetto.CARTELLA_PROGETTO, "settings.json"))
    os.environ.pop("PATCHSMITH_DEEPL", None)
    try:
        codice = cli.main(["traduci", profilo_cli, "--backend", "deepl"])
    except Exception:
        codice = "eccezione"
    controlla("riga di comando: DeepL senza chiave -> errore spiegato (uscita 1), niente traceback", codice == 1)

    # --- pulsante "Verifica": chiave DeepL (consumo del mese, nessun testo) ed email di MyMemory
    chieste = []

    def uso(req):
        chieste.append(req.full_url)
        return Risposta(json.dumps({"character_count": 1234, "character_limit": 500000}).encode())
    ok, messaggio = backend.verifica_traduttore("deepl", "abc-123:fx", apri=Apri(uso))
    controlla("Verifica DeepL: chiave buona -> consumo del mese (1.234 su 500.000), con /v2/usage",
              ok and "1.234" in messaggio and "500.000" in messaggio and chieste == ["https://api-free.deepl.com/v2/usage"])
    ok, messaggio = backend.verifica_traduttore("deepl", "sbagliata:fx", apri=Apri(lambda r: errore_http(403)))
    controlla("Verifica DeepL: chiave rifiutata -> spiegato (HTTP 403)", not ok and "non accetta la chiave" in messaggio)
    ok, messaggio = backend.verifica_traduttore("mymemory", "nome@", apri=Apri(lambda r: 1 / 0))
    controlla("Verifica MyMemory: email scritta male -> detto subito, senza rete", not ok and "non sembra" in messaggio)
    risposta_mm = Risposta(json.dumps({"responseStatus": 200, "responseData": {"translatedText": "Ciao"}}).encode())
    ok, messaggio = backend.verifica_traduttore("mymemory", "nome@esempio.it", apri=Apri(lambda r: risposta_mm))
    controlla("Verifica MyMemory: email accettata -> 50.000 caratteri al giorno, nessun account", ok and "50.000" in messaggio)
    ok, messaggio = backend.verifica_traduttore("mymemory", "", apri=Apri(lambda r: OSError("rete")))
    controlla("Verifica MyMemory senza rete: spiegato, niente eccezioni", not ok and ("connessione" in messaggio or "non risponde" in messaggio))

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
