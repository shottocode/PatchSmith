"""web-ui adapter: translates the interface texts of web games (Electron / NW.js / HTML5).

Menu texts are often written inside the (minified) JavaScript code. Changing that code is risky,
because the same string can also act as an identifier. So this adapter does NOT touch the game's
code:
  1. it statically extracts the readable strings from the .js/.html files of the web folder (one-word buttons
     too, PLAY, OK, quit, when they are certainly visible: HTML text, el.textContent = "...", fillText...);
  2. after the translation it generates patchsmith-translation.js, with the dictionary {English: Italian};
  3. it adds to index.html a single <script> tag that, while the game runs, replaces the DISPLAYED
     texts (text nodes, title/placeholder/aria-label/alt, text drawn on canvas) that exactly
     match a dictionary entry.
A string extracted by mistake does no harm: it is replaced only if it appears on screen.
"""
import collections
import html as html_lib
import json
import os
import re
import shutil
import subprocess

from .adapter_webjson import MARKER
from .text import _CJK, is_readable_text

BACKUP_SUFFIX = ".patchsmith-originale"  # must match project.BACKUP_SUFFIX

SCRIPT_NAME = "patchsmith-traduzione.js"
TAG = '<script src="%s" data-patchsmith="1"></script>' % SCRIPT_NAME
EXCLUDED_FOLDERS = {"node_modules", ".git", "__pycache__"}
MAX_SIZE = 10 * 1024 * 1024
MAX_REGEX = 400  # beyond this length a "regex" is almost certainly a misread division

_WORDS_BEFORE_REGEX = {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await",
                          "throw", "delete", "instanceof", "new"}
_CONDITION_WORDS = {"if", "while", "for", "with"}


# --- reading the strings from the JavaScript ----------------------------------------------

def _string_end(code, i):
    """codice[i] is ' or ". Index right after the closing quote (or the end of the line)."""
    q, j, n = code[i], i + 1, len(code)
    while j < n and code[j] != q and code[j] != "\n":
        j += 2 if code[j] == "\\" else 1
    return j + 1


def _read_template(code, i):
    """codice[i] is `. Returns (index after the closing backtick, fixed text pieces)."""
    n, j, pieces, piece = len(code), i + 1, [], []
    while j < n:
        d = code[j]
        if d == "\\":
            piece.append(code[j:j + 2])
            j += 2
        elif d == "`":
            j += 1
            break
        elif d == "$" and j + 1 < n and code[j + 1] == "{":
            if piece:
                pieces.append("".join(piece))
                piece = []
            j, depth = j + 2, 1
            while j < n and depth:
                e = code[j]
                if e == "{":
                    depth += 1
                    j += 1
                elif e == "}":
                    depth -= 1
                    j += 1
                elif e in "\"'":
                    j = _string_end(code, j)
                elif e == "`":
                    j, inner_parts = _read_template(code, j)
                    pieces.extend(inner_parts)
                else:
                    j += 1
        else:
            piece.append(d)
            j += 1
    if piece:
        pieces.append("".join(piece))
    return j, pieces


def _regex_end(code, i):
    """codice[i] is / at the start of a regex. Index after the flags, or None if it does not look like a regex."""
    j, n, class_ = i + 1, len(code), False
    limit = min(n, i + MAX_REGEX)
    while j < limit:
        c = code[j]
        if c == "\n":
            return None
        if c == "\\":
            j += 2
            continue
        if c == "[":
            class_ = True
        elif c == "]":
            class_ = False
        elif c == "/" and not class_:
            j += 1
            while j < n and code[j].isalpha():
                j += 1
            return j
        j += 1
    return None


# Contexts in which a JavaScript string is certainly a text shown to the player (see js_literals):
# assigned to one of these properties (el.textContent = "PLAY"), argument of one of these functions
# (ctx.fillText("OK", x, y), this.add.text(10, 20, "QUIT")) or value of one of these keys ({label: "Back"}).
_UI_PROPERTIES = {"textContent", "innerText", "innerHTML", "nodeValue", "value", "placeholder", "title", "alt", "label",
                 "text", "caption", "tooltip"}
_UI_FUNCTIONS = {"fillText", "strokeText", "alert", "confirm", "prompt", "createTextNode", "setText", "text", "Text",
                "BitmapText", "addText"}
_UI_KEYS = {"text", "label", "title", "caption", "tooltip", "placeholder", "message", "hint", "buttonText"}


