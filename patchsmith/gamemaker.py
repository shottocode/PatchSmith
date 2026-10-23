"""Reading GameMaker's date.win (read-only, standard library): chunks, strings, fonts.

date.win = "FORM" + size + chunks ("GEN8", "STRG", "FONT", ...), each one name + size + content.
References between chunks are absolute offsets in the file; strings are length (uint32) + UTF-8 + 0,
and references point to the text (that is, to the length + 4).

GameMaker fonts have their characters "baked" at compile time: a character that is not there
is drawn as a small square (or does not appear). That is why a translation must use the fonts' characters.
"""
import os
import re
import struct

DATA_NAMES = ("data.win", "game.unx", "game.ios", "game.droid")


class GameMakerError(ValueError):
    pass


def pieces(data):
    """{nome: (content start, size)} of the chunks of a data.win."""
    if len(data) < 8 or data[:4] != b"FORM":
        raise GameMakerError("this is not a GameMaker data.win file (it's missing the FORM)")
    end = 8 + struct.unpack_from("<I", data, 4)[0]
    if end > len(data):
        raise GameMakerError("data.win truncated")
    output, p = {}, 8
    while p + 8 <= end:
        name = data[p:p + 4].decode("ascii", "replace")
        dim = struct.unpack_from("<I", data, p + 4)[0]
        if p + 8 + dim > end:
            raise GameMakerError("chunk %s beyond the end of the file" % name)
        output[name] = (p + 8, dim)
        p += 8 + dim
    return output


def read_string(data, pointer):
    """GameMaker string from its reference (which points to the text, preceded by the length)."""
    if not 4 <= pointer <= len(data) - 1:
        raise GameMakerError("invalid string reference")
    n = struct.unpack_from("<I", data, pointer - 4)[0]
    if pointer + n >= len(data) or data[pointer + n] != 0:
        raise GameMakerError("invalid string")
    return data[pointer:pointer + n].decode("utf-8", "replace")


def _glyphs(data, start, end, p):
    """Tries to read a list of glyph pointers at p; returns the characters or None.

    The list is valid only if the glyphs start right after the pointers, in increasing order."""
    if p + 4 > end:
        return None
    n = struct.unpack_from("<I", data, p)[0]
    if not 0 < n <= 70000 or p + 4 + 4 * n > end:
        return None
    pointers = struct.unpack_from("<%dI" % n, data, p + 4)
    if pointers[0] != p + 4 + 4 * n:
        return None
    chars, previous, last = [], -1, 0
    for q in pointers:
        if not start <= q <= end - 16 or q <= last:
            return None
        c, x, y, w, h = struct.unpack_from("<HHHHH", data, q)
        if w > 4096 or h > 4096 or c <= previous:  # glyphs are in character order
            return None
        chars.append(chr(c))
        previous, last = c, q
    return chars


def font(data):
    """([{'name', 'chars'}], how many fonts could not be read)."""
    table = pieces(data)
    if "FONT" not in table:
        return [], 0
    start, dim = table["FONT"]
    end = start + dim
    if dim < 4:
        raise GameMakerError("invalid FONT chunk")
    n = struct.unpack_from("<I", data, start)[0]
    if n > 10000 or dim < 4 + 4 * n:
        raise GameMakerError("invalid FONT chunk")
    output, unreadable = [], 0
    for k in range(n):
        p = struct.unpack_from("<I", data, start + 4 + 4 * k)[0]
        try:
            if not start <= p < end - 4:
                raise GameMakerError("font outside the FONT chunk")
            name = read_string(data, struct.unpack_from("<I", data, p)[0])
        except (GameMakerError, struct.error):
            unreadable += 1
            continue
        # the fields after the name change with the GameMaker version: look for the glyph list
        for shift in range(40, 96, 4):
            chars = _glyphs(data, start, end, p + shift)
            if chars:
                output.append({"name": name, "chars": chars})
                break
        else:
            unreadable += 1
    return output, unreadable


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


