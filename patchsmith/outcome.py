"""Outcome of an operation (Analyze and translate, Reapply, Restore) in a form the interface can read:
a code, the counts, the reason in one sentence and the possible actions afterwards. From here also the text of the
windows the interface shows at the end, one per code, with the buttons for the next step.

The functions of automatico still return the same values as before (the command line and the tests use them): the
outcome is in outcome["outcome"], in the engine detection dictionary (or in the one passed to riapplica/ripristina).
"""
from dataclasses import asdict, dataclass, field

CODES = ("OK", "PARTIAL", "NOTHING_TO_DO", "OFFICIAL", "NOT_SUPPORTED", "ANTICHEAT", "GAME_OPEN",
          "TRANSLATOR_BLOCKED", "TRANSLATOR_MISSING", "ALREADY_ORIGINAL", "PERMISSIONS", "WRONG_FOLDER", "ERROR")
# actions proposed after an outcome (each becomes a button, except scegli_lingua which is an instruction)
ACTIONS = ("close_game", "use_local", "install_local", "show_remaining", "create_report", "reapply",
          "choose_language", "translate_anyway", "experimental", "restore", "translate", "retranslate", "fix")
MAX_EXAMPLES = 20  # texts left untranslated to show (the first ones)
LIVE = {"local": "PatchSmith, with the local translator (on your PC)",
            "online": "PatchSmith, with the online translator (with pauses that avoid getting blocked)",
            "google": "XUnity, with free Google access (via the internet)",
            "none": "none, they remain in their original form"}
# why PatchSmith is not the one translating the new sentences (Outcome.extra["live_note"], see automatic._live)
LIVE_NOTES = {
    "no_translator": "Right now no translator is available: the local translator isn't installed and the online "
                        "ones are paused. Install the local translator: from the next time you start the game, it will "
                        "translate the new phrases.",
    "plugin_google": "I couldn't set up the PatchSmith translator in the game: XUnity asks Google for the new phrases, "
                     "without PatchSmith's pauses.",
    "plugin_none": "I haven't been able to set up the PatchSmith translator in the game, and Google Translate is on "
                      "hold: any new phrases will remain in their original language. You can create a report to send "
                      "to anyone who is helping you with PatchSmith.",
}


@dataclass
class Outcome:
    """code: one of CODES. engine: unity, unity-localization, unreal, xna, gamemaker, web... (None if unknown).
    written: in this operation at least one translation was written to the game (for Unity: BepInEx and XUnity
    installed, which also translate while playing). translated_items/remaining: texts translated and left
    untranslated; remaining_examples: the first originals left. live (Unity): who translates the new sentences while
    playing, 'local', 'google', 'none' (None: there is no live translation). reason: one sentence for the user.
    actions: from ACTIONS. extra: data for the interface's questions (official language, added language...) and what
    PatchSmith has already redone on its own (retried, redone, cache_fixed, hand_fixes_discarded, font_adapted,
    shortened, too_long: see automatic._extra_attempts)."""
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
        if self.remaining and self.remaining_examples and "show_remaining" not in self.actions:
            self.actions.append("show_remaining")

    def as_dict(self):
        return asdict(self)

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
    """Records the outcome in outcome["outcome"] (the detection dictionary, or the one given by the interface) and
    returns the dictionary. With esito None it does nothing."""
    if outcome is not None:
        outcome["outcome"] = Outcome(code, reason, **fields)
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
        return Outcome("TRANSLATOR_BLOCKED", message, actions=["use_local"])
    if isinstance(error, SysError):
        return Outcome("GAME_OPEN", message, actions=["close_game"])
    if isinstance(error, PermissionError):
        return Outcome("PERMISSIONS", "Windows doesn't allow me to modify %s (%s)." % (
            error.filename or "a game file", error.strerror or "access denied"),
            actions=["close_game", "create_report"])
    if _known_error(error):
        return Outcome("ERROR", message or type(error).__name__, actions=["create_report"])
    return Outcome("ERROR", message, actions=["create_report"],
                 extra={"unexpected": True, "kind": type(error).__name__})


