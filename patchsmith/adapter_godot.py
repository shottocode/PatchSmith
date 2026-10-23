"""Godot 4 adapter: games that use Godot's translations (.translation tables imported from CSV or PO).

The game is not touched: the data package (PCK, also embedded in the executable) is only read. PatchSmith
reads the table of the source language (OptimizedTranslation: keys in the form of hashes, texts compressed with
smaz; or Translation with the message dictionary), translates the texts and puts next to the executable:
- override.cfg: the project settings that Godot reads at startup from the executable's folder (the way
  Godot provides to change them without touching the game), with a PatchSmith autoload;
- patchsmith-translation.gd: the autoload script, which at startup replaces the table of the source language
  with a "bridge" (first the original text from the game's table, then its translation): with the source
  language chosen in the game (usually English) the texts appear translated; it also adds the target language;
- patchsmith-translation.json: the translations (original text -> translated).
Everything is listed with its fingerprint in patchsmith-godot.json; "Restore originals" removes the files (and puts
back an override.cfg of the game, if there was one).

Encrypted PCK (key in the game's engine): developers' protection, it is not opened.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import struct

from .adapter_webjson import write_atomic

MANIFEST = "patchsmith-godot.json"
OVERRIDE = "override.cfg"
SCRIPT = "patchsmith-traduzione.gd"
DICTIONARY = "patchsmith-traduzione.json"
AUTOLOAD = "PatchsmithTraduzione"
_LANGUAGE_IN_NAME = re.compile(r"\.([A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,4})?)\.translation$")


class GodotError(Exception):
    pass


# --- data package (PCK) ---------------------------------------------------------------------------------------------

class Pck:
    """The Godot 4 PCK (.pck file or embedded at the end of the executable): list of files and reading."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            start = 0
            if f.read(4) != b"GDPC":  # embedded: at the end of the executable [size u64]["GDPC"]
                f.seek(0, 2)
                end = f.tell()
                if end < 16:
                    raise GodotError("this is not a Godot package")
                f.seek(end - 12)
                size = struct.unpack("<Q", f.read(8))[0]
                if f.read(4) != b"GDPC" or size + 12 > end:
                    raise GodotError("no Godot package included in the executable")
                start = end - 12 - size
                f.seek(start)
                if f.read(4) != b"GDPC":
                    raise GodotError("invalid Godot package")
            self.fmt, self.major, self.minor, self.patch = struct.unpack("<4I", f.read(16))
            if self.fmt < 2:
                raise GodotError("Godot package %d (format %d): for now PatchSmith only reads Godot 4"
                                  % (self.major, self.fmt))
            flags, base = struct.unpack("<IQ", f.read(12))
            self.encrypted = bool(flags & 1)
            if self.encrypted:
                raise GodotError("the game's file list is encrypted")
            relative = bool(flags & 2)
            f.read(64)
            self.file = {}
            for _ in range(struct.unpack("<I", f.read(4))[0]):
                length = struct.unpack("<I", f.read(4))[0]
                name = f.read(length).rstrip(b"\0").decode("utf-8", "replace")
                position, size = struct.unpack("<QQ", f.read(16))
                f.read(16)  # md5
                fl = struct.unpack("<I", f.read(4))[0]
                if name.startswith("res://"):
                    name = name[6:]
                self.file[name] = ((start + base if relative else start) + position, size, fl)

    def version(self):
        return "%d.%d.%d" % (self.major, self.minor, self.patch)

    def read_file(self, name):
        position, size, fl = self.file[name]
        if fl & 1:
            raise GodotError("%s is encrypted" % name)
        with open(self.path, "rb") as f:
            f.seek(position)
            return f.read(size)


