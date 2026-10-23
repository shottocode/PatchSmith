"""Languages: readable names, the Windows language and automatic detection of the game's language.

Detection works without the network:
- first it looks at the script (kana -> Japanese, hangul -> Korean, ideograms -> Chinese, Cyrillic...);
- for languages with the Latin alphabet it counts the most frequent words (articles, prepositions...).
"""
import locale
import re
import sys

AUTO = "auto"

# Google Translate codes with their Italian name.
NOMI = {
    "en": "Inglese", "it": "Italiano", "ja": "Giapponese", "zh-CN": "Cinese semplificato",
    "zh-TW": "Cinese tradizionale", "ko": "Coreano", "fr": "Francese", "de": "Tedesco", "es": "Spagnolo",
    "pt": "Portoghese", "ru": "Russo", "pl": "Polacco", "tr": "Turco", "nl": "Olandese", "sv": "Svedese",
    "da": "Danese", "no": "Norvegese", "fi": "Finlandese", "cs": "Ceco", "hu": "Ungherese", "ro": "Rumeno",
    "el": "Greco", "uk": "Ucraino", "ar": "Arabo", "he": "Ebraico", "th": "Thailandese", "vi": "Vietnamita",
    "id": "Indonesiano", "hi": "Hindi",
}
ETICHETTA_AUTO = "Rileva automaticamente"

# Language names written in their own language: in language selection menus they must not be translated.
ENDONIMI = {
    "English", "Español", "Español (Latinoamérica)", "Español - Latinoamérica", "Français", "Deutsch", "Italiano",
    "Português", "Português - Brasil", "Português (Brasil)", "Português Brasileiro", "Русский", "Українська",
    "Polski", "Türkçe", "Nederlands", "Svenska", "Dansk", "Norsk", "Suomi", "Čeština", "Magyar", "Română",
    "Ελληνικά", "日本語", "한국어", "简体中文", "繁體中文", "中文", "中文(简体)", "中文(繁體)", "ภาษาไทย",
    "Tiếng Việt", "Bahasa Indonesia", "العربية", "עברית", "हिन्दी",
}


# Name of each language in the language itself (for the language menu entry that now shows the translation).
ENDONIMO = {
    "en": "English", "it": "Italiano", "fr": "Français", "de": "Deutsch", "es": "Español", "pt": "Português",
    "ru": "Русский", "pl": "Polski", "ja": "日本語", "ko": "한국어", "zh-CN": "简体中文", "zh-TW": "繁體中文",
    "nl": "Nederlands", "tr": "Türkçe", "sv": "Svenska", "da": "Dansk", "no": "Norsk", "fi": "Suomi",
    "cs": "Čeština", "hu": "Magyar", "ro": "Română", "el": "Ελληνικά", "uk": "Українська",
}


def chiave_confronto(codice):
    """Key to compare two languages: the base language ('pt-BR' -> 'pt'), but Chinese by script
    ('zh-TW', 'zh-HK', 'zh-Hant' -> 'zh-hant'; 'zh-CN', 'zh-SG', 'zh-Hans', 'zh' -> 'zh-hans'):
    traditional and simplified are different written languages and are not interchangeable."""
    c = (codice or "").strip().replace("_", "-").lower()
    base = c.split("-")[0]
    if base in ("zh", "chinese", "schinese", "tchinese"):
        if c == "tchinese" or any(p in ("tw", "hk", "mo", "hant") for p in c.split("-")[1:]):
            return "zh-hant"
        return "zh-hans"
    return base


def stessa_lingua(a, b):
    return chiave_confronto(a) == chiave_confronto(b)


def migliore(candidati, destinazione):
    """The code among 'candidati' that best matches 'destinazione': the same code, then the language without a
    region ('it', 'zh-Hans'), then another variant of the same language (never the other Chinese script).
    None if none matches."""
    dest = (destinazione or "").strip().replace("_", "-").lower()
    chiave = chiave_confronto(dest)
    voti = []
    for c in candidati:
        n = (c or "").strip().replace("_", "-").lower()
        if not n or chiave_confronto(n) != chiave:
            continue
        rango = 0 if n == dest else 1 if n in (chiave, dest.split("-")[0]) else 2
        voti.append((rango, n, c))
    return min(voti)[2] if voti else None


def endonimo(codice):
    return ENDONIMO.get(codice) or ENDONIMO.get((codice or "").split("-")[0])


def etichetta(codice):
    """'Italiano (it)'; 'Detect automatically' for auto; the code itself if unknown."""
    if codice == AUTO:
        return ETICHETTA_AUTO
    return "%s (%s)" % (NOMI[codice], codice) if codice in NOMI else codice


def etichette(con_auto=False):
    principali = ["en", "it", "ja", "zh-CN", "ko", "fr", "de", "es", "pt", "ru"]
    altre = sorted((c for c in NOMI if c not in principali), key=lambda c: NOMI[c])
    return ([ETICHETTA_AUTO] if con_auto else []) + [etichetta(c) for c in principali + altre]


def codice(testo):
    """From a label ('Italiano (it)'), name ('italiano') or code ('it') to the code."""
    t = (testo or "").strip()
    if not t or t.lower() in (ETICHETTA_AUTO.lower(), AUTO):
        return AUTO
    m = re.search(r"\(([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)\)\s*$", t)
    if m:
        return m.group(1)
    for c, nome in NOMI.items():
        if t.lower() in (nome.lower(), c.lower()):
            return c
    return t