def _actions_for(code):
    return {"GAME_OPEN": ["close_game"], "TRANSLATOR_MISSING": ["install_local"],
            "TRANSLATOR_BLOCKED": ["use_local"], "NOT_SUPPORTED": ["create_report"],
            "PERMISSIONS": ["close_game", "create_report"]}.get(code, [])


def _known_error(error):
    """True for the errors with a message written for the user (the same ones the command line shows as
    'ERROR: ...')."""
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
    "OK": "Translation installed", "PARTIAL": "Translation partially installed", "NOTHING_TO_DO": "Nothing to do",
    "OFFICIAL": "The game already has your language", "NOT_SUPPORTED": "Game not yet supported",
    "ANTICHEAT": "Game with anti-cheat", "GAME_OPEN": "The game is open",
    "TRANSLATOR_BLOCKED": "Translation interrupted", "TRANSLATOR_MISSING": "Translator not ready",
    "ALREADY_ORIGINAL": "Game already original", "PERMISSIONS": "Locked files", "WRONG_FOLDER": "Wrong folder",
    "ERROR": "Operation failed",
}
OPERATION_TITLES = {("reapply", "OK"): "Translation reapplied", ("retranslate", "OK"): "Translations redone",
                     ("restore", "OK"): "Originals restored",
                     ("restore", "PARTIAL"): "Almost complete restoration",
                     ("check", "OK"): "Check after playing", ("check", "PARTIAL"): "Something to do",
                     ("check", "NOTHING_TO_DO"): "Nothing to check"}
STATE = {
    "OK": "completed.", "PARTIAL": "partially completed.", "NOTHING_TO_DO": "nothing to do.",
    "OFFICIAL": "the game already has the official language.", "NOT_SUPPORTED": "game not supported yet.",
    "ANTICHEAT": "game with anti-cheat, nothing changed.", "GAME_OPEN": "the game is open.",
    "TRANSLATOR_BLOCKED": "translator stopped.", "TRANSLATOR_MISSING": "translator not ready.",
    "ALREADY_ORIGINAL": "the game already has its original files.", "PERMISSIONS": "files locked.",
    "WRONG_FOLDER": "folder to change.", "ERROR": "failed.",
}
KINDS = {"OK": "info", "PARTIAL": "warning", "NOTHING_TO_DO": "info", "OFFICIAL": "info", "ALREADY_ORIGINAL": "info",
        "ERROR": "error"}
BUTTONS = {"show_remaining": "Show remaining", "create_report": "Create report", "reapply": "Reapply",
            "use_local": "Use the local translator", "install_local": "Install the local translator",
            "translate_anyway": "Translate anyway", "close_game": "Try again",
            "experimental": "Try it out experimentally", "restore": "Restore originals",
            "translate": "Analyze and translate", "retranslate": "Re-translate the phrases", "fix": "Fix translations"}
OPERATION_NAMES = {"translate": "Analyze and translate", "reapply": "Reapply",
                   "restore": "Restore originals", "check": "Check after playing",
                   "retranslate": "Re-translate"}
# operations after which, with texts left untranslated or translations the game cannot show, "Fix translations"
# is proposed (the window with the right list already chosen)
WITH_FIXES = ("translate", "reapply", "retranslate")
# status of the problems of the after-play check (checks.Problem.state) -> group in the window
CHECK_GROUPS = (("fixed", "Fixed automatically:"),
                    ("to_do", "To do:"), ("postponed", "To fix when the game is closed:"),
                    ("info", "Good to know:"))
GROUP_STATES = {"fixed": "fixed", "failed": "to_do", "already_tried": "to_do", "to_do": "to_do",
                "postponed": "postponed", "to_fix": "postponed", "info": "info"}
# operations that do not touch the game's files (installation of the local translator, report, window
# commands): no "Try again" (it would repeat the last operation on the game) nor advice about the open game
OUTSIDE_GAME = ("install", "report", "interface")


def title_text(e, operation="translate"):
    if operation == "report":
        return "Report not created"
    if e.extra.get("unexpected"):
        return "Unexpected error"
    if e.code == "OK" and not e.written and operation == "translate":
        return "Done"
    return OPERATION_TITLES.get((operation, e.code)) or TITLES[e.code]


