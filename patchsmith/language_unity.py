"""Official language in Unity games: which languages the game has and, if it uses I2 Localization, how to set it.

Detection (read only):
- I2 Localization: the language lists (name, code, flag) saved in the assets (resources.assets, level*,
  sharedassets*, also inside date.unity3d); a language with the "disabled" flag does not count;
- Unity Localization: the "Locales" in the Addressables catalog (StreamingAssets/aa).

Setting: only for I2, which saves the chosen language in the PlayerPrefs (Windows registry,
HKCU\\Software\\<Azienda>\\<Prodotto>, value "I2 Language_h<hash>" = exact name of the language, UTF-8 + NUL).
Only that value is changed, with the game closed and only if the game's key already exists (game started at least
once), with a backup of the previous value and restore. Unity Localization: instructions.
"""
import lzma
import mmap
import os
import re
import struct
import sys

PREF_I2 = "I2 Language"
_ASSET = re.compile(r"^(resources\.assets|globalgamemanagers\.assets|level\d+|sharedassets\d+\.assets)$")
_MAXIMUM = 400 * 1024 * 1024  # maximum bytes of assets read per game


class UnityLanguageError(ValueError):
    pass


# --- data folder and app.info -------------------------------------------------------------------

def data_folder(folder):
    for root, dirs, files in os.walk(folder):
        if os.path.relpath(root, folder).count(os.sep) >= 2:
            dirs[:] = []
        for d in sorted(dirs):
            p = os.path.join(root, d)
            if d.endswith("_Data") and (os.path.exists(os.path.join(p, "globalgamemanagers")) or
                                        os.path.exists(os.path.join(p, "data.unity3d"))):
                return p
    return None


