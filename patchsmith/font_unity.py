"""Characters that the TextMeshPro fonts of a Unity game can draw (read-only).

Unity Localization texts are written in the tables of the bundles (adapter_unity_loc): if the game's fonts lack
a letter (e.g. a "static" atlas made for English, without à è ì ò ù), a small square appears in its place in the
game. Before a translation is written, Project adapts the characters that no font has
(chars.adapt_chars: è -> e', as for GameMaker and XNA).

Where fonts are looked for: the serialized files of <game>_Data (resources.assets, sharedassets, level...),
data.unity3d and the bundles in StreamingAssets (Addressables). For each TextMeshPro font (TMP_FontAsset) these are
read:
- the character table (m_CharacterTable, m_Unicode; in old versions m_glyphInfoList, id) with the field tree
  (type tree, always in bundles). In files without a tree (those of _Data) the table is recognized by its shape:
  a number N and N entries of 16 bytes (type 1, code, glyph, scale 1.0);
- the atlas mode (m_AtlasPopulationMode): 0 static, 1 dynamic (characters are drawn on the fly from the font
  file, the .ttf), 2 dynamic from the Windows fonts.
For the game's .ttf/.otf fonts (Font objects with the file's data) the cmap table is read.

Rule (allowed_chars): a character can be written if at least one font can draw it, because TextMeshPro looks for
missing characters in the fallback fonts. If there is a dynamic font, the characters of the game's .ttf files count
too; if it is not known (no TextMeshPro font, dynamic fonts without their .ttf, Windows fonts, reading not
finished) there is no limit: better a correct letter than a needless replacement.
"""
import hashlib
import json
import os
import re
import struct
import time

from . import interruption, unityfs
from .adapter_webjson import write_atomic

MAX_SECONDS = 180  # reading the fonts: beyond this, not known (no limit); the result is then kept (see cache)
_CACHE_VERSION = 1
MAX_MB_OBJECT = 256  # a larger object is not read (it is not a font)
MIN_LETTERS = 20  # a character set with fewer ASCII letters is not credible as a font for the texts
_FONT_CLASS = 128
_SCRIPT_CLASS = 114  # MonoBehaviour (TextMeshPro fonts are ScriptableObjects)
_SKIPPED_EXTENSIONS = (".ress", ".resource", ".json", ".txt", ".bank", ".bnk", ".wem", ".pck", ".mp4", ".webm",
                       ".ogg", ".wav", ".mp3", ".dll", ".exe", ".pdb", ".xml", ".csv", ".info", ".config", ".hash",
                       ".manifest", ".bin")
_STATIC, _DYNAMIC, _DYNAMIC_OS = 0, 1, 2
# entry of the TextMeshPro character table (without field tree): type 1 (character), code, glyph, scale
_TMP_ENTRY = re.compile(rb"(?s)\x01\x00\x00\x00.{8}\x00\x00\x80\x3f")
_CACHE = {}  # (folder, fingerprint of the files) -> result of game_fonts


# --- .ttf / .otf files: cmap table -----------------------------------------------------------------------------

def ttf_chars(data, start=0):
    """The characters (set of str) of the cmap table of a TrueType/OpenType font starting at 'start', or None."""
    try:
        label = data[start:start + 4]
        if label == b"ttcf":  # font collection: the first one
            start = struct.unpack_from(">I", data, start + 12)[0] + start
            label = data[start:start + 4]
        if label not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            return None
        ntab = struct.unpack_from(">H", data, start + 4)[0]
        if not 1 <= ntab <= 100:
            return None
        cmap = None
        for k in range(ntab):
            tag, _, off, length = struct.unpack_from(">4sIII", data, start + 12 + 16 * k)
            if tag == b"cmap":
                cmap = start + off  # (in collections the offsets are from the start of the file)
        if cmap is None:
            return None
        subtables = []
        for k in range(struct.unpack_from(">H", data, cmap + 2)[0]):
            platform_id, encoding, off = struct.unpack_from(">HHI", data, cmap + 4 + 8 * k)
            fmt = struct.unpack_from(">H", data, cmap + off)[0]
            unicode = platform_id == 0 or (platform_id == 3 and encoding in (1, 10))
            if unicode and fmt in (4, 12):
                subtables.append((0 if fmt == 12 else 1, cmap + off, fmt))
        output = set()
        for _, p, fmt in sorted(subtables)[:1]:
            output = _cmap_12(data, p) if fmt == 12 else _cmap_4(data, p)
        return {chr(c) for c in output if 0 < c < 0x110000 and not 0xD800 <= c <= 0xDFFF} or None
    except (struct.error, IndexError, ValueError):
        return None


