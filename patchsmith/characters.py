"""Adapting the characters of a translation to the ones the game's fonts can draw.

Many engines have "baked" fonts with a fixed set of characters: in XNA a missing character makes
the game crash, in GameMaker it simply does not appear. Before a translation is written,
every character the font lacks is replaced with the best one available:
  - Italian: ì -> í, ò -> ó (acute accent, if the font has it), otherwise an apostrophe (e', perche');
  - typographic quotes and ellipses -> plain ones; tab -> space; invisible characters -> removed;
  - other languages: the letter without the accent.
If there is no way to make the text drawable, the sentence is left untranslated.
"""
import unicodedata

ASCII = frozenset(chr(c) for c in range(32, 127))  # the characters of the default fonts

# Italian: first the acute accent (similar), then the apostrophe (as typed on a typewriter)
_ACUTE_IT = {"\u00ec": "\u00ed", "\u00f2": "\u00f3", "\u00f9": "\u00fa",
             "\u00cc": "\u00cd", "\u00d2": "\u00d3", "\u00d9": "\u00da"}  # grave i o u -> acute
_APOSTROPHE_IT = {"à": "a'", "è": "e'", "é": "e'", "ì": "i'", "í": "i'", "ò": "o'", "ó": "o'", "ù": "u'", "ú": "u'",
                 "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'"}
PUNCTUATION = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
                 "\u2026": "...", "\u2013": "-", "\u2014": "-", "\u00a0": " ",
                 "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",  # typographic dashes and minus signs
                 "\t": " ",  # in XNA the tab is an ordinary character: if the font lacks it, crash
                 "\u200b": "", "\u2060": "", "\ufeff": ""}  # invisible: removed


_FALLBACKS = {"[": "(", "]": ")", '"': "'", "“": "'", "”": "'", "«": "'", "»": "'", "„": "'", ";": ","}
# the accented letters a language usually uses (to tell which ones the game's fonts lack)
TYPICAL_LETTERS = {"it": "àèéìòùÈ", "fr": "àâçéèêëîïôùûœ", "de": "äöüßÄÖÜ", "es": "áéíóúñüÁÉÍÓÚÑ¿¡",
                   "pt": "àáâãçéêíóôõúÁÉÍÓÚ", "pl": "ąćęłńóśźżŁŚŻ", "cs": "áčďéěíňóřšťúůýž", "tr": "çğıöşüİ"}


def typical_letters(target_lang):
    return TYPICAL_LETTERS.get((target_lang or "").split("-")[0].lower(), "")


class Filter:
    """The characters that can be written, without copying large sets (CJK fonts have tens of thousands of them): for
    adapt_chars 'c in allowed' is enough. Those of 'base' (None: all), plus those of the original text ('added':
    the game already shows them), except the 'excluded' (the ones the game said it cannot draw, see checks.py: they
    apply even if the original has them)."""

    def __init__(self, base=None, excluded=(), added=()):
        self.base, self.excluded, self.added = base, frozenset(excluded or ()), frozenset(added or ())

    def __contains__(self, c):
        if c in self.excluded:
            return False
        return c in self.added or self.base is None or c in self.base


def adapt_without(text, excluded, target_lang):
    """The text without the 'excluded' characters (the ones the game said it cannot draw), replaced only with ASCII
    characters (è -> e', ò -> o': for the other accented characters it is not known whether the font has them), the
    text itself if it has none, None if one cannot be replaced (e.g. an emoji)."""
    if not excluded or not isinstance(text, str) or not any(c in excluded for c in text):
        return text
    safe_chars = Filter(ASCII, excluded)
    output = []
    for c in text:
        if c not in excluded:
            output.append(c)
            continue
        substitute = adapt_chars(c, safe_chars, target_lang)
        if substitute is None:
            return None
        output.append(substitute)
    return "".join(output)


def undrawable(text, allowed, target_lang):
    """The characters of the text that cannot be written even when adapted (for the reason in problemi.json)."""
    return "".join(c for c in dict.fromkeys(text) if not c.isspace()
                   and adapt_chars(c, allowed, target_lang) is None)


def adapt_chars(text, allowed, target_lang):
    """Text that uses only drawable characters (allowed=None: no limit). None if impossible."""
    if allowed is None:
        return text
    italian = target_lang.split("-")[0] == "it"
    output = []
    for c in text:
        if c in allowed or c in "\n\r":
            output.append(c)
            continue
        candidates = []
        if c in PUNCTUATION:
            candidates.append(PUNCTUATION[c])
        if c in _FALLBACKS:  # fonts without double quotes or semicolons (e.g. Forager's sprite font)
            candidates.append(_FALLBACKS[c])
        if italian:
            candidates += [s for s in (_ACUTE_IT.get(c), _APOSTROPHE_IT.get(c)) if s]
        candidates.append("".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x)))
        for s in candidates:
            if (s or c in PUNCTUATION) and all(x in allowed for x in s):
                output.append(s)
                break
        else:
            return None
    return "".join(output)
