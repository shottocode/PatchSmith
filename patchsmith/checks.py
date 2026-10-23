"""After-play check: what the game left behind since the last time PatchSmith wrote to it, and the safe fixes made
on their own. It starts on its own when PatchSmith opens (for games with new signals), with the "Re-translate"
button (the lines to redo) and from the command line ("check" command).

Signals read (read only):
- Unity: the Crash_* folders in %TEMP%\\<azienda>\\<prodotto>\\Crashes, Player.log and Player-prev.log in
  %USERPROFILE%\\AppData\\LocalLow\\<azienda>\\<prodotto> (company and product from <gioco>_Data/app.info),
  BepInEx/LogOutput.log, the configuration of BepInEx, of XUnity and of PatchSmith's translator, the state written by
  the plugin, the journal of the lines translated while playing (xunity_service) and the log of the live translator.
  The LocalLow and %TEMP% folders are the same for all copies of a game: a log counts only if it belongs to THIS
  copy (the game's path written inside it: "Mono path[0]" or "Symbol-SearchPath");
- games with translated files (GameMaker, web, Unity Localization...): the fingerprints of the files
  (.patchsmith-info) and, for GameMaker's added language, the language in the options saved by the game;
- Unreal: PatchSmith's pak and the game's log (%LOCALAPPDATA%\\<Project>\\Saved\\Logs); XNA: the satellites;
- the other engines: the crashes in the Windows Application log (events 1000 and 1026 of the game's executable).
Only the signals that arrived after PatchSmith's last write to the game count (installation, Reapply, a fix made by
this check).

Rules for automatic fixes (Problem.safe):
- only with the game closed (system.game_open): with the game open they are postponed to the next check;
- only once per signal: every attempt is kept in profiles/<nome>.checks.json (not <nome>.state.json, which
  project.py deletes as a file of versions 0.1-0.2); if the problem comes back, it stays "to do";
- reversible: they change only PatchSmith's files (BepInEx and XUnity configuration, plugin, translation cache,
  translation memory) or put back the saved translations (like "Reapply"); "Restore originals" removes everything
  as always;
- never the game's saves;
- every result goes into the log and the outcome (as_outcome: outcome.Outcome with the list of problems).
Fixes that remove the translation (the game crashes even after the fix) or that need a choice (new texts to
translate after an update) do not start on their own: they stay "to do", with the right button.
"""
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import (__version__, adapter_unity, adapter_unreal, adapter_xna, components,
               shared_file, interruption)
from . import language, system
from .adapter_webjson import write_atomic
from .backend import BackendError, create_backend
from .outcome import Outcome
from .project import INFO_SUFFIXES, _has_tag, _hash_file, read_info, memory_path

STATE_SUFFIX = ".controllo.json"  # profiles/<nome>.checks.json (automatic.list_profiles skips it)
SEVERITIES = ("errore", "avviso", "info")
# state of a problem after the check: fixed on its own, fix failed, fix already tried for the same signal (the
# problem came back), postponed (game open), to do (the user is needed), to fix (read only: it would be fixed on its
# own), just to know
STATES = ("trovato", "corretto", "non_riuscito", "gia_tentato", "rimandato", "da_fare", "da_correggere", "info")
MAX_BYTES = 2 * 1024 * 1024  # at most this much of each log is read (the start and the end)
MAX_HEAD = 256 * 1024  # the start of a large log (startup of Unity and BepInEx)
MAX_SAVED_ATTEMPTS = 20  # attempts kept for each fix
MAX_SENTENCES = 200  # journal lines re-translated per check ("Analyze and translate" retries the others)
EVENT_DAYS = 30  # crashes in the Windows log: at most from these last days
XUNITY_SIGNALS = (  # (expression, what it means) in the Unity and BepInEx logs
    (r"consecutive errors occurred\. Shutting down", "spento dopo troppi errori"),
    (r"Failed: '", "frasi non tradotte"),
    (r"SPAM DETECTED", "fermato dal controllo anti-spam di XUnity"),
    (r"GoogleTranslateEndpointV2", "errore di Google"),
)
MOD_STACK = ("BepInEx", "HarmonyX", "Harmony", "XUnity", "PatchSmith", "MonoMod")
KEY_DESCRIPTIONS = {  # keys of the XUnity and BepInEx configuration, in words for the player
    "Endpoint": "chi traduce le frasi nuove", "FallbackEndpoint": "chi traduce le frasi nuove",
    "Url": "chi traduce le frasi nuove", "EnableShortDelay": "limiti per le frasi nuove",
    "DisableSpamChecks": "limiti per le frasi nuove", "MaxCharactersPerTranslation": "limiti per le frasi nuove",
    "EnableBatching": "limiti per le frasi nuove", "Language": "lingue", "FromLanguage": "lingue",
    "EnableIMGUI": "testi disegnati dal gioco (IMGUI)", "EnableTextPathLogging": "opzioni di prova di XUnity",
    "EnableTranslationHelper": "opzioni di prova di XUnity", "CopyToClipboard": "opzioni di prova di XUnity",
    "Type": "avvio di BepInEx (Unity 6)", "WriteUnityLog": "registro delle lettere mancanti nei font del gioco",
}
PLUGIN_OUTCOMES = {"cfg-mancante": "manca il suo file di configurazione",
                "patchsmith-mancante": "PatchSmith non si trova piu' dove era",
                "python-mancante": "Python non si trova piu' dove era", "errore": "non e' partito"}


class CheckError(Exception):
    """A fix that cannot be made: the message is for the player."""


@dataclass
class Problem:
    """codice: the type (e.g. CRASH_AVVIO, LIVE, RIPRISTINATO_DAL_GIOCO). gravita: errore/avviso/info. messaggio:
    a sentence for the player. rimedio: the fix (function without arguments; returns a sentence, (riuscito, frase) or
    (riuscito, frase, action to offer if it is still to do)), or None. sicuro: the fix starts on its own (with the
    game closed, once per signal). segnale: what found it (a crash folder, a log with its date...): the same fix for
    the same signal is not repeated. azione: the interface button if it is still to do (outcome.ACTIONS). chiave: fixes
    with the same key are made only once (e.g. 'riconfigura' fixes configuration, version and live translator
    together). stato and dettaglio: how it went (STATES)."""
    code: str
    severity: str
    message: str
    remedy: object = None
    safe: bool = False
    signal: str = ""
    action: str = None
    key: str = None
    state: str = "trovato"
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        output = {"codice": self.code, "gravita": self.severity, "messaggio": self.message, "sicuro": self.safe,
                  "segnale": self.signal, "azione": self.action, "stato": self.state, "dettaglio": self.detail}
        if self.extra.get("frasi"):  # lines to re-translate (the "Re-translate N lines" button)
            output["frasi"] = self.extra["frasi"]
        return output


# --- check state (profiles/<nome>.checks.json) -----------------------------------------------------------

def state_path(profile_path):
    return os.path.splitext(os.path.abspath(profile_path))[0] + STATE_SUFFIX


