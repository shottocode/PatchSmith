"""Test del traduttore locale (Ollama + TranslateGemma) senza rete e senza scheda video.

Uso: python -I test/prova_locale.py <cartella_temporanea_vuota>
- livello 1: backend Locale con un server simulato in memoria (risposte programmate);
- livello 2: ollama.Server che avvia e chiude un Ollama simulato (test/ollama_finto.py) su 127.0.0.1.
"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import backend, interruzione, ollama, progetto  # noqa: E402
from patchsmith.progetto import Progetto  # noqa: E402

ESITI = []
QUI = os.path.dirname(os.path.abspath(__file__))


def controlla(nome, condizione):
    ESITI.append((nome, bool(condizione)))
    print(("OK    " if condizione else "ERRORE") + "  " + nome)


def silenzio(_):
    pass


class ServerFinto:
    """Al posto di ollama.Server: risponde a /api/chat con la funzione 'traduci(testo, opzioni)'."""

    def __init__(self, traduci, errore=None):
        self.traduci = traduci
        self.errore = errore
        self.richieste = []
        self.chiuso = False
        self.scaricato = False  # modello tolto dalla scheda video (keep_alive 0)
        self.attivo = False

    def avvia(self):
        self.attivo = True

    def chiama(self, percorso, dati=None, timeout=180):
        if self.errore:
            raise self.errore
        if percorso == "/api/chat":
            testo = dati["messages"][0]["content"].split("\n\n\n", 1)[1]
            self.richieste.append((testo, dati))
            risposta = self.traduci(testo, dati["options"])
            if isinstance(risposta, BaseException):
                raise risposta
            if isinstance(risposta, tuple):
                contenuto, fine = risposta
            else:
                contenuto, fine = risposta, "stop"
            return {"message": {"content": contenuto}, "done_reason": fine, "eval_count": 20,
                    "eval_duration": 500_000_000, "prompt_eval_count": 60}
        if percorso == "/api/generate" and dati.get("keep_alive") == 0:
            self.scaricato = True
        if percorso == "/api/ps":
            return {"models": [{"name": "translategemma:4b-it-q4_K_M", "size": 100, "size_vram": 100}]}
        return {}

    def chiudi(self):
        self.chiuso = True


def errore_http(codice, corpo):
    import io
    import urllib.error
    return urllib.error.HTTPError("http://127.0.0.1", codice, "x", {}, io.BytesIO(corpo))


def locale(traduci, errore=None):
    server = ServerFinto(traduci, errore)
    b = backend.Locale("4b", log=silenzio, server=server)
    return b, server


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("La cartella di prova deve essere vuota: %s" % base)
    os.makedirs(base)
    progetto.CARTELLA_PROGETTO = os.path.join(base, "patchsmith")
    progetto.RADICE_DATI = progetto.CARTELLA_PROGETTO  # anche i dati dell'utente qui
    os.makedirs(os.path.join(progetto.CARTELLA_PROGETTO, "memory"))

    # --- 1. traduzione normale: una richiesta per riga, prompt ufficiale di TranslateGemma
    b, server = locale(lambda t, o: "IT " + t)
    esito = b.traduci_righe(["Start game", "Quit"], "en", "it")
    testo, dati = server.richieste[0]
    controlla("una richiesta per riga, prompt ufficiale (English (en) to Italian (it)), temperatura 0",
              esito == ["IT Start game", "IT Quit"] and len(server.richieste) == 2
              and "English (en) to Italian (it) translator" in dati["messages"][0]["content"]
              and dati["options"]["temperature"] == 0 and dati["options"]["num_ctx"] == 2048
              and dati["stream"] is False)

    # --- 2. controlli e secondo tentativo
    casi = {
        "testo di contorno": (lambda t, o: "Translation: Inizia" if o["temperature"] == 0 else "Inizia", "Inizia"),
        "scrittura sbagliata": (lambda t, o: "开始游戏", ""),
        "rifiuto del modello": (lambda t, o: "I cannot translate this.", ""),
        "ripetizioni": (lambda t, o: "gioco gioco gioco gioco gioco", ""),
        "a capo aggiunto": (lambda t, o: "Inizia\nla partita", "Inizia la partita"),
        "non tradotta": (lambda t, o: t, ""),
    }
    for nome, (traduci, atteso) in casi.items():
        b, server = locale(traduci)
        originale = {"numeri diversi": "Deal 50 damage", "non tradotta": "Start the game with your friends"}.get(
            nome, "Start the game")
        controlla("controllo '%s': %s" % (nome, "risposta usata (corretta o ritentata)" if atteso
                                           else "resta in originale"),
                  b.traduci_righe([originale], "en", "it") == [atteso])
    risposte = iter(["Infliggi 5 danni", "Infliggi 50 danni"])
    b, server = locale(lambda t, o: next(risposte))
    numeri = b.traduci_righe(["Deal 50 damage"], "en", "it")
    controlla("numeri cambiati dal modello: secondo tentativo con la correzione sui numeri nel prompt",
              numeri == ["Infliggi 50 danni"] and backend.CORREZIONE_NUMERI in server.richieste[1][1]["messages"][0]["content"]
              and backend.CORREZIONE_NUMERI not in server.richieste[0][1]["messages"][0]["content"])
    controlla("numeri scritti in lettere: '1 or more' -> 'uno o piu'' accettato; 'un', 'sei' (ambigui) no",
              not backend.problemi_traduzione("You have 1 or more mods", "Hai uno o più mod", "it")
              and backend.problemi_traduzione("lose 1 max H", "perdi un po' di salute", "it")
              and backend.problemi_traduzione("gain 6 slots", "sei più forte", "it"))
    b, server = locale(lambda t, o: ("Inizia la parti", "length") if o["num_predict"] < 100 else "Inizia la partita")
    esito = b.traduci_righe(["Start the game"], "en", "it")
    controlla("risposta troncata (done_reason 'length'): ritenta con piu' spazio",
              esito == ["Inizia la partita"] and server.richieste[-1][1]["options"]["num_predict"] >= 100)
    b, server = locale(lambda t, o: '"Inizia"')
    controlla("virgolette aggiunte dal modello tolte", b.traduci_righe(["Start"], "en", "it") == ["Inizia"])
    pulizie = [("building", "edificio; costruzione; costruire.", "edificio"),
               ("spellbind", "incantare, affascinare, rapire.", "incantare"),
               ("swords and stuff", "spade, scudi, ecc.", "spade, scudi, ecc."),
               ("antlers", "corna (di cervo, alce, ecc.)", "corna"),
               ("golden boots", "scarpe dorate.", "scarpe dorate"),
               ("awarded only to those", "conferito esclusivamente a coloro che...", "conferito esclusivamente a coloro che"),
               ("why?!", "Perché?!", "perché?!"), ("uh oh!", "Oh, no!", "oh, no!"), ("Start", "Inizia.", "Inizia"),
               ("loading...", "caricamento...", "caricamento..."), ("U.S. army", "esercito U.S.A.", "esercito U.S.A."),
               ("Press (A) to jump.", "Premi (A) per saltare.", "Premi (A) per saltare.")]
    b, server = locale(lambda t, o: "x")
    sbagliate = [(o, b._pulisci(t, o)) for o, t, atteso in pulizie if b._pulisci(t, o) != atteso]
    controlla("pulizia di quello che il modello aggiunge (alternative, note, punto, puntini, maiuscola) senza "
              "toccare il resto%s" % (": %s" % sbagliate if sbagliate else ""), not sbagliate
              and b._pulisci("Schwert", "sword", "de") == "Schwert")

    # --- 2b. niente falsi scarti: frasi normali, lingue asiatiche, numeri scritti all'uso locale, virgolette
    buone = [("Here, take this.", "Ecco, prendi questo.", "it"), ("Sorry, the shop is closed.",
             "Mi dispiace, il negozio è chiuso.", "it"), ("Can't carry more!", "Non posso portarne altri!", "it"),
             ("Notes", "Note", "it"), ("Start the game with your friends", "与朋友一起开始游戏", "zh"),
             ("暴击率", "Probabilità di colpo critico", "it"), ("Costs 1000 gold", "Costa 1.000 monete d'oro", "it"),
             ("Costs 1,000 gold", "Costa 1000 monete", "it"), ("Reach level 10", "レベル１０に到達", "ja"),
             ("Level 1.5", "Livello 1,5", "it")]
    scartate = [(o, backend.problemi_traduzione(o, t, d)) for o, t, d in buone if backend.problemi_traduzione(o, t, d)]
    controlla("controlli: nessun falso scarto (Ecco/Mi dispiace, cinese e giapponese, 1.000, cifre larghe)%s"
              % (": %s" % scartate if scartate else ""), not scartate)
    b, server = locale(lambda t, o: "x")
    tenute = [("\"Stay a while.\"", "«Resta un po'.»", "«Resta un po'.»"), ("你好朋友", "Ciao, amico", "Ciao, amico"),
              ("攻撃力（ATK）", "Attacco (ATK)", "Attacco (ATK)"), ("「こんにちは。」", "\"Ciao.\"", "\"Ciao.\"")]
    toccate = [(o, b._pulisci(t, o)) for o, t, atteso in tenute if b._pulisci(t, o) != atteso]
    controlla("pulizia: virgolette di altro stile e testi cinesi/giapponesi lasciati intatti%s"
              % (": %s" % toccate if toccate else ""), not toccate)

    # --- 2c. testo rimasto uguale: si riprova una volta; se resta uguale e' un nome e si tiene
    b, server = locale(lambda t, o: t if o["temperature"] == 0 else "Apri la porta")
    primo = b.traduci_righe(["Open the door"], "en", "it")
    b, server = locale(lambda t, o: t)
    nome = b.traduci_righe(["Katherine"], "en", "it")
    controlla("eco dell'originale: secondo tentativo; uguale due volte (un nome) si tiene",
              primo == ["Apri la porta"] and nome == ["Katherine"] and len(server.richieste) == 2)

    # --- 2d. una sola riga troppo lenta (scade il tempo) ma server vivo: solo quella resta in originale
    b, server = locale(lambda t, o: TimeoutError("timed out") if t == "Long dialogue" else "IT " + t)
    righe = b.traduci_righe(["Alpha", "Long dialogue", "Omega"], "en", "it")
    controlla("riga che va oltre il tempo massimo: resta in originale, le altre si traducono",
              righe == ["IT Alpha", "", "IT Omega"])
    b, server = locale(lambda t, o: "x", ConnectionResetError())
    try:
        b.traduci_righe(["Alpha"], "en", "it")
        caduto = False
    except backend.ErroreBackend:
        caduto = True
    controlla("server caduto: errore (la traduzione si ferma e riprende al prossimo avvio)", caduto)

    # --- 3. segnaposto persi: l'ultimo tentativo torna a PatchSmith, che ritraduce i pezzi tra i codici
    profilo = os.path.join(base, "profilo.json")
    json.dump({"nome": "prova", "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
               "file": []}, open(profilo, "w"))
    p = Progetto(profilo, silenzio)

    def perde(testo, opzioni):
        return testo.replace("[[0]]", "").replace("Hello", "Ciao").replace("welcome", "benvenuto").strip()
    b, server = locale(perde)
    p.traduci_testi(["Hello {name}, welcome"], b, "en", "it")
    controlla("segnaposto perso dal modello: la traduzione finale ha comunque {name}",
              p.memoria.get("Hello {name}, welcome") == "Ciao {name}, benvenuto" and server.scaricato)

    # --- 3a. contesto per riga: chiave (senza numero) e traduzioni ufficiali, riga per riga se le righe corrispondono
    b, server = locale(lambda t, o: "meteo" if t == "weather" else "IT " + t)
    p.memoria = {}
    contesti = {"weather": {"chiave": "configWeather", "riferimenti": {"fr": "météo", "es": "clima"}},
                "first line\nsecond line": {"chiave": "dialogue", "riferimenti": {"fr": "une seule ligne"}},
                "a\nb": {"chiave": "x", "riferimenti": {"fr": "riga a\nriga b"}}}
    p.traduci_testi(list(contesti), b, "en", "it", contesti)
    prompt = {t: d["messages"][0]["content"] for t, d in server.richieste}
    controlla("contesto nel prompt: chiave e traduzioni ufficiali; righe diverse -> nessun riferimento",
              'identifier "configWeather"' in prompt["weather"] and "French: météo; Spanish: clima" in prompt["weather"]
              and "French" not in prompt["first line"] and 'identifier "dialogue"' in prompt["first line"]
              and "French: riga a" in prompt["a"] and "French: riga b" in prompt["b"] and p.memoria["weather"] == "meteo")
    b, server = locale(lambda t, o: "configWeather")
    controlla("il modello scrive la chiave invece di tradurre -> scartato",
              b.traduci_righe(["weather"], "en", "it", [{"chiave": "configWeather"}]) == [""])
    prompt_visti = []

    class ServerPrompt(ServerFinto):
        def chiama(self, percorso, dati=None, timeout=180):
            if percorso == "/api/chat":
                prompt_visti.append(dati["messages"][0]["content"])
            return ServerFinto.chiama(self, percorso, dati, timeout)

    def secondo_prompt(risposte):
        server = ServerPrompt(lambda t, o: risposte[min(len(prompt_visti), len(risposte)) - 1])
        prompt_visti.clear()
        return backend.Locale("4b", log=silenzio, server=server)
    b = secondo_prompt(["saque", "saccheggio"])
    copia = b.traduci_righe(["looting"], "en", "it", [{"chiave": "skillName", "riferimenti": {"pt": "saque"}}])
    controlla("traduzione ufficiale copiata (es. dal portoghese): scartata, nuovo tentativo senza riferimenti",
              copia == ["saccheggio"] and "saque" in prompt_visti[0] and "saque" not in prompt_visti[1])
    b = secondo_prompt(["configWeather", "meteo"])
    chiave = b.traduci_righe(["weather"], "en", "it", [{"chiave": "configWeather"}])
    controlla("chiave copiata: nuovo tentativo senza la chiave nel prompt",
              chiave == ["meteo"] and "configWeather" in prompt_visti[0] and "configWeather" not in prompt_visti[1])
    b = secondo_prompt(["le monete valgono il doppio!", "coins are worth 200% more!", "valgono il triplo!"])
    controlla("testo rimasto uguale solo all'ultimo tentativo (dopo uno scarto): resta da rifare, non e' una traduzione",
              b.traduci_righe(["coins are worth 200% more!"], "en", "it") == [""])
    b = secondo_prompt(["[[0]]Compagno Sigillo", "[[0]]Compagno[[1]] Sigillo"])
    segni = b.traduci_righe(["[[0]]Companion[[1]] Seal"], "en", "it")
    controlla("codici persi dal modello: secondo tentativo con l'istruzione sui codici",
              segni == ["[[0]]Compagno[[1]] Sigillo"] and backend.CORREZIONE_SEGNAPOSTO in prompt_visti[1])
    b, server = locale(lambda t, o: "[[0]] Attacchi [[1]] ti danno [[2]\u3015evitare [[3]\u3015 danni")
    controlla("codici rovinati dal modello ('[[2]\u3015', '[0]'): rimessi; quelli scritti nell'originale no",
              b.traduci_righe(["[[0]]Backstabs[[1]] grant [[2]]Evade[[3]] damage"], "en", "it")
              == ["[[0]] Attacchi [[1]] ti danno [[2]]evitare [[3]] danni"]
              and b._pulisci("Premi [1] per saltare [[0]]", "Press [1] to jump [[0]]") == "Premi [1] per saltare [[0]]")
    controlla("numeri: 'una' per 1 se l'originale non ha un articolo che la spieghi; cifra che traduce 'one'",
              not backend.problemi_traduzione("appear 1 rarity higher", "appaiono con una rarità superiore", "it")
              and backend.problemi_traduzione("deals 1 damage to a target", "infligge danni a una creatura", "it")
              and not backend.problemi_traduzione("restore one [[7]] on hit", "ripristina 1 [[7]] al colpo", "it"))
    controlla("lettere che non sono dell'italiano (es. 'crânio' dal portoghese): scartata",
              "lettere di un'altra lingua" in backend.problemi_traduzione("great skull", "crânio grande", "it")
              and not backend.problemi_traduzione("so is it", "così è", "it"))

    # --- 3b. Ferma durante il ripiego: le traduzioni buone dello stesso gruppo restano in memoria
    def perde_e_ferma(testo, opzioni):
        if "[[0]]" not in testo and testo in ("Hello", ", welcome"):  # siamo nel ripiego
            interruzione.richiedi()
        return perde(testo, opzioni) if "[[0]]" in testo else "IT " + testo
    b, server = locale(perde_e_ferma)
    p.memoria = {}
    if os.path.exists(progetto.percorso_comune("en", "it")):  # testi brevi gia' visti nei casi prima
        os.remove(progetto.percorso_comune("en", "it"))
    try:
        p.traduci_testi(["Start game", "Quit", "Hello {name}, welcome"], b, "en", "it")
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    controlla("Ferma durante il ripiego dei segnaposto: le altre traduzioni del gruppo sono salvate",
              fermato and p.memoria.get("Start game") == "IT Start game" and p.memoria.get("Quit") == "IT Quit")

    # --- 3c. righe in parallelo: stesso ordine delle righe; con Ferma le righe gia' fatte restano
    b, server = locale(lambda t, o: "IT " + t)
    b.parallele = 3
    righe = ["riga %d" % i for i in range(20)]
    controlla("righe tradotte 3 alla volta: risultato nello stesso ordine", b.traduci_righe(righe, "en", "it")
              == ["IT " + r for r in righe])

    def ferma_al_quinto(testo, opzioni):
        if testo == "riga 5":
            interruzione.richiedi()
        return "IT " + testo
    b, server = locale(ferma_al_quinto)
    b.parallele = 3
    try:
        b.traduci_righe(righe, "en", "it")
        parziali = None
    except interruzione.Interrotto as e:
        parziali = e.parziali
    interruzione.azzera()
    controlla("Ferma con le righe in parallelo: si ferma, le righe gia' tradotte restano al loro posto",
              parziali is not None and len(parziali) == 20 and parziali[0] == "IT riga 0" and parziali[-1] == ""
              and all(p in ("", "IT " + r) for p, r in zip(parziali, righe)))

    # --- 3d. memoria comune: i testi brevi tradotti per un gioco servono agli altri; le correzioni a mano anche
    def profilo_gioco(nome):
        percorso = os.path.join(base, nome + ".json")
        json.dump({"nome": nome, "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
                   "file": []}, open(percorso, "w"))
        return Progetto(percorso, silenzio)
    primo = profilo_gioco("gioco-uno")
    b, server = locale(lambda t, o: "IT " + t)
    primo.traduci_testi(["options", "back", "a long sentence that only this game has"], b, "en", "it")
    secondo = profilo_gioco("gioco-due")
    b, server = locale(lambda t, o: "IT " + t)
    secondo.traduci_testi(["options", "back", "new game"], b, "en", "it")
    controlla("memoria comune: i testi brevi gia' tradotti per un altro gioco non si ritraducono (le frasi si')",
              [t for t, _ in server.richieste] == ["new game"] and secondo.memoria["options"] == "IT options")
    primo.memoria["options"] = "opzioni"  # come con 'Correggi traduzioni'
    primo._salva_memoria()
    primo = profilo_gioco("gioco-uno")
    b, server = locale(lambda t, o: "IT " + t)
    primo.traduci_testi(["options"], b, "en", "it")
    terzo = profilo_gioco("gioco-tre")
    b, server = locale(lambda t, o: "IT " + t)
    terzo.traduci_testi(["options"], b, "en", "it")
    controlla("correzione a mano di un testo breve: passa nella memoria comune e la usano i giochi successivi",
              terzo.memoria["options"] == "opzioni" and not server.richieste)

    # --- 4. errori comprensibili
    for nome, errore, parola in (("modello mancante (404)", errore_http(404, b'{"error":"model not found"}'),
                                  "non e' scaricato"),
                                 ("memoria insufficiente", errore_http(500, b'{"error":"requires more system memory"}'),
                                  "Memoria insufficiente")):
        b, server = locale(lambda t, o: "x", errore)
        try:
            b.traduci_righe(["Start"], "en", "it")
            messaggio = ""
        except backend.ErroreBackend as e:
            messaggio = str(e)
        controlla("errore '%s' spiegato" % nome, parola in messaggio)

    # --- 5. Ferma a meta': righe gia' tradotte salvate, server chiuso
    conta = [0]

    def ferma_al_terzo(testo, opzioni):
        conta[0] += 1
        if conta[0] == 3:
            interruzione.richiedi()
        return "IT " + testo
    b, server = locale(ferma_al_terzo)
    p.memoria = {}
    try:
        p.traduci_testi(["uno", "due", "tre", "quattro", "cinque"], b, "en", "it")
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    controlla("Ferma a meta': si ferma, le righe gia' tradotte restano, la scheda video viene liberata",
              fermato and set(p.memoria) == {"uno", "due", "tre"} and server.scaricato)

    # --- 5b. server avviato dal traduttore stesso: chiudi() lo spegne
    b, server = locale(lambda t, o: "IT " + t)
    b._proprio = True
    b.traduci_righe(["Start"], "en", "it")
    b.chiudi()
    controlla("chiudi(): con il server avviato da PatchSmith, modello scaricato e server spento",
              server.scaricato and server.chiuso and b._server is None)

    # --- 6. stima dalla velocita' misurata
    b, server = locale(lambda t, o: "IT " + t)
    prima = b.stima([40] * 100)  # ritmo delle prove (0,25 s a testo per il 4B) + 30 s di avvio
    b.traduci_righe(["Start"] * 12, "en", "it")  # server finto: istantaneo
    controlla("stima: prima delle prove circa un minuto per 100 testi col 4B, poi il ritmo misurato "
              "su questo PC", 45 <= prima <= 65 and b.stima([40] * 100) < 5)
    controlla("prompt: formato ufficiale di TranslateGemma con il contesto 'videogioco, del tu'",
              "video game" in server.richieste[0][1]["messages"][0]["content"]
              and server.richieste[0][1]["messages"][0]["content"].endswith("\n\n\nStart"))

    # --- 7. Ollama simulato: avvio, ambiente, chiusura, server che non parte
    os.environ["OLLAMA_ORIGINS"] = r"C:\non\usare"  # configurazione dell'utente: non deve passare al server
    ambiente = os.path.join(base, "ambiente.json")
    os.environ["PATCHSMITH_FINTO_AMBIENTE"] = ambiente
    comando = [sys.executable, "-I", os.path.join(QUI, "ollama_finto.py")]
    server = ollama.Server(silenzio, comando=comando, attesa_avvio=20)
    server.avvia()
    versione = server.chiama("/api/version")
    tradotto = backend.Locale("4b", log=silenzio, server=server).traduci_righe(["Start game"], "en", "it")
    env = json.load(open(ambiente, encoding="utf-8"))
    pid = server.processo.pid
    server.chiudi()
    import subprocess
    vivo = str(pid).encode() in subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True).stdout
    controlla("Ollama simulato: avvio, traduzione, chiusura del processo",
              versione.get("version") == "0.40.4" and tradotto == ["IT: Start game"] and not vivo)
    controlla("ambiente del server: modelli nella cartella di PatchSmith, porta propria, niente variabili dell'utente",
              env.get("OLLAMA_MODELS") == ollama.cartella_modelli() and env.get("OLLAMA_NO_CLOUD") == "1"
              and env.get("OLLAMA_HOST", "").startswith("127.0.0.1:114") and "OLLAMA_ORIGINS" not in env
              and r"C:\non\usare" not in env.values())
    del os.environ["OLLAMA_ORIGINS"]
    os.environ["PATCHSMITH_FINTO_MUORI"] = "1"
    try:
        ollama.Server(silenzio, comando=comando, attesa_avvio=20).avvia()
        messaggio = ""
    except ollama.ErroreLocale as e:
        messaggio = str(e)
    del os.environ["PATCHSMITH_FINTO_MUORI"]
    controlla("server che non parte: errore con le ultime righe del suo registro",
              "non si e' avviato" in messaggio and "errore simulato" in messaggio)

    # Ferma mentre il server si avvia: il processo appena lanciato non resta acceso
    import threading
    pid_avviati = []
    lega_vero = ollama._lega_al_job
    ollama._lega_al_job = lambda pid: pid_avviati.append(pid) or lega_vero(pid)
    os.environ["PATCHSMITH_FINTO_LENTO"] = "8"
    threading.Timer(1.0, interruzione.richiedi).start()
    try:
        with ollama.Server(silenzio, comando=comando, attesa_avvio=20):
            pass
        fermato = False
    except interruzione.Interrotto:
        fermato = True
    interruzione.azzera()
    del os.environ["PATCHSMITH_FINTO_LENTO"]
    ollama._lega_al_job = lega_vero
    vivo = bool(pid_avviati) and str(pid_avviati[0]).encode() in subprocess.run(
        ["tasklist", "/FI", "PID eq %d" % pid_avviati[0]], capture_output=True).stdout
    controlla("Ferma durante l'avvio del server: nessun server lasciato acceso", fermato and pid_avviati and not vivo)

    # una porta gia' usata da un altro programma (anche in ascolto su 0.0.0.0) non viene scelta
    import socket
    occupante = socket.socket()
    occupante.bind(("0.0.0.0", 0))
    occupante.listen(1)
    porta_altrui = occupante.getsockname()[1]
    occupata = ollama._porta_occupata(porta_altrui)
    occupante.close()
    controlla("porta occupata da un altro programma riconosciuta", occupata and not ollama._porta_occupata(porta_altrui))

    # --- 8. installazione: zip con percorsi pericolosi rifiutato, stato senza installazione
    pericoloso = os.path.join(base, "pericoloso.zip")
    with zipfile.ZipFile(pericoloso, "w") as z:
        z.writestr("../fuori.txt", "x")
    try:
        ollama._estrai(pericoloso, os.path.join(base, "estratto"))
        rifiutato = False
    except ollama.ErroreLocale:
        rifiutato = True
    controlla("installazione: zip con percorsi fuori cartella rifiutato, nulla estratto",
              rifiutato and not os.path.exists(os.path.join(base, "fuori.txt"))
              and not os.path.exists(os.path.join(base, "estratto")))
    # installazione rovinata (ollama.exe tolto, es. dall'antivirus): la reinstallazione la sostituisce
    buono = os.path.join(base, "buono.zip")
    with zipfile.ZipFile(buono, "w") as z:
        z.writestr("ollama.exe", "nuovo")
        z.writestr("lib/ollama/x.dll", "x")
    rovinata = os.path.join(base, "versione")
    os.makedirs(os.path.join(rovinata, "lib", "ollama"))
    open(os.path.join(rovinata, "lib", "ollama", "vecchio.dll"), "w").close()
    ollama._estrai(buono, rovinata)
    controlla("installazione rovinata: la cartella viene rifatta da zero",
              open(os.path.join(rovinata, "ollama.exe")).read() == "nuovo"
              and not os.path.exists(os.path.join(rovinata, "lib", "ollama", "vecchio.dll"))
              and not os.path.exists(rovinata + ".tmp"))

    # scaricamento di Ollama: Ferma o rete interrotta non buttano il pezzo gia' scaricato, si riprende con Range
    contenuto = bytes(range(256)) * 12  # 3072 byte al posto di 1,47 GB
    richieste = []

    class Risposta:
        def __init__(self, dati, stato, rompi_dopo=None):
            self.dati, self.status, self.rompi, self.letti = dati, stato, rompi_dopo, 0

        def read(self, n):
            if self.rompi is not None and self.letti >= self.rompi:
                raise ConnectionResetError("rete interrotta")
            blocco = self.dati[self.letti:self.letti + min(n, 1024)]
            self.letti += len(blocco)
            return blocco

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def urlopen_finto(req, timeout=None):
        intervallo = req.get_header("Range")
        richieste.append(intervallo)
        if intervallo:
            inizio = int(intervallo.split("=")[1].rstrip("-"))
            return Risposta(contenuto[inizio:], 206)
        return Risposta(contenuto, 200, rompi_dopo=1024)
    originali = (ollama.DIMENSIONE_ZIP, ollama.urllib.request.urlopen)
    ollama.DIMENSIONE_ZIP, ollama.urllib.request.urlopen = len(contenuto), urlopen_finto
    parziale = os.path.join(base, "ollama.zip.scaricamento")
    try:
        try:
            ollama._scarica_zip(parziale, silenzio)
        except ConnectionResetError:
            pass
        meta = os.path.getsize(parziale)
        ollama._scarica_zip(parziale, silenzio)
    finally:
        ollama.DIMENSIONE_ZIP, ollama.urllib.request.urlopen = originali
    controlla("scaricamento interrotto: il pezzo resta e si riprende da li' (Range), file finale identico",
              meta == 1024 and richieste == [None, "bytes=1024-"] and open(parziale, "rb").read() == contenuto)
    controlla("stato senza installazione: 'non installato' e descrizione dei download con fonte e dimensione",
              ollama.stato() == "non installato" and any("github.com" in r for r in ollama.descrizione_download(["4b"]))
              and any("Gemma Terms" in r for r in ollama.descrizione_download(["4b"])))
    try:
        backend.Locale("4b", log=silenzio).traduci_righe(["Start"], "en", "it")
        messaggio = ""
    except backend.ErroreBackend as e:
        messaggio = str(e)
    controlla("traduttore locale non installato: messaggio con cosa fare", "Installa" in messaggio)
    try:
        backend.crea_backend("locale", silenzio)
        messaggio, codice, comando = "", None, ""
    except backend.ErroreBackend as e:
        messaggio, codice, comando = str(e), e.codice, e.comando or ""
    controlla("crea_backend('locale') senza installazione: errore subito (prima di toccare il gioco), il messaggio "
              "parla del pulsante (niente comandi), il comando e' a parte per la riga di comando",
              "Installa" in messaggio and "smith.py" not in messaggio and codice == "TRADUTTORE_ASSENTE"
              and "smith.py locale --installa" in comando)
    # installato, ma il modello scelto (12B) non e' scaricato: si usa quello che c'e' (4B)
    os.makedirs(os.path.join(ollama.cartella(), ollama.VERSIONE))
    open(ollama.programma(), "w").close()
    manifesto = os.path.join(ollama.cartella_modelli(), "manifests", "registry.ollama.ai", "library", "translategemma")
    os.makedirs(manifesto)
    open(os.path.join(manifesto, ollama.MODELLI["4b"]["nome"].split(":")[1]), "w").close()
    json.dump({"modello_locale": "12b"}, open(os.path.join(progetto.CARTELLA_PROGETTO, "settings.json"), "w"))
    righe = []
    scelto = backend.crea_backend("locale", righe.append)
    controlla("modello scelto non scaricato: si usa quello scaricato, e lo si dice",
              scelto.chiave == "4b" and any("non e' scaricato" in r for r in righe))

    prova_recupero(base)
    prova_memoria(base)
    prova_applica(base)
    prova_rifatte_limitate(base)

    falliti = [n for n, ok in ESITI if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(ESITI) - len(falliti), len(ESITI)))
    return not falliti


def profilo_di(base, nome, **altro):
    """Un profilo di prova (gioco nella cartella base, inglese -> italiano), con i campi in piu' dati."""
    percorso = os.path.join(base, nome + ".json")
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(dict({"nome": nome, "cartella_gioco": base, "lingua_origine": "en", "lingua_destinazione": "it",
                        "file": []}, **altro), f)
    return percorso