def find_pck(folder):
    """(path of the PCK, folder of the executable) of the game: embedded in an .exe or in a .pck next to it; or None."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return None
    for name in [n for n in names if n.lower().endswith(".pck")] + [n for n in names if n.lower().endswith(".exe")]:
        p = os.path.join(folder, name)
        try:
            Pck(p)
        except (GodotError, OSError, struct.error):
            continue
        return p, folder
    return None


# --- translation tables -------------------------------------------------------------------------------------------

_SMAZ = [" ", "the", "e", "t", "a", "of", "o", "and", "i", "n", "s", "e ", "r", " th", " t", "in", "he", "th", "h",
         "he ", "to", "\r\n", "l", "s ", "d", " a", "an", "er", "c", " o", "d ", "on", " of", "re", "of ", "t ", ", ",
         "is", "u", "at", "   ", "n ", "or", "which", "f", "m", "as", "it", "that", "\n", "was", "en", "  ", " w",
         "es", " an", " i", "\r", "f ", "g", "p", "nd", " s", "nd ", "ed ", "w", "ed", "http://", "for", "te", "ing",
         "y ", "The", " c", "ti", "r ", "his", "st", " in", "ar", "nt", ",", " to", "y", "ng", " h", "with", "le",
         "al", "to ", "b", "ou", "be", "were", " b", "se", "o ", "ent", "ha", "ng ", "their", "\"", "hi", "from",
         " f", "in ", "de", "ion", "me", "v", ".", "ve", "all", "re ", "ri", "ro", "is ", "co", "f t", "are", "ea",
         ". ", "her", " m", "er ", " p", "es ", "by", "they", "di", "ra", "ic", "not", "s, ", "d t", "at ", "ce",
         "la", "h ", "ne", "as ", "tio", "on ", "n t", "io", "we", " a ", "om", ", a", "s o", "ur", "li", "ll", "ch",
         "had", "this", "e t", "g ", "e\r\n", " wh", "ere", " co", "e o", "a ", "us", " d", "ss", "\n\r\n", "\r\n\r",
         "=\"", " be", " e", "s a", "ma", "one", "t t", "or ", "but", "el", "so", "l ", "e s", "s,", "no", "ter",
         " wa", "iv", "ho", "e a", " r", "hat", "s t", "ns", "ch ", "wh", "tr", "ut", "/", "have", "ly ", "ta", " ha",
         " on", "tha", "-", " l", "ati", "en ", "pe", " re", "there", "ass", "si", " fo", "wa", "ec", "our", "who",
         "its", "z", "fo", "rs", ">", "ot", "un", "<", "im", "th ", "nc", "ate", "><", "ver", "ad", " we", "ly", "ee",
         " n", "id", " cl", "ac", "il", "</", "rt", " wi", "div", "e, ", " it", "whi", " ma", "ge", "x", "e c", "men",
         ".com"]  # the smaz dictionary (thirdparty/misc/smaz.c in Godot)


def _smaz(data):
    output, i = bytearray(), 0
    while i < len(data):
        c = data[i]
        if c == 254:
            output.append(data[i + 1])
            i += 2
        elif c == 255:
            n = data[i + 1] + 1
            output += data[i + 2:i + 2 + n]
            i += 2 + n
        else:
            output += _SMAZ[c].encode("latin-1")
            i += 1
    return bytes(output)


class _Reader:
    def __init__(self, data, o=0):
        self.d, self.o = data, o

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.d, self.o)[0]
        self.o += 8
        return v

    def read_string(self):
        n = self.u32() & 0x7FFFFFFF
        s = self.d[self.o:self.o + n].rstrip(b"\0").decode("utf-8", "replace")
        self.o += n
        return s

    def variant(self):
        """Only the types needed for the translation tables; None for the others (ValueError if their length is
        unknown)."""
        kind = self.u32()
        if kind == 1:  # nil
            return None
        if kind == 2:  # bool
            return bool(self.u32())
        if kind == 3:  # int
            return struct.unpack("<i", struct.pack("<I", self.u32()))[0]
        if kind == 40:  # int64
            return self.u64()
        if kind in (5, 44):  # String, StringName
            return self.read_string()
        if kind == 26:  # Dictionary
            n = self.u32() & 0x7FFFFFFF
            return {self.variant(): self.variant() for _ in range(n)}
        if kind == 30:  # Array
            n = self.u32() & 0x7FFFFFFF
            return [self.variant() for _ in range(n)]
        if kind == 31:  # PackedByteArray (aligned to 4)
            n = self.u32()
            v = self.d[self.o:self.o + n]
            self.o += n + (4 - n % 4) % 4
            return v
        if kind == 32:  # PackedInt32Array
            n = self.u32()
            v = list(struct.unpack_from("<%di" % n, self.d, self.o))
            self.o += 4 * n
            return v
        if kind == 34:  # PackedStringArray
            return [self.read_string() for _ in range(self.u32())]
        raise ValueError("unexpected type %d" % kind)


def read_resource(data):
    """(type, {property: value}) of the last internal resource of a binary Godot 4 .translation file."""
    if data[:4] != b"RSRC":
        raise GodotError("it's not a binary resource from Godot")
    r = _Reader(data, 4)
    if r.u32():
        raise GodotError("big-endian resource: not supported")
    r.u32()  # real64
    major = r.u32()
    r.u32()  # minor
    r.u32()  # format
    r.read_string()  # main type
    r.u64()  # importmd
    flags = r.u32()
    r.u64()  # uid
    if flags & 4:  # FORMAT_FLAG_HAS_SCRIPT_CLASS
        r.read_string()
    r.o += 4 * 11  # reserved fields
    names = [r.read_string() for _ in range(r.u32())]
    for _ in range(r.u32()):  # external resources
        r.read_string()
        r.read_string()
        if major >= 4:
            r.read_string()  # uid
    internal_res = [(r.read_string(), r.u64()) for _ in range(r.u32())]
    if not internal_res:
        raise GodotError("no resources found in the file")
    r.o = internal_res[-1][1]
    kind = r.read_string()
    properties = {}
    for _ in range(r.u32()):
        name = names[r.u32() & 0x7FFFFFFF]
        properties[name] = r.variant()
    return kind, properties


def table_texts(data):
    """(language, [texts]) of a translation table (OptimizedTranslation or Translation)."""
    kind, p = read_resource(data)
    language = p.get("locale") or "en"  # (en is the default value: Godot does not write it)
    if kind == "OptimizedTranslation":
        ht, bt, st = p.get("hash_table") or [], p.get("bucket_table") or [], p.get("strings") or b""
        texts = []
        for b in ht:
            if b < 0 or b >= len(bt):
                continue
            for j in range(bt[b]):
                _key, start, compressed, whole = bt[b + 2 + 4 * j:b + 6 + 4 * j]
                raw = st[start:start + compressed]
                text = raw if compressed == whole else _smaz(raw)
                texts.append(text.split(b"\0", 1)[0].decode("utf-8", "replace"))
        return language, texts
    if kind == "Translation":
        messages = p.get("messages") or {}
        return language, [v for v in messages.values() if isinstance(v, str)]
    raise GodotError("unexpected table of type %s" % kind)


def tables(pck):
    """{language: path in the PCK} of the translation tables (from the name, e.g. translations.en.translation)."""
    output = {}
    for name in pck.file:
        m = _LANGUAGE_IN_NAME.search(name)
        if m and not name.startswith(".godot/"):
            output.setdefault(m.group(1).replace("_", "-"), name)
    return output


# --- installation ----------------------------------------------------------------------------------------------------

SCRIPT_GD = '''extends Node
## Patchsmith: traduzione di questo gioco (aggiunta da Patchsmith: "Ripristina originali" in Patchsmith la toglie).
## Prende il posto della tabella della lingua indicata in patchsmith-traduzione.json ("lingua", di solito "en"):
## con quella lingua scelta nel gioco i testi compaiono tradotti. Aggiunge anche le lingue in "aggiunte".

class Ponte extends Translation:
	var originale: Translation
	var testi := {}

	func _traduci(sorgente: String, chiave: StringName) -> StringName:
		if sorgente.is_empty():
			sorgente = String(chiave)
		var t = testi.get(sorgente)
		return StringName(t) if t != null else StringName(sorgente)

	func _get_message(src_message: StringName, context: StringName) -> StringName:
		return _traduci(String(originale.get_message(src_message, context)), src_message)

	func _get_plural_message(src_message: StringName, src_plural_message: StringName, n: int, context: StringName) -> StringName:
		return _traduci(String(originale.get_plural_message(src_message, src_plural_message, n, context)), src_message)

var _fatto := false

func _enter_tree() -> void:
	_sostituisci()

func _ready() -> void:
	if not _fatto:
		_sostituisci.call_deferred()

func _sostituisci() -> void:
	if _fatto:
		return
	var f := FileAccess.open("res://patchsmith-traduzione.json", FileAccess.READ)
	if f == null:
		return
	var dati = JSON.parse_string(f.get_as_text())
	if typeof(dati) != TYPE_DICTIONARY:
		return
	var lingua: String = dati.get("lingua", "en")
	var originale := TranslationServer.get_translation_object(lingua)
	if originale == null or originale is Ponte:
		return
	_fatto = true
	var testi: Dictionary = dati.get("testi", {})
	TranslationServer.remove_translation(originale)
	var lingue: Array = [lingua]
	lingue.append_array(dati.get("aggiunte", []))
	for l in lingue:
		var ponte := Ponte.new()
		ponte.originale = originale
		ponte.testi = testi
		ponte.locale = l
		TranslationServer.add_translation(ponte)
	TranslationServer.set_locale(TranslationServer.get_locale())
'''


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and m.get("patchsmith") else None


def manifest_present(folder):
    return os.path.exists(os.path.join(folder, MANIFEST))


def _original(manifest):
    """The text of the game's override.cfg (if there was one), or None."""
    if manifest.get("override_originale") is None:
        return None
    return base64.b64decode(manifest["override_originale"]).decode("utf-8", "replace")