def lingua_di_windows(predefinita="it"):
    """Code of the Windows interface language (or of the system), e.g. 'it'."""
    nome = None
    if sys.platform.startswith("win"):
        try:
            import ctypes
            nome = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except (AttributeError, OSError):
            nome = None
    if not nome:
        nome = (locale.getlocale()[0] or "")
    nome = nome.replace("-", "_")
    if nome.lower().startswith("zh"):
        return "zh-TW" if nome.upper().endswith(("TW", "HK", "MO")) else "zh-CN"
    primario = nome.split("_")[0].lower()
    if primario == "nb" or primario == "nn":
        primario = "no"
    return primario if primario in NOMI else predefinita


# --- detection ----------------------------------------------------------------------------------

_SCRITTURE = [  # (language, character ranges)
    ("ko", [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)]),
    ("kana", [(0x3040, 0x30FF), (0x31F0, 0x31FF)]),
    ("han", [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)]),
    ("cirillico", [(0x0400, 0x04FF)]),
    ("el", [(0x0370, 0x03FF)]),
    ("ar", [(0x0600, 0x06FF)]),
    ("he", [(0x0590, 0x05FF)]),
    ("th", [(0x0E00, 0x0E7F)]),
    ("hi", [(0x0900, 0x097F)]),
]

_PAROLE = {
    "en": "the and of to a in is you that it for on with as are this be your or not can will have from at by "
          "all an if no was what into out up",
    "it": "il di che e la per un una non è in del della le si con sono al alla gli lo da ma come più questo "
          "tuo tua nel nella dei delle anche",
    "fr": "le la les de des et un une est vous pas pour que en du dans sur avec ce qui votre au plus il elle "
          "sont ne",
    "de": "der die das und ist nicht ein eine zu den mit sie es du ich von auf für dem sich auch wird im "
          "werden",
    "es": "el la los las de y que en un una es no para con por se del al lo tu su más está como pero",
    "pt": "o a os as de e que em um uma é não para com do da por se no na você seu sua mais como",
    "nl": "de het een en van is niet dat je op te met voor zijn ook dit naar",
    "pl": "i w na nie z się to jest że do o jak ale co czy jego tak po",
    "tr": "ve bir bu da de için ile ne çok değil gibi olarak daha mi",
    "sv": "och att det som en är på för med inte har till av den om",
}
_PAROLE = {k: set(v.split()) for k, v in _PAROLE.items()}
# frequent words of a language that in the _PAROLE lists appear only among those of other languages: in an Italian
# text "se", "tu", "su", "no" do not mean that it is Spanish
ANCHE_DI = {"it": {"a", "o", "i", "no", "se", "su", "tu", "ne", "te", "mi", "ce", "sua", "do", "po", "ve", "qui"}}


def lingua_evidente(testo, attesa):
    """The language (a _PAROLE code) whose frequent words the text clearly has instead of those of the 'attesa'
    language: at least 2, and more than those of 'attesa'. None if no other language shows (also for short texts
    without frequent words: doubt is not enough). A word that also belongs to the expected language ("la", "in", "se")
    counts only for it."""
    parole = re.findall(r"[^\W\d_]+", testo.lower())
    arrivo = attesa.split("-")[0]
    proprie = _PAROLE.get(arrivo, set()) | ANCHE_DI.get(arrivo, set())
    punti = {codice: sum(1 for p in parole if p in insieme and (codice == arrivo or p not in proprie))
             for codice, insieme in _PAROLE.items()}
    migliore = max(punti, key=punti.get)
    if migliore != arrivo and punti[migliore] >= 2 and punti[migliore] > punti.get(arrivo, 0):
        return migliore
    return None


def _scrittura(c):
    o = ord(c)
    for nome, intervalli in _SCRITTURE:
        for a, b in intervalli:
            if a <= o <= b:
                return nome
    return "latino" if c.isalpha() else None


def rileva(testi, massimo=200_000):
    """(codice, descrizione) of the prevailing language in the texts. ('en', ...) if it cannot be told."""
    campione = "\n".join(testi)[:massimo]
    conteggi = {}
    for c in campione:
        s = _scrittura(c)
        if s:
            conteggi[s] = conteggi.get(s, 0) + 1
    lettere = sum(conteggi.values())
    if not lettere:
        return "en", "nessun testo: uso l'inglese"

    kana, han = conteggi.get("kana", 0), conteggi.get("han", 0)
    if (kana + han) / lettere > 0.3:
        if kana > 0.05 * (kana + han):
            return "ja", "caratteri giapponesi (kana)"
        return "zh-CN", "ideogrammi cinesi"
    for lingua in ("ko", "el", "ar", "he", "th", "hi"):
        if conteggi.get(lingua, 0) / lettere > 0.3:
            return lingua, "alfabeto"
    if conteggi.get("cirillico", 0) / lettere > 0.3:
        ucraino = sum(campione.count(c) for c in "ієїґІЄЇҐ")
        return ("uk" if ucraino > 0.005 * conteggi["cirillico"] else "ru"), "alfabeto cirillico"

    parole = re.findall(r"[^\W\d_]+", campione.lower())
    if not parole:
        return "en", "testo non riconosciuto: uso l'inglese"
    punteggi = {lingua: sum(1 for p in parole if p in insieme) for lingua, insieme in _PAROLE.items()}
    migliore = max(punteggi, key=punteggi.get)
    quota = punteggi[migliore] / len(parole)
    if quota < 0.03:
        return "en", "parole non riconosciute: uso l'inglese"
    secondo = sorted(punteggi.values())[-2]
    sicurezza = "alta" if punteggi[migliore] > 1.5 * max(secondo, 1) else "media"
    return migliore, "parole frequenti (affidabilità %s)" % sicurezza
