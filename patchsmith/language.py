"""Languages: readable names, the Windows language and automatic detection of the game's language.

Detection works without the network:
- first it looks at the script (kana -> Japanese, hangul -> Korean, ideograms -> Chinese, Cyrillic...);
- for languages with the Latin alphabet it counts the most frequent words (articles, prepositions...).
"""
import locale
import re
import sys

AUTO = "auto"

# Google Translate codes with their English name.
NAMES = {
    "en": "English", "it": "Italian", "ja": "Japanese", "zh-CN": "Simplified Chinese",
    "zh-TW": "Traditional Chinese", "ko": "Korean", "fr": "French", "de": "German", "es": "Spanish",
    "pt": "Portuguese", "ru": "Russian", "pl": "Polish", "tr": "Turkish", "nl": "Dutch", "sv": "Swedish",
    "da": "Danish", "no": "Norwegian", "fi": "Finnish", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian",
    "el": "Greek", "uk": "Ukrainian", "ar": "Arabic", "he": "Hebrew", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "hi": "Hindi",
}
AUTO_LABEL = "Detect automatically"

# Language names written in their own language: in language selection menus they must not be translated.
ENDONYMS = {
    "English", "Español", "Español (Latinoamérica)", "Español - Latinoamérica", "Français", "Deutsch", "Italiano",
    "Português", "Português - Brasil", "Português (Brasil)", "Português Brasileiro", "Русский", "Українська",
    "Polski", "Türkçe", "Nederlands", "Svenska", "Dansk", "Norsk", "Suomi", "Čeština", "Magyar", "Română",
    "Ελληνικά", "日本語", "한국어", "简体中文", "繁體中文", "中文", "中文(简体)", "中文(繁體)", "ภาษาไทย",
    "Tiếng Việt", "Bahasa Indonesia", "العربية", "עברית", "हिन्दी",
}


# Name of each language in the language itself (for the language menu entry that now shows the translation).
ENDONYM = {
    "en": "English", "it": "Italiano", "fr": "Français", "de": "Deutsch", "es": "Español", "pt": "Português",
    "ru": "Русский", "pl": "Polski", "ja": "日本語", "ko": "한국어", "zh-CN": "简体中文", "zh-TW": "繁體中文",
    "nl": "Nederlands", "tr": "Türkçe", "sv": "Svenska", "da": "Dansk", "no": "Norsk", "fi": "Suomi",
    "cs": "Čeština", "hu": "Magyar", "ro": "Română", "el": "Ελληνικά", "uk": "Українська",
}


def comparison_key(code):
    """Key to compare two languages: the base language ('pt-BR' -> 'pt'), but Chinese by script
    ('zh-TW', 'zh-HK', 'zh-Hant' -> 'zh-hant'; 'zh-CN', 'zh-SG', 'zh-Hans', 'zh' -> 'zh-hans'):
    traditional and simplified are different written languages and are not interchangeable."""
    c = (code or "").strip().replace("_", "-").lower()
    base = c.split("-")[0]
    if base in ("zh", "chinese", "schinese", "tchinese"):
        if c == "tchinese" or any(p in ("tw", "hk", "mo", "hant") for p in c.split("-")[1:]):
            return "zh-hant"
        return "zh-hans"
    return base


def same_language(a, b):
    return comparison_key(a) == comparison_key(b)


def best(candidates, dest_lang):
    """The code among 'candidates' that best matches 'dest_lang': the same code, then the language without a
    region ('it', 'zh-Hans'), then another variant of the same language (never the other Chinese script).
    None if none matches."""
    dest = (dest_lang or "").strip().replace("_", "-").lower()
    key = comparison_key(dest)
    votes = []
    for c in candidates:
        n = (c or "").strip().replace("_", "-").lower()
        if not n or comparison_key(n) != key:
            continue
        rank = 0 if n == dest else 1 if n in (key, dest.split("-")[0]) else 2
        votes.append((rank, n, c))
    return min(votes)[2] if votes else None


def endonym(code):
    return ENDONYM.get(code) or ENDONYM.get((code or "").split("-")[0])


def label(code):
    """'Italian (it)'; 'Detect automatically' for auto; the code itself if unknown."""
    if code == AUTO:
        return AUTO_LABEL
    return "%s (%s)" % (NAMES[code], code) if code in NAMES else code


def labels(with_auto=False):
    main_languages = ["en", "it", "ja", "zh-CN", "ko", "fr", "de", "es", "pt", "ru"]
    other_ones = sorted((c for c in NAMES if c not in main_languages), key=lambda c: NAMES[c])
    return ([AUTO_LABEL] if with_auto else []) + [label(c) for c in main_languages + other_ones]


def _italian():
    """The Italian interface texts (names typed or saved in Italian are still understood)."""
    from .ui_language import texts_of
    return texts_of("it")


