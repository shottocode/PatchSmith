"""Adapter web-ui: traduce i testi dell'interfaccia dei giochi web (Electron / NW.js / HTML5).

I testi dei menu sono spesso scritti dentro il codice JavaScript (minificato). Modificare quel
codice e' rischioso, perche' la stessa stringa puo' fare anche da identificatore. Questo adapter
quindi NON tocca il codice del gioco:
  1. estrae staticamente le stringhe leggibili dai file .js/.html della cartella web (anche i pulsanti di una
     parola, PLAY, OK, quit, quando di sicuro si vedono: testo dell'HTML, el.textContent = "...", fillText...);
  2. dopo la traduzione genera patchsmith-traduzione.js, con il dizionario {inglese: italiano};
  3. aggiunge a index.html un solo tag <script> che, mentre il gioco gira, sostituisce i testi
     VISUALIZZATI (nodi di testo, title/placeholder/aria-label/alt, testo disegnato su canvas)
     che corrispondono esattamente a una voce del dizionario.
Una stringa estratta per errore non fa danni: viene sostituita solo se appare a schermo.
"""
import collections
import html as html_lib
import json
import os
import re
import shutil
import subprocess

from .adapter_webjson import MARCATORE
from .testo import _CJK, e_testo_leggibile

SUFFISSO_BACKUP = ".patchsmith-originale"  # deve coincidere con progetto.SUFFISSO_BACKUP

NOME_SCRIPT = "patchsmith-traduzione.js"
TAG = '<script src="%s" data-patchsmith="1"></script>' % NOME_SCRIPT
CARTELLE_ESCLUSE = {"node_modules", ".git", "__pycache__"}
MAX_DIMENSIONE = 10 * 1024 * 1024
MAX_REGEX = 400  # oltre questa lunghezza una "regex" e' quasi certamente una divisione letta male

_PAROLE_PRIMA_DI_REGEX = {"return", "typeof", "case", "do", "else", "in", "of", "void", "yield", "await",
                          "throw", "delete", "instanceof", "new"}
_PAROLE_CONDIZIONE = {"if", "while", "for", "with"}


# --- lettura delle stringhe dal JavaScript ------------------------------------------------

def _fine_stringa(codice, i):
    """codice[i] e' ' o ". Indice subito dopo la chiusura (o la fine riga)."""
    q, j, n = codice[i], i + 1, len(codice)
    while j < n and codice[j] != q and codice[j] != "\n":
        j += 2 if codice[j] == "\\" else 1
    return j + 1


def _leggi_template(codice, i):
    """codice[i] e' `. Restituisce (indice dopo la chiusura, pezzi di testo fissi)."""
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
    """codice[i] e' / all'inizio di una regex. Indice dopo i flag, o None se non sembra una regex."""
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


# Contesti in cui una stringa del JavaScript e' di sicuro un testo mostrato al giocatore (vedi letterali_js):
# assegnata a una di queste proprieta' (el.textContent = "PLAY"), argomento di una di queste funzioni
# (ctx.fillText("OK", x, y), this.add.text(10, 20, "QUIT")) o valore di una di queste chiavi ({label: "Back"}).
_PROPRIETA_UI = {"textContent", "innerText", "innerHTML", "nodeValue", "value", "placeholder", "title", "alt", "label",
                 "text", "caption", "tooltip"}
_FUNZIONI_UI = {"fillText", "strokeText", "alert", "confirm", "prompt", "createTextNode", "setText", "text", "Text",
                "BitmapText", "addText"}
_CHIAVI_UI = {"text", "label", "title", "caption", "tooltip", "placeholder", "message", "hint", "buttonText"}


def _contesto_ui(storia, parentesi):
    """True se la stringa che segue i token 'storia' (gli ultimi tre: (tipo, valore)) e' un testo dell'interfaccia."""
    ultimi = [None] * (3 - len(storia)) + list(storia)
    if ultimi[2] == ("punt", "=") and ultimi[0] == ("punt", ".") and ultimi[1] and ultimi[1][0] == "parola" \
            and ultimi[1][1] in _PROPRIETA_UI:
        return True
    if ultimi[2] == ("punt", ":") and ultimi[1] and ultimi[1][0] in ("parola", "stringa") \
            and ultimi[1][1] in _CHIAVI_UI:
        return True
    return bool(parentesi) and parentesi[-1] == "ui" and ultimi[2] in (("punt", "("), ("punt", ","))