def _cmap_4(data, p):
    n = struct.unpack_from(">H", data, p + 6)[0] // 2
    end = struct.unpack_from(">%dH" % n, data, p + 14)
    start = struct.unpack_from(">%dH" % n, data, p + 16 + 2 * n)
    delta = struct.unpack_from(">%dh" % n, data, p + 16 + 4 * n)
    p_offset = p + 16 + 6 * n
    offset = struct.unpack_from(">%dH" % n, data, p_offset)
    codes = set()
    for i in range(n):
        if start[i] == 0xFFFF:
            continue
        for c in range(start[i], end[i] + 1):
            if offset[i] == 0:
                glyph = (c + delta[i]) & 0xFFFF
            else:
                q = p_offset + 2 * i + offset[i] + 2 * (c - start[i])
                glyph = struct.unpack_from(">H", data, q)[0]
                if glyph:
                    glyph = (glyph + delta[i]) & 0xFFFF
            if glyph:
                codes.add(c)
    return codes


def _cmap_12(data, p):
    groups = struct.unpack_from(">I", data, p + 12)[0]
    codes = set()
    for k in range(min(groups, 100000)):
        a, b, _ = struct.unpack_from(">III", data, p + 16 + 12 * k)
        if b >= a and b - a < 0x110000:
            codes.update(range(a, b + 1))
    return codes


def _ttf_in(data):
    """The characters of the first .ttf/.otf file inside the data of a Font object (m_FontData), or None."""
    for magic in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
        p = data.find(magic)
        while p >= 0:
            found = ttf_chars(data, p)
            if found:
                return found
            p = data.find(magic, p + 1)
    return None


# --- TextMeshPro fonts -------------------------------------------------------------------------------------------

def _object_name(data):
    """The name (m_Name) of a MonoBehaviour without field tree: after m_GameObject, m_Enabled and m_Script."""
    try:
        n = struct.unpack_from("<i", data, 28)[0]
        if 0 <= n <= 200:
            return data[32:32 + n].decode("utf-8")
    except (struct.error, UnicodeDecodeError):
        pass
    return ""


def tmp_table(data):
    """The codes of the character table of a TextMeshPro font in data without field tree, or None: a number N
    followed by N entries of 16 bytes (type 1, code < 0x110000, glyph, scale 1.0), all codes different. The longest
    table of the object."""
    best = None
    seen = set()
    for m in _TMP_ENTRY.finditer(data):
        p = m.start()
        if p < 4 or p in seen:
            continue
        n = struct.unpack_from("<i", data, p - 4)[0]
        if not 1 <= n <= 200000 or p + 16 * n > len(data) or (best and n <= len(best)):
            continue
        codes = []
        for k in range(n):
            kind, code, glyph, scale = struct.unpack_from("<IIIf", data, p + 16 * k)
            if kind != 1 or code >= 0x110000 or glyph > 10_000_000 or not 0 < scale < 1000:
                break
            codes.append(code)
            seen.add(p + 16 * k)
        else:
            if len(set(codes)) == n:
                best = codes
    return best


def _font_from_tree(tree, data):
    """(name, mode, codes) of a TextMeshPro font read with the field tree."""
    output = []
    try:
        unityfs._read(tree, data, 0, [], output, {"m_Unicode", "id", "m_AtlasPopulationMode"})
    except unityfs._VariableField:
        pass  # variable fields at the end: what was read up to there is valid
    except (struct.error, IndexError, ValueError):
        return None
    name, mode, codes = "", None, []
    for path, a, b in output:
        if a == "value":
            if path[-1] == "m_Unicode" and "m_CharacterTable" in path:
                codes.append(b)
            elif path[-1] == "id" and "m_glyphInfoList" in path:
                codes.append(b)
            elif path[-1] == "m_AtlasPopulationMode" and len(path) == 2:
                mode = b
        elif a is not None and path[-1] == "m_Name" and len(path) == 2:
            name = data[a + 4:b].decode("utf-8", "replace")
    return name, mode, codes


