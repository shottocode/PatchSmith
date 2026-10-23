"""CSV translation tables with one column per language (e.g. Megaloot: translations.csv with
id,english,french,german,spanish,japanese,koreana,...,schinese,tchinese).

The column of the original language (usually english) is translated in place: that language stays selected in the
game. A column that is not a language is needed (the key, e.g. 'id'): without it, the game might look up the texts
by their very English value, and the table is not touched.

The file is rewritten the way the game had written it (separator , ; or tab, BOM, line breaks \\n or \\r\\n, cells
in quotes or not): only the translated cells are replaced, the rest stays identical byte for byte; after writing,
the file is read back and every cell must be the expected one. Many games read the CSV by hand, splitting the lines
on the commas (Megaloot: no English text has a comma, '"?"' is a text with quotes): if the file does not use quotes
the CSV way (a quoted cell containing the separator or a line break), quotes are text like any other letter, and in
the translations the separator and the quotes are replaced the way the game's texts do (',' -> ';', '"' -> "'"); a
translation with an extra line break is left untranslated.
"""
import csv
import io
import os
import re

from . import adapter_gamemaker

SEPARATORI = (",", ";", "\t")
MASSIMO = 20 * 1024 * 1024
MINIMO_TESTI = 3  # lines with a text in the original column, to be a translation table
PIENA = 0.5  # a language is present (official translation) if its column has at least half the texts of the original
ESTENSIONI = (".csv", ".tsv")


class ErroreCSV(ValueError):
    pass


def lingua_della_colonna(nome):
    """The language code of a column name ('english', 'en', 'English (US)', 'koreana', 'schinese',
    'textEN'...), or None ('id', 'key', 'notes'...)."""
    n = (nome or "").strip().lstrip("\ufeff").strip()
    if not n or len(n) > 40 or "/" in n or "\\" in n:
        return None
    return adapter_gamemaker.lingua_del_percorso(re.sub(r"[()\[\]:,]", " ", n).strip())


def _celle_csv(testo, sep):
    """[[(inizio, fine, valore, tra_virgolette)]] of the lines, with the CSV rules (quotes, "" inside a quoted cell,
    line breaks inside quotes). Raises ErroreCSV if the quotes are not used this way."""
    righe, riga, i, n = [], [], 0, len(testo)
    fine_cella = re.compile("[%s\r\n]" % re.escape(sep))
    while n:
        if testo.startswith('"', i):
            j, pezzi = i + 1, []
            while True:
                k = testo.find('"', j)
                if k < 0:
                    raise ErroreCSV("virgolette non chiuse")
                pezzi.append(testo[j:k])
                if testo.startswith('"', k + 1):
                    pezzi.append('"')
                    j = k + 2
                    continue
                fine = k + 1
                break
            if fine < n and testo[fine] not in sep + "\r\n":
                raise ErroreCSV("testo dopo le virgolette di chiusura")
            riga.append((i, fine, "".join(pezzi), True))
        else:
            m = fine_cella.search(testo, i)
            fine = m.start() if m else n
            riga.append((i, fine, testo[i:fine], False))
        if fine >= n:
            righe.append(riga)
            break
        if testo[fine] == sep:
            i = fine + 1
            continue
        righe.append(riga)
        riga = []
        i = fine + (2 if testo.startswith("\r\n", fine) else 1)
        if i >= n:
            break
    return righe


def _celle_semplici(testo, sep):
    """The lines split the way a game that reads the CSV by hand does: at every line break and at every separator
    (quotes are text)."""
    righe, i, n = [], 0, len(testo)
    for m in re.finditer(r"\r\n|\n|\r|\Z", testo):
        if m.start() == n and i >= n:
            break
        riga, inizio = [], i
        for pezzo in testo[i:m.start()].split(sep):
            riga.append((inizio, inizio + len(pezzo), pezzo, False))
            inizio += len(pezzo) + 1
        righe.append(riga)
        i = m.end()
        if m.start() == n:
            break
    return righe


def _separatore(testo):
    """The table's separator: the one that appears most in the first line (outside quotes)."""
    prima = re.split(r"\r\n|\n|\r", testo, 1)[0]
    prima = re.sub(r'"[^"]*"', "", prima)
    conti = {s: prima.count(s) for s in SEPARATORI}
    migliore = max(SEPARATORI, key=lambda s: conti[s])
    if not conti[migliore]:
        raise ErroreCSV("nessun separatore nella prima riga")
    return migliore


