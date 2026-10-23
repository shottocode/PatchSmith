"""Outcome of an operation (Analyze and translate, Reapply, Restore) in a form the interface can read:
a code, the counts, the reason in one sentence and the possible actions afterwards. From here also the text of the
windows the interface shows at the end, one per code, with the buttons for the next step.

The functions of automatico still return the same values as before (the command line and the tests use them): the
outcome is in esito["esito"], in the engine detection dictionary (or in the one passed to riapplica/ripristina).
"""
from dataclasses import asdict, dataclass, field

CODES = ("OK", "PARZIALE", "NULLA_DA_FARE", "UFFICIALE", "NON_SUPPORTATO", "ANTICHEAT", "GIOCO_APERTO",
          "TRADUTTORE_BLOCCATO", "TRADUTTORE_ASSENTE", "GIA_ORIGINALE", "PERMESSI", "CARTELLA_SBAGLIATA", "ERRORE")
# actions proposed after an outcome (each becomes a button, except scegli_lingua which is an instruction)
ACTIONS = ("chiudi_gioco", "usa_locale", "installa_locale", "mostra_rimasti", "crea_rapporto", "riapplica",
          "scegli_lingua", "traduci_comunque", "sperimentale", "ripristina", "traduci", "ritraduci", "correggi")
MAX_EXAMPLES = 20  # texts left untranslated to show (the first ones)
LIVE = {"locale": "PatchSmith, con il traduttore locale (sul tuo PC)",
            "online": "PatchSmith, con il traduttore online (con le pause che evitano i blocchi)",
            "google": "XUnity, con Google gratuito (via internet)",
            "nessuno": "nessuno, restano in originale"}
# why PatchSmith is not the one translating the new sentences (Outcome.extra["dal_vivo_nota"], see automatic._live)
LIVE_NOTES = {
    "senza_traduttore": "Adesso non c'è un traduttore disponibile: il traduttore locale non è installato e quelli "
                        "online sono in pausa. Installa il traduttore locale: dalla prossima volta che avvii il "
                        "gioco tradurrà le frasi nuove.",
    "plugin_google": "Non sono riuscito a preparare nel gioco il traduttore di PatchSmith: le frasi nuove le chiede "
                     "XUnity a Google, senza le pause di PatchSmith.",
    "plugin_nessuno": "Non sono riuscito a preparare nel gioco il traduttore di PatchSmith e Google è in pausa: le "
                      "frasi nuove resteranno in originale. Puoi creare un rapporto da inviare a chi ti aiuta con "
                      "PatchSmith.",
}
# Outcome field -> key of its dictionary (as_dict, used in the report)
_DATA_KEYS = {"code": "codice", "reason": "motivo", "engine": "motore", "written": "scritto",
              "translated_items": "tradotti", "remaining": "rimasti", "remaining_examples": "esempi_rimasti",
              "live": "dal_vivo", "actions": "azioni"}