def company_product(data):
    try:
        with open(os.path.join(data, "app.info"), encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return None, None
    return (lines[0].strip() or None), (lines[1].strip() if len(lines) > 1 and lines[1].strip() else None)


# --- UnityFS (date.unity3d) -----------------------------------------------------------------------

def _lz4(src, dim):
    try:
        return _lz4_block(src, dim)
    except IndexError as e:
        raise UnityLanguageError("LZ4 is invalid") from e


def _lz4_block(src, dim):
    out, i, n = bytearray(), 0, len(src)
    while i < n:
        tok = src[i]
        i += 1
        lit = tok >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += src[i:i + lit]
        i += lit
        if i >= n:
            break
        off = src[i] | (src[i + 1] << 8)
        i += 2
        length = tok & 15
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(out) - off
        if off <= 0 or start < 0:
            raise UnityLanguageError("LZ4 is invalid")
        if off >= length:
            out += out[start:start + length]
        else:
            for k in range(length):
                out.append(out[start + k])
    if len(out) != dim:
        raise UnityLanguageError("LZ4: Unexpected size")
    return out


def _decompress(data, kind, dim):
    if kind == 0:
        return data
    if kind == 1:
        props = data[0]
        filter_ = [{"id": lzma.FILTER_LZMA1, "lc": props % 9, "lp": (props // 9) % 5, "pb": props // 45,
                   "dict_size": struct.unpack_from("<I", data, 1)[0]}]
        return lzma.decompress(data[5:], format=lzma.FORMAT_RAW, filters=filter_)[:dim]
    if kind in (2, 3):
        return _lz4(data, dim)
    raise UnityLanguageError("compression %d not supported" % kind)


def _open_bundle(path):
    f = open(path, "rb")
    try:
        head = f.read(512)
        p = head.index(b"\0")
        if head[:p] != b"UnityFS":
            raise UnityLanguageError("this is not a UnityFS bundle")
        p += 1
        version = struct.unpack_from(">I", head, p)[0]
        p += 4
        for _ in range(2):
            p = head.index(b"\0", p) + 1
        total, c_info, u_info, flags = struct.unpack_from(">qIII", head, p)
        p += 20
        if version >= 7:
            p = (p + 15) & ~15
        f.seek(total - c_info if flags & 0x80 else p)
        info = _decompress(f.read(c_info), flags & 0x3F, u_info)
        if not flags & 0x80:
            p += c_info
            if flags & 0x200:
                p = (p + 15) & ~15
        q = 16  # hash of the uncompressed data
        count = struct.unpack_from(">i", info, q)[0]
        q += 4
        blocks = []
        for _ in range(count):
            blocks.append(struct.unpack_from(">IIH", info, q))
            q += 10
        count = struct.unpack_from(">i", info, q)[0]
        q += 4
        nodes = []
        for _ in range(count):
            off, dim, _flag = struct.unpack_from(">qqI", info, q)
            q += 20
            end = info.index(b"\0", q)
            nodes.append((info[q:end].decode("utf-8", "replace"), off, dim))
            q = end + 1
        return f, p, blocks, nodes
    except BaseException:
        f.close()
        raise


def _extract(f, start, blocks, off, dim):
    out, pos_c, pos_u = bytearray(), start, 0
    for u, c, fl in blocks:
        if pos_u + u > off and pos_u < off + dim:
            f.seek(pos_c)
            block = _decompress(f.read(c), fl & 0x3F, u)
            out += block[max(off, pos_u) - pos_u:min(off + dim, pos_u + u) - pos_u]
        pos_c += c
        pos_u += u
        if pos_u >= off + dim:
            break
    return out


def _mapping(path):
    """The file, read only, as an mmap (the memory is not taken all at once); b"" if it is empty."""
    with open(path, "rb") as f:
        if os.fstat(f.fileno()).st_size == 0:
            return b""
        return mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)


def _asset_file(data):
    """([(name, size, function that returns the bytes)], function to close) of the game's serialized files, the
    global catalog first."""
    output, opened = [], []
    bundle = os.path.join(data, "data.unity3d")
    if os.path.exists(bundle):
        try:
            f, start, blocks, nodes = _open_bundle(bundle)
            opened.append(f)
            for name, off, dim in nodes:
                if _ASSET.match(name):
                    output.append((name, dim, lambda off=off, dim=dim: _extract(f, start, blocks, off, dim)))
        except (UnityLanguageError, OSError, struct.error, ValueError, IndexError, lzma.LZMAError):
            pass
    for name in sorted(os.listdir(data)):
        if _ASSET.match(name):
            p = os.path.join(data, name)
            try:
                dim = os.path.getsize(p)
            except OSError:
                continue
            output.append((name, dim, lambda p=p: _mapping(p)))

    def order(entry):
        name = entry[0]
        if name == "resources.assets":
            return (0, 0)
        if name == "globalgamemanagers.assets":
            return (1, 0)
        return (2 if name.startswith("level") else 3, int(re.sub(r"\D", "", name) or 0))

    def close():
        for f in opened:
            f.close()
    return sorted(output, key=order), close


# --- I2 Localization ------------------------------------------------------------------------------

_I2_CODE = re.compile(r"^[ -~]{1,24}$")


def _string(d, p):
    if p + 4 > len(d):
        return None, p
    n = struct.unpack_from("<i", d, p)[0]
    if not 0 <= n <= 200 or p + 4 + n > len(d):
        return None, p
    try:
        s = d[p + 4:p + 4 + n].decode("utf-8")
    except UnicodeDecodeError:
        return None, p
    return s, p + 4 + ((n + 3) & ~3)


def _try_list(d, p):
    if p + 4 > len(d):
        return None
    how_many = struct.unpack_from("<i", d, p)[0]
    if not 1 <= how_many <= 120:
        return None
    q, entries = p + 4, []
    for _ in range(how_many):
        name, q = _string(d, q)
        if not name or len(name) < 2 or not all(c.isprintable() or c in "\n\r\t" for c in name):
            return None
        code, q = _string(d, q)
        if code is None or (code and not _I2_CODE.match(code)) or q + 4 > len(d):
            return None
        flag = d[q]
        if d[q + 1:q + 4] != b"\x00\x00\x00" or flag > 0x0F:
            return None
        entries.append((name, code, flag))
        q += 4
    return entries, q


def i2_lists(d):
    """[[(nome, codice, flag)]] of the I2 language lists found in the bytes of an asset."""
    output, seen, last_end = [], set(), -1
    for m in re.finditer(rb"\x02\x00\x00\x00en\x00\x00|\x05\x00\x00\x00en-[A-Z]{2}\x00\x00\x00", d):
        if m.start() < last_end:
            continue  # inside a list already read
        best = None
        for back in range(m.start() - 4, max(0, m.start() - 16000), -4):
            r = _try_list(d, back)
            if r and r[1] > m.start() and (best is None or len(r[0]) > len(best[1][0])):
                best = (back, r)
        if best and best[0] not in seen:
            seen.add(best[0])
            output.append(best[1][0])
            last_end = best[1][1]
    return output


def uses_i2(data):
    """True if the game contains I2 Localization (from the managed libraries or the IL2CPP metadata)."""
    managed_folder = os.path.join(data, "Managed")
    if os.path.isdir(managed_folder):
        if any(n.lower().startswith("i2") and "locali" in n.lower() for n in os.listdir(managed_folder)):
            return True
        for name in ("Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll"):
            p = os.path.join(managed_folder, name)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    if b"I2.Loc\x00" in f.read():
                        return True
    half = os.path.join(data, "il2cpp_data", "Metadata", "global-metadata.dat")
    if os.path.exists(half):
        with open(half, "rb") as f:
            return b"I2.Loc\x00" in f.read()
    return False


def i2_languages(data, after_first=2):
    """[(exact name, code)] of the ACTIVE I2 languages, or None if no lists are found.
    Reads at most _MAXIMUM bytes (files that would exceed it are skipped) and, once the first list is found, only
    'dopo_la_prima' more files (the lists of the single scenes usually repeat the main one)."""
    active_langs, matches, read_items, after = {}, False, 0, None
    try:
        entries, close = _asset_file(data)
    except (OSError, ValueError):
        return None
    try:
        for _name, size, load in entries:
            if after is not None:
                if after >= after_first:
                    break
                after += 1
            if read_items + size > _MAXIMUM:
                continue
            read_items += size
            byte = None
            try:
                byte = load()
                lists = i2_lists(byte)
            except Exception:  # an unreadable file must not lose what was already found
                continue
            finally:
                if isinstance(byte, mmap.mmap):
                    byte.close()
                byte = None
            for list_ in lists:
                if any(c for _, c, _ in list_):
                    matches = True
                    for n, c, flag in list_:
                        if not flag & 1:
                            active_langs.setdefault(n, c)
            if matches and after is None:
                after = 0
    finally:
        close()
    return list(active_langs.items()) if matches else None


def i2_custom(data):
    """True if the game modified I2 to save the language its own way (e.g. 'LastSelectedLanguage',
    'last_language.txt'): then the "I2 Language" preference is not enough and the language is chosen from the menu."""
    markers = [s for t in ("LastSelectedLanguage", "last_language.txt")
             for s in (t.encode("ascii"), t.encode("utf-16-le"))]
    file = []
    managed_folder = os.path.join(data, "Managed")
    if os.path.isdir(managed_folder):
        file += [os.path.join(managed_folder, n) for n in os.listdir(managed_folder)
                 if n.lower().startswith("i2") and n.lower().endswith(".dll")]
    file.append(os.path.join(data, "il2cpp_data", "Metadata", "global-metadata.dat"))
    for p in file:
        try:
            with open(p, "rb") as f:
                content = f.read(64 * 1024 * 1024)
        except OSError:
            continue
        if any(s in content for s in markers):
            return True
    return False


# --- Unity Localization ---------------------------------------------------------------------------

_LOCALE_TEXT = re.compile(rb"Locales/[^/\"\x00]{1,60} \(([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\)\.asset")
_LOCALE_BINARY = re.compile(
    rb"(?:^|[/\x00-\x1f\"])[A-Za-z][^/\"\x00-\x1f]{0,60} \(([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\)\.asset")


def unity_localization_languages(data):
    """{code} of the Locales in the Addressables catalog, or None if the game does not use it."""
    aa = os.path.join(data, "StreamingAssets", "aa")
    if not os.path.isdir(aa):
        return None
    codes = set()
    for f in os.listdir(aa):
        if not f.startswith("catalog") or f.endswith(".hash"):
            continue
        p = os.path.join(aa, f)
        try:
            if os.path.getsize(p) > 256 * 1024 * 1024:
                continue
            with open(p, "rb") as g:
                b = g.read()
        except OSError:
            continue
        for m in _LOCALE_TEXT.finditer(b):
            codes.add(m.group(1).decode())
        # binary catalog (catalog.bin): the names are not necessarily under "Locales/"; they count only if the file
        # uses Unity Localization and each code also has its own "Locale-<codice>" label
        if f.endswith(".bin") and (b"Localization.Locale" in b or b"Localization/Locales" in b):
            for m in _LOCALE_BINARY.finditer(b):
                if b"Locale-" + m.group(1) in b:
                    codes.add(m.group(1).decode())
    return codes or None


# --- PlayerPrefs (Windows registry) ---------------------------------------------------------------

def pref_name(key):
    """Name of the registry value for a PlayerPrefs key: key + '_h' + djb2-xor hash of the UTF-8 bytes."""
    h = 5381
    for b in key.encode("utf-8"):
        h = ((h * 33) ^ b) & 0xFFFFFFFF
    return "%s_h%d" % (key, h)


def _registry_key(company, product):
    return "Software\\%s\\%s" % (company, product)


def read_pref(company, product, key):
    """(tipo, byte) of the value, None if the value is missing, False if the game's key is missing."""
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _registry_key(company, product), 0, winreg.KEY_READ) as k:
            try:
                value_, kind = winreg.QueryValueEx(k, pref_name(key))
            except FileNotFoundError:
                return None
    except FileNotFoundError:
        return False
    if kind == winreg.REG_BINARY:
        return kind, bytes(value_)
    if kind == winreg.REG_SZ:
        return kind, str(value_).encode("utf-8")
    return kind, value_ if isinstance(value_, (bytes, bytearray)) else str(value_).encode()


