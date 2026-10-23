"""Workflow on a game profile: translate, apply, restore, status.

Safety of the game's files:
- before the first change the original is copied next to it as <file>.patchsmith-originale
  (copy to a temporary file, fingerprint check, then rename: never a half-finished backup);
- next to the backup there is <file>.patchsmith-info with the fingerprint of the original, the fingerprints of
  everything we wrote and the files created. It lives in the game's folder, so it holds for
  any game profile and even if the tool is moved;
- the fingerprint of what we are about to write is recorded BEFORE writing;
- if the game's file is changed by someone else (an update), it becomes the new original and the
  old original is kept with a version number;
- the texts are always extracted from the original.
"""
import collections
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import stat
import unicodedata

from . import (__version__, adapter_csv, adapter_gamemaker, adapter_lines, adapter_unity_loc,
               adapter_webjson)
from . import (adapter_webui, adapter_xna, shared_file, font_unity, interruption,
               language, names as game_names)
from .characters import ASCII, Filter, adapt_chars, adapt_without, typical_letters, undrawable
from .backend import BackendError, _weight, _unchanged, translation_problems, clean
from .system import game_open
from .text import _TOKEN, split_lines, is_readable_text, protect, reassemble_lines, restore_placeholders
from .text import DETAIL, words_only

# The program: code, downloaded components (components/), rules (data/), smith.py. It always stays here.
PROJECT_FOLDER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The user's data (USER_DATA): normally next to the program (None = PROJECT_FOLDER, read when needed).
# The environment variable PATCHSMITH_DATI moves them to another folder: the automatic tests use it so they never
# touch the real data. The translator for XUnity is started by the game, which normally does not have the variable:
# it uses the data next to the program.
DATA_ROOT = os.path.abspath(os.environ["PATCHSMITH_DATI"]) if os.environ.get("PATCHSMITH_DATI") else None
USER_DATA = ("profiles", "memory", "logs", "languages", "configurations", "save-backups", "settings.json",
               "services.json")
# the Italian names from before 0.1.0: migrate_data moves them to the English ones; until it succeeds (a folder in
# use) data_folder and components_folder still find the old ones
ITALIAN_NAMES = {"profiles": "profili", "memory": "memoria", "logs": "registri", "languages": "lingue",
                 "configurations": "configurazioni", "save-backups": "salvataggi", "settings.json": "impostazioni.json",
                 "services.json": "stato_servizi.json", "components": "componenti"}
USER_DATA_BEFORE = tuple(ITALIAN_NAMES[n] for n in USER_DATA)
BACKUP_SUFFIX = adapter_webui.BACKUP_SUFFIX  # ".patchsmith-originale"
# without a final ".json": Forager (and other GameMaker games) load every local/*.json as a language, ours too
INFO_SUFFIX = ".patchsmith-info"
OLD_INFO_SUFFIX = ".patchsmith-info.json"  # up to 0.12: still read, and renamed on the first write
INFO_SUFFIXES = (INFO_SUFFIX, OLD_INFO_SUFFIX)
TEXTS_PER_GROUP = 150  # the translation memory is saved after each group
MAX_FINGERPRINTS = 50
# translator passes for the same text (2 or 3 for each "Analyze and translate"): beyond that, a saved translation
# that is suspect or unfit for the game stays as it is (it is not re-translated at every run)
MAX_ATTEMPTS = 6


def data_folder(*parts):
    """Path among the user's data (profiles, memory, settings.json...): see DATA_ROOT. A folder with the old Italian
    name not yet moved (migrate_data) is used as long as the new one does not exist."""
    return _with_fallback(DATA_ROOT or PROJECT_FOLDER, parts)


def components_folder(*parts):
    """The downloaded components (Ollama and the models, BepInEx, XUnity) and the plugin sources: components/ next to
    the program (or componenti/, the old name, until it has been moved)."""
    return _with_fallback(PROJECT_FOLDER, ("components",) + parts)


def _with_fallback(base, parts):
    new = os.path.join(base, *parts)
    if parts and parts[0] in ITALIAN_NAMES and not os.path.exists(new):
        old = os.path.join(base, ITALIAN_NAMES[parts[0]], *parts[1:])
        if os.path.exists(old):
            return old
    return new


def migrate_data():
    """Moves the data with the Italian names from before 0.1.0 (profili, memoria, registri...) to the English names: it
    moves them, without copying or deleting anything. If the new folder already exists it moves into it the entries
    that are missing (in subfolders too); those with the same name stay in the old one. A folder in use stays where it
    is: data_folder still finds it. Called by the window and the command line at startup; returns the moved names."""
    moved_names = []
    for new, old in ITALIAN_NAMES.items():
        base = PROJECT_FOLDER if new == "components" else (DATA_ROOT or PROJECT_FOLDER)
        if _move(os.path.join(base, old), os.path.join(base, new)):
            moved_names.append(old)
    memory = os.path.join(DATA_ROOT or PROJECT_FOLDER, "memory")
    if _move(os.path.join(memory, "copie"), os.path.join(memory, "copies")):
        moved_names.append("copie")
    return moved_names


def _move(src_lang, a):
    if not os.path.exists(src_lang):
        return False
    try:
        if not os.path.exists(a):
            os.rename(src_lang, a)
            return True
        if not (os.path.isdir(src_lang) and os.path.isdir(a)):
            return False
        for entry in os.listdir(src_lang):
            if not os.path.exists(os.path.join(a, entry)):
                os.rename(os.path.join(src_lang, entry), os.path.join(a, entry))
            elif os.path.isdir(os.path.join(src_lang, entry)):
                _move(os.path.join(src_lang, entry), os.path.join(a, entry))
        if not os.listdir(src_lang):
            os.rmdir(src_lang)  # (empty: everything is already in the new one)
        return True
    except OSError:  # in use (e.g. the local translator running): try again at the next startup
        return False


def capitalize_like(original, translated):
    """If the original starts with a capital letter, so does the translation (Google writes 'francese' for 'French')."""
    if original[:1].isupper() and translated[:1].islower():
        return translated[:1].upper() + translated[1:]
    return translated


def _text_fingerprint(text):
    """The text reduced to lowercase letters and digits without accents (to recognize an adapted translation)."""
    simplified = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[\W_]+", "", simplified).casefold()


_WORD = re.compile(r"[^\W\d_]{3,}")


def _to_translate(line):
    """A line must be translated if it contains at least one letter; not numbers with symbols and units (no
    word of at least 3 letters: "$5k", "+$0,01/s", "x 2", "1920 x 1080", "v1.0"), which the translator would ruin
    ("+0,01/secondo", without the dollar)."""
    if not line or not any(c.isalpha() for c in line):
        return False
    return not (any(c.isdigit() for c in line) and not _WORD.search(line))


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def read_info(path):
    for suffix in INFO_SUFFIXES:
        try:
            with open(path + suffix, encoding="utf-8") as f:
                info = json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        return info if isinstance(info, dict) and info.get("hash_originale") else None
    return None


def save_info(path, info):
    adapter_webjson.write_atomic(
        path + INFO_SUFFIX, json.dumps(info, ensure_ascii=False, indent=2).encode("utf-8"))
    if os.path.exists(path + OLD_INFO_SUFFIX):  # old name: remove it (the game loaded it as a language)
        os.remove(path + OLD_INFO_SUFFIX)


def _remove_info(path):
    for suffix in INFO_SUFFIXES:
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def create_backup(path, extra=None):
    """Verified and atomic backup of the original. Returns the info just saved ('extra' is added to it
    before the single save). If something goes wrong no temporary file is left."""
    backup = path + BACKUP_SUFFIX
    temp_file = backup + adapter_webjson.TMP_SUFFIX
    info_saved = False
    before = None  # info that was already there (e.g. a game update): put back if something goes wrong
    for suffix in INFO_SUFFIXES:
        if os.path.exists(path + suffix):
            with open(path + suffix, "rb") as f:
                before = (suffix, f.read())
            break
    try:
        shutil.copyfile(path, temp_file)  # not copy2: the file's read-only flag must not pass to the backup
        with open(temp_file, "rb+") as f:
            os.fsync(f.fileno())
        fingerprint = _hash_file(path)
        if _hash_file(temp_file) != fingerprint:
            raise RuntimeError("Backup failed for %s (the copy differs from the original): nothing was changed."
                               % path)
        info = {"hash_originale": fingerprint, "hash_scritti": [], "creati": [], "precedenti": []}
        info.update(extra or {})
        save_info(path, info)
        info_saved = True
        os.replace(temp_file, backup)
    except BaseException:
        to_remove = [temp_file] + ([path + INFO_SUFFIX] if info_saved else [])
        for p in to_remove:
            if os.path.exists(p):
                try:
                    os.chmod(p, stat.S_IREAD | stat.S_IWRITE)
                    os.remove(p)
                except OSError:
                    pass
        if before and info_saved:
            try:
                adapter_webjson.write_atomic(path + before[0], before[1])
            except OSError:
                pass
        raise
    return info


def _has_tag(path):
    if not path.lower().endswith((".html", ".htm")):
        return False
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return adapter_webui.SCRIPT_NAME in f.read()
    except OSError:
        return False


def restore_file(path, log):
    """Brings a game file back to the original using backup and info. Returns True if it acted."""
    backup = path + BACKUP_SUFFIX
    info = read_info(path)
    name = os.path.basename(path)
    if not info:
        if os.path.exists(backup):
            log("  ! %s: backup without information, leaving it in place (check it by hand)" % name)
        return False
    folder = os.path.dirname(path)
    if os.path.exists(backup):
        if _hash_file(backup) != info["hash_originale"]:
            log("  ! %s: the backup doesn't match the recorded original, NOT using it (check it by hand)" % name)
            return False
        if not os.path.exists(path):
            log("  %s: The game has removed the file, I'm just deleting the backup" % name)
        else:
            current = _hash_file(path)
            if current in info.get("hash_scritti", []) or _has_tag(path):
                with open(backup, "rb") as f:
                    adapter_webjson.write_atomic(path, f.read())
                if _hash_file(path) != info["hash_originale"]:
                    log("  ! %s: restore not verified, the backup stays in place" % name)
                    return False
                log("  %s: Restored (identical to the original)" % name)
            elif current == info["hash_originale"]:
                log("  %s: already original" % name)
            elif info.get("configuratore") or info.get("solo_creati"):
                # file replaced by an update: the configurator's change is gone and the backup
                # (of an old version) is not needed: the file stays the new one
                log("  %s: updated by the game, PatchSmith's change is no longer there" % name
                    if info.get("configuratore") else "  %s: Updated by the game (not modified by PatchSmith)"
                    % name)
            else:
                log("  ! %s: It has been changed after the translation (update or manual modification): I'll leave it "
                    "as is and keep the original in %s" % (name, os.path.basename(backup)))
                for other in info.get("creati", []):
                    _remove_created(folder, other, info, log)
                return False
        os.remove(backup)
    for other in info.get("creati", []):
        _remove_created(folder, other, info, log)
    for other in info.get("precedenti", []):
        p = os.path.join(folder, other)
        if os.path.exists(p):
            os.remove(p)
    _remove_info(path)
    return True


def _remove_created(folder, name, info, log):
    """Removes a file added by PatchSmith (e.g. local/italian.json). If it has been changed in the meantime (by hand,
    or it now belongs to someone else) it is not lost: it is set aside as <nome>.patchsmith-modificato, which the game
    does not load."""
    p = os.path.join(folder, name)
    if not os.path.exists(p):
        return
    expected = (info.get("hash_creati") or {}).get(name)
    if expected and _hash_file(p) != expected:
        os.replace(p, p + ".patchsmith-modificato")
        log("  ! %s had been changed after the translation: removed from the game but kept as %s"
            % (name, name + ".patchsmith-modificato"))
        return
    os.remove(p)
    log("  %s: Removed (it was added by PatchSmith)" % name)


def find_info(folder):
    """All the game's files that have a .patchsmith-info (that is, changed by us)."""
    found = set()
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d.lower() not in ("node_modules", ".git")]
        for f in files:
            for suffix in INFO_SUFFIXES:
                if f.endswith(suffix):
                    found.add(os.path.join(root, f[: -len(suffix)]))
    return sorted(found)


def _groups(texts, texts_limit, max_chars=None):
    """Texts split into groups (one request to the translator each, the translation memory is saved after each
    group)."""
    groups, group, chars = [], [], 0
    for t in texts:
        if group and (len(group) >= texts_limit or
                       (max_chars and chars + len(t) + 1 > max_chars)):
            groups.append(group)
            group, chars = [], 0
        group.append(t)
        chars += len(t) + 1
    if group:
        groups.append(group)
    return groups


def _core_contexts(split_parts, cores, contexts):
    """The context of each line to translate: that of the text it comes from; of the official translations the
    matching line is given if they have the same number of lines, the whole text if there is only one line, nothing if
    it is unknown which piece matches (with the whole text the translator would add sentences that are not there)."""
    by_core = {}
    for text, parts in split_parts.items():
        context = contexts.get(text)
        if not context:
            continue
        lines = [n for _, n, _ in parts if n]
        for i, n in enumerate(lines):
            if n in by_core or not _to_translate(n):
                continue
            selected = {}
            for code, r in (context.get("riferimenti") or {}).items():
                lines_r = [x.strip() for x in r.split("\n") if x.strip()]
                if len(lines_r) == len(lines):
                    selected[code] = lines_r[i]
                elif len(lines) == 1:
                    selected[code] = " ".join(lines_r)
            by_core[n] = {"chiave": context.get("chiave"), "riferimenti": selected,
                             "larghezza": context.get("larghezza") if len(lines) == 1 else None,
                             "non_usare": context.get("non_usare")}
    return [by_core.get(n) for n in cores]