@dataclass
class Outcome:
    """codice: one of CODES. motore: unity, unity-localization, unreal, xna, gamemaker, web... (None if unknown).
    scritto: in this operation at least one translation was written to the game (for Unity: BepInEx and XUnity
    installed, which also translate while playing). tradotti/rimasti: texts translated and left
    untranslated; remaining_examples: the first originals left. live (Unity): who translates the new sentences while
    playing, 'locale', 'google', 'nessuno' (None: there is no live translation). motivo: one sentence for the user.
    azioni: from ACTIONS. extra: data for the interface's questions (official language, added language...) and what
    PatchSmith has already redone on its own (ritentati, rifatte, cache_corrette, corrette_a_mano_scartate,
    adattate_font, accorciate, troppo_lunghe: see automatic._extra_attempts)."""
    code: str
    reason: str = ""
    engine: str = None
    written: bool = False
    translated_items: int = 0
    remaining: int = 0
    remaining_examples: list = field(default_factory=list)
    live: str = None
    actions: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.code not in CODES:
            raise ValueError("Codice di esito sconosciuto: %s" % self.code)
        unknowns = [a for a in self.actions if a not in ACTIONS]
        if unknowns:
            raise ValueError("Azioni sconosciute: %s" % ", ".join(unknowns))
        self.remaining_examples = [str(t) for t in list(dict.fromkeys(self.remaining_examples))[:MAX_EXAMPLES]]
        self.actions = list(dict.fromkeys(self.actions))
        if self.remaining and self.remaining_examples and "mostra_rimasti" not in self.actions:
            self.actions.append("mostra_rimasti")

    def as_dict(self):
        # the keys stay the Italian ones written before the code was renamed in English (a data format)
        return {_DATA_KEYS.get(k, k): v for k, v in asdict(self).items()}

    def summary(self):
        """One line for the log on disk."""
        parts = ["Esito: %s" % self.code]
        if self.engine:
            parts.append("motore %s" % self.engine)
        if self.written or self.translated_items or self.remaining:
            parts.append("scritto nel gioco: %s, tradotti %d, rimasti %d" % ("si'" if self.written else "no",
                                                                             self.translated_items, self.remaining))
        if self.live:
            parts.append("frasi nuove: %s" % self.live)
        if self.reason:
            parts.append(self.reason)
        return " - ".join(parts)


def set_(outcome, code, reason="", **fields):
    """Records the outcome in esito["esito"] (the detection dictionary, or the one given by the interface) and
    returns the dictionary. With esito None it does nothing."""
    if outcome is not None:
        outcome["esito"] = Outcome(code, reason, **fields)
    return outcome


# --- exceptions --------------------------------------------------------------------------------------

def from_exception(error):
    """Outcome for an error that made it up to the interface. PatchSmith's errors have a message for the user
    (and sometimes a code: error.code); the others are unexpected: the type is stated and a report is proposed."""
    from .backend import BackendError
    from .system import SysError
    code = getattr(error, "code", None)
    message = str(error).strip()
    if code in CODES:
        return Outcome(code, message, actions=_actions_for(code))
    if isinstance(error, BackendError):
        return Outcome("TRADUTTORE_BLOCCATO", message, actions=["usa_locale"])
    if isinstance(error, SysError):
        return Outcome("GIOCO_APERTO", message, actions=["chiudi_gioco"])
    if isinstance(error, PermissionError):
        return Outcome("PERMESSI", "Windows non mi lascia modificare %s (%s)." % (
            error.filename or "un file del gioco", error.strerror or "accesso negato"),
            actions=["chiudi_gioco", "crea_rapporto"])
    if _known_error(error):
        return Outcome("ERRORE", message or type(error).__name__, actions=["crea_rapporto"])
    return Outcome("ERRORE", message, actions=["crea_rapporto"],
                 extra={"imprevisto": True, "tipo": type(error).__name__})


def _actions_for(code):
    return {"GIOCO_APERTO": ["chiudi_gioco"], "TRADUTTORE_ASSENTE": ["installa_locale"],
            "TRADUTTORE_BLOCCATO": ["usa_locale"], "NON_SUPPORTATO": ["crea_rapporto"],
            "PERMESSI": ["chiudi_gioco", "crea_rapporto"]}.get(code, [])


def _known_error(error):
    """True for the errors with a message written for the user (the same ones the command line shows as
    'ERRORE: ...')."""
    from . import adapter_unity, adapter_unreal, adapter_xna, components, dotnet, ollama, xnb
    known = (adapter_unity.UnityError, adapter_unreal.UnrealError, adapter_xna.XnaError, components.ComponentError,
            dotnet.DotnetError, xnb.XnbError, ollama.LocalError, RuntimeError, ValueError, OSError)
    return isinstance(error, known) and type(error) not in (UnicodeDecodeError, NotImplementedError, RecursionError)


# --- interface windows ----------------------------------------------------------------------------

