"""Reading of the .xnb files of XNA/FNA/MonoGame (read-only, standard library).

For now it is used to know which characters the fonts can draw (SpriteFont): in XNA a character that
is missing from the font makes the game crash (ArgumentException in DrawString/MeasureString), unless
the font has a default character. So a translation must use only characters that are present.

Format (XNB version 5, uncompressed): "XNB" + platform + version + flags + total size,
list of type readers, shared resources, then the main object.
"""
import struct


class XnbError(ValueError):
    pass


def _read_7bit(data, pos):
    value_ = shift = 0
    while True:
        b = data[pos]
        pos += 1
        value_ |= (b & 0x7F) << shift
        if not b & 0x80:
            return value_, pos
        shift += 7
        if shift > 35:
            raise XnbError("intero a 7 bit non valido")


def _read_string(data, pos):
    n, pos = _read_7bit(data, pos)
    return data[pos:pos + n].decode("utf-8"), pos + n


def _read_char(data, pos):
    """BinaryReader.ReadChar: one UTF-8 character (1-4 bytes)."""
    b = data[pos]
    n = 1 if b < 0x80 else 2 if b >> 5 == 6 else 3 if b >> 4 == 14 else 4
    return data[pos:pos + n].decode("utf-8"), pos + n


def header(data):
    """(platform, version, flags, readers [(name, version)], position of the main object)."""
    if data[:3] != b"XNB":
        raise XnbError("non e' un file XNB")
    platform_id, version, flags = chr(data[3]), data[4], data[5]
    size = struct.unpack_from("<I", data, 6)[0]
    if size != len(data):
        raise XnbError("dimensione dichiarata diversa da quella del file")
    if flags & 0xC0:
        raise XnbError("XNB compresso: non ancora supportato")
    pos = 10
    n, pos = _read_7bit(data, pos)
    readers = []
    for _ in range(n):
        name, pos = _read_string(data, pos)
        ver = struct.unpack_from("<i", data, pos)[0]
        pos += 4
        readers.append((name, ver))
    shared_count, pos = _read_7bit(data, pos)
    if shared_count:
        raise XnbError("risorse condivise non supportate")
    return platform_id, version, flags, readers, pos


def _short_name(reader):
    return reader.split(",")[0].split("`")[0].rsplit(".", 1)[-1]


def read_spritefont(data):
    """SpriteFont -> {"caratteri": [...], "predefinito": character or None, "interlinea", "spaziatura"}."""
    _, _, _, readers, pos = header(data)

    def read_object(pos, expected):
        idx, pos = _read_7bit(data, pos)
        if idx == 0:
            raise XnbError("oggetto nullo inatteso")
        name = readers[idx - 1][0]
        if expected not in name:
            raise XnbError("atteso %s, trovato %s" % (expected, name))
        return pos

    pos = read_object(pos, "SpriteFontReader")
    pos = read_object(pos, "Texture2DReader")
    _fmt, width, height, levels = struct.unpack_from("<iIII", data, pos)
    pos += 16
    for _ in range(levels):
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4 + n
    for _ in range(2):  # glyph rectangles and cropping
        pos = read_object(pos, "ListReader")
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4 + 16 * n
    pos = read_object(pos, "ListReader")
    n = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    chars = []
    for _ in range(n):
        c, pos = _read_char(data, pos)
        chars.append(c)
    line_spacing, spacing = struct.unpack_from("<if", data, pos)
    pos += 8
    pos = read_object(pos, "ListReader")
    n = struct.unpack_from("<I", data, pos)[0]
    pos += 4 + 12 * n
    has_default = data[pos]
    pos += 1
    default = None
    if has_default:
        default, pos = _read_char(data, pos)
    if pos != len(data):
        raise XnbError("SpriteFont non letto fino in fondo (%d byte avanzati)" % (len(data) - pos))
    return {"caratteri": chars, "predefinito": default, "interlinea": line_spacing,
            "spaziatura": spacing, "texture": (width, height)}


def main_kind(data):
    """Short name of the main object's reader (e.g. 'SpriteFontReader', 'Texture2DReader')."""
    _, _, _, readers, pos = header(data)
    idx, _ = _read_7bit(data, pos)
    return _short_name(readers[idx - 1][0]) if idx else None
