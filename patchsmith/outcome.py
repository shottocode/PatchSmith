"""Esito di un'operazione (Analizza e traduci, Riapplica, Ripristina) in una forma che l'interfaccia sa leggere:
un codice, i conteggi, il motivo in una frase e le azioni possibili dopo. Da qui anche il testo delle finestre che
l'interfaccia mostra alla fine, una per codice, con i pulsanti per il passo successivo.

Le funzioni di automatico restituiscono ancora i valori di prima (la riga di comando e le prove li usano): l'esito
si trova in esito["esito"], nel dizionario del riconoscimento del motore (o in quello passato a riapplica/ripristina).
"""
from dataclasses import asdict, dataclass, field

CODICI = ("OK", "PARZIALE", "NULLA_DA_FARE", "UFFICIALE", "NON_SUPPORTATO", "ANTICHEAT", "GIOCO_APERTO",
          "TRADUTTORE_BLOCCATO", "TRADUTTORE_ASSENTE", "GIA_ORIGINALE", "PERMESSI", "CARTELLA_SBAGLIATA", "ERRORE")
# azioni proposte dopo un esito (ognuna diventa un pulsante, tranne scegli_lingua che e' un'istruzione)
AZIONI = ("chiudi_gioco", "usa_locale", "installa_locale", "mostra_rimasti", "crea_rapporto", "riapplica",
          "scegli_lingua", "traduci_comunque", "sperimentale", "ripristina", "traduci", "ritraduci", "correggi")
MAX_ESEMPI = 20  # testi rimasti in originale da mostrare (i primi)
DAL_VIVO = {"locale": "PatchSmith, con il traduttore locale (sul tuo PC)",
            "online": "PatchSmith, con il traduttore online (con le pause che evitano i blocchi)",
            "google": "XUnity, con Google gratuito (via internet)",
            "nessuno": "nessuno, restano in originale"}
# perche' le frasi nuove non le traduce PatchSmith (Esito.extra["dal_vivo_nota"], vedi automatico._dal_vivo)
NOTE_DAL_VIVO = {
    "senza_traduttore": "Adesso non c'è un traduttore disponibile: il traduttore locale non è installato e quelli "
                        "online sono in pausa. Installa il traduttore locale: dalla prossima volta che avvii il "
                        "gioco tradurrà le frasi nuove.",
    "plugin_google": "Non sono riuscito a preparare nel gioco il traduttore di PatchSmith: le frasi nuove le chiede "
                     "XUnity a Google, senza le pause di PatchSmith.",
    "plugin_nessuno": "Non sono riuscito a preparare nel gioco il traduttore di PatchSmith e Google è in pausa: le "
                      "frasi nuove resteranno in originale. Puoi creare un rapporto da inviare a chi ti aiuta con "
                      "PatchSmith.",
}