def _ui_context(history, brackets):
    """True if the string that follows the tokens 'storia' (the last three: (tipo, valore)) is an interface text."""
    recent = [None] * (3 - len(history)) + list(history)
    if recent[2] == ("punt", "=") and recent[0] == ("punt", ".") and recent[1] and recent[1][0] == "parola" \
            and recent[1][1] in _UI_PROPERTIES:
        return True
    if recent[2] == ("punt", ":") and recent[1] and recent[1][0] in ("parola", "stringa") \
            and recent[1][1] in _UI_KEYS:
        return True
    return bool(brackets) and brackets[-1] == "ui" and recent[2] in (("punt", "("), ("punt", ","))


def js_literals(code, context=False):
    """String literals of a JavaScript source (minified too).

    Small token reader: it tells apart strings '..' ".." `..`, comments and regular expressions,
    so quotes inside a regex or a comment do not confuse the extraction.
    A "/" is a regex only where an expression can start (after operators, '(', ',', words
    like return/typeof, the ')' of if/while/for), otherwise it is a division.
    contesto=True: pairs (string, interface), with interface True if the string is certainly a shown text
    (see _ui_context).
    """
    i, n = 0, len(code)
    regex_allowed = True  # True if an expression can start at this point
    last = ""  # last significant character
    word_before = None  # last word read, if it is the last token
    brackets = []  # for each open '(': "condizione" (if/while/for/with), "ui" (see _UI_FUNCTIONS) or None
    history = collections.deque(maxlen=3)  # the last three tokens: (tipo, valore)
    while i < n:
        c = code[i]
        if c.isspace():
            i += 1
        elif c in "\"'":
            j = _string_end(code, i)
            s = code[i + 1:j - 1]
            yield (s, _ui_context(history, brackets)) if context else s
            history.append(("stringa", s))
            i, regex_allowed, last, word_before = j, False, c, None
        elif c == "`":
            j, pieces = _read_template(code, i)
            if context:
                ui = _ui_context(history, brackets)
                yield from ((p, ui) for p in pieces)
            else:
                yield from pieces
            history.append(("stringa", None))
            i, regex_allowed, last, word_before = j, False, c, None
        elif c == "/":
            after = code[i + 1] if i + 1 < n else ""
            if after == "/":
                j = code.find("\n", i)
                i = n if j < 0 else j
                continue
            if after == "*":
                j = code.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            end = _regex_end(code, i) if regex_allowed else None
            if end:
                i, regex_allowed = end, False
            else:
                i, regex_allowed = i + 1, True
            last, word_before = "/", None
            history.append(("punt", "/"))
        elif c.isalpha() or c in "_$" or ord(c) > 127:
            j = i + 1
            while j < n and (code[j].isalnum() or code[j] in "_$" or ord(code[j]) > 127):
                j += 1
            word = code[i:j]
            regex_allowed = word in _WORDS_BEFORE_REGEX and last != "."
            i, last, word_before = j, "a", word
            history.append(("parola", word))
        elif c.isdigit():
            j = i + 1
            while j < n and (code[j].isalnum() or code[j] in "._"):
                j += 1
            i, regex_allowed, last, word_before = j, False, "0", None
            history.append(("numero", None))
        elif c in "+-" and i + 1 < n and code[i + 1] == c:
            i += 2  # ++/--: after a value (postfix) a division follows, otherwise nothing changes
            last, word_before = c, None
            history.append(("punt", c + c))
        else:
            if c == "(":
                brackets.append("condizione" if word_before in _CONDITION_WORDS and last == "a" else
                                 "ui" if word_before in _UI_FUNCTIONS and last == "a" else None)
                regex_allowed = True
            elif c == ")":
                regex_allowed = (brackets.pop() == "condizione") if brackets else False
            elif c == "]":
                regex_allowed = False
            else:
                regex_allowed = True  # operators, '.', ',', ';', ':', '{', '}', ...
            i, last, word_before = i + 1, c, None
            history.append(("punt", c))


# --- text filters -----------------------------------------------------------------------------

