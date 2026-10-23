"""Lingua dell'interfaccia di PatchSmith.

I testi del programma sono scritti in italiano nel codice. Le altre lingue stanno in patchsmith/interfaccia/<codice>.json:
    {"nome": "English", "bandiera": {"tipo": "gb"}, "testi": {"testo italiano": "traduzione", ...}}
La traduzione avviene quando la finestra mostra un testo (aggancia_tk: pulsanti, etichette, titoli, menu, valori dei
menu a tendina; TestoVar per le righe di stato; traduci() per il registro), anche per i testi con valori dentro: una
voce con %s, %d... ("Testi del gioco: %d, gia' tradotti: %d") vale come modello per tutte le righe di quella forma. I
testi senza traduzione restano in italiano. I testi originali restano ricordati nei widget: cambiata la lingua,
ritraduci() rimette quelli gia' mostrati nella lingua nuova senza riaprire il programma.

Per aggiungere una lingua: il comando "interfaccia --crea fr" della riga di comando (traduce tutti i testi con il traduttore locale;
quelli gia' nel file restano) e poi rivedere a mano il file patchsmith/interfaccia/fr.json. La bandiera si descrive con
strisce ({"verticali": [colori]} o {"orizzontali": [colori]}) oppure con un'immagine PNG 24x16 accanto al file
({"file": "fr.png"}).
"""
import ast
import json
import os
import re
import weakref

CARTELLA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interfaccia")
SORGENTE = "it"
ITALIANO = {"nome": "Italiano", "bandiera": {"verticali": ["#009246", "#ffffff", "#ce2b37"]}}
# segnaposto dei testi del programma: %s, %d, %5.1f, %r, %(nome)s, %%
_SEGNO = re.compile(r"%(?:\([^)]*\))?[-+ #0]*(?:\d+)?(?:\.\d+)?[sdrifxXceEgG%]")
# a che cosa corrisponde ogni segnaposto in una riga gia' scritta
_VALORE = {"d": r"-?\d[\d.,']*", "i": r"-?\d[\d.,']*", "f": r"-?[\d.,]+", "x": r"[0-9a-fA-F]+", "%": r"%"}
# all'inizio delle righe del registro: rientro e segni ("! ", "~ ", "* ", "- ", "== ") che restano come sono
_PREFISSO = re.compile(r"^(\s*(?:(?:!|~|\*|-|==)\s+)?)(.*?)(\s*)$", re.S)
_stato = {"codice": SORGENTE, "testi": {}, "modelli": None}
FISSO = "⁠"  # all'inizio di un testo: resta com'e' in ogni lingua (es. i nomi delle lingue nel loro menu)


# --- lingue disponibili ----------------------------------------------------------------------------------------------

def lingue():
    """{codice: {"nome", "bandiera"}}: l'italiano (la lingua del codice) e i file in patchsmith/interfaccia/."""
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
    """La lingua dell'interfaccia se non e' stata scelta: PATCHSMITH_INTERFACCIA, poi quella di Windows se c'e', poi
    l'inglese se c'e', poi l'italiano."""
    disponibili = lingue()
    scelta = os.environ.get("PATCHSMITH_INTERFACCIA")
    if scelta in disponibili:
        return scelta
    from .lingua import lingua_di_windows
    windows = (lingua_di_windows() or "").split("-")[0].lower()
    if windows in disponibili:
        return windows
    return "en" if "en" in disponibili else SORGENTE


def imposta(codice):
    """Usa la lingua 'codice' (se non c'e', l'italiano). Restituisce quella usata."""
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


# --- traduzione ------------------------------------------------------------------------------------------------------

def _regex_modello(chiave):
    """(regex, segni) di una voce con segnaposto, o None se non ne ha."""
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
            if not _SEGNO.sub("", chiave).strip():  # solo segnaposto ("%s", "%s%s"): andrebbe bene per tutto
                continue
            r = _regex_modello(chiave)
            if r is None:
                continue
            inizio = _SEGNO.split(chiave)[0][:6]
            modelli.setdefault(inizio, []).append((len(_SEGNO.sub("", chiave)), r[0], r[1], valore))
        for elenco in modelli.values():  # prima i modelli con piu' testo fisso (i piu' precisi)
            elenco.sort(key=lambda m: -m[0])
        # ultimo ripiego: due pezzi separati da ": " o tra parentesi, tradotti ciascuno per conto suo
        modelli[None] = [(0,) + _regex_modello(k) + (k,) for k in ("%s: %s", "%s (%s)", "%s - %s")]
        _stato["modelli"] = modelli
    return _stato["modelli"]