def letterali_js(codice, contesto=False):
    """Stringhe letterali di un sorgente JavaScript (anche minificato).

    Piccolo lettore a token: distingue stringhe '..' ".." `..`, commenti ed espressioni regolari,
    cosi' le virgolette dentro una regex o un commento non confondono l'estrazione.
    Una "/" e' una regex solo dove puo' iniziare un'espressione (dopo operatori, '(', ',', parole
    come return/typeof, la ')' di if/while/for), altrimenti e' una divisione.
    contesto=True: coppie (stringa, interfaccia), con interfaccia True se la stringa e' di sicuro un testo mostrato
    (vedi _contesto_ui).
    """
    i, n = 0, len(codice)
    puo_regex = True  # True se in questo punto puo' iniziare un'espressione
    ultimo = ""  # ultimo carattere significativo
    parola_prima = None  # ultima parola letta, se e' l'ultimo token
    parentesi = []  # per ogni '(' aperta: "condizione" (if/while/for/with), "ui" (vedi _FUNZIONI_UI) o None
    storia = collections.deque(maxlen=3)  # gli ultimi tre token: (tipo, valore)
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
            i += 2  # ++/--: dopo un valore (postfisso) resta una divisione, altrimenti nulla cambia
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
                puo_regex = True  # operatori, '.', ',', ';', ':', '{', '}', ...
            i, ultimo, parola_prima = i + 1, c, None
            storia.append(("punt", c))


# --- filtri sul testo -------------------------------------------------------------------------

_ESCAPE = re.compile(r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)", re.S)
_SEMPLICI = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_CODICE = re.compile(
    r"=>|&&|\|\||===|!==|\bthis\.|[{}]|;\S|</|/>|\d+px\b|\bvar\(--|^[.#:@\[]|https?:|^NG0\d"
    r"|\\|<[a-zA-Z]|=\"|^[),=]|[(,=]$"
    r"|\bfunction\s*[\w$]*\s*\(|\bnew [A-Z][\w$]*\(|\breturn\s*[;)}]"
)
# Solo i nomi di tasti che non sono mai parole d'interfaccia. Parole come Delete, Enter, Space,
# Home restano traducibili: quando compaiono come TASTO (dentro <kbd> o un elemento con classe
# "key") e' l'iniettore a lasciarle in originale, in base al contesto.
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
    # \uD83C\uDF89 -> un solo carattere (emoji); surrogati spaiati -> U+FFFD (poi scartato)
    return testo.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def normalizza(s):
    return re.sub(r"\s+", " ", s.strip())


def e_testo_ui(s):
    """Stringa che sembra testo per il giocatore e non codice, CSS, nomi tecnici o errori."""
    t = normalizza(s)
    if not e_testo_leggibile(t) or len(t) > 400 or "\ufffd" in t:
        return False
    if _CODICE.search(t):
        return False
    basso = t.lower()
    if basso in _TASTI or basso in _FONT or _LINGUA.match(t) or _INTESTAZIONE.match(t):
        return False
    lettere = sum(c.isalpha() for c in t)
    # in giapponese/cinese/coreano ogni carattere e' una sillaba o una parola: soglia non applicabile
    if lettere < len(t) * 0.5 and not _CJK.search(t):
        return False
    parole = t.split()
    if len(parole) == 1 and re.search(r"[A-Z]{2,}[a-z]", t):  # nomi di classi: SVGElement, IDBIndex
        return False
    if len(parole) > 1 and _CAMEL.search(t):  # identificatori in mezzo alle parole
        return False
    # classi CSS, selettori, liste di eventi: tutte minuscole con - _ : . /
    if all(p.islower() or not any(c.isalpha() for c in p) for p in parole) and any(
        re.search(r"[-_:./]", p) for p in parole
    ):
        return False
    return True


# una parola sola di almeno 2 lettere (anche con apostrofo o trattino: Co-op), con un segno finale (Quit?)
_PAROLA_UI = re.compile(r"^[^\W\d_]{2,}(?:['\u2019\-][^\W\d_]+)*[!?.:\u2026]?$")


def e_testo_mostrato(s):
    """Come e_testo_ui, per i testi che il gioco di sicuro mostra (testo e attributi visibili dell'HTML, stringhe del
    JavaScript nei contesti dell'interfaccia: vedi _contesto_ui): vale anche una parola sola di almeno 2 lettere, in
    qualsiasi maiuscolo (PLAY, OK, quit), che per i dati JSON sarebbe troppo spesso un identificatore. Restano
    fuori codice, CSS, camelCase, snake_case, numeri, nomi di tasti e di font. Una parola presa per sbaglio non fa
    danni: si sostituisce solo se appare a schermo."""
    t = normalizza(s)
    if e_testo_ui(t):
        return True
    if len(t) > 40 or "�" in t or not _PAROLA_UI.match(t) or re.search(r"[a-z][A-Z]", t):
        return False
    basso = t.lower()
    return basso not in _TASTI and basso not in _FONT and not _LINGUA.match(t)