@dataclass
class Esito:
    """codice: uno di CODICI. motore: unity, unity-localization, unreal, xna, gamemaker, web... (None se non si sa).
    scritto: in questa operazione e' stata scritta nel gioco almeno una traduzione (per Unity: BepInEx e XUnity
    installati, che traducono anche mentre si gioca). tradotti/rimasti: testi tradotti e rimasti in
    originale; esempi_rimasti: i primi originali rimasti. dal_vivo (Unity): chi traduce le frasi nuove mentre si
    gioca, 'locale', 'google', 'nessuno' (None: non c'e' traduzione dal vivo). motivo: una frase per l'utente.
    azioni: da AZIONI. extra: dati per le domande dell'interfaccia (lingua ufficiale, lingua aggiunta...) e cosa ha
    gia' rifatto PatchSmith da solo (ritentati, rifatte, cache_corrette, corrette_a_mano_scartate, adattate_font,
    accorciate, troppo_lunghe: vedi automatico._extra_tentativi)."""
    codice: str
    motivo: str = ""
    motore: str = None
    scritto: bool = False
    tradotti: int = 0
    rimasti: int = 0
    esempi_rimasti: list = field(default_factory=list)
    dal_vivo: str = None
    azioni: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.codice not in CODICI:
            raise ValueError("Codice di esito sconosciuto: %s" % self.codice)
        sconosciute = [a for a in self.azioni if a not in AZIONI]
        if sconosciute:
            raise ValueError("Azioni sconosciute: %s" % ", ".join(sconosciute))
        self.esempi_rimasti = [str(t) for t in list(dict.fromkeys(self.esempi_rimasti))[:MAX_ESEMPI]]
        self.azioni = list(dict.fromkeys(self.azioni))
        if self.rimasti and self.esempi_rimasti and "mostra_rimasti" not in self.azioni:
            self.azioni.append("mostra_rimasti")

    def come_dizionario(self):
        return asdict(self)

    def riassunto(self):
        """Una riga per il registro su disco."""
        parti = ["Esito: %s" % self.codice]
        if self.motore:
            parti.append("motore %s" % self.motore)
        if self.scritto or self.tradotti or self.rimasti:
            parti.append("scritto nel gioco: %s, tradotti %d, rimasti %d" % ("si'" if self.scritto else "no",
                                                                             self.tradotti, self.rimasti))
        if self.dal_vivo:
            parti.append("frasi nuove: %s" % self.dal_vivo)
        if self.motivo:
            parti.append(self.motivo)
        return " - ".join(parti)


def imposta(esito, codice, motivo="", **campi):
    """Registra l'esito in esito["esito"] (il dizionario del riconoscimento, o quello dato dall'interfaccia) e
    restituisce il dizionario. Con esito None non fa nulla."""
    if esito is not None:
        esito["esito"] = Esito(codice, motivo, **campi)
    return esito


# --- eccezioni ---------------------------------------------------------------------------------------

def da_eccezione(errore):
    """Esito per un errore arrivato fino all'interfaccia. Gli errori di PatchSmith hanno un messaggio per l'utente
    (e a volte un codice: errore.codice); gli altri sono imprevisti: si dice il tipo e si propone un rapporto."""
    from .backend import ErroreBackend
    from .system import ErroreSistema
    codice = getattr(errore, "codice", None)
    messaggio = str(errore).strip()
    if codice in CODICI:
        return Esito(codice, messaggio, azioni=_azioni_per(codice))
    if isinstance(errore, ErroreBackend):
        return Esito("TRADUTTORE_BLOCCATO", messaggio, azioni=["usa_locale"])
    if isinstance(errore, ErroreSistema):
        return Esito("GIOCO_APERTO", messaggio, azioni=["chiudi_gioco"])
    if isinstance(errore, PermissionError):
        return Esito("PERMESSI", "Windows non mi lascia modificare %s (%s)." % (
            errore.filename or "un file del gioco", errore.strerror or "accesso negato"),
            azioni=["chiudi_gioco", "crea_rapporto"])
    if _errore_noto(errore):
        return Esito("ERRORE", messaggio or type(errore).__name__, azioni=["crea_rapporto"])
    return Esito("ERRORE", messaggio, azioni=["crea_rapporto"],
                 extra={"imprevisto": True, "tipo": type(errore).__name__})


def _azioni_per(codice):
    return {"GIOCO_APERTO": ["chiudi_gioco"], "TRADUTTORE_ASSENTE": ["installa_locale"],
            "TRADUTTORE_BLOCCATO": ["usa_locale"], "NON_SUPPORTATO": ["crea_rapporto"],
            "PERMESSI": ["chiudi_gioco", "crea_rapporto"]}.get(codice, [])


def _errore_noto(errore):
    """True per gli errori con un messaggio scritto per l'utente (gli stessi che la riga di comando mostra come
    'ERRORE: ...')."""
    from . import adapter_unity, adapter_unreal, adapter_xna, components as componenti, dotnet, ollama, xnb
    noti = (adapter_unity.ErroreUnity, adapter_unreal.ErroreUnreal, adapter_xna.ErroreXna, componenti.ErroreComponente,
            dotnet.ErroreDotnet, xnb.ErroreXnb, ollama.ErroreLocale, RuntimeError, ValueError, OSError)
    return isinstance(errore, noti) and type(errore) not in (UnicodeDecodeError, NotImplementedError, RecursionError)


