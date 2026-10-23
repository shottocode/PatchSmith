"""PatchSmith's interface language.

The program's texts are written in Italian in the code. The other languages are in
patchsmith/ui_language/<codice>.json:
    {"nome": "English", "bandiera": {"tipo": "gb"}, "testi": {"testo italiano": "traduzione", ...}}
Translation happens when the window shows a text (aggancia_tk: buttons, labels, titles, menus, values of drop-down
menus; TestoVar for status lines; traduci() for the log), also for texts with values inside: an entry with %s, %d...
("Testi del gioco: %d, gia' tradotti: %d") works as a template for all the lines of that form. Texts without a
translation stay in Italian. The original texts stay remembered in the widgets: when the language changes,
ritraduci() puts the ones already shown back in the new language without reopening the program.

To add a language: the command line command "interface --create fr" (translates all the texts with the local
translator; those already in the file stay) and then review the file patchsmith/ui_language/fr.json by hand. The flag
is described with stripes ({"verticali": [colori]} or {"orizzontali": [colori]}) or with a 24x16 PNG image next to
the file ({"file": "fr.png"}).
"""
import ast
import json
import os
import re
import weakref

CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui_language")
SORGENTE = "it"
ITALIANO = {"nome": "Italiano", "bandiera": {"verticali": ["#009246", "#ffffff", "#ce2b37"]}}
# placeholders of the program's texts: %s, %d, %5.1f, %r, %(nome)s, %%
_SEGNO = re.compile(r"%(?:\([^)]*\))?[-+ #0]*(?:\d+)?(?:\.\d+)?[sdrifxXceEgG%]")
# what each placeholder matches in a line already written
_VALORE = {"d": r"-?\d[\d.,']*", "i": r"-?\d[\d.,']*", "f": r"-?[\d.,]+", "x": r"[0-9a-fA-F]+", "%": r"%"}
# at the start of log lines: indentation and markers ("! ", "~ ", "* ", "- ", "== ") that stay as they are
_PREFISSO = re.compile(r"^(\s*(?:(?:!|~|\*|-|==)\s+)?)(.*?)(\s*)$", re.S)
_stato = {"codice": SORGENTE, "testi": {}, "modelli": None}
FISSO = "⁠"  # at the start of a text: it stays as it is in every language (e.g. the language names in their menu)


# --- available languages ---------------------------------------------------------------------------------------------

def lingue():
    """{codice: {"nome", "bandiera"}}: Italian (the language of the code) and the files in patchsmith/ui_language/."""
    uscita = {SORGENTE: ITALIANO}
    try:
        nomi = sorted(os.listdir(CARTELLA))
    except OSError:
        nomi = []
    for nome in nomi:
        codice, est = os.path.splitext(nome)
        if est.lower() != ".json" or codice == SORGENTE:
            continue
        try:
            with open(os.path.join(CARTELLA, nome), encoding="utf-8") as f:
                dati = json.load(f)
        except (OSError, ValueError):
            continue
        uscita[codice] = {"nome": dati.get("nome") or codice, "bandiera": dati.get("bandiera") or {}}
    return uscita


def predefinita():
    """The interface language if none was chosen: PATCHSMITH_INTERFACCIA, then the Windows one if available, then
    English if available, then Italian."""
    disponibili = lingue()
    scelta = os.environ.get("PATCHSMITH_INTERFACCIA")
    if scelta in disponibili:
        return scelta
    from .language import lingua_di_windows
    windows = (lingua_di_windows() or "").split("-")[0].lower()
    if windows in disponibili:
        return windows
    return "en" if "en" in disponibili else SORGENTE


def imposta(codice):
    """Uses the language 'codice' (if it is not available, Italian). Returns the one used."""
    testi = {}
    if codice != SORGENTE:
        try:
            with open(os.path.join(CARTELLA, codice + ".json"), encoding="utf-8") as f:
                testi = json.load(f).get("testi") or {}
        except (OSError, ValueError):
            codice = SORGENTE
    _stato.update(codice=codice, testi={k: v for k, v in testi.items() if isinstance(v, str) and v}, modelli=None)
    return codice


def attuale():
    return _stato["codice"]


# --- translation -----------------------------------------------------------------------------------------------------