class FileCSV:
    """A CSV table that was read: cells with their position in the text, header, language columns."""

    def __init__(self, dati_byte, a_mano=False):
        """a_mano=True: the lines are split the way a game that reads the CSV by hand does, even if the file is a
        valid CSV (to read back a file rewritten like the original, see serializza)."""
        try:
            testo = dati_byte.decode("utf-8")
        except UnicodeDecodeError as e:
            raise ErroreCSV("il file non e' UTF-8: %s" % e) from e
        self.bom = testo.startswith("\ufeff")
        self.testo = testo[1:] if self.bom else testo
        self.separatore = _separatore(self.testo)
        try:
            if a_mano:
                raise ErroreCSV("diviso a mano")
            self.righe = _celle_csv(self.testo, self.separatore)
            self.virgolette = True
        except ErroreCSV:
            self.righe = _celle_semplici(self.testo, self.separatore)
            self.virgolette = False
        citate = [c[2] for r in self.righe for c in r if c[3]]
        # the game really reads the CSV with quotes only if the file needs them (separator or line break inside a
        # cell); otherwise the game might split by hand: no separators or line breaks in the translations
        self.speciali = any(self.separatore in v or "\n" in v or "\r" in v for v in citate)
        self.doppie = any('"' in v for v in citate)
        if self.virgolette:
            self._controlla_con_csv()
        self.intestazione = [c[2] for c in self.righe[0]] if self.righe else []
        self.lingue = {i: lingua_della_colonna(n) for i, n in enumerate(self.intestazione)}
        self.lingue = {i: c for i, c in self.lingue.items() if c}
        # the key: filled in almost all the lines that have something (the empty lines, which many tables use to
        # separate groups of texts, do not count)
        usate = sum(1 for r in range(1, len(self.righe)) if any(c[2].strip() for c in self.righe[r]))
        self.chiave = next((i for i in range(len(self.intestazione)) if i not in self.lingue
                            and self._piene(i) >= 0.8 * max(1, usate)), None)

    def _controlla_con_csv(self):
        """The cells read here must be those of Python's csv module (same dialect)."""
        try:
            letti = list(csv.reader(io.StringIO(self.testo, newline=""), delimiter=self.separatore, quotechar='"',
                                    doublequote=True, strict=True))
        except csv.Error as e:
            raise ErroreCSV("CSV non valido: %s" % e) from e
        nostri = [[] if len(r) == 1 and not r[0][3] and not r[0][2] else [c[2] for c in r] for r in self.righe]
        if [r for r in letti if r] != [r for r in nostri if r]:
            raise ErroreCSV("celle diverse da quelle del modulo csv")

    def _cella(self, r, c):
        riga = self.righe[r]
        return riga[c][2] if c < len(riga) else ""

    def _piene(self, c):
        return sum(1 for r in range(1, len(self.righe)) if self._cella(r, c).strip())

    def _con_testo(self, c):
        return sum(1 for r in range(1, len(self.righe)) if adapter_gamemaker.testo_localizzazione(self._cella(r, c)))

    def colonna(self, nome):
        """The index of the column with this name (ignoring case and spaces), or None."""
        cerca = (nome or "").strip().lower()
        return next((i for i, n in enumerate(self.intestazione)
                     if n.strip().lstrip("\ufeff").lower() == cerca), None)

    def sorgente(self, origine="auto"):
        """(indice, codice) of the column to translate: the one of the 'origine' language (with 'auto' English, if
        present, otherwise the first language), the fullest if there is more than one; (None, None) if there is none."""
        base = (origine or "auto").split("-")[0].lower()
        scelte = [i for i, c in self.lingue.items() if c.split("-")[0] == ("en" if base == "auto" else base)]
        if not scelte and base == "auto" and self.lingue:
            scelte = [min(self.lingue)]
        if not scelte:
            return None, None
        i = max(scelte, key=lambda k: (self._con_testo(k), -k))
        return i, self.lingue[i]

    def tabella(self):
        """True if it is a usable translation table: at least two languages, a key column and some text in the
        original column."""
        i, _ = self.sorgente()
        return len(set(self.lingue.values())) >= 2 and self.chiave is not None and i is not None \
            and self._con_testo(i) >= MINIMO_TESTI

    def lingue_piene(self, sorgente=None):
        """The languages whose column is full (at least PIENA of the texts of the 'sorgente' column): they are the
        game's official translations. Empty or almost empty columns (languages planned but not translated) do not
        count."""
        sorgente = self.sorgente()[0] if sorgente is None else sorgente
        totale = self._con_testo(sorgente) if sorgente is not None else 0
        if not totale:
            return set()
        return {c for i, c in self.lingue.items() if self._con_testo(i) >= PIENA * totale}

    def testi(self, colonna):
        """[((riga, colonna), testo)] of the column's texts (\\r\\n line breaks inside a cell become \\n)."""
        uscita = []
        for r in range(1, len(self.righe)):
            if colonna < len(self.righe[r]):
                v = self.righe[r][colonna][2]
                if adapter_gamemaker.testo_localizzazione(v):
                    uscita.append(((r, colonna), v.replace("\r\n", "\n")))
        return uscita

    def chiave_di(self, pos):
        """The value of the key column in the text's line (context for the translator), or ''."""
        return self._cella(pos[0], self.chiave) if self.chiave is not None else ""

    def riferimenti(self, pos, codici):
        """{codice: testo} of the official translations of the same text (the columns of those languages)."""
        uscita = {}
        for i, c in self.lingue.items():
            if c in codici and c not in uscita and i != pos[1]:
                v = self._cella(pos[0], i).strip()
                if v:
                    uscita[c] = v.replace("\r\n", "\n")
        return uscita

    def non_scrivibile(self, tradotto):
        """The characters of the translation that cannot be written in this file without changing how the game reads
        it (separator, quotes, line breaks), or ''."""
        vietati = ""
        if not self.speciali:
            vietati += self.separatore + "\n\r"
        if not self.doppie or not self.virgolette:
            vietati += '"'
        return "".join(c for c in dict.fromkeys(tradotto) if c in vietati)

    # in place of the characters the file does not allow, the ones the games themselves use (Megaloot: ';' for ',')
    SOSTITUTI = {",": ";", ";": ",", "\t": " ", '"': "'"}

    def adatta(self, tradotto):
        """The translation with the characters the file does not allow (see non_scrivibile) replaced the way the
        original texts do: ',' -> ';' in a file split on commas, '"' -> "'". Line breaks stay (they are checked
        separately)."""
        for c in self.non_scrivibile(tradotto):
            sostituto = self.SOSTITUTI.get(c)
            if sostituto and sostituto not in self.non_scrivibile(sostituto):
                tradotto = tradotto.replace(c, sostituto)
        return tradotto

    def _scrivi_cella(self, valore, tra_virgolette):
        if self.virgolette and (tra_virgolette or any(c in valore for c in (self.separatore, '"', "\n", "\r"))):
            return '"' + valore.replace('"', '""') + '"'
        return valore

    def serializza(self, sostituzioni):
        """Bytes of the file with the cells replaced {(riga, colonna): nuovo testo}: the rest identical. Raises
        ErroreCSV if a translation would change how the file is read (see non_scrivibile) or if the file read back
        does not match."""
        pezzi, ultimo, attese = [], 0, {}
        for (r, c), nuovo in sorted(sostituzioni.items(), key=lambda x: self.righe[x[0][0]][x[0][1]][0]):
            inizio, fine, valore, tra_virgolette = self.righe[r][c]
            if self.non_scrivibile(nuovo):
                raise ErroreCSV("riga %d: la traduzione ha caratteri che il file non permette" % (r + 1))
            if "\r\n" in valore:
                nuovo = nuovo.replace("\r\n", "\n").replace("\n", "\r\n")
            attese[(r, c)] = nuovo
            if nuovo == valore:
                continue
            pezzi += [self.testo[ultimo:inizio], self._scrivi_cella(nuovo, tra_virgolette)]
            ultimo = fine
        testo = "".join(pezzi) + self.testo[ultimo:]
        dati = (("\ufeff" if self.bom else "") + testo).encode("utf-8")
        # read back the way the game reads it: in a table split by hand a translation can remove the only cell that
        # was not valid CSV ('"Hero" rises' -> "L'eroe sorge"), and the file would look like a CSV with quotes
        controllo = FileCSV(dati, a_mano=not self.virgolette)
        if (controllo.separatore, controllo.virgolette) != (self.separatore, self.virgolette) or \
                [len(r) for r in controllo.righe] != [len(r) for r in self.righe] or \
                any(controllo.righe[r][c][2] != attese.get((r, c), self.righe[r][c][2])
                    for r in range(len(self.righe)) for c in range(len(self.righe[r]))):
            raise ErroreCSV("il file riletto non torna: non scrivo")
        return dati


def leggi(percorso):
    with open(percorso, "rb") as f:
        return FileCSV(f.read())


def tabella(percorso):
    """The translation table (FileCSV) of the file, or None if the file is not one (or cannot be read)."""
    try:
        if os.path.getsize(percorso) > MASSIMO:
            return None
        f = leggi(percorso)
    except (OSError, ErroreCSV):
        return None
    return f if f.tabella() else None


def tabelle(cartella):
    """{relativo: FileCSV} of the folder's CSV translation tables (read only). Files created by PatchSmith and Godot
    sources are skipped (a .csv with its .csv.import next to it: the game uses the compiled file)."""
    from . import adapter_webjson
    trovate = {}
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in ("node_modules", "__pycache__"))
        for nome in sorted(files):
            if not nome.lower().endswith(ESTENSIONI) or adapter_webjson.MARCATORE in nome or nome + ".import" in files:
                continue
            p = os.path.join(radice, nome)
            f = tabella(adapter_gamemaker.leggi_originale(p, lambda x: x))
            if f:
                trovate[os.path.relpath(p, cartella).replace(os.sep, "/")] = f
    return trovate