def shared_path(orig, dest):
    """Translation memory shared by all games for short texts (menus and buttons: 'options', 'back', 'press any
    key')."""
    return data_folder("memory", "comune.%s-%s.json" % (orig, dest))


def _is_short(text):
    """Text for the shared translation memory: one line, 1 to 4 words, at most 40 characters, with at least one
    letter. Long sentences almost never repeat identically across different games."""
    return "\n" not in text and len(text) <= 40 and 1 <= len(text.split()) <= 4 and any(c.isalpha() for c in text)


MAX_LABEL_WORDS = 3  # short labels (buttons, menu entries): up to 3 words...
MAX_LABEL_CHARS = 30  # ...and 30 characters


def short_label(text):
    """True for a button or menu text: one line, 1 to 3 words, at most 30 characters, with letters."""
    t = (text or "").strip()
    return bool(t) and "\n" not in t and len(t) <= MAX_LABEL_CHARS and \
        1 <= len(t.split()) <= MAX_LABEL_WORDS and any(c.isalpha() for c in t)


def label_space(original, references=(), space=None):
    """The space of a short label, in 'visual' characters (backend._weight: a Chinese character counts as two and a
    half), rounded up: 1.3 times the original plus 2, or the game's longest official translation
    (of one line) or the longest line of its versions ('spazio', GameMaker), if those are more.
    If the game says nothing about its space (no official translation): only translations at least twice as long
    and 7 characters longer ('Credits' -> 'Ringraziamenti', 'Slider' -> 'Dispositivo di scorrimento'). On real
    translation memories the 1.3 rule alone flagged 15-30% of the labels, even the ordinary 'Statistiche' and
    'Combattimento'; this way it is 3-8%, the obvious cases."""
    x = _weight(original.strip())
    maximum = 1.3 * x + 2
    officials = [_weight(r.strip()) for r in references or () if isinstance(r, str) and r.strip()
                 and "\n" not in r.strip()]
    if officials or space:
        maximum = max([maximum, space or 0] + officials)
    else:
        maximum = max(maximum, 2 * x - 1, x + 6)
    return math.ceil(maximum - 1e-9)


def memory_path(profile, test=False):
    return data_folder("memory", "%s.%s%s.json" % (
        profile["nome"], profile["lingua_destinazione"], ".prova" if test else ""))


def problems_path(profile, test=False):
    """A game's texts with a problem (memory/<gioco>.<lingua>.problems.json): {text: {motivo, dove,
    tentativi, quando, ...}}. 'dove': 'traduzione' (no valid translation, not even after the new attempts),
    'gioco' (the saved translation cannot be written to the game: different form, characters the fonts lack),
    'memoria' (the saved translation does not pass the current checks and redoing it did not succeed), 'lunghezza'
    (short label longer than its space even after asking for a shorter one, see Project._shorten: shown in "Fix
    translations", list "Too long"). 'tentativi': the translator passes already made for that text; they decide the
    seeds of the local translator (see backend.Local._attempts): each round, even in another run, asks new
    questions."""
    return memory_path(profile, test)[:-len(".json")] + ".problemi.json"


def shared_origin_path(orig, dest):
    """Where each entry of the shared translation memory comes from: {text: {backend, modello, versione, gioco, mano}}.
    Entries without one (put there by previous versions, which copied all the short texts into it) are used only if
    they pass the current checks (see valid_shared_entry)."""
    return shared_path(orig, dest)[:-len(".json")] + ".origine.json"


def protected_pattern(terms):
    """The expression that finds the game's codes and names in a text (like testo.proteggi), or None."""
    terms = [t for t in terms or () if t]
    if not terms:
        return None
    return re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(
        re.escape(t) for t in sorted(terms, key=len, reverse=True)))


def lost_names(original, translated, pattern):
    """The names (or codes) of the original that are no longer in the translation (e.g. 'Cup Dog Games' translated)."""
    if pattern is None:
        return []
    return sorted(collections.Counter(pattern.findall(original)) - collections.Counter(pattern.findall(translated)))


def entry_problems(original, translated, dest, pattern=None, references=()):
    """Why a saved translation is not good (empty = it is fine): the checks of the local translator
    (backend.translation_problems: letters of another language, numbers, line breaks...), a game name translated, an
    official translation of another language copied."""
    if not isinstance(translated, str) or not translated.strip():
        return ["vuota"]
    if pattern is not None and _unchanged(original, translated) and \
            not _to_translate(pattern.sub("", protect(original)[0]).replace("[[", "").replace("]]", "")):
        return []  # only the game's names and codes ('Cult Of The Lamb'): they stay as they were on purpose
    problems = translation_problems(original, translated, dest)
    if dest.split("-")[0] in language._WORDS and not _unchanged(original, translated) and \
            language.obvious_language(words_only(translated), dest):  # e.g. left in English or came out in Spanish
        problems.append("in another language")
    if lost_names(original, translated, pattern):
        problems.append("translated name")
    # only for sentences: a word identical in the two languages is normal ('Oro' in Spanish and in Italian, 'Ferro' in
    # Portuguese), and the local translator accepts it if it says it again without the official translations in view
    if references and len(re.findall(r"[^\W\d_]+", translated)) >= 3 and not _unchanged(original, translated) and \
            any(r and _unchanged(r, translated) for r in references):
        problems.append("copied reference")
    return problems


def valid_shared_entry(text, translated, dest, origin=None, pattern=None):
    """True if an entry of the shared translation memory can be used for a game: with its origin (put there by this
    version, or fixed by hand) it is enough that it does not translate a game name; without it, it must also pass the
    current checks (entry_problems)."""
    if not isinstance(translated, str) or not translated.strip():
        return False
    if origin:
        return not lost_names(text, translated, pattern)
    return not entry_problems(text, translated, dest, pattern)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _adapt_tmp(translated, original, allowed, excluded, dest):
    """(translation with only the characters the TextMeshPro fonts can draw, '') or (None, characters that cannot
    be replaced). ammessi: the fonts' characters (font_unity; None if unknown); esclusi: those the game said it
    does not have (Unity log): replaced only with ASCII characters if nothing is known about the fonts."""
    if allowed is not None:
        item_set = Filter(allowed, excluded, original)
        if all(c in item_set or c in "\r\n" for c in translated):
            return translated, ""
        is_ready = adapt_chars(translated, item_set, dest)
        if is_ready is not None and any(c.isalpha() for c in is_ready):
            return is_ready, ""
        return None, undrawable(translated, item_set, dest)
    is_ready = adapt_without(translated, excluded, dest)
    if is_ready is not None and any(c.isalpha() for c in is_ready):
        return is_ready, ""
    return None, "".join(c for c in dict.fromkeys(translated)
                         if c in excluded and adapt_without(c, excluded, dest) is None)