def leggi_json(percorso):
    with open(percorso, encoding="utf-8") as f:
        return json.load(f)


def scrivi_json(percorso, dati):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "w", encoding="utf-8") as f:
        json.dump(dati, f, ensure_ascii=False)


class ServerConPrompt(ServerFinto):
    """Come ServerFinto, ma ricorda il prompt e il modello di ogni richiesta (la risposta puo' dipendere da loro)."""

    def __init__(self, traduci):
        ServerFinto.__init__(self, lambda t, o: traduci(t, o, self.prompt, self.modello))
        self.prompt, self.modello, self.modelli = "", None, []

    def chiama(self, percorso, dati=None, timeout=180):
        if percorso == "/api/chat":
            self.prompt, self.modello = dati["messages"][0]["content"], dati["model"]
            self.modelli.append(dati["model"])
        return ServerFinto.chiama(self, percorso, dati, timeout)


def prova_recupero(base):
    """Testi scartati dai controlli: nuovi giri subito (frasi separate, semi nuovi, l'altro modello), problemi.json e
    semi diversi a ogni avvio (prima si ripeteva la stessa domanda con 42, 7, 11 e la risposta era la stessa)."""
    from patchsmith import automatico, esito

    # 9a. il traduttore sbaglia con i semi del primo giro (42, 7, 11) e risponde bene con gli altri
    visti = []

    def semi_fissi(testo, opzioni):
        visti.append(opzioni["seed"])
        return testo if opzioni["seed"] in (42, 7, 11) else "IT " + testo
    p = Progetto(profilo_di(base, "recupero"), silenzio)
    b, server = locale(semi_fissi)
    falliti = p.traduci_testi(["Open the door to the castle"], b, "en", "it")
    controlla("scartato con i semi del primo giro (42, 7, 11): tradotto nello stesso avvio, con un seme nuovo",
              falliti == 0 and p.memoria.get("Open the door to the castle") == "IT Open the door to the castle"
              and visti[:3] == [42, 7, 11] and len(visti) == 4 and visti[3] not in (42, 7, 11) and not p.problemi())

    # 9b. un testo sempre scartato: problemi.json, non in memoria; il secondo avvio usa semi tutti nuovi
    testo = "Close the window of the house"
    p = Progetto(profilo_di(base, "sempre-scartato"), silenzio)
    b, server = locale(lambda t, o: t)
    falliti = p.traduci_testi([testo], b, "en", "it")
    semi_primo = [d["options"]["seed"] for _, d in server.richieste]
    problemi = p.problemi()
    controlla("testo sempre scartato: in problemi.json con il motivo e i passaggi fatti, non in memoria",
              falliti == 1 and testo not in p.memoria and os.path.exists(p.percorso_problemi)
              and problemi.get(testo, {}).get("dove") == "traduzione" and "non tradotta" in problemi[testo]["motivo"]
              and problemi[testo]["tentativi"] == 2 and p.ultimi_falliti == [testo])
    e = esito.Esito("PARZIALE", motore="web", scritto=True, tradotti=3, rimasti=1, esempi_rimasti=[testo],
                    extra=automatico._extra_tentativi(p))
    registro = []
    automatico._dopo_i_tentativi({"esito": e}, registro.append)
    finestra = esito.finestra(e)["testo"]
    controlla("messaggio finale: niente 'rilancia'; dice cosa ha gia' provato da solo e nomina 'Correggi traduzioni'",
              "Correggi traduzioni" in " ".join(registro) and "riprovato da solo" in finestra
              and "Mostra i rimasti" in finestra and "rilancia" not in (" ".join(registro) + finestra).lower())
    p = Progetto(profilo_di(base, "sempre-scartato"), silenzio)
    b, server = locale(lambda t, o: t)
    p.traduci_testi([testo], b, "en", "it")
    semi_secondo = [d["options"]["seed"] for _, d in server.richieste]
    controlla("secondo avvio: semi tutti diversi da quelli del primo (registro delle richieste), passaggi annotati",
              len(semi_secondo) == 6 and not set(semi_primo) & set(semi_secondo)
              and p.problemi()[testo]["tentativi"] == 4)
    controlla("semi: stessi per lo stesso testo e tentativo (anche in un altro avvio), mai 42, 7, 11",
              backend.seme_per(testo, 3) == backend.seme_per(testo, 3) != backend.seme_per(testo, 4)
              and all(backend.seme_per("x%d" % i, i) >= 100 for i in range(200)))

    # 9c. lo stesso con l'Ollama simulato vero (HTTP), che sbaglia con i semi 42, 7, 11 e registra le richieste
    registro_richieste = os.path.join(base, "richieste-ollama.jsonl")
    comando = [sys.executable, "-I", os.path.join(QUI, "ollama_finto.py")]
    os.environ["PATCHSMITH_FINTO_SEMI_SBAGLIATI"] = "42,7,11"
    os.environ["PATCHSMITH_FINTO_RICHIESTE"] = registro_richieste
    try:
        server = ollama.Server(silenzio, comando=comando, attesa_avvio=20)
        server.avvia()
        try:
            p = Progetto(profilo_di(base, "ollama-finto"), silenzio)
            p.traduci_testi(["Find the key in the old house"], backend.Locale("4b", log=silenzio, server=server),
                            "en", "it")
        finally:
            server.chiudi()
    finally:
        del os.environ["PATCHSMITH_FINTO_SEMI_SBAGLIATI"], os.environ["PATCHSMITH_FINTO_RICHIESTE"]
    with open(registro_richieste, encoding="utf-8") as f:
        semi = [json.loads(r)["seed"] for r in f]
    controlla("Ollama simulato che sbaglia con 42, 7, 11: tradotto nello stesso avvio con un seme nuovo (dal suo "
              "registro delle richieste)",
              p.memoria.get("Find the key in the old house") == "IT: Find the key in the old house"
              and semi[:3] == [42, 7, 11] and len(semi) == 4 and semi[3] not in (42, 7, 11))

    # 9d. scartato dal modello Veloce anche nei nuovi giri: il Qualita' (12B) NON si carica da solo, anche se c'e'
    dodici = ollama.MODELLI["12b"]["nome"]
    manifesto = os.path.join(ollama.cartella_modelli(), "manifests", "registry.ollama.ai", "library",
                             "translategemma", dodici.split(":")[1])
    os.makedirs(os.path.dirname(manifesto), exist_ok=True)
    open(manifesto, "w").close()
    try:
        server = ServerConPrompt(lambda t, o, prompt, modello: ("IT " + t) if modello == dodici else t)
        p = Progetto(profilo_di(base, "altro-modello"), silenzio)
        p.traduci_testi(["Talk to the guard at the gate"], backend.Locale("4b", log=silenzio, server=server),
                        "en", "it")
        controlla("scartato dal modello Veloce anche nei nuovi giri: resta il modello scelto (niente 12B caricato da "
                  "solo), il testo va tra i problemi",
                  "Talk to the guard at the gate" not in p.memoria and dodici not in server.modelli
                  and "Talk to the guard at the gate" in p.problemi())
    finally:
        os.remove(manifesto)


