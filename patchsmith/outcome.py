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
LIVE = {"locale": "PatchSmith, with the local translator (on your PC)",
            "online": "PatchSmith, with the online translator (with pauses that avoid getting blocked)",
            "google": "XUnity, with free Google access (via the internet)",
            "nessuno": "none, they remain in their original form"}
# why PatchSmith is not the one translating the new sentences (Outcome.extra["dal_vivo_nota"], see automatic._live)
LIVE_NOTES = {
    "senza_traduttore": "Right now no translator is available: the local translator isn't installed and the online "
                        "ones are paused. Install the local translator: from the next time you start the game, it will "
                        "translate the new phrases.",
    "plugin_google": "I couldn't set up the PatchSmith translator in the game: XUnity asks Google for the new phrases, "
                     "without PatchSmith's pauses.",
    "plugin_nessuno": "I haven't been able to set up the PatchSmith translator in the game, and Google Translate is on "
                      "hold: any new phrases will remain in their original language. You can create a report to send "
                      "to anyone who is helping you with PatchSmith.",
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
            raise ValueError("Unknown outcome code: %s" % self.code)
        unknowns = [a for a in self.actions if a not in ACTIONS]
        if unknowns:
            raise ValueError("Unknown actions: %s" % ", ".join(unknowns))
        self.remaining_examples = [str(t) for t in list(dict.fromkeys(self.remaining_examples))[:MAX_EXAMPLES]]
        self.actions = list(dict.fromkeys(self.actions))
        if self.remaining and self.remaining_examples and "mostra_rimasti" not in self.actions:
            self.actions.append("mostra_rimasti")

    def as_dict(self):
        # the keys stay the Italian ones written before the code was renamed in English (a data format)
        return {_DATA_KEYS.get(k, k): v for k, v in asdict(self).items()}

    def summary(self):
        """One line for the log on disk."""
        parts = ["Result: %s" % self.code]
        if self.engine:
            parts.append("engine %s" % self.engine)
        if self.written or self.translated_items or self.remaining:
            parts.append("written to the game: %s, translated %d, remaining %d" % ("yes" if self.written else "no",
                                                                             self.translated_items, self.remaining))
        if self.live:
            parts.append("new phrases: %s" % self.live)
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
        return Outcome("PERMESSI", "Windows doesn't allow me to modify %s (%s)." % (
            error.filename or "a game file", error.strerror or "access denied"),
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
    "unity": "The translation is installed within the game (BepInEx + XUnity.AutoTranslator).\n\nStart the game as "
             "usual: the pre-translated phrases appear immediately in Italian; the others appear in the original "
             "language for a moment, then are translated. In the game: ALT+T switches between the original and the "
             "translation.",
    "unity-localization": "The game's text tables are translated (the originals are saved and can be put back with "
                          '"Restore originals").\n\nIn the game, keep English selected: its texts are now translated.',
    "gamemaker": "The game's text files are translated (the originals are saved and can be put back with \"Restore "
                 'originals").\n\nIn the game, keep the original language selected, usually English.',
    "xna": "The translation is installed as additional language files (the original files have not been "
           "modified).\n\nIn the game, either leave or select the original language, usually English: the translation "
           "will then take over.",
    "unreal": "The translation is installed as an additional patch pak (the original files have not been "
              "modified).\n\nIn the game, you need to select the original language, usually English: the translation "
              "will then take its place.",
    None: 'The game texts are translated (the originals are saved and can be put back with "Restore '
          'originals").\n\nStart the game as usual.',
}

TITLES = {
    "OK": "Translation installed", "PARZIALE": "Translation partially installed", "NULLA_DA_FARE": "Nothing to do",
    "UFFICIALE": "The game already has your language", "NON_SUPPORTATO": "Game not yet supported",
    "ANTICHEAT": "Game with anti-cheat", "GIOCO_APERTO": "The game is open",
    "TRADUTTORE_BLOCCATO": "Translation interrupted", "TRADUTTORE_ASSENTE": "Translator not ready",
    "GIA_ORIGINALE": "Game already original", "PERMESSI": "Locked files", "CARTELLA_SBAGLIATA": "Wrong folder",
    "ERRORE": "Operation failed",
}
OPERATION_TITLES = {("riapplica", "OK"): "Translation reapplied", ("ritraduci", "OK"): "Translations redone",
                     ("ripristina", "OK"): "Originals restored",
                     ("ripristina", "PARZIALE"): "Almost complete restoration",
                     ("controlla", "OK"): "Check after playing", ("controlla", "PARZIALE"): "Something to do",
                     ("controlla", "NULLA_DA_FARE"): "Nothing to check"}
STATE = {
    "OK": "completed.", "PARZIALE": "partially completed.", "NULLA_DA_FARE": "nothing to do.",
    "UFFICIALE": "the game already has the official language.", "NON_SUPPORTATO": "game not supported yet.",
    "ANTICHEAT": "game with anti-cheat, nothing changed.", "GIOCO_APERTO": "the game is open.",
    "TRADUTTORE_BLOCCATO": "translator stopped.", "TRADUTTORE_ASSENTE": "translator not ready.",
    "GIA_ORIGINALE": "the game already has its original files.", "PERMESSI": "files locked.",
    "CARTELLA_SBAGLIATA": "folder to change.", "ERRORE": "failed.",
}
KINDS = {"OK": "info", "PARZIALE": "avviso", "NULLA_DA_FARE": "info", "UFFICIALE": "info", "GIA_ORIGINALE": "info",
        "ERRORE": "errore"}
BUTTONS = {"mostra_rimasti": "Show remaining", "crea_rapporto": "Create report", "riapplica": "Reapply",
            "usa_locale": "Use the local translator", "installa_locale": "Install the local translator",
            "traduci_comunque": "Translate anyway", "chiudi_gioco": "Try again",
            "sperimentale": "Try it out experimentally", "ripristina": "Restore originals",
            "traduci": "Analyze and translate", "ritraduci": "Re-translate the phrases", "correggi": "Fix translations"}
OPERATION_NAMES = {"traduci": "Analyze and translate", "riapplica": "Reapply",
                   "ripristina": "Restore originals", "controlla": "Check after playing",
                   "ritraduci": "Re-translate"}
# operations after which, with texts left untranslated or translations the game cannot show, "Fix translations"
# is proposed (the window with the right list already chosen)
WITH_FIXES = ("traduci", "riapplica", "ritraduci")
# status of the problems of the after-play check (checks.Problem.state) -> group in the window
CHECK_GROUPS = (("corretto", "Fixed automatically:"),
                    ("da_fare", "To do:"), ("rimandato", "To fix when the game is closed:"),
                    ("info", "Good to know:"))
GROUP_STATES = {"corretto": "corretto", "non_riuscito": "da_fare", "gia_tentato": "da_fare", "da_fare": "da_fare",
                "rimandato": "rimandato", "da_correggere": "rimandato", "info": "info"}
# operations that do not touch the game's files (installation of the local translator, report, window
# commands): no "Try again" (it would repeat the last operation on the game) nor advice about the open game
OUTSIDE_GAME = ("installa", "rapporto", "interfaccia")


def title_text(e, operation="traduci"):
    if operation == "rapporto":
        return "Report not created"
    if e.extra.get("imprevisto"):
        return "Unexpected error"
    if e.code == "OK" and not e.written and operation == "traduci":
        return "Done"
    return OPERATION_TITLES.get((operation, e.code)) or TITLES[e.code]


def _how_to_see_it(e):
    added_lang = e.extra.get("lingua_aggiunta")
    if added_lang:
        return ('The translation is a new language for the game: "%s". The original remains as it was and you can '
                'still choose it.\n\n%s\n\n"Restore originals" removes the added language.' % (
                    added_lang["nome"], "I've already set it up in the game options: just launch it and it will work."
                    if added_lang.get("impostata") else
                    'In the game, open Options > Language and select "%s" using the arrows.'
                    % added_lang["nome"].upper()))
    return HOW_TO_SEE_IT.get(e.engine, HOW_TO_SEE_IT[None])


def _counts(e):
    if not (e.translated_items or e.remaining):
        return ""
    if not e.remaining:
        return "Translated texts: %d." % e.translated_items
    return "Translated texts: %d, left untranslated: %d." % (e.translated_items, e.remaining)


def window(e, operation="traduci", local_ready=False, current=None):
    """The end-of-operation window: {"titolo", "tipo" (info/avviso/errore/domanda), "testo",
    "pulsanti": [(label, action)], "stato" (line under the log)}. The first button is "OK" (action None).
    local_ready: the local translator is installed (then 'usa_locale' chooses it, otherwise it installs it);
    attuale: the chosen translator (if it is already the local one, 'usa_locale' is not needed)."""
    op = OPERATION_NAMES.get(operation, operation)
    other = "usa_locale" in e.actions and not (current == "locale" and local_ready)  # propose another translator
    lines = []
    if operation == "rapporto":  # no log on disk nor report to propose: only the reason is given
        lines.append("I wasn't able to create the report: %s" % (
            "unexpected error (%s): %s" % (e.extra.get("tipo", "?"), e.reason) if e.extra.get("imprevisto")
            else e.reason or "unknown reason"))
    elif e.extra.get("imprevisto"):
        lines.append("Unexpected error (%s): %s" % (e.extra.get("tipo", "?"), e.reason or "no details"))
        lines.append("The technical details are in the saved log. You can create a report to send to whoever is "
                     "assisting you with PatchSmith.")
    elif operation == "controlla" and "controllo" in e.extra:
        lines += check_text(e)
    elif operation in OUTSIDE_GAME:
        lines += [e.reason, "You can create a report to send to anyone who helps you with PatchSmith."]
    elif operation == "ripristina":
        lines += _restore_text(e)
    elif e.written and e.code in ("OK", "PARZIALE", "TRADUTTORE_BLOCCATO", "GIOCO_APERTO"):
        lines += _written_text(e, op, other)
    elif e.code == "OK":
        lines += [e.reason or "Done.", _counts(e)] + _attempts_text(e)
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
    text = "I re-translated %d of %d texts." % (r.get("ritradotte", 0), r.get("testi", 0))
    if r.get("rimaste"):
        text += (" For the other %d, the previous translation remains in place (the new one didn't pass quality "
                 "control, or it was the same)."
                  % r["rimaste"])
    if r.get("interrotta"):
        text += " The retranslation has stopped: %s" % r["interrotta"]
    return text


def _written_text(e, op, other=True):
    """Something is in the game: how to see it, the counts, who translates the new sentences and what to do for the
    rest."""
    lines = []
    if e.code == "GIOCO_APERTO":
        lines.append(e.reason or "The game is open.")
        lines.append('Close the game and press "Reapply": the translations are already done, no need to translate them '
                     "again.")
        return lines
    if e.code == "TRADUTTORE_BLOCCATO":
        lines.append(e.reason)
    lines.append(_how_to_see_it(e))
    lines.append(_counts(e))
    if e.live:
        lines.append("New phrases while playing: %s." % LIVE.get(e.live, e.live))
        lines.append(LIVE_NOTES.get(e.extra.get("dal_vivo_nota"), ""))
    if e.code == "PARZIALE" and e.reason:
        lines.append(e.reason)
    lines += _attempts_text(e)
    if e.remaining and e.code in ("PARZIALE", "TRADUTTORE_BLOCCATO"):
        if other:
            lines.append("For the remaining texts you can use another translator (the local translator has no blocks) "
                         'and press "Analyze and translate" again: the translations already done are kept.')
        elif fixes_filter(e) == "gioco":
            lines.append("You can fix the translations the game couldn't display yourself with \"Fix translations\" "
                         '("Not written to the game" list): when you save, PatchSmith writes them into the game on its '
                         "own.")
        else:
            lines.append('You can translate the untranslated texts yourself with "Fix translations" ("Left '
                         'untranslated" list%s): when you save, PatchSmith writes them to the game by itself.' % (
                             "; those that the game wouldn't be able to display are in \"Not written to the game\""
                             if e.extra.get("scartate") else ""))
    return lines


def _attempts_text(e):
    """What PatchSmith has already done on its own for the texts left (Outcome.extra, see automatic._extra_attempts):
    in place of the old "rilancia", which repeated the same questions and gave the same answers."""
    lines = []
    if e.extra.get("ritentati"):
        n = e.extra["ritentati"]
        # whole sentences, singular and plural (in pieces they cannot be translated into the interface language)
        lines.append("One text rejected by the checks has already been retried automatically (separate sentences, "
                     'different parameters): you can see it with "Show remaining". At the next "Analyze and translate" '
                     "I'll retry it with new parameters." if n == 1 else
                     "%d texts rejected by the checks have already been retried automatically (separate sentences, "
                     'different parameters): you can see them with "Show remaining". At the next "Analyze and '
                     "translate\" I'll retry them with new parameters." % n)
    if e.extra.get("rifatte"):
        lines.append("I redid %d saved translations that weren't right." % e.extra["rifatte"])
    if e.extra.get("cache_corrette"):
        lines.append("I fixed %d wrong phrases in the translations saved in the game." % e.extra["cache_corrette"])
    if e.extra.get("correzioni_scritte"):
        n = e.extra["correzioni_scritte"]
        lines.append("I wrote %s to the game." % ("your correction" if n == 1 else "your %d corrections" % n))
    if e.extra.get("corrette_a_mano_scartate"):
        n = e.extra["corrette_a_mano_scartate"]
        lines.append("One translation fixed by hand can't be written to the game (lines or characters the game can't "
                     'display): fix it with "Fix translations" (list "Not written to the game").' if n == 1
                     else "%d translations fixed by hand can't be written to the game (lines or characters the game "
                          "can't display): fix them with \"Fix translations\" (list \"Not written to the game\")."
                     % n)
    if e.extra.get("adattate_font"):
        n = e.extra["adattate_font"]
        lines.append("%s letters that the game's fonts can't draw: I've replaced them (e.g., \"è\" becomes \"e\")."
                     % ("A text had" if n == 1 else "%d texts had" % n))
    if e.extra.get("accorciate"):
        n = e.extra["accorciate"]
        lines.append("A label was too long for its space in the game: I asked for a shorter one." if n == 1
                     else "%d labels were too long for their space in the game: I asked for shorter ones." % n)
    if e.extra.get("troppo_lunghe"):
        n = e.extra["troppo_lunghe"]
        if n == 1:
            lines.append('A label might not fit its space in the game: you can see it in "Fix translations" ("Too '
                         'long" list).')
        else:
            lines.append('%d labels might not fit their space in the game: you can see them in "Fix translations" '
                         '("Too long" list).' % n)
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
        return ['The game is back to how it was. The translations stay saved: "Analyze and translate" puts them back '
                "right away, without translating them again."]
    lines = [e.reason] if e.reason else []
    if e.code == "GIOCO_APERTO":
        lines.append("Close the game and press \"Restore originals\" again. I haven't touched anything.")
    elif e.code == "PERMESSI":
        lines.append("Close the game and any programs that might use its files (including your antivirus), then press "
                     '"Restore originals" again.')
    elif e.code == "ERRORE":
        lines.append("You can create a report to send to anyone who helps you with PatchSmith.")
    return lines


def _problem_text(e, op, other=True):
    """Nothing (or not everything) was written: the reason and the next step."""
    reason = e.reason
    if e.code == "NON_SUPPORTATO":
        lines = ["This game cannot yet be translated: %s" % _lowercase(reason) if reason else
                 "This game cannot yet be translated."]
        if "sperimentale" in e.actions:
            lines.append("However, I've found some files containing text; I can try to translate them experimentally "
                         "(with automatic backups of the originals).")
        else:
            lines.append("I haven't made any changes to the game. If you'd like, create a report to send to the "
                         "PatchSmith developers.")
        return lines
    lines = [reason] if reason else []
    if e.code == "ANTICHEAT":
        lines.append("Just to be safe, I haven't made any changes.")
    elif e.code == "GIOCO_APERTO" and "riapplica" in e.actions:
        lines.append('Close the game and press "Reapply": the translations are already done, no need to translate them '
                     "again.")
    elif e.code == "GIOCO_APERTO":
        lines.append('Close the game and press "%s" again (or "Try again").' % op)
    elif e.code == "UFFICIALE":
        lines.append("Choose it from the game's language menu: the official translation is better than the automatic "
                     "one." + (' If you still want the machine translation, press "Translate anyway".'
                                      if "traduci_comunque" in e.actions else ""))
    elif e.code == "TRADUTTORE_BLOCCATO":
        lines.append("The translations done so far stay saved. " + (
            'You can choose another translator (the local translator has no blocks) and press "Analyze and translate" '
            "again." if other else 'When the translator is available again, press "Analyze and translate" again: '
                                         "it resumes from where it stopped."))
    elif e.code == "TRADUTTORE_ASSENTE":
        lines.append("I haven't made any changes to the game.")
    elif e.code == "PARZIALE":
        lines.append(_counts(e))
        lines += _attempts_text(e)
        if other:
            lines.append('You can try another translator (the local translator has no blocks) and press "Analyze and '
                         'translate" again.')
    elif e.code == "PERMESSI":
        lines.append("Close the game and any programs that might use its files (including your antivirus), then try "
                     "again. If the game is in the Program Files folder, Windows might be protecting it.")
    elif e.code == "ERRORE":
        lines.append("You can create a report to send to anyone who helps you with PatchSmith.")
    return lines


def _lowercase(text):
    """The sentence after the colon: lowercase initial, except acronyms (e.g. 'IL2CPP')."""
    return text if text[1:2].isupper() else text[:1].lower() + text[1:]


def remaining_text(e):
    """The text of the "Show remaining" window: the first originals left, one per line."""
    lines = ["- " + " ".join(t.split())[:90] for t in e.remaining_examples]
    header = ("Texts left untranslated: %d. Here are %d:" % (e.remaining, len(lines)) if e.remaining > len(lines)
                    else "Texts left untranslated: %d." % e.remaining)
    return header + "\n\n" + "\n".join(lines)
