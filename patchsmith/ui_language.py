"""PatchSmith's interface language.

The program's texts are written in English in the code. The other languages are in
patchsmith/ui_language/<code>.json:
    {"name": "Italiano", "flag": {"vertical": [...]}, "texts": {"English text": "translation", ...}}
Translation happens when the window shows a text (hook_tk: buttons, labels, titles, menus, values of drop-down
menus; TextVar for status lines; translate() for the log), also for texts with values inside: an entry with %s, %d...
("Game texts: %d, already translated: %d") works as a template for all the lines of that form. Texts without a
translation stay in English. The original texts stay remembered in the widgets: when the language changes,
retranslate() puts the ones already shown back in the new language without reopening the program.

To add a language: the command line command "interface --create fr" (translates all the texts with the local
translator; those already in the file stay) and then review the file patchsmith/ui_language/fr.json by hand. The flag
is described with stripes ({"vertical": [colors]} or {"horizontal": [colors]}) or with a 24x16 PNG image next to
the file ({"file": "fr.png"}).
"""
import ast
import json
import os
import re
import weakref

from . import legacy

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_language")
SOURCE = "en"
ENGLISH = {"name": "English", "flag": {"kind": "gb"}}
# placeholders of the program's texts: %s, %d, %5.1f, %r, %(name)s, %%
_MARKER = re.compile(r"%(?:\([^)]*\))?[-+ #0]*(?:\d+)?(?:\.\d+)?[sdrifxXceEgG%]")
# what each placeholder matches in a line already written
_VALUE = {"d": r"-?\d[\d.,']*", "i": r"-?\d[\d.,']*", "f": r"-?[\d.,]+", "x": r"[0-9a-fA-F]+", "%": r"%"}
# at the start of log lines: indentation and markers ("! ", "~ ", "* ", "- ", "== ") that stay as they are
_PREFIX = re.compile(r"^(\s*(?:(?:!|~|\*|-|==)\s+)?)(.*?)(\s*)$", re.S)
_state = {"code": SOURCE, "texts": {}, "models": None}
_catalogs = {}  # texts_of
VERBATIM = "⁠"  # at the start of a text: it stays as it is in every language (e.g. the language names in their menu)


# --- available languages ---------------------------------------------------------------------------------------------

def languages():
    """{code: {"name", "flag"}}: English (the language of the code) and the files in patchsmith/ui_language/."""
    output = {SOURCE: ENGLISH}
    try:
        names = sorted(os.listdir(FOLDER))
    except OSError:
        names = []
    for name in names:
        code, ext = os.path.splitext(name)
        if ext.lower() != ".json" or code == SOURCE:
            continue
        try:
            with open(os.path.join(FOLDER, name), encoding="utf-8") as f:
                data = legacy.load(f)
        except (OSError, ValueError):
            continue
        output[code] = {"name": data.get("name") or code, "flag": data.get("flag") or {}}
    return output


def default_language():
    """The interface language if none was chosen: PATCHSMITH_INTERFACE, then the Windows one if PatchSmith has it
    (today Italian), otherwise English. A language chosen with the flag is saved in settings.json and wins."""
    available = languages()
    choice = os.environ.get("PATCHSMITH_INTERFACE") or os.environ.get("PATCHSMITH_INTERFACCIA")  # (old name)
    if choice in available:
        return choice
    from .language import windows_language
    windows = (windows_language() or "").split("-")[0].lower()
    if windows in available:
        return windows
    return SOURCE


def texts_of(code):
    """{source text: translation} of a language (empty for English or a missing file); read once."""
    if code not in _catalogs:
        try:
            with open(os.path.join(FOLDER, code + ".json"), encoding="utf-8") as f:
                _catalogs[code] = legacy.load(f).get("texts") or {}
        except (OSError, ValueError):
            _catalogs[code] = {}
    return _catalogs[code]


def set_(code):
    """Uses the language 'code' (if it is not available, English). Returns the one used."""
    texts = {}
    if code != SOURCE:
        try:
            with open(os.path.join(FOLDER, code + ".json"), encoding="utf-8") as f:
                texts = legacy.load(f).get("texts") or {}
        except (OSError, ValueError):
            code = SOURCE
    _state.update(code=code, texts={k: v for k, v in texts.items() if isinstance(v, str) and v}, models=None)
    return code