def prova_memoria(base):
    """Nomi del gioco protetti, memoria comune con la provenienza, traduzioni salvate rifatte se non vanno."""
    from patchsmith import nomi

    # 10a. nomi del gioco: app.info di Unity e informazioni di versione di un eseguibile
    gioco = os.path.join(base, "gioco-nomi")
    os.makedirs(os.path.join(gioco, "Gioco_Data"))
    open(os.path.join(gioco, "Gioco_Data", "globalgamemanagers"), "wb").write(b"x")
    with open(os.path.join(gioco, "Gioco_Data", "app.info"), "w", encoding="utf-8") as f:
        f.write("Cup Dog Games\nRubinite")
    trovati = nomi.nomi_del_gioco(gioco)
    info = nomi.versione_exe(sys.executable)
    controlla("nomi del gioco: azienda e prodotto da app.info; CompanyName dalle informazioni di un eseguibile",
              trovati[:2] == ["Cup Dog Games", "Rubinite"] and info.get("CompanyName") == "Python Software Foundation")
    controlla("nomi: anche senza la forma societaria ('LLC'); nomi generici e sigle corte scartati",
              nomi._varianti("Cup Dog Games LLC") == ["Cup Dog Games LLC", "Cup Dog Games"]
              and nomi._varianti("DefaultCompany") == [] and nomi._varianti("GM") == []
              # piu' forme di seguito; i generici anche con il punto finale e i programmi di servizio accanto al gioco
              and nomi._varianti("SQUARE ENIX CO., LTD.") == ["SQUARE ENIX CO., LTD", "SQUARE ENIX"]
              and nomi._varianti("Epic Games, Inc.") == [] and nomi._varianti("Microsoft Game Launch Helper") == [])
    comune = progetto.percorso_comune("en", "it")
    vecchia = leggi_json(comune) if os.path.exists(comune) else {}
    vecchia.update({"Cup Dog Games": "Giochi di cani da coppa", "Deal 5 damage": "Infliggi 50 danni",
                    "Load game": "Carica partita"})  # voci di una versione precedente, senza provenienza
    scrivi_json(comune, vecchia)
    b, server = locale(lambda t, o: t.replace("Cup Dog Games", "Giochi di cani da coppa").replace("Made by",
                                                                                                   "Fatto da"))
    p = Progetto(profilo_di(base, "rubinite", cartella_gioco=gioco, non_tradurre=trovati), silenzio)
    p.traduci_testi(["Cup Dog Games", "Made by Cup Dog Games"], b, "en", "it")
    chiesti = [t for t, _ in server.richieste]
    controlla("app.info con 'Cup Dog Games': la traduzione e' uguale all'originale (nessuna richiesta; la memoria "
              "comune vecchia 'Giochi di cani da coppa' non vale), il nome resta anche dentro le frasi",
              p.memoria.get("Cup Dog Games") == "Cup Dog Games" and chiesti == ["Made by [[0]]"]
              and p.memoria.get("Made by Cup Dog Games") == "Fatto da Cup Dog Games")
    controlla("nome di una sola parola che nei testi c'e' anche in minuscolo ('Control'): e' una parola, si traduce",
              Progetto(profilo_di(base, "parola", non_tradurre=["Control"]), silenzio).nomi_attivi(
                  ["Control", "Hold control to run"]) == ()
              and Progetto(profilo_di(base, "nome", non_tradurre=["Rubinite"]), silenzio).nomi_attivi(
                  ["Welcome to Rubinite"]) == ("Rubinite",))

    # 10b. memoria comune: entrano solo i testi tradotti adesso, con la provenienza; le voci vecchie senza
    # provenienza che non passano i controlli si saltano (e si sostituiscono)
    percorso = profilo_di(base, "comune-a")
    scrivi_json(progetto.percorso_memoria({"nome": "comune-a", "lingua_destinazione": "it"}),
                {"Save game": "Salva partita"})  # voce vecchia del gioco
    p = Progetto(percorso, silenzio)
    b, server = locale(lambda t, o: {"Deal 5 damage": "Infliggi 5 danni", "New quest": "Nuova missione"}.get(
        t, "IT " + t))
    p.traduci_testi(["Deal 5 damage", "Load game", "New quest", "Save game"], b, "en", "it")
    dopo = leggi_json(comune)
    origine = leggi_json(progetto.percorso_origine_comune("en", "it"))
    controlla("memoria comune: voce vecchia con i numeri sbagliati saltata e sostituita (con la provenienza: "
              "traduttore, modello, versione); voce vecchia buona usata",
              [t for t, _ in server.richieste] == ["Deal 5 damage", "New quest"]
              and p.memoria["Load game"] == "Carica partita" and dopo["Deal 5 damage"] == "Infliggi 5 danni"
              and origine["Deal 5 damage"]["backend"] == "locale" and "4b" in origine["Deal 5 damage"]["modello"]
              and origine["Deal 5 damage"]["versione"])
    controlla("memoria comune: le voci vecchie della memoria del gioco non ci entrano (solo quelle tradotte adesso)",
              "Save game" not in dopo and dopo.get("New quest") == "Nuova missione")
    p.memoria["New quest"] = "Missione nuova"  # come con 'Correggi traduzioni'
    p._salva_memoria()
    p = Progetto(percorso, silenzio)
    b, server = locale(lambda t, o: "IT " + t)
    p.traduci_testi(["New quest"], b, "en", "it")
    dopo, origine = leggi_json(comune), leggi_json(progetto.percorso_origine_comune("en", "it"))
    controlla("correzione a mano: va nella memoria comune (provenienza 'a mano'); in .automatiche.json resta la "
              "traduzione automatica, cosi' la correzione si riconosce sempre",
              dopo["New quest"] == "Missione nuova" and origine["New quest"].get("mano")
              and leggi_json(p._percorso_automatiche())["New quest"] == "Nuova missione"
              and p.memoria["New quest"] == "Missione nuova" and not server.richieste)

    # 10c. traduzioni salvate rifatte: solo quelle automatiche che non passano i controlli di adesso
    nome = "rivalida"
    percorso = profilo_di(base, nome)
    salvate = {"Collect 5 coins": "Raccogli 50 monete", "Find 3 gems": "Trova 30 gemme",
               "Open the chest of the house": "Abre el cofre de la casa y el mapa"}
    scrivi_json(progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"}), salvate)
    scrivi_json(progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})[:-5] + ".automatiche.json",
                dict(salvate, **{"Find 3 gems": "Trova 3 gemme"}))  # 'Trova 30 gemme' e' una correzione a mano
    p = Progetto(percorso, silenzio)
    b, server = locale(lambda t, o: {"Collect 5 coins": "Raccogli 5 monete"}.get(t, t))
    p.traduci_testi(list(salvate), b, "en", "it")
    chiesti = {t for t, _ in server.richieste}
    problema = p.problemi().get("Open the chest of the house", {})
    controlla("traduzione automatica con i numeri sbagliati: rifatta; correzione a mano: non toccata",
              p.memoria["Collect 5 coins"] == "Raccogli 5 monete" and "Find 3 gems" not in chiesti
              and p.memoria["Find 3 gems"] == "Trova 30 gemme" and p.rifatte == 1)
    controlla("traduzione automatica in un'altra lingua: se rifarla non riesce resta quella di prima, annotata",
              p.memoria["Open the chest of the house"] == "Abre el cofre de la casa y el mapa"
              and problema.get("dove") == "memoria" and "in un'altra lingua" in problema.get("motivo", ""))
    p._problemi["Open the chest of the house"] = dict(problema, tentativi=progetto.MAX_TENTATIVI)
    p._salva_problemi()
    p = Progetto(percorso, silenzio)
    b, server = locale(lambda t, o: t)
    p.traduci_testi(list(salvate), b, "en", "it")
    controlla("dopo %d passaggi una traduzione sospetta si lascia com'e' (non si ritraduce a ogni avvio)"
              % progetto.MAX_TENTATIVI, not server.richieste)
    from patchsmith import lingua
    controlla("controlli: 'a capo diversi' (righe in piu' o in meno), 'a capo aggiunto' per un testo di una riga",
              "a capo diversi" in backend.problemi_traduzione("NEW\n\nText", "NUOVO\n\n\nTesto")
              and "a capo aggiunto" in backend.problemi_traduzione("Start", "Inizia\nora")
              and not backend.problemi_traduzione("A\nB c", "A\nB d"))
    controlla("lingua: niente falsi allarmi su 'Qui, qui!' o su titoli inglesi tra virgolette e tag",
              not progetto.problemi_voce("Over here, over here!", "Qui, qui!", "it")
              and not progetto.problemi_voce('Unlock the "Lambs to the Slaughter" Accolade',
                                             'Sblocca l\'insegna "Lambs to the Slaughter"', "it")
              and not progetto.problemi_voce('Use <sprite="Crystal Shard" name="Crystal Shard"> to gain power',
                                             'Usa <sprite="Crystal Shard" name="Crystal Shard"> per avere potere',
                                             "it")
              and lingua.lingua_evidente("Open the door of the castle", "it") == "en")