class Project:
    def __init__(self, profile_path, log=print, test=False):
        self.profile_path = os.path.abspath(profile_path)
        self.profile = self._read_json(self.profile_path, None, "profilo")
        self.log = log
        self.memory_path = memory_path(self.profile, test)
        self.test = test
        self.memory = self._read_json(self.memory_path, {}, "translation file")
        # the translation memory is also written by the translator started by the game: only our own changes are saved
        self._memory_file = shared_file.MergingFile(self.memory_path, self.memory)
        # for the outcome of the operation (see outcome.py): why the translation stopped and what apply() wrote
        self.translation_outcome = None  # None, "TRADUTTORE_BLOCCATO" or "NULLA_DA_FARE"
        self.translation_reason = ""
        self.last_apply = None  # {ok, mancanti, esempi, scartate, scritti, aperto}
        # for the live translator (xunity_service): the translator error that stopped the last translation and
        # the last translated group ({divisi, protetti, mappa, ripiegati}), to build a fallback answer
        self.last_error = None
        self.last_group = None
        # False in the live translator (xunity_service): one sentence at a time while playing, no second rounds,
        # no revalidation nor problems.json (its diary notes the rejected sentences)
        self.recovery = True
        self._min_round = {}  # {text: round}: saved translations to redo already count one round (see _rounds)
        self.last_failed = []  # texts of the last translation left without a valid translation (after new rounds)
        self.redone = 0  # saved translations redone in the last translation (suspect or unfit for the game)
        # labels asked shorter and shortened, and those noted as too long (see _shorten), over the whole
        # operation (translate() may translate twice)
        self.shortened = self.long_noted = 0
        self.problems_path = problems_path(self.profile, test)
        self._problems = self._read_problems()
        self._problems_file = shared_file.MergingFile(self.problems_path, self._problems)
        self._machine = None  # read when needed (_read_machine)
        self._names = ()  # game names not to translate, for the texts of this translation (_prepare_names)
        self._common_words = None
        self._migrate_old_state()
        # invisible characters inserted by Google in translations of previous versions: remove them
        cleaned_entries = {k: clean(v) for k, v in self.memory.items() if isinstance(v, str) and clean(v) != v}
        if cleaned_entries:
            self.memory.update(cleaned_entries)
            self._save_memory()

    @staticmethod
    def _read_json(path, default, description):
        try:  # (retries if another program is saving it right now: see shared_file)
            return shared_file.read_json(path)
        except FileNotFoundError:
            if default is None:
                raise
            return default
        except ValueError as e:
            raise RuntimeError("The %s is not a valid JSON: %s (%s). Please correct it and try again." % (
                description, path, e)) from e

    def _migrate_old_state(self):
        """Versions 0.1-0.2 kept the fingerprints in profili/<nome>.stato.json: move them into the game."""
        old = os.path.splitext(self.profile_path)[0] + ".stato.json"
        if not os.path.exists(old):
            return
        state = self._read_json(old, {}, "status file")
        for rel, data in state.get("file", {}).items():
            path = self._game_path(rel)
            if os.path.exists(path + BACKUP_SUFFIX) and not read_info(path) and data.get("hash_originale"):
                created = [os.path.basename(c) for c in data.get("creati", [])]
                save_info(path, {"hash_originale": data["hash_originale"],
                                      "hash_scritti": [h for h in [data.get("hash_scritto")] if h],
                                      "creati": created, "precedenti": []})
        os.remove(old)

    def _save_memory(self):
        """Saves the new (or changed or removed) translations and takes those saved in the meantime by others (e.g. the
        translator started by the game): see shared_file."""
        self._memory_file.save(self.memory)

    def _read_problems(self):
        """The texts with a problem (see problems_path). The user never writes the file: if it is damaged it
        starts over from scratch (on save the damaged copy stays in .illeggibile)."""
        try:
            data = shared_file.read_json(self.problems_path)
        except (OSError, ValueError):
            return {}
        return {k: v for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}

    def _save_problems(self):
        try:
            self._problems_file.save(self._problems)
        except OSError as e:
            self.log("  ! List of texts with problems not saved (%s)" % e)

    def problems(self):
        """Copy of the texts with a problem: {text: {motivo, dove, tentativi, ...}} (see percorso_problemi)."""
        return {k: dict(v) for k, v in self._problems.items()}

    def _mark_problem(self, text, **fields):
        """Records (or updates) the problem of a text. Always a new dictionary: MergingFile compares the entries with
        how they were, and a dictionary changed in place would not appear changed."""
        entry = dict(self._problems.get(text) or {})
        entry.update({k: v for k, v in fields.items() if v is not None})
        entry["quando"] = _now()
        self._problems[text] = entry

    def _read_machine(self):
        """The machine translations (<memoria>.machine.json): an entry of the translation memory equal to that
        one is a machine translation, a different one was fixed by hand (and is not touched). Not kept in the test
        memory."""
        if self._machine is None:
            self._machine = {} if self.test else \
                self._read_json(self._machine_path(), {}, "automatic translation file")
            self._machine_file = shared_file.MergingFile(self._machine_path(), self._machine)
        return self._machine

    def is_machine(self, text):
        """True if the saved translation of the text is the machine translation (not fixed by hand)."""
        v = self.memory.get(text)
        return isinstance(v, str) and self._read_machine().get(text) == v

    def _game_path(self, rel):
        return os.path.join(self.profile["cartella_gioco"], *rel.split("/"))

    # --- originals and updates ----------------------------------------------------------------

    def _looks_translated(self, path, entry):
        """True if the file mostly contains our translations (that is, it is the translated file, changed by hand)."""
        if not entry or entry.get("formato") == "web-ui":
            return False
        try:
            if entry.get("formato") == "righe":
                texts = [t for _, t in adapter_lines.read_file(path).units]
            elif entry.get("formato") == "csv":
                f = adapter_csv.read_file(path)
                texts = [t for _, t in f.texts(self._column(f, entry))]
            elif entry.get("formato") == "unity-tabelle":
                texts = [t for _, t in adapter_unity_loc.TablesFile(path).texts()]
            else:
                texts = [t for _, t in self._extract_json(adapter_webjson.JSONFile(path).data, entry)]
        except (OSError, ValueError, KeyError):
            return False
        # in the file the translations may be adapted to the game (lowercase, font characters, line breaks): only
        # letters and digits are compared, without accents and capitals
        translated_items = {_text_fingerprint(v) for v in self.memory.values() if isinstance(v, str)}
        originals = {_text_fingerprint(k) for k in self.memory}
        ours = sum(1 for t in texts
                   if _text_fingerprint(t) in translated_items and _text_fingerprint(t) not in originals)
        return len(texts) >= 3 and ours >= 0.3 * len(texts)

    def _original(self, path, update=True, entry=None):
        """Path of the file to extract the original texts from (None if the file no longer exists)."""
        if not os.path.exists(path):
            return None
        backup = path + BACKUP_SUFFIX
        if not os.path.exists(backup):
            return path
        info = read_info(path)
        if not info and _hash_file(backup) == _hash_file(path):
            info = {"hash_originale": _hash_file(backup), "hash_scritti": [], "creati": [], "precedenti": []}
            save_info(path, info)
        if not info:
            raise RuntimeError("For %s, there's a backup, but it doesn't contain its information: I can't verify if "
                               "it's reliable. Check manually (or delete the backup if the file is original)." % path)
        if _hash_file(backup) != info["hash_originale"]:
            raise RuntimeError("The backup of %s doesn't match the recorded original: stopping." % path)
        current = _hash_file(path)
        if current == info["hash_originale"] or current in info.get("hash_scritti", []) or _has_tag(path):
            return backup
        # The translated file was changed by hand: the original stays the one in the backup (it is not lost)
        if self._looks_translated(path, entry):
            if update:
                self.log("  ! %s was modified by hand after the translation: rewriting it from the original (to fix a "
                         "translation, use 'Fix translations')" % os.path.basename(path))
            return backup
        # The file was changed by someone else: an update of the game (or of another mod).
        if not update:
            return path
        n = len(info.get("precedenti", [])) + 1
        old = "%s.%d" % (os.path.basename(backup), n)
        os.replace(backup, os.path.join(os.path.dirname(backup), old))
        keep = {k: info[k] for k in ("creati", "hash_creati", "solo_creati") if k in info}
        try:
            create_backup(path, dict(keep, precedenti=info.get("precedenti", []) + [old]))
        except BaseException:
            os.replace(os.path.join(os.path.dirname(backup), old), backup)  # everything as it was
            raise
        self.log("  ! %s has changed (game update?): it's the new original, the previous one is in %s"
                 % (os.path.basename(path), old))
        return backup

    def _load(self, entry, update=True):
        """(object, [(position, text)]) for the entry; (None, None) if the file no longer exists."""
        path = self._game_path(entry["percorso"])
        source = self._original(path, update, entry)
        if source is None:
            return None, None
        if entry.get("formato", "json") == "web-ui":
            return None, [(None, t) for t in adapter_webui.extract_texts(os.path.dirname(path), self._once)]
        if entry.get("formato") == "righe":
            f = adapter_lines.read_file(source)
            return f, f.texts()
        if entry.get("formato") == "csv":
            f = adapter_csv.read_file(source)
            return f, f.texts(self._column(f, entry))
        if entry.get("formato") == "unity-tabelle":
            f = adapter_unity_loc.TablesFile(source)
            return f, f.texts()
        f = adapter_webjson.JSONFile(source)
        return f, self._extract_json(f.data, entry)

    def _once(self, message):
        """Writes a message to the log only once (the files are read several times: translation, writing)."""
        seen = self.__dict__.setdefault("_detti", set())
        if message not in seen:
            seen.add(message)
            self.log(message)

    @staticmethod
    def _column(f, entry):
        """The index of the column to translate of a CSV table (voce["colonna"]: its name in the header)."""
        i = f.column(entry.get("colonna"))
        if i is None:
            raise adapter_csv.CSVError("the column '%s' no longer exists in the table" % entry.get("colonna"))
        return i

    @staticmethod
    def _raw_json(data, entry):
        """[(position, text as it is in the file)]: in localization files every value with letters is a text."""
        readable = adapter_gamemaker.localization_text if entry.get("localizzazione") else is_readable_text
        return adapter_webjson.extract(data, entry["includi"], readable)

    @classmethod
    def _extract_json(cls, data, entry):
        """[(position, text to translate)]: with the game's conventions (line break '*', symbol at paragraph start)
        the text is normalized (whole sentences, paragraphs on separate lines)."""
        raw_texts = cls._raw_json(data, entry)
        if entry.get("a_capo") or entry.get("punto_elenco"):
            return [(p, adapter_gamemaker.normalize_line_breaks(t, entry.get("a_capo"), entry.get("punto_elenco")))
                    for p, t in raw_texts]
        return raw_texts

    def _prepare_write(self, path, data_bytes):
        """Backup (once) and recording of the fingerprint BEFORE writing."""
        info = read_info(path) if os.path.exists(path + BACKUP_SUFFIX) else None
        if info is None:
            info = create_backup(path)
        fingerprint = _hash_bytes(data_bytes)
        writes = [h for h in info.get("hash_scritti", []) if h != fingerprint] + [fingerprint]
        info["hash_scritti"] = writes[-MAX_FINGERPRINTS:]
        info["patchsmith_versione"] = __version__  # which version wrote it (for future checks)
        save_info(path, info)
        return info

    # --- commands -------------------------------------------------------------------------------

    @property
    def codes(self):
        """Words of the game not to translate (font icons, e.g. 'EP' and 'H' in Forager), from all entries."""
        return sorted({c for v in self.profile.get("file", []) for c in v.get("codici", [])})

    def _prepare_names(self, texts=()):
        """The game names not to translate for this translation (profilo["non_tradurre"]: studio and title, see
        names.py). A one-word name that also appears in lowercase in the game's texts ('Control') is an ordinary word
        and is translated: the texts to translate and those already in the translation memory are looked at."""
        items = self.profile.get("non_tradurre") or []
        if not items:
            self._names = ()
            return
        if self._common_words is None:
            self._common_words = game_names.common_words(self.memory)
        self._names = tuple(game_names.to_protect(items, self._common_words | game_names.common_words(texts)))

    def _protected(self):
        """The game's codes and names to protect in the texts to translate (testo.proteggi)."""
        codes = self.codes if hasattr(self, "profile") else []
        return tuple(sorted(set(codes) | set(getattr(self, "_names", ()))))

    def _to_redo(self, texts, dest, contexts=None):
        """The saved translations to redo, {text: reason}: they stay in the translation memory until a better one
        arrives (if the new translation fails, the previous one stays). Only machine translations (fixes made by
        hand are not touched):
        - that do not pass the current checks (entry_problems: letters of another language, numbers, different line
          breaks, a game name translated, an official translation of another language copied). Previous versions, and
          Google, did not have these checks;
        - that could not be written to the game (problems.json, dove 'gioco': form different from the original,
          characters the game's fonts lack): they are redone with the right instruction (see _with_fixes).
        In addition a text that is only a game name ('Cup Dog Games') and is translated in the translation memory, even
        without a trace of the machine translation (memories of previous versions). After MAX_ATTEMPTS passes a
        translation is left as it is."""
        if not self.recovery:
            return {}
        machine = self._read_machine()
        pattern = protected_pattern(self._protected())
        names_pattern = protected_pattern(self._names)
        redo = {}
        for t in texts:
            v = self.memory.get(t)
            if not isinstance(v, str) or (self._problems.get(t) or {}).get("tentativi", 0) >= MAX_ATTEMPTS:
                continue
            if names_pattern and names_pattern.fullmatch(t.strip()) and v.strip() != t.strip() and \
                    (machine.get(t) == v or t not in machine):  # machine translation or from a previous version
                redo[t] = "game name translated"
                continue
            if machine.get(t) != v:
                continue
            problem = self._problems.get(t) or {}
            if problem.get("dove") == "gioco" and problem.get("valore") == v:
                redo[t] = problem.get("motivo") or "not suitable for the game"
                continue
            references = ((contexts or {}).get(t) or {}).get("riferimenti") or {}
            reasons = entry_problems(t, v, dest, pattern, list(references.values()))
            if reasons:
                redo[t] = "suspicious saved translation (%s)" % ", ".join(reasons)
        if redo:
            example = next(iter(redo))
            self.log("  %d saved translations aren't right (e.g. %r: %s): redoing them; if that fails, the previous "
                     "one stays" % (len(redo), example[:50], redo[example]))
        return redo

    def _with_fixes(self, texts, contexts):
        """The contexts for the local translator with the instructions for the texts whose translation could not be
        written to the game: the characters not to use (those the game's fonts cannot draw, or the game's line break
        sign written inside the sentence). The labels noted as too long ('lunghezza', see _shorten), if
        re-translated (e.g. "Re-translate" in the "Too long" list), are requested with their space ('larghezza')."""
        output = dict(contexts or {})
        for t in texts:
            problem = self._problems.get(t) or {}
            do_not_use = problem.get("non_usare")
            if do_not_use:
                output[t] = dict(output.get(t) or {}, non_usare=do_not_use)
            space = problem.get("spazio")
            if problem.get("dove") == "lunghezza" and isinstance(space, int) and space > 0 and short_label(t):
                output[t] = dict(output.get(t) or {}, larghezza=space)
        return output

    def _forget_without_codes(self, texts):
        """The saved translations that lost a code of the original (e.g. 'max H' translated 'massimo', without
        the heart icon) are redone: previous versions did not protect the codes."""
        if not self.codes:
            return
        pattern = re.compile(r"(?<![^\W\d_])(?:%s)(?![^\W\d_])" % "|".join(map(re.escape, self.codes)))
        dropped = 0
        for t in set(texts):
            v = self.memory.get(t)
            if isinstance(v, str) and sorted(pattern.findall(t)) != sorted(pattern.findall(v)):
                del self.memory[t]
                dropped += 1
        if dropped:
            self.log("  %d saved translations had lost a game icon (%s): redoing them"
                     % (dropped, ", ".join(self.codes)))
            self._save_memory()

    def _contexts(self, entry, units, dest):
        """{text: context} for the local translator: the key of the text in the file (without the final number: it is a
        hint, not a text to translate), the game's official translations (at most 3 languages) and the space
        they take (for short labels)."""
        # for Italian, Spanish and Portuguese together are too close: the model copied them ('saque', 'crânio')
        preferred = ("fr", "de", "es", "pl", "ru", "pt", "tr")
        if entry.get("formato") == "csv":
            return self._csv_contexts(entry, units, dest, preferred)
        references = entry.get("riferimenti") or {}
        choices = [c for c in preferred if c in references and c.split("-")[0] != dest.split("-")[0]]
        choices += [c for c in sorted(references) if c not in choices and c.split("-")[0] != dest.split("-")[0]
                   and language.endonym(c)]
        data = {}
        for c in choices[:3]:
            try:
                data[c] = adapter_gamemaker.read_original(self._game_path(references[c]),
                                                            adapter_gamemaker._read_json)
            except (OSError, ValueError):
                continue
        limits = entry.get("limiti") or {}
        output = {}
        for pos, text in units:
            if text in output or not isinstance(pos, tuple) or not pos:
                continue
            key = adapter_gamemaker.key_of(pos)
            ref = {}
            for c, d in data.items():
                v = adapter_gamemaker.at_path(d, pos)  # also inside the sections ('menu', '_play')
                if isinstance(v, str) and v.strip():
                    ref[c] = adapter_gamemaker.normalize_line_breaks(v, entry.get("a_capo"),
                                                                     entry.get("punto_elenco")) \
                        if entry.get("a_capo") or entry.get("punto_elenco") else v
            limit = limits.get(key)
            # 'spazio': the longest line of the game's versions, for one-line labels (see _shorten)
            output[text] = {"chiave": re.sub(r"[_\-.]?\d+$", "", key), "riferimenti": ref,
                             "spazio": limit[0] if limit and limit[1] == 1 else None}
        return output

    def _csv_contexts(self, entry, units, dest, preferred):
        """Like _contexts, for a CSV table: the key is the cell of the keys column, the official translations
        are the other columns of the same row (at most 3 languages)."""
        f = adapter_csv.read_file(self._original(self._game_path(entry["percorso"]), update=False, entry=entry))
        codes = [c for i, c in f.languages.items() if c.split("-")[0] != dest.split("-")[0]
                  and i != self._column(f, entry)]
        choices = [c for c in preferred if c in codes] + [c for c in codes if c not in preferred]
        choices = list(dict.fromkeys(choices))[:3]
        output = {}
        for pos, text in units:
            if text not in output:
                key = f.key_of(pos)
                output[text] = {"chiave": re.sub(r"[_\-.]?\d+$", "", key), "spazio": None,
                                 "riferimenti": f.references(pos, choices)}
        return output

    def translate(self, backend):
        orig = self.profile.get("lingua_origine", language.AUTO)
        dest = self.profile["lingua_destinazione"]
        texts, contexts = [], {}
        for entry in self.profile["file"]:
            _, units = self._load(entry)
            texts += [t for _, t in units or []]
            if units and entry.get("localizzazione"):
                # key and official translations: to the local translator as context, and to recognize a copied
                # official translation among the saved ones (see _to_redo)
                for t, c in self._contexts(entry, units, dest).items():
                    contexts.setdefault(t, c)
        if orig == language.AUTO:
            orig, reason = language.detect(texts)
            self.log("Game language detected: %s" % language.label(orig))
            self.log(DETAIL + "  (language detected from: %s)" % reason)
        if orig.split("-")[0] == dest.split("-")[0]:
            self.log("The game is already in %s: nothing to translate." % language.label(dest))
            self.translation_outcome = "NULLA_DA_FARE"
            self.translation_reason = "The game is already in %s: there's nothing to translate." % language.label(dest)
            interruption.check()
            return self.apply()
        present = set(texts)
        old_files = [t for t in self._problems if t not in present]  # texts the game no longer has
        for t in old_files:
            del self._problems[t]
        if old_files:
            self._save_problems()
        failed = self.translate_texts(texts, backend, orig, dest, contexts)
        interruption.check()  # stopped during the last request: nothing is written to the game
        done = self.apply()
        # machine translations the game cannot show (different form, characters the fonts lack): another round
        # right away with the right instruction, only once
        redo = [t for t in self._refused if self.is_machine(t)
                  and (self._problems.get(t) or {}).get("tentativi", 0) < MAX_ATTEMPTS]
        if redo and self.recovery and self.translation_outcome is None:
            self.log("  %d translations can't be written to the game: redoing them right away with the right "
                     "instruction"
                     % len(redo))
            leading, redone = list(self.last_failed), self.redone
            failed += self.translate_texts(redo, backend, orig, dest, contexts)
            self.last_failed = list(dict.fromkeys(leading + self.last_failed))
            self.redone += redone
            interruption.check()
            done = self.apply()
        return done and not failed

    def active_names(self, texts=()):
        """The game names that stay as they are in the translation of these texts (see _prepara_nomi)."""
        self._prepare_names(texts)
        return self._names

    # --- fixes made with "Fix translations" (see corrections.py) ---------------------------------------

    def source_language(self, texts=None):
        """The game's language: that of the game profile, or detected from the texts (those given or those in the
        translation memory)."""
        orig = self.profile.get("lingua_origine") or language.AUTO
        if orig == language.AUTO:
            sample = list(texts) if texts is not None else list(self.memory)
            orig = language.detect(sample[:5000])[0] if sample else "en"
        return orig

    def fix(self, changes):
        """The translations fixed by hand with "Fix translations" ({text: translation}):
        - they go into the translation memory (saved with the lock, see shared_file: the sentences added in the
          meantime by the translator started by the game stay);
        - they leave problems.json if they were left untranslated or suspect (now there is yours); those the game could
          not show stay until "Reapply" manages to write them;
        - the short texts go into the shared translation memory as fixes made by hand (see _shared_memory), even those
          that had no machine translation (e.g. left untranslated and translated by you).
        The machine translation stays in .machine.json: the fix is always recognized and never touched again. A
        text that was not there (left untranslated, or translated by a version that did not record the machine
        translations: for PatchSmith an entry without a record is a machine translation) enters it with the previous
        translation (empty if there was none), so it too counts as fixed by hand.
        Returns the texts whose translation in the translation memory changed."""
        changed = [t for t, v in changes.items() if isinstance(t, str) and isinstance(v, str) and v.strip()
                    and self.memory.get(t) != v]
        before = {t: self.memory.get(t) for t in changed}
        for t in changed:
            self.memory[t] = changes[t]
        if changed:
            self._save_memory()
        if changed and not self.test:
            machine = self._read_machine()
            annotated = {t: before[t] if isinstance(before[t], str) else "" for t in changed if t not in machine}
            if annotated:
                machine.update(annotated)
                try:
                    self._machine_file.save(machine)
                except OSError as e:
                    self.log("  ! Automatic translations not recorded (%s)" % e)
        dropped = [t for t in changes if (self._problems.get(t) or {}).get("dove") in ("traduzione", "memoria")]
        for t in dropped:
            del self._problems[t]
        if dropped:
            self._save_problems()
        if self.test or not changed:
            return changed
        dest = self.profile["lingua_destinazione"]
        orig = self.source_language()
        if orig.split("-")[0] == dest.split("-")[0]:
            return changed
        shared = self._shared_memory(orig, dest)  # (also this game's earlier fixes made by hand)
        name, additions = self.profile.get("nome"), 0
        for t in changed:
            v = self.memory[t]
            if not _is_short(t) or v.strip() == t.strip() or shared.get(t) == v:
                continue
            already = self._shared_origin.get(t) or {}
            if already.get("mano") and already.get("gioco") != name:  # fixed by hand in another game: that one stays
                continue
            shared[t] = v
            self._shared_origin[t] = {"mano": True, "gioco": name, "versione": __version__}
            additions += 1
        if additions:
            try:
                self._shared_file.save(shared)
            except OSError as e:
                self.log("  ! Shared memory not updated (%s)" % e)
                return changed
            self._save_origin()
        return changed

    def retranslate(self, texts, backend, orig, dest, contexts=None, round_=1):
        """Re-translates the given texts now, as requested with "Fix translations": also those already in the
        translation memory and those fixed by hand (the choice is the user's). If the new translation fails the previous
        one stays. giro: which round the local translator's seeds start from (1 or more = new seeds and slightly higher
        temperatures: the same question would give the same answer; 0 = the questions of the first round, for example
        with the other model). The new translations become machine translations (.machine.json) and the short texts
        go into the shared translation memory as in a normal translation. Returns (texts with a new translation, texts
        left as they were)."""
        unique = [t for t in dict.fromkeys(texts) if isinstance(t, str) and _to_translate(t)]
        self._prepare_names(unique)
        self._current_backend = backend
        self.redone = 0
        self.last_failed = []
        self._succeeded = set()
        shared = None
        if not self.test and orig != language.AUTO:
            shared = self._shared_memory(orig, dest)
        else:
            self._read_machine()
        before = {t: self.memory.get(t) for t in unique}
        self._min_round = dict.fromkeys(unique, round_) if round_ else {}
        contexts = self._with_fixes(unique, contexts)
        self.log("Retranslating %d texts (%d characters)%s" % (len(unique), sum(len(t) for t in unique),
                                                         " with new prompts" if round_ else ""))
        try:
            self._translate_missing(unique, backend, orig, dest, contexts)
        finally:
            succeeded = [t for t in unique if t in self._succeeded]
            new_lines = [t for t in succeeded if self.memory.get(t) != before.get(t)]
            if not self.test:
                self._update_machine(succeeded)
            if shared is not None:
                self._update_shared(shared, orig, dest, new_lines, before)
            failed, has_changed = set(self.last_failed), False
            for t in unique:
                entry = self._problems.get(t)
                if t in self._succeeded:
                    if entry and entry.get("dove") != "gioco":  # those unfit for the game are judged by apply()
                        del self._problems[t]
                        has_changed = True
                elif t in failed and not isinstance(self.memory.get(t), str):
                    # still without a translation: as in a normal translation (new seeds next time); a saved
                    # translation that could not be redone stays as it was, without being noted as suspect
                    # (the re-translation counts as one round, even with the seeds of a later round: see
                    # corrections.retranslate)
                    self._mark_problem(t, motivo=self._failure_reasons.get(t) or "no valid translation",
                                         dove="traduzione", tentativi=max((entry or {}).get("tentativi", 0),
                                                                          min(round_, 1)) + getattr(self, "_steps", 1))
                    has_changed = True
            if has_changed:
                self._save_problems()
            self._min_round = {}
        return new_lines, [t for t in unique if t not in new_lines]

    def game_texts(self):
        """[(game profile entry, position, text to translate, text as it is in the file)] of the game files in the game
        profile, from the originals and without writing anything (for "Fix translations": the texts left untranslated
        and the space they have in the game). A file that cannot be read is skipped, with a line in the log."""
        output = []
        for entry in self.profile.get("file", []):
            try:
                f, units = self._load(entry, update=False)
                raw_texts = dict(self._raw_json(f.data, entry)) if units and isinstance(f, adapter_webjson.JSONFile) \
                    else {}
            except (OSError, ValueError, KeyError, RuntimeError, UnicodeDecodeError) as e:
                self.log("  ! %s: I can't read it (%s)" % (entry.get("percorso"), e))
                continue
            for pos, t in units or []:
                if isinstance(t, str):
                    output.append((entry, pos, t, raw_texts.get(pos, t)))
        return output

    def translate_texts(self, texts, backend, orig, dest, contexts=None, redo=None):
        """Translates the texts not yet in the translation memory and redoes the saved translations that are not good
        (see _to_redo; in 'rifare' {text: reason} others to redo, if machine translations: e.g. the wrong lines of the
        XUnity cache), in groups, saving after each group. Returns how many texts are left without a translation.
        - Short texts already translated for other games come from the shared translation memory (see
          _shared_memory).
        - Texts rejected by the checks are retried right away in another way (see _recover); those left
          without a translation go into problems.json (see problems_path) and next time they are tried with
          new parameters.
        - The game names (profilo["non_tradurre"]) stay as they are, like the codes."""
        redo_extra = redo
        unique = list(dict.fromkeys(texts))
        self._prepare_names(unique)
        self._forget_without_codes(unique)
        self._current_backend = backend
        self.redone = 0
        self.last_failed = []
        self._succeeded = set()
        self._current_texts = unique  # for labels that are too long (see _shorten)
        self._too_long = {}
        from_shared, shared = 0, None
        if not getattr(self, "test", False) and orig != language.AUTO:
            shared = self._shared_memory(orig, dest)
        else:
            self._read_machine()
        redo = self._to_redo(unique, dest, contexts)
        if self.recovery and redo_extra:
            item_set = set(unique)
            for t, reason in redo_extra.items():
                if t in item_set and t not in redo and self.is_machine(t) and \
                        (self._problems.get(t) or {}).get("tentativi", 0) < MAX_ATTEMPTS:
                    redo[t] = reason
        if shared is not None:
            pattern = protected_pattern(self._protected())
            problems_removed = False
            for t in unique:
                if (t in self.memory and t not in redo) or not _is_short(t) or t not in shared:
                    continue
                v = shared[t]
                if v == self.memory.get(t):
                    continue
                if not valid_shared_entry(t, v, dest, self._shared_origin.get(t), pattern):
                    self._shared_skipped.add(t)  # old entry that does not pass the checks: translated again
                    continue
                self.memory[t] = self._machine[t] = v
                if t in redo:
                    del redo[t]
                    self.redone += 1
                problems_removed = self._problems.pop(t, None) is not None or problems_removed
                from_shared += 1
            if from_shared:
                self._save_memory()
            if problems_removed:
                self._save_problems()
        missing = [t for t in unique if t not in self.memory or t in redo]
        before = {t: self.memory.get(t) for t in redo}  # the translations to redo, as they were
        # the saved translation is already one round: redoing it with the same questions (seeds 42, 7, 11) would give
        # the same answer, so it starts from the new seeds
        self._min_round = dict.fromkeys(redo, 1)
        redo_contexts = contexts or {}
        contexts = self._with_fixes(missing, contexts)
        self.log("Game texts: %d, already translated: %d%s, to translate: %d" % (
            len(unique), len(unique) - len(missing),
            " (%d from other games)" % from_shared if from_shared else "", len(missing)))
        self.log(DETAIL + "  (%d characters to translate)" % sum(len(t) for t in missing))
        try:
            return self._translate_missing(missing, backend, orig, dest, contexts)
        finally:
            still = self._still_to_redo(redo, before, dest, redo_contexts)
            self.redone += sum(1 for t in redo if t in self._succeeded and t not in still
                                and self.memory.get(t) != before[t])
            if not getattr(self, "test", False):
                self._update_machine(missing)
            if shared is not None:
                self._update_shared(shared, orig, dest, [t for t in missing if t not in still], before)
            self._update_problems(missing, redo, still)
            self._record_long()
            self._min_round = {}

    def _still_to_redo(self, redo, before, dest, contexts):
        """{text: reasons} of the saved translations redone now that are still not good: equal to the previous one or
        not passing the current checks (entry_problems: the local translator checks less, for example not the
        language). They count as a failed attempt (problems.json, 'memoria'), so they are not redone at every run
        beyond MAX_ATTEMPTS. Those the game could not show are judged by apply()."""
        still = {}
        if not self.recovery or not redo:
            return still
        pattern = protected_pattern(self._protected())
        for t in redo:
            v = self.memory.get(t)
            if t not in self._succeeded or not isinstance(v, str) or \
                    (self._problems.get(t) or {}).get("dove") == "gioco":
                continue
            references = ((contexts or {}).get(t) or {}).get("riferimenti") or {}
            reasons = entry_problems(t, v, dest, pattern, list(references.values()))
            if not reasons and v == before.get(t):
                reasons = ["the same as before"]
            if reasons:
                still[t] = reasons
        return still

    def _machine_path(self):
        return self.memory_path[:-len(".json")] + ".automatiche.json"

    def _shared_memory(self, orig, dest):
        """The shared translation memory of the short texts, updated with the fixes made by hand in this game: a short
        text whose translation in the translation memory differs from the machine translation was fixed with
        'Fix translations', and the fix also holds for the other games (except an entry fixed by hand in another
        game: the first fix stays). The machine translation stays in .machine.json: so it is always known
        which entries were fixed by hand (and they are not touched)."""
        shared = self._read_json(shared_path(orig, dest), {}, "shared memory file")
        self._machine = None  # read again: the translator started by the game writes it too
        self._read_machine()
        try:
            origin = shared_file.read_json(shared_origin_path(orig, dest))
        except FileNotFoundError:
            origin = {}
        except (OSError, ValueError):  # damaged service file: start over (on save it goes to .illeggibile)
            origin = {}
        self._shared_origin = {k: v for k, v in origin.items() if isinstance(v, dict)} \
            if isinstance(origin, dict) else {}
        self._shared_skipped = set()
        # files also written by the other open programs (e.g. the translator started by a game): see shared_file
        self._shared_file = shared_file.MergingFile(shared_path(orig, dest), shared)
        self._source_file = shared_file.MergingFile(shared_origin_path(orig, dest), self._shared_origin)
        changed = 0
        for t, v in self.memory.items():
            if not (isinstance(v, str) and v and _is_short(t)):
                continue
            auto = self._machine.get(t)
            if auto is None or v == auto:  # machine translation, or from before they were recorded: not a fix
                continue
            if v.strip() == t.strip():  # left as it is (e.g. a name): holds only for this game
                continue
            already = self._shared_origin.get(t) or {}
            if shared.get(t) == v or (already.get("mano") and already.get("gioco") != self.profile.get("nome")):
                continue
            shared[t] = v
            self._shared_origin[t] = {"mano": True, "gioco": self.profile.get("nome"), "versione": __version__}
            changed += 1
        if changed:
            self.log("  Shared memory: %d corrections made by hand in this game now apply to every game" % changed)
            self._shared_file.save(shared)
            self._save_origin()
        return shared

    def _save_origin(self):
        try:
            self._source_file.save(self._shared_origin)
        except OSError as e:
            self.log("  ! Shared memory origin not saved (%s)" % e)

    def _provenance(self):
        """Where a translation made now comes from: translator, model, PatchSmith version, game."""
        b = getattr(self, "_current_backend", None)
        b = getattr(b, "_backend", b)  # the real translator inside the wrapper of the live translator
        return {"backend": getattr(b, "name", None), "modello": getattr(b, "model", None), "versione": __version__,
                "gioco": self.profile.get("nome")}

    def _update_machine(self, missing):
        """The machine translations just written (redone ones too) go into .automatiche.json."""
        machine = self._read_machine()
        for t in missing:
            if isinstance(self.memory.get(t), str):
                machine[t] = self.memory[t]
        try:
            self._machine_file.save(machine)
        except OSError as e:
            self.log("  ! Automatic translations not recorded (%s)" % e)

    def _update_shared(self, shared, orig, dest, missing, before=None):
        """The short texts translated now go into the shared translation memory, with their origin
        (shared_origin_path). Only those: the old entries of the game's translation memory may come from versions
        without today's checks. Not those left equal to the original (names), nor those that do not pass the current
        checks (entry_problems: an entry with its origin holds for all games without further checks). An entry of the
        shared memory is replaced only if it had been skipped because it did not pass the checks, or if it was this
        game's translation just redone ('prima')."""
        before = before or {}
        origin = getattr(self, "_shared_origin", None)
        if origin is None:
            origin = self._shared_origin = {}
        skipped_texts = getattr(self, "_shared_skipped", set())
        provenance = self._provenance()
        pattern = protected_pattern(self._protected())
        new_items = 0
        for t in missing:
            v = self.memory.get(t)
            if not (isinstance(v, str) and v.strip() and _is_short(t) and v.strip() != t.strip()) or v == before.get(t):
                continue
            if t in shared and t not in skipped_texts and shared[t] != before.get(t):
                continue
            if entry_problems(t, v, dest, pattern):
                continue
            shared[t] = v
            origin[t] = dict(provenance)
            new_items += 1
        if not new_items:
            return
        try:
            self._shared_file.save(shared)
        except OSError as e:
            self.log("  ! Shared memory not updated (%s)" % e)
            return
        if hasattr(self, "_source_file"):
            self._save_origin()

    def _update_problems(self, missing, redo, still=None):
        """problemi.json after a translation: the translated texts leave the list (those that could not be written
        to the game stay until apply() manages to write them); those left without a translation enter it,
        with the reason and the passes made (next time the local translator uses new seeds). A saved translation
        to redo that did not succeed stays the previous one, noted; a redone one that is still not good
        ('ancora', see _still_to_redo) stays noted with the passes made."""
        if not self.recovery:
            return
        still = still or {}
        failed = set(self.last_failed)
        steps = getattr(self, "_steps", 1)
        has_changed = False
        for t in missing:
            entry = self._problems.get(t)
            # passes already made: those noted, and at least one for a saved translation to redo (see _rounds)
            handled = max((entry or {}).get("tentativi", 0), getattr(self, "_min_round", {}).get(t, 0)) + steps
            if t in self._succeeded:
                if entry and entry.get("dove") == "gioco":  # the new translation is judged by apply()
                    self._mark_problem(t, tentativi=handled)
                    has_changed = True
                elif t in still:
                    self._mark_problem(t, motivo="%s; redone, but still not right (%s)" % (
                        redo.get(t) or "suspicious saved translation", ", ".join(still[t])), dove="memoria",
                        tentativi=handled, valore=self.memory.get(t))
                    has_changed = True
                elif entry:
                    del self._problems[t]
                    has_changed = True
                continue
            if t not in failed:  # not attempted (translator stopped or paused): stays as it was
                continue
            if t in redo and entry and entry.get("dove") == "gioco":
                self._mark_problem(t, tentativi=handled)
            elif t in redo:
                self._mark_problem(t, motivo="%s; redoing it failed, the previous one stays" % redo[t],
                                     dove="memoria", tentativi=handled, valore=self.memory.get(t))
            else:
                self._mark_problem(t, motivo=self._failure_reasons.get(t) or "no valid translation",
                                     dove="traduzione", tentativi=handled)
            has_changed = True
        if has_changed:
            self._save_problems()

    # --- labels too long for their space ------------------------------------------------------------------------

    def _long_labels(self, contexts=None, backend=None):
        """{text: space} of the short labels of this translation (short_label) whose translation is longer than
        their space (label_space): only machine translations (now too), not the fixes made by hand,
        and not those already noted with this same translation (problems.json, 'lunghezza'): asked shorter once,
        or noted with an online translator and now again with an online one. Those noted with an online translator
        and now with the local one (backend.uses_context) are asked shorter, once."""
        if not self.recovery or getattr(self, "test", False):
            return {}
        local = getattr(backend, "uses_context", False)
        output = {}
        succeeded = getattr(self, "_succeeded", set())
        for t in getattr(self, "_current_texts", ()):
            v = self.memory.get(t)
            if not isinstance(v, str) or not v.strip() or not short_label(t) or _unchanged(t, v):
                continue
            if t not in succeeded and not self.is_machine(t):
                continue  # fixed by hand, or from a version that did not record the machine translations
            problem = self._problems.get(t) or {}
            if problem.get("dove") == "lunghezza" and problem.get("valore") == v and \
                    (problem.get("chiesta", True) or not local):
                continue  # already asked shorter once (or already noted, and the translator cannot shorten it)
            if problem.get("dove") in ("gioco", "memoria"):
                continue  # the other checks redo it
            context = (contexts or {}).get(t) or {}
            space = label_space(t, (context.get("riferimenti") or {}).values(), context.get("spazio"))
            if _weight(v) > space + 0.01:
                output[t] = space
        return output

    def _shorten(self, backend, orig, dest, contexts=None):
        """The short labels too long for their space (e.g. 'Credits' -> 'Riconoscimenti'): with the local translator
        they are asked again once, with the maximum number of characters (context 'larghezza'), and the shortest
        translation that passes the checks stays. Those still too long, and with the other translators (which do not
        know about a maximum space) all of them, go into problems.json ('lunghezza', with the original text as key):
        they are shown in "Fix translations", list "Too long"."""
        try:
            self._ask_shorter(backend, orig, dest, contexts)
        except Exception as e:  # noqa: BLE001 - it is an extra: the translation just made stays valid and is written
            self.log("  ! shorter labels failed (%s: %s)" % (type(e).__name__, e))

    def _ask_shorter(self, backend, orig, dest, contexts=None):
        if self.translation_outcome is not None:  # translator blocked: next time
            return
        long_labels = self._long_labels(contexts, backend)
        if not long_labels:
            return
        if not getattr(backend, "uses_context", False):
            for t, space in long_labels.items():
                self._too_long[t] = (space, self.memory[t], False)
            self.log("  %d short labels are longer than the space they usually have (e.g. %r -> %r): you can see them "
                     "in 'Fix translations' ('Too long' list); the local translator can make them shorter" % (
                         len(long_labels), next(iter(long_labels))[:30], self.memory[next(iter(long_labels))][:40]))
            return
        if hasattr(backend, "pause") and backend.pause():
            return
        self.log("  %d short labels are longer than the space they usually have: asking for shorter ones"
                 % len(long_labels))
        pattern = protected_pattern(self._protected())
        shortened = []
        for group in _groups(list(long_labels), getattr(backend, "max_texts", TEXTS_PER_GROUP)):
            interruption.check()
            protected = {t: protect(t, self._protected()) for t in group}
            requests = []
            for t in group:
                c = (contexts or {}).get(t) or {}
                requests.append({"chiave": c.get("chiave"), "riferimenti": c.get("riferimenti") or {},
                                  "non_usare": c.get("non_usare"), "larghezza": max(1, int(long_labels[t]))})
            try:
                responses = backend.translate_lines([protected[t][0] for t in group], orig, dest, contexts=requests)
            except BackendError as e:  # the others are asked next time
                self.log("  ! shorter labels not requested: %s" % e)
                break
            except Exception as e:  # noqa: BLE001 - it is an extra: the previous translations stay
                self.log("  ! shorter labels failed (%s: %s)" % (type(e).__name__, e))
                break
            for t, response in zip(group, responses):
                old_folder = self.memory[t]
                new_value = restore_placeholders(response, protected[t][1]) if response else None
                new_value = capitalize_like(t, new_value) if new_value else None
                references = list((((contexts or {}).get(t) or {}).get("riferimenti") or {}).values())
                if new_value and new_value.strip() and not _unchanged(t, new_value) \
                        and _weight(new_value) < _weight(old_folder) \
                        and not entry_problems(t, new_value, dest, pattern, references):
                    self.memory[t] = new_value
                    shortened.append(t)
                # still too long: it stays noted (the shorter one too); if it fits, it leaves the list
                still = _weight(self.memory[t]) > long_labels[t] + 0.01
                self._too_long[t] = (long_labels[t], self.memory[t], True) if still else None
            self._save_memory()
        if shortened:
            self._update_machine(shortened)
            self.shortened += len(shortened)
        still_long = sum(1 for v in self._too_long.values() if v)
        self.log("  Labels: %d shortened%s" % (len(shortened), ", %d still longer than usual (you can see them in "
                                                                   "'Fix translations', 'Too long' list)" % still_long
                                                    if still_long else ""))

    def _record_long(self):
        """The labels still too long go into problems.json ('lunghezza'), with the space and the translation; those
        that fit now leave the list."""
        long_labels = getattr(self, "_too_long", None)
        if not long_labels or not self.recovery:
            return
        for t, data in long_labels.items():
            if data is None:
                if (self._problems.get(t) or {}).get("dove") == "lunghezza":
                    del self._problems[t]
                continue
            space, value_, requested = data
            if (self._problems.get(t) or {}).get("dove") not in (None, "lunghezza"):
                continue  # a more important problem (not translated, unfit for the game)
            # chiesta: already asked shorter to the local translator (if not, it is asked with that one:
            # _long_labels)
            self._mark_problem(t, motivo="%d characters, usually about %d fit" % (
                round(_weight(value_)), int(space)), dove="lunghezza", valore=value_, spazio=int(space),
                chiesta=requested)
            self.long_noted += 1
        self._save_problems()
        self._too_long = {}

    def _translate_missing(self, missing, backend, orig, dest, contexts=None):
        self._cores_ok, self._steps, self._seen_rejects, self._failure_reasons = {}, 1, {}, {}
        if not missing:
            # (only the saved translations: the translator is started if needed)
            if self._long_labels(contexts, backend):
                try:
                    self._shorten(backend, orig, dest, contexts)
                finally:
                    if hasattr(backend, "close"):
                        backend.close()
            return 0
        pause = backend.pause() if hasattr(backend, "pause") else None
        if pause:  # service paused because of a previous block: no requests, so as not to extend it
            self.log("  ! Translation not started: %s" % pause)
            self.translation_outcome, self.translation_reason = "TRADUTTORE_BLOCCATO", pause
            return len(missing)
        groups = _groups(missing, min(TEXTS_PER_GROUP, getattr(backend, "max_texts", TEXTS_PER_GROUP)),
                         getattr(backend, "max_chars", None))
        if hasattr(backend, "estimate"):
            from .limiter import NAMES, describe_duration
            requests = backend.requests(missing) if hasattr(backend, "requests") else \
                [sum(len(t) + 1 for t in g) for g in groups]
            if getattr(backend, "limited", True):
                self.log("  Expected requests: about %d, estimated time: %s (PatchSmith goes slowly on purpose with "
                         "%s, to reduce the risk of being blocked)" % (len(requests),
                                                             describe_duration(backend.estimate(requests)),
                                                             NAMES.get(backend.name, backend.name)))
            else:
                self.log("  Local translator: %d texts, estimated time approximately %s. Do not start the game while "
                         "translating (the graphics card is busy)." % (len(requests),
                                                             describe_duration(backend.estimate(requests))))
            if hasattr(backend, "quota") and sum(requests) > backend.quota():
                self.log("  ! About %d characters are needed, more than the %d per day of %s: part of them gets "
                         "translated today, the rest on the following days (it resumes from where it stopped)."
                         % (sum(requests), backend.quota(), NAMES.get(backend.name, backend.name)))
        # the rejected proposals of the local translator say why a text did not pass (for problems.json); in the
        # live translator it prepares them itself (xunity_service)
        own_rejects = self.recovery and getattr(backend, "rejects", False) is None
        if own_rejects:
            backend.rejects = {}
        try:
            failed, not_attempted = self._translate_groups(backend, groups, missing, orig, dest, contexts)
            if failed and self.recovery and self.translation_outcome is None:
                failed = self._recover(backend, failed, orig, dest, contexts)
            if failed and self.recovery:
                self._seen_rejects.update(getattr(backend, "rejects", None) or {})
                self._failure_reasons = self._reasons(failed)
            self._shorten(backend, orig, dest, contexts)  # before closing the local translator
        finally:
            if hasattr(backend, "close"):  # local translator: frees the graphics card and closes the server
                backend.close()
            if own_rejects:
                backend.rejects = None
        self.last_failed = failed
        total = len(failed) + not_attempted
        self.log(DETAIL + "Memory: %d entries (%s)%s" % (
            len(self.memory), self.memory_path, ", untranslated: %d" % total if total else ""))
        return total

    def _translate_groups(self, backend, groups, missing, orig, dest, contexts=None):
        """([texts attempted and left without a valid translation], how many were not attempted because the translator
        stopped or the group was skipped because of an error)."""
        failed, not_attempted = [], 0
        handled = 0
        for group in groups:
            start = handled
            handled += len(group)
            self.log(DETAIL + "  texts %d-%d of %d" % (start + 1, start + len(group), len(missing)))
            # in the window the progress every 10% (not every group of texts)
            if start and len(groups) > 2 and start * 10 // len(missing) > (start - len(group)) * 10 // len(missing):
                self.log("  Translated %d of %d (%d%%)..." % (start, len(missing), start * 100 // len(missing)))
            try:
                interruption.check()
                failed += self._translate_group(backend, group, orig, dest, contexts)
            except interruption.Interrupted:
                self.log("  Stopped: the translations done so far are saved (%d in memory) and next time it resumes "
                         "from there." % len(self.memory))
                raise
            except BackendError as e:  # service unreachable or blocked: resumes at the next run
                self.log("  ! translation interrupted: %s" % e)
                self.translation_outcome, self.translation_reason = "TRADUTTORE_BLOCCATO", str(e)
                self.last_error = e
                not_attempted += len(missing) - start
                break
            except Exception as e:  # problem with this group: skip it and continue with the next ones
                self.log("  ! group skipped (%s: %s)" % (type(e).__name__, e))
                self.last_error = e
                not_attempted += len(group)
            finally:
                self._save_memory()
        return failed, not_attempted

    def _rounds(self, split_parts):
        """{line: passes already made} from the attempts noted in problems.json for the texts the line comes from (at
        least one for a saved translation to redo: see translate_texts)."""
        rounds = {}
        if not self.recovery:
            return rounds
        minimum = getattr(self, "_min_round", {})
        for t, parts in split_parts.items():
            g = max((self._problems.get(t) or {}).get("tentativi", 0), minimum.get(t, 0))
            if g:
                for _, n, _ in parts:
                    if n:
                        rounds[n] = max(rounds.get(n, 0), g)
        return rounds

    def _translate_group(self, backend, group, orig, dest, contexts=None, step=0):
        """Translates a group of texts line by line, with the game's codes and names protected, and saves in the
        translation memory those with all lines translated; returns the others. passo: the rounds already made in this
        translation (0 = the first; after that only the missing lines are asked, with new seeds: see _recover)."""
        split_parts = {t: split_lines(t) for t in group}
        cores_ok = getattr(self, "_cores_ok", {})
        cores = list(dict.fromkeys(n for parts in split_parts.values() for _, n, _ in parts
                                    if _to_translate(n) and not (step and n in cores_ok)))
        protected = {n: protect(n, self._protected()) for n in cores}
        # a line made only of the game's codes and names ('Cup Dog Games') stays as it is, without asking for it
        unchanged = {n: n for n in cores if protected[n][1] and not _to_translate(_TOKEN.sub("", protected[n][0]))}
        to_ask = [n for n in cores if n not in unchanged]
        extra = {}
        if contexts and getattr(backend, "uses_context", False):
            extra["contexts"] = _core_contexts(split_parts, to_ask, contexts)
        rounds = {}
        if getattr(backend, "varying_seeds", False) and getattr(self, "recovery", True):
            already = self._rounds(split_parts)
            rounds = {n: already.get(n, 0) + step for n in to_ask}
            if any(rounds.values()):
                extra["rounds"] = [rounds[n] for n in to_ask]
        try:
            translated_items = backend.translate_lines([protected[n][0] for n in to_ask],
                                                       orig, dest, **extra) if to_ask else []
        except BaseException as e:  # block, quota used up or "Stop" halfway: what arrived is saved
            partials = getattr(e, "partials", None)
            if partials and len(partials) == len(to_ask):
                mapping, _ = self._mapping(to_ask, protected, partials)
                mapping.update(unchanged)
                self._in_memory(split_parts, mapping)
            raise
        mapping, to_fall_back = self._mapping(to_ask, protected, translated_items)
        mapping.update(unchanged)
        if to_fall_back:  # the pieces between the placeholders of the whole group, in a single request
            try:
                fallback = self._translate_in_pieces(backend, to_fall_back, orig, dest,
                                                rounds={n: rounds.get(n, 0) for n in to_fall_back} if rounds else None)
            except BaseException:  # Stop or block during the fallback: the group's already good translations stay
                self._in_memory(split_parts, mapping)
                raise
            for n, restored_text in fallback.items():
                if restored_text:
                    mapping[n] = capitalize_like(n, restored_text)
        self.last_group = {"divisi": split_parts, "protetti": protected, "mappa": mapping, "ripiegati": to_fall_back}
        return self._in_memory(split_parts, mapping)

    @staticmethod
    def _mapping(cores, protected, translated_items):
        """({core: valid translation}, [cores with damaged placeholders to re-translate in pieces])."""
        mapping, to_fall_back = {}, []
        for n, t in zip(cores, translated_items):
            restored_text = restore_placeholders(t, protected[n][1]) if t else None
            if restored_text is None and protected[n][1] and t:
                to_fall_back.append(n)
            elif restored_text:
                mapping[n] = capitalize_like(n, restored_text)
        return mapping, to_fall_back

    def _in_memory(self, split_parts, mapping):
        """Saves in the translation memory the texts with all lines translated (also in a group or round before this
        one, in the same translation). Returns the texts left without a translation."""
        if not hasattr(self, "_cores_ok"):
            self._cores_ok = {}
        self._cores_ok.update(mapping)
        succeeded = getattr(self, "_succeeded", None)
        failed = []
        for text, parts in split_parts.items():
            if all(n in self._cores_ok or not _to_translate(n) for _, n, _ in parts):
                self.memory[text] = reassemble_lines(parts, self._cores_ok)
                if succeeded is not None:
                    succeeded.add(text)
            else:
                failed.append(text)
        return failed

    def _recover(self, backend, failed, orig, dest, contexts=None):
        """New automatic rounds, right away, for the texts rejected by the checks (before, the same question was
        repeated with the same parameters, and the answer was the same at every run):
        1. the missing lines split into sentences and into the pieces between the codes, each translated on its own, in
           a single request; the local translator uses new seeds, derived from the text and the round;
        2. the model stays the chosen one: the texts still rejected go into problems.json ('Fix translations').
        Returns the texts still without a translation."""
        self.log("  %d texts rejected by the checks: retrying them right away in a different way (separate phrases, "
                 "different parameters)" % len(failed))
        split_parts = {t: split_lines(t) for t in failed}
        rounds = self._rounds(split_parts)
        lines = list(dict.fromkeys(n for t in failed for _, n, _ in split_parts[t]
                                   if _to_translate(n) and n not in self._cores_ok))
        self._steps = 2
        stopped = False
        try:
            fallback = self._translate_in_pieces(backend, lines, orig, dest, sentences=True,
                                            rounds={n: rounds.get(n, 0) + 1 for n in lines})
        except BackendError as e:  # translator stopped or blocked: resumes at the next run
            self.log("  ! new pass interrupted: %s" % e)
            self.translation_outcome, self.translation_reason, self.last_error = "TRADUTTORE_BLOCCATO", str(e), e
            fallback, stopped = {}, True
        except Exception as e:  # noqa: BLE001 - the new rounds are an extra: the translations made are written anyway
            self.log("  ! new pass failed (%s: %s)" % (type(e).__name__, e))
            self.last_error = e
            fallback = {}
        for n, r in fallback.items():
            if r:
                self._cores_ok[n] = capitalize_like(n, r)
        remaining = self._in_memory(split_parts, {})
        self._save_memory()
        # no automatic model switch: the one chosen by the user is used (the Quality 12B instead of the Fast one
        # would take minutes and the graphics card without being asked); the remaining ones can be re-translated with
        # the 12B from 'Fix translations', if wanted
        recovered = len(failed) - len(remaining)
        self.log("  More passes: %d texts translated, %d still untranslated" % (recovered, len(remaining)))
        return remaining

    def _reasons(self, failed):
        """{text: why it was left without a translation} for problems.json, from the rejected proposals of the local
        translator (the checks they did not pass)."""
        output = {}
        for t in failed:
            reasons = []
            for _, n, _ in split_lines(t):
                if not n or not _to_translate(n) or n in self._cores_ok:
                    continue
                proposal = self._seen_rejects.get(protect(n, self._protected())[0])
                if proposal:
                    reasons += [m for m in proposal[1] if m not in reasons]
            output[t] = ("rejected by the checks (%s)" % ", ".join(reasons)) if reasons else \
                "the translator didn't provide a valid translation"
        return output

    def _translate_in_pieces(self, backend, cores, orig, dest, sentences=False, rounds=None):
        """Fallback: translates separately the pieces of text between the placeholders (frasi=True: also each sentence
        on its own), of all the cores together with a single call to the translator. giri: {core: round} for the seeds
        of the local translator. Returns {core: translation or None}."""
        prepared, unique_pieces, piece_round = {}, [], {}
        code = re.compile(r"\[\[\d+\]\]")
        for core in cores:
            text, placeholders = protect(core, self._protected())
            pieces = re.split(r"(\[\[\d+\]\])", text)
            if sentences:  # also between one sentence and the next (the spaces between sentences stay as they are)
                pieces = [x for p in pieces for x in
                         ([p] if code.fullmatch(p) else re.split(r"((?<=[.!?…。！？])\s+)", p))]
            prepared[core] = (pieces, placeholders)
            for p in pieces:
                if p.strip() and not code.fullmatch(p) and _to_translate(p):
                    unique_pieces.append(p.strip())
                    piece_round[p.strip()] = max(piece_round.get(p.strip(), 0), (rounds or {}).get(core, 0))
        unique_pieces = list(dict.fromkeys(unique_pieces))
        extra = {}
        if rounds and getattr(backend, "varying_seeds", False) and any(piece_round.values()):
            extra["rounds"] = [piece_round[p] for p in unique_pieces]
        translated_items = dict(zip(unique_pieces, backend.translate_lines(unique_pieces, orig, dest, **extra))) \
            if unique_pieces else {}
        output = {}
        for core, (pieces, placeholders) in prepared.items():
            to_translate = [p for p in pieces if p.strip() and not code.fullmatch(p) and _to_translate(p)]
            if any(not translated_items.get(p.strip()) for p in to_translate):
                output[core] = None
                continue
            reassembled = "".join(
                (p[:len(p) - len(p.lstrip())] + translated_items[p.strip()] + p[len(p.rstrip()):])
                if p in to_translate else p
                for p in pieces
            )
            output[core] = restore_placeholders(reassembled, placeholders)
        return output

    def apply(self):
        """Writes the translations in the translation memory to the game's files (does not use the network). The account
        stays in self.last_apply: texts translated (ok) and left untranslated (mancanti), the first originals left
        (esempi), translations rejected because the game would not show them well, files written, game open.
        The rejected translations go into problems.json with the reason (see _record_applied)."""
        self.last_apply = {"ok": 0, "mancanti": 0, "esempi": [], "scartate": 0, "scritti": 0, "aperto": False}
        self._refused, self._applied = {}, set()
        if self.profile.get("motore") == "gamemaker" and game_open(self.profile["cartella_gioco"]):
            self.log("  ! The game is open: close it and try again (it rereads some texts only when restarted).")
            self.last_apply["aperto"] = True
            return False
        total_ok = total_missing = 0
        self._left_as_is = 0  # labels left like the original on purpose (as the game does in its translations)
        self._examples, self._rejected, self._writes = {}, 0, 0
        for entry in self.profile["file"]:
            try:
                if entry.get("formato", "json") == "web-ui":
                    ok, missing = self._apply_webui(entry)
                elif entry.get("formato") == "righe":
                    ok, missing = self._apply_lines(entry)
                elif entry.get("formato") == "csv":
                    ok, missing = self._apply_csv(entry)
                elif entry.get("formato") == "unity-tabelle":
                    ok, missing = self._apply_unity_tables(entry)
                else:
                    ok, missing = self._apply_json(entry)
            except (OSError, ValueError, UnicodeDecodeError) as e:
                self.log("  ! %s skipped: %s" % (entry["percorso"], e))
                ok, missing = 0, 1
            total_ok += ok
            total_missing += missing
        self.log("Applied: %d texts translated%s, %d still untranslated." % (
            total_ok, ", %d left untranslated, as in the game's translations" % self._left_as_is
            if self._left_as_is else "", total_missing))
        self.last_apply.update(ok=total_ok, mancanti=total_missing, esempi=list(self._examples),
                                   scartate=self._rejected, scritti=self._writes)
        self._record_applied()
        return total_missing == 0

    def _refuse(self, text, reason, entry, do_not_use=""):
        """A saved translation that cannot be written to the game (the original stays): noted for problemi.json."""
        if isinstance(text, str) and text not in self._refused:
            self._refused[text] = {"motivo": reason, "file": entry.get("percorso"),
                                     "non_usare": "".join(dict.fromkeys(do_not_use or ""))}

    def _record_applied(self):
        """After apply(): the rejected translations go into problems.json (dove 'gioco', with the reason and the
        characters not to use); the machine translations will be redone at the next translation with the right
        instruction (see _to_redo), those fixed by hand stay (they are shown in 'Show remaining'). The texts written
        to the game leave the list, except the suspect saved translations ('memoria'): they can be written, but stay
        noted (with the passes made, which stop them after MAX_ATTEMPTS) as long as that translation is in the
        translation memory."""
        if not self.recovery:
            return
        has_changed = False
        for t in self._applied:
            entry = self._problems.get(t)
            if entry is None or t in self._refused:
                continue
            if entry.get("dove") in ("memoria", "lunghezza") and entry.get("valore") == self.memory.get(t):
                continue  # (suspect and too long: they are written, but stay noted until they change)
            del self._problems[t]
            has_changed = True
        machine = 0
        for t, info in self._refused.items():
            before = self._problems.get(t) or {}
            is_machine = self.is_machine(t)
            machine += is_machine
            self._mark_problem(t, motivo=info["motivo"], dove="gioco", file=info.get("file"),
                                 valore=self.memory.get(t), automatica=is_machine,
                                 non_usare="".join(dict.fromkeys((before.get("non_usare") or "") + info["non_usare"]))
                                 or None,
                                 tentativi=max(before.get("tentativi", 0), 1))  # redone: new seeds
            has_changed = True
        if machine:
            self.log("  %d automatic translations can't be written to the game: I'll redo them with the right "
                     "instruction at the next translation" % machine)
        if has_changed:
            self._save_problems()

    def _remaining(self, texts):
        """Notes the first originals left without a translation (to show them to the user)."""
        for t in texts:
            if len(self._examples) >= 20:
                return
            if isinstance(t, str) and t.strip():
                self._examples[t] = True

    def _apply_json(self, entry):
        f, units = self._load(entry)
        if units is None:
            self.log("  %s: This file no longer exists in the game, so I'm skipping it" % entry["percorso"])
            return 0, 0
        special = entry.get("a_capo") or entry.get("punto_elenco")
        originals = 0  # labels left like the original on purpose (they are not missing translations)
        left_alone = set()  # their positions
        if special or self._game_fonts():
            raw_texts = dict(self._raw_json(f.data, entry))
            replacements, adapted, rejected, too_wide = {}, 0, [], []
            limits = entry.get("limiti") or {}
            undrawable = []
            for p, t in units:
                translated = self.memory.get(t)
                if not translated:
                    continue
                # lowercase game (sprite font): holds for the texts written in lowercase; those with normal
                # capitals (system messages) the game draws with another font
                lowercase = entry.get("maiuscole") == "minuscolo" and not adapter_gamemaker._mixed_words(
                    adapter_gamemaker._without_placeholders(raw_texts[p]))
                if lowercase:  # the game writes in lowercase: capitals would be icons or missing letters
                    translated = adapter_gamemaker.to_lowercase(translated, raw_texts[p], entry.get("codici", []),
                                                              self.profile["lingua_destinazione"])
                limit = limits.get(adapter_gamemaker.key_of(p)) if isinstance(p, tuple) and p else None
                if special:  # again with the game's line breaks and symbols, and in the space the game gives the text
                    reassembled = adapter_gamemaker.reassemble_line_breaks(
                        raw_texts[p], translated, entry.get("a_capo"), entry.get("punto_elenco"),
                        limit[0] if limit else None, limit[1] if limit else None)
                    if reassembled is None:
                        rejected.append(p)
                        # the game's signs (line break, symbol) written inside the translation: not next time
                        markers = "".join(s for s in (entry.get("a_capo") or "", entry.get("punto_elenco") or "")
                                        if s and not s.isalnum() and s in translated)
                        self._refuse(t, "different format from the original (different paragraphs or the game's line "
                                        "break character within the sentence)", entry, markers)
                        continue
                    translated = reassembled
                if limit and entry.get("a_capo"):
                    line_len = adapter_gamemaker._longest_line(translated, entry["a_capo"])
                    short = len(raw_texts[p].split()) <= 2 and entry["a_capo"] not in raw_texts[p]
                    if line_len > 1.25 * limit[0] and short and len(limit) > 2 and limit[2] >= 2:
                        originals += 1  # like the official translations that leave it as it is (e.g. 'vsync:')
                        left_alone.add(p)
                        continue
                    if line_len > max(limit[0] + 2, 1.1 * limit[0]):
                        too_wide.append((" ".join(t.split())[:30], line_len, limit[0]))
                is_ready = self._adapt(translated, raw_texts[p], repair=False, lowercase=lowercase)
                if is_ready is None:
                    undrawable.append(translated)
                    absent = self._missing_chars(translated, raw_texts[p], lowercase)
                    self._refuse(t, "characters that the game's fonts can't draw: %s" % " ".join(absent),
                                   entry, absent)
                    continue
                adapted += is_ready != translated
                replacements[p] = is_ready
            if adapted:
                self.log("  %s: %d texts adapted to the game's font characters" % (entry["percorso"], adapted))
            if originals:
                self.log("  %s: %d short labels left untranslated, as in the game's official translations (the "
                         "translation didn't fit)" % (entry["percorso"], originals))
            if undrawable:
                self.log("  ! %s: %d translations have characters the game's fonts can't draw and stay untranslated, "
                         "e.g. %r (I'll redo the ones written by PatchSmith without those characters; the ones fixed "
                         "by hand need correcting with 'Fix translations')"
                         % (entry["percorso"], len(undrawable), undrawable[0][:60]))
            if too_wide:
                too_wide.sort(key=lambda x: x[2] - x[1])
                self.log("  ! %s: %d texts might not fit their space (longer than every version in the game), e.g. %s: "
                         "if needed, shorten them with 'Fix translations' ('Too long' list)" % (
                             entry["percorso"], len(too_wide),
                             ", ".join("'%s' %d/%d characters" % x for x in too_wide[:4])))
            if rejected:
                self.log("  ! %s: %d translations left untranslated because they don't have the shape of the original "
                         "text (different paragraphs or a '%s' typed by hand), e.g. %s (I'll redo the ones written by "
                         "PatchSmith; the ones fixed by hand need correcting with 'Fix translations')"
                         % (entry["percorso"], len(rejected), entry.get("a_capo") or "*",
                            ", ".join(str(p[-1]) for p in rejected[:3])))
            self._rejected += len(rejected) + len(undrawable)
        else:
            replacements = {p: self.memory[t] for p, t in units if t in self.memory}
        missing = len(units) - len(replacements) - originals
        new_items = adapter_webjson.apply(f.data, replacements)
        problems = adapter_webjson.verify_structure(f.data, new_items, set(replacements))
        if problems:
            self.log("  ! %s NOT written, structure check failed: %s" % (entry["percorso"], problems[:3]))
            self._remaining(t for _, t in units)
            return 0, len(units)
        self._remaining(t for p, t in units if p not in replacements and p not in left_alone)
        data_bytes = f.serialize(new_items)  # raises ValueError if the JSON cannot be represented
        adapter_webjson.reread_strict(data_bytes, f.lenient)
        path = self._game_path(entry["percorso"])
        if entry.get("lingua_nuova"):  # one more language in the game: the original file stays as it is
            new = self._prepare_new_language(path, entry["lingua_nuova"], entry, data_bytes)
            adapter_webjson.write_atomic(new, data_bytes)
            self._writes += 1
            self._applied.update(t for p, t in units if p in replacements)
            self.log("  %s: %d texts translated, %d still untranslated (language added to the game; %s stays original)"
                     % (entry["lingua_nuova"], len(replacements), missing, entry["percorso"]))
            self._left_as_is += originals
            return len(replacements), missing
        self._prepare_write(path, data_bytes)
        adapter_webjson.write_atomic(path, data_bytes)
        self._writes += 1
        self._applied.update(t for p, t in units if p in replacements)
        self.log("  %s: %d texts translated, %d still untranslated" % (entry["percorso"], len(replacements), missing))
        self._left_as_is += originals
        return len(replacements), missing

    def _prepare_new_language(self, source, relative, entry, data_bytes):
        """Before writing the added language (e.g. local/italian.json):
        - the file of the original language goes back to the original if a previous version had translated it in place
          (even if it had then been fixed by hand: the fixed version is set aside, .patchsmith-modificato);
        - a file with that name that does not belong to PatchSmith (e.g. a translation made by others) is not touched;
        - an added language fixed by hand is set aside before rewriting it;
        - the added language is recorded (with the fingerprint of what is written) among the files created in the info
          of the original file's backup, so 'Restore originals' removes it."""
        new = self._game_path(relative)
        if os.path.normcase(os.path.dirname(new)) != os.path.normcase(os.path.dirname(source)):
            raise ValueError("the new language file must be placed next to the original file")
        name = os.path.basename(new)
        backup = source + BACKUP_SUFFIX
        info = read_info(source) if os.path.exists(backup) else None
        if os.path.exists(new) and (info is None or name not in info.get("creati", [])):
            raise ValueError("%s already exists and wasn't created by PatchSmith (someone else's translation?): I "
                             "won't touch it. Move or rename it if you want PatchSmith's translation" % relative)
        if info is None:
            info = create_backup(source, {"solo_creati": True})
        else:
            current = _hash_file(source)
            if current != info["hash_originale"] and (current in info.get("hash_scritti", [])
                                                      or self._looks_translated(source, entry)):
                if current not in info.get("hash_scritti", []):
                    shutil.copyfile(source, source + ".patchsmith-modificato")
                    self.log("  ! %s was translated and fixed by hand: the fixed version is kept in %s"
                             % (os.path.basename(source), os.path.basename(source) + ".patchsmith-modificato"))
                with open(backup, "rb") as f:
                    adapter_webjson.write_atomic(source, f.read())
                if _hash_file(source) != info["hash_originale"]:
                    raise RuntimeError("I can't restore the original of %s" % source)
                self.log("  %s: Restored to original (the translation is now a separate language)"
                         % os.path.basename(source))
            info["solo_creati"] = True
        expected = (info.get("hash_creati") or {}).get(name)
        if os.path.exists(new) and expected and _hash_file(new) != expected and not self._looks_translated(new, entry):
            # it is no longer ours (e.g. an official italian.json arrived with an update): it is not touched
            info["creati"] = [c for c in info.get("creati", []) if c != name]
            info["hash_creati"] = {k: v for k, v in (info.get("hash_creati") or {}).items() if k != name}
            save_info(source, info)
            raise ValueError("%s has been replaced by a file that isn't from PatchSmith (is this a game update?): I "
                             "won't touch it. If the game now has its own translation, choose it from the menu"
                             % relative)
        if os.path.exists(new) and expected and _hash_file(new) != expected:
            shutil.copyfile(new, new + ".patchsmith-modificato")
            self.log("  ! %s had been fixed by hand: rewriting it, the fixed version is kept in %s (to fix a "
                     "translation, use 'Fix translations')" % (name, name + ".patchsmith-modificato"))
        if name not in info.get("creati", []):
            info["creati"] = info.get("creati", []) + [name]
        info["hash_creati"] = dict(info.get("hash_creati") or {}, **{name: _hash_bytes(data_bytes)})
        info["patchsmith_versione"] = __version__
        save_info(source, info)  # before writing, and also to move to the new name of the info file
        return new

    def _game_fonts(self):
        """'gamemaker' or 'xna' if the translations must be adapted to the characters of the game's fonts (in GameMaker
        a character the font lacks becomes a small square, in XNA it can make the game close), otherwise None.
        Also holds for the game profiles of the generic search, with the engine detected in profilo["motore_gioco"]."""
        if self.profile.get("motore") == "gamemaker":
            return "gamemaker"
        return self.profile.get("motore_gioco") if self.profile.get("motore_gioco") in ("gamemaker", "xna") else None

    def _adapt(self, translated, original, repair=True, lowercase=False):
        """Translation with only the characters the game's fonts can draw (None: impossible). 'ripara':
        also the rules of the codes between slashes of line-based files (e.g. /name/ in Dokimon). 'minuscolo': text of
        a game that writes in lowercase with a sprite font (Forager): the characters of that font apply."""
        font = self._game_fonts()
        gamemaker = font == "gamemaker"
        if not hasattr(self, "_allowed"):
            self._allowed = None
            if gamemaker:
                self._allowed = adapter_gamemaker.font_chars(self.profile["cartella_gioco"], self.log,
                                                                 self.profile.get("lettere_ufficiali"),
                                                                 self.profile.get("lingua_file"))
                if self._allowed is None:
                    self.log("  ! I'm having trouble finding the game's fonts (data.win): for safety, I'm only using "
                             "ASCII characters")
                    self._allowed = set(ASCII)
            elif font == "xna":  # None if the game has no XNB fonts: no limit
                self._allowed = adapter_xna.font_chars(self.profile["cartella_gioco"], self.log)
        is_ready = translated
        allowed = self._allowed
        if lowercase and gamemaker:
            if not hasattr(self, "_allowed_sprite"):
                mapping = adapter_gamemaker.sprite_font_chars(self.profile["cartella_gioco"])
                self._allowed_sprite = set(mapping) | {" ", "*", "\n"} if mapping else None
            allowed = self._allowed_sprite or allowed
        if allowed is not None:
            is_ready = adapt_chars(translated, allowed | set(original), self.profile["lingua_destinazione"])
        if is_ready is not None and gamemaker and repair:
            is_ready = adapter_gamemaker.repair(original, is_ready)
        return is_ready

    def _missing_chars(self, translated, original, lowercase=False):
        """The characters of the translation the game's fonts cannot draw, not even adapted (see _adatta)."""
        return "".join(c for c in dict.fromkeys(translated)
                       if not c.isspace() and self._adapt(c, original, repair=False, lowercase=lowercase) is None)

    MAX_TABLE_REJECTS = 5  # translations removed one at a time until the bundle read back matches

    def _remove_crc(self, entry):
        """Unity Localization with the Addressables catalog (catalog.json) that checks the fingerprint (CRC) of the
        bundles: before rewriting a bundle, the CRC of this game profile's bundles (only those) is set to 0 in the
        catalog. The catalog has its backup and its info like the other files: 'Restore originals' puts it
        back; if a game update changes it, the new one becomes the original. If the bundle is not found
        in the catalog it stops (ValueError): the bundle is not written."""
        rel = entry["catalogo"]
        catalog = self._game_path(rel)
        source = self._original(catalog)
        if source is None:
            raise ValueError("I can't find the bundle catalog (%s)" % rel)
        names = [os.path.basename(v["percorso"]) for v in self.profile.get("file", [])
                if v.get("catalogo") == rel and v.get("percorso")]
        with open(source, "rb") as f:
            original = f.read()
        new = adapter_unity_loc.remove_crc(original, names)
        if new is None:
            raise ValueError("I can't remove the checksum (CRC) check of %s from the game's catalog: not modifying it"
                             % ", ".join(names))
        with open(catalog, "rb") as f:
            current = f.read()
        if current != new:
            self._prepare_write(catalog, new)
            adapter_webjson.write_atomic(catalog, new)
            self.log(DETAIL + "  %s: removed the CRC check of %s" % (os.path.basename(catalog),
                                                                            ", ".join(names)))

    def _tmp_chars(self, entry):
        """The characters the game's TextMeshPro fonts can draw (font_unity), or None if unknown (no
        limit). Read once per translation; the result stays in memory/<gioco>.unity/font.json until the game's files
        change."""
        if not hasattr(self, "_allowed_tmp"):
            self._allowed_tmp = None
            data = font_unity.bundle_data(self.profile["cartella_gioco"], entry["percorso"])
            if data and os.path.isdir(data):
                cache = data_folder("memory", "%s.unity" % self.profile["nome"], "font.json")
                try:
                    self._allowed_tmp, reason = font_unity.allowed_chars(data, self.log, cache=cache)
                except Exception as e:  # noqa: BLE001 - the fonts are an extra check: an odd file must not
                    # stop the writing of the translations (it goes on without limits, as before)
                    self._allowed_tmp, reason = None, "%s: %s" % (type(e).__name__, e)
                dest = self.profile["lingua_destinazione"]
                if self._allowed_tmp is None:
                    self.log("  Game fonts: I don't know which characters they have (%s), not limiting them" % reason)
                else:
                    absent_letters = [c for c in typical_letters(dest) if c not in self._allowed_tmp]
                    self.log(DETAIL + "  Game fonts: %s%s" % (reason, "; they don't have %s: adapting them (e.g. "
                                                                          "e')"
                                                                  % " ".join(absent_letters) if absent_letters else ""))
        return self._allowed_tmp

    def _apply_unity_tables(self, entry):
        """Rewrites the bundle of the text tables with the translations, first checking that, read back, it has the
        same texts in the same order. A translation without letters would disappear from the table read back: the
        original stays. If the check fails because of another translation, that one is removed (up to
        MAX_TABLE_REJECTS); the removed ones go into problems.json. The characters no TextMeshPro font of the game can
        draw are adapted (chars.adapt_chars: è -> e'); a translation that cannot be adapted stays untranslated
        and goes into problems.json (the next translation redoes it without those characters)."""
        f, units = self._load(entry)
        if units is None:
            self.log("  %s: This file no longer exists in the game, so I'm skipping it" % entry["percorso"])
            return 0, 0
        allowed = self._tmp_chars(entry)
        # characters the game said it cannot draw (Unity log, see checks.py)
        excluded = set(self.profile.get("caratteri_mancanti") or "")
        dest = self.profile["lingua_destinazione"]
        replacements, adapted, font_rejected = {}, [], 0
        for pos, t in units:
            v = self.memory.get(t)
            if not isinstance(v, str):
                continue
            if not any(c.isalpha() for c in v):
                self._refuse(t, "translation without letters: it can't be written in the text table", entry)
                continue
            is_ready, absent = _adapt_tmp(v, t, allowed, excluded, dest)
            if is_ready is None:
                self._refuse(t, "characters that the game's fonts can't draw: %s" % " ".join(absent),
                               entry, absent)
                font_rejected += 1
                continue
            if is_ready != v:
                adapted.append((v, is_ready))
            replacements[pos] = is_ready
        if adapted:
            self.log("  %s: %d texts adapted to the characters of the game's fonts (e.g. %r -> %r)" % (
                os.path.basename(entry["percorso"]), len(adapted), adapted[0][0][:40], adapted[0][1][:40]))
            self._font_adapted = getattr(self, "_font_adapted", 0) + len(adapted)
        if font_rejected:
            self.log("  ! %s: %d translations have characters the game's fonts can't draw and stay untranslated (I'll "
                     "redo the ones written by PatchSmith without those characters; the ones fixed by hand need "
                     "correcting with 'Fix translations')" % (os.path.basename(entry["percorso"]),
                                                                     font_rejected))
        for _ in range(self.MAX_TABLE_REJECTS + 1):
            data_bytes = f.serialize(replacements)
            expected_list = [replacements.get(pos, t) for pos, t in units]
            reread_texts = [t for _, t in adapter_unity_loc.TablesFile(data_bytes).texts()]
            if expected_list == reread_texts:
                break
            # the first entry that does not match: if it is a translation it is removed and it tries again
            differs = next((i for i, (a, r) in enumerate(zip(expected_list, reread_texts)) if a != r),
                           min(len(expected_list), len(reread_texts)))
            pos, text = units[differs] if differs < len(units) else (None, None)
            if pos not in replacements:
                data_bytes = None
                break
            del replacements[pos]
            self._refuse(text, "the text table doesn't match this translation", entry)
        else:
            data_bytes = None
        if data_bytes is None:
            self.log("  ! %s NOT written: the check after rewriting doesn't match" % entry["percorso"])
            self._remaining(t for _, t in units)
            return 0, len(units)
        path = self._game_path(entry["percorso"])
        if entry.get("catalogo"):  # the catalog first: a rewritten bundle with the CRC still there would not load
            self._remove_crc(entry)
        self._prepare_write(path, data_bytes)
        adapter_webjson.write_atomic(path, data_bytes)
        self._writes += 1
        self._applied.update(t for pos, t in units if pos in replacements)
        self._rejected += len({t for t in self._refused if self._refused[t].get("file") == entry["percorso"]})
        self._remaining(t for pos, t in units if pos not in replacements)
        missing = len(units) - len(replacements)
        self.log("  %s: %d texts translated, %d still untranslated" % (os.path.basename(entry["percorso"]),
                                                                       len(replacements), missing))
        return len(replacements), missing

    def _apply_lines(self, entry):
        f, units = self._load(entry)
        if units is None:
            self.log("  %s: This file no longer exists in the game, so I'm skipping it" % entry["percorso"])
            return 0, 0
        replacements, adapted = {}, 0
        for pos, text in units:
            translated = self.memory.get(text)
            if not translated:
                continue
            if translated.count("\n") != text.count("\n"):
                self._rejected += 1  # different number of lines (e.g. badly fixed by hand)
                self._refuse(text, "different lines from the original (%d instead of %d)" % (
                    translated.count("\n") + 1, text.count("\n") + 1), entry)
                continue
            is_ready = self._adapt(translated, text)
            if is_ready is None:
                self._rejected += 1
                absent = self._missing_chars(translated, text)
                self._refuse(text, "characters that the game's fonts can't draw: %s" % " ".join(absent),
                               entry, absent)
                continue
            adapted += is_ready != translated
            replacements[pos] = is_ready
        # each translation that would change how the game reads the file stays untranslated (on its own)
        data_bytes, rejected = f.serialize_robust(replacements)
        for pos in rejected:
            del replacements[pos]
        if rejected:
            originals = dict(units)
            self.log("  ! %s: %d translations left untranslated because the game would read them incorrectly (e.g. %r)"
                     % (entry["percorso"], len(rejected), originals.get(rejected[0], "")[:60]))
            self._rejected += len(rejected)
            for pos in rejected:
                self._refuse(originals.get(pos), "the game would misinterpret the file with this translation (an empty "
                                                 "line, a number, or a file marker where the text should go)", entry)
        missing = len(units) - len(replacements)
        self._remaining(t for pos, t in units if pos not in replacements)
        path = self._game_path(entry["percorso"])
        self._prepare_write(path, data_bytes)
        adapter_webjson.write_atomic(path, data_bytes)
        self._writes += 1
        self._applied.update(t for pos, t in units if pos in replacements)
        self.log("  %s: %d texts translated, %d still untranslated%s" % (
            entry["percorso"], len(replacements), missing,
            ", %d adapted to the font characters" % adapted if adapted else ""))
        return len(replacements), missing

    def _apply_csv(self, entry):
        """CSV table with one column per language (adapter_csv): only the cells of the original column are rewritten.
        The separator and the quotes in a file that the game splits by hand are replaced (CSVFile.adapt); a
        translation with a different number of lines, or with characters the fonts cannot draw, stays
        untranslated."""
        f, units = self._load(entry)
        if units is None:
            self.log("  %s: This file no longer exists in the game, so I'm skipping it" % entry["percorso"])
            return 0, 0
        replacements, adapted, rejected = {}, 0, []
        for pos, text in units:
            translated = self.memory.get(text)
            if not translated:
                continue
            if translated.count("\n") != text.count("\n"):
                rejected.append(text)
                self._refuse(text, "different lines from the original (%d instead of %d)" % (
                    translated.count("\n") + 1, text.count("\n") + 1), entry)
                continue
            is_ready = self._adapt(translated, text, repair=False)
            if is_ready is None:
                rejected.append(text)
                absent = self._missing_chars(translated, text)
                self._refuse(text, "characters that the game's fonts can't draw: %s" % " ".join(absent),
                               entry, absent)
                continue
            is_ready = f.adapt(is_ready)  # e.g. ',' -> ';' in a table that the game splits on commas
            forbidden = f.unwritable(is_ready)
            if forbidden:
                rejected.append(text)
                self._refuse(text, "characters that the game's table does not allow in the text: %s" % " ".join(
                    repr(c) if c.isspace() else c for c in forbidden), entry,
                    forbidden.replace("\n", "").replace("\r", ""))
                continue
            adapted += is_ready != translated
            replacements[pos] = is_ready
        if rejected:
            self.log("  ! %s: %d translations left untranslated because the game wouldn't read them correctly, e.g. %r "
                     "(I'll redo the ones written by PatchSmith; the ones fixed by hand need correcting with 'Fix "
                     "translations')" % (entry["percorso"], len(rejected), rejected[0][:60]))
            self._rejected += len(rejected)
        data_bytes = f.serialize(replacements)  # CSVError (ValueError) if the file read back differs: not written
        missing = len(units) - len(replacements)
        self._remaining(t for pos, t in units if pos not in replacements)
        path = self._game_path(entry["percorso"])
        self._prepare_write(path, data_bytes)
        adapter_webjson.write_atomic(path, data_bytes)
        self._writes += 1
        self._applied.update(t for pos, t in units if pos in replacements)
        self.log("  %s: %d texts translated, %d still untranslated%s" % (
            entry["percorso"], len(replacements), missing,
            ", %d adapted to the font characters" % adapted if adapted else ""))
        return len(replacements), missing

    def _apply_webui(self, entry):
        index = self._game_path(entry["percorso"])
        _, units = self._load(entry)
        if units is None:
            self.log("  %s: This file no longer exists in the game, so I'm skipping it" % entry["percorso"])
            return 0, 0
        dictionary = {t: self.memory[t] for _, t in units if t in self.memory}
        missing = len(units) - len(dictionary)
        self._remaining(t for _, t in units if t not in dictionary)

        with open(self._original(index), "rb") as f:
            html, encoding = adapter_webui.decode_html(f.read())
        new_html = adapter_webui.insert_tag(html)
        if new_html.count(adapter_webui.SCRIPT_NAME) != 1:
            self.log("  ! %s: Tag insertion failed, interface NOT modified" % entry["percorso"])
            return 0, len(units)
        html_bytes = adapter_webui.encode_html(new_html, encoding)
        script_bytes = adapter_webui.generate_script(dictionary).encode("utf-8")
        script = os.path.join(os.path.dirname(index), adapter_webui.SCRIPT_NAME)

        # 1) backup and record (fingerprint and created file) before any writing
        info = self._prepare_write(index, html_bytes)
        if adapter_webui.SCRIPT_NAME not in info.get("creati", []):
            info["creati"] = info.get("creati", []) + [adapter_webui.SCRIPT_NAME]
            save_info(index, info)
        # 2) script, checked before hooking it to the page
        adapter_webjson.write_atomic(script, script_bytes)
        syntax = adapter_webui.check_script(script)
        if syntax is False:
            self.log("  ! %s: The generated script is invalid; interface NOT modified" % entry["percorso"])
            return 0, len(units)
        # 3) page with the tag
        adapter_webjson.write_atomic(index, html_bytes)
        self._writes += 1
        self._applied.update(dictionary)
        self.log("  %s: interface, %d texts translated, %d still untranslated%s" % (
            entry["percorso"], len(dictionary), missing,
            "" if syntax else " (script syntax not verified: Node.js not present)"))
        return len(dictionary), missing

    def restore(self):
        """Puts back the originals of ALL the changed files in the game's folder."""
        paths = find_info(self.profile["cartella_gioco"])
        if not paths:
            self.log("  No files modified: the game is already in its original state.")
            return False
        for path in paths:
            restore_file(path, self.log)
        return True

    def show_status(self, examples=8):
        unique = {}
        for entry in self.profile["file"]:
            _, units = self._load(entry, update=False)
            if units is None:
                self.log("  %s: It no longer exists in the game" % entry["percorso"])
                continue
            for _, t in units:
                unique[t] = t in self.memory
            is_modified = os.path.exists(self._game_path(entry["percorso"]) + BACKUP_SUFFIX)
            kinds = {"web-ui": "interfaccia", "righe": "testi", "csv": "text table"}
            kind = kinds.get(entry.get("formato"), "dati")
            self.log("  %s (%s): %d texts, %s" % (entry["percorso"], kind, len(units),
                                                 "translated (backup available)" if is_modified else "originale"))
        translated_items = sum(unique.values())
        self.log("Unique texts: %d, translated in memory: %d, missing: %d" % (len(unique), translated_items,
                                                                              len(unique) - translated_items))
        for t in [t for t, ok in unique.items() if ok][:examples]:
            self.log("  EN: %s\n  %s: %s" % (t[:160].replace("\n", " / "), self.profile["lingua_destinazione"].upper(),
                                              self.memory[t][:160].replace("\n", " / ")))
