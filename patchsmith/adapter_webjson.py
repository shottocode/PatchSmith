"""Web-json adapter: extracts and reinserts the texts in JSON files (Electron/NW.js/HTML5 games,
RPG Maker MV/MZ and any game that keeps its texts in JSON).

Fields are selected by "path schema", for example rooms[].rewards[].text.
"""
import copy
import json
import os
import re

from .text import is_readable_text

# All the files PatchSmith creates in the game's folder contain this marker in their name.
MARKER = "patchsmith-"
TMP_SUFFIX = ".patchsmith-tmp"

# Last keys that almost always hold technical data or credits, not text to translate.
TECHNICAL_KEYS = {
    "id", "uid", "uuid", "guid", "key", "keys", "type", "kind", "class", "classname", "url", "uri",
    "href", "src", "path", "file", "filename", "image", "img", "icon", "sprite", "texture", "sound",
    "audio", "music", "bgm", "se", "font", "fontface", "color", "colour", "version", "author",
    "author_name", "email", "lang", "language", "locale", "code", "hash", "checksum", "token",
    "format", "mime", "encoding", "event", "action", "method", "script", "eval", "regex", "source",
}
# Keys that END with a technical suffix: achievementId, imageUrl, music_path, ruleType...
_TECHNICAL_SUFFIX = re.compile(
    r"(?:[a-z0-9](?:Id|ID|Ids|IDs|Key|Keys|Url|URL|Uri|Path|File|Color|Colour|Hash|Type|Code)"
    r"|_(?:id|ids|key|keys|url|uri|path|file|color|colour|hash|type|code))$"
)


# --- "Tolerant" JSON: // and /* */ comments and trailing commas (e.g. Another Door: localization/stringsEN.json) ---
# Many games read their JSON with a reader that accepts comments and commas before } or ]. PatchSmith reads them
# removing comments and commas (spaces in their place: positions stay the same) and rewrites them replacing in the
# original text only the literals of the translated strings: comments, spaces, newlines and everything else stay
# identical byte for byte.

_STRING = r'"[^"\\]*(?:\\.[^"\\]*)*"'
_STRING_OR_COMMENT = re.compile(_STRING + r"|//[^\n]*|/\*.*?(?:\*/|\Z)", re.S)
_STRING_OR_COMMA = re.compile(_STRING + r"|,(?=\s*[}\]])", re.S)


def _spaces(piece):
    """The piece covered with spaces, keeping the newlines (same positions and same lines in error messages)."""
    return re.sub(r"[^\r\n]", " ", piece)


def without_comments(text):
    """The JSON text without // and /* */ comments and without commas before } or ] (outside strings), with spaces
    in their place: every remaining character is in the same position as in the original."""
    text = _STRING_OR_COMMENT.sub(lambda m: m.group(0) if m.group(0).startswith('"') else _spaces(m.group(0)), text)
    return _STRING_OR_COMMA.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " ", text)


def read_lenient(text):
    """The data of a JSON text, even with comments and trailing commas. Raises ValueError if it is not JSON."""
    return json.loads(without_comments(text))


_JSON_SPACES = re.compile(r"[ \t\n\r]*")
_DECODER = json.JSONDecoder()


def string_positions(text):
    """{path: (start, end)} of the string literals that are values (not keys) in a JSON text already
    without comments (see without_comments). With repeated keys the last one wins, as with json.loads."""
    output = {}

    def spaces(i):
        return _JSON_SPACES.match(text, i).end()

    def value_(i, path):
        i = spaces(i)
        c = text[i:i + 1]
        if c == "{":
            i = spaces(i + 1)
            if text[i:i + 1] == "}":
                return i + 1
            while True:
                if text[i:i + 1] != '"':
                    raise ValueError("expected key at position %d" % i)
                key, i = json.decoder.scanstring(text, i + 1)
                i = spaces(i)
                if text[i:i + 1] != ":":
                    raise ValueError("':' expected at position %d" % i)
                i = spaces(value_(i + 1, path + (key,)))
                if text[i:i + 1] == ",":
                    i = spaces(i + 1)
                    continue
                if text[i:i + 1] == "}":
                    return i + 1
                raise ValueError("',' or '}' expected at position %d" % i)
        if c == "[":
            i = spaces(i + 1)
            if text[i:i + 1] == "]":
                return i + 1
            n = 0
            while True:
                i = spaces(value_(i, path + (n,)))
                n += 1
                if text[i:i + 1] == ",":
                    i += 1
                    continue
                if text[i:i + 1] == "]":
                    return i + 1
                raise ValueError("',' or ']' expected at position %d" % i)
        if c == '"':
            _, end = json.decoder.scanstring(text, i + 1)
            output[path] = (i, end)
            return end
        try:
            _, end = _DECODER.scan_once(text, i)  # numbers, true, false, null
        except StopIteration:
            raise ValueError("expected value at position %d" % i) from None
        return end

    try:
        value_(0, ())
    except RecursionError:
        raise ValueError("Too deeply nested JSON") from None
    return output


def _at_path(data, path):
    for step in path:
        data = data[step]
    return data


