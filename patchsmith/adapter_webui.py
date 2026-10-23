"""web-ui adapter: translates the interface texts of web games (Electron / NW.js / HTML5).

Menu texts are often written inside the (minified) JavaScript code. Changing that code is risky,
because the same string can also act as an identifier. So this adapter does NOT touch the game's
code:
  1. it statically extracts the readable strings from the .js/.html files of the web folder (one-word buttons
     too, PLAY, OK, quit, when they are certainly visible: HTML text, el.textContent = "...", fillText...);
  2. after the translation it generates patchsmith-traduzione.js, with the dictionary {English: Italian};
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

from .adapter_webjson import MARCATORE
from .text import _CJK, e_testo_leggibile

SUFFISSO_BACKUP = ".patchsmith-originale"  # must match progetto.SUFFISSO_BACKUP

NOME_SCRIPT = "patchsmith-traduzione.js"
TAG = '<script src="%s" data-patchsmith="1"></script>' % NOME_SCRIPT
CARTELLE_ESCLUSE = {"node_modules", ".git", "__pycache__"}
MAX_DIMENSIONE = 10 * 1024 * 1024
MAX_REGEX = 400  # beyond this length a "regex" is almost certainly a misread division

_PAROLE_PRIMA_DI_REGEX = {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await",
                          "throw", "delete", "instanceof", "new"}
_PAROLE_CONDIZIONE = {"if", "while", "for", "with"}


# --- reading the strings from the JavaScript ----------------------------------------------

def _fine_stringa(codice, i):
    """codice[i] is ' or ". Index right after the closing quote (or the end of the line)."""
    q, j, n = codice[i], i + 1, len(codice)
    while j < n and codice[j] != q and codice[j] != "\n":
        j += 2 if codice[j] == "\\" else 1
    return j + 1


def _leggi_template(codice, i):
    """codice[i] is `. Returns (index after the closing backtick, fixed text pieces)."""
    n, j, pezzi, pezzo = len(codice), i + 1, [], []
    while j < n:
        d = codice[j]
        if d == "\\":
            pezzo.append(codice[j:j + 2])
            j += 2
        elif d == "`":
            j += 1
            break
        elif d == "$" and j + 1 < n and codice[j + 1] == "{":
            if pezzo:
                pezzi.append("".join(pezzo))
                pezzo = []
            j, profondita = j + 2, 1
            while j < n and profondita:
                e = codice[j]
                if e == "{":
                    profondita += 1
                    j += 1
                elif e == "}":
                    profondita -= 1
                    j += 1
                elif e in "\"'":
                    j = _fine_stringa(codice, j)
                elif e == "`":
                    j, interni = _leggi_template(codice, j)
                    pezzi.extend(interni)
                else:
                    j += 1
        else:
            pezzo.append(d)
            j += 1
    if pezzo:
        pezzi.append("".join(pezzo))
    return j, pezzi


def _fine_regex(codice, i):
    """codice[i] is / at the start of a regex. Index after the flags, or None if it does not look like a regex."""
    j, n, classe = i + 1, len(codice), False
    limite = min(n, i + MAX_REGEX)
    while j < limite:
        c = codice[j]
        if c == "\n":
            return None
        if c == "\\":
            j += 2
            continue
        if c == "[":
            classe = True
        elif c == "]":
            classe = False
        elif c == "/" and not classe:
            j += 1
            while j < n and codice[j].isalpha():
                j += 1
            return j
        j += 1
    return None


# Contexts in which a JavaScript string is certainly a text shown to the player (see letterali_js):
# assigned to one of these properties (el.textContent = "PLAY"), argument of one of these functions
# (ctx.fillText("OK", x, y), this.add.text(10, 20, "QUIT")) or value of one of these keys ({label: "Back"}).
_PROPRIETA_UI = {"textContent", "innerText", "innerHTML", "nodeValue", "value", "placeholder", "title", "alt", "label",
                 "text", "caption", "tooltip"}
_FUNZIONI_UI = {"fillText", "strokeText", "alert", "confirm", "prompt", "createTextNode", "setText", "text", "Text",
                "BitmapText", "addText"}
_CHIAVI_UI = {"text", "label", "title", "caption", "tooltip", "placeholder", "message", "hint", "buttonText"}


