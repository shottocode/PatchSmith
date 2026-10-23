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

SEPARATORS = (",", ";", "\t")
MAXIMUM = 20 * 1024 * 1024
MIN_TEXTS = 3  # lines with a text in the original column, to be a translation table
FULL = 0.5  # a language is present (official translation) if its column has at least half the texts of the original
EXTENSIONS = (".csv", ".tsv")


class CSVError(ValueError):
    pass


def column_language(name):
    """The language code of a column name ('english', 'en', 'English (US)', 'koreana', 'schinese',
    'textEN'...), or None ('id', 'key', 'notes'...)."""
    n = (name or "").strip().lstrip("\ufeff").strip()
    if not n or len(n) > 40 or "/" in n or "\\" in n:
        return None
    return adapter_gamemaker.path_language(re.sub(r"[()\[\]:,]", " ", n).strip())


def _csv_cells(text, sep):
    """[[(inizio, fine, valore, tra_virgolette)]] of the lines, with the CSV rules (quotes, "" inside a quoted cell,
    line breaks inside quotes). Raises CSVError if the quotes are not used this way."""
    lines, line, i, n = [], [], 0, len(text)
    cell_end = re.compile("[%s\r\n]" % re.escape(sep))
    while n:
        if text.startswith('"', i):
            j, pieces = i + 1, []
            while True:
                k = text.find('"', j)
                if k < 0:
                    raise CSVError("virgolette non chiuse")
                pieces.append(text[j:k])
                if text.startswith('"', k + 1):
                    pieces.append('"')
                    j = k + 2
                    continue
                end = k + 1
                break
            if end < n and text[end] not in sep + "\r\n":
                raise CSVError("testo dopo le virgolette di chiusura")
            line.append((i, end, "".join(pieces), True))
        else:
            m = cell_end.search(text, i)
            end = m.start() if m else n
            line.append((i, end, text[i:end], False))
        if end >= n:
            lines.append(line)
            break
        if text[end] == sep:
            i = end + 1
            continue
        lines.append(line)
        line = []
        i = end + (2 if text.startswith("\r\n", end) else 1)
        if i >= n:
            break
    return lines


def _simple_cells(text, sep):
    """The lines split the way a game that reads the CSV by hand does: at every line break and at every separator
    (quotes are text)."""
    lines, i, n = [], 0, len(text)
    for m in re.finditer(r"\r\n|\n|\r|\Z", text):
        if m.start() == n and i >= n:
            break
        line, start = [], i
        for piece in text[i:m.start()].split(sep):
            line.append((start, start + len(piece), piece, False))
            start += len(piece) + 1
        lines.append(line)
        i = m.end()
        if m.start() == n:
            break
    return lines


def _separator(text):
    """The table's separator: the one that appears most in the first line (outside quotes)."""
    before = re.split(r"\r\n|\n|\r", text, 1)[0]
    before = re.sub(r'"[^"]*"', "", before)
    occurrences = {s: before.count(s) for s in SEPARATORS}
    best = max(SEPARATORS, key=lambda s: occurrences[s])
    if not occurrences[best]:
        raise CSVError("nessun separatore nella prima riga")
    return best


