"""Limitatore delle richieste ai traduttori online, con memoria su disco dei blocchi.

Google non pubblica i limiti del traduttore gratuito (e oggi rifiuta del tutto alcuni accessi automatici): i
valori qui sotto sono margini prudenti scelti a priori, non limiti misurati. La regola che conta di piu':
al primo blocco ci si ferma subito e si ricorda fino a quando non riprovare, perche' insistere durante un
blocco puo' allungarlo.

Lo stato sta in services.json tra i dati dell'utente (progetto.cartella_dati: di norma accanto al programma;
scrittura atomica, con un lucchetto cosi' interfaccia, riga di comando e traduttore avviato dai giochi Unity
(servizio_xunity) contano le stesse richieste e rispettano le stesse pause):
{"versione": 1, "google": {"richieste": [[ora, caratteri], ...], "ultimo_ok": ora, "ok_totali": n,
                           "blocco": {"dal", "fino", "livello", "tipo", "http", "disattivato"}}}
"""
import datetime
import json
import os
import random
import time

from . import interruzione

MINUTO, ORA, GIORNO = 60, 3600, 86400

# per servizio: pausa minima tra l'inizio di due richieste (+ variazione casuale) e finestre
# (durata in secondi, richieste al massimo, caratteri al massimo)
REGOLE = {
    "google": {"pausa": 4.0, "variazione": 2.0,
               "finestre": ((MINUTO, 8, 32_000), (10 * MINUTO, 30, 120_000), (ORA, 60, 240_000),
                            (GIORNO, 300, 1_000_000))},
    "deepl": {"pausa": 0.5, "variazione": 0.0, "finestre": ()},
    "mymemory": {"pausa": 1.0, "variazione": 0.5, "finestre": ((MINUTO, 50, None),)},
}
# attese dopo un blocco: "a freddo" = rifiutata gia' la prima richiesta (non e' un limite superato, l'accesso e'
# rifiutato: inutile riprovare presto); "dopo traffico" = dopo richieste andate a buon fine (un limite di ritmo)
ATTESE = {"a_freddo": (GIORNO, 3 * GIORNO, 7 * GIORNO), "dopo_traffico": (ORA, 6 * ORA, GIORNO, 3 * GIORNO)}
MASSIMO_FUTURO = 8 * GIORNO  # un "fino" piu' lontano (orologio sbagliato, file rovinato) viene ridotto
DIMENTICA_DOPO = 7 * GIORNO  # una settimana senza blocchi: si riparte dal primo livello
OK_PER_AZZERARE = 10
NOMI = {"google": "Google", "deepl": "DeepL", "mymemory": "MyMemory"}
CONSIGLIO_LOCALE = ("Per continuare subito usa il traduttore locale (consigliato: nessun limite e nessun blocco) o un "
                    "altro traduttore.")


class InPausa(Exception):
    """Il servizio e' in pausa per un blocco precedente: nessuna richiesta finche' non passa."""


def percorso_stato():
    from . import progetto
    return progetto.cartella_dati("services.json")


def quando(istante):
    """'alle 18:30 di domani', 'alle 18:30 del 22/10': per i messaggi."""
    momento = datetime.datetime.fromtimestamp(istante)
    oggi = datetime.date.today()
    if momento.date() == oggi:
        return "alle %s" % momento.strftime("%H:%M")
    if momento.date() == oggi + datetime.timedelta(days=1):
        return "alle %s di domani" % momento.strftime("%H:%M")
    return "alle %s del %s" % (momento.strftime("%H:%M"), momento.strftime("%d/%m"))


class _Lucchetto:
    """Lucchetto su file (os.O_EXCL): uno abbandonato da piu' di 60 s si considera libero."""

    def __init__(self, percorso):
        self.percorso = percorso + ".lock"
        self.preso = False

    def __enter__(self):
        limite = time.monotonic() + 5
        while True:
            try:
                fd = os.open(self.percorso, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.preso = True
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.percorso) > 60:
                        os.remove(self.percorso)
                        continue
                except OSError:
                    pass
                if time.monotonic() > limite:
                    return self  # meglio procedere senza lucchetto che bloccarsi
                time.sleep(0.05)
            except OSError:
                return self  # cartella non scrivibile: si lavora senza lucchetto

    def __exit__(self, *_):
        if self.preso:
            try:
                os.remove(self.percorso)
            except OSError:
                pass


