"""Localization files "in lines with an id" (e.g. Dokimon/GameMaker: Localization/english_ui.txt).

Two kinds of file, recognized by their content:

  LINES (ui, scene...)                   BLOCKS (npc, char, trainer...)
    // comment: left untouched             12                <- line with only the id
    0 Fight                                Hello there!      <- one line per text box
    5  do next?   (separator: space,       Bye!
                   tab or ideographic space) .               <- end of the section ('.', '. ', '\uff0e')
                                           Second part...    <- another section of the same entry
                                           .                    (e.g. trainer: before/after the battle)

Only the text is translated: ids, separators, comments, empty lines, section ends and line breaks (CRLF)
stay identical byte for byte. Lines the format does not allow for (errors in the original files)
are left untouched. Each translation is checked on its own (valida): one that would change the way
the game reads the file stays in the original, without blocking the others. After writing, the file
is read back and its structure compared.
"""
import re

_SEPARATORS = " \t\u3000"
_ID_TEXT = re.compile(r"^(\d+(?:\.\d+)*)([ \t\u3000])(.*)$", re.S)
_ID_ALONE = re.compile(r"^\d+(?:\.\d+)*[ \t\u3000]*$")
_CLOSERS = {".", "\uff0e"}  # '.' and the full-width period (also with spaces around it)
# numeric prefix that games compare with the id they look for (e.g. Dokimon: every line is a candidate id)
_NUMBER_PREFIX = re.compile(r"^[\s\u3000\ufeff]*[-+]?(?:\d{0,30}\.)?\d{1,30}")
BOM = "\ufeff"
# control characters and Unicode line separators: a game could read them as line breaks
_CONTROL_CHARS = frozenset([chr(c) for c in range(32) if c not in (9, 10)] + ["\x7f", "\x85", "\u2028", "\u2029"])


class LinesError(ValueError):
    pass