# --- finestre dell'interfaccia --------------------------------------------------------------------

COME_VEDERLA = {
    "unity": "La traduzione è installata nel gioco (BepInEx + XUnity.AutoTranslator).\n\nAvvia il gioco come al "
             "solito: le frasi pre-tradotte compaiono subito in italiano; le altre la prima volta compaiono in "
             "originale per un attimo, poi tradotte. Nel gioco: ALT+T alterna originale e traduzione.",
    "unity-localization": "Le tabelle dei testi del gioco sono tradotte (gli originali sono salvati e si rimettono "
                          "con \"Ripristina originali\").\n\nNel gioco lascia selezionata la lingua English: i suoi "
                          "testi ora sono tradotti.",
    "gamemaker": "I file di testo del gioco sono tradotti (gli originali sono salvati e si rimettono con "
                 "\"Ripristina originali\").\n\nNel gioco lascia selezionata la lingua originale, di solito English.",
    "xna": "La traduzione è installata come file di lingua aggiuntivi (i file originali non sono stati toccati).\n\n"
           "Nel gioco lascia o seleziona la lingua originale, di solito English: la traduzione prende il suo posto.",
    "unreal": "La traduzione è installata come pak di patch aggiuntivo (i file originali non sono stati toccati).\n\n"
              "Nel gioco deve essere selezionata la lingua originale, di solito English: la traduzione prende il "
              "suo posto.",
    None: "I testi del gioco sono tradotti (gli originali sono salvati e si rimettono con \"Ripristina "
          "originali\").\n\nAvvia il gioco come al solito.",
}

TITOLI = {
    "OK": "Traduzione installata", "PARZIALE": "Traduzione installata in parte", "NULLA_DA_FARE": "Niente da fare",
    "UFFICIALE": "Il gioco ha già la tua lingua", "NON_SUPPORTATO": "Gioco non ancora supportato",
    "ANTICHEAT": "Gioco con anti-cheat", "GIOCO_APERTO": "Il gioco è aperto",
    "TRADUTTORE_BLOCCATO": "Traduzione interrotta", "TRADUTTORE_ASSENTE": "Traduttore non pronto",
    "GIA_ORIGINALE": "Gioco già originale", "PERMESSI": "File bloccati", "CARTELLA_SBAGLIATA": "Cartella da cambiare",
    "ERRORE": "Operazione non riuscita",
}
TITOLI_OPERAZIONE = {("riapplica", "OK"): "Traduzione riapplicata", ("ritraduci", "OK"): "Traduzioni rifatte",
                     ("ripristina", "OK"): "Originali ripristinati",
                     ("ripristina", "PARZIALE"): "Ripristino quasi completo",
                     ("controlla", "OK"): "Controllo dopo aver giocato", ("controlla", "PARZIALE"): "Qualcosa da fare",
                     ("controlla", "NULLA_DA_FARE"): "Niente da controllare"}
STATO = {
    "OK": "completato.", "PARZIALE": "completato in parte.", "NULLA_DA_FARE": "niente da fare.",
    "UFFICIALE": "il gioco ha già la lingua ufficiale.", "NON_SUPPORTATO": "gioco non ancora supportato.",
    "ANTICHEAT": "gioco con anti-cheat, nessuna modifica.", "GIOCO_APERTO": "il gioco è aperto.",
    "TRADUTTORE_BLOCCATO": "traduttore fermo.", "TRADUTTORE_ASSENTE": "traduttore non pronto.",
    "GIA_ORIGINALE": "il gioco è già originale.", "PERMESSI": "file bloccati.",
    "CARTELLA_SBAGLIATA": "cartella da cambiare.", "ERRORE": "non riuscito.",
}
TIPI = {"OK": "info", "PARZIALE": "avviso", "NULLA_DA_FARE": "info", "UFFICIALE": "info", "GIA_ORIGINALE": "info",
        "ERRORE": "errore"}