def _regex_modello(chiave):
    """(regex, segni) of an entry with placeholders, or None if it has none."""
    segni = _SEGNO.findall(chiave)
    if not segni:
        return None
    parti, posizione = [], 0
    for m in _SEGNO.finditer(chiave):
        parti.append(re.escape(chiave[posizione:m.start()]))
        tipo = m.group()[-1]
        parti.append("(%s)" % _VALORE.get(tipo, r".*?") if tipo != "%" else "%")
        posizione = m.end()
    parti.append(re.escape(chiave[posizione:]))
    try:
        return re.compile("^" + "".join(parti) + "$", re.S), [s for s in segni if s != "%%"]
    except re.error:
        return None


def _modelli():
    if _stato["modelli"] is None:
        modelli = {}
        for chiave, valore in _stato["testi"].items():
            if not _SEGNO.sub("", chiave).strip():  # placeholders only ("%s", "%s%s"): it would match everything
                continue
            r = _regex_modello(chiave)
            if r is None:
                continue
            inizio = _SEGNO.split(chiave)[0][:6]
            modelli.setdefault(inizio, []).append((len(_SEGNO.sub("", chiave)), r[0], r[1], valore))
        for elenco in modelli.values():  # first the templates with more fixed text (the most precise)
            elenco.sort(key=lambda m: -m[0])
        # last fallback: two pieces separated by ": " or in parentheses, each translated on its own
        modelli[None] = [(0,) + _regex_modello(k) + (k,) for k in ("%s: %s", "%s (%s)", "%s - %s")]
        _stato["modelli"] = modelli
    return _stato["modelli"]


def _riempi(traduzione, segni, valori):
    """The translation with the values in place of the placeholders: by type and in order (the translation may have
    moved them)."""
    code = {}
    for s, v in zip(segni, valori):
        code.setdefault(s, []).append(v)
    uscita, posizione = [], 0
    for m in _SEGNO.finditer(traduzione):
        uscita.append(traduzione[posizione:m.start()])
        if m.group() == "%%":
            uscita.append("%")
        else:
            coda = code.get(m.group())
            uscita.append(coda.pop(0) if coda else m.group())
        posizione = m.end()
    uscita.append(traduzione[posizione:])
    return "".join(uscita)


def _traduci_nucleo(testo, profondita=0):
    if not testo or not _stato["testi"]:
        return testo
    t = _stato["testi"].get(testo)
    if t is not None:
        return t
    modelli = _modelli()
    # among the templates that start like the text, the one with more fixed text wins (the most precise), not the one
    # with the longest start: "%s: interfaccia, %d testi tradotti..." beats "index.html: %s"
    migliore = None
    for inizio in [testo[:n] for n in range(min(6, len(testo)), -1, -1)]:
        for fisso, regex, segni, traduzione in modelli.get(inizio, ()):
            if migliore is not None and fisso <= migliore[0]:
                break  # (sorted by fixed text: the others in the group are less precise)
            m = regex.match(testo)
            if m:
                migliore = (fisso, m, segni, traduzione)
                break
    if migliore is None and profondita == 0:  # messages made of several sentences of the file one after the other
        tradotto = _per_frasi(testo)
        if tradotto != testo:
            return tradotto
    if migliore is None and profondita <= 1:  # fallbacks ("%s: %s", "%s (%s)"), also for the pieces inside a sentence
        for fisso, regex, segni, traduzione in modelli[None]:
            m = regex.match(testo)
            if m:
                migliore = (fisso, m, segni, traduzione)
                break
    if migliore is None:
        return testo
    _fisso, m, segni, traduzione = migliore
    valori = list(m.groups())
    if profondita < 2:  # the pieces of text inside (e.g. " (387 da altri giochi)"): translated too
        valori = [_traduci_pezzo(v, profondita + 1) if s[-1] in "sr" else v for s, v in zip(segni, valori)]
    return _riempi(traduzione, segni, valori)


def _per_frasi(testo):
    """The text split into sentences ("... evitano. Una frase con..."), translated in groups: from the start, the
    longest group of consecutive sentences that is in the file (an entry can have several sentences); the sentences
    that are not there stay as they were."""
    parti = re.split(r"(?<=[.!?])( +)(?=[A-ZÀ-Ý\"«(\d])", testo)
    frasi, spazi = parti[0::2], parti[1::2] + [""]
    if len(frasi) < 2:
        return testo
    uscita, i = [], 0
    while i < len(frasi):
        for j in range(len(frasi), i, -1):
            if (i, j) == (0, len(frasi)):
                continue  # (the whole text is already known not to be there)
            pezzo = "".join(frasi[k] + spazi[k] for k in range(i, j - 1)) + frasi[j - 1]
            tradotto = _traduci_nucleo(pezzo, 1)
            if tradotto != pezzo or j == i + 1:
                uscita.append(tradotto + spazi[j - 1])
                i = j
                break
    return "".join(uscita)