def prova_applica(base):
    """Traduzioni che non si possono scrivere nel gioco: in problemi.json; quelle automatiche si rifanno subito con
    l'istruzione giusta, quelle corrette a mano restano."""
    from patchsmith import automatico
    gioco = os.path.join(base, "gioco-righe")
    os.makedirs(gioco)
    with open(os.path.join(gioco, "english.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("0 Start a brand new game now\n1 Quit\n2 Options\n")  # frase lunga: non e' nella memoria comune
    nome = "righe"
    percorso = profilo_di(base, nome, cartella_gioco=gioco, motore="gamemaker",
                          file=[{"percorso": "english.txt", "formato": "righe"}])
    memoria = progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})
    salvate = {"Start a brand new game now": "Inizia → partita", "Quit": "Esci ★", "Options": "Opzioni"}
    scrivi_json(memoria, salvate)
    scrivi_json(memoria[:-5] + ".automatiche.json", dict(salvate, Quit="Esci"))  # 'Esci ★' corretta a mano
    server = ServerConPrompt(lambda t, o, prompt, modello: "Inizia la partita" if "do not use them: →" in prompt
                             else "Inizia → partita")
    p = Progetto(percorso, silenzio)
    p.traduci(backend.Locale("4b", log=silenzio, server=server))
    with open(os.path.join(gioco, "english.txt"), encoding="utf-8") as f:
        scritto = f.read()
    problemi = p.problemi()
    controlla("traduzione automatica con un carattere che il font non ha: rifatta subito con l'istruzione di non "
              "usarlo, e scritta nel gioco",
              "0 Inizia la partita" in scritto and p.memoria["Start a brand new game now"] == "Inizia la partita"
              and "Start a brand new game now" not in problemi and len(server.richieste) == 1)
    controlla("correzione a mano che il gioco non sa mostrare: resta in memoria e in originale nel gioco, annotata "
              "(motivo e caratteri) e detta nell'esito",
              "1 Quit" in scritto and p.memoria["Quit"] == "Esci ★"
              and problemi.get("Quit", {}).get("dove") == "gioco"
              and problemi["Quit"].get("automatica") is False and "★" in problemi["Quit"]["motivo"]
              and automatico._extra_tentativi(p).get("corrette_a_mano_scartate") == 1)
    # 'Riapplica' (senza rete): una traduzione automatica non adatta si annota e si rifa' alla prossima traduzione
    p.memoria["Options"] = "Opzioni ★"
    p._salva_memoria()
    automatiche = leggi_json(p._percorso_automatiche())
    automatiche["Options"] = "Opzioni ★"
    scrivi_json(p._percorso_automatiche(), automatiche)
    p = Progetto(percorso, silenzio)
    p.applica()
    controlla("riapplica: traduzione automatica non adatta annotata con i caratteri da non usare; si rifara' alla "
              "prossima traduzione",
              p.problemi().get("Options", {}).get("automatica") is True
              and p.problemi()["Options"].get("non_usare") == "★"
              and "Options" in p._da_rifare(["Options", "Quit"], "it") and "Quit" not in p._da_rifare(["Quit"], "it"))


