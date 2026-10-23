"""Fix translations: the list of a game's translations with the problems found (for the gui_corrections window and for
the tests) and what is done from there: saving the fixes, re-translating the chosen texts.

The rows of the list come from:
- the game's translation memory (memory/<game>.<language>.json) and the texts with a problem (.problemi.json: left
  untranslated, not suitable for the game, suspicious);
- games with translated files (web, GameMaker, Unity Localization...): also the texts of the game's files still
  without a translation, and the space each text has in the game (the longest line among the game's versions, see
  adapter_gamemaker.line_limits);
- Unity (XUnity): also the XUnity cache, that is what the game really shows (lines written by the live translator, by
  XUnity with Google, lines left identical to the original), and the journal of the sentences translated while you
  played.

The fixes go into the translation memory with Project.fix (with the lock of the shared memory; short texts also
into the shared translation memory). For Unity the lines to change in the XUnity cache are noted in
memory/<game>.<language>.da_scrivere.json: the next "Reapply" (or "Analyze and translate") writes them with the game
closed, see automatic.unity_full. After saving, the interface starts "Reapply" on its own.
"""
import collections
import datetime
import os
import random
import re

from . import adapter_gamemaker, adapter_unity, shared_file, language, legacy
from .backend import _numbers, _numbers_in_words, _unchanged, numbers_difference
from .project import Project, memory_path, entry_problems, protected_pattern
from .text import protect

# (key, label) of the window's filters, in menu order
FILTERS = (("all", "All"), ("remaining", "Left untranslated"), ("long", "Too long"),
          ("game", "Not written to the game"), ("placeholders", "Using numbers or placeholders"),
          ("language", "In another language"), ("suspicious", "Suspicious"), ("by_hand", "Fixed by hand"))
FILTER_NAMES = dict(FILTERS)
# the reasons from the Project checks (entry_problems) -> filter and words for the player
_REASONS = {"not translated": ("remaining", "identical to the original"),
           "in another language": ("language", "it seems to be in another language"),
           "copied reference": ("language", "same as the official translation of another language"),
           "letters from another language": ("language", "it contains letters from another language"),
           "wrong script": ("language", "wrong alphabet"),
           "different numbers": ("placeholders", "different numbers from the original"),
           "different line breaks": ("suspicious", "different lines from the original"),
           "line break added": ("suspicious", "different lines from the original"),
           "abnormal length": ("suspicious", "unusual length compared to the original"),
           "ripetizioni": ("suspicious", "repeated words"),
           "surrounding text": ("suspicious", "extra text from the translator"),
           "model refusal": ("suspicious", "the translator responded with a refusal"),
           "translated name": ("suspicious", "a game name translated")}
# verbs that in Italian menus and buttons are usually written in the imperative ("Salva", not "Salvare")
_UI_VERBS = set("save load export import continue quit exit start restart resume retry reset delete apply cancel "
                "confirm select play open close buy sell equip unequip use drop craft upgrade join leave create edit "
                "rename copy paste undo redo accept decline skip pause show hide enable disable back backup "
                "download upload install uninstall connect disconnect invite search sort filter refresh "
                "update share print send submit".split())
_INFINITIVE = re.compile(r"^[^\W\d_]{3,}(?:are|ere|ire)$", re.I)
_NOT_INFINITIVE = re.compile(r"(?:iere|ore)$", re.I)  # forziere, cavaliere, colore: nouns, not verbs
_NUMBERS = re.compile(r"\d+")


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