PULSANTI = {"mostra_rimasti": "Mostra i rimasti", "crea_rapporto": "Crea rapporto", "riapplica": "Riapplica",
            "usa_locale": "Usa il traduttore locale", "installa_locale": "Installa il traduttore locale",
            "traduci_comunque": "Traduci comunque", "chiudi_gioco": "Riprova",
            "sperimentale": "Prova in modo sperimentale", "ripristina": "Ripristina originali",
            "traduci": "Analizza e traduci", "ritraduci": "Ritraduci le frasi", "correggi": "Correggi traduzioni"}
NOMI_OPERAZIONE = {"traduci": "Analizza e traduci", "riapplica": "Riapplica",
                   "ripristina": "Ripristina originali", "controlla": "Controllo dopo aver giocato",
                   "ritraduci": "Ritraduci"}
# operazioni dopo le quali, con testi rimasti in originale o traduzioni che il gioco non sa mostrare, si propone
# "Correggi traduzioni" (la finestra con l'elenco giusto gia' scelto)
CON_CORREZIONI = ("traduci", "riapplica", "ritraduci")
# stato dei problemi del controllo dopo aver giocato (controllo.Problema.stato) -> gruppo nella finestra
GRUPPI_CONTROLLO = (("corretto", "Corretto da solo:"),
                    ("da_fare", "Da fare:"), ("rimandato", "Da correggere quando il gioco è chiuso:"),
                    ("info", "Da sapere:"))
STATI_GRUPPO = {"corretto": "corretto", "non_riuscito": "da_fare", "gia_tentato": "da_fare", "da_fare": "da_fare",
                "rimandato": "rimandato", "da_correggere": "rimandato", "info": "info"}
# operazioni che non toccano i file del gioco (installazione del traduttore locale, rapporto, comandi della
# finestra): niente "Riprova" (ripeterebbe l'ultima operazione sul gioco) ne' consigli sul gioco aperto
FUORI_DAL_GIOCO = ("installa", "rapporto", "interfaccia")


def titolo(e, operazione="traduci"):
    if operazione == "rapporto":
        return "Rapporto non creato"
    if e.extra.get("imprevisto"):
        return "Errore imprevisto"
    if e.codice == "OK" and not e.scritto and operazione == "traduci":
        return "Fatto"
    return TITOLI_OPERAZIONE.get((operazione, e.codice)) or TITOLI[e.codice]


def _come_vederla(e):
    aggiunta = e.extra.get("lingua_aggiunta")
    if aggiunta:
        return ("La traduzione è una lingua nuova del gioco: \"%s\". L'originale resta com'era e si può ancora "
                "scegliere.\n\n%s\n\n\"Ripristina originali\" toglie la lingua aggiunta." % (
                    aggiunta["nome"], "L'ho già impostata nelle opzioni del gioco: avvialo e basta."
                    if aggiunta.get("impostata") else
                    "Nel gioco apri Opzioni > Lingua e scegli \"%s\" con le frecce." % aggiunta["nome"].upper()))
    return COME_VEDERLA.get(e.motore, COME_VEDERLA[None])


def _conteggi(e):
    if not (e.tradotti or e.rimasti):
        return ""
    if not e.rimasti:
        return "Testi tradotti: %d." % e.tradotti
    return "Testi tradotti: %d, rimasti in originale: %d." % (e.tradotti, e.rimasti)