def current():
    return _state["code"]


# --- translation -----------------------------------------------------------------------------------------------------

def _template_regex(key):
    """(regex, markers) of an entry with placeholders, or None if it has none."""
    markers = _MARKER.findall(key)
    if not markers:
        return None
    parts, position = [], 0
    for m in _MARKER.finditer(key):
        parts.append(re.escape(key[position:m.start()]))
        kind = m.group()[-1]
        parts.append("(%s)" % _VALUE.get(kind, r".*?") if kind != "%" else "%")
        position = m.end()
    parts.append(re.escape(key[position:]))
    try:
        return re.compile("^" + "".join(parts) + "$", re.S), [s for s in markers if s != "%%"]
    except re.error:
        return None


def _models():
    if _state["models"] is None:
        models = {}
        for key, value_ in _state["texts"].items():
            if not _MARKER.sub("", key).strip():  # placeholders only ("%s", "%s%s"): it would match everything
                continue
            r = _template_regex(key)
            if r is None:
                continue
            start = _MARKER.split(key)[0][:6]
            models.setdefault(start, []).append((len(_MARKER.sub("", key)), r[0], r[1], value_))
        for items in models.values():  # first the templates with more fixed text (the most precise)
            items.sort(key=lambda m: -m[0])
        # last fallback: two pieces separated by ": " or in parentheses, each translated on its own
        models[None] = [(0,) + _template_regex(k) + (k,) for k in ("%s: %s", "%s (%s)", "%s - %s")]
        _state["models"] = models
    return _state["models"]


def _fill(translation, markers, values):
    """The translation with the values in place of the placeholders: by type and in order (the translation may have
    moved them)."""
    code = {}
    for s, v in zip(markers, values):
        code.setdefault(s, []).append(v)
    output, position = [], 0
    for m in _MARKER.finditer(translation):
        output.append(translation[position:m.start()])
        if m.group() == "%%":
            output.append("%")
        else:
            tail = code.get(m.group())
            output.append(tail.pop(0) if tail else m.group())
        position = m.end()
    output.append(translation[position:])
    return "".join(output)


def _translate_core(text, depth=0):
    if not text or not _state["texts"]:
        return text
    t = _state["texts"].get(text)
    if t is not None:
        return t
    models = _models()
    # among the templates that start like the text, the one with more fixed text wins (the most precise), not the one
    # with the longest start: "%s: interface, %d texts translated..." beats "index.html: %s"
    best = None
    for start in [text[:n] for n in range(min(6, len(text)), -1, -1)]:
        for verbatim, regex, markers, translation in models.get(start, ()):
            if best is not None and verbatim <= best[0]:
                break  # (sorted by fixed text: the others in the group are less precise)
            m = regex.match(text)
            if m:
                best = (verbatim, m, markers, translation)
                break
    if best is None and depth == 0:  # messages made of several sentences of the file one after the other
        translated = _by_sentences(text)
        if translated != text:
            return translated
    if best is None and depth <= 1:  # fallbacks ("%s: %s", "%s (%s)"), also for the pieces inside a sentence
        for verbatim, regex, markers, translation in models[None]:
            m = regex.match(text)
            if m:
                best = (verbatim, m, markers, translation)
                break
    if best is None:
        return text
    _verbatim, m, markers, translation = best
    values = list(m.groups())
    if depth < 2:  # the pieces of text inside (e.g. " (387 from other games)"): translated too
        values = [_translate_piece(v, depth + 1) if s[-1] in "sr" else v for s, v in zip(markers, values)]
    return _fill(translation, markers, values)


def _by_sentences(text):
    """The text split into sentences ("... avoided. A sentence with..."), translated in groups: from the start, the
    longest group of consecutive sentences that is in the file (an entry can have several sentences); the sentences
    that are not there stay as they were."""
    parts = re.split(r"(?<=[.!?])( +)(?=[A-ZÀ-Ý\"«(\d])", text)
    sentences, spaces = parts[0::2], parts[1::2] + [""]
    if len(sentences) < 2:
        return text
    output, i = [], 0
    while i < len(sentences):
        for j in range(len(sentences), i, -1):
            if (i, j) == (0, len(sentences)):
                continue  # (the whole text is already known not to be there)
            piece = "".join(sentences[k] + spaces[k] for k in range(i, j - 1)) + sentences[j - 1]
            translated = _translate_core(piece, 1)
            if translated != piece or j == i + 1:
                output.append(translated + spaces[j - 1])
                i = j
                break
    return "".join(output)