def read_state(profile_path):
    """{ultimo_controllo, visti: {segnale: valore}, rimedi: {chiave: [{segnale, quando, ts, riuscito, dettaglio}]},
    journal_until, entrypoint_forzato, ultimo: {quando, corretti, to_do, problemi}}; empty if missing."""
    try:
        with open(state_path(profile_path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(profile_path, state):
    p = state_path(profile_path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    write_atomic(p, json.dumps(state, ensure_ascii=False, indent=2).encode("utf-8"))


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _when(instant):
    try:
        return datetime.datetime.fromtimestamp(instant).strftime("%d/%m alle %H:%M")
    except (OSError, OverflowError, ValueError, TypeError):
        return "?"


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except (OSError, TypeError, ValueError):
        return None


def _read(path, maximum=MAX_BYTES):
    """A log (text), or "" if it cannot be read. If it is larger than 'massimo', the start (MAX_HEAD: that is where
    "Mono path[0]" and "Chainloader startup complete" are) and the end (the last errors and the crash) are read: a
    long play session with many warnings must not look like a crash at startup, nor like a log of another copy of the
    game."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(0)
            if size <= maximum:
                data = f.read()
            else:
                head = f.read(min(MAX_HEAD, maximum // 2) // 2 * 2)
                start = size - (maximum - len(head))
                f.seek(start + start % 2)  # (even: UTF-16 logs stay readable)
                line_break = {b"\xff\xfe": b"\n\x00", b"\xfe\xff": b"\x00\n"}.get(head[:2], b"\n")
                data = head + line_break + f.read()
    except OSError:
        return ""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8-sig", "replace")


def _same(a, b):
    """True if the two paths are the same folder (case, slashes and trailing '/' do not matter)."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)).rstrip("\\/") == os.path.normcase(os.path.abspath(b)).rstrip("\\/")


def _inside(child, parent):
    child = os.path.normcase(os.path.abspath(child))
    parent = os.path.normcase(os.path.abspath(parent)).rstrip("\\/")
    return child == parent or child.startswith(parent + os.sep)


def _version_tuple(version):
    try:
        return tuple(int(x) for x in str(version).split("."))
    except (TypeError, ValueError):
        return (0,)


# --- context of a check -----------------------------------------------------------------------------------

class _Context:
    def __init__(self, path, profile, state, log, apply, translator, retranslate=True, events=None):
        self.path = os.path.abspath(path)
        self.profile = profile
        self.name = profile.get("nome") or os.path.splitext(os.path.basename(path))[0]
        self.title_text = os.path.basename(os.path.normpath(profile.get("cartella_gioco") or "")) or self.name
        self.engine = profile.get("motore")
        # (a game profile without a folder must not become the current folder)
        self.folder = os.path.abspath(profile["cartella_gioco"]) if profile.get("cartella_gioco") else ""
        self.a = profile.get("lingua_destinazione") or "it"
        self.state = state
        self.log = log
        self.apply = apply
        self.translator = translator
        self.retranslate = retranslate  # re-translate the journal's provisional lines now (see controlla)
        self.events = events  # Windows crashes already read (None: they are read if needed)
        self._is_open = None
        self.reference = 0.0
        self.journal_until = None  # up to where the live translator's journal has nothing left to redo

    def is_open(self):
        """True if the game is open (when in doubt it is considered open: better to postpone than to risk it)."""
        if self._is_open is None:
            try:
                self._is_open = bool(system.game_open(self.folder))
            except system.SysError:
                self._is_open = True
        return self._is_open

    def last_remedy(self):
        timestamps = [v.get("ts", 0) for entries in (self.state.get("rimedi") or {}).values()
                 if isinstance(entries, list) for v in entries if isinstance(v, dict)]
        return max(timestamps, default=0.0)


def _read_profile(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("profilo non valido")
    return data


# --- Unity: logs and crashes -------------------------------------------------------------------------------

def unity_logs(folder):
    """{'player': [Player.log, Player-prev.log], 'crash': Crashes folder, 'azienda', 'prodotto'} of the Unity game
    (folders shared by all copies of the game), or {} without app.info."""
    from . import language_unity
    info = adapter_unity.analyze(folder)
    if not info.get("dati"):
        return {}
    company, product = language_unity.company_product(info["dati"])
    if not company or not product:
        return {}
    user = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    locallow = os.path.join(user, "AppData", "LocalLow", company, product)
    temp = os.environ.get("TEMP") or os.environ.get("TMP") or tempfile.gettempdir()
    return {"player": [os.path.join(locallow, n) for n in ("Player.log", "Player-prev.log")],
            "crash": os.path.join(temp, company, product, "Crashes"), "azienda": company, "prodotto": product}


def of_this_copy(text, folder):
    """True if the Unity log belongs to this copy of the game, False if to another one, None if unknown. It looks at
    'Mono path[0]' (<gioco>/<exe>_Data/Managed), 'Discovering subsystems at path' (<gioco>/<exe>_Data/
    UnitySubsystems: also in IL2CPP games) or, in crash logs, 'Symbol-SearchPath'."""
    m = re.search(r"^Mono path\[0\] = '([^']*)'", text, re.M)
    if m:
        managed = m.group(1).replace("/", os.sep)
        return _same(os.path.dirname(os.path.dirname(managed)), folder)
    m = re.search(r"Discovering subsystems at path (.+?)[\\/]UnitySubsystems\s*$", text, re.M)
    if m:
        return _same(os.path.dirname(m.group(1).replace("/", os.sep)), folder)
    m = re.search(r"Symbol-SearchPath: '([^']*)'", text)
    if m:
        entries = [v for v in m.group(1).split(";") if v and v != "."]
        if entries:
            return any(_same(v, folder) for v in entries)
    return None


def unity_crash(logs):
    """[(folder name, time, path of the Player.log)] of the Unity crashes (all copies of the game)."""
    folder = logs.get("crash")
    try:
        names = sorted(os.listdir(folder)) if folder else []
    except OSError:
        return []
    output = []
    for name in names:
        d = os.path.join(folder, name)
        if not name.startswith("Crash_") or not os.path.isdir(d):
            continue
        log_file = os.path.join(d, "Player.log")
        instant = _mtime(log_file) or _mtime(d)
        if instant:
            output.append((name, instant, log_file))
    return output


# TextMeshPro writes to the Unity log every character that a font (with its fallback fonts) lacks: in the game a little
# box or a space appears in its place. Old versions: "Character with ASCII value of 232 was not found..."
_FONT_MISSING = re.compile(rb"Unicode value \\[uU]([0-9A-Fa-f]{4,8}) was not found in the \[([^\]\r\n]{0,200})\] "
                            rb"font asset")
_FONT_MISSING_OLD = re.compile(rb"Character with ASCII value of (\d{1,7}) was not found in the Font Asset")
MAX_MB_FONT_LOG = 256  # missing characters are searched for in at most these MB of a log (the whole file)


def missing_chars(paths):
    """{character: {font names}} of the characters TextMeshPro did not find in the game's fonts (Unity and BepInEx
    logs, read whole in pieces: the warnings can be in the middle of a large log). Read only."""
    found = {}
    for path in paths:
        try:
            with open(path, "rb") as f:
                tail, read_items = b"", 0
                while read_items < MAX_MB_FONT_LOG * 1024 * 1024:
                    piece = f.read(16 * 1024 * 1024)
                    if not piece:
                        break
                    read_items += len(piece)
                    data = tail + piece
                    tail = data[-512:]
                    if b"was not found in the" not in data:
                        continue
                    for m in _FONT_MISSING.finditer(data):
                        code, font = int(m.group(1), 16), m.group(2).decode("utf-8", "replace")
                        if 0x20 < code < 0x110000 and not 0xD800 <= code <= 0xDFFF and not chr(code).isspace():
                            found.setdefault(chr(code), set()).add(font)
                    for m in _FONT_MISSING_OLD.finditer(data):
                        code = int(m.group(1))
                        if 0x20 < code < 0x110000 and not 0xD800 <= code <= 0xDFFF:
                            found.setdefault(chr(code), set()).add("")
        except OSError:
            continue
    return found


def _player_logs(c):
    """[path] of the Unity Player.log files of this copy of the game written after PatchSmith's last write."""
    output = []
    try:
        logs = unity_logs(c.folder)
    except OSError:
        return output
    for p in logs.get("player", []):
        t = _mtime(p)
        if t and t > c.reference and of_this_copy(_read(p, MAX_HEAD), c.folder):
            output.append(p)
    return output


def _chars_list(chars):
    return ", ".join("«%s»" % ch for ch in sorted(chars)[:12]) + (" e altri" if len(chars) > 12 else "")


def _verify_font(c, logs):
    """Letters of the translations that the game's fonts cannot draw (TextMeshPro warnings in the logs): they are
    noted in the game profile (profilo["caratteri_mancanti"]: from then on every translation written to the game
    adapts them, è -> e') and the ones already written are adapted at once: for XUnity the cache lines written by
    PatchSmith (those changed by hand stay), for Unity Localization with "Reapply". Characters that appear only in the
    game's texts (not in the translations) do not count. An unexpected error here (unreadable translation memory or
    cache) only ends up in the log: it must not hide the game's other problems (crashes, configuration)."""
    try:
        return _missing_letters(c, logs)
    except Exception as e:  # noqa: BLE001 - an extra check
        c.log("  ! Controllo delle lettere dei font non riuscito (%s: %s)" % (type(e).__name__, e))
        return []


def _missing_letters(c, logs):
    found = missing_chars(logs)
    if not found:
        return []
    already = set(c.profile.get("caratteri_mancanti") or "")
    all_ = already | set(found)

    def read_memory(path):  # (missing or corrupted: as if empty)
        try:
            data = shared_file.read_json(path)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    memory = read_memory(memory_path(c.profile))
    lines, by_hand = {}, 0
    if c.engine == "unity":
        from .auto import our_values
        from .characters import adapt_without
        _, a = _unity_languages(c)
        entries = adapter_unity.xunity_cache_entries(c.folder, a)
        our_translations = None
        for t, v in entries.items():
            if t in language.ENDONYMS or not any(ch in all_ for ch in v):
                continue
            if our_translations is None:
                machine = read_memory(memory_path(c.profile)[:-len(".json")] + ".automatiche.json")
                our_translations = our_values(c.profile, memory, machine)[0]
            new_value = adapt_without(v, all_, a)
            if v in our_translations.get(t, ()) and new_value and new_value != v:
                lines[t] = (v, new_value)
            else:
                by_hand += 1
        used = {ch for v in entries.values() for ch in v if ch in found}
    else:
        used = {ch for v in memory.values() if isinstance(v, str) for ch in v if ch in found}
    new_items = used - already
    if not lines and not new_items:
        return []  # only in the game's texts, or already adapted
    shown_chars = new_items or {ch for v, _ in lines.values() for ch in v if ch in all_}
    message = ("Nel gioco alcune lettere delle traduzioni non si vedono (al loro posto un quadratino o uno spazio): "
                 "i font del gioco non hanno %s. Le sostituisco nelle frasi tradotte (es. è diventa e') e da ora le "
                 "traduzioni scritte nel gioco le evitano." % _chars_list(shown_chars))
    # whole sentences, singular and plural (pieces cannot be translated into the interface language)
    if by_hand == 1:
        message += (" Una frase con quelle lettere non la posso cambiare da solo (cambiata a mano, o senza una "
                      "lettera simile): correggila con \"Correggi traduzioni\".")
    elif by_hand:
        message += (" %d frasi con quelle lettere non le posso cambiare da solo (cambiate a mano, o senza una "
                      "lettera simile): correggile con \"Correggi traduzioni\"." % by_hand)
    return [Problem("FONT_MANCANTI", "avviso", message, remedy=lambda: _adapt_font(c, set(found), lines),
                     safe=True, signal="font:" + "".join(sorted(shown_chars | new_items)), key="font",
                     action="correggi" if by_hand else "crea_rapporto",
                     detail="font senza questi caratteri: %s" % "; ".join(
                         "%s U+%04X in %s" % (ch, ord(ch), ", ".join(sorted(f for f in found[ch] if f)) or "?")
                         for ch in sorted(found)[:12]))]


def _adapt_font(c, chars, lines):
    """The fix for FONT_MANCANTI: the characters in the game profile, then the translations already written adapted."""
    from . import scan
    profile = _read_profile(c.path)
    profile["caratteri_mancanti"] = "".join(sorted(set(profile.get("caratteri_mancanti") or "") | set(chars)))
    scan.save_json(c.path, profile)
    c.profile["caratteri_mancanti"] = profile["caratteri_mancanti"]
    if c.engine == "unity":
        _, a = _unity_languages(c)
        counts = {}
        adapter_unity.write_xunity_cache(c.folder, a, {t: new_value for t, (_, new_value) in lines.items()}, c.log,
                                          substitute={t: old_folder for t, (old_folder, _) in lines.items()},
                                          counts=counts)
        n = counts.get("sostituite", 0)
        return True, ("una frase della cache adattata ai font del gioco" if n == 1 else
                      "%d frasi della cache adattate ai font del gioco" % n)
    success, text = _reapply(c)
    return success, ("traduzioni riscritte senza le lettere che i font non hanno (%s)" % text) if success else text


def _windows_crash(c, since):
    """The game's crashes in the Windows Application log after 'dal' (events already read or read now)."""
    try:
        events = c.events if c.events is not None else crash_events(since)
        return game_crash(c.folder, events, since)
    except Exception:  # noqa: BLE001 - the Windows log is just an extra clue
        return []


def _stack(text):
    """The part of the crash log after 'Crash!!!' (the call stack)."""
    i = text.find("Crash!!!")
    return text[i:] if i >= 0 else text


# --- Unity: the checks -----------------------------------------------------------------------------------

def _unity_languages(c):
    """(game language, target language) for XUnity: the one written in its configuration, otherwise the game
    profile's (English if it is 'auto')."""
    ini = adapter_unity.read_ini(os.path.join(c.folder, *adapter_unity.CONFIG_XUNITY.split("/")))
    src_lang = (ini.get("General") or {}).get("FromLanguage") or c.profile.get("lingua_origine") or "en"
    if src_lang == language.AUTO:
        src_lang = "en"
    return src_lang, (ini.get("General") or {}).get("Language") or c.a


def _live_choice(profile):
    from .auto import LIVE_CHOICES
    return profile.get("dal_vivo") if profile.get("dal_vivo") in LIVE_CHOICES else "servizio"


def _reconfigure(c):
    """Fixes the Unity installation with the current code: missing files (from the components already downloaded),
    translator of new lines as the game profile wants (normally PatchSmith's: plugin and command redone),
    configuration of XUnity and BepInEx, version in the manifest. Like a part of "Reapply", without touching the
    translations."""
    from .auto import _live
    from .outcome import LIVE
    manifest = adapter_unity.read_manifest(c.folder)
    if manifest is None:
        raise CheckError("l'elenco dei file installati da PatchSmith non e' valido")
    info = adapter_unity.analyze(c.folder)
    missing = [rel for rel in manifest["file"] if not os.path.exists(os.path.join(c.folder, *rel.split("/")))]
    packages = ["bepinex5_%s" % info.get("architettura"), "xunity_bepinex5"]
    if missing and not all(n in components.CATALOG and components.is_available(n) for n in packages):
        raise CheckError("per rimettere i file mancanti servono i componenti da scaricare: premi 'Analizza e "
                              "traduci'")
    choice = _live_choice(c.profile)
    local = c.path if choice == "servizio" else ("nessuno" if choice == "nessuno" else False)
    src_lang, a = _unity_languages(c)
    adapter_unity._update(c.folder, manifest, info, src_lang, a, c.log, local, c.name)
    if c.state.get("entrypoint_forzato"):
        adapter_unity.bepinex_entrypoint(c.folder, None, force=True)
    kind, note = _live(c.folder, c.profile, local)
    text = "frasi nuove mentre giochi: %s" % LIVE.get(kind, kind or "nessuno")
    if note == "senza_traduttore":  # PatchSmith's translator is in the game, but right now it has no translators
        return False, "il traduttore di PatchSmith e' pronto nel gioco, ma adesso non ha un traduttore disponibile " \
                      "(quello locale non e' installato e quelli online sono in pausa)", "installa_locale"
    if local and kind not in ("locale", "online"):
        return False, "non sono riuscito a preparare il traduttore di PatchSmith nel gioco (%s)" % text
    return True, text


def _entrypoint(c):
    adapter_unity.bepinex_entrypoint(c.folder, None, force=True)
    c.state["entrypoint_forzato"] = True
    return "BepInEx ora parte quando il gioco e' pronto (al primo MonoBehaviour)"


def _verify_unity(c):
    problems = []
    folder = c.folder
    manifest = adapter_unity.read_manifest(folder)
    if manifest is None:
        return [Problem("MANIFESTO", "avviso", "Il file con l'elenco di quello che PatchSmith ha installato nel gioco "
                         "non e' valido: per sicurezza non correggo nulla da solo.", action="crea_rapporto",
                         signal="manifesto")]
    if manifest.get("in_corso"):  # as for "Analyze and translate": a half-done installation does not fix itself
        return [Problem("INSTALLAZIONE_INTERROTTA", "errore", "L'installazione della traduzione si e' interrotta a "
                         "meta': premi \"Ripristina originali\" e poi \"Analizza e traduci\" per rifarla.",
                         action="ripristina", signal="in_corso")]
    ref = c.reference
    logs = unity_logs(folder)
    bepinex_log_path = os.path.join(folder, *adapter_unity.BEPINEX_LOG.split("/"))
    t_log = _mtime(bepinex_log_path)
    log_text = _read(bepinex_log_path) if t_log and t_log > ref else ""

    # 1) crashes after PatchSmith's last write (only those of this copy of the game)
    recent = []
    for name, instant, log_file in unity_crash(logs):
        if instant <= ref:
            continue
        text = _read(log_file)
        if of_this_copy(text, folder):
            recent.append((instant, name, text))
    incomplete = bool(log_text.strip()) and "Chainloader startup complete" not in log_text and not c.is_open()
    # a real crash besides the incomplete log: also the Windows Application log (events 1000 and 1026)
    windows = _windows_crash(c, ref) if incomplete and not recent else []
    if recent or incomplete:
        most_recent = max(recent) if recent else None
        text = most_recent[2] if most_recent else log_text
        instant = most_recent[0] if most_recent else (max(e["istante"] for e in windows) if windows else t_log)
        when = _when(instant)
        repeats = "%d volte, l'ultima il %s" % (len(recent), when) if len(recent) > 1 else "il %s" % when
        signature = "Chainloader:Start" in text and "Application:.cctor" in text
        signal = "crash:%s" % most_recent[1] if most_recent else "bepinex:%d" % int(t_log)
        forced = bool(c.state.get("entrypoint_forzato"))  # already fixed once by a check
        if (signature or not most_recent) and adapter_unity.current_entrypoint(folder) != "MonoBehaviour":
            problems.append(Problem(
                "CRASH_AVVIO", "errore", "Il gioco si e' chiuso all'avvio (%s) mentre partiva BepInEx, il programma che "
                "carica la traduzione: succede con alcuni giochi Unity. Lo faccio partire piu' tardi, quando il gioco "
                "e' pronto." % repeats, remedy=lambda: _entrypoint(c), safe=True, signal=signal,
                action="ripristina"))
        elif (most_recent and (signature or any(k in _stack(text) for k in MOD_STACK))) or windows:
            # "does not accept mods" only with a real crash (Unity crash folder or Windows log) while BepInEx was
            # starting or with BepInEx in the stack: an incomplete log alone is not enough
            problems.append(Problem(
                "CRASH_CON_MOD", "errore", "Il gioco si e' chiuso all'improvviso (%s) e la causa sembra BepInEx, il "
                "programma che carica la traduzione%s: questo gioco probabilmente non accetta mod. Premi \"Ripristina "
                "originali\" per giocare senza traduzione (le traduzioni restano salvate) e crea un rapporto." % (
                    repeats, ", anche dopo la correzione che lo fa partire piu' tardi" if forced else ""),
                remedy=lambda: _remove_unity(c), safe=False, signal=signal, action="ripristina"))
        elif most_recent:
            problems.append(Problem(
                "CRASH", "avviso", "Il gioco si e' chiuso all'improvviso (%s), ma non sembra colpa della traduzione. Se "
                "succede ancora, prova \"Ripristina originali\" per vedere se senza traduzione va." % repeats,
                signal=signal, action="ripristina"))
        else:
            # BepInEx already starts at the first MonoBehaviour and there is no trace of a crash: usually the game was
            # closed while loading. Just to know
            problems.append(Problem(
                "AVVIO_INCOMPLETO", "info", "All'ultima partita (%s) BepInEx, il programma che carica la traduzione, "
                "non ha finito di partire, ma non c'e' traccia di un crash: forse il gioco e' stato chiuso mentre "
                "caricava. Se invece si chiude da solo all'avvio, premi \"Ripristina originali\" e crea un rapporto."
                % when, signal=signal))

    # 2) the translator of new lines stopped during the play session (XUnity: errors, anti-spam, Google)
    log_sources = []
    for log_file in logs.get("player", []):
        t = _mtime(log_file)
        if t and t > ref:
            text = _read(log_file)
            if of_this_copy(text, folder):
                log_sources.append((log_file, t, text))
    if log_text:
        log_sources.append((bepinex_log_path, t_log, log_text))
    found, failed, signals = set(), [], []
    for log_file, t, text in log_sources:
        for expression, meaning in XUNITY_SIGNALS:
            if re.search(expression, text):
                found.add(meaning)
                signals.append("%s@%d" % (os.path.basename(log_file), int(t)))
        failed += re.findall(r"Failed: '((?:[^'\\]|\\.){1,80})'", text)
    if found:
        kind = adapter_unity.live_translator(folder)
        count = ((" (una frase rimasta in originale: \"%s\")" % failed[0]) if len(set(failed)) == 1 else
                  (" (%d frasi rimaste in originale, es. \"%s\")" % (len(set(failed)), failed[0]))) if failed else ""
        signal = "xunity:" + ",".join(sorted(set(signals)))
        if kind != "servizio" or "errore di Google" in found:
            problems.append(Problem(
                "TRADUTTORE_FERMO", "avviso", "Durante l'ultima partita il traduttore delle frasi nuove si e' fermato "
                "(%s)%s. Da ora le frasi nuove le traduce PatchSmith, con i suoi traduttori e le loro pause."
                % (", ".join(sorted(found)), count), remedy=lambda: _reconfigure(c), safe=True, signal=signal,
                key="riconfigura", action="crea_rapporto"))
        else:
            from . import xunity_service
            no_live = not xunity_service.expected_translators(c.profile)
            problems.append(Problem(
                "TRADUTTORE_FERMO", "avviso", "Durante l'ultima partita il traduttore di PatchSmith non ha tradotto "
                "alcune frasi nuove (%s)%s.%s" % (", ".join(sorted(found)), count,
                                                  " Adesso non c'e' un traduttore disponibile: installa il traduttore "
                                                  "locale." if no_live else " Alla prossima \"Analizza e traduci\" le "
                                                  "riprovo."),
                signal=signal, action="installa_locale" if no_live else None))

    # 2b) letters of the translations that the game's fonts cannot draw (TextMeshPro warnings)
    problems += _verify_font(c, [log_file for log_file, _, _ in log_sources])

    # 3) installed files that are missing (e.g. removed by the antivirus)
    missing = [rel for rel in manifest["file"] if not os.path.exists(os.path.join(folder, *rel.split("/")))]
    if missing:
        problems.append(Problem(
            "FILE_MANCANTI", "errore", "Mancano %d file della traduzione nel gioco (es. %s): forse li ha tolti "
            "l'antivirus. Li rimetto." % (len(missing), missing[0]), remedy=lambda: _reconfigure(c), safe=True,
            signal="mancanti:%d:%s" % (len(missing), missing[0]), key="riconfigura", action="riapplica"))

    # 4) PatchSmith's translator cannot start with the game (changed paths, state written by the plugin)
    faults = []
    if adapter_unity.service_installed(folder):
        cfg = adapter_unity.read_service_command(folder)
        program = cfg.get("Programma")
        if not program or not os.path.exists(program):
            faults.append("Python non si trova piu' dove era")
        m = re.search(r'-I "([^"]+)"', cfg.get("Argomenti", ""))
        if not m or not os.path.exists(m.group(1)):
            faults.append("PatchSmith non si trova piu' dove era")
        m = re.search(r'--profilo "([^"]+)"', cfg.get("Argomenti", ""))
        if not m or not _same(m.group(1), c.path):
            faults.append("il profilo del gioco e' cambiato")
    plugin_state = adapter_unity.read_service_state(folder)
    t_plugin = _mtime(os.path.join(folder, *adapter_unity.SERVICE_STATE.split("/")))
    if plugin_state and t_plugin and t_plugin > ref and plugin_state.get("Esito", "avviato") != "avviato":
        faults.append("all'ultimo avvio del gioco %s"
                      % PLUGIN_OUTCOMES.get(plugin_state.get("Esito"), "non e' partito"))
    if faults:
        problems.append(Problem(
            "SERVIZIO", "avviso", "Il traduttore di PatchSmith per le frasi nuove non puo' partire con il gioco (%s): "
            "lo preparo di nuovo." % "; ".join(faults), remedy=lambda: _reconfigure(c), safe=True,
            signal="servizio:" + "|".join(faults), key="riconfigura", action="crea_rapporto"))

    # 5) who translates new lines, configuration and the version of PatchSmith that wrote it
    problems += _verify_config(c, manifest)

    # 6) lines translated while playing: those with a provisional answer are re-translated, lost ones are saved
    problems += _verify_journal(c)

    # 6b) fixes from "Fix translations" saved with the game open and not yet written to the cache
    problems += _verify_fixes(c)

    # 7) the live translator did not start or had no translators (service log)
    problems += _verify_service_log(c)
    return problems


def _verify_config(c, manifest):
    problems = []
    folder = c.folder
    choice = _live_choice(c.profile)
    kind = adapter_unity.live_translator(folder)
    google = adapter_unity.google_free() and choice != "nessuno"
    src_lang, a = _unity_languages(c)
    url = adapter_unity.service_url(folder) if choice == "servizio" else None
    expectations = adapter_unity._config_values(src_lang, a, adapter_unity.uses_imgui(folder), url, google)
    ini = adapter_unity.read_ini(os.path.join(folder, *adapter_unity.CONFIG_XUNITY.split("/")))
    mismatches = [(sec, k, (ini.get(sec) or {}).get(k), v) for sec, keys in expectations.items()
               for k, v in keys.items() if (ini.get(sec) or {}).get(k) != v]
    mismatched = (choice == "servizio" and kind != "servizio") or (choice == "google" and kind == "servizio") or \
        (choice == "nessuno" and kind not in ("nessuno", None))
    if mismatched:
        if choice == "nessuno":
            where = "le traduce ancora un traduttore, ma l'hai spento alla fine di 'Analizza e traduci'"
        elif kind == "google":
            where = "le chiede XUnity a Google da solo, senza le pause di PatchSmith"
            from .limiter import Limiter, when
            try:
                block = Limiter("google", log=None).current_block()
            except (OSError, ValueError, TypeError, AttributeError):
                block = None
            if block:
                where += ", ma Google e' in pausa (bloccato) %s: restano in originale" % (
                    "finche' non lo riattivi" if block.get("disattivato") else "fino " + when(block["fino"]))
        else:
            where = "nessuno le traduce (restano in originale)"
        problems.append(Problem(
            "DAL_VIVO", "avviso", "Frasi nuove mentre giochi: %s. %s" % (
                where, "Le passo al traduttore di PatchSmith." if choice == "servizio" else
                "Spengo il traduttore, come hai scelto." if choice == "nessuno" else
                "Le rimetto a Google, come chiede il profilo del gioco."),
            remedy=lambda: _reconfigure(c), safe=True, signal="dal_vivo:%s" % kind, key="riconfigura",
            action="crea_rapporto"))
        mismatches = [d for d in mismatches
                      if d[0] not in ("Service", "Custom") and d[1] != "MaxCharactersPerTranslation"]
    unity_version = manifest.get("versione_unity") or adapter_unity.analyze(folder).get("versione_unity")
    try:
        unity6 = int(str(unity_version or "").split(".")[0]) >= 6000
    except ValueError:
        unity6 = False
    entrypoint = adapter_unity.current_entrypoint(folder)
    if (unity6 or c.state.get("entrypoint_forzato")) and entrypoint != "MonoBehaviour":
        mismatches.append(("Preloader.Entrypoint", "Type", entrypoint, "MonoBehaviour"))
    if not adapter_unity.unity_log_enabled(folder):  # (older installations: _update turns it on)
        mismatches.append(("Logging.Disk", "WriteUnityLog", (adapter_unity.read_ini(os.path.join(
            folder, *adapter_unity.CONFIG_BEPINEX.split("/"))).get("Logging.Disk") or {}).get("WriteUnityLog"),
            "true"))
    version = manifest.get("patchsmith_versione")
    # another version, not only a previous one: with the first release the numbering restarted from 0.1.0 (the
    # development versions went up to 0.19.0), so a higher number can belong to an older version
    differs = _version_tuple(version) != _version_tuple(__version__)
    if mismatches:
        what = list(dict.fromkeys(KEY_DESCRIPTIONS.get(k, k) for _, k, _, _ in mismatches))
        # installed with another version: it is said here (the same fix also updates the version in the manifest),
        # without a second VERSION message for the same thing
        problems.append(Problem(
            "CONFIGURAZIONE", "avviso", "La configurazione della traduzione nel gioco non e' quella giusta (%s)%s: la "
            "rimetto a posto." % (", ".join(what), ", perche' e' stata installata con un'altra versione di "
                                  "PatchSmith%s" % (" (%s)" % version if version else "") if differs else ""),
            remedy=lambda: _reconfigure(c), safe=True,
            signal="config:" + "|".join(sorted("%s.%s=%s" % (s, k, v) for s, k, v, _ in mismatches)),
            key="riconfigura", action="crea_rapporto",
            detail="; ".join("%s.%s: %s invece di %s" % (s, k, v, exp) for s, k, v, exp in mismatches)))
    elif differs:
        problems.append(Problem(
            "VERSIONE", "info", "La traduzione e' stata installata con un'altra versione di PatchSmith%s: "
            "aggiorno la configurazione nel gioco con quella di adesso." % (" (%s)" % version if version else ""),
            remedy=lambda: _reconfigure(c), safe=True, signal="versione:%s" % (version or "-"),
            key="riconfigura", action="riapplica"))
    return problems


def _verify_journal(c):
    """The lines requested while playing (journal of the live translator) after the last check: those with a
    provisional answer (relaxed, refused, deferred) or without an answer (error, interrupted) are re-translated now
    and put into the XUnity cache (a provisional line is replaced only if it still has the journal's answer);
    those translated but missing from the translation memory (lost save) are put back."""
    from .xunity_service import read_journal
    until = c.state.get("diario_fino") or ""
    lines = [r for r in read_journal(c.name) if str(r.get("ts", "")) > until]
    if not lines:
        return []
    up_to = max(str(r.get("ts", "")) for r in lines)
    def read_memory(path):  # (missing or corrupted: as if empty, Project fixes it when it saves it)
        try:
            data = shared_file.read_json(path)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    memory = read_memory(memory_path(c.profile))
    machine = read_memory(memory_path(c.profile)[:-len(".json")] + ".automatiche.json")
    lost, provisional = {}, {}
    from .project import _to_translate
    for r in lines:
        t = r["testo"]
        if not _to_translate(t):  # only numbers, symbols and units ("$2,33k"): they stay as they are, nothing to redo
            continue
        if r.get("esito") == "ok":
            provisional.pop(t, None)  # translated properly later
            # (not the pieces already in Italian that the service left as they were: they are not translations)
            if r.get("backend") not in ("memoria", "comune", "gia' tradotta", None) and \
                    isinstance(r.get("risposta"), str) and \
                    t not in memory and t not in machine:
                lost[t] = r["risposta"]
        elif not (isinstance(memory.get(t), str) and memory.get(t)):
            provisional[t] = r.get("risposta")
    for t in lost:
        provisional.pop(t, None)
    if not lost and not provisional:
        c.journal_until = up_to  # nothing to redo: next time it starts from here
        return []
    if provisional and not c.retranslate:
        # check at startup: re-translating them would mean loading the local translator or waiting for Google's
        # pauses with the window frozen. They stay to do, with the "Re-translate" button (or the next "Analyze and
        # translate"); the journal does not move forward, so the button's check finds them again. Lost lines are
        # saved at once (no translator needed)
        problems = [Problem("FRASI_DA_RITRADURRE", "avviso", "Mentre giocavi, %d frasi sono rimaste in originale o "
                             "tradotte senza tutti i controlli (es. \"%s\"): premi \"Ritraduci\" per tradurle adesso "
                             "(le riprova anche la prossima \"Analizza e traduci\")." % (
                                 len(provisional), " ".join(next(iter(provisional)).split())[:60]),
                             action="ritraduci", signal="diario:%s" % up_to, extra={"frasi": len(provisional)})]
        if lost:
            problems.append(Problem("FRASI_DA_RIFARE", "info", "Mentre giocavi, %d frasi tradotte non erano state "
                                     "salvate: le salvo." % len(lost), remedy=lambda: _redo_sentences(c, {}, lost),
                                     safe=True, signal="diario-perse:%s" % up_to, key="FRASI_PERSE"))
        return problems
    parts = []
    if provisional:
        parts.append("%d frasi sono rimaste in originale o tradotte senza tutti i controlli (es. \"%s\"): le traduco "
                     "di nuovo adesso" % (len(provisional), " ".join(next(iter(provisional)).split())[:60]))
    if lost:
        parts.append("%d frasi tradotte non erano state salvate: le salvo" % len(lost))
    return [Problem("FRASI_DA_RIFARE", "info", "Mentre giocavi, %s." % "; ".join(parts),
                     remedy=lambda: _redo_sentences(c, provisional, lost), safe=True,
                     signal="diario:%s" % up_to, action="traduci", extra={"diario_fino": up_to})]


def _verify_fixes(c):
    """Unity: the fixes made in "Fix translations" while the game was open wait in
    memory/<gioco>.<lingua>.to_write.json (the XUnity cache lines are changed only with the game closed) until
    "Reapply" writes them: if nobody pressed it, the check writes them (with the game closed, once)."""
    from . import corrections
    try:
        pending = corrections.to_write(c.profile)
    except (OSError, ValueError, KeyError, TypeError):
        return []
    if not pending:
        return []
    n = len(pending)
    fingerprint = _sha1(json.dumps(sorted((t, d["valore"]) for t, d in pending.items()),
                                ensure_ascii=False).encode("utf-8"))[:12]

    def write():
        from .auto import write_unity_fixes
        written_texts, how_many = write_unity_fixes(c.profile, c.log)
        text = "%s nel gioco" % ("scritta" if written_texts == 1 else "scritte %d" % written_texts)
        if written_texts < how_many:
            text += (", una lasciata com'era (la riga era stata cambiata dopo la correzione)"
                      if how_many - written_texts == 1
                      else ", %d lasciate com'erano (le righe erano state cambiate dopo la correzione)"
                      % (how_many - written_texts))
        return True, text

    return [Problem(
        "CORREZIONI_IN_ATTESA", "info",
        "Una tua correzione fatta in \"Correggi traduzioni\" con il gioco aperto non era ancora nel gioco: la scrivo "
        "adesso." if n == 1 else "%d tue correzioni fatte in \"Correggi traduzioni\" con il gioco aperto non erano "
        "ancora nel gioco: le scrivo adesso." % n,
        remedy=write, safe=True, signal="correzioni:%s" % fingerprint, key="correzioni", action="riapplica")]


def _redo_sentences(c, provisional, lost):
    from .project import Project
    project = Project(c.path, c.log)
    if lost:
        project.memory.update(lost)
        project._save_memory()
        project._update_machine(list(lost))
    src_lang, a = _unity_languages(c)
    texts = list(provisional)[:MAX_SENTENCES]
    failed, absent = 0, None
    if texts:
        name = c.translator or _profile_translator(c.profile)
        try:
            translator = create_backend(name, c.log)
        except BackendError as e:  # (e.g. local translator chosen but not installed) lost lines are saved
            # anyway; the others are left to the next "Analyze and translate", with the button to fix it
            absent, texts = e, []
        else:
            failed = project.translate_texts(texts, translator, src_lang, a)
    translations = {t: project.memory[t] for t in list(lost) + texts
                  if isinstance(project.memory.get(t), str) and project.memory[t]}
    entries = adapter_unity.xunity_cache_entries(c.folder, a)
    names = project.active_names(list(translations))
    substitute = {t: entries[t] for t in translations if t in entries and entries[t] == provisional.get(t)
                   and entries[t] != translations[t]
                   and not adapter_unity.check_xunity_cache({t: translations[t]}, src_lang, a, names)}
    counts = {}
    adapter_unity.write_xunity_cache(c.folder, a, translations, c.log, substitute=substitute, counts=counts,
                                      excluded=c.profile.get("caratteri_mancanti"))
    remaining_count = len(provisional) - len([t for t in texts if t in translations])
    text = "%d frasi tradotte e messe nel gioco (%d righe provvisorie sostituite)" % (
        len(translations), counts.get("sostituite", 0))
    if remaining_count or failed:
        text += "; %d restano in originale (\"Analizza e traduci\" le riprova con parametri nuovi)" % max(
            remaining_count, failed)
    if absent is not None:
        text += ". %s" % absent
        if getattr(absent, "code", None) == "TRADUTTORE_ASSENTE":
            return False, text, "installa_locale"
    return (not remaining_count), text


def _profile_translator(profile):
    from .backend import BACKEND, default
    name = profile.get("backend")
    if name in (None, "xunity-google"):
        name = "google"
    return name if name in BACKEND and name != "finto" else default()


def _verify_service_log(c):
    """The sessions of this game's live translator after the last write (servizio-xunity.log log, shared by all
    games): it counts if it did not start (port in use) or had no translators ready."""
    from . import ollama, xunity_service
    text = _read(os.path.join(ollama.folder(), "servizio-xunity.log"), 512 * 1024)
    if not text:
        return []
    port = "porta %d " % xunity_service.port_for(c.name)
    current_name, found = None, []
    for line in text.splitlines():
        m = re.match(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$", line)
        if not m:
            continue
        message = m.group(2).strip()
        g = re.search(r"\(gioco: ([^)]+)\)\s*$", message)
        if g:
            current_name = g.group(1)
            continue
        # the "! ..." lines (port in use, a translator that does not start) and the translator queue left empty
        unlabeled = message.startswith("Traduttori per le frasi nuove: nessuno")
        if not message.startswith("!") and not unlabeled:
            continue
        is_ours = current_name == c.name or port in message
        if is_ours and (unlabeled or re.search(r"occupata|non pronti|non disponibile|non avviato", message)):
            try:
                instant = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            except (ValueError, OverflowError):
                continue
            if instant > c.reference:
                found.append((instant, message.lstrip("! ").strip()))
    if not found:
        return []
    return [Problem("SERVIZIO_REGISTRO", "avviso", "Durante l'ultima partita il traduttore delle frasi nuove ha avuto "
                     "un problema (%s: \"%s\")." % (_when(found[-1][0]), found[-1][1][:120]),
                     signal="registro:%d" % int(found[-1][0]), action="crea_rapporto")]


def _remove_unity(c):
    from . import auto as automatic
    d = {}
    automatic.restore(c.folder, c.log, outcome=d)
    e = d.get("esito")
    return (e is not None and e.code == "OK"), (e.reason if e is not None else "")


# --- games with translated files, Unreal, XNA ---------------------------------------------------------------

def _reapply(c, force_language=False):
    """Puts back the saved translations (like "Reapply", without network)."""
    from . import auto as automatic
    d = {}
    automatic.reapply(c.folder, c.log, outcome=d)
    e = d.get("esito")
    if force_language:
        _put_back_added_language(c, force=True)
    if e is None:
        return False, "nessun esito"
    success = e.code in ("OK", "PARZIALE") and (e.written or e.translated_items)
    text = "traduzioni rimesse nel gioco (testi tradotti: %d%s)" % (
        e.translated_items, ", rimasti in originale: %d" % e.remaining if e.remaining else "") if success else \
        (e.reason or "non riuscito")
    return success, text


def _verify_web_live(c):
    """Live translation (adapter_web_live): if the game put back its own preload.js (update, Steam file
    verification) the script is gone: it is put back (Reapply, without network)."""
    from . import adapter_web_live
    if adapter_web_live.installed(c.folder):
        return []
    preload = adapter_web_live.find_preload(c.folder)
    return [Problem("RIPRISTINATO_DAL_GIOCO", "avviso", "Il gioco ha rimesso il suo file originale (preload.js): "
                     "forse un aggiornamento o la verifica dei file di Steam. Rimetto la traduzione dal vivo.",
                     remedy=lambda: _reapply(c), safe=True,
                     signal="ripristinato:%s" % (_mtime(preload) if preload else ""), key="riapplica",
                     action="riapplica")]


def _verify_file(c):
    """Games with translated files: the game (an update, Steam's "Verify integrity") put back its originals or
    changed them; the added language is gone."""
    problems, put_back, changed_files, removed = [], [], [], []
    rel_paths, catalogs, fingerprints = [], set(), {}
    for entry in c.profile.get("file") or []:
        if isinstance(entry, dict):
            # Unity Localization: also the catalog from which PatchSmith removed the CRC (if the game puts it back,
            # the translated bundle would no longer load)
            rel_paths += [r for r in (entry.get("percorso"), entry.get("catalogo")) if r and r not in rel_paths]
            if entry.get("catalogo"):
                catalogs.add(entry["catalogo"])
    for rel in rel_paths:
        p = os.path.join(c.folder, *rel.split("/"))
        info = read_info(p)
        if not info or info.get("configuratore"):
            continue
        for name in info.get("creati", []):  # added language (e.g. local/italian.json) removed by the game
            if not os.path.exists(os.path.join(os.path.dirname(p), name)):
                removed.append(name)
        if not os.path.exists(p):
            continue
        try:
            current = fingerprints[rel] = _hash_file(p)
        except OSError:
            continue
        if info.get("solo_creati"):
            if current != info["hash_originale"]:
                changed_files.append(rel)
        elif current == info["hash_originale"]:
            put_back.append((rel, current))
        elif current not in info.get("hash_scritti", []) and not _has_tag(p):
            changed_files.append(rel)
    if put_back or removed:
        names = [r for r, _ in put_back] + removed
        problems.append(Problem(
            "RIPRISTINATO_DAL_GIOCO", "avviso", "Il gioco ha rimesso i suoi file originali (%s): forse un "
            "aggiornamento o la verifica dei file di Steam. Rimetto le traduzioni salvate." % ", ".join(names[:3]),
            remedy=lambda: _reapply(c, force_language=bool(removed)), safe=True,
            signal="ripristinato:" + "|".join(sorted("%s=%s" % x for x in put_back) + sorted(removed)),
            key="riapplica", action="riapplica"))
    new_catalogs = [r for r in changed_files if r in catalogs]
    if new_catalogs and len(new_catalogs) == len(changed_files):
        # an update changed only the bundle catalog (not the text tables): the new one has the CRC of the original
        # table again and the game would not load the translated one (missing texts). Reapply removes the CRC
        # from the new catalog (which becomes the original, with its backup).
        problems.append(Problem(
            "RIPRISTINATO_DAL_GIOCO", "avviso", "Un aggiornamento del gioco ha cambiato l'elenco dei suoi file (%s) e "
            "ha rimesso il controllo che impedisce di caricare i testi tradotti. Lo tolgo di nuovo e rimetto le "
            "traduzioni salvate." % ", ".join(new_catalogs), remedy=lambda: _reapply(c), safe=True,
            signal="catalogo:" + "|".join(sorted("%s=%s" % (r, fingerprints[r]) for r in new_catalogs)),
            key="riapplica", action="riapplica"))
        changed_files = []
    if changed_files:
        problems.append(Problem(
            "AGGIORNATO", "avviso", "Dopo la traduzione il gioco ha cambiato %d file con i testi (es. %s): forse un "
            "aggiornamento. Premi \"Analizza e traduci\" per tradurre anche i testi nuovi (quelli gia' tradotti "
            "restano)." % (len(changed_files), changed_files[0]), signal="cambiati:" + "|".join(sorted(changed_files)),
            action="traduci"))
    if c.engine == "gamemaker" and not removed:
        problems += _verify_added_language(c)
    return problems


def _added_language(c):
    """(name of the added language written by PatchSmith in the options, game languages) or (None, None)."""
    from . import auto as automatic, official_language
    entries = [v for v in c.profile.get("file") or [] if isinstance(v, dict) and v.get("lingua_nuova")]
    log_file = official_language._read_log(c.folder) or {}
    if not entries or log_file.get("tipo") != "gamemaker_opzioni" or not log_file.get("scritto"):
        return None, None
    languages = automatic.folder_languages(c.folder, entries[0]["percorso"]) | {log_file["scritto"]}
    return log_file["scritto"], languages


def _verify_added_language(c):
    """GameMaker with the translation as an added language: another language is chosen in the game's options. It is
    unknown whether the player chose it: it says how to set it back, without touching anything."""
    from . import language_gamemaker
    written_lang, languages = _added_language(c)
    if not written_lang:
        return []
    try:
        path, spot = language_gamemaker.search(c.folder, languages)
    except OSError:
        return []
    if path is None or spot[3] == written_lang:
        return []
    return [Problem("LINGUA_AGGIUNTA", "info", "Nel gioco e' scelta la lingua '%s': la traduzione e' la lingua '%s'. "
                     "Se la vuoi, sceglila nel gioco in Opzioni > Lingua." % (spot[3], written_lang),
                     signal="lingua:%s:%s" % (spot[3], int(_mtime(path) or 0)))]


def _put_back_added_language(c, force=False):
    from . import official_language
    written_lang, languages = _added_language(c)
    if written_lang:
        official_language.set_added_language(c.folder, written_lang, languages, c.log, force=force)


def _verify_unreal(c):
    problems = []
    manifest = adapter_unreal.read_manifest(c.folder)
    if not manifest:
        return []
    dropped = [rel for rel in manifest["file"] if not os.path.exists(os.path.join(c.folder, *rel.split("/")))]
    if dropped:
        problems.append(Problem(
            "TRADUZIONE_TOLTA", "avviso", "Il file con la traduzione (%s) non c'e' piu' nel gioco: forse un "
            "aggiornamento. Lo rimetto con le traduzioni salvate." % os.path.basename(dropped[0]),
            remedy=lambda: _reapply(c), safe=True, signal="tolti:" + "|".join(sorted(dropped)), key="riapplica",
            action="riapplica"))
    for log_file in _unreal_logs(c.folder, manifest):
        t = _mtime(log_file)
        if not t or t <= c.reference:
            continue
        text = _read(log_file)
        if not dropped and not re.search(r"Mounting pak file .*%s" % re.escape(adapter_unreal.PAK_PREFIX), text):
            problems.append(Problem(
                "PAK_NON_CARICATO", "avviso", "Nell'ultima partita il gioco non ha caricato il file della traduzione: "
                "forse usa archivi firmati o un formato piu' nuovo. Crea un rapporto da inviare a chi ti aiuta con "
                "PatchSmith.", signal="pak:%d" % int(t), action="crea_rapporto"))
        origin = (c.profile.get("lingua_origine") or "en").split("-")[0]
        languages = set(re.findall(r"Overriding language with [^(]*\(([^)]+)\)", text))
        other_ones = sorted(x for x in languages if x.split("-")[0].lower() not in (origin, language.AUTO))
        if other_ones:
            problems.append(Problem(
                "LINGUA_UNREAL", "info", "Nell'ultima partita il gioco usava la lingua '%s': la traduzione prende il "
                "posto di quella originale (%s). Scegli %s nelle opzioni del gioco." % (
                    other_ones[0], language.label(origin), language.label(origin)),
                signal="lingua:%s:%d" % (other_ones[0], int(t))))
    try:
        current_count = {n: _sha1(pak.read_file(n)) for pak, n, _, _ in adapter_unreal.find_locres(c.folder)}
    except Exception:  # noqa: BLE001 - unreadable archives: "Analyze and translate" reports it
        current_count = None
    if current_count and manifest.get("sorgenti") and current_count != manifest.get("sorgenti"):
        problems.append(Problem(
            "AGGIORNATO", "avviso", "Il gioco e' stato aggiornato: premi \"Analizza e traduci\" per tradurre anche i "
            "testi nuovi (quelli gia' tradotti restano).", signal="sorgenti:" + _sha1(
                json.dumps(sorted(current_count.items())).encode("utf-8")), action="traduci"))
    return problems


def _unreal_logs(folder, manifest):
    """The Unreal game logs (%LOCALAPPDATA%\\<Project>\\Saved\\Logs\\<Project>.log) of the projects with a
    PatchSmith pak (<Project>/Content/Paks/<pak>), even if the chosen folder is the project's."""
    if not os.environ.get("LOCALAPPDATA"):
        return []
    projects = set()
    for rel in manifest.get("file") or {}:
        parts = os.path.normpath(os.path.join(folder, *rel.split("/"))).split(os.sep)
        if len(parts) >= 4:
            projects.add(parts[-4])
    return [os.path.join(os.environ["LOCALAPPDATA"], p, "Saved", "Logs", p + ".log") for p in sorted(projects)]


def _sha1(data):
    import hashlib
    return hashlib.sha1(data).hexdigest()


def _verify_xna(c):
    problems = []
    manifest = adapter_xna.read_manifest(c.folder)
    if not manifest:
        return []
    dropped = [rel for rel in manifest["file"] if not os.path.exists(os.path.join(c.folder, *rel.split("/")))]
    if dropped:
        problems.append(Problem(
            "TRADUZIONE_TOLTA", "avviso", "I file con la traduzione (%s) non ci sono piu' nel gioco: forse un "
            "aggiornamento. Li rimetto con le traduzioni salvate." % ", ".join(dropped[:2]),
            remedy=lambda: _reapply(c), safe=True, signal="tolti:" + "|".join(sorted(dropped)), key="riapplica",
            action="riapplica"))
    changed_files = []
    for name, fingerprint in (manifest.get("sorgenti") or {}).items():
        p = os.path.join(c.folder, name)
        try:
            if adapter_xna._sha256(p) != fingerprint:
                changed_files.append(name)
        except OSError:
            changed_files.append(name)
    if changed_files:
        problems.append(Problem(
            "AGGIORNATO", "avviso", "Il gioco e' stato aggiornato (%s): premi \"Analizza e traduci\" per tradurre "
            "anche i testi nuovi." % ", ".join(changed_files[:2]), signal="sorgenti:" + "|".join(sorted(changed_files)),
            action="traduci"))
    return problems


# --- crashes of the other engines (Windows Application log) -------------------------------------------------

def crash_events(since):
    """Program crashes from the Windows Application log after the time 'dal' (at most EVENT_DAYS days ago), read
    only: [{id, istante, codice (1000 o 1026), app, percorso, testo}]. 1000: a program crash (with the executable's
    path); 1026: unhandled .NET error (only the executable's name, in the text). Empty if it cannot be read."""
    if not sys.platform.startswith("win"):
        return []
    seconds = max(60.0, min(time.time() - (since or 0), EVENT_DAYS * 86400.0))
    question = "*[System[(EventID=1000 or EventID=1026) and TimeCreated[timediff(@SystemTime) <= %d]]]" % int(
        seconds * 1000)
    try:
        r = subprocess.run(["wevtutil", "qe", "Application", "/q:" + question, "/f:xml", "/rd:true", "/c:500"],
                           capture_output=True, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return read_events(r.stdout)


def read_events(data):
    """The events from wevtutil's XML (bytes: UTF-8 or the Windows encoding)."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("mbcs" if sys.platform.startswith("win") else "latin-1", "replace")
    try:
        root = ET.fromstring("<Eventi>%s</Eventi>" % text)
    except ET.ParseError:
        return []
    ns = "{http://schemas.microsoft.com/win/2004/08/events/event}"
    events = []
    for ev in root.iter(ns + "Event"):
        try:
            code = int(ev.findtext("%sSystem/%sEventID" % (ns, ns)) or 0)
            tempo = ev.find("%sSystem/%sTimeCreated" % (ns, ns)).get("SystemTime")
            instant = datetime.datetime.strptime(tempo[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=datetime.timezone.utc).timestamp()
        except (AttributeError, TypeError, ValueError):
            continue
        ev_data = {d.get("Name") or str(i): d.text or "" for i, d in enumerate(ev.iter(ns + "Data"))}
        event_text = "\n".join(ev_data.values())
        app, path = ev_data.get("AppName"), ev_data.get("AppPath")
        if code == 1026:
            m = re.search(r"^[^:\n]*:\s*(\S+\.exe)\s*$", event_text, re.M | re.I)
            app = m.group(1) if m else None
        events.append({"id": ev.findtext("%sSystem/%sEventRecordID" % (ns, ns)) or "%d-%d" % (code, instant),
                       "istante": instant, "codice": code, "app": app, "percorso": path, "testo": event_text})
    return events


def _executables(folder):
    """The (lowercase) names of the game's executables: in the folder and in subfolders up to two levels deep."""
    names = set()
    for root, dirs, files in os.walk(folder):
        if os.path.relpath(root, folder).count(os.sep) >= 2:
            dirs[:] = []
        names |= {f.lower() for f in files if f.lower().endswith(".exe")}
    return names


def game_crash(folder, events, since):
    """The crash events of the game's executable (inside its folder) after 'dal'."""
    output, exe = [], None
    for ev in events:
        if ev["istante"] <= since:
            continue
        if ev.get("percorso"):
            if _inside(ev["percorso"], folder):
                output.append(ev)
        elif ev.get("app"):
            if exe is None:
                exe = _executables(folder)
            if ev["app"].lower() in exe:
                output.append(ev)
    return output


def _verify_crash_events(c, events):
    crash = game_crash(c.folder, events if events is not None else crash_events(c.reference), c.reference)
    if not crash:
        return []
    most_recent = max(crash, key=lambda e: e["istante"])
    repeats = "%d volte, l'ultima il %s" % (len(crash), _when(most_recent["istante"])) if len(crash) > 1 else \
        "il %s" % _when(most_recent["istante"])
    return [Problem("CRASH", "errore", "Il gioco si e' chiuso all'improvviso dopo la traduzione (%s). Se succede "
                     "ogni volta, premi \"Ripristina originali\" per tornare ai file originali (le traduzioni restano "
                     "salvate) e crea un rapporto." % repeats, remedy=lambda: _remove_file(c), safe=False,
                     signal="eventi:" + ",".join(sorted(str(e["id"]) for e in crash)), action="ripristina")]


def _remove_file(c):
    from . import auto as automatic
    d = {}
    automatic.restore(c.folder, c.log, outcome=d)
    e = d.get("esito")
    return (e is not None and e.code == "OK"), (e.reason if e is not None else "")


# --- installation and reference point ---------------------------------------------------------------------

def _installed(c):
    """True if the game has PatchSmith's translation for this game profile."""
    if not c.folder or not os.path.isdir(c.folder):
        return False
    if c.engine == "unity":
        return adapter_unity.manifest_present(c.folder)
    if c.engine == "unreal":
        return adapter_unreal.manifest_present(c.folder)
    if c.engine == "xna":
        return adapter_xna.manifest_present(c.folder)
    if c.engine == "web-vivo":  # the script in preload.js (or its backup, if the game put the original back)
        from . import adapter_web_live
        preload = adapter_web_live.find_preload(c.folder)
        return bool(preload and read_info(preload))
    return any(_entry_info(c, v) for v in c.profile.get("file") or [])


def _entry_info(c, entry):
    rel = entry.get("percorso") if isinstance(entry, dict) else None
    if not rel:
        return None
    info = read_info(os.path.join(c.folder, *rel.split("/")))
    return info if info and not info.get("configuratore") else None


def reference(c):
    """The time of PatchSmith's last write to the game: manifest, command and plugin of the live translator,
    information of the translated files, fixes made by this check. Older signals do not count (they were already
    there when PatchSmith last wrote)."""
    timestamps = [c.last_remedy()]
    if c.engine == "unity":
        for rel in (adapter_unity.MANIFEST, adapter_unity.SERVICE_CONFIG, adapter_unity.SERVICE_PLUGIN):
            timestamps.append(_mtime(os.path.join(c.folder, *rel.split("/"))) or 0)
    elif c.engine == "unreal":
        timestamps.append(_mtime(os.path.join(c.folder, adapter_unreal.MANIFEST)) or 0)
    elif c.engine == "xna":
        timestamps.append(_mtime(os.path.join(c.folder, adapter_xna.MANIFEST)) or 0)
    for entry in c.profile.get("file") or []:
        rel = entry.get("percorso") if isinstance(entry, dict) else None
        if rel:
            for suffix in INFO_SUFFIXES:  # (also the name used by versions up to 0.12)
                timestamps.append(_mtime(os.path.join(c.folder, *rel.split("/")) + suffix) or 0)
    return max(timestamps)


def signals(profile_path, profile=None):
    """{signal: value} of a game, quick to read (file dates): they change when playing, when the game updates and
    when PatchSmith writes. Empty if the translation is not installed. A completed check saves them in 'visti': when
    PatchSmith opens, only the games with different signals are checked (to_check)."""
    profile = profile if profile is not None else _read_profile(profile_path)
    c = _Context(profile_path, profile, {}, None, False, None)
    if not _installed(c):
        return {}
    output = {}

    def date(key, path):
        """Date (to the millisecond) and size: a file put back right after a write still changes."""
        try:
            st = os.stat(path)
        except (OSError, TypeError, ValueError):
            return
        output[key] = "%d/%d" % (st.st_mtime_ns // 1_000_000, st.st_size)

    if c.engine == "unity":
        for rel in (adapter_unity.MANIFEST, adapter_unity.BEPINEX_LOG, adapter_unity.SERVICE_STATE,
                    adapter_unity.SERVICE_CONFIG):
            date(rel, os.path.join(c.folder, *rel.split("/")))
        manifest = adapter_unity.read_manifest(c.folder) or {}
        output["versione"] = manifest.get("patchsmith_versione") or "-"
        # PatchSmith updated: the games installed with the previous version are checked again (VERSION)
        output["patchsmith"] = __version__
        try:
            logs = unity_logs(c.folder)
        except OSError:
            logs = {}
        for p in logs.get("player", []):
            date("unity:" + os.path.basename(p), p)
        for name, instant, _ in unity_crash(logs):
            output["crash:" + name] = int(instant)
        from .xunity_service import journal_path
        date("diario", journal_path(c.name))
        if profile.get("nome") and profile.get("lingua_destinazione"):  # pending fixes (CORREZIONI_IN_ATTESA)
            from .corrections import to_write_path
            date("da_scrivere", to_write_path(profile))
    elif c.engine in ("unreal", "xna"):
        module = adapter_unreal if c.engine == "unreal" else adapter_xna
        date("manifesto", os.path.join(c.folder, module.MANIFEST))
        manifest = module.read_manifest(c.folder) or {}
        for rel in list(manifest.get("file") or {}) + list(manifest.get("sorgenti") or {}):
            date(rel, os.path.join(c.folder, *rel.split("/")))
        if c.engine == "unreal":
            for log_file in _unreal_logs(c.folder, manifest):
                date("log:" + os.path.splitext(os.path.basename(log_file))[0], log_file)
    else:
        for entry in profile.get("file") or []:
            info = _entry_info(c, entry)
            if not info:
                continue
            p = os.path.join(c.folder, *entry["percorso"].split("/"))
            date(entry["percorso"], p)
            for name in info.get("creati", []):
                date("creato:" + name, os.path.join(os.path.dirname(p), name))
        if c.engine == "unity-localization":  # the Unity log: the letters the fonts lack (FONT_MANCANTI)
            try:
                for p in unity_logs(c.folder).get("player", []):
                    date("unity:" + os.path.basename(p), p)
            except OSError:
                pass
        if c.engine == "gamemaker" and os.environ.get("LOCALAPPDATA"):
            for exe in sorted(_executables(c.folder))[:5]:
                options = os.path.join(os.environ["LOCALAPPDATA"], os.path.splitext(exe)[0])
                for p in glob.glob(os.path.join(glob.escape(options), "*"))[:20]:
                    if os.path.isfile(p):
                        date("opzioni:" + os.path.basename(p), p)
    return output


def to_check(events=None):
    """The game profiles of games with new signals since the last check (or never checked), with the translation
    installed: [game profile path]. Fast: only file dates (and one read of the Windows crash log for non-Unity
    games)."""
    from . import auto as automatic
    selected, others = [], []
    for path, profile in automatic.list_profiles():
        folder = profile.get("cartella_gioco")
        if not folder or not os.path.isdir(folder) or profile.get("backend") == "finto":
            continue
        try:
            current_count = signals(path, profile)
        except Exception:  # noqa: BLE001 - an unreadable game does not stop the others
            continue
        if not current_count:
            continue
        if current_count != (read_state(path).get("visti") or {}):
            selected.append(path)
        elif profile.get("motore") != "unity":
            others.append((path, profile))
    if others:  # new Windows crashes for the games that have no other signals
        if events is None:
            events = crash_events(min(read_state(p).get("eventi_dal") or 0 for p, _ in others))
        for path, profile in others:
            state = read_state(path)
            already = set(state.get("eventi") or [])
            # only those after its last check: it has already seen the earlier ones (or they came before
            # PatchSmith's last write), otherwise it would be checked again at every startup
            new_items = [e for e in game_crash(os.path.abspath(profile["cartella_gioco"]), events,
                                                state.get("eventi_dal") or 0) if str(e["id"]) not in already]
            if new_items:
                selected.append(path)
    return selected


# --- the check -------------------------------------------------------------------------------------------

def check(profile_path, apply=True, log=print, translator=None, events=None, retranslate=True):
    """Checks a game after playing. applica=True: the safe fixes start (with the game closed, once per signal) and
    the result is saved in profiles/<nome>.checks.json; False: read only, writes nothing.
    traduttore: to re-translate the lines left provisional while playing (default: the game profile's one).
    ritraduci=False (the check when the interface opens): those lines are not re-translated now, they stay to do
    (FRASI_DA_RITRADURRE, action 'ritraduci'); the quick checks and their fixes are made anyway.
    eventi: the Windows crashes already read (for the tests; default: crash_events). Returns [Problem]."""
    log = log or (lambda _m: None)
    profile = _read_profile(profile_path)
    state = read_state(profile_path)
    c = _Context(profile_path, profile, state, log, apply, translator, retranslate, events)
    log("Controllo dopo aver giocato: %s" % c.title_text)
    if not _installed(c):
        log("  Traduzione non installata in %s: niente da controllare." % (c.folder or "?"))
        if apply and state:  # (e.g. removed with "Restore originals"): earlier problems no longer apply
            state.update(visti={}, ultimo_controllo=_now(), ultimo={"quando": _now(), "corretti": 0,
                                                                      "da_fare": 0, "problemi": []})
            try:
                _save_state(profile_path, state)
            except OSError:
                pass
        return []
    c.reference = reference(c)
    log("  Conta solo quello che e' successo dopo l'ultima scrittura di PatchSmith nel gioco (%s)."
        % _when(c.reference))
    if c.engine == "unity":
        problems = _verify_unity(c)
    elif c.engine == "web-vivo":
        problems = _verify_web_live(c)
    else:
        problems = _verify_file(c) if c.engine not in ("unreal", "xna") else []
        if c.engine == "unity-localization":  # (Unity's Player.log: without BepInEx it is the only log)
            problems += _verify_font(c, _player_logs(c))
        if c.engine == "unreal":
            problems += _verify_unreal(c)
        elif c.engine == "xna":
            problems += _verify_xna(c)
        crash = _verify_crash_events(c, events)
        problems += crash
    problems += _verify_temp_files(c)
    for p in problems:  # the technical details only in the log
        if p.detail:
            log("  (%s: %s)" % (p.code, p.detail))
    _run_remedies(c, problems)
    if any(p.state == "rimandato" for p in problems):
        # game open: nothing is written, not even the result (the next check starts over)
        apply = False
    if apply:
        state["visti"] = signals(profile_path, profile)
        if c.engine != "unity":  # the Windows crashes already seen (they are not files: they are not in 'visti')
            seen = {i for p in problems if p.signal.startswith("eventi:") for i in p.signal[7:].split(",")}
            state["eventi"] = sorted(set(state.get("eventi") or []) | seen)[-200:]
            state["eventi_dal"] = time.time()
        # the journal lines already seen (redone, or tried once) are not picked up again: "Analyze and translate"
        # retries the remaining ones (provisional cache lines, see automatic._cache_lines_to_redo)
        for p in problems:
            if p.code == "FRASI_DA_RIFARE" and p.state in ("corretto", "non_riuscito", "gia_tentato"):
                c.journal_until = max(c.journal_until or "", p.extra.get("diario_fino") or "")
        if c.journal_until:
            state["diario_fino"] = c.journal_until
    if apply:
        state["ultimo_controllo"] = _now()
        state["versione"] = __version__
        state["ultimo"] = {"quando": _now(), "corretti": sum(p.state == "corretto" for p in problems),
                           "da_fare": sum(p.state in TO_DO for p in problems),
                           "problemi": [p.as_dict() for p in problems]}
        try:
            _save_state(profile_path, state)
        except OSError as e:
            log("  ! Non ho potuto salvare il risultato del controllo (%s)" % e)
    for line in lines(problems):
        log("  " + line)
    return problems


TO_DO = ("da_fare", "non_riuscito", "gia_tentato")


def _verify_temp_files(c):
    """Temporary files left in the translation memory by an interrupted save (e.g. the game closed suddenly while
    the live translator was saving): those of this game's translation memory and of the shared memory."""
    folder = os.path.dirname(memory_path(c.profile))
    now = time.time()
    old_files = [p for p in glob.glob(os.path.join(glob.escape(folder), "*.tmp"))
              if os.path.basename(p).startswith((c.name + ".", "comune.")) and os.path.isfile(p)
              and now - (_mtime(p) or now) > shared_file.OLD_TEMP_AGE]
    if not old_files:
        return []
    def remove():
        dropped = 0
        for p in old_files:
            try:
                if time.time() - os.path.getmtime(p) > shared_file.OLD_TEMP_AGE:
                    os.remove(p)
                    dropped += 1
            except OSError:
                pass
        return "tolti %d" % dropped

    return [Problem("TEMPORANEI", "info", "Nella memoria delle traduzioni %s di un salvataggio interrotto: %s tolgo "
                     "(le traduzioni sono al sicuro nei file veri)." % (
                         "è rimasto un file temporaneo" if len(old_files) == 1 else
                         "sono rimasti %d file temporanei" % len(old_files), "lo" if len(old_files) == 1 else "li"),
                     remedy=remove, safe=True, signal="tmp:" + "|".join(sorted(os.path.basename(p)
                                                                                     for p in old_files)))]


def _already_tried(state, key, signal):
    return next((v for v in (state.get("rimedi") or {}).get(key) or [] if isinstance(v, dict)
                 and v.get("segnale") == signal), None)


def _record(state, key, signal, success, detail):
    entries = [v for v in (state.setdefault("rimedi", {}).get(key) or []) if isinstance(v, dict)]
    entries.append({"segnale": signal, "quando": _now(), "ts": time.time(), "riuscito": bool(success),
                 "dettaglio": str(detail)[:300]})
    state["rimedi"][key] = entries[-MAX_SAVED_ATTEMPTS:]


def _run_remedies(c, problems):
    """The safe fixes, with the game closed, once per signal (applica=True); the others stay to do."""
    candidates = [p for p in problems if p.remedy is not None and p.safe]
    if c.apply and candidates and c.is_open():
        c.log("  Il gioco e' aperto: le correzioni le faccio al prossimo controllo, a gioco chiuso.")
    handled, actions = {}, {}
    for p in problems:
        if p.remedy is None or not p.safe:
            p.state = "da_fare" if p.action else "info"
            continue
        key = p.key or p.code
        attempted = _already_tried(c.state, key, p.signal)
        if attempted:
            p.state = "gia_tentato"
            p.detail = "gia' provato a correggerlo il %s (%s), ma il problema c'e' ancora" % (
                _when(attempted.get("ts")), attempted.get("dettaglio") or "")
            continue
        if not c.apply:
            p.state = "da_correggere"
            continue
        if c.is_open():
            p.state = "rimandato"
            continue
        if key not in handled:
            interruption.check()
            try:
                result = p.remedy()
                if not isinstance(result, tuple):
                    result = (True, result or "")
                success, text = result[:2]
                if len(result) > 2:  # the right button if it is still to do (e.g. install the local translator)
                    actions[key] = result[2]
            except interruption.Interrupted:
                raise
            except Exception as e:  # noqa: BLE001 - a failed fix does not stop the others
                success, text = False, str(e) if isinstance(e, (CheckError, adapter_unity.UnityError,
                                                                  BackendError)) else \
                    "%s: %s" % (type(e).__name__, e)
            handled[key] = (success, text)
            c.log("  %s %s" % ("-> corretto:" if success else "-> non riuscito:", text))
        success, text = handled[key]
        _record(c.state, key, p.signal, success, text)
        p.state = "corretto" if success else "non_riuscito"
        p.detail = text
        p.action = actions.get(key, p.action)


# --- results for the interface and the command line ----------------------------------------------------------

LABELS = {"corretto": "corretto", "non_riuscito": "da fare (correzione non riuscita)",
             "gia_tentato": "da fare (gia' corretto una volta, ma e' tornato)", "rimandato": "da correggere a gioco "
             "chiuso", "da_fare": "da fare", "da_correggere": "si correggerebbe da solo", "info": "da sapere"}


def lines(problems):
    """The summary for the log and the command line."""
    if not problems:
        return ["Nessun problema trovato."]
    output = []
    for p in problems:
        output.append("[%s] %s" % (LABELS.get(p.state, p.state), p.message))
        if p.detail and p.state != "info":
            output.append("    %s" % p.detail)
    fixed_count = sum(p.state == "corretto" for p in problems)
    to_do = sum(p.state in TO_DO for p in problems)
    to_fix = sum(p.state in ("da_correggere", "rimandato") for p in problems)
    output.append("Problemi corretti: %d, da fare: %d%s." % (fixed_count, to_do, ", da correggere a gioco chiuso o "
                                                             "senza sola lettura: %d" % to_fix
                                                             if to_fix else ""))
    return output


def as_outcome(problems, profile_path=None, title_text=None):
    """The outcome (outcome.Outcome) of a game's check, for the interface window: OK (nothing to do), PARZIALE
    (something to do), GIOCO_APERTO (fixes postponed). In extra: 'controllo' (the problems as dictionaries),
    'gioco', 'cartella', 'profilo', 'corretti', 'da_fare'."""
    profile = {}
    if profile_path:
        try:
            profile = _read_profile(profile_path)
        except (OSError, ValueError):
            profile = {}
    game = title_text or os.path.basename(os.path.normpath(profile.get("cartella_gioco") or "")) or "il gioco"
    fixed_count = [p for p in problems if p.state == "corretto"]
    to_do = [p for p in problems if p.state in TO_DO]
    postponed_problems = [p for p in problems if p.state == "rimandato"]
    actions = []
    for p in to_do:
        if p.action:
            actions.append(p.action)
    if any(p.state in ("non_riuscito", "gia_tentato") for p in to_do):
        actions.append("crea_rapporto")
    if postponed_problems:
        code, actions = "GIOCO_APERTO", ["chiudi_gioco"] + actions
        reason = "Il gioco e' aperto: le correzioni le faccio quando e' chiuso."
    elif to_do:
        code = "PARZIALE"
        reason = "Dopo l'ultima partita a %s: %s, %d da fare." % (game, _fixed_label(len(fixed_count)), len(to_do))
    else:
        code = "OK"
        reason = ("Dopo l'ultima partita a %s: %s." % (game, _fixed_label(len(fixed_count))) if fixed_count else
                  "Dopo l'ultima partita a %s: nessun problema." % game)
    return Outcome(code, reason, engine=profile.get("motore"), actions=actions,
                 extra={"controllo": [p.as_dict() for p in problems], "gioco": game,
                        "cartella": profile.get("cartella_gioco"), "profilo": profile_path,
                        "corretti": len(fixed_count), "da_fare": len(to_do)})


def _fixed_label(n):
    return "1 problema corretto" if n == 1 else "%d problemi corretti" % n


def error_problem(error):
    """The check of a game failed (unexpected error): a problem to do, with the report."""
    return Problem("ERRORE", "errore", "Il controllo non e' riuscito (%s: %s): puoi creare un rapporto da inviare a "
                    "chi ti aiuta con PatchSmith." % (type(error).__name__, str(error)[:200]), action="crea_rapporto",
                    state="da_fare")


def last(profile_path):
    """The result of the last saved check: {quando, corretti, da_fare, problemi} or {}."""
    data = read_state(profile_path).get("ultimo")
    return data if isinstance(data, dict) else {}


def saved_outcome(profile_path):
    """The outcome of the last saved check (like come_esito), or None if there is none."""
    data = last(profile_path)
    if not data:
        return None
    problems = []
    for d in data.get("problemi") or []:
        if isinstance(d, dict) and d.get("messaggio"):
            problems.append(Problem(d.get("codice", "?"), d.get("gravita", "info"), d["messaggio"],
                                     safe=bool(d.get("sicuro")), signal=d.get("segnale") or "",
                                     action=d.get("azione") if d.get("azione") in _actions() else None,
                                     state=d.get("stato") if d.get("stato") in STATES else "info",
                                     detail=d.get("dettaglio") or "",
                                     extra={"frasi": d["frasi"]} if isinstance(d.get("frasi"), int) else {}))
    return as_outcome(problems, profile_path)


def _actions():
    from .outcome import ACTIONS
    return ACTIONS