def finestra(e, operazione="traduci", locale_pronto=False, attuale=None):
    """La finestra di fine operazione: {"titolo", "tipo" (info/avviso/errore/domanda), "testo",
    "pulsanti": [(etichetta, azione)], "stato" (riga sotto il registro)}. Il primo pulsante e' "OK" (azione None).
    locale_pronto: il traduttore locale e' installato (allora 'usa_locale' lo sceglie, se no lo installa);
    attuale: il traduttore scelto (se e' gia' quello locale, 'usa_locale' non serve)."""
    op = NOMI_OPERAZIONE.get(operazione, operazione)
    altro = "usa_locale" in e.azioni and not (attuale == "locale" and locale_pronto)  # proporre un altro traduttore
    righe = []
    if operazione == "rapporto":  # niente registro su disco ne' rapporto da proporre: si dice solo il motivo
        righe.append("Non sono riuscito a creare il rapporto: %s" % (
            "errore imprevisto (%s): %s" % (e.extra.get("tipo", "?"), e.motivo) if e.extra.get("imprevisto")
            else e.motivo or "motivo sconosciuto"))
    elif e.extra.get("imprevisto"):
        righe.append("Errore imprevisto (%s): %s" % (e.extra.get("tipo", "?"), e.motivo or "nessun dettaglio"))
        righe.append("I dettagli tecnici sono nel registro salvato. Puoi creare un rapporto da inviare a chi ti "
                     "aiuta con PatchSmith.")
    elif operazione == "controlla" and "controllo" in e.extra:
        righe += testo_controllo(e)
    elif operazione in FUORI_DAL_GIOCO:
        righe += [e.motivo, "Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith."]
    elif operazione == "ripristina":
        righe += _testo_ripristino(e)
    elif e.scritto and e.codice in ("OK", "PARZIALE", "TRADUTTORE_BLOCCATO", "GIOCO_APERTO"):
        righe += _testo_scritto(e, op, altro)
    elif e.codice == "OK":
        righe += [e.motivo or "Fatto.", _conteggi(e)] + _testo_tentativi(e)
    else:
        righe += _testo_problema(e, op, altro)
    if e.extra.get("ritraduzione") and operazione == "ritraduci":
        righe.insert(0, _testo_ritraduzione(e.extra["ritraduzione"]))
    pulsanti = [("OK", None)]
    for azione in e.azioni:
        if azione in ("usa_locale", "installa_locale"):
            if azione == "usa_locale" and attuale == "locale" and locale_pronto:
                continue  # e' gia' quello scelto
            # installato e non scelto: si usa (installarlo non farebbe nulla); altrimenti si installa
            azione = "usa_locale" if locale_pronto and attuale != "locale" else "installa_locale"
        if operazione in FUORI_DAL_GIOCO and azione == "chiudi_gioco":
            continue
        if operazione == "rapporto" and azione == "crea_rapporto":
            continue
        if azione in PULSANTI and azione not in [a for _, a in pulsanti]:
            if azione == "mostra_rimasti" and not e.esempi_rimasti:
                continue
            pulsanti.append((PULSANTI[azione], azione))
    if da_correggere(e, operazione) and "correggi" not in [a for _, a in pulsanti]:
        pulsanti.append((PULSANTI["correggi"], "correggi"))
    tipo = TIPI.get(e.codice, "avviso")
    if e.extra.get("imprevisto") or operazione == "rapporto":
        tipo = "errore"
    return {"titolo": titolo(e, operazione), "tipo": tipo, "testo": "\n\n".join(r for r in righe if r),
            "pulsanti": pulsanti, "stato": STATO[e.codice]}


def da_correggere(e, operazione="traduci"):
    """True se dopo questo esito ha senso proporre "Correggi traduzioni": dopo Analizza e traduci, Riapplica o
    Ritraduci, con testi rimasti in originale, traduzioni che il gioco non sa mostrare o testi gia' riprovati senza
    riuscirci (non per gli errori imprevisti)."""
    if operazione not in CON_CORREZIONI or e.extra.get("imprevisto") or \
            e.codice not in ("OK", "PARZIALE", "TRADUTTORE_BLOCCATO", "GIOCO_APERTO"):
        return False
    return bool(e.rimasti or e.extra.get("corrette_a_mano_scartate") or e.extra.get("ritentati")
                or (e.extra.get("ritraduzione") or {}).get("rimaste") or e.extra.get("troppo_lunghe"))


