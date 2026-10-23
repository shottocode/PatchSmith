"""Unreal Engine 4 adapter (classic .pak archives): translates the .locres localization files.

1. reads (read only) the game's .pak files and finds the .locres files of the original language,
   for example DDS/Content/Localization/Game/en/Game.locres (the language comes from the .locmeta file);
2. translates the lines (translation memory + Google), protecting {x} placeholders, <Stile>...</> tags and separators;
3. writes a NEW patch pak (Content/Paks/zz_Patchsmith_<lingua>_999_P.pak) with the translated .locres files at the
   same path: Unreal loads it with a higher priority and shows the translation ("_P" = patch,
   "_999" = high patch number, so it wins even over the official patches "_1_P", "_2_P"...).
The original paks are never modified: to go back, removing the added pak is enough.

Current limits: pak version 3-9 (UE 4.x) with an unencrypted index and uncompressed or zlib files;
the original language (usually English) must be selected in the game.
"""
import datetime
import hashlib
import json
import os
import re
import struct
import unicodedata
import zlib

from . import __version__, language, legacy, detection
from .adapter_webjson import TMP_SUFFIX, write_atomic
from .system import game_open

MAGIC_PAK = 0x5A6F12E1
MAGIC_LOCRES = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")
MAGIC_LOCMETA = bytes.fromhex("4fee4ca1684855836c4c46bd70da507c")
MANIFEST = "patchsmith-unreal.json"
PAK_PREFIX = "zz_Patchsmith_"


def pak_name(target_lang):
    return "%s%s_999_P.pak" % (PAK_PREFIX, target_lang)


class UnrealError(Exception):
    def __init__(self, message="", code=None):
        super().__init__(message)
        self.code = code  # outcome for the interface (see outcome.py), e.g. GAME_OPEN


# --- FString ------------------------------------------------------------------------------------

def _read_fstring(data, pos):
    n = struct.unpack_from("<i", data, pos)[0]
    pos += 4
    if n == 0:
        return "", pos
    if n > 0:
        return data[pos:pos + n - 1].decode("latin-1"), pos + n
    n = -n
    return data[pos:pos + 2 * n - 2].decode("utf-16-le", "surrogatepass"), pos + 2 * n