class Entry:
    """A row of the list. text: the original (the translation memory key); memory: the saved translation (or None);
    cache: for Unity the line of the XUnity cache (or None); space: (text as it is in the file, line break mark,
    paragraph start symbol, width, lines, official translations identical to the original) for texts with the space
    measured in the game; problems: {filter: reason} (empty reason = only to look at, e.g. a text with placeholders);
    by_hand: fixed by hand (different from the machine translation)."""

    __slots__ = ("text", "memory", "cache", "space", "problems", "by_hand")

    def __init__(self, text):
        self.text = text
        self.memory = self.cache = self.space = None
        self.problems = {}
        self.by_hand = False

    @property
    def translation(self):
        """The one that counts now: for Unity the cache line (it is what the game shows), if there is one."""
        if self.cache is not None:
            return self.cache
        return self.memory or ""

    def reasons(self):
        """The problems on one line, for the "Problem" column."""
        text = "; ".join(dict.fromkeys(m for k, m in self.problems.items() if m and k != "by_hand"))
        return text or ("fixed by hand" if self.by_hand else "")


class Items:
    """A game's translations for the "Fix translations" window (see load)."""

    def __init__(self, profile_path, profile, entries, test, orig, protected, known_machine):
        self.profile_path = profile_path
        self.profile = profile
        self.entries = entries
        self.test = test
        self.unity = profile.get("engine") == "unity"
        self.dest = profile["target_language"]
        self.orig = orig
        self.protected = protected
        # False: the game was translated before PatchSmith remembered the machine translations
        # (.automatiche.json): fixes made by hand cannot be recognized
        self.known_machine = known_machine
        # translations in memory without a note in .automatiche.json (made by a version that did not note them):
        # for PatchSmith they are machine translations, but they might have been fixed by hand in the file (see load)
        self.unannotated = 0

    @property
    def name(self):
        return self.profile.get("name", "")

    def counts(self):
        occurrences = collections.Counter(k for x in self.entries for k in x.problems)
        occurrences["all"] = len(self.entries)
        return {k: occurrences.get(k, 0) for k, _ in FILTERS}

    def apply_filter(self, filter_="all", search=""):
        """The rows of the filter that contain 'search' (in the original or in the translation, case-insensitive). In a
        filter, first those with a written problem (e.g. among placeholders, first those that do not match)."""
        search = (search or "").casefold().strip()
        output = [x for x in self.entries if filter_ in ("all", None, "") or filter_ in x.problems]
        if search:
            output = [x for x in output if search in x.text.casefold() or search in x.translation.casefold()]
        if filter_ not in ("all", None, ""):
            output.sort(key=lambda x: not x.problems.get(filter_))
        return output


def _game_cache(profile):
    """{original: translation} of the XUnity cache: the one in the game, or the one saved by "Restore originals"."""
    folder, a = profile.get("game_folder") or "", profile["target_language"]
    path = adapter_unity._cache_path(folder, a)
    if not os.path.exists(path):
        path = adapter_unity.saved_cache_path(profile["name"], a)
    return adapter_unity.xunity_cache_entries(folder, a, path)


def load(profile_path, test=False, log=None):
    """The list of the game's translations (Items), with the problems of each row. Reads only (Project cleans up the
    invisible characters left by old versions, as always). test: the translation memory of the test translator."""
    log = log or (lambda _m: None)
    p = Project(profile_path, log, test=test)
    profile = p.profile
    dest = profile["target_language"]
    entries = {}

    def entry(t):
        if t not in entries:
            entries[t] = Entry(t)
        return entries[t]

    # 1. the texts of the game's files, in their order, with the space they have
    game = p.game_texts() if profile.get("file") else []
    for v, pos, t, raw in game:
        x = entry(t)
        limit = (v.get("limits") or {}).get(adapter_gamemaker.key_of(pos)) if isinstance(pos, tuple) and pos \
            and v.get("format", "json") == "json" else None
        if limit and v.get("line_break") and (x.space is None or limit[0] < x.space[3]):
            x.space = (raw, v.get("line_break"), v.get("bullet"), limit[0], limit[1],
                        limit[2] if len(limit) > 2 else 0)
    # 2. the translation memory; 3. for Unity the XUnity cache (what the game shows)
    for t, v in p.memory.items():
        if isinstance(v, str):
            entry(t).memory = v
    unity = profile.get("engine") == "unity"
    cache = _game_cache(profile) if unity and not test else {}
    for t, v in cache.items():
        if t not in language.ENDONYMS and any(c.isalpha() for c in t):
            entry(t).cache = v
    problems = p.problems()
    for t in problems:
        entry(t)
    in_game_texts = [t for _, _, t, _ in game]
    orig = p.source_language(in_game_texts or None)
    protected = tuple(sorted(set(p.codes) | set(p.active_names(list(entries)))))
    pattern = protected_pattern(protected)
    # official translations into other languages (language files with the game's translations): for
    # "copied reference"
    references = {t: list((c.get("references") or {}).values()) for t, c in game_contexts(p, game).items()}
    machine = p._read_machine()
    provisional = _provisional(profile["name"]) if unity and not test else {}
    flagged_cache = adapter_unity.check_xunity_cache(cache, orig, dest, p.active_names(list(cache))) \
        if cache else {}
    for x in entries.values():
        _problems_of(x, problems.get(x.text) or {}, dest, pattern, protected, references.get(x.text) or (),
                     machine, provisional, flagged_cache)
    items = Items(os.path.abspath(profile_path), profile, list(entries.values()), test, orig, protected,
                    bool(machine))
    if machine:
        items.unannotated = sum(1 for x in items.entries if isinstance(x.memory, str) and x.text not in machine)
    return items