def filtro_correzioni(e):
    """L'elenco da mostrare in "Correggi traduzioni" aperta da questo esito (vedi correzioni.FILTRI)."""
    if e is not None and (e.extra.get("corrette_a_mano_scartate") or (
            e.extra.get("scartate") and e.rimasti <= e.extra["scartate"] and not e.extra.get("ritentati"))):
        return "gioco"  # (rimaste fuori dal gioco solo traduzioni che il gioco non saprebbe mostrare)
    if e is not None and (e.rimasti or e.extra.get("ritentati")):
        return "rimasti"
    if e is not None and e.extra.get("troppo_lunghe"):
        return "lunghi"
    return None


def _testo_ritraduzione(r):
    testo = "Ho ritradotto %d testi su %d." % (r.get("ritradotte", 0), r.get("testi", 0))
    if r.get("rimaste"):
        testo += (" Per gli altri %d resta la traduzione di prima (la nuova non ha passato i controlli, o era uguale)."
                  % r["rimaste"])
    if r.get("interrotta"):
        testo += " La ritraduzione si è fermata: %s" % r["interrotta"]
    return testo


def _testo_scritto(e, op, altro=True):
    """Qualcosa e' nel gioco: come vederla, i conteggi, chi traduce le frasi nuove e cosa fare per il resto."""
    righe = []
    if e.codice == "GIOCO_APERTO":
        righe.append(e.motivo or "Il gioco è aperto.")
        righe.append("Chiudi il gioco e premi \"Riapplica\": le traduzioni sono già pronte, non serve tradurle di "
                     "nuovo.")
        return righe
    if e.codice == "TRADUTTORE_BLOCCATO":
        righe.append(e.motivo)
    righe.append(_come_vederla(e))
    righe.append(_conteggi(e))
    if e.dal_vivo:
        righe.append("Frasi nuove mentre giochi: %s." % DAL_VIVO.get(e.dal_vivo, e.dal_vivo))
        righe.append(NOTE_DAL_VIVO.get(e.extra.get("dal_vivo_nota"), ""))
    if e.codice == "PARZIALE" and e.motivo:
        righe.append(e.motivo)
    righe += _testo_tentativi(e)
    if e.rimasti and e.codice in ("PARZIALE", "TRADUTTORE_BLOCCATO"):
        if altro:
            righe.append("Per i testi rimasti puoi usare un altro traduttore (il traduttore locale non ha blocchi) e "
                         "premere di nuovo \"Analizza e traduci\": le traduzioni fatte restano.")
        elif filtro_correzioni(e) == "gioco":
            righe.append("Le traduzioni che il gioco non saprebbe mostrare puoi correggerle tu con \"Correggi "
                         "traduzioni\" (elenco \"Non scritte nel gioco\"): quando salvi, PatchSmith le scrive nel gioco "
                         "da solo.")
        else:
            righe.append("I testi rimasti in originale puoi tradurli tu con \"Correggi traduzioni\" (elenco "
                         "\"Rimasti in originale\"%s): quando salvi, PatchSmith li scrive nel gioco da solo." % (
                             "; quelli che il gioco non saprebbe mostrare sono in \"Non scritte nel gioco\""
                             if e.extra.get("scartate") else ""))
    return righe