def _override_text(previous, autoload):
    """override.cfg with PatchSmith's autoload (added to the game's one, if there was one)."""
    line = '%s="*res://%s"' % (AUTOLOAD, autoload)
    lines = (previous or "").replace("\r\n", "\n").split("\n") if previous else []
    lines = [r for r in lines if not r.strip().startswith(AUTOLOAD + "=")]
    if "[autoload]" in [r.strip() for r in lines]:
        i = [r.strip() for r in lines].index("[autoload]")
        lines.insert(i + 1, line)
    else:
        lines += (["", "[autoload]"] if lines and lines[-1].strip() else ["[autoload]"]) + [line]
    return "\n".join(lines).strip("\n") + "\n"


def install(folder, table_language, translations, dest_lang, log=print):
    """Writes (or updates) override.cfg, the script and the translations next to the executable, with the manifest."""
    manifest = read_manifest(folder) or {"patchsmith": True, "versione": 1, "file": {}}
    override = os.path.join(folder, OVERRIDE)
    if os.path.exists(override) and OVERRIDE not in manifest["file"]:  # the game's override.cfg: it is kept
        with open(override, "rb") as f:  # (the exact bytes: it is put back identical)
            manifest["override_originale"] = base64.b64encode(f.read()).decode("ascii")
        log("  game's override.cfg: kept, adding only PatchSmith's line")
    dictionary = {k: v for k, v in translations.items() if isinstance(v, str) and v and v != k}
    contents = {
        OVERRIDE: _override_text(_original(manifest), SCRIPT).encode("utf-8"),
        SCRIPT: SCRIPT_GD.encode("utf-8"),
        DICTIONARY: json.dumps({"lingua": table_language.replace("-", "_"),
                                "aggiunte": [dest_lang.replace("-", "_")],
                                "testi": dictionary}, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"),
    }
    manifest["data"] = datetime.datetime.now().isoformat(timespec="seconds")
    write_atomic(os.path.join(folder, MANIFEST), json.dumps(dict(manifest, in_corso=True), indent=2).encode())
    for name, data in contents.items():
        write_atomic(os.path.join(folder, name), data)
        manifest["file"][name] = hashlib.sha256(data).hexdigest()
    write_atomic(os.path.join(folder, MANIFEST), json.dumps(manifest, indent=2).encode())
    log("  Translation for Godot next to the executable: %d texts (override.cfg, %s, %s)" % (
        len(dictionary), SCRIPT, DICTIONARY))
    return len(dictionary)


def uninstall(folder, log=print):
    """Removes PatchSmith's files (those changed by hand stay) and puts back the game's override.cfg."""
    manifest = read_manifest(folder)
    if manifest is None:
        return False
    for name, fingerprint in manifest.get("file", {}).items():
        p = os.path.join(folder, name)
        if os.path.exists(p):
            if _sha256(p) == fingerprint:
                os.remove(p)
            else:
                log("  ! %s has been changed after the translation: I'm leaving it as is" % name)
    if manifest.get("override_originale") is not None:
        write_atomic(os.path.join(folder, OVERRIDE), base64.b64decode(manifest["override_originale"]))
        log("  game's override.cfg restored as it was")
    os.remove(os.path.join(folder, MANIFEST))
    log("  Translation for Godot removed from the game folder")
    return True