def _provisional(name):
    """{text: response} of the sentences translated while you played without all the checks (journal of the live
    translator: 'relaxed', 'rejected', 'deferred'...), if they were not translated properly afterwards."""
    from .xunity_service import read_journal
    output = {}
    for line in read_journal(name):
        response = line.get("response")
        if not isinstance(response, str):
            continue
        if line.get("outcome") == "ok":
            output.pop(line["text"], None)
        else:
            output[line["text"]] = response
    return output


def _problems_of(x, problem, dest, pattern, protected, references, machine, provisional, flagged_cache):
    """The problems of a row ({filter: reason} in x.problems): see FILTERS."""
    t, tr = x.text, x.translation
    # fixed by hand: different from the noted machine translation. Without a note (translation memories of the
    # versions that did not keep them) it is a machine translation, as for Project._shared_memory and _to_redo;
    # the fixes made here to texts without a note get one (Project.fix)
    if isinstance(x.memory, str) and t in machine and machine[t] != x.memory:
        x.by_hand = True
        x.problems["by_hand"] = "fixed by hand"
    where, value_ = problem.get("where"), problem.get("value")
    if not tr.strip():
        reason = problem.get("reason") if where == "translation" else ""
        x.problems["remaining"] = "without translation" + (" (%s)" % reason if reason else "")
    if where == "game" and (value_ is None or value_ == x.memory):
        x.problems["game"] = problem.get("reason") or "the game couldn't display it"
    if where == "memory" and (value_ is None or value_ == x.memory):
        x.problems["suspicious"] = "suspicious: %s" % (problem.get("reason") or "doesn't pass the checks")
    if where == "length" and (value_ is None or value_ == x.memory):  # (see Project._shorten)
        x.problems["long"] = "too long: %s" % (problem.get("reason") or "longer than its space")
    if not tr.strip():
        return
    for m in entry_problems(t, tr, dest, pattern, references):
        if m in _REASONS:
            filter_, words = _REASONS[m]
            x.problems.setdefault(filter_, words)
    if x.cache is not None and t in flagged_cache:
        for m in flagged_cache[t][1]:
            if m == "identical to the original":
                x.problems.setdefault("remaining", "identical to the original")
            elif m == "in another language":
                x.problems.setdefault("language", "it seems to be in another language")
            elif m == "different placeholders":
                x.problems.setdefault("placeholders", "placeholders different from the original")
    if x.cache is not None and provisional.get(t) == x.cache:
        x.problems.setdefault("suspicious", "translated while you were playing, without all the checks")
    # numbers and placeholders: the filter has all the texts that contain them (to look at), with the reason if they
    # do not match
    marker_warnings = [a for a in warnings(t, tr, protected) if a.startswith(("missing", "extra", "different numbers"))]
    if marker_warnings:
        x.problems["placeholders"] = "; ".join(marker_warnings)
    elif protect(t, protected)[1] or _NUMBERS.search(t):
        x.problems.setdefault("placeholders", "")
    # length: with the space measured in the game; without it, the short labels that became much longer
    measured = measure(x, tr)
    if measured and measured[0] is not None and too_long(*measured):
        x.problems["long"] = "longest line: %d characters, about %d fit in the game" % measured
    elif x.space is None and len(t.split()) <= 3 and len(t) <= 25 and len(tr) >= 2 * len(t) and \
            len(tr) - len(t) >= 8:  # (thresholds tested on real memories: about 2-5% of texts, the obvious cases)
        x.problems.setdefault("long", "%d characters instead of %d: it might not fit" % (len(tr), len(t)))
    # one-word labels: turned into a sentence, or an infinitive verb where the imperative is used
    words = re.findall(r"[^\W\d_]+", t)
    if len(words) == 1 and len(words[0]) >= 3 and len(re.findall(r"[^\W\d_]+", tr)) >= 3:
        x.problems.setdefault("suspicious", "a word translated as a phrase")
    if dest.split("-")[0] == "it" and 1 <= len(words) <= 2 and "".join(words).lower() in _UI_VERBS:
        first = tr.split()[0] if tr.split() else ""
        if _INFINITIVE.match(first) and not _NOT_INFINITIVE.search(first):
            shape = _imperative(first)
            x.problems.setdefault("suspicious", "infinitive verb: buttons usually use %s" % (
                '"%s", not "%s"' % (shape, first) if shape else 'the imperative ("%s" is in the infinitive)' % first))