def gioco_righe(base, nome, righe):
    """Un gioco con un file a righe ('0 testo') e il suo profilo; restituisce (cartella, profilo, memoria)."""
    gioco = os.path.join(base, "gioco-" + nome)
    os.makedirs(gioco)
    with open(os.path.join(gioco, "english.txt"), "w", encoding="utf-8", newline="") as f:
        f.write("".join("%d %s\n" % (i, r) for i, r in enumerate(righe)))
    percorso = profilo_di(base, nome, cartella_gioco=gioco, file=[{"percorso": "english.txt", "formato": "righe"}])
    return gioco, percorso, progetto.percorso_memoria({"nome": nome, "lingua_destinazione": "it"})


def prova_rifatte_limitate(base):
    """Traduzioni salvate rifatte: una rifatta che non va ancora conta come tentativo (prima si rifaceva a ogni avvio,
    sempre con i semi 42, 7, 11, e scrivendo il gioco usciva da problemi.json), i nuovi giri non fermano la scrittura
    nel gioco, la memoria comune non prende traduzioni che non passano i controlli."""
    sospetta = {"Open the chest of the house": "Abre el cofre de la casa y el mapa"}

    # 11a. il traduttore locale la ridice uguale (passa i suoi controlli, non quello della lingua): semi nuovi a ogni
    # avvio, annotata in problemi.json, e dopo MAX_TENTATIVI si lascia
    percorso = profilo_di(base, "rifatta-uguale")
    memoria = progetto.percorso_memoria({"nome": "rifatta-uguale", "lingua_destinazione": "it"})
    scrivi_json(memoria, sospetta)
    scrivi_json(memoria[:-5] + ".automatiche.json", sospetta)
    semi = []
    for _ in range(progetto.MAX_TENTATIVI + 2):
        p = Progetto(percorso, silenzio)
        b, server = locale(lambda t, o: "Abre el cofre de la casa y el mapa")
        p.traduci_testi(list(sospetta), b, "en", "it")
        semi.append([d["options"]["seed"] for _, d in server.richieste])
    tutti = [s for giro in semi for s in giro]
    problema = p.problemi().get("Open the chest of the house", {})
    controlla("traduzione salvata rifatta uguale (non passa i controlli di adesso): semi nuovi a ogni avvio (mai 42, "
              "7, 11), annotata come sospetta, lasciata dopo %d tentativi" % progetto.MAX_TENTATIVI,
              tutti and not set(tutti) & {42, 7, 11} and len(set(tutti)) == len(tutti) and not semi[-1]
              and problema.get("dove") == "memoria" and problema.get("tentativi", 0) >= progetto.MAX_TENTATIVI
              and p.rifatte == 0)

    # 11b. lo stesso con 'Analizza e traduci' completo (scrive nel gioco): la nota in problemi.json resta, i tentativi
    # crescono e dopo MAX_TENTATIVI non si chiede piu' nulla
    gioco, percorso, memoria = gioco_righe(base, "rifatta-gioco", ["Open the chest of the house", "Quit"])
    scrivi_json(memoria, dict(sospetta, Quit="Esci"))
    scrivi_json(memoria[:-5] + ".automatiche.json", dict(sospetta, Quit="Esci"))
    richieste, tentativi = [], []
    for _ in range(5):
        p = Progetto(percorso, silenzio)
        b, server = locale(lambda t, o: t)  # rifarla non riesce mai
        p.traduci(b)
        richieste.append(len(server.richieste))
        tentativi.append(p.problemi().get("Open the chest of the house", {}).get("tentativi"))
    with open(os.path.join(gioco, "english.txt"), encoding="utf-8") as f:
        scritto = f.read()
    controlla("con la scrittura nel gioco: la traduzione sospetta resta annotata (i tentativi crescono) e dopo %d "
              "tentativi non si rifa' piu' (prima si rifaceva a ogni avvio)" % progetto.MAX_TENTATIVI,
              richieste[0] > 0 and richieste[-1] == 0 and tentativi == sorted(tentativi) and tentativi[0]
              and tentativi[-1] >= progetto.MAX_TENTATIVI and "0 Abre el cofre de la casa y el mapa" in scritto)

    # 11c. un errore imprevisto del traduttore nei nuovi giri: le traduzioni del primo giro si scrivono comunque
    gioco, percorso, memoria = gioco_righe(base, "giro-rotto", ["Light the lamp in the tower", "Close the gate of the house"])

    def rotto(testo, opzioni):
        if opzioni["seed"] not in (42, 7, 11):
            return RuntimeError("guasto imprevisto")
        return testo if testo == "Close the gate of the house" else "IT " + testo
    p = Progetto(percorso, silenzio)
    b, server = locale(rotto)
    completato = p.traduci(b)
    with open(os.path.join(gioco, "english.txt"), encoding="utf-8") as f:
        scritto = f.read()
    controlla("errore imprevisto nei nuovi giri: la traduzione non si ferma, il resto e' scritto nel gioco e il testo "
              "rimasto va in problemi.json",
              completato is False and "0 IT Light the lamp in the tower" in scritto and "1 Close the gate of the house" in scritto
              and p.problemi().get("Close the gate of the house", {}).get("dove") == "traduzione")

    # 11d. memoria comune: un testo breve tradotto adesso che non passa i controlli di adesso non ci entra
    comune = progetto.percorso_comune("en", "it")
    p = Progetto(profilo_di(base, "comune-spagnolo"), silenzio)
    b, server = locale(lambda t, o: {"Open the house chest": "Abre el cofre y el mapa",
                                     "Light the torch": "Accendi la torcia"}.get(t, t))
    p.traduci_testi(["Open the house chest", "Light the torch"], b, "en", "it")
    dopo = leggi_json(comune)
    controlla("memoria comune: una traduzione nuova in un'altra lingua resta nel gioco ma non va agli altri giochi",
              p.memoria.get("Open the house chest") == "Abre el cofre y el mapa" and "Open the house chest" not in dopo
              and dopo.get("Light the torch") == "Accendi la torcia")

    # 11f. un testo che e' solo un nome del gioco, tradotto in automatico: torna il nome, senza richieste, e poi non
    # si ricontrolla a ogni avvio ('non tradotta' non vale per un nome lasciato com'e' apposta)
    nome = "Cult Of The Lamb"
    percorso = profilo_di(base, "solo-nome", non_tradurre=[nome])
    memoria = progetto.percorso_memoria({"nome": "solo-nome", "lingua_destinazione": "it"})
    scrivi_json(memoria, {nome: "Culto dell'Agnello"})
    scrivi_json(memoria[:-5] + ".automatiche.json", {nome: "Culto dell'Agnello"})
    richieste = []
    for _ in range(2):
        p = Progetto(percorso, silenzio)
        b, server = locale(lambda t, o: "IT " + t)
        p.traduci_testi([nome], b, "en", "it")
        richieste.append(len(server.richieste))
    controlla("testo che e' solo un nome del gioco, tradotto: torna il nome senza richieste, e non si rifa' a ogni avvio",
              p.memoria[nome] == nome and richieste == [0, 0] and not p.problemi() and not p._da_rifare([nome], "it"))

    # 11e. traduzione ufficiale copiata: solo per le frasi (una parola uguale in due lingue e' normale)
    controlla("'riferimento copiato' solo per le frasi: 'Oro' per 'Gold' (come in spagnolo) va bene",
              not progetto.problemi_voce("Gold", "Oro", "it", riferimenti=["Oro"])
              and progetto.problemi_voce("Open the big chest now", "Abre el gran cofre ahora", "it",
                                         riferimenti=["Abre el gran cofre ahora"]) == ["riferimento copiato"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
