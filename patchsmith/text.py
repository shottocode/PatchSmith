"""Text heuristics: what counts as readable text, placeholder protection, line handling."""
import re

_LETTERS2 = re.compile(r"[^\W\d_]{2,}")
_URL = re.compile(r"^(?:[a-z][a-z0-9+.-]*:)?//|^www\.|^data:|^mailto:", re.I)
_COLOR = re.compile(r"^(?:#[0-9a-f]{3,8}|(?:rgba?|hsla?)\(.*\))$", re.I)
_FILE = re.compile(
    r"^\S+\.(?:png|jpe?g|gif|webp|svg|bmp|tga|dds|ico|mp3|ogg|wav|flac|m4a|opus|mp4|webm|avi|"
    r"json|js|mjs|cjs|ts|css|html?|xml|ya?ml|txt|md|csv|ini|cfg|dat|bin|bytes|pak|asset|prefab|"
    r"unity|ttf|otf|woff2?|fnt|atlas|exe|dll|so|dylib|lua|py|rb)$",
    re.I,
)
_SPACES = re.compile(r"\s")
# Lines of code inside the data (e.g. script commands of RPG Maker events)
_START = r"(?:^|[^\w$])"
_CODE = re.compile(
    r"=>|&&|\|\||===|!==|\$game[A-Z]|;\S"
    + r"|" + _START + r"function\s*\("
    + r"|" + _START + r"this\."
    + r"|" + _START + r"[A-Za-z_$][\w$]*\.[A-Za-z_$][\w$]*\("
)

# Placeholders and codes not to translate: {x} {{x}} %s %1$d <tag> \C[3] **  [variable]
_PLACEHOLDER = re.compile(
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
_PATH = re.compile(r"^[\w\-./\\]+$")


def _cjk_text(t):
    """True/False for texts in Japanese, Chinese or Korean; None if the text is not CJK."""
    cjk = len(_CJK.findall(t))
    if not cjk or cjk < 0.5 * sum(1 for c in t if c.isalpha()):
        return None
    if _URL.search(t) or _FILE.match(t) or _CODE.search(t):
        return False
    # paths like img/背景 (with at least one ASCII part); はい/いいえ stays text
    if ("/" in t or "\\" in t) and _PATH.match(t) and re.search(r"[A-Za-z]", t):
        return False
    return True


def is_readable_text(s):
    """True if the string looks like text for the player (and not an id, a file, a color...)."""
    t = s.strip()
    cjk = _cjk_text(t) if t else None
    if cjk is not None:
        return cjk
    if len(t) < 3:
        return False
    if not _LETTERS2.search(t):
        return False
    if _URL.search(t) or _COLOR.match(t) or _FILE.match(t):
        return False
    if _CODE.search(t):
        return False
    if not _SPACES.search(t):  # a single "word": often an identifier
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


def protect(line, codes=()):
    """Replaces the placeholders with [[n]] tokens. Returns (text, placeholder_list). 'codici': game words not
    to translate (e.g. Forager's 'EP' and 'H' icons), protected like the placeholders (exact case)."""
    found = []

    def _sub(m):
        found.append(m.group(0))
        return "[[%d]]" % (len(found) - 1)

    if not codes:
        return _PLACEHOLDER.sub(_sub, line), found
    pattern = re.compile(r"%s|(?<![^\W\d_])(?:%s)(?![^\W\d_])" % (
        _PLACEHOLDER.pattern, "|".join(re.escape(c) for c in sorted(codes, key=len, reverse=True))))
    return pattern.sub(_sub, line), found


_QUOTATION = re.compile(r"\"[^\"\n]{1,80}\"|“[^”\n]{1,80}”|«[^»\n]{1,80}»")


def words_only(text):
    """The text without placeholders, tags and codes (e.g. <color=#FFF>, {action.Interact}) and without the quoted
    parts (titles and names left untranslated on purpose, e.g. the achievement "Favor of the Fae"): to tell which
    language it is in."""
    return _QUOTATION.sub(" ", _TOKEN.sub(" ", protect(text)[0]))


def restore_placeholders(text, placeholders):
    """Puts the placeholders back in place of the tokens. None if some token was lost."""
    if not placeholders:
        return text
    seen = []

    def _sub(m):
        i = int(m.group(1))
        if i >= len(placeholders):
            return m.group(0)
        seen.append(i)
        return placeholders[i]

    result = _TOKEN.sub(_sub, text)
    if sorted(seen) != list(range(len(placeholders))):
        return None
    return result


def split_lines(text):
    """Splits into lines keeping the leading/trailing spaces: [(prima, nucleo, dopo), ...]."""
    parts = []
    for line in text.split("\n"):
        core = line.strip()
        if not core:
            parts.append((line, "", ""))
            continue
        start = len(line) - len(line.lstrip())
        end = start + len(core)
        parts.append((line[:start], core, line[end:]))
    return parts


def reassemble_lines(parts, translations):
    """Rebuilds the lines using the dictionary {nucleo: traduzione}."""
    return "\n".join(p + (translations.get(n, n) if n else "") + d for p, n, d in parts)


def slug(name):
    s = re.sub(r"[^\w]+", "-", name.lower(), flags=re.UNICODE).strip("-")
    return s or "gioco"


# --- log lines --------------------------------------------------------------------------------------------------
# A line that contains DETAIL is a technical detail (versions, addresses, paths, file lists): the window does not
# show it, but it stays in the log on disk (and so in "Create report"); the command line prints it.
DETAIL = "\x1f"

# words written in the code with the apostrophe instead of the accent (e', gia', piu'...): in the window with the accent
_ACCENTS = {"e": "è", "gia": "già", "piu": "più", "puo": "può", "perche": "perché", "cosi": "così", "pero": "però",
            "finche": "finché", "poiche": "poiché", "affinche": "affinché", "meta": "metà", "li": "lì", "la": "là",
            "cioe": "cioè", "sara": "sarà", "verra": "verrà", "partira": "partirà", "potra": "potrà",
            "dovra": "dovrà", "avra": "avrà", "fara": "farà", "dara": "darà", "restera": "resterà",
            "tradurra": "tradurrà", "usera": "userà", "ripartira": "ripartirà", "funzionera": "funzionerà",
            "riprovero": "riproverò", "faro": "farò", "potro": "potrò", "affidabilita": "affidabilità",
            "unita": "unità", "qualita": "qualità", "velocita": "velocità", "novita": "novità",
            "possibilita": "possibilità", "quantita": "quantità", "identita": "identità", "attivita": "attività",
            "E": "È", "Gia": "Già", "Piu": "Più", "Perche": "Perché", "Cosi": "Così", "Pero": "Però"}
_APOSTROPHE = re.compile(r"(?<![\w'])(%s)'(?=[\s.,;:!?)\]]|$)" % "|".join(sorted(_ACCENTS, key=len, reverse=True)))


def readable(line):
    """The line for the window: 'e'' -> 'è', 'gia'' -> 'già'... (only the known words, standing on their own)."""
    return _APOSTROPHE.sub(lambda m: _ACCENTS[m.group(1)], line)