def write_pref_string(company, product, key, text):
    """Writes a PlayerPrefs string (REG_BINARY UTF-8 + NUL) to an ALREADY existing key of the game."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _registry_key(company, product), 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, pref_name(key), 0, winreg.REG_BINARY, text.encode("utf-8") + b"\x00")
    was_read = read_pref(company, product, key)
    if not was_read or was_read[1] != text.encode("utf-8") + b"\x00":
        raise UnityLanguageError("the written preference doesn't read back the same")


def restore_pref(company, product, key, previous, written):
    """Puts back the previous value ((tipo, hex) or None = absent), only if the one we wrote is still there."""
    import winreg
    current = read_pref(company, product, key)
    if not current or current[1] != written:
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _registry_key(company, product), 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if previous is None:
            winreg.DeleteValue(k, pref_name(key))
        else:
            kind, byte = int(previous[0]), bytes.fromhex(previous[1])
            winreg.SetValueEx(k, pref_name(key), 0, kind, _registry_value(kind, byte))
    return True


def _registry_value(kind, byte):
    """The value in the form winreg wants for that type (leggi_pref always gives it as bytes)."""
    import winreg
    if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return byte.decode("utf-8")
    if kind in (winreg.REG_DWORD, winreg.REG_QWORD):
        return int(byte.decode("ascii"))
    return byte


def i2_entry(entries, dest_lang="it"):
    """The exact name of the I2 entry [(nome, codice)] for the language 'destinazione', or None: first by code
    (same code, then the language without region, then a variant; never the other Chinese script), then, for
    Italian without a suitable code, by name."""
    from .language import best
    chosen = best([c for _, c in entries if c], dest_lang)
    if chosen is not None:
        return next(n for n, c in entries if c == chosen)
    if dest_lang.split("-")[0].lower() == "it":
        return next((n for n, _ in entries if "itali" in (n or "").lower()), None)
    return None


def is_italian(name, code, dest_lang="it"):
    return i2_entry([(name, code)], dest_lang) is not None