def _testo_tentativi(e):
    """Cosa ha gia' fatto PatchSmith da solo per i testi rimasti (Esito.extra, vedi automatico._extra_tentativi): al
    posto del vecchio "rilancia", che ripeteva le stesse domande e dava le stesse risposte."""
    righe = []
    if e.extra.get("ritentati"):
        n = e.extra["ritentati"]
        # frasi intere, al singolare e al plurale (a pezzi non si traducono nella lingua dell'interfaccia)
        righe.append("Un testo scartato dai controlli l'ho già riprovato da solo (frasi separate, parametri diversi): "
                     "lo vedi con \"Mostra i rimasti\". Alla prossima \"Analizza e traduci\" lo riprovo con parametri "
                     "nuovi." if n == 1 else
                     "%d testi scartati dai controlli li ho già riprovati da solo (frasi separate, parametri diversi): "
                     "li vedi con \"Mostra i rimasti\". Alla prossima \"Analizza e traduci\" li riprovo con parametri "
                     "nuovi." % n)
    if e.extra.get("rifatte"):
        righe.append("Ho rifatto %d traduzioni salvate che non andavano bene." % e.extra["rifatte"])
    if e.extra.get("cache_corrette"):
        righe.append("Ho corretto %d frasi sbagliate nelle traduzioni salvate nel gioco." % e.extra["cache_corrette"])
    if e.extra.get("correzioni_scritte"):
        n = e.extra["correzioni_scritte"]
        righe.append("Ho scritto nel gioco %s." % ("la tua correzione" if n == 1 else "%d tue correzioni" % n))
    if e.extra.get("corrette_a_mano_scartate"):
        n = e.extra["corrette_a_mano_scartate"]
        righe.append("Una traduzione corretta a mano non si può scrivere nel gioco (righe o caratteri che il gioco non "
                     "sa mostrare): sistemala con \"Correggi traduzioni\" (elenco \"Non scritte nel gioco\")." if n == 1
                     else "%d traduzioni corrette a mano non si possono scrivere nel gioco (righe o caratteri che il "
                     "gioco non sa mostrare): sistemale con \"Correggi traduzioni\" (elenco \"Non scritte nel gioco\")."
                     % n)
    if e.extra.get("adattate_font"):
        n = e.extra["adattate_font"]
        righe.append("%s lettere che i font del gioco non sanno disegnare: le ho sostituite (es. è diventa e')."
                     % ("Un testo aveva" if n == 1 else "%d testi avevano" % n))
    if e.extra.get("accorciate"):
        n = e.extra["accorciate"]
        righe.append("Un'etichetta era troppo lunga per lo spazio che ha nel gioco: l'ho chiesta più corta." if n == 1
                     else "%d etichette erano troppo lunghe per lo spazio che hanno nel gioco: le ho chieste più "
                     "corte." % n)
    if e.extra.get("troppo_lunghe"):
        n = e.extra["troppo_lunghe"]
        if n == 1:
            righe.append("Un'etichetta potrebbe uscire dal suo spazio nel gioco: la vedi in \"Correggi traduzioni\" "
                         "(elenco \"Troppo lunghi\").")
        else:
            righe.append("%d etichette potrebbero uscire dal loro spazio nel gioco: le vedi in \"Correggi traduzioni\" "
                         "(elenco \"Troppo lunghi\")." % n)
    return righe


def testo_controllo(e):
    """Il controllo dopo aver giocato (controllo.come_esito): il riassunto e i problemi divisi per gruppo (corretti da
    soli, da fare, rimandati perche' il gioco era aperto, da sapere)."""
    righe = [e.motivo] if e.motivo else []
    problemi = e.extra.get("controllo") or []
    for stato, intestazione in GRUPPI_CONTROLLO:
        voci = []
        for p in problemi:
            if STATI_GRUPPO.get(p.get("stato")) != stato:
                continue
            voce = "- " + p.get("messaggio", "")
            if p.get("dettaglio") and p.get("stato") in ("corretto", "non_riuscito", "gia_tentato"):
                voce += " (%s)" % p["dettaglio"]
            voci.append(voce)
        if voci:
            righe.append(intestazione + "\n" + "\n".join(voci))
    return righe