def _traduci_pezzo(testo, profondita=0):
    m = _PREFISSO.match(testo)
    prefisso, nucleo, coda = m.group(1), m.group(2), m.group(3)
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", nucleo):  # numbers and symbols: only an entry of the file (e.g. "A:" -> "To:")
        return prefisso + _stato["testi"].get(nucleo, nucleo) + coda if nucleo else testo
    return prefisso + _traduci_nucleo(nucleo, profondita) + coda


def traduci(testo):
    """The text in the interface language (line by line; what is not in the file stays as it is)."""
    if isinstance(testo, str) and testo.startswith(FISSO):
        return testo[len(FISSO):]
    if not isinstance(testo, str) or _stato["codice"] == SORGENTE or not _stato["testi"] or not testo.strip():
        return testo
    intero = _traduci_pezzo(testo)
    if intero != testo or "\n" not in testo:
        return intero
    return "\n".join(_traduci_pezzo(riga) for riga in testo.split("\n"))


# --- window (Tkinter) ------------------------------------------------------------------------------------------------

_OPZIONI_TESTO = ("text", "label", "title", "message")
_VARIABILI = weakref.WeakSet()  # the TestoVar objects, to translate again when the language changes


def _ricorda(widget, cnf):
    """The original (Italian) texts given to the widget, to translate them again if the language changes (ritraduci)."""
    if not isinstance(cnf, dict):
        return
    for k in _OPZIONI_TESTO + ("values",):
        v = cnf.get(k)
        if isinstance(v, str) or k == "values" and isinstance(v, (list, tuple)):
            widget.__dict__.setdefault("_patchsmith_testi", {})[k] = v


def aggancia_tk():
    """The texts given to Tkinter (buttons, labels, menus, window titles, values of drop-down menus) go through
    traduci() and stay remembered in the widget (ritraduci). Only once; with Italian nothing changes."""
    import tkinter
    if getattr(tkinter.Misc, "_patchsmith_lingua", False):
        return
    originale_opzioni = tkinter.Misc._options
    originale_titolo = tkinter.Wm.wm_title

    def _traduci_opzioni(cnf):
        if not cnf or _stato["codice"] == SORGENTE and not any(
                isinstance(cnf.get(k), str) and cnf[k].startswith(FISSO) for k in _OPZIONI_TESTO):
            return cnf
        cnf = dict(cnf)
        for k in _OPZIONI_TESTO:
            if isinstance(cnf.get(k), str):
                cnf[k] = traduci(cnf[k])
        if isinstance(cnf.get("values"), (list, tuple)):
            cnf["values"] = [traduci(v) if isinstance(v, str) else v for v in cnf["values"]]
        return cnf

    def _options(self, cnf, kw=None):
        _ricorda(self, cnf)
        _ricorda(self, kw)
        return originale_opzioni(self, _traduci_opzioni(cnf), _traduci_opzioni(kw))

    def wm_title(self, string=None):
        if isinstance(string, str):
            self.__dict__["_patchsmith_titolo"] = string
        return originale_titolo(self, traduci(string) if isinstance(string, str) else string)

    tkinter.Misc._options = _options
    tkinter.Wm.wm_title = tkinter.Wm.title = wm_title
    from tkinter import ttk
    originale_intestazione = ttk.Treeview.heading

    def heading(self, column, option=None, **kw):  # (column headings do not go through _options)
        if isinstance(kw.get("text"), str):
            self.__dict__.setdefault("_patchsmith_intestazioni", {})[column] = kw["text"]
            kw["text"] = traduci(kw["text"])
        return originale_intestazione(self, column, option, **kw)
    ttk.Treeview.heading = heading
    tkinter.Misc._patchsmith_lingua = True