def _contesto_ui(storia, parentesi):
    """True if the string that follows the tokens 'storia' (the last three: (tipo, valore)) is an interface text."""
    ultimi = [None] * (3 - len(storia)) + list(storia)
    if ultimi[2] == ("punt", "=") and ultimi[0] == ("punt", ".") and ultimi[1] and ultimi[1][0] == "parola" \
            and ultimi[1][1] in _PROPRIETA_UI:
        return True
    if ultimi[2] == ("punt", ":") and ultimi[1] and ultimi[1][0] in ("parola", "stringa") \
            and ultimi[1][1] in _CHIAVI_UI:
        return True
    return bool(parentesi) and parentesi[-1] == "ui" and ultimi[2] in (("punt", "("), ("punt", ","))


def letterali_js(codice, contesto=False):
    """String literals of a JavaScript source (minified too).

    Small token reader: it tells apart strings '..' ".." `..`, comments and regular expressions,
    so quotes inside a regex or a comment do not confuse the extraction.
    A "/" is a regex only where an expression can start (after operators, '(', ',', words
    like return/typeof, the ')' of if/while/for), otherwise it is a division.
    contesto=True: pairs (string, interface), with interface True if the string is certainly a shown text
    (see _contesto_ui).
    """
    i, n = 0, len(codice)
    puo_regex = True  # True if an expression can start at this point
    ultimo = ""  # last significant character
    parola_prima = None  # last word read, if it is the last token
    parentesi = []  # for each open '(': "condizione" (if/while/for/with), "ui" (see _FUNZIONI_UI) or None
    storia = collections.deque(maxlen=3)  # the last three tokens: (tipo, valore)
    while i < n:
        c = codice[i]
        if c.isspace():
            i += 1
        elif c in "\"'":
            j = _fine_stringa(codice, i)
            s = codice[i + 1:j - 1]
            yield (s, _contesto_ui(storia, parentesi)) if contesto else s
            storia.append(("stringa", s))
            i, puo_regex, ultimo, parola_prima = j, False, c, None
        elif c == "`":
            j, pezzi = _leggi_template(codice, i)
            if contesto:
                ui = _contesto_ui(storia, parentesi)
                yield from ((p, ui) for p in pezzi)
            else:
                yield from pezzi
            storia.append(("stringa", None))
            i, puo_regex, ultimo, parola_prima = j, False, c, None
        elif c == "/":
            dopo = codice[i + 1] if i + 1 < n else ""
            if dopo == "/":
                j = codice.find("\n", i)
                i = n if j < 0 else j
                continue
            if dopo == "*":
                j = codice.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            fine = _fine_regex(codice, i) if puo_regex else None
            if fine:
                i, puo_regex = fine, False
            else:
                i, puo_regex = i + 1, True
            ultimo, parola_prima = "/", None
            storia.append(("punt", "/"))
        elif c.isalpha() or c in "_$" or ord(c) > 127:
            j = i + 1
            while j < n and (codice[j].isalnum() or codice[j] in "_$" or ord(codice[j]) > 127):
                j += 1
            parola = codice[i:j]
            puo_regex = parola in _PAROLE_PRIMA_DI_REGEX and ultimo != "."
            i, ultimo, parola_prima = j, "a", parola
            storia.append(("parola", parola))
        elif c.isdigit():
            j = i + 1
            while j < n and (codice[j].isalnum() or codice[j] in "._"):
                j += 1
            i, puo_regex, ultimo, parola_prima = j, False, "0", None
            storia.append(("numero", None))
        elif c in "+-" and i + 1 < n and codice[i + 1] == c:
            i += 2  # ++/--: after a value (postfix) a division follows, otherwise nothing changes
            ultimo, parola_prima = c, None
            storia.append(("punt", c + c))
        else:
            if c == "(":
                parentesi.append("condizione" if parola_prima in _PAROLE_CONDIZIONE and ultimo == "a" else
                                 "ui" if parola_prima in _FUNZIONI_UI and ultimo == "a" else None)
                puo_regex = True
            elif c == ")":
                puo_regex = (parentesi.pop() == "condizione") if parentesi else False
            elif c == "]":
                puo_regex = False
            else:
                puo_regex = True  # operators, '.', ',', ';', ':', '{', '}', ...
            i, ultimo, parola_prima = i + 1, c, None
            storia.append(("punt", c))


# --- text filters -----------------------------------------------------------------------------