def _imperative(infinitive):
    """The imperative for a button, for regular verbs: 'Salvare' -> 'Salva', 'Esportare' -> 'Esporta', 'Chiudere'
    -> 'Chiudi'. None if it is not certain: verbs in -ire ('Inserire' -> 'Inserisci', 'Uscire' -> 'Esci') and irregular
    ones ('Rifare' -> 'Rifai', 'Ottenere' -> 'Ottieni')."""
    lowercase = infinitive.lower()
    if lowercase.endswith(("fare", "dare", "stare", "andare", "tenere", "ire")):
        return None
    return infinitive[:-3] + ("a" if lowercase.endswith("are") else "i")


def warnings(original, translated, protected=()):
    """What is wrong in a translation written by hand, in words for the player (empty: it is fine): placeholders and
    game codes that are missing or extra (they must be copied as they are), different numbers, different lines."""
    if not translated.strip():
        return ["the translation is empty"]
    output = []
    so, markers_o = protect(original, protected)
    st, markers_t = protect(translated, protected)
    absent = collections.Counter(markers_o) - collections.Counter(markers_t)
    additional = collections.Counter(markers_t) - collections.Counter(markers_o)
    if absent:
        output.append("missing %s (must be copied exactly as is)" % ", ".join(sorted(absent)))
    if additional:
        output.append("extra: %s" % ", ".join(sorted(additional)))
    without_o, without_t = re.sub(r"\[\[\d+\]\]", " ", so), re.sub(r"\[\[\d+\]\]", " ", st)
    if _numbers(without_o) != _numbers(without_t) and not _numbers_in_words(without_o, without_t, "it"):
        absent, additional = numbers_difference(without_o, without_t)
        details = (["missing %s" % ", ".join(absent)] if absent else []) + \
            (["extra %s" % ", ".join(additional)] if additional else [])
        output.append("different numbers from the original%s" % (" (%s)" % "; ".join(details) if details else ""))
    if original.count("\n") != translated.count("\n"):
        output.append("lines: %d instead of %d" % (translated.count("\n") + 1, original.count("\n") + 1))
    return output