class Limitatore:
    def __init__(self, servizio, log=print, percorso=None, ora=time.time, dormi=None, caso=random.random):
        self._corretto = False
        self.servizio = servizio
        self.regole = REGOLE.get(servizio, {"pausa": 1.0, "variazione": 0.0, "finestre": ()})
        self.log = log
        self.percorso = percorso or percorso_stato()
        self.ora = ora
        self.dormi = dormi or interruzione.attendi
        self.caso = caso
        self.ok_sessione = 0
        self._in_memoria = None  # stato tenuto in memoria se il file non si puo' scrivere
        self._avvisato = False

    # --- file di stato ----------------------------------------------------------------------------

    def _leggi(self):
        if self._in_memoria is not None:
            return self._in_memoria
        try:
            with open(self.percorso, encoding="utf-8") as f:
                dati = json.load(f)
            return dati if isinstance(dati, dict) else {"versione": 1}
        except FileNotFoundError:
            return {"versione": 1}
        except (OSError, ValueError):
            self._avviso("  ! Stato dei traduttori illeggibile (%s): riparto da zero." % self.percorso)
            return {"versione": 1}

    def _scrivi(self, dati):
        if self._in_memoria is not None:
            self._in_memoria = dati
            return
        testo = json.dumps(dati, ensure_ascii=False, indent=1).encode("utf-8")
        temporaneo = self.percorso + ".tmp"
        for tentativo in range(5):
            try:
                os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
                with open(temporaneo, "wb") as f:
                    f.write(testo)
                os.replace(temporaneo, self.percorso)
                return
            except OSError:
                time.sleep(0.1)  # antivirus o indicizzatore che tiene il file: si riprova
        self._avviso("  ! Non riesco a salvare lo stato dei traduttori (%s): lo tengo solo in memoria." % self.percorso)
        self._in_memoria = dati

    def _avviso(self, testo):
        if not self._avvisato and self.log:
            self.log(testo)
        self._avvisato = True

    def _mio(self, dati, adesso):
        s = dati.setdefault(self.servizio, {})
        if not isinstance(s, dict):
            s = dati[self.servizio] = {}
        s["richieste"] = [r for r in s.get("richieste", []) if isinstance(r, list) and len(r) == 2
                          and 0 <= adesso - r[0] < GIORNO]
        blocco = s.get("blocco")
        if isinstance(blocco, dict):
            if blocco.get("dal", adesso) > adesso or (blocco.get("fino") and blocco["fino"] - adesso > MASSIMO_FUTURO):
                # registrato con l'orologio avanti (o file rovinato): si riparte da adesso, una volta sola
                durata = max(0, blocco.get("fino", adesso) - blocco.get("dal", adesso))
                blocco["dal"] = adesso
                blocco["fino"] = adesso + min(durata, MASSIMO_FUTURO)
                self._corretto = True
            if not blocco.get("disattivato") and adesso - blocco.get("fino", adesso) > DIMENTICA_DOPO:
                s.pop("blocco")  # una settimana senza blocchi dopo la fine dell'ultimo: si riparte da capo
        else:
            s.pop("blocco", None)
        return s

    # --- domande ----------------------------------------------------------------------------------

    def _stato_corretto(self):
        """Lo stato del servizio; se c'erano date da correggere le salva subito (cosi' la correzione vale una volta)."""
        self._corretto = False
        dati = self._leggi()
        adesso = self.ora()
        s = self._mio(dati, adesso)
        if self._corretto:
            with _Lucchetto(self.percorso):
                self._scrivi(dati)
        return s, adesso

    def blocco_attuale(self):
        s, adesso = self._stato_corretto()
        blocco = s.get("blocco")
        return blocco if blocco and (blocco.get("disattivato") or adesso < blocco.get("fino", 0)) else None

    def pausa(self):
        """None se il servizio si puo' usare, altrimenti il messaggio con fino a quando e' in pausa."""
        s, adesso = self._stato_corretto()
        blocco = s.get("blocco")
        if not blocco:
            return None
        if blocco.get("disattivato"):
            return ("%s e' disattivato dopo ripetuti rifiuti (ultimo %s): usa il traduttore locale (nessun limite e "
                    "nessun blocco) o un altro traduttore, oppure riattivalo con il pulsante 'Riattiva' accanto al "
                    "traduttore."
                    % (NOMI.get(self.servizio, self.servizio), quando(blocco.get("dal", adesso))))
        if adesso < blocco.get("fino", 0) and blocco.get("tipo") == "quota":
            return "%s: %s, si riprende %s." % (NOMI.get(self.servizio, self.servizio),
                                                blocco.get("dettaglio") or "quota finita", quando(blocco["fino"]))
        if adesso < blocco.get("fino", 0):
            return ("%s e' in pausa fino %s (blocco ricevuto %s): PatchSmith non manda richieste prima, per non "
                    "allungarlo." % (NOMI.get(self.servizio, self.servizio), quando(blocco["fino"]),
                                     quando(blocco.get("dal", adesso))))
        return None

    def stato_breve(self):
        """'in pausa fino alle 05:58 di domani', 'disattivato' oppure '' (per l'interfaccia)."""
        try:
            s, adesso = self._stato_corretto()
            blocco = s.get("blocco")
        except (OSError, ValueError, TypeError, AttributeError):
            return ""
        if not blocco:
            return ""
        if blocco.get("disattivato"):
            return "disattivato dopo ripetuti rifiuti"
        if adesso < blocco.get("fino", 0):
            return "in pausa fino %s" % quando(blocco["fino"])
        return ""

    def _attesa(self, s, caratteri, adesso):
        richieste = s["richieste"]
        attesa = 0.0
        if richieste:
            attesa = richieste[-1][0] + self.regole["pausa"] + s.get("variazione", 0.0) - adesso
        for durata, massimo_n, massimo_c in self.regole["finestre"]:
            dentro = [r for r in richieste if adesso - r[0] < durata]
            while dentro and (len(dentro) + 1 > massimo_n or
                              (massimo_c is not None and sum(c for _, c in dentro) + caratteri > massimo_c)):
                attesa = max(attesa, dentro.pop(0)[0] + durata - adesso)
        return max(0.0, attesa)

    # --- azioni -----------------------------------------------------------------------------------

    def prima_di(self, caratteri):
        """Aspetta il tempo necessario e registra la richiesta (prima di mandarla). Solleva InPausa se il
        servizio e' in pausa per un blocco."""
        while True:
            messaggio = self.pausa()
            if messaggio:
                raise InPausa(messaggio)
            with _Lucchetto(self.percorso):
                dati = self._leggi()
                adesso = self.ora()
                s = self._mio(dati, adesso)
                attesa = self._attesa(s, caratteri, adesso)
                if attesa <= 0:
                    s["richieste"].append([adesso, caratteri])
                    s["variazione"] = round(self.caso() * self.regole["variazione"], 3)
                    self._scrivi(dati)
                    return
            if attesa > 15 and self.log:
                self.log("    Pausa di %s per restare nei margini prudenti di %s: riprendo %s. Puoi fermarti: il "
                         "lavoro fatto resta salvato." % (_durata(attesa), NOMI.get(self.servizio, self.servizio),
                                                          quando(self.ora() + attesa)))
            self.dormi(attesa)

    def ok(self):
        with _Lucchetto(self.percorso):
            dati = self._leggi()
            adesso = self.ora()
            s = self._mio(dati, adesso)
            s["ultimo_ok"] = adesso
            s["ok_totali"] = s.get("ok_totali", 0) + 1
            if s["ok_totali"] >= OK_PER_AZZERARE and s.get("blocco") and adesso >= s["blocco"].get("fino", 0):
                s.pop("blocco")  # tornato affidabile: i prossimi blocchi ripartono dal primo livello
            self.ok_sessione += 1
            self._scrivi(dati)

    def bloccato(self, http=None, dettaglio=None, adesso=None, fino=None, extra=None):
        """Registra un blocco e restituisce il messaggio per l'utente. 'fino': pausa fino a quell'ora precisa (es.
        quota giornaliera finita), senza livelli."""
        with _Lucchetto(self.percorso):
            dati = self._leggi()
            adesso = self.ora() if adesso is None else adesso
            s = self._mio(dati, adesso)
            if fino is not None:
                s["blocco"] = dict({"dal": adesso, "fino": min(fino, adesso + MASSIMO_FUTURO), "livello": 0,
                                    "tipo": "quota", "http": http, "dettaglio": dettaglio, "disattivato": False},
                                   **(extra or {}))
                self._scrivi(dati)
                return "%s in pausa fino %s (%s)." % (NOMI.get(self.servizio, self.servizio), quando(fino), dettaglio)
            recente = s.get("ultimo_ok") and adesso - s["ultimo_ok"] < GIORNO
            riuscite_24h = s.get("ok_totali", 0)
            tipo = "dopo_traffico" if (self.ok_sessione or recente) else "a_freddo"
            precedente = s.get("blocco") or {}
            livello = precedente.get("livello", 0) + 1 if precedente.get("tipo") == tipo else 1
            attese = ATTESE[tipo]
            disattivato = tipo == "a_freddo" and livello > len(attese)
            fino = adesso + attese[min(livello, len(attese)) - 1]
            s["blocco"] = {"dal": adesso, "fino": fino, "livello": livello, "tipo": tipo, "http": http,
                           "dettaglio": dettaglio, "disattivato": disattivato}
            s["ok_totali"] = 0
            self._scrivi(dati)
        nome = NOMI.get(self.servizio, self.servizio)
        if disattivato:
            return ("%s ha rifiutato di nuovo le richieste: lo disattivo finche' non lo riattivi tu (pulsante "
                    "'Riattiva' accanto al traduttore). %s" % (nome, CONSIGLIO_LOCALE))
        if tipo == "a_freddo":
            return ("%s ha rifiutato la prima richiesta: non e' un limite superato, %s non accetta richieste "
                    "automatiche da questo programma (succede a molti). Riprovare subito non serve e potrebbe "
                    "allungare il blocco: PatchSmith non riprovera' prima %s. Le traduzioni fatte restano salvate. "
                    "Per continuare subito usa il traduttore locale (consigliato: nessun limite e nessun blocco), "
                    "oppure DeepL (gratis con una tua chiave, 500.000 caratteri al mese) o MyMemory (senza account)."
                    % (nome, nome, quando(fino)))
        dopo = ("dopo %d richieste riuscite" % self.ok_sessione if self.ok_sessione else
                "dopo %d richieste riuscite nelle ultime ore" % riuscite_24h if riuscite_24h else
                "dopo le richieste riuscite delle ultime ore")
        return ("%s ha messo in pausa le richieste %s: PatchSmith riprendera' %s, non prima. Il lavoro fatto e' "
                "salvato. %s" % (nome, dopo, quando(fino), CONSIGLIO_LOCALE))

    def azzera(self):
        with _Lucchetto(self.percorso):
            dati = self._leggi()
            s = self._mio(dati, self.ora())
            s.pop("blocco", None)
            s["ok_totali"] = 0
            self._scrivi(dati)

    def stima(self, caratteri_per_richiesta, durata_risposta=1.5):
        """Secondi stimati per mandare queste richieste rispettando le regole (a partire dallo stato attuale)."""
        dati = self._leggi()
        orologio = [self.ora()]
        s = self._mio(json.loads(json.dumps(dati)), orologio[0])
        s["variazione"] = self.regole["variazione"] / 2
        inizio = orologio[0]
        for caratteri in caratteri_per_richiesta:
            orologio[0] += self._attesa(s, caratteri, orologio[0])
            s["richieste"].append([orologio[0], caratteri])
            orologio[0] += durata_risposta
        return orologio[0] - inizio


def _durata(secondi):
    if secondi < 90:
        return "%d secondi" % round(secondi)
    if secondi < 90 * 60:
        return "%d minuti" % round(secondi / 60)
    return "%.1f ore" % (secondi / 3600)


def descrivi_durata(secondi):
    return _durata(secondi)