_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_CODE = re.compile(
    r"=>|&&|\|\||===|!==|\bthis\.|[{}]|;\S|</|/>|\d+px\b|\bvar\(--|^[.#:@\[]|https?:|^NG0\d"
    r"|\\|<[a-zA-Z]|=\"|^[),=]|[(,=]$"
    r"|\bfunction\s*[\w$]*\s*\(|\bnew [A-Z][\w$]*\(|\breturn\s*[;)}]"
)
# Only the key names that are never interface words. Words like Delete, Enter, Space,
# Home stay translatable: when they appear as a KEY (inside <kbd> or an element with class
# "key") the injector leaves them untranslated, based on the context.
_KEY_NAMES = {
    "escape", "esc", "shift", "ctrl", "alt", "altgr", "meta", "backspace", "pageup", "pagedown",
    "arrowup", "arrowdown", "arrowleft", "arrowright", "capslock", "numlock", "printscreen",
    "contextmenu", "spacebar", "cmd", "fn",
} | {"f%d" % k for k in range(1, 25)}
_FONT = {
    "segoe ui", "roboto", "roboto mono", "arial", "helvetica", "helvetica neue", "times new roman",
    "courier new", "consolas", "georgia", "verdana", "tahoma", "trebuchet ms", "sans-serif", "serif",
    "monospace", "system-ui", "open sans", "noto sans", "lucida console", "menlo", "monaco",
    "comic sans ms", "impact", "ui-monospace", "sf mono", "cascadia code", "fira code", "inter",
    "segoe ui emoji", "apple color emoji", "noto color emoji",
}
_LANGUAGE = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})+$")  # en-US, zh-Hans-CN
_HEADER = re.compile(r"^(?:X-[\w-]+|[A-Z][a-z]+(?:-[A-Z][a-z]+)+)$")  # Content-Type, X-Request-URL
_CAMEL = re.compile(r"(?:^|\s)[a-z]+[A-Z]\w*")  # xMidYMid meet, onClick handler


def _decode(s):
    def _sub(m):
        e = m.group(1)
        if e.startswith("u{"):
            return chr(int(e[2:-1], 16))
        if e[0] in "ux" and len(e) > 1:
            return chr(int(e[1:], 16))
        return _SIMPLE_ESCAPES.get(e, e)

    text = _ESCAPE.sub(_sub, s)
    # \uD83C\uDF89 -> a single character (emoji); unpaired surrogates -> U+FFFD (rejected later)
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def normalize(s):
    return re.sub(r"\s+", " ", s.strip())


def is_ui_text(s):
    """String that looks like text for the player and not code, CSS, technical names or errors."""
    t = normalize(s)
    if not is_readable_text(t) or len(t) > 400 or "\ufffd" in t:
        return False
    if _CODE.search(t):
        return False
    low = t.lower()
    if low in _KEY_NAMES or low in _FONT or _LANGUAGE.match(t) or _HEADER.match(t):
        return False
    letters = sum(c.isalpha() for c in t)
    # in Japanese/Chinese/Korean each character is a syllable or a word: threshold not applicable
    if letters < len(t) * 0.5 and not _CJK.search(t):
        return False
    words = t.split()
    if len(words) == 1 and re.search(r"[A-Z]{2,}[a-z]", t):  # class names: SVGElement, IDBIndex
        return False
    if len(words) > 1 and _CAMEL.search(t):  # identifiers among the words
        return False
    # CSS classes, selectors, event lists: all lowercase with - _ : . /
    if all(p.islower() or not any(c.isalpha() for c in p) for p in words) and any(
        re.search(r"[-_:./]", p) for p in words
    ):
        return False
    return True


# a single word of at least 2 letters (also with an apostrophe or hyphen: Co-op), with a final mark (Quit?)
_UI_WORD = re.compile(r"^[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*[!?.:\u2026]?$")


def is_shown_text(s):
    """Like is_ui_text, for the texts the game certainly shows (visible HTML text and attributes, JavaScript strings
    in interface contexts: see _ui_context): a single word of at least 2 letters also counts, in any case (PLAY, OK,
    quit), which in JSON data would too often be an identifier. Code, CSS, camelCase, snake_case, numbers, key names
    and font names stay out. A word picked by mistake does no harm: it is replaced only if it appears on screen."""
    t = normalize(s)
    if is_ui_text(t):
        return True
    if len(t) > 40 or "�" in t or not _UI_WORD.match(t) or re.search(r"[a-z][A-Z]", t):
        return False
    low = t.lower()
    return low not in _KEY_NAMES and low not in _FONT and not _LANGUAGE.match(t)


# --- extraction -------------------------------------------------------------------------------

# Even 1-2 characters: in Japanese/Chinese/Korean many menu entries are like that (設定, はい, 시작).
# In the Latin alphabet a single word of at least 2 letters counts (OK, PLAY: see is_shown_text).
_HTML_TEXT = re.compile(r">([^<>]{1,400})<")
_HTML_ATTR = re.compile(r"""\b(?:title|alt|placeholder|aria-label)\s*=\s*(?:"([^"]{1,400})"|'([^']{1,400})')""", re.I)