class CSVFile:
    """A CSV table that was read: cells with their position in the text, header, language columns."""

    def __init__(self, data_bytes, by_hand=False):
        """a_mano=True: the lines are split the way a game that reads the CSV by hand does, even if the file is a
        valid CSV (to read back a file rewritten like the original, see serializza)."""
        try:
            text = data_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise CSVError("il file non e' UTF-8: %s" % e) from e
        self.bom = text.startswith("\ufeff")
        self.text = text[1:] if self.bom else text
        self.separator = _separator(self.text)
        try:
            if by_hand:
                raise CSVError("diviso a mano")
            self.lines = _csv_cells(self.text, self.separator)
            self.quotes = True
        except CSVError:
            self.lines = _simple_cells(self.text, self.separator)
            self.quotes = False
        quoted_cells = [c[2] for r in self.lines for c in r if c[3]]
        # the game really reads the CSV with quotes only if the file needs them (separator or line break inside a
        # cell); otherwise the game might split by hand: no separators or line breaks in the translations
        self.has_special = any(self.separator in v or "\n" in v or "\r" in v for v in quoted_cells)
        self.doubled = any('"' in v for v in quoted_cells)
        if self.quotes:
            self._check_with_csv()
        self.header = [c[2] for c in self.lines[0]] if self.lines else []
        self.languages = {i: column_language(n) for i, n in enumerate(self.header)}
        self.languages = {i: c for i, c in self.languages.items() if c}
        # the key: filled in almost all the lines that have something (the empty lines, which many tables use to
        # separate groups of texts, do not count)
        used_rows = sum(1 for r in range(1, len(self.lines)) if any(c[2].strip() for c in self.lines[r]))
        self.key = next((i for i in range(len(self.header)) if i not in self.languages
                            and self._filled(i) >= 0.8 * max(1, used_rows)), None)

    def _check_with_csv(self):
        """The cells read here must be those of Python's csv module (same dialect)."""
        try:
            read_items = list(csv.reader(io.StringIO(self.text, newline=""), delimiter=self.separator, quotechar='"',
                                    doublequote=True, strict=True))
        except csv.Error as e:
            raise CSVError("CSV non valido: %s" % e) from e
        ours = [[] if len(r) == 1 and not r[0][3] and not r[0][2] else [c[2] for c in r] for r in self.lines]
        if [r for r in read_items if r] != [r for r in ours if r]:
            raise CSVError("celle diverse da quelle del modulo csv")

    def _cell(self, r, c):
        line = self.lines[r]
        return line[c][2] if c < len(line) else ""

    def _filled(self, c):
        return sum(1 for r in range(1, len(self.lines)) if self._cell(r, c).strip())

    def _with_text(self, c):
        return sum(1 for r in range(1, len(self.lines)) if adapter_gamemaker.localization_text(self._cell(r, c)))

    def column(self, name):
        """The index of the column with this name (ignoring case and spaces), or None."""
        search = (name or "").strip().lower()
        return next((i for i, n in enumerate(self.header)
                     if n.strip().lstrip("\ufeff").lower() == search), None)

    def source(self, origin="auto"):
        """(indice, codice) of the column to translate: the one of the 'origine' language (with 'auto' English, if
        present, otherwise the first language), the fullest if there is more than one; (None, None) if there is none."""
        base = (origin or "auto").split("-")[0].lower()
        choices = [i for i, c in self.languages.items() if c.split("-")[0] == ("en" if base == "auto" else base)]
        if not choices and base == "auto" and self.languages:
            choices = [min(self.languages)]
        if not choices:
            return None, None
        i = max(choices, key=lambda k: (self._with_text(k), -k))
        return i, self.languages[i]

    def table(self):
        """True if it is a usable translation table: at least two languages, a key column and some text in the
        original column."""
        i, _ = self.source()
        return len(set(self.languages.values())) >= 2 and self.key is not None and i is not None \
            and self._with_text(i) >= MIN_TEXTS

    def full_languages(self, source=None):
        """The languages whose column is full (at least FULL of the texts of the 'sorgente' column): they are the
        game's official translations. Empty or almost empty columns (languages planned but not translated) do not
        count."""
        source = self.source()[0] if source is None else source
        total = self._with_text(source) if source is not None else 0
        if not total:
            return set()
        return {c for i, c in self.languages.items() if self._with_text(i) >= FULL * total}

    def texts(self, column):
        """[((riga, colonna), testo)] of the column's texts (\\r\\n line breaks inside a cell become \\n)."""
        output = []
        for r in range(1, len(self.lines)):
            if column < len(self.lines[r]):
                v = self.lines[r][column][2]
                if adapter_gamemaker.localization_text(v):
                    output.append(((r, column), v.replace("\r\n", "\n")))
        return output

    def key_of(self, pos):
        """The value of the key column in the text's line (context for the translator), or ''."""
        return self._cell(pos[0], self.key) if self.key is not None else ""

    def references(self, pos, codes):
        """{codice: testo} of the official translations of the same text (the columns of those languages)."""
        output = {}
        for i, c in self.languages.items():
            if c in codes and c not in output and i != pos[1]:
                v = self._cell(pos[0], i).strip()
                if v:
                    output[c] = v.replace("\r\n", "\n")
        return output

    def unwritable(self, translated):
        """The characters of the translation that cannot be written in this file without changing how the game reads
        it (separator, quotes, line breaks), or ''."""
        forbidden = ""
        if not self.has_special:
            forbidden += self.separator + "\n\r"
        if not self.doubled or not self.quotes:
            forbidden += '"'
        return "".join(c for c in dict.fromkeys(translated) if c in forbidden)

    # in place of the characters the file does not allow, the ones the games themselves use (Megaloot: ';' for ',')
    SUBSTITUTES = {",": ";", ";": ",", "\t": " ", '"': "'"}

    def adapt(self, translated):
        """The translation with the characters the file does not allow (see unwritable) replaced the way the
        original texts do: ',' -> ';' in a file split on commas, '"' -> "'". Line breaks stay (they are checked
        separately)."""
        for c in self.unwritable(translated):
            substitute = self.SUBSTITUTES.get(c)
            if substitute and substitute not in self.unwritable(substitute):
                translated = translated.replace(c, substitute)
        return translated

    def _write_cell(self, value_, quoted):
        if self.quotes and (quoted or any(c in value_ for c in (self.separator, '"', "\n", "\r"))):
            return '"' + value_.replace('"', '""') + '"'
        return value_

    def serialize(self, replacements):
        """Bytes of the file with the cells replaced {(riga, colonna): nuovo testo}: the rest identical. Raises
        CSVError if a translation would change how the file is read (see unwritable) or if the file read back
        does not match."""
        pieces, last, expectations = [], 0, {}
        for (r, c), new in sorted(replacements.items(), key=lambda x: self.lines[x[0][0]][x[0][1]][0]):
            start, end, value_, quoted = self.lines[r][c]
            if self.unwritable(new):
                raise CSVError("riga %d: la traduzione ha caratteri che il file non permette" % (r + 1))
            if "\r\n" in value_:
                new = new.replace("\r\n", "\n").replace("\n", "\r\n")
            expectations[(r, c)] = new
            if new == value_:
                continue
            pieces += [self.text[last:start], self._write_cell(new, quoted)]
            last = end
        text = "".join(pieces) + self.text[last:]
        data = (("\ufeff" if self.bom else "") + text).encode("utf-8")
        # read back the way the game reads it: in a table split by hand a translation can remove the only cell that
        # was not valid CSV ('"Hero" rises' -> "L'eroe sorge"), and the file would look like a CSV with quotes
        checks = CSVFile(data, by_hand=not self.quotes)
        if (checks.separator, checks.quotes) != (self.separator, self.quotes) or \
                [len(r) for r in checks.lines] != [len(r) for r in self.lines] or \
                any(checks.lines[r][c][2] != expectations.get((r, c), self.lines[r][c][2])
                    for r in range(len(self.lines)) for c in range(len(self.lines[r]))):
            raise CSVError("il file riletto non torna: non scrivo")
        return data


def read_file(path):
    with open(path, "rb") as f:
        return CSVFile(f.read())


def table(path):
    """The translation table (FileCSV) of the file, or None if the file is not one (or cannot be read)."""
    try:
        if os.path.getsize(path) > MAXIMUM:
            return None
        f = read_file(path)
    except (OSError, CSVError):
        return None
    return f if f.table() else None


def tables(folder):
    """{relativo: CSVFile} of the folder's CSV translation tables (read only). Files created by PatchSmith and Godot
    sources are skipped (a .csv with its .csv.import next to it: the game uses the compiled file)."""
    from . import adapter_webjson
    matches = {}
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.lower() not in ("node_modules", "__pycache__"))
        for name in sorted(files):
            if not name.lower().endswith(EXTENSIONS) or adapter_webjson.MARKER in name or name + ".import" in files:
                continue
            p = os.path.join(root, name)
            f = table(adapter_gamemaker.read_original(p, lambda x: x))
            if f:
                matches[os.path.relpath(p, folder).replace(os.sep, "/")] = f
    return matches