def _write_fstring(s):
    if not s:
        return struct.pack("<i", 0)
    if all(ord(c) < 128 for c in s):
        return struct.pack("<i", len(s) + 1) + s.encode("ascii") + b"\x00"
    encoded = s.encode("utf-16-le", "surrogatepass")
    return struct.pack("<i", -(len(encoded) // 2 + 1)) + encoded + b"\x00\x00"


# --- .pak archives ------------------------------------------------------------------------------

def full_name(mount, name):
    """The full name of a file in the pak: the mount point without the leading '../', plus the name. UnrealPak uses
    as mount point the folder shared by all the files of the pak: '../../../' for the main paks (the names are
    already complete, 'Proj/Content/...'), a deeper folder for small paks (Remnant: '../../../Remnant/
    Content/', with the names 'Localization/Game/en/Game.locres')."""
    m = (mount or "").replace("\\", "/")
    while m.startswith("../"):
        m = m[3:]
    m = m.lstrip("/")
    if m and not m.endswith("/"):
        m += "/"
    return m + name.lstrip("/")


class PakEntry:
    def __init__(self, name, offset, size, orig_size, method, blocks, flags, block_size, sha1, entry_bytes):
        self.name, self.offset, self.size, self.orig_size = name, offset, size, orig_size
        self.method, self.blocks, self.flags, self.block_size, self.sha1 = method, blocks, flags, block_size, sha1
        self.header_length = len(entry_bytes)


class Pak:
    """Reader of UE4 paks version 3-9 ('legacy' index)."""

    def __init__(self, path):
        self.path = path
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - 1024))
            tail = f.read()
        i = tail.rfind(struct.pack("<I", MAGIC_PAK))
        if i < 0:
            raise UnrealError("%s is not a valid Unreal pak" % os.path.basename(path))
        self.version, self.index_offset, self.index_size = struct.unpack_from("<Iqq", tail, i + 4)
        if not 3 <= self.version <= 9:
            raise UnrealError("Pak version %d: Not yet supported (versions 3-9, Unreal 4.x are supported)"
                              % self.version)
        self.index_encrypted = self.version >= 4 and tail[i - 1] != 0
        self.key_guid = tail[i - 17:i - 1] if self.version >= 7 else b"\x00" * 16
        if self.index_encrypted:
            raise UnrealError("The PAK index is encrypted (requires the game's AES key): Not supported")
        rest = tail[i + 4 + 4 + 8 + 8 + 20:]
        self.frozen = rest[0] if self.version >= 9 else None
        names = rest[1:] if self.version >= 9 else rest
        self.compression_slots = len(names) // 32 if self.version >= 8 else 0
        self.methods = [names[k * 32:(k + 1) * 32].split(b"\x00")[0].decode("ascii", "replace")
                       for k in range(self.compression_slots)]
        with open(path, "rb") as f:
            f.seek(self.index_offset)
            idx = f.read(self.index_size)
        for width in ((4, 1) if self.version == 8 else (4,)):  # 4.22 used 1 byte for the method
            try:
                self.mount, self.entries = self._read_index(idx, width)
                self.method_width = width
                break
            except (struct.error, UnrealError, UnicodeDecodeError):
                continue
        else:
            raise UnrealError("Pak index unreadable")
        self.prefix = full_name(self.mount, "")

    def _read_index(self, idx, width):
        mount, pos = _read_fstring(idx, 0)
        n = struct.unpack_from("<i", idx, pos)[0]
        pos += 4
        entries = {}
        for _ in range(n):
            name, pos = _read_fstring(idx, pos)
            start = pos
            offset, dim, dim_orig = struct.unpack_from("<qqq", idx, pos)
            pos += 24
            method = struct.unpack_from("<I" if width == 4 else "<B", idx, pos)[0]
            pos += width
            sha1 = idx[pos:pos + 20]
            pos += 20
            blocks = []
            if method:
                nb = struct.unpack_from("<i", idx, pos)[0]
                pos += 4
                for _ in range(nb):
                    blocks.append(struct.unpack_from("<qq", idx, pos))
                    pos += 16
            flags = idx[pos]
            block_size = struct.unpack_from("<I", idx, pos + 1)[0]
            pos += 5
            entries[name] = PakEntry(name, offset, dim, dim_orig, method, blocks, flags, block_size, sha1,
                                     idx[start:pos])
        if pos != len(idx):
            raise UnrealError("inconsistent index")
        return mount, entries

    def method_name(self, entry):
        if not entry.method:
            return None
        if self.version >= 8:
            return self.methods[entry.method - 1] if entry.method - 1 < len(self.methods) else "?"
        return {1: "Zlib", 2: "Gzip"}.get(entry.method, "Custom")

    def read_file(self, name):
        """The data of a file in the pak, from its name in the pak or its full name (see nome_pieno)."""
        entry = self.entries.get(name)
        if entry is None and self.prefix and name.startswith(self.prefix):
            entry = self.entries.get(name[len(self.prefix):])
        if entry is None:
            raise KeyError(name)
        if entry.flags & 1:
            raise UnrealError("%s is encrypted: not supported" % name)
        with open(self.path, "rb") as f:
            if not entry.method:
                f.seek(entry.offset + entry.header_length)
                data = f.read(entry.size)
            else:
                if self.method_name(entry).lower() != "zlib":
                    raise UnrealError("%s is compressed with %s: not supported" % (name, self.method_name(entry)))
                base = entry.offset if self.version >= 5 else 0  # relative blocks from version 5
                parts = []
                for start, end in entry.blocks:
                    f.seek(base + start)
                    parts.append(zlib.decompress(f.read(end - start)))
                data = b"".join(parts)
        if not entry.method and hashlib.sha1(data).digest() != entry.sha1:
            raise UnrealError("%s: hash different from the one in the index" % name)
        return data