def measure(x, translated):
    """(longest line, width) of the translation as the game writes it, for texts with the measured space (x.space);
    (None, width) if it does not have the shape of the original; None without a measured space."""
    if not x.space:
        return None
    raw, marker, bullet, width, lines, _ = x.space
    written = adapter_gamemaker.reassemble_line_breaks(raw, translated, marker, bullet, width, lines)
    if written is None:
        return None, width
    return adapter_gamemaker._longest_line(written, marker), width


def too_long(line_len, width):
    """As in Project._apply_json: over 10% (and 2 characters) beyond the longest line among the game's versions."""
    return line_len > max(width + 2, 1.1 * width)


# --- fixes to write into the XUnity cache (Unity) ------------------------------------------------------------

def to_write_path(profile):
    """memory/<game>.<language>.da_scrivere.json: {text: {before, value, when}}, the lines of the XUnity cache to
    change with a fix (or a re-translation) made in "Fix translations". 'before': the translation the line had (None:
    no line, or any line from the translator). automatic.unity_full writes them."""
    return memory_path(profile)[:-len(".json")] + ".da_scrivere.json"


def _read_to_write(path):
    try:
        data = legacy.upgrade_entries(shared_file.read_json(path))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def mark_to_write(profile, lines):
    """lines: {text: (translation the cache line has now or None, new translation)}. A fix already waiting for the
    same text keeps its 'before' (the line has not changed yet)."""
    if not lines:
        return
    path = to_write_path(profile)
    data = _read_to_write(path)
    base = dict(data)
    for t, (before, value_) in lines.items():
        old_folder = data.get(t)
        if isinstance(old_folder, dict) and "before" in old_folder:
            before = old_folder["before"]
        data[t] = {"before": before, "value": value_, "when": _now()}
    shared_file.save_merging(path, data, base)


def to_write(profile):
    """{text: {'before', 'value'}} of the XUnity cache lines to change (see to_write_path)."""
    return {t: d for t, d in _read_to_write(to_write_path(profile)).items()
            if isinstance(t, str) and isinstance(d, dict) and isinstance(d.get("value"), str) and d["value"].strip()
            and (d.get("before") is None or isinstance(d.get("before"), str))}


def written_texts(profile, texts):
    """Removes from the list of lines to write the ones already written (or left on purpose)."""
    path = to_write_path(profile)
    data = _read_to_write(path)
    if not any(t in data for t in texts):
        return
    base = dict(data)
    for t in texts:
        data.pop(t, None)
    shared_file.save_merging(path, data, base)


# --- saving and re-translating --------------------------------------------------------------------------

def save(profile_path, changes, seen=None, test=False, log=print):
    """Saves the fixes ({text: translation}) made in the window: into the translation memory (Project.fix) and,
    for Unity, among the lines to write into the XUnity cache (seen: {text: the cache line the window showed, or
    None}). Returns (texts changed in the translation memory, cache lines to write)."""
    p = Project(profile_path, log, test=test)
    changes = {t: v for t, v in changes.items() if isinstance(v, str) and v.strip()}
    changed = p.fix(changes)
    lines = {}
    if p.profile.get("engine") == "unity" and not test:
        seen = seen or {}
        lines = {t: (seen.get(t), v) for t, v in changes.items() if seen.get(t) != v}
        mark_to_write(p.profile, lines)
    if changed or lines:
        log("Fix translations: %d corrections saved%s" % (
            len(set(changed) | set(lines)), " (The XUnity cache lines are written when the game is closed)"
            if lines else ""))
    return changed, lines


def texts_to_retranslate(items):
    """(texts, fixes made by hand that remain) for "Re-translate everything": the machine translations (also those
    without a note from previous versions: see Items.known_machine and unannotated) and the texts without a
    translation or left untranslated. For Unity, the cache lines that are not in the translation memory only if left
    untranslated or provisional (the others might be fixes made in the file)."""
    texts, by_hand = [], 0
    for x in items.entries:
        if x.by_hand:
            by_hand += 1
            continue
        suspicious = x.problems.get("suspicious") or ""  # (Italian in the problems saved by earlier versions)
        is_provisional = "translated while you were playing" in suspicious or "tradotta mentre giocavi" in suspicious
        if x.memory is not None or "remaining" in x.problems or is_provisional:
            texts.append(x.text)
    return texts, by_hand