def _translate_piece(text, depth=0):
    m = _PREFIX.match(text)
    prefix, core, tail = m.group(1), m.group(2), m.group(3)
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", core):  # numbers and symbols: only an entry of the file (e.g. "A:" -> "To:")
        return prefix + _state["texts"].get(core, core) + tail if core else text
    return prefix + _translate_core(core, depth) + tail


def translate(text):
    """The text in the interface language (line by line; what is not in the file stays as it is)."""
    if isinstance(text, str) and text.startswith(VERBATIM):
        return text[len(VERBATIM):]
    if not isinstance(text, str) or _state["code"] == SOURCE or not _state["texts"] or not text.strip():
        return text
    whole = _translate_piece(text)
    if whole != text or "\n" not in text:
        return whole
    return "\n".join(_translate_piece(line) for line in text.split("\n"))


# --- window (Tkinter) ------------------------------------------------------------------------------------------------

_TEXT_OPTIONS = ("text", "label", "title", "message")
_VARIABLES = weakref.WeakSet()  # the TextVar objects, to translate again when the language changes


def _remember(widget, cnf):
    """The original (English) texts given to the widget, to translate them again if the language changes
    (retranslate)."""
    if not isinstance(cnf, dict):
        return
    for k in _TEXT_OPTIONS + ("values",):
        v = cnf.get(k)
        if isinstance(v, str) or k == "values" and isinstance(v, (list, tuple)):
            widget.__dict__.setdefault("_patchsmith_texts", {})[k] = v


def hook_tk():
    """The texts given to Tkinter (buttons, labels, menus, window titles, values of drop-down menus) go through
    translate() and stay remembered in the widget (retranslate). Only once; with English nothing changes."""
    import tkinter
    if getattr(tkinter.Misc, "_patchsmith_language", False):
        return
    original_options = tkinter.Misc._options
    original_title = tkinter.Wm.wm_title

    def _translate_options(cnf):
        if not cnf or _state["code"] == SOURCE and not any(
                isinstance(cnf.get(k), str) and cnf[k].startswith(VERBATIM) for k in _TEXT_OPTIONS):
            return cnf
        cnf = dict(cnf)
        for k in _TEXT_OPTIONS:
            if isinstance(cnf.get(k), str):
                cnf[k] = translate(cnf[k])
        if isinstance(cnf.get("values"), (list, tuple)):
            cnf["values"] = [translate(v) if isinstance(v, str) else v for v in cnf["values"]]
        return cnf

    def _options(self, cnf, kw=None):
        _remember(self, cnf)
        _remember(self, kw)
        return original_options(self, _translate_options(cnf), _translate_options(kw))

    def wm_title(self, string=None):
        if isinstance(string, str):
            self.__dict__["_patchsmith_titolo"] = string
        return original_title(self, translate(string) if isinstance(string, str) else string)

    tkinter.Misc._options = _options
    tkinter.Wm.wm_title = tkinter.Wm.title = wm_title
    from tkinter import ttk
    original_heading = ttk.Treeview.heading

    def heading(self, column, option=None, **kw):  # (column headings do not go through _options)
        if isinstance(kw.get("text"), str):
            self.__dict__.setdefault("_patchsmith_intestazioni", {})[column] = kw["text"]
            kw["text"] = translate(kw["text"])
        return original_heading(self, column, option, **kw)
    ttk.Treeview.heading = heading
    tkinter.Misc._patchsmith_language = True


def retranslate(root):
    """After set_(): the texts already shown by the root window and its windows (buttons, labels, values of
    drop-down menus, titles, column headings, TextVar) again from their originals, in the new language.
    The choices of drop-down menus (their variables) and the log are put back by whoever wrote them."""
    import tkinter

    def visit(w):
        for option_name, original in list(getattr(w, "_patchsmith_texts", {}).items()):
            try:
                w.configure(**{option_name: original})
            except tkinter.TclError:  # not an option of the widget (e.g. a menu entry): leave it alone
                del w._patchsmith_testi[option_name]
        if getattr(w, "_patchsmith_titolo", None) is not None:
            w.title(w._patchsmith_titolo)
        for column, original in list(getattr(w, "_patchsmith_intestazioni", {}).items()):
            w.heading(column, text=original)
        for child in w.winfo_children():
            visit(child)
    visit(root)
    for v in list(_VARIABLES):
        try:
            v.set(v.original)
        except tkinter.TclError:  # its window is already closed
            pass