def ritraduci(radice):
    """After imposta(): the texts already shown by the root window and its windows (buttons, labels, values of
    drop-down menus, titles, column headings, TestoVar) again from their originals, in the new language.
    The choices of drop-down menus (their variables) and the log are put back by whoever wrote them."""
    import tkinter

    def visita(w):
        for opzione, originale in list(getattr(w, "_patchsmith_testi", {}).items()):
            try:
                w.configure(**{opzione: originale})
            except tkinter.TclError:  # not an option of the widget (e.g. a menu entry): leave it alone
                del w._patchsmith_testi[opzione]
        if getattr(w, "_patchsmith_titolo", None) is not None:
            w.title(w._patchsmith_titolo)
        for colonna, originale in list(getattr(w, "_patchsmith_intestazioni", {}).items()):
            w.heading(colonna, text=originale)
        for figlio in w.winfo_children():
            visita(figlio)
    visita(radice)
    for v in list(_VARIABILI):
        try:
            v.set(v.originale)
        except tkinter.TclError:  # its window is already closed
            pass


def TestoVar(*args, **kw):
    """A StringVar for the texts shown (status lines): what is written goes through traduci() (and stays remembered,
    for ritraduci)."""
    import tkinter

    class _TestoVar(tkinter.StringVar):
        originale = ""
        __hash__ = object.__hash__  # (Variable redefines == but not the hash: needed to be in _VARIABILI)

        def set(self, value):
            self.originale = value
            return super().set(traduci(value) if isinstance(value, str) else value)
    v = _TestoVar(*args, **kw)
    if kw.get("value"):
        v.set(kw["value"])
    _VARIABILI.add(v)
    return v