_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)
_SEMPLICI = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_CODICE = re.compile(
    r"=>|&&|\|\||===|!==|\bthis\.|[{}]|;\S|</|/>|\d+px\b|\bvar\(--|^[.#:@\[]|https?:|^NG0\d"
    r"|\\|<[a-zA-Z]|=\"|^[),=]|[(,=]$"
    r"|\bfunction\s*[\w$]*\s*\(|\bnew [A-Z][\w$]*\(|\breturn\s*[;)}]"
)
# Only the key names that are never interface words. Words like Delete, Enter, Space,
# Home stay translatable: when they appear as a KEY (inside <kbd> or an element with class
# "key") the injector leaves them untranslated, based on the context.
_TASTI = {
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
_LINGUA = re.compile(r"^[a-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})+$")  # en-US, zh-Hans-CN
_INTESTAZIONE = re.compile(r"^(?:X-[\w-]+|[A-Z][a-z]+(?:-[A-Z][a-z]+)+)$")  # Content-Type, X-Request-URL
_CAMEL = re.compile(r"(?:^|\s)[a-z]+[A-Z]\w*")  # xMidYMid meet, onClick handler


def _decodifica(s):
    def _sub(m):
        e = m.group(1)
        if e.startswith("u{"):
            return chr(int(e[2:-1], 16))
        if e[0] in "ux" and len(e) > 1:
            return chr(int(e[1:], 16))
        return _SEMPLICI.get(e, e)

    testo = _ESCAPE.sub(_sub, s)
    # \uD83C\uDF89 -> a single character (emoji); unpaired surrogates -> U+FFFD (rejected later)
    return testo.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def normalizza(s):
    return re.sub(r"\s+", " ", s.strip())


def e_testo_ui(s):
    """String that looks like text for the player and not code, CSS, technical names or errors."""
    t = normalizza(s)
    if not e_testo_leggibile(t) or len(t) > 400 or "\ufffd" in t:
        return False
    if _CODICE.search(t):
        return False
    basso = t.lower()
    if basso in _TASTI or basso in _FONT or _LINGUA.match(t) or _INTESTAZIONE.match(t):
        return False
    lettere = sum(c.isalpha() for c in t)
    # in Japanese/Chinese/Korean each character is a syllable or a word: threshold not applicable
    if lettere < len(t) * 0.5 and not _CJK.search(t):
        return False
    parole = t.split()
    if len(parole) == 1 and re.search(r"[A-Z]{2,}[a-z]", t):  # class names: SVGElement, IDBIndex
        return False
    if len(parole) > 1 and _CAMEL.search(t):  # identifiers among the words
        return False
    # CSS classes, selectors, event lists: all lowercase with - _ : . /
    if all(p.islower() or not any(c.isalpha() for c in p) for p in parole) and any(
        re.search(r"[-_:./]", p) for p in parole
    ):
        return False
    return True


# a single word of at least 2 letters (also with an apostrophe or hyphen: Co-op), with a final mark (Quit?)
_PAROLA_UI = re.compile(r"^[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*[!?.:\u2026]?$")


def e_testo_mostrato(s):
    """Like e_testo_ui, for the texts the game certainly shows (visible HTML text and attributes, JavaScript strings
    in interface contexts: see _contesto_ui): a single word of at least 2 letters also counts, in any case (PLAY, OK,
    quit), which in JSON data would too often be an identifier. Code, CSS, camelCase, snake_case, numbers, key names
    and font names stay out. A word picked by mistake does no harm: it is replaced only if it appears on screen."""
    t = normalizza(s)
    if e_testo_ui(t):
        return True
    if len(t) > 40 or "�" in t or not _PAROLA_UI.match(t) or re.search(r"[a-z][A-Z]", t):
        return False
    basso = t.lower()
    return basso not in _TASTI and basso not in _FONT and not _LINGUA.match(t)


# --- extraction -------------------------------------------------------------------------------

# Even 1-2 characters: in Japanese/Chinese/Korean many menu entries are like that (設定, はい, 시작).
# In the Latin alphabet a single word of at least 2 letters counts (OK, PLAY: see e_testo_mostrato).
_HTML_TESTO = re.compile(r">([^<>]{1,400})<")
_HTML_ATTR = re.compile(r"""\b(?:title|alt|placeholder|aria-label)\s*=\s*(?:"([^"]{1,400})"|'([^']{1,400})')""", re.I)


def decodifica_html(dati):
    """(text, encoding) of a page: UTF-8 (with or without BOM), declared charset or latin-1."""
    if dati.startswith(b"\xef\xbb\xbf"):
        return dati[3:].decode("utf-8"), ("utf-8", True)
    try:
        return dati.decode("utf-8"), ("utf-8", False)
    except UnicodeDecodeError:
        pass
    m = re.search(rb"""charset\s*=\s*["']?([\w-]+)""", dati[:4096], re.I)
    if m:
        try:
            nome = m.group(1).decode("ascii")
            return dati.decode(nome), (nome, False)
        except (LookupError, UnicodeDecodeError):
            pass
    return dati.decode("latin-1"), ("latin-1", False)  # latin-1 reads and writes back any byte


def codifica_html(testo, codifica):
    nome, bom = codifica
    dati = testo.encode(nome)
    return (b"\xef\xbb\xbf" + dati) if bom else dati


def _file_web(radice):
    for cartella, dirs, files in os.walk(radice):
        dirs[:] = sorted(d for d in dirs if d.lower() not in CARTELLE_ESCLUSE)
        for f in sorted(files):
            if MARCATORE in f:  # files created by us (backup, info, script)
                continue
            if re.search(r"polyfill|vendor|zone\.js", f, re.I):  # third-party libraries: no game texts
                continue
            if f.lower().endswith((".js", ".mjs", ".html", ".htm")):
                yield os.path.join(cartella, f)


def estrai_testi(radice, log=None):
    """Ordered list, without duplicates, of the interface texts found in the .js/.html files of the root. With 'log':
    the files skipped because they are too large (over MAX_DIMENSIONE)."""
    trovati, grandi = {}, []
    for percorso in _file_web(radice):
        try:
            dimensione = os.path.getsize(percorso)
            if dimensione > MAX_DIMENSIONE:
                grandi.append("%s (%d MB)" % (os.path.relpath(percorso, radice).replace(os.sep, "/"),
                                              dimensione // (1024 * 1024)))
                continue
            if percorso.lower().endswith((".html", ".htm")):
                # the html must be read from the original: our tag must not end up among the texts
                originale = percorso + SUFFISSO_BACKUP
                with open(originale if os.path.exists(originale) else percorso, "rb") as f:
                    contenuto, _ = decodifica_html(f.read())
                contenuto = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", contenuto, flags=re.S | re.I)
                contenuto = re.sub(r"<!--.*?-->", " ", contenuto, flags=re.S)
                # visible text and attributes: single words too (PLAY, OK)
                grezzi = [(html_lib.unescape(g), True) for g in _HTML_TESTO.findall(contenuto)]
                grezzi += [(html_lib.unescape(a or b), True) for a, b in _HTML_ATTR.findall(contenuto)]
            else:
                with open(percorso, encoding="utf-8", errors="replace") as f:
                    contenuto = f.read()
                grezzi = [(_decodifica(s), ui) for s, ui in letterali_js(contenuto, contesto=True)
                          if 1 <= len(s) <= 600]
        except OSError:
            continue
        for g, mostrato in grezzi:
            if e_testo_mostrato(g) if mostrato else e_testo_ui(g):
                trovati.setdefault(normalizza(g), None)
    if log and grandi:
        log("  ! %d file del gioco web troppo grandi, non letti per i menu (oltre %d MB): %s" % (
            len(grandi), MAX_DIMENSIONE // (1024 * 1024), ", ".join(grandi[:3])))
    return list(trovati)


# --- writing --------------------------------------------------------------------------------

def genera_script(dizionario):
    modello_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "injector.js")
    with open(modello_path, encoding="utf-8") as f:
        modello = f.read()
    voci = {normalizza(k): v for k, v in dizionario.items() if v and normalizza(k) != v}
    # ensure_ascii: the dictionary becomes a safe JavaScript literal (no raw U+2028)
    return modello.replace("/*__DIZIONARIO__*/{}", json.dumps(voci, ensure_ascii=True, sort_keys=True))


def _maschera(html):
    """Copy of the page with comments and script/style content covered by spaces (same positions)."""
    def _spazi(m):
        return " " * len(m.group(0))

    html = re.sub(r"<!--.*?-->", _spazi, html, flags=re.S)
    return re.sub(r"(<(script|style)\b[^>]*>).*?(</\2\s*>)",
                  lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1)) - len(m.group(3))) + m.group(3),
                  html, flags=re.S | re.I)


def inserisci_tag(html):
    """Adds the tag right after <head> (before <base> and the other scripts).

    It never puts it inside a comment or before <!DOCTYPE> (the page would end up in quirks mode).
    """
    if NOME_SCRIPT in html:
        return html
    mascherato = _maschera(html)
    for modello in (r"<head\b[^>]*>", r"<html\b[^>]*>", r"<!doctype\b[^>]*>"):
        m = re.search(modello, mascherato, re.I)
        if m:
            return html[:m.end()] + "\n" + TAG + html[m.end():]
    m = re.match(r"(?:\s|<!--.*?-->)*", html, re.S)
    return html[:m.end()] + TAG + "\n" + html[m.end():]


def controlla_script(percorso):
    """Syntax check with Node.js, if installed. None if the check is not possible."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        r = subprocess.run([node, "--check", percorso], capture_output=True, timeout=60)  # only the outcome counts
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0