def _serialize_entry(version, offset, data, method_width=4):
    """FPakEntry of an uncompressed file (version 3-9)."""
    parts = struct.pack("<qqq", offset, len(data), len(data))
    parts += struct.pack("<B", 0) if method_width == 1 else struct.pack("<i", 0)
    parts += hashlib.sha1(data).digest()
    parts += struct.pack("<BI", 0, 0)  # flags (not encrypted), block size
    return parts


def write_pak(path, model, file, mount=None):
    """Writes an uncompressed pak with the same format as the 'model' pak. file: [(nome, dati)]. mount: the mount
    point (usually the model's; '../../../' with full names)."""
    v, width = model.version, getattr(model, "method_width", 4)
    mount = model.mount if mount is None else mount
    body = bytearray()
    entries = []
    for name, data in file:
        offset = len(body)
        body += _serialize_entry(v, 0, data, width)  # the header in front of the data has offset 0 (like UnrealPak)
        body += data
        entries.append((name, offset, data))
    idx = _write_fstring(mount) + struct.pack("<i", len(entries))
    for name, offset, data in entries:
        idx += _write_fstring(name) + _serialize_entry(v, offset, data, width)
    tail = b""
    if v >= 7:
        tail += b"\x00" * 16  # no encryption key
    if v >= 4:
        tail += b"\x00"  # index not encrypted
    tail += struct.pack("<IIqq", MAGIC_PAK, v, len(body), len(idx)) + hashlib.sha1(idx).digest()
    if v >= 9:
        tail += b"\x00"  # index not frozen
    if v >= 8:
        tail += b"\x00" * 32 * model.compression_slots
    with open(path, "wb") as f:
        f.write(bytes(body) + idx + tail)


# --- .locres / .locmeta -------------------------------------------------------------------------

def read_locres(data):
    """Structure of a .locres (versions 1-3). The translated strings are at the end of the file."""
    if data[:16] != MAGIC_LOCRES:
        raise UnrealError("'Legacy' .locres format (no header): not yet supported")
    version = data[16]
    if not 1 <= version <= 3:
        raise UnrealError(".locres version %d not supported" % version)
    pos = 17
    strings_offset = struct.unpack_from("<q", data, pos)[0]
    pos += 8
    if version >= 2:
        pos += 4  # number of entries
    nns = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    keys = []
    for _ in range(nns):
        if version >= 2:
            pos += 4
        ns, pos = _read_fstring(data, pos)
        nk = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        for _ in range(nk):
            if version >= 2:
                pos += 4
            key, pos = _read_fstring(data, pos)
            pos += 4  # hash of the source text
            idx = struct.unpack_from("<i", data, pos)[0]
            pos += 4
            keys.append((ns, key, idx))
    if pos != strings_offset:
        raise UnrealError(".locres inconsistent (key section)")
    n = struct.unpack_from("<i", data, pos)[0]
    pos += 4
    strings = []
    for _ in range(n):
        s, pos = _read_fstring(data, pos)
        ref = None
        if version >= 2:
            ref = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        strings.append((s, ref))
    if pos != len(data):
        raise UnrealError(".locres inconsistent (strings)")
    if any(not 0 <= i < n for _, _, i in keys):
        raise UnrealError(".locres inconsistent (string indices)")
    return {"version": version, "head": data[:strings_offset], "keys": keys, "strings": strings}


def write_locres(info, new_lines):
    """Same .locres with the strings replaced (everything else identical byte for byte)."""
    output = bytearray(info["head"]) + struct.pack("<i", len(new_lines))
    for s, (_, ref) in zip(new_lines, info["strings"]):
        output += _write_fstring(unicodedata.normalize("NFC", s))  # accents the way Unreal expects them
        if info["version"] >= 2:
            output += struct.pack("<i", ref)
    return bytes(output)


def native_culture(locmeta_data):
    """Original language written in the .locmeta (e.g. 'en')."""
    try:
        culture, _ = _read_fstring(locmeta_data, 17 if locmeta_data[:16] == MAGIC_LOCMETA else 0)
        return culture or None
    except (struct.error, UnicodeDecodeError):
        return None


# --- game analysis ------------------------------------------------------------------------------