# --- estrazione -------------------------------------------------------------------------------

# Anche 1-2 caratteri: in giapponese/cinese/coreano molte voci di menu sono cosi' (設定, はい, 시작).
# In alfabeto latino vale una parola sola di almeno 2 lettere (OK, PLAY: vedi e_testo_mostrato).
_HTML_TESTO = re.compile(r">([^<>]{1,400})<")
_HTML_ATTR = re.compile(r"""\b(?:title|alt|placeholder|aria-label)\s*=\s*(?:"([^"]{1,400})"|'([^']{1,400})')""", re.I)


def decodifica_html(dati):
    """(testo, codifica) di una pagina: UTF-8 (con o senza BOM), charset dichiarato o latin-1."""
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
    return dati.decode("latin-1"), ("latin-1", False)  # latin-1 rilegge e riscrive qualsiasi byte


def codifica_html(testo, codifica):
    nome, bom = codifica
    dati = testo.encode(nome)
    return (b"\xef\xbb\xbf" + dati) if bom else dati


def _file_web(radice):
    for cartella, dirs, files in os.walk(radice):
        dirs[:] = sorted(d for d in dirs if d.lower() not in CARTELLE_ESCLUSE)
        for f in sorted(files):
            if MARCATORE in f:  # file creati da noi (backup, info, script)
                continue
            if re.search(r"polyfill|vendor|zone\.js", f, re.I):  # librerie di terzi: niente testi di gioco
                continue
            if f.lower().endswith((".js", ".mjs", ".html", ".htm")):
                yield os.path.join(cartella, f)


def estrai_testi(radice, log=None):
    """Elenco ordinato e senza doppioni dei testi d'interfaccia trovati nei .js/.html della radice. Con 'log': i file
    saltati perche' troppo grandi (oltre MAX_DIMENSIONE)."""
    trovati, grandi = {}, []
    for percorso in _file_web(radice):
        try:
            dimensione = os.path.getsize(percorso)
            if dimensione > MAX_DIMENSIONE:
                grandi.append("%s (%d MB)" % (os.path.relpath(percorso, radice).replace(os.sep, "/"),
                                              dimensione // (1024 * 1024)))
                continue
            if percorso.lower().endswith((".html", ".htm")):
                # l'html va letto dall'originale: il nostro tag non deve finire tra i testi
                originale = percorso + SUFFISSO_BACKUP
                with open(originale if os.path.exists(originale) else percorso, "rb") as f:
                    contenuto, _ = decodifica_html(f.read())
                contenuto = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", contenuto, flags=re.S | re.I)
                contenuto = re.sub(r"<!--.*?-->", " ", contenuto, flags=re.S)
                # testo e attributi visibili: anche le parole sole (PLAY, OK)
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


# --- scrittura ------------------------------------------------------------------------------

def genera_script(dizionario):
    modello_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "iniettore.js")
    with open(modello_path, encoding="utf-8") as f:
        modello = f.read()
    voci = {normalizza(k): v for k, v in dizionario.items() if v and normalizza(k) != v}
    # ensure_ascii: il dizionario diventa un letterale JavaScript sicuro (niente U+2028 grezzi)
    return modello.replace("/*__DIZIONARIO__*/{}", json.dumps(voci, ensure_ascii=True, sort_keys=True))


def _maschera(html):
    """Copia della pagina con commenti e contenuto di script/style coperti da spazi (stesse posizioni)."""
    def _spazi(m):
        return " " * len(m.group(0))

    html = re.sub(r"<!--.*?-->", _spazi, html, flags=re.S)
    return re.sub(r"(<(script|style)\b[^>]*>).*?(</\2\s*>)",
                  lambda m: m.group(1) + " " * (len(m.group(0)) - len(m.group(1)) - len(m.group(3))) + m.group(3),
                  html, flags=re.S | re.I)


def inserisci_tag(html):
    """Aggiunge il tag subito dopo <head> (prima di <base> e degli altri script).

    Non lo mette mai dentro un commento ne' prima di <!DOCTYPE> (la pagina finirebbe in quirks mode).
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
    """Controllo di sintassi con Node.js, se installato. None se il controllo non e' possibile."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        r = subprocess.run([node, "--check", percorso], capture_output=True, timeout=60)  # conta solo l'esito
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0
