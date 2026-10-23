"""Euristiche sul testo: cosa e' testo leggibile, protezione dei segnaposto, gestione delle righe."""
import re

_LETTERE2 = re.compile(r"[^\W\d_]{2,}")
_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^www\.|^data:|^mailto:", re.I)
_COLORE = re.compile(r"^(?:#[0-9a-f]{3,8}|(?:rgba?|hsla?)\(.*\))$", re.I)
_FILE = re.compile(
    r"^\S+\.(?:png|jpe?g|gif|webp|svg|bmp|tga|dds|ico|mp3|ogg|wav|flac|m4a|opus|mp4|webm|avi|"
    r"json|js|mjs|cjs|ts|css|html?|xml|ya?ml|txt|md|csv|ini|cfg|dat|bin|bytes|pak|asset|prefab|"
    r"unity|ttf|otf|woff2?|fnt|atlas|exe|dll|so|dylib|lua|py|rb)$",
    re.I,
)
_SPAZI = re.compile(r"\s")
# Righe di codice dentro i dati (es. comandi di script degli eventi RPG Maker)
_INIZIO = r"(?:^|[^\w$])"
_CODICE = re.compile(
    r"=>|&&|\|\||===|!==|\$game[A-Z]|;\S"
    + r"|" + _INIZIO + r"function\s*\("
    + r"|" + _INIZIO + r"this\."
    + r"|" + _INIZIO + r"[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\("
)

# Segnaposto e codici da non tradurre: {x} {{x}} %s %1$d <tag> \C[3] **  [variabile]
_SEGNAPOSTO = re.compile(
    r"^[+\-\u2212]\d+(?:[.,]\d+)?%?(?=\s)"  # valore con il segno all'inizio ("-1 Dash Charge", "+25% damage")
    r"|\[\[\d+\]\]"  # un [[0]] scritto nel testo del gioco: si protegge, se no si confonde con i segnaposto interni
    r"|\{\{[^{}]*\}\}(?:%|×|(?:ms|s|m|x)(?!\w))?"  # {{A}} di XUnity, con l'unita' attaccata ({{A}}s)
    r"|\{[^{}\s]{0,40}\}"
    r"|\{[^\W\d][\w ]{0,38}\w\}"  # {game mode}, {player name}: segnaposto con parole separate da spazi
    r"|%(?:\d+\$)?[-+0#]*\d*(?:\.\d+)?[sdifxXc]"
    r"|<[^<>\n]{1,80}>"
    r"|(?<!\w)(?:[A-Za-z]:|%\w+%)(?:\\[\w.\-]+){2,}"  # percorsi Windows interi (C:\dir\file.txt)
    r"|\\[nrt](?![A-Za-z]*[\[<])"  # a capo scritto come testo (\n): da solo, senza la parola che segue
    r"|\\[A-Za-z]+(?:\[[^\]\n]*\])?"
    r"|\\[{}$.|!><^]"
    r"|\*\*"
    r"|\[[A-Za-z_][\w.:]*\]"  # [x], [Input:12], [Button:LeftStick]
    r"|\[[A-Za-z_]\w*(?:=[^\[\]\n]{1,60}| [\w.]+=[^\[\]\n]{1,80})\]"  # BBCode con valori: [color=40ff40], [wave amp=5]
    r"|\[/[A-Za-z_]?[\w.:]*\]"  # chiusura dei tag di colore e font (Scribble di GameMaker: [/c], [/f], [/])
    r"|/[A-Za-z_]{1,16}/"  # codici tra barre: /name/ (nome del giocatore), /n/ (a capo)
    r"|[;|][^\w\s]{0,3}[;|]"  # separatori usati dai giochi per dividere il testo, es. ";-;"
    r"|\{[^{}\n]{1,60}\}"  # altri segnaposto tra graffe ({chance variable - to be implemented})
)
_TOKEN = re.compile(r"\[\s*\[\s*(\d+)\s*\]\s*\]")

# Giapponese (kana), cinese/giapponese (ideogrammi), coreano (hangul): lingue senza spazi tra le
# parole o con parole di 1-2 caratteri, per cui le regole "a parole" non valgono.
_CJK = re.compile(
    "[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿"
    "가-힯ᄀ-ᇿ㄰-㆏ｦ-ﾟ]"
)
_PERCORSO = re.compile(r"^[\w\-./\\]+$")


def _testo_cjk(t):
    """True/False per i testi in giapponese, cinese o coreano; None se il testo non e' CJK."""
    cjk = len(_CJK.findall(t))
    if not cjk or cjk < 0.5 * sum(1 for c in t if c.isalpha()):
        return None
    if _URL.search(t) or _FILE.match(t) or _CODICE.search(t):
        return False
    # percorsi come img/背景 (con almeno un pezzo in ASCII); はい/いいえ resta testo
    if ("/" in t or "\\" in t) and _PERCORSO.match(t) and re.search(r"[A-Za-z]", t):
        return False
    return True