def _paks(folder):
    """Paks of the projects (not those of Engine/Programs), ours excluded."""
    found = []
    for root, dirs, files in os.walk(folder):
        rel = os.path.relpath(root, folder).replace(os.sep, "/")
        if rel.split("/")[0] == "Engine":
            dirs[:] = []
            continue
        for f in files:
            if f.lower().endswith(".pak") and not f.startswith(PAK_PREFIX):
                found.append(os.path.join(root, f))
    # like Unreal: patch paks (_P) have a higher priority, then alphabetical order
    return sorted(found, key=lambda p: (os.path.basename(p).lower().endswith("_p.pak"), os.path.basename(p).lower()))


def present_cultures(folder):
    """Languages the game is localized in ({'en', 'fr', 'es-419'...}), from the .locres files in the paks (read
    only)."""
    cultures = set()
    for path in _paks(folder):
        try:
            pak = Pak(path)
        except (UnrealError, OSError, struct.error):
            continue
        for name in pak.entries:
            full = full_name(pak.mount, name)
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", full)
            if m and not full.startswith("Engine/"):
                cultures.add(m.group(3))
    return cultures


def find_locres(folder, skipped=None):
    """[(pak, nome completo, target, cultura)] of the .locres files of the original language (highest-priority
    version), with full names also from paks with a deeper mount point (see full_name: Pak.read_file accepts them).
    A pak that cannot be read (newer format, encrypted, corrupted) is skipped, without stopping the others: 'saltati'
    (a list) receives 'nome: motivo'."""
    read_items = []
    for path in _paks(folder):
        try:
            pak = Pak(path)
        except (UnrealError, OSError, struct.error, ValueError) as e:
            if skipped is not None:
                skipped.append("%s: %s" % (os.path.basename(path), e))
            continue
        read_items.append((pak, [full_name(pak.mount, name) for name in pak.entries]))
    native_lang = {}  # the original language of each target, from its .locmeta (even in another pak)
    for pak, names in read_items:
        for name in names:
            m = re.match(r"^(.*/Localization/([^/]+))/\2\.locmeta$", name)
            if m and not name.startswith("Engine/"):
                try:
                    native_lang[m.group(1)] = native_culture(pak.read_file(name))
                except (UnrealError, OSError, struct.error, ValueError) as e:
                    if skipped is not None:
                        skipped.append("%s (%s): %s" % (os.path.basename(pak.path), name, e))
    sources = {}
    for pak, names in read_items:
        for name in names:
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", name)
            if not m or name.startswith("Engine/"):
                continue
            target_root, target, culture = m.group(1), m.group(2), m.group(3)
            if culture == (native_lang.get(target_root) or "en"):
                sources[name] = (pak, name, target, culture)  # later paks overwrite (priority)
    return list(sources.values())


def analyze(folder):
    paks = _paks(folder)
    info = {"paks": paks, "versions": [], "iostore": False, "signed": False, "problems": []}
    for p in paks:
        if os.path.exists(p[:-4] + ".utoc"):
            info["iostore"] = True
        if os.path.exists(p[:-4] + ".sig"):
            info["signed"] = True
        try:
            info["versions"].append(Pak(p).version)
        except UnrealError as e:
            info["problems"].append("%s: %s" % (os.path.basename(p), e))
    if info["signed"]:
        info["problems"].append("the game's paks are signed (.sig): an added pak would be rejected")
    return info


# --- manifest -----------------------------------------------------------------------------------

def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            m = legacy.load(f)
    except (OSError, ValueError):
        return None
    def ours(entries):
        return isinstance(entries, dict) and all(
            isinstance(r, str) and r.endswith(".pak") and ".." not in r.split("/") and not os.path.isabs(r)
            and ":" not in r and os.path.basename(r).startswith(PAK_PREFIX) and isinstance(h, str)
            for r, h in entries.items())
    valid = (isinstance(m, dict) and m.get("patchsmith") is True and ours(m.get("file"))
              and ours(m.get("prior", {})) and set(m.get("prior", {})) <= set(m["file"]))
    return m if valid else None