def _how_to_see_it(e):
    added_lang = e.extra.get("added_language")
    if added_lang:
        return ('The translation is a new language for the game: "%s". The original remains as it was and you can '
                'still choose it.\n\n%s\n\n"Restore originals" removes the added language.' % (
                    added_lang["name"], "I've already set it up in the game options: just launch it and it will work."
                    if added_lang.get("is_set") else
                    'In the game, open Options > Language and select "%s" using the arrows.'
                    % added_lang["name"].upper()))
    return HOW_TO_SEE_IT.get(e.engine, HOW_TO_SEE_IT[None])


def _counts(e):
    if not (e.translated_items or e.remaining):
        return ""
    if not e.remaining:
        return "Translated texts: %d." % e.translated_items
    return "Translated texts: %d, left untranslated: %d." % (e.translated_items, e.remaining)


def window(e, operation="translate", local_ready=False, current=None):
    """The end-of-operation window: {"title", "kind" (info/avviso/errore/domanda), "text",
    "buttons": [(label, action)], "state" (line under the log)}. The first button is "OK" (action None).
    local_ready: the local translator is installed (then 'use_local' chooses it, otherwise it installs it);
    attuale: the chosen translator (if it is already the local one, 'use_local' is not needed)."""
    op = OPERATION_NAMES.get(operation, operation)
    other = "use_local" in e.actions and not (current == "local" and local_ready)  # propose another translator
    lines = []
    if operation == "report":  # no log on disk nor report to propose: only the reason is given
        lines.append("I wasn't able to create the report: %s" % (
            "unexpected error (%s): %s" % (e.extra.get("kind", "?"), e.reason) if e.extra.get("unexpected")
            else e.reason or "unknown reason"))
    elif e.extra.get("unexpected"):
        lines.append("Unexpected error (%s): %s" % (e.extra.get("kind", "?"), e.reason or "no details"))
        lines.append("The technical details are in the saved log. You can create a report to send to whoever is "
                     "assisting you with PatchSmith.")
    elif operation == "check" and "checks" in e.extra:
        lines += check_text(e)
    elif operation in OUTSIDE_GAME:
        lines += [e.reason, "You can create a report to send to anyone who helps you with PatchSmith."]
    elif operation == "restore":
        lines += _restore_text(e)
    elif e.written and e.code in ("OK", "PARTIAL", "TRANSLATOR_BLOCKED", "GAME_OPEN"):
        lines += _written_text(e, op, other)
    elif e.code == "OK":
        lines += [e.reason or "Done.", _counts(e)] + _attempts_text(e)
    else:
        lines += _problem_text(e, op, other)
    if e.extra.get("retranslation") and operation == "retranslate":
        lines.insert(0, _retranslation_text(e.extra["retranslation"]))
    buttons = [("OK", None)]
    for action in e.actions:
        if action in ("use_local", "install_local"):
            if action == "use_local" and current == "local" and local_ready:
                continue  # it is already the chosen one
            # installed and not chosen: use it (installing it would do nothing); otherwise install it
            action = "use_local" if local_ready and current != "local" else "install_local"
        if operation in OUTSIDE_GAME and action == "close_game":
            continue
        if operation == "report" and action == "create_report":
            continue
        if action in BUTTONS and action not in [a for _, a in buttons]:
            if action == "show_remaining" and not e.remaining_examples:
                continue
            buttons.append((BUTTONS[action], action))
    if to_fix(e, operation) and "fix" not in [a for _, a in buttons]:
        buttons.append((BUTTONS["fix"], "fix"))
    kind = KINDS.get(e.code, "warning")
    if e.extra.get("unexpected") or operation == "report":
        kind = "error"
    return {"title": title_text(e, operation), "kind": kind, "text": "\n\n".join(r for r in lines if r),
            "buttons": buttons, "state": STATE[e.code]}


def to_fix(e, operation="translate"):
    """True if after this outcome it makes sense to propose "Fix translations": after Analyze and translate, Reapply or
    Re-translate, with texts left untranslated, translations the game cannot show or texts already retried without
    success (not for unexpected errors)."""
    if operation not in WITH_FIXES or e.extra.get("unexpected") or \
            e.code not in ("OK", "PARTIAL", "TRANSLATOR_BLOCKED", "GAME_OPEN"):
        return False
    return bool(e.remaining or e.extra.get("hand_fixes_discarded") or e.extra.get("retried")
                or (e.extra.get("retranslation") or {}).get("remaining_count") or e.extra.get("too_long"))