HOW_TO_SEE_IT = {
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

TITLES = {
    "OK": "Traduzione installata", "PARZIALE": "Traduzione installata in parte", "NULLA_DA_FARE": "Niente da fare",
    "UFFICIALE": "Il gioco ha già la tua lingua", "NON_SUPPORTATO": "Gioco non ancora supportato",
    "ANTICHEAT": "Gioco con anti-cheat", "GIOCO_APERTO": "Il gioco è aperto",
    "TRADUTTORE_BLOCCATO": "Traduzione interrotta", "TRADUTTORE_ASSENTE": "Traduttore non pronto",
    "GIA_ORIGINALE": "Gioco già originale", "PERMESSI": "File bloccati", "CARTELLA_SBAGLIATA": "Cartella da cambiare",
    "ERRORE": "Operazione non riuscita",
}
OPERATION_TITLES = {("riapplica", "OK"): "Traduzione riapplicata", ("ritraduci", "OK"): "Traduzioni rifatte",
                     ("ripristina", "OK"): "Originali ripristinati",
                     ("ripristina", "PARZIALE"): "Ripristino quasi completo",
                     ("controlla", "OK"): "Controllo dopo aver giocato", ("controlla", "PARZIALE"): "Qualcosa da fare",
                     ("controlla", "NULLA_DA_FARE"): "Niente da controllare"}
STATE = {
    "OK": "completato.", "PARZIALE": "completato in parte.", "NULLA_DA_FARE": "niente da fare.",
    "UFFICIALE": "il gioco ha già la lingua ufficiale.", "NON_SUPPORTATO": "gioco non ancora supportato.",
    "ANTICHEAT": "gioco con anti-cheat, nessuna modifica.", "GIOCO_APERTO": "il gioco è aperto.",
    "TRADUTTORE_BLOCCATO": "traduttore fermo.", "TRADUTTORE_ASSENTE": "traduttore non pronto.",
    "GIA_ORIGINALE": "il gioco è già originale.", "PERMESSI": "file bloccati.",
    "CARTELLA_SBAGLIATA": "cartella da cambiare.", "ERRORE": "non riuscito.",
}
KINDS = {"OK": "info", "PARZIALE": "avviso", "NULLA_DA_FARE": "info", "UFFICIALE": "info", "GIA_ORIGINALE": "info",
        "ERRORE": "errore"}
BUTTONS = {"mostra_rimasti": "Mostra i rimasti", "crea_rapporto": "Crea rapporto", "riapplica": "Riapplica",
            "usa_locale": "Usa il traduttore locale", "installa_locale": "Installa il traduttore locale",
            "traduci_comunque": "Traduci comunque", "chiudi_gioco": "Riprova",
            "sperimentale": "Prova in modo sperimentale", "ripristina": "Ripristina originali",
            "traduci": "Analizza e traduci", "ritraduci": "Ritraduci le frasi", "correggi": "Correggi traduzioni"}
OPERATION_NAMES = {"traduci": "Analizza e traduci", "riapplica": "Riapplica",
                   "ripristina": "Ripristina originali", "controlla": "Controllo dopo aver giocato",
                   "ritraduci": "Ritraduci"}
# operations after which, with texts left untranslated or translations the game cannot show, "Fix translations"
# is proposed (the window with the right list already chosen)
WITH_FIXES = ("traduci", "riapplica", "ritraduci")
# status of the problems of the after-play check (checks.Problem.state) -> group in the window
CHECK_GROUPS = (("corretto", "Corretto da solo:"),
                    ("da_fare", "Da fare:"), ("rimandato", "Da correggere quando il gioco è chiuso:"),
                    ("info", "Da sapere:"))
GROUP_STATES = {"corretto": "corretto", "non_riuscito": "da_fare", "gia_tentato": "da_fare", "da_fare": "da_fare",
                "rimandato": "rimandato", "da_correggere": "rimandato", "info": "info"}
# operations that do not touch the game's files (installation of the local translator, report, window
# commands): no "Try again" (it would repeat the last operation on the game) nor advice about the open game
OUTSIDE_GAME = ("installa", "rapporto", "interfaccia")


def title_text(e, operation="traduci"):
    if operation == "rapporto":
        return "Rapporto non creato"
    if e.extra.get("imprevisto"):
        return "Errore imprevisto"
    if e.code == "OK" and not e.written and operation == "traduci":
        return "Fatto"
    return OPERATION_TITLES.get((operation, e.code)) or TITLES[e.code]


def _how_to_see_it(e):
    added_lang = e.extra.get("lingua_aggiunta")
    if added_lang:
        return ("La traduzione è una lingua nuova del gioco: \"%s\". L'originale resta com'era e si può ancora "
                "scegliere.\n\n%s\n\n\"Ripristina originali\" toglie la lingua aggiunta." % (
                    added_lang["nome"], "L'ho già impostata nelle opzioni del gioco: avvialo e basta."
                    if added_lang.get("impostata") else
                    "Nel gioco apri Opzioni > Lingua e scegli \"%s\" con le frecce." % added_lang["nome"].upper()))
    return HOW_TO_SEE_IT.get(e.engine, HOW_TO_SEE_IT[None])


def _counts(e):
    if not (e.translated_items or e.remaining):
        return ""
    if not e.remaining:
        return "Testi tradotti: %d." % e.translated_items
    return "Testi tradotti: %d, rimasti in originale: %d." % (e.translated_items, e.remaining)


def window(e, operation="traduci", local_ready=False, current=None):
    """The end-of-operation window: {"titolo", "tipo" (info/avviso/errore/domanda), "testo",
    "pulsanti": [(label, action)], "stato" (line under the log)}. The first button is "OK" (action None).
    local_ready: the local translator is installed (then 'usa_locale' chooses it, otherwise it installs it);
    attuale: the chosen translator (if it is already the local one, 'usa_locale' is not needed)."""
    op = OPERATION_NAMES.get(operation, operation)
    other = "usa_locale" in e.actions and not (current == "locale" and local_ready)  # propose another translator
    lines = []
    if operation == "rapporto":  # no log on disk nor report to propose: only the reason is given
        lines.append("Non sono riuscito a creare il rapporto: %s" % (
            "errore imprevisto (%s): %s" % (e.extra.get("tipo", "?"), e.reason) if e.extra.get("imprevisto")
            else e.reason or "motivo sconosciuto"))
    elif e.extra.get("imprevisto"):
        lines.append("Errore imprevisto (%s): %s" % (e.extra.get("tipo", "?"), e.reason or "nessun dettaglio"))
        lines.append("I dettagli tecnici sono nel registro salvato. Puoi creare un rapporto da inviare a chi ti "
                     "aiuta con PatchSmith.")
    elif operation == "controlla" and "controllo" in e.extra:
        lines += check_text(e)
    elif operation in OUTSIDE_GAME:
        lines += [e.reason, "Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith."]
    elif operation == "ripristina":
        lines += _restore_text(e)
    elif e.written and e.code in ("OK", "PARZIALE", "TRADUTTORE_BLOCCATO", "GIOCO_APERTO"):
        lines += _written_text(e, op, other)
    elif e.code == "OK":
        lines += [e.reason or "Fatto.", _counts(e)] + _attempts_text(e)
    else:
        lines += _problem_text(e, op, other)
    if e.extra.get("ritraduzione") and operation == "ritraduci":
        lines.insert(0, _retranslation_text(e.extra["ritraduzione"]))
    buttons = [("OK", None)]
    for action in e.actions:
        if action in ("usa_locale", "installa_locale"):
            if action == "usa_locale" and current == "locale" and local_ready:
                continue  # it is already the chosen one
            # installed and not chosen: use it (installing it would do nothing); otherwise install it
            action = "usa_locale" if local_ready and current != "locale" else "installa_locale"
        if operation in OUTSIDE_GAME and action == "chiudi_gioco":
            continue
        if operation == "rapporto" and action == "crea_rapporto":
            continue
        if action in BUTTONS and action not in [a for _, a in buttons]:
            if action == "mostra_rimasti" and not e.remaining_examples:
                continue
            buttons.append((BUTTONS[action], action))
    if to_fix(e, operation) and "correggi" not in [a for _, a in buttons]:
        buttons.append((BUTTONS["correggi"], "correggi"))
    kind = KINDS.get(e.code, "avviso")
    if e.extra.get("imprevisto") or operation == "rapporto":
        kind = "errore"
    return {"titolo": title_text(e, operation), "tipo": kind, "testo": "\n\n".join(r for r in lines if r),
            "pulsanti": buttons, "stato": STATE[e.code]}


def to_fix(e, operation="traduci"):
    """True if after this outcome it makes sense to propose "Fix translations": after Analyze and translate, Reapply or
    Re-translate, with texts left untranslated, translations the game cannot show or texts already retried without
    success (not for unexpected errors)."""
    if operation not in WITH_FIXES or e.extra.get("imprevisto") or \
            e.code not in ("OK", "PARZIALE", "TRADUTTORE_BLOCCATO", "GIOCO_APERTO"):
        return False
    return bool(e.remaining or e.extra.get("corrette_a_mano_scartate") or e.extra.get("ritentati")
                or (e.extra.get("ritraduzione") or {}).get("rimaste") or e.extra.get("troppo_lunghe"))


def fixes_filter(e):
    """The list to show in "Fix translations" when opened from this outcome (see correzioni.FILTRI)."""
    if e is not None and (e.extra.get("corrette_a_mano_scartate") or (
            e.extra.get("scartate") and e.remaining <= e.extra["scartate"] and not e.extra.get("ritentati"))):
        return "gioco"  # (only translations the game could not show were left out of the game)
    if e is not None and (e.remaining or e.extra.get("ritentati")):
        return "rimasti"
    if e is not None and e.extra.get("troppo_lunghe"):
        return "lunghi"
    return None


def _retranslation_text(r):
    text = "Ho ritradotto %d testi su %d." % (r.get("ritradotte", 0), r.get("testi", 0))
    if r.get("rimaste"):
        text += (" Per gli altri %d resta la traduzione di prima (la nuova non ha passato i controlli, o era uguale)."
                  % r["rimaste"])
    if r.get("interrotta"):
        text += " La ritraduzione si è fermata: %s" % r["interrotta"]
    return text


def _written_text(e, op, other=True):
    """Something is in the game: how to see it, the counts, who translates the new sentences and what to do for the
    rest."""
    lines = []
    if e.code == "GIOCO_APERTO":
        lines.append(e.reason or "Il gioco è aperto.")
        lines.append("Chiudi il gioco e premi \"Riapplica\": le traduzioni sono già pronte, non serve tradurle di "
                     "nuovo.")
        return lines
    if e.code == "TRADUTTORE_BLOCCATO":
        lines.append(e.reason)
    lines.append(_how_to_see_it(e))
    lines.append(_counts(e))
    if e.live:
        lines.append("Frasi nuove mentre giochi: %s." % LIVE.get(e.live, e.live))
        lines.append(LIVE_NOTES.get(e.extra.get("dal_vivo_nota"), ""))
    if e.code == "PARZIALE" and e.reason:
        lines.append(e.reason)
    lines += _attempts_text(e)
    if e.remaining and e.code in ("PARZIALE", "TRADUTTORE_BLOCCATO"):
        if other:
            lines.append("Per i testi rimasti puoi usare un altro traduttore (il traduttore locale non ha blocchi) e "
                         "premere di nuovo \"Analizza e traduci\": le traduzioni fatte restano.")
        elif fixes_filter(e) == "gioco":
            lines.append("Le traduzioni che il gioco non saprebbe mostrare puoi correggerle tu con \"Correggi "
                         "traduzioni\" (elenco \"Non scritte nel gioco\"): quando salvi, PatchSmith le scrive nel gioco "
                         "da solo.")
        else:
            lines.append("I testi rimasti in originale puoi tradurli tu con \"Correggi traduzioni\" (elenco "
                         "\"Rimasti in originale\"%s): quando salvi, PatchSmith li scrive nel gioco da solo." % (
                             "; quelli che il gioco non saprebbe mostrare sono in \"Non scritte nel gioco\""
                             if e.extra.get("scartate") else ""))
    return lines


def _attempts_text(e):
    """What PatchSmith has already done on its own for the texts left (Outcome.extra, see automatic._extra_attempts):
    in place of the old "rilancia", which repeated the same questions and gave the same answers."""
    lines = []
    if e.extra.get("ritentati"):
        n = e.extra["ritentati"]
        # whole sentences, singular and plural (in pieces they cannot be translated into the interface language)
        lines.append("Un testo scartato dai controlli l'ho già riprovato da solo (frasi separate, parametri diversi): "
                     "lo vedi con \"Mostra i rimasti\". Alla prossima \"Analizza e traduci\" lo riprovo con parametri "
                     "nuovi." if n == 1 else
                     "%d testi scartati dai controlli li ho già riprovati da solo (frasi separate, parametri diversi): "
                     "li vedi con \"Mostra i rimasti\". Alla prossima \"Analizza e traduci\" li riprovo con parametri "
                     "nuovi." % n)
    if e.extra.get("rifatte"):
        lines.append("Ho rifatto %d traduzioni salvate che non andavano bene." % e.extra["rifatte"])
    if e.extra.get("cache_corrette"):
        lines.append("Ho corretto %d frasi sbagliate nelle traduzioni salvate nel gioco." % e.extra["cache_corrette"])
    if e.extra.get("correzioni_scritte"):
        n = e.extra["correzioni_scritte"]
        lines.append("Ho scritto nel gioco %s." % ("la tua correzione" if n == 1 else "%d tue correzioni" % n))
    if e.extra.get("corrette_a_mano_scartate"):
        n = e.extra["corrette_a_mano_scartate"]
        lines.append("Una traduzione corretta a mano non si può scrivere nel gioco (righe o caratteri che il gioco non "
                     "sa mostrare): sistemala con \"Correggi traduzioni\" (elenco \"Non scritte nel gioco\")." if n == 1
                     else "%d traduzioni corrette a mano non si possono scrivere nel gioco (righe o caratteri che il "
                     "gioco non sa mostrare): sistemale con \"Correggi traduzioni\" (elenco \"Non scritte nel gioco\")."
                     % n)
    if e.extra.get("adattate_font"):
        n = e.extra["adattate_font"]
        lines.append("%s lettere che i font del gioco non sanno disegnare: le ho sostituite (es. è diventa e')."
                     % ("Un testo aveva" if n == 1 else "%d testi avevano" % n))
    if e.extra.get("accorciate"):
        n = e.extra["accorciate"]
        lines.append("Un'etichetta era troppo lunga per lo spazio che ha nel gioco: l'ho chiesta più corta." if n == 1
                     else "%d etichette erano troppo lunghe per lo spazio che hanno nel gioco: le ho chieste più "
                     "corte." % n)
    if e.extra.get("troppo_lunghe"):
        n = e.extra["troppo_lunghe"]
        if n == 1:
            lines.append("Un'etichetta potrebbe uscire dal suo spazio nel gioco: la vedi in \"Correggi traduzioni\" "
                         "(elenco \"Troppo lunghi\").")
        else:
            lines.append("%d etichette potrebbero uscire dal loro spazio nel gioco: le vedi in \"Correggi traduzioni\" "
                         "(elenco \"Troppo lunghi\")." % n)
    return lines


def check_text(e):
    """The after-play check (checks.as_outcome): the summary and the problems split by group (fixed on their
    own, to do, postponed because the game was open, good to know)."""
    lines = [e.reason] if e.reason else []
    problems = e.extra.get("controllo") or []
    for state, header in CHECK_GROUPS:
        entries = []
        for p in problems:
            if GROUP_STATES.get(p.get("stato")) != state:
                continue
            entry = "- " + p.get("messaggio", "")
            if p.get("dettaglio") and p.get("stato") in ("corretto", "non_riuscito", "gia_tentato"):
                entry += " (%s)" % p["dettaglio"]
            entries.append(entry)
        if entries:
            lines.append(header + "\n" + "\n".join(entries))
    return lines


def _restore_text(e):
    if e.code == "OK":
        return ["Il gioco è tornato com'era. Le traduzioni restano salvate: \"Analizza e traduci\" le rimette "
                "subito, senza tradurle di nuovo."]
    lines = [e.reason] if e.reason else []
    if e.code == "GIOCO_APERTO":
        lines.append("Chiudi il gioco e premi di nuovo \"Ripristina originali\". Non ho toccato nulla.")
    elif e.code == "PERMESSI":
        lines.append("Chiudi il gioco e i programmi che potrebbero usarne i file (anche l'antivirus), poi premi di "
                     "nuovo \"Ripristina originali\".")
    elif e.code == "ERRORE":
        lines.append("Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith.")
    return lines


def _problem_text(e, op, other=True):
    """Nothing (or not everything) was written: the reason and the next step."""
    reason = e.reason
    if e.code == "NON_SUPPORTATO":
        lines = ["Questo gioco non si può ancora tradurre: %s" % _lowercase(reason) if reason else
                 "Questo gioco non si può ancora tradurre."]
        if "sperimentale" in e.actions:
            lines.append("Ho però trovato dei file con testo: posso provare a tradurli in modo sperimentale (con il "
                         "backup automatico degli originali).")
        else:
            lines.append("Non ho modificato nulla nel gioco. Se vuoi, crea un rapporto da inviare a chi sviluppa "
                         "PatchSmith.")
        return lines
    lines = [reason] if reason else []
    if e.code == "ANTICHEAT":
        lines.append("Per sicurezza non ho modificato nulla.")
    elif e.code == "GIOCO_APERTO" and "riapplica" in e.actions:
        lines.append("Chiudi il gioco e premi \"Riapplica\": le traduzioni sono già pronte, non serve tradurle di "
                     "nuovo.")
    elif e.code == "GIOCO_APERTO":
        lines.append("Chiudi il gioco e premi di nuovo \"%s\" (o \"Riprova\")." % op)
    elif e.code == "UFFICIALE":
        lines.append("Sceglila nel menu delle lingue del gioco: la traduzione ufficiale è migliore di quella "
                     "automatica." + (" Se vuoi comunque quella automatica, premi \"Traduci comunque\"."
                                      if "traduci_comunque" in e.actions else ""))
    elif e.code == "TRADUTTORE_BLOCCATO":
        lines.append("Le traduzioni fatte restano salvate. " + (
            "Puoi scegliere un altro traduttore (il traduttore locale non ha blocchi) e premere di nuovo \"Analizza "
            "e traduci\"." if other else "Quando il traduttore torna disponibile premi di nuovo \"Analizza e "
            "traduci\": si riparte da dove si era fermato."))
    elif e.code == "TRADUTTORE_ASSENTE":
        lines.append("Non ho modificato nulla nel gioco.")
    elif e.code == "PARZIALE":
        lines.append(_counts(e))
        lines += _attempts_text(e)
        if other:
            lines.append("Puoi provare con un altro traduttore (il traduttore locale non ha blocchi) e premere di "
                         "nuovo \"Analizza e traduci\".")
    elif e.code == "PERMESSI":
        lines.append("Chiudi il gioco e i programmi che potrebbero usarne i file (anche l'antivirus), poi riprova. "
                     "Se il gioco è nella cartella Programmi, Windows potrebbe proteggerla.")
    elif e.code == "ERRORE":
        lines.append("Puoi creare un rapporto da inviare a chi ti aiuta con PatchSmith.")
    return lines


def _lowercase(text):
    """The sentence after the colon: lowercase initial, except acronyms (e.g. 'IL2CPP')."""
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


def remaining_text(e):
    """The text of the "Show remaining" window: the first originals left, one per line."""
    lines = ["- " + " ".join(t.split())[:90] for t in e.remaining_examples]
    header = ("Testi rimasti in originale: %d. Eccone %d:" % (e.remaining, len(lines)) if e.remaining > len(lines)
                    else "Testi rimasti in originale: %d." % e.remaining)
    return header + "\n\n" + "\n".join(lines)