def manifest_present(folder):
    return os.path.exists(os.path.join(folder, MANIFEST))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- installation -------------------------------------------------------------------------------

def _checks(folder):
    if game_open(folder):
        raise UnrealError("The game is open: close it and try again.", "GAME_OPEN")
    if analyze(folder)["signed"]:
        raise UnrealError("The game's paks are signed (.sig files): the game would reject an added pak.",
                           "NOT_SUPPORTED")
    outcome = detection.detect(folder)
    names = [r["name"] for r in outcome["results"] if r["section"] == "AntiCheat"]
    if names:
        raise UnrealError("This game has an anti-cheat system (%s): I won't add modified paks (risk of a ban)."
                           % ", ".join(names), "ANTICHEAT")


def texts_to_translate(folder, log=print):
    """(sources, unique texts to translate, original language)."""
    sources = find_locres(folder)
    if not sources:
        raise UnrealError("I haven't found any localization files (.locres) within the game's PAK files.",
                          "NOT_SUPPORTED")
    texts, cultures = {}, set()
    for pak, name, target, culture in sources:
        info = read_locres(pak.read_file(name))
        log("  %s (%s): %d phrases" % (name, culture, len(info["strings"])))
        cultures.add(culture)
        for s, _ in info["strings"]:
            if any(c.isalpha() for c in s):
                texts.setdefault(s, None)
    return sources, list(texts), sorted(cultures)[0]


# typographic punctuation that Google may introduce: if the game does not already use it, its font
# may not have it (little boxes would appear), so it becomes ASCII
SUBSTITUTES = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
             "\u00ab": '"', "\u00bb": '"', "\u00a0": " "}
# languages with a non-Latin script: their texts say nothing about the accented letters of Latin fonts
_NON_LATIN_LANGS = {"ja", "zh", "ko", "ru", "uk", "be", "bg", "mk", "sr", "kk", "ky", "mn", "el", "ar", "he", "fa",
                    "ur", "th", "hi", "bn", "ta", "te", "ka", "hy", "km", "lo", "my"}
_LATIN_1 = {chr(c) for c in range(0xA0, 0x100)}


def culture_chars(folder, excluded_cultures=()):
    """(characters of the texts of the game's other languages written in Latin script, [those languages]) from the
    .locres files in the paks (read only): a letter the game uses in its official translations, its fonts can draw."""
    chars, cultures = set(), set()
    excluded_cultures = {c.lower() for c in excluded_cultures}
    for path in _paks(folder):
        try:
            pak = Pak(path)
        except (UnrealError, OSError, struct.error, ValueError):
            continue
        for name in pak.entries:
            full = full_name(pak.mount, name)
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", full)
            if not m or full.startswith("Engine/"):
                continue
            culture = m.group(3)
            if culture.lower() in excluded_cultures or culture.split("-")[0].lower() in _NON_LATIN_LANGS:
                continue
            try:
                info = read_locres(pak.read_file(name))
            except (UnrealError, OSError, struct.error, ValueError, UnicodeDecodeError):
                continue
            cultures.add(culture)
            for s, _ in info["strings"]:
                chars.update(s)
    return chars, sorted(cultures)


def allowed_chars(folder, present, native=()):
    """The characters that can be written in the translated .locres files, or None if unknown (no limit): ASCII,
    those of the original texts ('presenti') and those of the official translations in Latin-script languages.
    Unreal's fonts are .ttf/.otf files: if the official translations use accented Latin-1 letters
    (\u00e9, \u00fc, \u00f1...), the font has the whole block (including \u00ec and \u00f2, which French or Spanish do
    not use). Without official translations in Latin-script languages, or if they use no accented letter, there is no
    clue: no limit."""
    other_ones, cultures = culture_chars(folder, native)
    if not cultures or not any(c.isalpha() and not c.isascii() for c in other_ones):
        return None
    from .characters import ASCII
    allowed = set(ASCII) | set(present) | other_ones
    if any(c in _LATIN_1 and c.isalpha() for c in other_ones):
        allowed |= _LATIN_1
    return allowed


