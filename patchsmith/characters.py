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
_ACUTO_IT = {"\u00ec": "\u00ed", "\u00f2": "\u00f3", "\u00f9": "\u00fa", "\u00cc": "\u00cd", "\u00d2": "\u00d3", "\u00d9": "\u00da"}  # i o u gravi -> acuti
_APOSTROFO_IT = {"à": "a'", "è": "e'", "é": "e'", "ì": "i'", "í": "i'", "ò": "o'", "ó": "o'", "ù": "u'", "ú": "u'",
                 "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'"}
PUNTEGGIATURA = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
                 "\u2026": "...", "\u2013": "-", "\u2014": "-", "\u00a0": " ",
                 "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",  # typographic dashes and minus signs
                 "\t": " ",  # in XNA the tab is an ordinary character: if the font lacks it, crash
                 "\u200b": "", "\u2060": "", "\ufeff": ""}  # invisible: removed


_RIPIEGHI = {"[": "(", "]": ")", '"': "'", "“": "'", "”": "'", "«": "'", "»": "'", "„": "'", ";": ","}
# the accented letters a language usually uses (to tell which ones the game's fonts lack)
LETTERE_TIPICHE = {"it": "àèéìòùÈ", "fr": "àâçéèêëîïôùûœ", "de": "äöüßÄÖÜ", "es": "áéíóúñüÁÉÍÓÚÑ¿¡",
                   "pt": "àáâãçéêíóôõúÁÉÍÓÚ", "pl": "ąćęłńóśźżŁŚŻ", "cs": "áčďéěíňóřšťúůýž", "tr": "çğıöşüİ"}


def lettere_tipiche(lingua_dest):
    return LETTERE_TIPICHE.get((lingua_dest or "").split("-")[0].lower(), "")


class Filtro:
    """The characters that can be written, without copying large sets (CJK fonts have tens of thousands of them): for
    adatta_caratteri 'c in ammessi' is enough. Those of 'base' (None: all), plus those of the original text ('aggiunti':
    the game already shows them), except the 'esclusi' (the ones the game said it cannot draw, see checks.py: they
    apply even if the original has them)."""

    def __init__(self, base=None, esclusi=(), aggiunti=()):
        self.base, self.esclusi, self.aggiunti = base, frozenset(esclusi or ()), frozenset(aggiunti or ())

    def __contains__(self, c):
        if c in self.esclusi:
            return False
        return c in self.aggiunti or self.base is None or c in self.base


def adatta_senza(testo, esclusi, lingua_dest):
    """The text without the 'esclusi' characters (the ones the game said it cannot draw), replaced only with ASCII
    characters (è -> e', ò -> o': for the other accented characters it is not known whether the font has them), the
    text itself if it has none, None if one cannot be replaced (e.g. an emoji)."""
    if not esclusi or not isinstance(testo, str) or not any(c in esclusi for c in testo):
        return testo
    sicuri = Filtro(ASCII, esclusi)
    uscita = []
    for c in testo:
        if c not in esclusi:
            uscita.append(c)
            continue
        sostituto = adatta_caratteri(c, sicuri, lingua_dest)
        if sostituto is None:
            return None
        uscita.append(sostituto)
    return "".join(uscita)


def non_disegnabili(testo, ammessi, lingua_dest):
    """The characters of the text that cannot be written even when adapted (for the reason in problemi.json)."""
    return "".join(c for c in dict.fromkeys(testo) if not c.isspace()
                   and adatta_caratteri(c, ammessi, lingua_dest) is None)


def adatta_caratteri(testo, ammessi, lingua_dest):
    """Text that uses only drawable characters (ammessi=None: no limit). None if impossible."""
    if ammessi is None:
        return testo
    italiano = lingua_dest.split("-")[0] == "it"
    uscita = []
    for c in testo:
        if c in ammessi or c in "\n\r":
            uscita.append(c)
            continue
        candidati = []
        if c in PUNTEGGIATURA:
            candidati.append(PUNTEGGIATURA[c])
        if c in _RIPIEGHI:  # fonts without double quotes or semicolons (e.g. Forager's sprite font)
            candidati.append(_RIPIEGHI[c])
        if italiano:
            candidati += [s for s in (_ACUTO_IT.get(c), _APOSTROFO_IT.get(c)) if s]
        candidati.append("".join(x for x in unicodedata.normalize("NFKD", c) if not unicodedata.combining(x)))
        for s in candidati:
            if (s or c in PUNTEGGIATURA) and all(x in ammessi for x in s):
                uscita.append(s)
                break
        else:
            return None
    return "".join(uscita)