def is_data(path):
    """True if the file is a GameMaker data.win (or game.unx...)."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"FORM"
    except OSError:
        return False


def roots(folder, depth=2):
    """Folders (up to 'profondita' levels below the chosen one) that contain a GameMaker data.win."""
    matches = []
    base = os.path.abspath(folder)
    for root, dirs, files in os.walk(base):
        level = os.path.relpath(root, base).count(os.sep) + (0 if root == base else 1)
        if level >= depth:
            dirs[:] = []
        dirs.sort()
        if any(f.lower() in DATA_NAMES and is_data(os.path.join(root, f)) for f in files):
            matches.append(root)
    return matches


def sprite(data):
    """[{'name', 'frames'}] of the sprites (SPRT chunk). GameMaker Studio 1 and 2 format (with the -1 marker
    after the 14 fixed fields); sprites that cannot be read are skipped."""
    table = pieces(data)
    if "SPRT" not in table:
        return []
    start, dim = table["SPRT"]
    end = start + dim
    n = struct.unpack_from("<I", data, start)[0]
    if n > 100000 or dim < 4 + 4 * n:
        raise GameMakerError("invalid SPRT chunk")
    output = []
    for k in range(n):
        p = struct.unpack_from("<I", data, start + 4 + 4 * k)[0]
        if not start <= p <= end - 64:
            continue
        try:
            name = read_string(data, struct.unpack_from("<I", data, p)[0])
            q = p + 56
            marker = struct.unpack_from("<i", data, q)[0]
            if marker == -1:  # GameMaker Studio 2: version, type, speed, [version fields], frames
                version, kind = struct.unpack_from("<II", data, q + 4)
                if kind != 0:  # vector or Spine sprites: no normal frames
                    continue
                q += 20 + (4 if version >= 2 else 0) + (4 if version >= 3 else 0)
                frames = struct.unpack_from("<I", data, q)[0]
            else:
                frames = marker
            if 0 <= frames <= 10000:
                output.append({"name": name, "frames": frames})
        except (GameMakerError, struct.error):
            continue
    return output


def names_of_game(data):
    """Names of the game written in GEN8 (file name, name, display name): they are used to find the save folder
    in %LOCALAPPDATA%."""
    table = pieces(data)
    if "GEN8" not in table:
        return []
    start, dim = table["GEN8"]
    names = []
    for shift in (4, 40):
        try:
            names.append(read_string(data, struct.unpack_from("<I", data, start + shift)[0]))
        except (GameMakerError, struct.error):
            continue
    return [x for x in dict.fromkeys(names) if x]


def strings(data):
    """All the strings of the STRG chunk (games compiled for the virtual machine)."""
    table = pieces(data)
    if "STRG" not in table:
        return []
    start, dim = table["STRG"]
    n = struct.unpack_from("<I", data, start)[0]
    if n > 1000000 or dim < 4 + 4 * n:
        raise GameMakerError("invalid STRG chunk")
    output = []
    for k in range(n):
        p = struct.unpack_from("<I", data, start + 4 + 4 * k)[0]
        try:
            output.append(read_string(data, p + 4))
        except (GameMakerError, struct.error):
            continue
    return output


def yyc_compiled(data):
    """True if the game is compiled to native code (YYC): date.win does not have the code (CODE chunk), which is
    in the executable together with the strings it uses."""
    return "CODE" not in pieces(data)


def executables(folder):
    """The game's .exe files in the data.win folder (without uninstallers and the like)."""
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names if n.lower().endswith(".exe")
            and not re.match(r"(?i)unins|setup|install|crash|launcher|redist|vc_?redist", n)]


_TEXT_PIECE = re.compile(rb"(?<=\x00)[^\x00]{20,2400}(?=\x00)")


def _candidates(texts, lengths):
    """The strings that can be the map of a sprite font: as many characters as the frames of a sprite with 'font'
    in its name, all different, with at least 20 letters a-z in alphabetical order."""
    output = []
    for t in texts:
        if len(t) not in lengths or len(set(t)) != len(t):
            continue
        letters = [c for c in t if "a" <= c <= "z"]
        if len(letters) >= 20 and letters == sorted(letters):
            output.append(t)
    return output


def font_sprite_maps(folder, data=None):
    """[(sprite name, character map)] of the sprite fonts (font_add_sprite_ext): games that draw text with images
    of letters instead of a font. The map is a string with one character per frame: in games compiled for the
    virtual machine it is in the STRG chunk, in YYC ones in the executable."""
    if data is None:
        path = next((os.path.join(folder, n) for n in DATA_NAMES if os.path.exists(os.path.join(folder, n))),
                        None)
        if not path:
            return []
        data = read_file(path)
    by_length = {}
    for sp in sprite(data):
        if "font" in sp["name"].lower() and sp["frames"] >= 20:
            by_length.setdefault(sp["frames"], []).append(sp["name"])
    if not by_length:
        return []
    texts = strings(data)
    if yyc_compiled(data):
        for exe in executables(folder):
            try:
                with open(exe, "rb") as f:
                    content = f.read()
            except OSError:
                continue
            for m in _TEXT_PIECE.finditer(content):
                try:
                    texts.append(m.group().decode("utf-8"))
                except UnicodeDecodeError:
                    continue
    matches = []
    for mapping in dict.fromkeys(_candidates(texts, by_length)):
        for name in by_length[len(mapping)]:
            matches.append((name, mapping))
    return matches


def folder_mask(folder, relative_path, extension, data=None):
    """True if the game looks for the '<relativa>/*<estensione>' files by itself (e.g. 'local/*.json'): then a new
    language file put there becomes a selectable language in the game. The mask is searched among the game's
    strings (STRG chunk, or the executable if it is compiled with YYC)."""
    masks = {("%s/*%s" % (relative_path, extension)).encode("utf-8"),
             ("%s\\*%s" % (relative_path, extension)).encode("utf-8")}
    if data is None:
        path = next((os.path.join(folder, n) for n in DATA_NAMES if os.path.exists(os.path.join(folder, n))),
                        None)
        if not path:
            return False
        data = read_file(path)
    try:
        if any(m.decode("utf-8") in strings(data) for m in masks):
            return True
        if not yyc_compiled(data):
            return False
    except (GameMakerError, struct.error):
        return False
    for exe in executables(folder):
        try:
            with open(exe, "rb") as f:
                content = f.read()
        except OSError:
            continue
        if any(b"\x00" + m + b"\x00" in content for m in masks):
            return True
    return False