def _split(text):
    """Lines with their line breaks; splits only on \\n (not on other Unicode separators)."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _split_end(line):
    body = line.rstrip("\r\n")
    return body, line[len(body):]


def _is_text(s):
    return any(c.isalpha() for c in s)


def _comment_or_empty(body):
    return body.startswith("//") or not body.strip()


def _is_closer(body):
    return body.strip(" \t\u3000") in _CLOSERS


def _number_prefix(s):
    m = _NUMBER_PREFIX.match(s)
    return m.group(0).strip(" \t\u3000\ufeff") if m else ""


def mode(lines):
    """'blocchi' if the file is made of multi-line entries closed by '.', otherwise 'righe'."""
    bodies = [_split_end(r)[0] for r in lines]
    periods = sum(1 for c in bodies if _is_closer(c))
    line_entries = sum(1 for c in bodies if _ID_TEXT.match(c) and _ID_TEXT.match(c).group(3).strip())
    return "blocchi" if periods >= 3 and periods >= line_entries else "righe"


def is_valid(pos, original, translated):
    """True if the translation can replace the original without changing how the game reads the file."""
    if "\r" in translated or any(c in _CONTROL_CHARS for c in translated):
        return False
    if pos[0] == "riga":
        return ("\n" not in translated and _is_text(translated) and translated.strip(_SEPARATORS) != ""
                and not translated.lstrip(_SEPARATORS).startswith("//"))
    lines_o, lines_t = original.split("\n"), translated.split("\n")
    if len(lines_o) != len(lines_t) or not _is_text(translated):
        return False
    for o, t in zip(lines_o, lines_t):
        if o.strip() and not t.strip():
            return False  # a text line cannot become empty (the first one would open another entry)
        if _is_closer(t) or _ID_ALONE.match(t) or t.startswith("//"):
            return False
        if _number_prefix(t) != _number_prefix(o):
            return False  # a line starting with a different number could "steal" another entry
    return True


class LinesFile:
    """File read: lines (with their line breaks), translatable text units and fixed structure."""

    def __init__(self, data_bytes):
        try:
            text = data_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            raise LinesError("il file non e' UTF-8: %s" % e) from e
        self.bom = text.startswith(BOM)
        if self.bom:
            text = text[1:]
        self.lines = _split(text)
        self.mode = mode(self.lines)
        self.units, self.shape, self.anomalies = (_analyze_blocks if self.mode == "blocchi"
                                                else _analyze_lines)(self.lines)

    def texts(self):
        """[(posizione, testo)]: posizione = ('riga', i) or ('blocco', i, j) for lines i..j-1."""
        return list(self.units)

    def _write(self, replacements):
        lines = list(self.lines)
        for pos, new in replacements.items():
            if pos[0] == "riga":
                i = pos[1]
                body, end = _split_end(lines[i])
                m = _ID_TEXT.match(body)
                if not m or "\n" in new or "\r" in new:
                    raise LinesError("traduzione non valida per la riga %d" % (i + 1))
                lines[i] = m.group(1) + m.group(2) + new + end
            else:
                _, i, j = pos
                new_lines = new.split("\n")
                if len(new_lines) != j - i or any("\r" in r for r in new_lines):
                    raise LinesError("la traduzione delle righe %d-%d ha un numero di righe diverso" % (i + 1, j))
                for k, r in enumerate(new_lines):
                    lines[i + k] = r + _split_end(lines[i + k])[1]
        data = ((BOM if self.bom else "") + "".join(lines)).encode("utf-8")
        checks = LinesFile(data)
        if (checks.mode, checks.shape) != (self.mode, self.shape) or \
                [p for p, _ in checks.units] != [p for p, _ in self.units]:
            raise LinesError("la struttura del file cambierebbe: non scrivo")
        return data

    def serialize(self, replacements):
        """Bytes of the file with the texts replaced {posizione: nuovo testo}; structure verified."""
        return self._write(replacements)

    def serialize_robust(self, replacements):
        """(bytes, rejected positions): the translations that would change the structure stay in the original."""
        originals = dict(self.units)
        good_ones = {p: t for p, t in replacements.items() if is_valid(p, originals.get(p, ""), t)}
        rejected = [p for p in replacements if p not in good_ones]
        try:
            return self._write(good_ones), rejected
        except LinesError:
            pass
        # rare case: check one translation at a time
        kept = {}
        for p, t in good_ones.items():
            try:
                self._write({p: t})
                kept[p] = t
            except LinesError:
                rejected.append(p)
        return self._write(kept), rejected


def _analyze_lines(lines):
    units, shape, anomalies = [], [], []
    for i, line in enumerate(lines):
        body, end = _split_end(line)
        m = _ID_TEXT.match(body)
        if _comment_or_empty(body) or _ID_ALONE.match(body):
            shape.append(("fisso", line))
        elif m:
            shape.append(("voce", m.group(1), m.group(2), end))
            if _is_text(m.group(3)) and not m.group(3).lstrip(_SEPARATORS).startswith("//"):
                units.append((("riga", i), m.group(3)))
        else:
            shape.append(("fisso", line))
            anomalies.append(i + 1)
    return units, shape, anomalies


def _analyze_blocks(lines):
    units, shape, anomalies = [], [], []
    i, n = 0, len(lines)
    while i < n:
        body, _ = _split_end(lines[i])
        if _comment_or_empty(body) or _ID_ALONE.match(body) or _is_closer(body):
            shape.append(("fisso", lines[i]))
            i += 1
            continue
        # text section: up to the closing line
        j = i
        while j < n and not _is_closer(_split_end(lines[j])[0]):
            j += 1
        if j >= n:  # section without an end: left untouched
            for k in range(i, n):
                shape.append(("fisso", lines[k]))
                anomalies.append(k + 1)
            break
        content = [_split_end(r)[0] for r in lines[i:j]]
        shape.append(("sezione", j - i, tuple(_split_end(r)[1] for r in lines[i:j]), lines[j],
                      tuple(_number_prefix(c) for c in content)))
        if _is_text("\n".join(content)) and not any(c.startswith("//") for c in content):
            units.append((("blocco", i, j), "\n".join(content)))
        else:
            shape.append(("contenuto", tuple(lines[i:j])))
        i = j + 1
    return units, shape, anomalies


def read_file(path):
    with open(path, "rb") as f:
        return LinesFile(f.read())


def looks_like_localization(path, minimum=5):
    """True if the file is in the 'lines with an id' format and has at least 'minimo' texts."""
    try:
        f = read_file(path)
    except (LinesError, OSError):
        return False
    return len(f.units) >= minimum and len(f.anomalies) <= max(3, len(f.units) // 20)