def TextVar(*args, **kw):
    """A StringVar for the texts shown (status lines): what is written goes through translate() (and stays remembered,
    for retranslate)."""
    import tkinter

    class _TextVar(tkinter.StringVar):
        original = ""
        __hash__ = object.__hash__  # (Variable redefines == but not the hash: needed to be in _VARIABLES)

        def set(self, value):
            self.original = value
            return super().set(translate(value) if isinstance(value, str) else value)
    v = _TextVar(*args, **kw)
    if kw.get("value"):
        v.set(kw["value"])
    _VARIABLES.add(v)
    return v


def flag(code, master=None):
    """The language's flag as a Tkinter image (24x16), or None."""
    import tkinter
    info = languages().get(code, {}).get("flag") or {}
    width, height = 24, 16
    if info.get("file"):
        try:
            return tkinter.PhotoImage(master=master, file=os.path.join(FOLDER, info["file"]))
        except tkinter.TclError:
            pass
    img = tkinter.PhotoImage(master=master, width=width, height=height)
    if info.get("kind") == "gb":  # simplified Union Jack
        img.put("#012169", to=(0, 0, width, height))
        for x in range(width):  # white diagonals with the red ones on top
            y = round(x * (height - 1) / (width - 1))
            for dy in (-2, -1, 0, 1, 2):
                for yy in (y + dy, height - 1 - y + dy):
                    if 0 <= yy < height:
                        img.put("#ffffff" if abs(dy) > 0 else "#c8102e", to=(x, yy, x + 1, yy + 1))
        img.put("#ffffff", to=(0, height // 2 - 3, width, height // 2 + 3))
        img.put("#ffffff", to=(width // 2 - 3, 0, width // 2 + 3, height))
        img.put("#c8102e", to=(0, height // 2 - 2, width, height // 2 + 2))
        img.put("#c8102e", to=(width // 2 - 2, 0, width // 2 + 2, height))
        return img
    stripes, vertical = info.get("vertical"), True
    if not stripes:
        stripes, vertical = info.get("horizontal"), False
    if not stripes:
        img.put("#888888", to=(0, 0, width, height))
        return img
    n = len(stripes)
    for i, color in enumerate(stripes):
        if vertical:
            img.put(color, to=(width * i // n, 0, width * (i + 1) // n, height))
        else:
            img.put(color, to=(0, height * i // n, width, height * (i + 1) // n))
    return img


# --- creating a language (command "interface --create <code>") -------------------------------------------------------

def _looks_like_text(s):
    """A text for the program's user (not an internal name, a path, a regex...)."""
    core = _PREFIX.match(s).group(2).strip()
    if len(core) < 2 or not re.search(r"[A-Za-zÀ-ÿ]{2}", core) or "\n\n\n" in core:
        return False
    if re.search(r"(\\[dswbAZ(\[]|\(\?|[{}]\s*$|^[\w./\\-]+\.(?:json|py|dll|exe|cfg|ini|txt|log|bundle)$|^https?://)",
                 core) or re.fullmatch(r"[\w.\-]+", core) and not re.match(r"[A-ZÀ-Ý]", core):
        return False
    if re.fullmatch(r"[\w.\-/\\:]+", core) and ("_" in core or "/" in core or "\\" in core):
        return False
    if re.search(r"\b(?:var|func|class|extends|return|elif|def|import)\b.*[:=(]|\(\)|\)\s*->|\[[^\]]*\]\?"
                 r"|^#|==|^if .*:$|^for \w+ in .+:$|^[a-z_][\w.]* = |\b[a-z_]\w*\.[a-z_]\w*\("
                 r"|(?<![\w%])_[a-z]\w*", core):
        return False  # code (e.g. the script for Godot) or regular expressions
    if re.match(r"[A-Z][a-z]+=\S", core) or re.fullmatch(r"[a-z][\w-]*:", core):
        return False  # a line of a configuration file (Program=...), the prefix of a signal (removed:)
    if re.fullmatch(r"<.*>|-\w[\w-]*:?|\[assembly: .*\]|using [\w.]+;", core):
        return False  # XML, a command-line option, C# source (the details of PatchSmith.exe: see icon.py)
    if re.search(r"\w\|\w", core):
        return False  # alternatives of a regular expression (a|b)
    words = core.split()
    if len(words) >= 12 and core == core.lower() and not re.search(r"[.,:;!?()'\"]", core) \
            and sum(map(len, words)) / len(words) < 4.5:
        return False  # list of frequent words (language detection), not a sentence
    return " " in core or core.endswith(("…", ":", "?", "!")) or re.match(r"[A-ZÀ-Ý][a-zà-ÿ]+$", core) is not None


def program_texts(folder=None):
    """The texts of the code (patchsmith/*.py, in English), without the docstrings, as they go in a language file."""
    folder = folder or os.path.dirname(os.path.abspath(__file__))
    texts = set()
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py") or name == "legacy.py":  # (legacy: the names of the data, old and new)
            continue
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            tree = ast.parse(f.read())
        skip = set()  # docstrings and the field names of namedtuples: code, not texts
        for n in ast.walk(tree):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body and \
                    isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant):
                skip.add(id(n.body[0].value))
            if isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", None)) == "namedtuple":
                skip.update(id(a) for a in n.args)
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
                for piece in n.value.split("\n"):
                    if _looks_like_text(piece):
                        texts.add(_PREFIX.match(piece).group(2).strip())
    from .language import AUTO_LABEL, NAMES
    texts.update(NAMES.values())
    texts.add(AUTO_LABEL)
    return sorted(texts)


def create_language(code, name, translator, log=print, flag_info=None, maximum=None):
    """Creates or completes patchsmith/ui_language/<code>.json by translating with 'translator' (e.g. backend.Local)
    the program's texts that are missing; those already in the file (also fixed by hand) stay. Returns how many it
    added."""
    from .backend import BackendError
    from .language import obvious_language
    from .text import readable
    path = os.path.join(FOLDER, code + ".json")
    try:
        with open(path, encoding="utf-8") as f:
            data = legacy.load(f)
    except (OSError, ValueError):
        data = {"name": name, "flag": flag_info or {}, "texts": {}}
    texts = data.setdefault("texts", {})
    missing = [t for t in program_texts() if t not in texts]
    for t in [t for t in missing if obvious_language(t, SOURCE)]:
        texts[t] = t  # already in another language (e.g. English instructions for the local translator): stays as is
        missing.remove(t)
    if maximum:
        missing = missing[:maximum]
    log("Program texts to translate into %s: %d (already in the file: %d)" % (name, len(missing), len(texts)))
    added = 0
    for i in range(0, len(missing), 40):
        group = missing[i:i + 40]
        protected = []
        for t in group:  # placeholders (%s, %d...) become {{A}}, {{B}}: the translator leaves them alone
            markers = []

            def _substitute(m, markers=markers):
                markers.append(m.group())
                return "{{%s}}" % chr(64 + len(markers))
            protected.append((_MARKER.sub(_substitute, readable(t)), markers))
        try:
            translated_items = translator.translate_lines([p for p, _ in protected], SOURCE, code)
        except BackendError as e:
            log("  ! translation interrupted: %s" % e)
            break
        for t, (p, markers), r in zip(group, protected, translated_items):
            if not r:
                continue
            order = [ord(m) - 65 for m in re.findall(r"\{\{([A-Z])\}\}", r)]
            if any(markers[a] == markers[b] and a > b for i, a in enumerate(order) for b in order[i + 1:]
                   if a < len(markers) and b < len(markers)):
                continue  # two placeholders of the same type swapped: the values would end up in the wrong place
            for j, s in enumerate(markers):
                r = r.replace("{{%s}}" % chr(65 + j), s, 1)
            if re.search(r"\{\{[A-Z]\}\}", r) or len(_MARKER.findall(r)) != len(markers):
                continue  # placeholders lost or extra: better the English
            texts[t] = r
            added += 1
        log("  %d of %d" % (min(i + 40, len(missing)), len(missing)))
        os.makedirs(FOLDER, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
    return added