def fixes_filter(e):
    """The list to show in "Fix translations" when opened from this outcome (see correzioni.FILTRI)."""
    if e is not None and (e.extra.get("hand_fixes_discarded") or (
            e.extra.get("discarded") and e.remaining <= e.extra["discarded"] and not e.extra.get("retried"))):
        return "game"  # (only translations the game could not show were left out of the game)
    if e is not None and (e.remaining or e.extra.get("retried")):
        return "remaining"
    if e is not None and e.extra.get("too_long"):
        return "long"
    return None


def _retranslation_text(r):
    text = "I re-translated %d of %d texts." % (r.get("retranslated", 0), r.get("texts", 0))
    if r.get("remaining_count"):
        text += (" For the other %d, the previous translation remains in place (the new one didn't pass quality "
                 "control, or it was the same)."
                  % r["remaining_count"])
    if r.get("interrupted"):
        text += " The retranslation has stopped: %s" % r["interrupted"]
    return text


def _written_text(e, op, other=True):
    """Something is in the game: how to see it, the counts, who translates the new sentences and what to do for the
    rest."""
    lines = []
    if e.code == "GAME_OPEN":
        lines.append(e.reason or "The game is open.")
        lines.append('Close the game and press "Reapply": the translations are already done, no need to translate them '
                     "again.")
        return lines
    if e.code == "TRANSLATOR_BLOCKED":
        lines.append(e.reason)
    lines.append(_how_to_see_it(e))
    lines.append(_counts(e))
    if e.live:
        lines.append("New phrases while playing: %s." % LIVE.get(e.live, e.live))
        lines.append(LIVE_NOTES.get(e.extra.get("live_note"), ""))
    if e.code == "PARTIAL" and e.reason:
        lines.append(e.reason)
    lines += _attempts_text(e)
    if e.remaining and e.code in ("PARTIAL", "TRANSLATOR_BLOCKED"):
        if other:
            lines.append("For the remaining texts you can use another translator (the local translator has no blocks) "
                         'and press "Analyze and translate" again: the translations already done are kept.')
        elif fixes_filter(e) == "game":
            lines.append("You can fix the translations the game couldn't display yourself with \"Fix translations\" "
                         '("Not written to the game" list): when you save, PatchSmith writes them into the game on its '
                         "own.")
        else:
            lines.append('You can translate the untranslated texts yourself with "Fix translations" ("Left '
                         'untranslated" list%s): when you save, PatchSmith writes them to the game by itself.' % (
                             "; those that the game wouldn't be able to display are in \"Not written to the game\""
                             if e.extra.get("discarded") else ""))
    return lines


def _attempts_text(e):
    """What PatchSmith has already done on its own for the texts left (Outcome.extra, see automatic._extra_attempts):
    in place of the old "rilancia", which repeated the same questions and gave the same answers."""
    lines = []
    if e.extra.get("retried"):
        n = e.extra["retried"]
        # whole sentences, singular and plural (in pieces they cannot be translated into the interface language)
        lines.append("One text rejected by the checks has already been retried automatically (separate sentences, "
                     'different parameters): you can see it with "Show remaining". At the next "Analyze and translate" '
                     "I'll retry it with new parameters." if n == 1 else
                     "%d texts rejected by the checks have already been retried automatically (separate sentences, "
                     'different parameters): you can see them with "Show remaining". At the next "Analyze and '
                     "translate\" I'll retry them with new parameters." % n)
    if e.extra.get("redone"):
        lines.append("I redid %d saved translations that weren't right." % e.extra["redone"])
    if e.extra.get("cache_fixed"):
        lines.append("I fixed %d wrong phrases in the translations saved in the game." % e.extra["cache_fixed"])
    if e.extra.get("fixes_written"):
        n = e.extra["fixes_written"]
        lines.append("I wrote %s to the game." % ("your correction" if n == 1 else "your %d corrections" % n))
    if e.extra.get("hand_fixes_discarded"):
        n = e.extra["hand_fixes_discarded"]
        lines.append("One translation fixed by hand can't be written to the game (lines or characters the game can't "
                     'display): fix it with "Fix translations" (list "Not written to the game").' if n == 1
                     else "%d translations fixed by hand can't be written to the game (lines or characters the game "
                          "can't display): fix them with \"Fix translations\" (list \"Not written to the game\")."
                     % n)
    if e.extra.get("font_adapted"):
        n = e.extra["font_adapted"]
        lines.append("%s letters that the game's fonts can't draw: I've replaced them (e.g., \"è\" becomes \"e\")."
                     % ("A text had" if n == 1 else "%d texts had" % n))
    if e.extra.get("shortened"):
        n = e.extra["shortened"]
        lines.append("A label was too long for its space in the game: I asked for a shorter one." if n == 1
                     else "%d labels were too long for their space in the game: I asked for shorter ones." % n)
    if e.extra.get("too_long"):
        n = e.extra["too_long"]
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
    problems = e.extra.get("checks") or []
    for state, header in CHECK_GROUPS:
        entries = []
        for p in problems:
            if GROUP_STATES.get(p.get("state")) != state:
                continue
            entry = "- " + p.get("message", "")
            if p.get("detail") and p.get("state") in ("fixed", "failed", "already_tried"):
                entry += " (%s)" % p["detail"]
            entries.append(entry)
        if entries:
            lines.append(header + "\n" + "\n".join(entries))
    return lines