def bandiera(codice, master=None):
    """The language's flag as a Tkinter image (24x16), or None."""
    import tkinter
    info = lingue().get(codice, {}).get("bandiera") or {}
    larghezza, altezza = 24, 16
    if info.get("file"):
        try:
            return tkinter.PhotoImage(master=master, file=os.path.join(CARTELLA, info["file"]))
        except tkinter.TclError:
            pass
    img = tkinter.PhotoImage(master=master, width=larghezza, height=altezza)
    if info.get("tipo") == "gb":  # simplified Union Jack
        img.put("#012169", to=(0, 0, larghezza, altezza))
        for x in range(larghezza):  # white diagonals with the red ones on top
            y = round(x * (altezza - 1) / (larghezza - 1))
            for dy in (-2, -1, 0, 1, 2):
                for yy in (y + dy, altezza - 1 - y + dy):
                    if 0 <= yy < altezza:
                        img.put("#ffffff" if abs(dy) > 0 else "#c8102e", to=(x, yy, x + 1, yy + 1))
        img.put("#ffffff", to=(0, altezza // 2 - 3, larghezza, altezza // 2 + 3))
        img.put("#ffffff", to=(larghezza // 2 - 3, 0, larghezza // 2 + 3, altezza))
        img.put("#c8102e", to=(0, altezza // 2 - 2, larghezza, altezza // 2 + 2))
        img.put("#c8102e", to=(larghezza // 2 - 2, 0, larghezza // 2 + 2, altezza))
        return img
    strisce, verticali = info.get("verticali"), True
    if not strisce:
        strisce, verticali = info.get("orizzontali"), False
    if not strisce:
        img.put("#888888", to=(0, 0, larghezza, altezza))
        return img
    n = len(strisce)
    for i, colore in enumerate(strisce):
        if verticali:
            img.put(colore, to=(larghezza * i // n, 0, larghezza * (i + 1) // n, altezza))
        else:
            img.put(colore, to=(0, altezza * i // n, larghezza, altezza * (i + 1) // n))
    return img


# --- creating a language (command "interface --create <codice>") -----------------------------------------------------

def _sembra_testo(s):
    """A text for the program's user (not an internal name, a path, a regex...)."""
    nucleo = _PREFISSO.match(s).group(2).strip()
    if len(nucleo) < 2 or not re.search(r"[A-Za-zÀ-ÿ]{2}", nucleo) or "\n\n\n" in nucleo:
        return False
    if re.search(r"(\\[dswbAZ(\[]|\(\?|[{}]\s*$|^[\w./\\-]+\.(?:json|py|dll|exe|cfg|ini|txt|log|bundle)$|^https?://)",
                 nucleo) or re.fullmatch(r"[\w.\-]+", nucleo) and not re.match(r"[A-ZÀ-Ý]", nucleo):
        return False
    if re.fullmatch(r"[\w.\-/\\:]+", nucleo) and ("_" in nucleo or "/" in nucleo or "\\" in nucleo):
        return False
    if re.search(r"\b(?:var|func|class|extends|return|elif|def|import)\b.*[:=(]|\(\)|->|\[[^\]]*\]\?|^#|==|^if .*:$"
                 r"|(?<![\w%])_[a-z]\w*", nucleo):
        return False  # code (e.g. the script for Godot) or regular expressions
    parole = nucleo.split()
    if len(parole) >= 12 and nucleo == nucleo.lower() and not re.search(r"[.,:;!?()'\"]", nucleo) \
            and sum(map(len, parole)) / len(parole) < 4.5:
        return False  # list of frequent words (language detection), not a sentence
    return " " in nucleo or nucleo.endswith(("…", ":", "?", "!")) or re.match(r"[A-ZÀ-Ý][a-zà-ÿ]+$", nucleo) is not None


def testi_del_programma(cartella=None):
    """The Italian texts of the code (patchsmith/*.py), without the docstrings, as they go in a language file."""
    cartella = cartella or os.path.dirname(os.path.abspath(__file__))
    testi = set()
    for nome in sorted(os.listdir(cartella)):
        if not nome.endswith(".py"):
            continue
        with open(os.path.join(cartella, nome), encoding="utf-8") as f:
            albero = ast.parse(f.read())
        docstring = set()
        for nodo in ast.walk(albero):
            if isinstance(nodo, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and nodo.body and \
                    isinstance(nodo.body[0], ast.Expr) and isinstance(getattr(nodo.body[0], "value", None), ast.Constant):
                docstring.add(id(nodo.body[0].value))
        for nodo in ast.walk(albero):
            if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str) and id(nodo) not in docstring:
                for pezzo in nodo.value.split("\n"):
                    if _sembra_testo(pezzo):
                        testi.add(_PREFISSO.match(pezzo).group(2).strip())
    from .language import ETICHETTA_AUTO, NOMI
    testi.update(NOMI.values())
    testi.add(ETICHETTA_AUTO)
    return sorted(testi)


def crea_lingua(codice, nome, traduttore, log=print, bandiera_info=None, massimo=None):
    """Creates or completes patchsmith/ui_language/<codice>.json by translating with 'traduttore' (e.g. backend.Locale)
    the program's texts that are missing; those already in the file (also fixed by hand) stay. Returns how many it
    added."""
    from .backend import ErroreBackend
    from .language import lingua_evidente
    from .text import leggibile
    percorso = os.path.join(CARTELLA, codice + ".json")
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        dati = {"nome": nome, "bandiera": bandiera_info or {}, "testi": {}}
    testi = dati.setdefault("testi", {})
    mancanti = [t for t in testi_del_programma() if t not in testi]
    for t in [t for t in mancanti if lingua_evidente(t, SORGENTE)]:
        testi[t] = t  # already in another language (e.g. English instructions for the local translator): stays as is
        mancanti.remove(t)
    if massimo:
        mancanti = mancanti[:massimo]
    log("Testi del programma da tradurre in %s: %d (gia' nel file: %d)" % (nome, len(mancanti), len(testi)))
    aggiunti = 0
    for i in range(0, len(mancanti), 40):
        gruppo = mancanti[i:i + 40]
        protetti = []
        for t in gruppo:  # placeholders (%s, %d...) become {{A}}, {{B}}: the translator leaves them alone
            segni = []

            def _sost(m, segni=segni):
                segni.append(m.group())
                return "{{%s}}" % chr(64 + len(segni))
            protetti.append((_SEGNO.sub(_sost, leggibile(t)), segni))
        try:
            tradotti = traduttore.traduci_righe([p for p, _ in protetti], SORGENTE, codice)
        except ErroreBackend as e:
            log("  ! traduzione interrotta: %s" % e)
            break
        for t, (p, segni), r in zip(gruppo, protetti, tradotti):
            if not r:
                continue
            ordine = [ord(m) - 65 for m in re.findall(r"\{\{([A-Z])\}\}", r)]
            if any(segni[a] == segni[b] and a > b for i, a in enumerate(ordine) for b in ordine[i + 1:]
                   if a < len(segni) and b < len(segni)):
                continue  # two placeholders of the same type swapped: the values would end up in the wrong place
            for j, s in enumerate(segni):
                r = r.replace("{{%s}}" % chr(65 + j), s, 1)
            if re.search(r"\{\{[A-Z]\}\}", r) or len(_SEGNO.findall(r)) != len(segni):
                continue  # placeholders lost or extra: better the Italian
            testi[t] = r
            aggiunti += 1
        log("  %d di %d" % (min(i + 40, len(mancanti)), len(mancanti)))
        os.makedirs(CARTELLA, exist_ok=True)
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=1, sort_keys=True)
    return aggiunti