def _is_tmp_font(tree):
    return tree is not None and any(f.name in ("m_CharacterTable", "m_glyphInfoList") for f in tree.children)


def _useful_objects(s):
    """[(object, kind)] of the objects to read in a serialized file: 'tmp' (TextMeshPro font with the tree),
    'tmp?' (MonoBehaviour without tree, to recognize by its shape), 'font' (.ttf/.otf font)."""
    output = []
    for o in s.objects:
        if not 0 <= o.kind < len(s.kinds) or o.size > MAX_MB_OBJECT * 1024 * 1024:
            continue
        kind = s.kinds[o.kind]
        if kind["class"] == _FONT_CLASS and o.size > 128:  # (with the bytes of a .ttf file inside)
            output.append((o, "font"))
        elif kind["class"] == _SCRIPT_CLASS:
            if kind["tree"] is not None:
                if _is_tmp_font(kind["tree"]):
                    output.append((o, "tmp"))
            elif o.size >= 16 * MIN_LETTERS:  # a smaller one does not have the table of a text font
                output.append((o, "tmp?"))
    return output


def _extract(pieces, wanted):
    """The bytes of the wanted objects [(start, end, key)] (in order, without overlaps) from a file read in
    consecutive pieces [(position, bytes)]: {key: bytes}. Only the object being read is kept in memory."""
    output, i, buffer, src_lang = {}, 0, bytearray(), None
    for position, piece in pieces:
        while i < len(wanted):
            a, b, key = wanted[i]
            piece_end = position + len(piece)
            if a >= piece_end:
                break
            if src_lang is None:
                src_lang = a
                buffer = bytearray()
            x, y = max(a, position) - position, min(b, piece_end) - position
            if y > x:
                buffer += piece[x:y]
            if b <= piece_end:
                output[key] = bytes(buffer)
                i, src_lang, buffer = i + 1, None, bytearray()
            else:
                break
        if i >= len(wanted):
            break
    return output


class _Collector:
    def __init__(self, deadline):
        self.tmp, self.ttf, self.deadline, self.complete = [], [], deadline, True

    def expired(self):
        if time.monotonic() > self.deadline:
            self.complete = False
        return not self.complete

    def serialized_file(self, read_file, size, label):
        """A serialized file read with read_file(start, end) -> [(position, bytes)] in pieces."""
        head = b"".join(p for _, p in read_file(0, min(size, 65536)))
        try:
            _ms, _fs, version, data_start = struct.unpack_from(">IIII", head, 0)
            if version >= 22:
                data_start = struct.unpack_from(">q", head, 32)[0]
        except struct.error:
            return
        if not 9 <= version <= 22 or not 0 < data_start <= min(size, 64 * 1024 * 1024):
            return
        if data_start > len(head):
            head = b"".join(p for _, p in read_file(0, data_start))
        try:
            s = unityfs.SerializedFile(head)
        except (ValueError, IndexError, struct.error, KeyError):
            return
        useful = _useful_objects(s)
        if not useful:
            return
        wanted = sorted((s.data_start + o.start, s.data_start + o.start + o.size, k)
                        for k, (o, _) in enumerate(useful))
        # overlapping objects (damaged file): only the first one
        cleaned_items, end = [], -1
        for a, b, k in wanted:
            if a >= end and b <= size:
                cleaned_items.append((a, b, k))
                end = b
        if not cleaned_items:
            return
        read_items = _extract(read_file(cleaned_items[0][0], cleaned_items[-1][1]), cleaned_items)
        for k, data in read_items.items():
            o, kind = useful[k]
            if kind == "font":
                chars = _ttf_in(data)
                if chars:
                    self.ttf.append({"source": label, "chars": chars})
            elif kind == "tmp":
                was_read = _font_from_tree(s.kinds[o.kind]["tree"], data)
                if was_read:
                    name, mode, codes = was_read
                    self.tmp.append({"name": name, "mode": mode, "source": label,
                                     "chars": {chr(c) for c in codes if 0 < c < 0x110000}})
            else:
                codes = tmp_table(data)
                if codes:
                    self.tmp.append({"name": _object_name(data), "mode": None, "source": label,
                                     "chars": {chr(c) for c in codes if 0 < c < 0x110000}})

    def bundle(self, path, label):
        try:
            with unityfs.Index(path) as idx:
                for name, start, size, _ in idx.nodes:
                    if self.expired():
                        return
                    interruption.check()
                    if name.lower().endswith((".ress", ".resource")):
                        continue

                    def read_file(a, b, start=start):
                        return ((p - start, x) for p, x in idx.pieces(start + a, start + b))
                    self.serialized_file(read_file, size, "%s/%s" % (label, name))
        except (OSError, ValueError, MemoryError):
            return

    def file(self, path, label):
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                def read_file(a, b):
                    f.seek(a)
                    p = a
                    while p < b:
                        piece = f.read(min(16 * 1024 * 1024, b - p))
                        if not piece:
                            return
                        yield p, piece
                        p += len(piece)
                self.serialized_file(read_file, size, label)
        except (OSError, ValueError, MemoryError):
            return