class JSONFile:
    """A JSON file read from disk, with the information to rewrite it in the same format. A file with comments or
    trailing commas (lenient=True) is rewritten replacing only the literals of the changed strings."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            raw = f.read()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        self.lenient = False
        try:
            self.data = json.loads(text)
        except ValueError as e:
            try:
                self.data = read_lenient(text)
            except ValueError:
                raise e from None
            self.lenient = True
            self._text = text
        m = re.search(r"\n([ \t]+)\S", text)
        self.indent = (m.group(1) if m.group(1).startswith("\t") else len(m.group(1))) if m else None
        self.ascii_only = not re.search(r"[^\x00-\x7f]", text) and "\\u" in text
        self.trailing_line_break = text.endswith("\n")

    def serialize(self, data):
        """Same format as the original. Raises ValueError if NaN/Infinity would be needed (invalid JSON)."""
        if self.lenient:
            return self._replace(data)
        if self.indent is None:
            text = json.dumps(data, ensure_ascii=self.ascii_only, separators=(",", ":"), allow_nan=False)
        else:
            text = json.dumps(data, ensure_ascii=self.ascii_only, indent=self.indent, allow_nan=False)
        if self.trailing_line_break:
            text += "\n"
        data_bytes = text.encode("utf-8")
        return (b"\xef\xbb\xbf" + data_bytes) if self.bom else data_bytes

    def _replace(self, data):
        """The original text with only the literals of the changed strings rewritten; the rest (comments, spaces,
        trailing commas, numbers written as they were) stays identical. Raises ValueError if something that is not
        a string changed, or if the result, read back, does not give 'data'."""
        pieces, last = [], 0
        for path, (start, end) in sorted(string_positions(without_comments(self._text)).items(),
                                               key=lambda x: x[1][0]):
            try:
                before, after = _at_path(self.data, path), _at_path(data, path)
            except (KeyError, IndexError, TypeError):
                raise ValueError("structure changed in %s" % (path,)) from None
            if after == before:
                continue
            if not isinstance(after, str):
                raise ValueError("non-string value in %s" % (path,))
            pieces += [self._text[last:start], json.dumps(after, ensure_ascii=self.ascii_only)]
            last = end
        text = "".join(pieces) + self._text[last:]
        if read_lenient(text) != data:
            raise ValueError("the rewritten file doesn't match: something other than a string changed")
        data_bytes = text.encode("utf-8")
        return (b"\xef\xbb\xbf" + data_bytes) if self.bom else data_bytes


def _refuse_constant(name):
    raise ValueError("value not allowed in standard JSON: %s" % name)


def reread_strict(data_bytes, lenient=False):
    """Reads back with the rules of JSON.parse (no NaN/Infinity). Raises ValueError if invalid. lenient: the
    original file had comments or trailing commas (JSONFile.lenient), which the game accepts: removed first."""
    text = data_bytes.decode("utf-8-sig")
    return json.loads(without_comments(text) if lenient else text, parse_constant=_refuse_constant)


def leaves(data, path=(), pattern=""):
    """Yields (path_tuple, schema, value) for every string of the JSON."""
    if isinstance(data, dict):
        for k, v in data.items():
            yield from leaves(v, path + (k,), (pattern + "." + k) if pattern else k)
    elif isinstance(data, list):
        for i, v in enumerate(data):
            yield from leaves(v, path + (i,), pattern + "[]")
    elif isinstance(data, str):
        yield path, pattern, data


def schema_stats(data, max_examples=3):
    """For each schema: how many strings, how many readable, characters, examples."""
    stat = {}
    for _, pattern, value_ in leaves(data):
        s = stat.setdefault(pattern, {"strings": 0, "readable": 0, "chars": 0, "examples": []})
        s["strings"] += 1
        if is_readable_text(value_):
            s["readable"] += 1
            s["chars"] += len(value_)
            if len(s["examples"]) < max_examples:
                s["examples"].append(value_[:90].replace("\n", " / "))
    return stat


def last_key(pattern):
    return pattern.replace("[]", "").rsplit(".", 1)[-1]


def technical_key(pattern):
    key = last_key(pattern)
    return key.lower() in TECHNICAL_KEYS or bool(_TECHNICAL_SUFFIX.search(key))


def suggest_schemas(stat):
    """Splits the schemas into (include, exclude) with generic rules."""
    include, exclude = [], []
    for pattern, s in sorted(stat.items()):
        if s["readable"] == 0:
            continue
        if technical_key(pattern):
            exclude.append(pattern)
        else:
            include.append(pattern)
    return include, exclude


def extract(data, include, readable=is_readable_text):
    """[(path_tuple, text)] of the strings to translate ('readable' decides which values are text)."""
    include = set(include)
    return [(p, v) for p, pattern, v in leaves(data) if pattern in include and readable(v)]


def apply(data, replacements):
    """Copy of the data with the strings replaced. replacements: {path_tuple: new_text}."""
    new_items = copy.deepcopy(data)
    for path, text in replacements.items():
        tree_node = new_items
        for step in path[:-1]:
            tree_node = tree_node[step]
        tree_node[path[-1]] = text
    return new_items


def verify_structure(original, new, allowed, path=()):
    """List of problems: the structure must be identical, only the allowed strings change."""
    problems = []
    if type(original) is not type(new):
        return ["different type in %s" % (path,)]
    if isinstance(original, dict):
        if list(original.keys()) != list(new.keys()):
            return ["different keys in %s" % (path,)]
        for k in original:
            problems += verify_structure(original[k], new[k], allowed, path + (k,))
    elif isinstance(original, list):
        if len(original) != len(new):
            return ["different lengths in %s" % (path,)]
        for i, (a, b) in enumerate(zip(original, new)):
            problems += verify_structure(a, b, allowed, path + (i,))
    elif original != new and path not in allowed:
        problems.append("value changed without permission in %s" % (path,))
    return problems[:20]


def write_atomic(path, data_bytes):
    temp_file = path + TMP_SUFFIX
    try:
        with open(temp_file, "wb") as f:
            f.write(data_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, path)
    except BaseException:  # no temporary file left next to the game's file
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass
        raise