def _riempi(traduzione, segni, valori):
    """La traduzione con i valori al posto dei segnaposto: per tipo e in ordine (la traduzione puo' averli spostati)."""
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
    # tra i modelli che cominciano come il testo vince quello con piu' testo fisso (il piu' preciso), non quello con
    # l'inizio piu' lungo: "%s: interfaccia, %d testi tradotti..." batte "index.html: %s"
    migliore = None
    for inizio in [testo[:n] for n in range(min(6, len(testo)), -1, -1)]:
        for fisso, regex, segni, traduzione in modelli.get(inizio, ()):
            if migliore is not None and fisso <= migliore[0]:
                break  # (ordinati per testo fisso: gli altri del gruppo sono meno precisi)
            m = regex.match(testo)
            if m:
                migliore = (fisso, m, segni, traduzione)
                break
    if migliore is None and profondita == 0:  # messaggi fatti di piu' frasi del file messe una dopo l'altra
        tradotto = _per_frasi(testo)
        if tradotto != testo:
            return tradotto
    if migliore is None and profondita <= 1:  # ripieghi ("%s: %s", "%s (%s)"), anche per i pezzi dentro una frase
        for fisso, regex, segni, traduzione in modelli[None]:
            m = regex.match(testo)
            if m:
                migliore = (fisso, m, segni, traduzione)
                break
    if migliore is None:
        return testo
    _fisso, m, segni, traduzione = migliore
    valori = list(m.groups())
    if profondita < 2:  # i pezzi di testo dentro (es. " (387 da altri giochi)"): anche loro tradotti
        valori = [_traduci_pezzo(v, profondita + 1) if s[-1] in "sr" else v for s, v in zip(segni, valori)]
    return _riempi(traduzione, segni, valori)


def _per_frasi(testo):
    """Il testo diviso in frasi ("... evitano. Una frase con..."), tradotto a gruppi: dall'inizio, il gruppo di frasi
    consecutive piu' lungo che e' nel file (una voce puo' avere piu' frasi); le frasi che non ci sono restano com'erano."""
    parti = re.split(r"(?<=[.!?])( +)(?=[A-ZÀ-Ý\"«(\d])", testo)
    frasi, spazi = parti[0::2], parti[1::2] + [""]
    if len(frasi) < 2:
        return testo
    uscita, i = [], 0
    while i < len(frasi):
        for j in range(len(frasi), i, -1):
            if (i, j) == (0, len(frasi)):
                continue  # (il testo intero non c'e' gia')
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
    if not re.search(r"[A-Za-zÀ-ÿ]{2}", nucleo):  # numeri e simboli: solo una voce del file (es. "A:" -> "To:")
        return prefisso + _stato["testi"].get(nucleo, nucleo) + coda if nucleo else testo
    return prefisso + _traduci_nucleo(nucleo, profondita) + coda


def traduci(testo):
    """Il testo nella lingua dell'interfaccia (riga per riga; quello che non e' nel file resta com'e')."""
    if isinstance(testo, str) and testo.startswith(FISSO):
        return testo[len(FISSO):]
    if not isinstance(testo, str) or _stato["codice"] == SORGENTE or not _stato["testi"] or not testo.strip():
        return testo
    intero = _traduci_pezzo(testo)
    if intero != testo or "\n" not in testo:
        return intero
    return "\n".join(_traduci_pezzo(riga) for riga in testo.split("\n"))


# --- finestra (Tkinter) ----------------------------------------------------------------------------------------------

_OPZIONI_TESTO = ("text", "label", "title", "message")
_VARIABILI = weakref.WeakSet()  # le TestoVar, da ritradurre quando cambia la lingua


def _ricorda(widget, cnf):
    """I testi originali (italiani) dati al widget, per ritradurli se cambia la lingua (ritraduci)."""
    if not isinstance(cnf, dict):
        return
    for k in _OPZIONI_TESTO + ("values",):
        v = cnf.get(k)
        if isinstance(v, str) or k == "values" and isinstance(v, (list, tuple)):
            widget.__dict__.setdefault("_patchsmith_testi", {})[k] = v


def aggancia_tk():
    """I testi dati a Tkinter (pulsanti, etichette, menu, titoli delle finestre, valori dei menu a tendina) passano da
    traduci() e restano ricordati nel widget (ritraduci). Una volta sola; con l'italiano non cambia nulla."""
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

    def heading(self, column, option=None, **kw):  # (le intestazioni delle colonne non passano da _options)
        if isinstance(kw.get("text"), str):
            self.__dict__.setdefault("_patchsmith_intestazioni", {})[column] = kw["text"]
            kw["text"] = traduci(kw["text"])
        return originale_intestazione(self, column, option, **kw)
    ttk.Treeview.heading = heading
    tkinter.Misc._patchsmith_lingua = True


def ritraduci(radice):
    """Dopo imposta(): i testi gia' mostrati dalla finestra radice e dalle sue finestre (pulsanti, etichette, valori dei
    menu a tendina, titoli, intestazioni delle colonne, TestoVar) di nuovo dai loro originali, nella lingua nuova.
    Le scelte dei menu a tendina (le loro variabili) e il registro le rimette chi li ha scritti."""
    import tkinter

    def visita(w):
        for opzione, originale in list(getattr(w, "_patchsmith_testi", {}).items()):
            try:
                w.configure(**{opzione: originale})
            except tkinter.TclError:  # non e' un'opzione del widget (per es. una voce di un menu): si lascia stare
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
        except tkinter.TclError:  # la sua finestra e' gia' chiusa
            pass