def game_contexts(p, game):
    """{text: context} (key and official translations) of the game's language files, for the local translator.
    game: Project.game_texts()."""
    contexts, by_file = {}, collections.defaultdict(list)
    for v, pos, t, _ in game:
        by_file[id(v)].append((v, pos, t))
    for group in by_file.values():
        if group[0][0].get("localization"):
            try:
                for t, c in p._contexts(group[0][0], [(pos, t) for _, pos, t in group],
                                        p.profile["target_language"]).items():
                    contexts.setdefault(t, c)
            except (OSError, ValueError, KeyError):
                pass
    return contexts


def create_translator(name, model=None, log=print):
    """The translator for re-translating: the chosen one (backend.create_backend) or, with 'model', the local
    translator with that model (Fast or Quality), which must be installed."""
    from . import backend, ollama
    if name == "local" and model:
        if ollama.state() != "ready":
            raise backend.BackendError(backend.NOT_INSTALLED, "TRANSLATOR_MISSING", command=backend.INSTALL_COMMAND)
        if not ollama.model_present(model):
            raise backend.BackendError("The %s model is not downloaded: choose it next to the local translator and "
                                       "press 'Install...'." % ollama.MODELS[model]["label"], "TRANSLATOR_MISSING",
                                        command="python -I smith.py local --install %s" % model)
        return backend.Local(model, log=log)
    return backend.create_backend(name, log)


def _new_round():
    """The round (the local translator's seeds) of a "Re-translate selected": random, never 0, so that even pressing
    again on the same line the requests are new (with a fixed round the second time would return the answer of the
    first)."""
    return random.randint(1, 99_999)


def retranslate(profile_path, texts, translator, log=print, test=False):
    """Re-translates the texts chosen in the window (texts None: everything, see texts_to_retranslate). The chosen
    texts are redone with new requests every time (also those fixed by hand: you chose them; see _new_round);
    "everything" with the normal requests (useful with the other model: Quality gives other answers). For Unity the
    changed lines go among those to write into the XUnity cache: the chosen ones always, with "everything" only those
    that still have PatchSmith's translation.
    Returns {retranslated, remaining_count, texts, interrupted (the reason, if the translator stopped)}."""
    selected = texts is not None
    if not selected:
        texts, _ = texts_to_retranslate(load(profile_path, test, log))
    p = Project(profile_path, log, test=test)
    dest = p.profile["target_language"]
    game = p.game_texts() if p.profile.get("file") else []
    orig = p.source_language([t for _, _, t, _ in game] or None)
    machine = dict(p._read_machine())
    before = {t: p.memory.get(t) for t in texts}
    new_lines = []
    try:
        new_lines, _ = p.retranslate(texts, translator, orig, dest, game_contexts(p, game),
                               round_=_new_round() if selected else 0)
    finally:
        changed = [t for t in before if isinstance(p.memory.get(t), str) and p.memory.get(t) != before[t]]
        if p.profile.get("engine") == "unity" and not test and changed:
            cache = _game_cache(p.profile)
            lines = {}
            for t in changed:
                c = cache.get(t)
                if selected or c is None or c in (before[t], machine.get(t)) or _unchanged(c, t):
                    lines[t] = (c, p.memory[t])
            mark_to_write(p.profile, lines)
    unique = list(dict.fromkeys(texts))
    log("Re-translated: %d texts out of %d%s" % (len(new_lines), len(unique),
                                                 " (the others keep the previous translation)"
                                                 if len(new_lines) < len(unique) else ""))
    return {"retranslated": len(new_lines), "remaining_count": len(unique) - len(new_lines), "texts": len(unique),
            "interrupted": p.translation_reason if p.translation_outcome == "TRANSLATOR_BLOCKED" else ""}