def code(text):
    """From a label ('Italian (it)'), name ('italian', also the Italian 'italiano') or code ('it') to the code."""
    t = (text or "").strip()
    italian = _italian()
    if not t or t.lower() in (AUTO_LABEL.lower(), italian.get(AUTO_LABEL, AUTO_LABEL).lower(), AUTO):
        return AUTO
    m = re.search(r"\(([A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?)\)\s*$", t)
    if m:
        return m.group(1)
    for c, name in NAMES.items():
        if t.lower() in (name.lower(), c.lower(), italian.get(name, name).lower()):
            return c
    return t


def windows_language(default_language="it"):
    """Code of the Windows interface language (or of the system), e.g. 'it'."""
    name = None
    if sys.platform.startswith("win"):
        try:
            import ctypes
            name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        except (AttributeError, OSError):
            name = None
    if not name:
        name = (locale.getlocale()[0] or "")
    name = name.replace("-", "_")
    if name.lower().startswith("zh"):
        return "zh-TW" if name.upper().endswith(("TW", "HK", "MO")) else "zh-CN"
    primary = name.split("_")[0].lower()
    if primary == "nb" or primary == "nn":
        primary = "no"
    return primary if primary in NAMES else default_language


# --- detection ----------------------------------------------------------------------------------

_WRITING_SYSTEMS = [  # (language, character ranges)
    ("ko", [(0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)]),
    ("kana", [(0x3040, 0x30FF), (0x31F0, 0x31FF)]),
    ("han", [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)]),
    ("cyrillic", [(0x0400, 0x04FF)]),
    ("el", [(0x0370, 0x03FF)]),
    ("ar", [(0x0600, 0x06FF)]),
    ("he", [(0x0590, 0x05FF)]),
    ("th", [(0x0E00, 0x0E7F)]),
    ("hi", [(0x0900, 0x097F)]),
]

_WORDS = {
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
_WORDS = {k: set(v.split()) for k, v in _WORDS.items()}
# frequent words of a language that in the _WORDS lists appear only among those of other languages: in an Italian
# text "se", "tu", "su", "no" do not mean that it is Spanish
ALSO_IN = {"it": {"a", "o", "i", "no", "se", "su", "tu", "ne", "te", "mi", "ce", "sua", "do", "po", "ve", "qui"}}


def obvious_language(text, wait):
    """The language (a _WORDS code) whose frequent words the text clearly has instead of those of the expected
    language 'wait': at least 2, and more than those of 'wait'. None if no other language shows (also for short texts
    without frequent words: doubt is not enough). A word that also belongs to the expected language ("la", "in", "se")
    counts only for it."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    target_files = wait.split("-")[0]
    own_letters = _WORDS.get(target_files, set()) | ALSO_IN.get(target_files, set())
    periods = {code: sum(1 for p in words if p in item_set and (code == target_files or p not in own_letters))
             for code, item_set in _WORDS.items()}
    best = max(periods, key=periods.get)
    if best != target_files and periods[best] >= 2 and periods[best] > periods.get(target_files, 0):
        return best
    return None


def _writing_system(c):
    o = ord(c)
    for name, ranges in _WRITING_SYSTEMS:
        for a, b in ranges:
            if a <= o <= b:
                return name
    return "latin" if c.isalpha() else None


def detect(texts, maximum=200_000):
    """(code, description) of the prevailing language in the texts. ('en', ...) if it cannot be told."""
    sample = "\n".join(texts)[:maximum]
    counts = {}
    for c in sample:
        s = _writing_system(c)
        if s:
            counts[s] = counts.get(s, 0) + 1
    letters = sum(counts.values())
    if not letters:
        return "en", "no text: I will use English"

    kana, han = counts.get("kana", 0), counts.get("han", 0)
    if (kana + han) / letters > 0.3:
        if kana > 0.05 * (kana + han):
            return "ja", "Japanese characters (kana)"
        return "zh-CN", "Chinese ideograms"
    for language in ("ko", "el", "ar", "he", "th", "hi"):
        if counts.get(language, 0) / letters > 0.3:
            return language, "alphabet"
    if counts.get("cyrillic", 0) / letters > 0.3:
        ukrainian = sum(sample.count(c) for c in "ієїґІЄЇҐ")
        return ("uk" if ukrainian > 0.005 * counts["cyrillic"] else "ru"), "Cyrillic alphabet"

    words = re.findall(r"[^\W\d_]+", sample.lower())
    if not words:
        return "en", "unrecognized text: I will use English"
    scores = {language: sum(1 for p in words if p in item_set) for language, item_set in _WORDS.items()}
    best = max(scores, key=scores.get)
    quota = scores[best] / len(words)
    if quota < 0.03:
        return "en", "unknown words: I'll use English"
    second = sorted(scores.values())[-2]
    if scores[best] > 1.5 * max(second, 1):
        return best, "common words (high reliability)"
    return best, "common words (medium reliability)"