def TestoVar(*args, **kw):
    """Una StringVar per i testi mostrati (righe di stato): quello che si scrive passa da traduci() (e resta ricordato,
    per ritraduci)."""
    import tkinter

    class _TestoVar(tkinter.StringVar):
        originale = ""
        __hash__ = object.__hash__  # (Variable ridefinisce == e non l'hash: serve per stare in _VARIABILI)

        def set(self, value):
            self.originale = value
            return super().set(traduci(value) if isinstance(value, str) else value)
    v = _TestoVar(*args, **kw)
    if kw.get("value"):
        v.set(kw["value"])
    _VARIABILI.add(v)
    return v


def bandiera(codice, master=None):
    """La bandiera della lingua come immagine di Tkinter (24x16), o None."""
    import tkinter
    info = lingue().get(codice, {}).get("bandiera") or {}
    larghezza, altezza = 24, 16
    if info.get("file"):
        try:
            return tkinter.PhotoImage(master=master, file=os.path.join(CARTELLA, info["file"]))
        except tkinter.TclError:
            pass
    img = tkinter.PhotoImage(master=master, width=larghezza, height=altezza)
    if info.get("tipo") == "gb":  # Union Jack semplificata
        img.put("#012169", to=(0, 0, larghezza, altezza))
        for x in range(larghezza):  # diagonali bianche con quelle rosse sopra
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


# --- creazione di una lingua (comando "interfaccia --crea <codice>") ---------------------------------------------------

def _sembra_testo(s):
    """Un testo per chi usa il programma (non un nome interno, un percorso, una regex...)."""
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
        return False  # codice (es. lo script per Godot) o espressioni regolari
    parole = nucleo.split()
    if len(parole) >= 12 and nucleo == nucleo.lower() and not re.search(r"[.,:;!?()'\"]", nucleo) \
            and sum(map(len, parole)) / len(parole) < 4.5:
        return False  # elenco di parole frequenti (riconoscimento della lingua), non una frase
    return " " in nucleo or nucleo.endswith(("…", ":", "?", "!")) or re.match(r"[A-ZÀ-Ý][a-zà-ÿ]+$", nucleo) is not None


def testi_del_programma(cartella=None):
    """I testi italiani del codice (patchsmith/*.py), senza le docstring, come vanno nel file di una lingua."""
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
    from .lingua import ETICHETTA_AUTO, NOMI
    testi.update(NOMI.values())
    testi.add(ETICHETTA_AUTO)
    return sorted(testi)


def crea_lingua(codice, nome, traduttore, log=print, bandiera_info=None, massimo=None):
    """Crea o completa patchsmith/interfaccia/<codice>.json traducendo con 'traduttore' (es. backend.Locale) i testi
    del programma che mancano; quelli gia' nel file (anche corretti a mano) restano. Restituisce quanti ne ha aggiunti."""
    from .backend import ErroreBackend
    from .lingua import lingua_evidente
    from .testo import leggibile
    percorso = os.path.join(CARTELLA, codice + ".json")
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        dati = {"nome": nome, "bandiera": bandiera_info or {}, "testi": {}}
    testi = dati.setdefault("testi", {})
    mancanti = [t for t in testi_del_programma() if t not in testi]
    for t in [t for t in mancanti if lingua_evidente(t, SORGENTE)]:
        testi[t] = t  # gia' in un'altra lingua (es. le istruzioni in inglese per il traduttore locale): resta com'e'
        mancanti.remove(t)
    if massimo:
        mancanti = mancanti[:massimo]
    log("Testi del programma da tradurre in %s: %d (gia' nel file: %d)" % (nome, len(mancanti), len(testi)))
    aggiunti = 0
    for i in range(0, len(mancanti), 40):
        gruppo = mancanti[i:i + 40]
        protetti = []
        for t in gruppo:  # i segnaposto (%s, %d...) diventano {{A}}, {{B}}: il traduttore li lascia stare
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
                continue  # due segnaposto dello stesso tipo scambiati: i valori finirebbero al posto sbagliato
            for j, s in enumerate(segni):
                r = r.replace("{{%s}}" % chr(65 + j), s, 1)
            if re.search(r"\{\{[A-Z]\}\}", r) or len(_SEGNO.findall(r)) != len(segni):
                continue  # segnaposto persi o in piu': meglio l'italiano
            testi[t] = r
            aggiunti += 1
        log("  %d di %d" % (min(i + 40, len(mancanti)), len(mancanti)))
        os.makedirs(CARTELLA, exist_ok=True)
        with open(percorso, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=1, sort_keys=True)
    return aggiunti