def _adapt(translation, present, allowed=None, target_lang="it"):
    """Translation ready for the .locres, or None if it must be rejected (U+0000 and U+FFFF truncate the text; a
    character the game's fonts cannot draw and that cannot be replaced: see allowed_chars)."""
    if "\x00" in translation or "\uffff" in translation:
        return None
    ready_text = "".join(SUBSTITUTES[c] if c in SUBSTITUTES and c not in present else c for c in translation)
    if allowed is not None:
        from .characters import adapt_chars
        ready_text = adapt_chars(ready_text, allowed, target_lang)
    return ready_text


def install(folder, a, profile_name, memory, log=print, refused=None):
    """Writes the patch pak with the translated .locres files using the translations in 'memory' {originale:
    tradotto}. Returns the number of translated lines inserted. Characters the game's fonts probably lack are
    adapted (see allowed_chars: è -> e'); a translation that cannot be adapted stays in the original and goes into
    'respinte' (if given), {original: chars}, for problems.json."""
    folder = os.path.abspath(folder)
    _checks(folder)
    sources = find_locres(folder)
    if not sources:
        raise UnrealError("I haven't found any localization files (.locres) within the game's PAK files.",
                          "NOT_SUPPORTED")
    manifest = read_manifest(folder) if manifest_present(folder) else None
    if manifest_present(folder) and manifest is None:
        raise UnrealError("The file %s is invalid: I'm not using it. Please check the folder manually." % MANIFEST)

    read_items = [(pak, name, pak.read_file(name)) for pak, name, _, _ in sources]
    read_items = [(pak, name, original, read_locres(original)) for pak, name, original in read_items]
    present = {c for _, _, _, info in read_items for s, _ in info["strings"] for c in s}
    allowed = allowed_chars(folder, present, {culture for _, _, _, culture in sources})
    by_pak, translated_count, total, adapted, rejected = {}, 0, 0, 0, {}
    for pak, name, original, info in read_items:
        new_lines = []
        for s, _ in info["strings"]:
            raw_translation = memory.get(s)
            t = _adapt(raw_translation, present, allowed, a) if raw_translation else None
            if raw_translation and t is None and allowed is not None and _adapt(raw_translation, present) is not None:
                from .characters import Filter, undrawable
                rejected[s] = undrawable(raw_translation, Filter(allowed, added=s), a)
            elif t and _adapt(raw_translation, present) != t:
                adapted += 1
            new_lines.append(t if t else s)
            translated_count += bool(t) and t != s
            total += 1
        new = write_locres(info, new_lines)
        checks = read_locres(new)  # it must read back with the same structure
        if checks["keys"] != info["keys"] or checks["head"] != info["head"]:
            raise UnrealError("Check of the translated .locres failed for %s: not writing anything" % name)
        by_pak.setdefault(os.path.dirname(pak.path), (pak, []))[1].append((name, new, original))
    if adapted:
        log("  %d phrases adapted to the game's font characters (letters that its languages don't use, e.g., \"è\")"
            % adapted)
    if rejected:
        log("  ! %d translations have characters the game's fonts probably lack and stay untranslated, e.g. %r (I'll "
            "redo the ones written by PatchSmith without those characters)" % (
                len(rejected), next(iter(rejected))[:60]))
        if refused is not None:
            refused.update(rejected)

    # 1) each new pak is written alongside with a temporary name (Unreal only loads .pak files) and verified
    ready, old_files = {}, (manifest or {}).get("file", {})
    try:
        for paks_folder, (model, file) in by_pak.items():
            dest_lang = os.path.join(paks_folder, pak_name(a))
            rel = os.path.relpath(dest_lang, folder).replace(os.sep, "/")
            if os.path.exists(dest_lang) and rel not in old_files:
                raise UnrealError("There's already %s, which wasn't created by PatchSmith: I won't overwrite it." % rel)
            temp_file = dest_lang + TMP_SUFFIX
            # full names with the root mount point: this also works for the .locres files of a pak mounted in a
            # deeper folder (for the main paks it is the same mount point, '../../../')
            write_pak(temp_file, model, [(n, d) for n, d, _ in file], mount="../../../")
            was_read = Pak(temp_file)  # verification: the written pak must read back with the same files
            if any(was_read.read_file(n) != d for n, d, _ in file) or set(was_read.entries) != {n for n, _, _ in file}:
                raise UnrealError("Verification of the written pak failed: not installing anything")
            ready[rel] = (temp_file, dest_lang, _sha256(temp_file))
    except BaseException:
        for temp_file, _, _ in ready.values():
            if os.path.exists(temp_file):
                os.remove(temp_file)
        raise

    # 2) manifest written BEFORE putting the paks in place: if things stop halfway, restore
    #    recognizes both the new pak and the one that was there before ("prior" field)
    def write_manifest(file, prior):
        write_atomic(os.path.join(folder, MANIFEST), json.dumps({
            "version": 1, "patchsmith": True, "patchsmith_version": __version__, "language": a,
            "profile": profile_name,
            "date": datetime.datetime.now().isoformat(timespec="seconds"),
            "file": file, "prior": prior,
            "sources": {n: hashlib.sha1(o).hexdigest() for _, (_, f) in by_pak.items() for n, _, o in f},
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    new_files = {rel: fingerprint for rel, (_, _, fingerprint) in ready.items()}
    write_manifest(dict(old_files, **new_files), {rel: h for rel, h in old_files.items() if rel in new_files})
    for temp_file, dest_lang, _ in ready.values():
        os.replace(temp_file, dest_lang)
    # 3) paks of a previous language (e.g. switching from it to es): remove them
    for rel, fingerprint in old_files.items():
        p = os.path.join(folder, *rel.split("/"))
        if rel not in new_files and os.path.exists(p) and _sha256(p) == fingerprint:
            os.remove(p)
    write_manifest(new_files, {})
    log("  Translation pak written: %s (%d of %d sentences translated)"
        % (", ".join(new_files), translated_count, total))
    return translated_count


def uninstall(folder, log=print):
    folder = os.path.abspath(folder)
    if not manifest_present(folder):
        return False
    manifest = read_manifest(folder)
    if manifest is None:
        raise UnrealError("The file %s is invalid: for your safety, I won't delete anything." % MANIFEST)
    if game_open(folder):
        raise UnrealError("The game is open: close it and try again.", "GAME_OPEN")
    for rel, fingerprint in manifest["file"].items():
        p = os.path.join(folder, *rel.split("/"))
        if os.path.exists(p + TMP_SUFFIX):
            os.remove(p + TMP_SUFFIX)
        if os.path.exists(p):
            if _sha256(p) in (fingerprint, manifest.get("prior", {}).get(rel)):
                os.remove(p)
                log("  Removed %s" % rel)
            else:
                log("  ! %s has been modified by others: I'm leaving it as is" % rel)
    if os.path.exists(os.path.join(folder, MANIFEST + TMP_SUFFIX)):
        os.remove(os.path.join(folder, MANIFEST + TMP_SUFFIX))
    os.remove(os.path.join(folder, MANIFEST))
    log("  Translation removed: the game is using its original files again.")
    return True


def state(folder, log=print):
    info = analyze(folder)
    log("  Game paks: %d (versions %s)%s" % (len(info["paks"]), sorted(set(info["versions"])),
                                                ", IoStore present" if info["iostore"] else ""))
    for p in info["problems"]:
        log("  ! " + p)
    manifest = read_manifest(folder)
    if not manifest:
        log("  Translation not installed.")
        return False
    for rel, fingerprint in manifest["file"].items():
        p = os.path.join(folder, *rel.split("/"))
        ok = os.path.exists(p) and _sha256(p) == fingerprint
        log("  %s %s (language %s, %s)" % ("OK " if ok else "NO ", rel, manifest.get("language"), manifest.get("date")))
    try:
        current_count = {n: hashlib.sha1(pak.read_file(n)).hexdigest() for pak, n, _, _ in find_locres(folder)}
        if current_count != manifest.get("sources"):
            log("  ! The game has been updated: press 'Analyze and translate' to translate the new texts too (the ones "
                "already translated are kept).")
    except UnrealError as e:
        log("  ! " + str(e))
    return True