def decode_html(data):
    """(text, encoding) of a page: UTF-8 (with or without BOM), declared charset or latin-1."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8"), ("utf-8", True)
    try:
        return data.decode("utf-8"), ("utf-8", False)
    except UnicodeDecodeError:
        pass
    m = re.search(rb"""charset\s*=\s*["']?([\w-]+)""", data[:4096], re.I)
    if m:
        try:
            name = m.group(1).decode("ascii")
            return data.decode(name), (name, False)
        except (LookupError, UnicodeDecodeError):
            pass
    return data.decode("latin-1"), ("latin-1", False)  # latin-1 reads and writes back any byte


def encode_html(text, encoding):
    name, bom = encoding
    data = text.encode(name)
    return (b"\xef\xbb\xbf" + data) if bom else data


def _web_files(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d.lower() not in EXCLUDED_FOLDERS)
        for f in sorted(files):
            if MARKER in f:  # files created by us (backup, info, script)
                continue
            if re.search(r"polyfill|vendor|zone\.js", f, re.I):  # third-party libraries: no game texts
                continue
            if f.lower().endswith((".js", ".mjs", ".html", ".htm")):
                yield os.path.join(folder, f)


def extract_texts(root, log=None):
    """Ordered list, without duplicates, of the interface texts found in the .js/.html files of the root. With 'log':
    the files skipped because they are too large (over MAX_SIZE)."""
    found, large = {}, []
    for path in _web_files(root):
        try:
            size = os.path.getsize(path)
            if size > MAX_SIZE:
                large.append("%s (%d MB)" % (os.path.relpath(path, root).replace(os.sep, "/"),
                                              size // (1024 * 1024)))
                continue
            if path.lower().endswith((".html", ".htm")):
                # the html must be read from the original: our tag must not end up among the texts
                original = path + BACKUP_SUFFIX
                with open(original if os.path.exists(original) else path, "rb") as f:
                    content, _ = decode_html(f.read())
                content = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", content, flags=re.S | re.I)
                content = re.sub(r"<!--.*?-->", " ", content, flags=re.S)
                # visible text and attributes: single words too (PLAY, OK)
                raw_texts = [(html_lib.unescape(g), True) for g in _HTML_TEXT.findall(content)]
                raw_texts += [(html_lib.unescape(a or b), True) for a, b in _HTML_ATTR.findall(content)]
            else:
                with open(path, encoding="utf-8", errors="replace") as f:
                    content = f.read()
                raw_texts = [(_decode(s), ui) for s, ui in js_literals(content, context=True)
                          if 1 <= len(s) <= 600]
        except OSError:
            continue
        for g, is_shown in raw_texts:
            if is_shown_text(g) if is_shown else is_ui_text(g):
                found.setdefault(normalize(g), None)
    if log and large:
        log("  ! %d file del gioco web troppo grandi, non letti per i menu (oltre %d MB): %s" % (
            len(large), MAX_SIZE // (1024 * 1024), ", ".join(large[:3])))
    return list(found)


# --- writing --------------------------------------------------------------------------------

def generate_script(dictionary):
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "injector.js")
    with open(template_path, encoding="utf-8") as f:
        model = f.read()
    entries = {normalize(k): v for k, v in dictionary.items() if v and normalize(k) != v}
    # ensure_ascii: the dictionary becomes a safe JavaScript literal (no raw U+2028)
    return model.replace("/*__DIZIONARIO__*/{}", json.dumps(entries, ensure_ascii=True, sort_keys=True))


def _mask(html):
    """Copy of the page with comments and script/style content covered by spaces (same positions)."""
    def _spaces(m):
        return " " * len(m.group(0))

    html = re.sub(r"<!--.*?-->", _spaces, html, flags=re.S)
    return re.sub(r"(<(script|style)\b[^>]*>).*?(</\2\s*>)",
                  lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1)) - len(m.group(3))) + m.group(3),
                  html, flags=re.S | re.I)


def insert_tag(html):
    """Adds the tag right after <head> (before <base> and the other scripts).

    It never puts it inside a comment or before <!DOCTYPE> (the page would end up in quirks mode).
    """
    if SCRIPT_NAME in html:
        return html
    masked = _mask(html)
    for model in (r"<head\b[^>]*>", r"<html\b[^>]*>", r"<!doctype\b[^>]*>"):
        m = re.search(model, masked, re.I)
        if m:
            return html[:m.end()] + "\n" + TAG + html[m.end():]
    m = re.match(r"(?:\s|<!--.*?-->)*", html, re.S)
    return html[:m.end()] + TAG + "\n" + html[m.end():]


def check_script(path):
    """Syntax check with Node.js, if installed. None if the check is not possible."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        r = subprocess.run([node, "--check", path], capture_output=True, timeout=60)  # only the outcome counts
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0
