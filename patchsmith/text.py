"""Text heuristics: what counts as readable text, placeholder protection, line handling."""
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
# Lines of code inside the data (e.g. script commands of RPG Maker events)
_INIZIO = r"(?:^|[^\w$])"
_CODICE = re.compile(
    r"=>|&&|\|\||===|!==|\$game[A-Z]|;\S"
    + r"|" + _INIZIO + r"function\s*\("
    + r"|" + _INIZIO + r"this\."
    + r"|" + _INIZIO + r"[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\("
)

# Placeholders and codes not to translate: {x} {{x}} %s %1$d <tag> \C[3] **  [variable]
_SEGNAPOSTO = re.compile(
    r"^[+\-\u2212]\d+(?:[.,]\d+)?%?(?=\s)"  # value with the sign at the start ("-1 Dash Charge", "+25% damage")
    r"|\[\[\d+\]\]"  # a [[0]] written in the game's text: protected, otherwise mistaken for the internal placeholders
    r"|\{\{[^{}]*\}\}(?:%|×|(?:ms|s|m|x)(?!\w))?"  # XUnity's {{A}}, with the unit attached ({{A}}s)
    r"|\{[^{}\s]{0,40}\}"
    r"|\{[^\W\d][\w ]{0,38}\w\}"  # {game mode}, {player name}: placeholders with words separated by spaces
    r"|%(?:\d+\$)?[-+0#]*\d*(?:\.\d+)?[sdifxXc]"
    r"|<[^<>\n]{1,80}>"
    r"|(?<!\w)(?:[A-Za-z]:|%\w+%)(?:\\[\w.\-]+){2,}"  # whole Windows paths (C:\dir\file.txt)
    r"|\\[nrt](?![A-Za-z]*[\[<])"  # newline written as text (\n): on its own, without the word that follows
    r"|\\[A-Za-z]+(?:\[[^\]\n]*\])?"
    r"|\\[{}$.|!><^]"
    r"|\*\*"
    r"|\[[A-Za-z_][\w.:]*\]"  # [x], [Input:12], [Button:LeftStick]
    r"|\[[A-Za-z_]\w*(?:=[^\[\]\n]{1,60}| [\w.]+=[^\[\]\n]{1,80})\]"  # BBCode with values: [color=40ff40], [wave amp=5]
    r"|\[/[A-Za-z_]?[\w.:]*\]"  # closing color and font tags (GameMaker's Scribble: [/c], [/f], [/])
    r"|/[A-Za-z_]{1,16}/"  # codes between slashes: /name/ (the player's name), /n/ (newline)
    r"|[;|][^\w\s]{0,3}[;|]"  # separators games use to split the text, e.g. ";-;"
    r"|\{[^{}\n]{1,60}\}"  # other placeholders in braces ({chance variable - to be implemented})
)
_TOKEN = re.compile(r"\[\s*\[\s*(\d+)\s*\]\s*\]")

# Japanese (kana), Chinese/Japanese (ideograms), Korean (hangul): languages without spaces between
# words or with words of 1-2 characters, so the word-based rules do not apply.
_CJK = re.compile(
    "[぀-ヿㇰ-ㇿ㐀-䶿一-鿿豈-﫿"
    "가-힯ᄀ-ᇿ㄰-㆏ｦ-ﾟ]"
)
_PERCORSO = re.compile(r"^[\w\-./\\]+$")


def _testo_cjk(t):
    """True/False for texts in Japanese, Chinese or Korean; None if the text is not CJK."""
    cjk = len(_CJK.findall(t))
    if not cjk or cjk < 0.5 * sum(1 for c in t if c.isalpha()):
        return None
    if _URL.search(t) or _FILE.match(t) or _CODICE.search(t):
        return False
    # paths like img/背景 (with at least one ASCII part); はい/いいえ stays text
    if ("/" in t or "\\" in t) and _PERCORSO.match(t) and re.search(r"[A-Za-z]", t):
        return False
    return True


def e_testo_leggibile(s):
    """True if the string looks like text for the player (and not an id, a file, a color...)."""
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
    if not _SPAZI.search(t):  # a single "word": often an identifier
        if "/" in t or "\\" in t:
            return False
        if re.search(r"[_\d]", t):  # img_12, room_3, r4c3
            return False
        if t.islower():  # enum values: round, cellvalue
            return False
        if t.isupper():  # constants: NONE, LEFT
            return False
        if re.search(r"[a-z][A-Z]", t):  # compound camelCase / PascalCase: UnlockFirstNewRule
            return False
        if len(t) > 40:  # encoded blobs
            return False
    return True


def proteggi(riga, codici=()):
    """Replaces the placeholders with [[n]] tokens. Returns (text, placeholder_list). 'codici': game words not
    to translate (e.g. Forager's 'EP' and 'H' icons), protected like the placeholders (exact case)."""
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
    """The text without placeholders, tags and codes (e.g. <color=#FFF>, {action.Interact}) and without the quoted
    parts (titles and names left untranslated on purpose, e.g. the achievement "Favor of the Fae"): to tell which
    language it is in."""
    return _CITAZIONE.sub(" ", _TOKEN.sub(" ", proteggi(testo)[0]))


def ripristina_segnaposto(testo, segnaposto):
    """Puts the placeholders back in place of the tokens. None if some token was lost."""
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
    """Splits into lines keeping the leading/trailing spaces: [(prima, nucleo, dopo), ...]."""
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
    """Rebuilds the lines using the dictionary {nucleo: traduzione}."""
    return "\n".join(p + (traduzioni.get(n, n) if n else "") + d for p, n, d in parti)


def slug(nome):
    s = re.sub(r"[^\w]+", "-", nome.lower(), flags=re.UNICODE).strip("-")
    return s or "gioco"


# --- log lines --------------------------------------------------------------------------------------------------
# A line that contains DETTAGLIO is a technical detail (versions, addresses, paths, file lists): the window does not
# show it, but it stays in the log on disk (and so in "Create report"); the command line prints it.
DETTAGLIO = "\x1f"

# words written in the code with the apostrophe instead of the accent (e', gia', piu'...): in the window with the accent
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
    """The line for the window: 'e'' -> 'è', 'gia'' -> 'già'... (only the known words, standing on their own)."""
    return _APOSTROFO.sub(lambda m: _ACCENTI[m.group(1)], riga)