def _to_skip(name, names):
    """Files that are not read: images, sounds, texts..., PatchSmith's files and those that PatchSmith rewrote
    (the translated text tables, with their .patchsmith-originale next to them: they change at every Reapply)."""
    low = name.lower()
    return low.endswith(_SKIPPED_EXTENSIONS) or ".patchsmith-" in name or name + ".patchsmith-originale" in names


def _files_to_read(data_folder):
    """[(path, 'bundle'|'file', size, date)] of the game files where fonts can be: the files of
    <game>_Data (not Unity's Resources folder: the engine's fonts), data.unity3d, the bundles of StreamingAssets."""
    output = []
    try:
        entries = sorted(os.scandir(data_folder), key=lambda e: e.name.lower())
    except OSError:
        return output
    names = {e.name for e in entries}
    for e in entries:
        try:
            if not e.is_file() or _to_skip(e.name, names):
                continue
            st = e.stat()
            output.append((e.path, "bundle" if e.name.lower() == "data.unity3d" else "file", st.st_size,
                           st.st_mtime_ns))
        except OSError:
            continue
    stack = [os.path.join(data_folder, "StreamingAssets")]
    while stack:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            continue
        names = {e.name for e in entries}
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    stack.append(e.path)
                elif not _to_skip(e.name, names):
                    st = e.stat()
                    output.append((e.path, "bundle", st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    return output


def fingerprint(data_folder, items=None):
    """The fingerprint of the files where fonts can be (names, sizes, dates): if it does not change, the fonts are
    the same."""
    items = _files_to_read(data_folder) if items is None else items
    h = hashlib.sha1()
    for path, _, size, date in sorted(items):
        h.update(("%s|%d|%d\n" % (os.path.relpath(path, data_folder).lower(), size, date)).encode("utf-8"))
    return h.hexdigest()


def _is_bundle(path):
    try:
        with open(path, "rb") as f:
            return f.read(8) == b"UnityFS\x00"
    except OSError:
        return False


def game_fonts(data_folder, log=None, seconds=MAX_SECONDS, items=None):
    """{'tmp': [{name, mode, source, chars}], 'ttf': [{source, chars}], 'complete': bool} of the game's fonts
    (read-only). The result stays in memory until the game's files change."""
    items = list(_files_to_read(data_folder) if items is None else items)
    key = (os.path.normcase(os.path.abspath(data_folder)),
              tuple((os.path.normcase(p), d, t) for p, _, d, t in items))
    if key in _CACHE:
        return _CACHE[key]
    collector = _Collector(time.monotonic() + seconds)
    # first the _Data files and the bundles with "font" or "tmp" in their name: if time runs out, the fonts are
    # usually already read
    items.sort(key=lambda x: (0 if x[1] == "file" or re.search(r"(?i)font|tmp|textmesh", os.path.basename(x[0]))
                               else 1))
    for path, kind, _, _ in items:
        if collector.expired():
            break
        interruption.check()
        label = os.path.relpath(path, data_folder).replace(os.sep, "/")
        if _is_bundle(path):
            collector.bundle(path, label)
        elif kind == "file":
            collector.file(path, label)
    result = {"tmp": collector.tmp, "ttf": collector.ttf, "complete": collector.complete}
    if collector.complete:
        _CACHE[key] = result
    elif log:
        log("  ! Reading the game's fonts didn't finish within %d seconds: I'm not limiting the characters" % seconds)
    return result


def _letters(chars):
    return sum(1 for c in chars if c.isascii() and c.isalpha())


def ranges(chars):
    """[[first, last], ...] of the character codes (to save them in little space: CJK fonts have tens of
    thousands of them, almost all consecutive)."""
    output = []
    for c in sorted(ord(x) for x in chars):
        if output and c == output[-1][1] + 1:
            output[-1][1] = c
        else:
            output.append([c, c])
    return output


def from_ranges(items):
    return {chr(c) for a, b in items for c in range(a, b + 1) if 0 < c < 0x110000 and not 0xD800 <= c <= 0xDFFF}


def _read_cache(path, key):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("fingerprint") != key or data.get("version") != _CACHE_VERSION:
        return None
    try:
        return (from_ranges(data["ranges"]) if data.get("ranges") is not None else None), \
            str(data.get("reason") or "")
    except (TypeError, ValueError, KeyError):
        return None


def allowed_chars(data_folder, log=None, seconds=MAX_SECONDS, cache=None):
    """(set of the characters that at least one of the game's fonts can draw, description) or (None, reason) if it is
    not known: see the rule at the start of the file. cache: a .json file where the result is kept until the game's
    files change (a game with thousands of bundles can take a few minutes). A reading not finished in time is kept
    too (None: no limit, as before), so as not to redo it at every "Reapply"."""
    items = _files_to_read(data_folder)
    key = fingerprint(data_folder, items)
    if cache:
        was_read = _read_cache(cache, key)
        if was_read is not None:
            return was_read
    if log and len(items) > 100:
        log("  Checking which letters the game's fonts can draw (%d files, read-only; only the first time, it may take "
            "a few minutes)..." % len(items))
    found = game_fonts(data_folder, log, seconds, items)
    result = _apply_rule(found) if found["complete"] else (None, "font reading didn't finish in time")
    if cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            write_atomic(cache, json.dumps({
                "version": _CACHE_VERSION, "fingerprint": key, "reason": result[1],
                "ranges": ranges(result[0]) if result[0] is not None else None},
                separators=(",", ":")).encode("utf-8"))
        except OSError:
            pass  # it is only a shortcut: next time it is read again
    return result


def _apply_rule(found):
    """(characters, description) or (None, reason) from the fonts found (see the rule at the start of the file)."""
    tmp = found["tmp"]
    if not tmp:
        return None, "no TextMeshPro font"
    if any(f["mode"] == _DYNAMIC_OS for f in tmp):
        return None, "a font uses Windows fonts"
    allowed = set()
    dynamic_ones = []
    for f in tmp:
        allowed |= f["chars"]
        # dynamic, or without field tree and almost empty (a dynamic one emptied when the game was built)
        if f["mode"] == _DYNAMIC or (f["mode"] is None and _letters(f["chars"]) < MIN_LETTERS):
            dynamic_ones.append(f)
    if dynamic_ones:
        if not found["ttf"]:
            return None, "dynamic fonts without their .ttf files"
        for t in found["ttf"]:
            allowed |= t["chars"]
    if _letters(allowed) < MIN_LETTERS:
        return None, "font without the letters of the text"
    description = "%d TextMeshPro fonts%s" % (len(tmp), (" (%d dynamic, with %d .ttf files)" % (
        len(dynamic_ones), len(found["ttf"]))) if dynamic_ones else "")
    return allowed, description


def bundle_data(game_folder, rel_bundle):
    """The <game>_Data folder of a bundle (path relative to the game folder), or None."""
    parts = rel_bundle.replace("\\", "/").split("/")
    for k, p in enumerate(parts):
        if p.endswith("_Data"):
            return os.path.join(game_folder, *parts[:k + 1])
    return None