def _restore_text(e):
    if e.code == "OK":
        return ['The game is back to how it was. The translations stay saved: "Analyze and translate" puts them back '
                "right away, without translating them again."]
    lines = [e.reason] if e.reason else []
    if e.code == "GAME_OPEN":
        lines.append("Close the game and press \"Restore originals\" again. I haven't touched anything.")
    elif e.code == "PERMISSIONS":
        lines.append("Close the game and any programs that might use its files (including your antivirus), then press "
                     '"Restore originals" again.')
    elif e.code == "ERROR":
        lines.append("You can create a report to send to anyone who helps you with PatchSmith.")
    return lines


def _problem_text(e, op, other=True):
    """Nothing (or not everything) was written: the reason and the next step."""
    reason = e.reason
    if e.code == "NOT_SUPPORTED":
        lines = ["This game cannot yet be translated: %s" % _lowercase(reason) if reason else
                 "This game cannot yet be translated."]
        if "experimental" in e.actions:
            lines.append("However, I've found some files containing text; I can try to translate them experimentally "
                         "(with automatic backups of the originals).")
        else:
            lines.append("I haven't made any changes to the game. If you'd like, create a report to send to the "
                         "PatchSmith developers.")
        return lines
    lines = [reason] if reason else []
    if e.code == "ANTICHEAT":
        lines.append("Just to be safe, I haven't made any changes.")
    elif e.code == "GAME_OPEN" and "reapply" in e.actions:
        lines.append('Close the game and press "Reapply": the translations are already done, no need to translate them '
                     "again.")
    elif e.code == "GAME_OPEN":
        lines.append('Close the game and press "%s" again (or "Try again").' % op)
    elif e.code == "OFFICIAL":
        lines.append("Choose it from the game's language menu: the official translation is better than the automatic "
                     "one." + (' If you still want the machine translation, press "Translate anyway".'
                                      if "translate_anyway" in e.actions else ""))
    elif e.code == "TRANSLATOR_BLOCKED":
        lines.append("The translations done so far stay saved. " + (
            'You can choose another translator (the local translator has no blocks) and press "Analyze and translate" '
            "again." if other else 'When the translator is available again, press "Analyze and translate" again: '
                                         "it resumes from where it stopped."))
    elif e.code == "TRANSLATOR_MISSING":
        lines.append("I haven't made any changes to the game.")
    elif e.code == "PARTIAL":
        lines.append(_counts(e))
        lines += _attempts_text(e)
        if other:
            lines.append('You can try another translator (the local translator has no blocks) and press "Analyze and '
                         'translate" again.')
    elif e.code == "PERMISSIONS":
        lines.append("Close the game and any programs that might use its files (including your antivirus), then try "
                     "again. If the game is in the Program Files folder, Windows might be protecting it.")
    elif e.code == "ERROR":
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