def _testo_ripristino(e):
    if e.codice == "OK":
        return ["Il gioco è tornato com'era. Le traduzioni restano salvate: \"Analizza e traduci\" le rimette "
                "subito, senza tradurle di nuovo."]
    righe = [e.motivo] if e.motivo else []
    if e.codice == "GIOCO_APERTO":
        righe.append("Chiudi il gioco e premi di nuovo \"Ripristina originali\". Non ho toccato nulla.")
    elif e.codice == "PERMESSI":
        righe.append("Chiudi il gioco e i programmi che potrebbero usarne i file (anche l'antivirus), poi premi di "
                     "nuovo \"Ripristina originali\".")
    elif e.codice == "ERRORE":
        righe.append("Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith.")
    return righe


def _testo_problema(e, op, altro=True):
    """Niente (o non tutto) e' stato scritto: il motivo e il passo successivo."""
    motivo = e.motivo
    if e.codice == "NON_SUPPORTATO":
        righe = ["Questo gioco non si può ancora tradurre: %s" % _minuscola(motivo) if motivo else
                 "Questo gioco non si può ancora tradurre."]
        if "sperimentale" in e.azioni:
            righe.append("Ho però trovato dei file con testo: posso provare a tradurli in modo sperimentale (con il "
                         "backup automatico degli originali).")
        else:
            righe.append("Non ho modificato nulla nel gioco. Se vuoi, crea un rapporto da inviare a chi sviluppa "
                         "PatchSmith.")
        return righe
    righe = [motivo] if motivo else []
    if e.codice == "ANTICHEAT":
        righe.append("Per sicurezza non ho modificato nulla.")
    elif e.codice == "GIOCO_APERTO" and "riapplica" in e.azioni:
        righe.append("Chiudi il gioco e premi \"Riapplica\": le traduzioni sono già pronte, non serve tradurle di "
                     "nuovo.")
    elif e.codice == "GIOCO_APERTO":
        righe.append("Chiudi il gioco e premi di nuovo \"%s\" (o \"Riprova\")." % op)
    elif e.codice == "UFFICIALE":
        righe.append("Sceglila nel menu delle lingue del gioco: la traduzione ufficiale è migliore di quella "
                     "automatica." + (" Se vuoi comunque quella automatica, premi \"Traduci comunque\"."
                                      if "traduci_comunque" in e.azioni else ""))
    elif e.codice == "TRADUTTORE_BLOCCATO":
        righe.append("Le traduzioni fatte restano salvate. " + (
            "Puoi scegliere un altro traduttore (il traduttore locale non ha blocchi) e premere di nuovo \"Analizza "
            "e traduci\"." if altro else "Quando il traduttore torna disponibile premi di nuovo \"Analizza e "
            "traduci\": si riparte da dove si era fermato."))
    elif e.codice == "TRADUTTORE_ASSENTE":
        righe.append("Non ho modificato nulla nel gioco.")
    elif e.codice == "PARZIALE":
        righe.append(_conteggi(e))
        righe += _testo_tentativi(e)
        if altro:
            righe.append("Puoi provare con un altro traduttore (il traduttore locale non ha blocchi) e premere di "
                         "nuovo \"Analizza e traduci\".")
    elif e.codice == "PERMESSI":
        righe.append("Chiudi il gioco e i programmi che potrebbero usarne i file (anche l'antivirus), poi riprova. "
                     "Se il gioco è nella cartella Programmi, Windows potrebbe proteggerla.")
    elif e.codice == "ERRORE":
        righe.append("Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith.")
    return righe


def _minuscola(testo):
    """La frase dopo i due punti: iniziale minuscola, tranne le sigle (es. 'IL2CPP')."""
    return testo if testo[1:2].isupper() else testo[:1].lower() + testo[1:]


def testo_rimasti(e):
    """Il testo della finestra "Mostra i rimasti": i primi originali rimasti, uno per riga."""
    righe = ["- " + " ".join(t.split())[:90] for t in e.esempi_rimasti]
    intestazione = ("Testi rimasti in originale: %d. Eccone %d:" % (e.rimasti, len(righe)) if e.rimasti > len(righe)
                    else "Testi rimasti in originale: %d." % e.rimasti)
    return intestazione + "\n\n" + "\n".join(righe)