def e_testo_leggibile(s):
    """True se la stringa sembra testo per il giocatore (e non un id, un file, un colore...)."""
    t = s.strip()
    cjk = _testo_cjk(t) if t else None
    if cjk is not None:
        return cjk
    if len(t) < 3:
        return False
    if not _LETTERE2.search(t):
        return False
    if _URL.search(t) or _COLORE.match(t) or _FILE.match(t):
        return False
    if _CODICE.search(t):
        return False
    if not _SPAZI.search(t):  # una sola "parola": spesso e' un identificatore
        if "/" in t or "\\" in t:
            return False
        if re.search(r"[_\d]", t):  # img_12, room_3, r4c3
            return False
        if t.islower():  # valori enum: round, cellvalue
            return False
        if t.isupper():  # costanti: NONE, LEFT
            return False
        if re.search(r"[a-z][A-Z]", t):  # camelCase / PascalCase composti: UnlockFirstNewRule
            return False
        if len(t) > 40:  # blob codificati
            return False
    return True


def proteggi(riga, codici=()):
    """Sostituisce i segnaposto con token [[n]]. Restituisce (testo, lista_segnaposto). 'codici': parole del gioco
    da non tradurre (es. le icone 'EP' e 'H' di Forager), protette come i segnaposto (maiuscole esatte)."""
    trovati = []

    def _sub(m):
        trovati.append(m.group(0))
        return "[[%d]]" % (len(trovati) - 1)

    if not codici:
        return _SEGNAPOSTO.sub(_sub, riga), trovati
    schema = re.compile(r"%s|(?<![^\W\d_])(?:%s)(?![^\W\d_])" % (
        _SEGNAPOSTO.pattern, "|".join(re.escape(c) for c in sorted(codici, key=len, reverse=True))))
    return schema.sub(_sub, riga), trovati


_CITAZIONE = re.compile(r"\"[^\"\n]{1,80}\"|“[^”\n]{1,80}”|«[^»\n]{1,80}»")


def solo_parole(testo):
    """Il testo senza segnaposto, tag e codici (es. <color=#FFF>, {action.Interact}) e senza le parti tra virgolette
    (titoli e nomi lasciati in originale apposta, es. l'impresa "Favor of the Fae"): per capire in che lingua e'."""
    return _CITAZIONE.sub(" ", _TOKEN.sub(" ", proteggi(testo)[0]))


def ripristina_segnaposto(testo, segnaposto):
    """Rimette i segnaposto al posto dei token. None se qualche token e' andato perso."""
    if not segnaposto:
        return testo
    visti = []

    def _sub(m):
        i = int(m.group(1))
        if i >= len(segnaposto):
            return m.group(0)
        visti.append(i)
        return segnaposto[i]

    risultato = _TOKEN.sub(_sub, testo)
    if sorted(visti) != list(range(len(segnaposto))):
        return None
    return risultato


def dividi_righe(testo):
    """Divide in righe conservando gli spazi iniziali/finali: [(prima, nucleo, dopo), ...]."""
    parti = []
    for riga in testo.split("\n"):
        nucleo = riga.strip()
        if not nucleo:
            parti.append((riga, "", ""))
            continue
        inizio = len(riga) - len(riga.lstrip())
        fine = inizio + len(nucleo)
        parti.append((riga[:inizio], nucleo, riga[fine:]))
    return parti


def ricomponi_righe(parti, traduzioni):
    """Ricompone le righe usando il dizionario {nucleo: traduzione}."""
    return "\n".join(p + (traduzioni.get(n, n) if n else "") + d for p, n, d in parti)


def slug(nome):
    s = re.sub(r"[^\w]+", "-", nome.lower(), flags=re.UNICODE).strip("-")
    return s or "gioco"


# --- righe del registro -----------------------------------------------------------------------------------------
# Una riga che contiene DETTAGLIO e' un dettaglio tecnico (versioni, indirizzi, percorsi, elenchi di file): nella
# finestra non si mostra, ma resta nel registro su disco (e quindi in "Crea rapporto"); la riga di comando la stampa.
DETTAGLIO = "\x1f"

# parole scritte nel codice con l'apostrofo al posto dell'accento (e', gia', piu'...): nella finestra con l'accento
_ACCENTI = {"e": "è", "gia": "già", "piu": "più", "puo": "può", "perche": "perché", "cosi": "così", "pero": "però",
            "finche": "finché", "poiche": "poiché", "affinche": "affinché", "meta": "metà", "li": "lì", "la": "là",
            "cioe": "cioè", "sara": "sarà", "verra": "verrà", "partira": "partirà", "potra": "potrà",
            "dovra": "dovrà", "avra": "avrà", "fara": "farà", "dara": "darà", "restera": "resterà",
            "tradurra": "tradurrà", "usera": "userà", "ripartira": "ripartirà", "funzionera": "funzionerà",
            "riprovero": "riproverò", "faro": "farò", "potro": "potrò", "affidabilita": "affidabilità",
            "unita": "unità", "qualita": "qualità", "velocita": "velocità", "novita": "novità",
            "possibilita": "possibilità", "quantita": "quantità", "identita": "identità", "attivita": "attività",
            "E": "È", "Gia": "Già", "Piu": "Più", "Perche": "Perché", "Cosi": "Così", "Pero": "Però"}
_APOSTROFO = re.compile(r"(?<![\w'])(%s)'(?=[\s.,;:!?)\]]|$)" % "|".join(sorted(_ACCENTI, key=len, reverse=True)))


def leggibile(riga):
    """La riga per la finestra: 'e'' -> 'è', 'gia'' -> 'già'... (solo le parole note, staccate dal resto)."""
    return _APOSTROFO.sub(lambda m: _ACCENTI[m.group(1)], riga)
