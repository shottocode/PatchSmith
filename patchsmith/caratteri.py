"""Adattamento dei caratteri di una traduzione a quelli che i font del gioco sanno disegnare.

Molti motori hanno font "cotti" con un insieme fisso di caratteri: in XNA un carattere mancante fa
andare in crash il gioco, in GameMaker semplicemente non compare. Prima di scrivere una traduzione,
ogni carattere che il font non ha viene sostituito con il migliore disponibile:
  - italiano: ì -> í, ò -> ó (accento acuto, se il font lo ha), altrimenti apostrofo (e', perche');
  - virgolette e puntini tipografici -> quelli semplici; tab -> spazio; caratteri invisibili -> tolti;
  - altre lingue: lettera senza accento.
Se non c'e' modo di rendere il testo disegnabile, la frase resta in originale.
"""
import unicodedata

ASCII = frozenset(chr(c) for c in range(32, 127))  # i caratteri dei font predefiniti

# italiano: prima l'accento acuto (simile), poi l'apostrofo (come si scriveva a macchina)
_ACUTO_IT = {"\u00ec": "\u00ed", "\u00f2": "\u00f3", "\u00f9": "\u00fa", "\u00cc": "\u00cd", "\u00d2": "\u00d3", "\u00d9": "\u00da"}  # i o u gravi -> acuti
_APOSTROFO_IT = {"à": "a'", "è": "e'", "é": "e'", "ì": "i'", "í": "i'", "ò": "o'", "ó": "o'", "ù": "u'", "ú": "u'",
                 "À": "A'", "È": "E'", "É": "E'", "Ì": "I'", "Ò": "O'", "Ù": "U'"}
PUNTEGGIATURA = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00ab": '"', "\u00bb": '"',
                 "\u2026": "...", "\u2013": "-", "\u2014": "-", "\u00a0": " ",
                 "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2212": "-",  # trattini e meno tipografici
                 "\t": " ",  # in XNA il tab e' un carattere qualsiasi: se il font non lo ha, crash
                 "\u200b": "", "\u2060": "", "\ufeff": ""}  # invisibili: via


_RIPIEGHI = {"[": "(", "]": ")", '"': "'", "“": "'", "”": "'", "«": "'", "»": "'", "„": "'", ";": ","}
# le lettere accentate che una lingua usa di solito (per dire quali mancano ai font del gioco)
LETTERE_TIPICHE = {"it": "àèéìòùÈ", "fr": "àâçéèêëîïôùûœ", "de": "äöüßÄÖÜ", "es": "áéíóúñüÁÉÍÓÚÑ¿¡",
                   "pt": "àáâãçéêíóôõúÁÉÍÓÚ", "pl": "ąćęłńóśźżŁŚŻ", "cs": "áčďéěíňóřšťúůýž", "tr": "çğıöşüİ"}


def lettere_tipiche(lingua_dest):
    return LETTERE_TIPICHE.get((lingua_dest or "").split("-")[0].lower(), "")


class Filtro:
    """I caratteri che si possono scrivere, senza copiare insiemi grandi (i font CJK ne hanno decine di migliaia): per
    adatta_caratteri basta 'c in ammessi'. Quelli di 'base' (None: tutti), piu' quelli del testo originale ('aggiunti':
    il gioco li mostra gia'), tranne gli 'esclusi' (quelli che il gioco ha detto di non saper disegnare, vedi
    controllo.py: valgono anche se l'originale li ha)."""

    def __init__(self, base=None, esclusi=(), aggiunti=()):
        self.base, self.esclusi, self.aggiunti = base, frozenset(esclusi or ()), frozenset(aggiunti or ())

    def __contains__(self, c):
        if c in self.esclusi:
            return False
        return c in self.aggiunti or self.base is None or c in self.base


def adatta_senza(testo, esclusi, lingua_dest):
    """Il testo senza i caratteri 'esclusi' (quelli che il gioco ha detto di non saper disegnare), sostituiti solo con
    caratteri ASCII (è -> e', ò -> o': degli altri caratteri accentati il font non si sa se li ha), il testo stesso se
    non ne ha, None se uno non si puo' sostituire (es. un'emoji)."""
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
    """I caratteri del testo che non si possono scrivere nemmeno adattati (per il motivo in problemi.json)."""
    return "".join(c for c in dict.fromkeys(testo) if not c.isspace()
                   and adatta_caratteri(c, ammessi, lingua_dest) is None)


def adatta_caratteri(testo, ammessi, lingua_dest):
    """Testo che usa solo caratteri disegnabili (ammessi=None: nessun limite). None se impossibile."""
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
        if c in _RIPIEGHI:  # font senza virgolette doppie o punto e virgola (es. il font a sprite di Forager)
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
