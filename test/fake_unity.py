"""Pieces of fake Unity games for the tests (no files from real games):
- bundle(): UnityFS AssetBundle with the given internal files, uncompressed, LZ4 or LZMA, in blocks;
- tables_file(): serialized file (version 22, with the type trees) with Unity Localization text tables
  (StringTable: name, m_LocaleId, m_Localized entries);
- serialized_strings(): bytes with "length + UTF-8" strings as in asset files;
- catalog(): Addressables catalog.json with the options (and the CRC) of each bundle;
- dll(): minimal .NET DLL with the code's strings (#US heap);
- il2cpp_game(): Unity IL2CPP game with the bundles in StreamingAssets/aa/StandaloneWindows64.
"""
import base64
import json
import lzma
import os
import struct

# type tree of a StringTable (reduced: without metadata and managed references, which are at the end)
_TABLE_TREE = [
    (0, "MonoBehaviour", "Base", -1, False),
    (1, "PPtr<GameObject>", "m_GameObject", 12, False),
    (2, "int", "m_FileID", 4, False),
    (2, "SInt64", "m_PathID", 8, False),
    (1, "UInt8", "m_Enabled", 1, True),
    (1, "PPtr<MonoScript>", "m_Script", 12, False),
    (2, "int", "m_FileID", 4, False),
    (2, "SInt64", "m_PathID", 8, False),
    (1, "string", "m_Name", -1, False),
    (2, "Array", "Array", -1, True),
    (3, "int", "size", 4, False),
    (3, "char", "data", 1, False),
    (1, "LocaleIdentifier", "m_LocaleId", -1, False),
    (2, "string", "m_Code", -1, False),
    (3, "Array", "Array", -1, True),
    (4, "int", "size", 4, False),
    (4, "char", "data", 1, False),
    (1, "TableEntryData", "m_TableData", -1, False),
    (2, "Array", "Array", -1, False),
    (3, "int", "size", 4, False),
    (3, "TableEntryData", "data", -1, False),
    (4, "SInt64", "m_Id", 8, False),
    (4, "string", "m_Localized", -1, False),
    (5, "Array", "Array", -1, True),
    (6, "int", "size", 4, False),
    (6, "char", "data", 1, False),
]


def _string(text):
    b = text.encode("utf-8")
    return struct.pack("<i", len(b)) + b + b"\x00" * (-len(b) % 4)


def serialized_strings(texts, padding=b"\x07\x00\x00\x00"):
    """Bytes as in an asset file: each text with its length (4 bytes) and 4-byte alignment."""
    return padding + b"".join(_string(t) + padding for t in texts)


def _table_data(name, code, entries):
    b = struct.pack("<iq", 0, 0) + b"\x01\x00\x00\x00" + struct.pack("<iq", 0, 0) + _string(name) + _string(code)
    b += struct.pack("<i", len(entries))
    for k, text in enumerate(entries):
        b += struct.pack("<q", 1000 + k) + _string(text)
    return b


def tables_file(tables, unity_version="2022.3.62f2"):
    """Serialized file with the tables [(name, language code, [texts])]."""
    buffer, positions = bytearray(), {}

    def position(s):
        if s not in positions:
            positions[s] = len(buffer)
            buffer.extend(s.encode("ascii") + b"\x00")
        return positions[s]
    lines = b""
    for k, (level, kind, name, size, align) in enumerate(_TABLE_TREE):
        lines += struct.pack("<HBBIIiii", 1, level, 0, position(kind), position(name), size, k,
                             0x4000 if align else 0) + struct.pack("<Q", 0)
    kind = struct.pack("<iBh", 114, 0, 0) + b"\x00" * 32 + struct.pack("<ii", len(_TABLE_TREE), len(buffer))
    kind += lines + bytes(buffer) + struct.pack("<i", 0)
    objects = [_table_data(*t) for t in tables]
    half = unity_version.encode("ascii") + b"\x00" + struct.pack("<i", 19) + b"\x01" + struct.pack("<i", 1) + kind
    half += struct.pack("<i", len(objects))
    body, entries = b"", []
    for k, d in enumerate(objects):
        body += b"\x00" * (-len(body) % 8)
        entries.append((k + 1, len(body), len(d)))
        body += d
    for path_id, start, size in entries:
        half += b"\x00" * (-(48 + len(half)) % 4)
        half += struct.pack("<qqIi", path_id, start, size, 0)
    half += struct.pack("<iii", 0, 0, 0) + b"\x00"
    data_start = 48 + len(half)
    data_start += -data_start % 16
    total = data_start + len(body)
    head = struct.pack(">IIII", 0, 0, 22, 0) + b"\x00" * 4 + struct.pack(">IqqQ", len(half), total, data_start, 0)
    return head + half + b"\x00" * (data_start - 48 - len(half)) + body


# type tree of a TextMeshPro font (TMP_FontAsset, reduced: name, atlas mode, character table)
_TMP_FONT_TREE = _TABLE_TREE[:12] + [
    (1, "int", "m_AtlasPopulationMode", 4, False),
    (1, "vector", "m_CharacterTable", -1, False),
    (2, "Array", "Array", -1, False),
    (3, "int", "size", 4, False),
    (3, "TMP_Character", "data", -1, False),
    (4, "int", "m_ElementType", 4, False),
    (4, "unsigned int", "m_Unicode", 4, False),
    (4, "unsigned int", "m_GlyphIndex", 4, False),
    (4, "float", "m_Scale", 4, False),
]
# reduced tree of a .ttf font (Font: name and the file's bytes)
_FONT_TREE = [
    (0, "Font", "Base", -1, False),
    (1, "string", "m_Name", -1, False),
    (2, "Array", "Array", -1, True),
    (3, "int", "size", 4, False),
    (3, "char", "data", 1, False),
    (1, "vector", "m_FontData", -1, True),
    (2, "Array", "Array", -1, True),
    (3, "int", "size", 4, False),
    (3, "char", "data", 1, False),
]


def tmp_font_data(name, chars, mode=0):
    """The bytes of a TextMeshPro font: m_GameObject, m_Enabled, m_Script, m_Name, the atlas mode (0 static, 1
    dynamic) and the character table (type 1, code, glyph, scale 1.0)."""
    codes = sorted({ord(c) for c in chars})
    b = struct.pack("<iq", 0, 0) + b"\x01\x00\x00\x00" + struct.pack("<iq", 0, 0) + _string(name)
    b += struct.pack("<ii", mode, len(codes))
    for k, c in enumerate(codes):
        b += struct.pack("<IIIf", 1, c, k + 1, 1.0)
    return b


def ttf(chars):
    """A minimal TrueType font: only the cmap table (format 4) with these characters."""
    codes = sorted({ord(c) for c in chars if ord(c) < 0xFFFF}) + [0xFFFF]
    n = len(codes)
    end = b"".join(struct.pack(">H", c) for c in codes)
    start = end
    delta = b"".join(struct.pack(">H", ((k + 1) - c) & 0xFFFF) if c != 0xFFFF else struct.pack(">H", 1)
                     for k, c in enumerate(codes))
    subfolders = struct.pack(">HHHHHHH", 4, 16 + 8 * n, 0, 2 * n, 0, 0, 0) + end + b"\x00\x00" + start + delta + \
        b"\x00\x00" * n
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + subfolders
    return struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0) + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap


def font_data(name, ttf_bytes):
    return _string(name) + struct.pack("<i", len(ttf_bytes)) + ttf_bytes + b"\x00" * (-len(ttf_bytes) % 4)


def _kind(class_, tree, trees):
    """A type of the type table (version 22): class, (script), hash, type tree."""
    t = struct.pack("<iBh", class_, 0, 0) + b"\x00" * (32 if class_ == 114 else 16)
    if not trees:
        return t
    buffer, positions = bytearray(), {}

    def position(s):
        if s not in positions:
            positions[s] = len(buffer)
            buffer.extend(s.encode("ascii") + b"\x00")
        return positions[s]
    lines = b""
    for k, (level, kind, name, size, align) in enumerate(tree):
        lines += struct.pack("<HBBIIiii", 1, level, 0, position(kind), position(name), size, k,
                             0x4000 if align else 0) + struct.pack("<Q", 0)
    return t + struct.pack("<ii", len(tree), len(buffer)) + lines + bytes(buffer) + struct.pack("<i", 0)


def serialized_file(objects, trees=True, unity_version="2022.3.62f2"):
    """Serialized file (version 22) with the objects [(classe, albero, byte)]: one type for each (classe, albero);
    alberi=False like the files of <gioco>_Data (without the type trees)."""
    kinds, indices = [], {}
    for class_, tree, _ in objects:
        key = (class_, id(tree))
        if key not in indices:
            indices[key] = len(kinds)
            kinds.append(_kind(class_, tree, trees))
    half = unity_version.encode("ascii") + b"\x00" + struct.pack("<i", 19) + (b"\x01" if trees else b"\x00")
    half += struct.pack("<i", len(kinds)) + b"".join(kinds) + struct.pack("<i", len(objects))
    body, entries = b"", []
    for k, (class_, tree, d) in enumerate(objects):
        body += b"\x00" * (-len(body) % 8)
        entries.append((k + 1, len(body), len(d), indices[(class_, id(tree))]))
        body += d
    for path_id, start, size, kind in entries:
        half += b"\x00" * (-(48 + len(half)) % 4)
        half += struct.pack("<qqIi", path_id, start, size, kind)
    half += struct.pack("<iii", 0, 0, 0) + b"\x00"
    data_start = 48 + len(half)
    data_start += -data_start % 16
    total = data_start + len(body)
    head = struct.pack(">IIII", 0, 0, 22, 0) + b"\x00" * 4 + struct.pack(">IqqQ", len(half), total, data_start, 0)
    return head + half + b"\x00" * (data_start - 48 - len(half)) + body


def font_file(font_tmp=(), font_ttf=(), trees=True):
    """Serialized file with the TextMeshPro fonts [(nome, caratteri, modo)] and the .ttf fonts [(nome, caratteri)]."""
    objects = [(114, _TMP_FONT_TREE, tmp_font_data(*f)) for f in font_tmp]
    objects += [(128, _FONT_TREE, font_data(name, ttf(chars))) for name, chars in font_ttf]
    return serialized_file(objects, trees)


def _lz4_literals(b):
    """A valid LZ4 block made only of literals."""
    n = len(b)
    if n < 15:
        return bytes([n << 4]) + b
    output, rest = bytearray([0xF0]), n - 15
    while rest >= 255:
        output.append(255)
        rest -= 255
    output.append(rest)
    return bytes(output) + b


def _lzma(b):
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2}]
    return bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 20) + lzma.compress(
        b, format=lzma.FORMAT_RAW, filters=filters)


def bundle(nodes, compression=None, block=1 << 17, unity_version="2022.3.62f2"):
    """UnityFS AssetBundle (version 7) with the internal files [(nome, byte)]; compression None, 'lz4' or 'lzma'."""
    data = b"".join(b for _, b in nodes)
    blocks, body = [], b""
    for i in range(0, max(len(data), 1), block):
        piece = data[i:i + block]
        if compression == "lz4":
            c, kind = _lz4_literals(piece), 2
        elif compression == "lzma":
            c, kind = _lzma(piece), 1
        else:
            c, kind = piece, 0
        blocks.append((len(piece), len(c), kind))
        body += c
    info = b"\x00" * 16 + struct.pack(">i", len(blocks)) + b"".join(struct.pack(">IIH", *b) for b in blocks)
    info += struct.pack(">i", len(nodes))
    position = 0
    for name, b in nodes:
        info += struct.pack(">qqI", position, len(b), 4 if not name.endswith((".resS", ".resource")) else 0)
        info += name.encode("utf-8") + b"\x00"
        position += len(b)
    head = b"UnityFS\x00" + struct.pack(">I", 7) + b"5.x.x\x00" + unity_version.encode("ascii") + b"\x00"
    length = len(head) + 20
    length += -length % 16
    head += struct.pack(">qIII", length + len(info) + len(body), len(info), len(info), 0x40)
    return head + b"\x00" * (length - len(head)) + info + body


def table_bundles(tables, **options):
    return bundle([("CAB-%08x" % (hash(str(tables)) & 0xFFFFFFFF), tables_file(tables))], **options)


_RUNTIME = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\"


def catalog(bundle_crc, utf8=False):
    """Bytes of an Addressables catalog.json: for each (bundle file name, crc) its options
    (AssetBundleRequestOptions in JSON, UTF-16 as Unity writes them), plus an asset without options."""
    ids, extra, entries = ["Assets/Localization/Locales/English (en).asset"], bytearray(), [(0, 1, -1, 0, -1, 0, 0)]
    assembly = b"Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
    class_ = b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
    for k, (name, crc) in enumerate(bundle_crc):
        ids.append(("0#\\" if k % 2 else _RUNTIME) + name)  # also with the shortened prefix (m_InternalIdPrefixes)
        text = json.dumps({"m_Hash": "%032x" % (k + 1), "m_Crc": crc, "m_Timeout": 0, "m_ChunkedTransfer": False,
                            "m_BundleName": "%032x" % (k + 99), "m_BundleSize": 1234 + k},
                           separators=(",", ":")).encode("utf-8" if utf8 else "utf-16-le")
        idx = len(extra)
        extra += bytes([7, len(assembly)]) + assembly + bytes([len(class_)]) + class_
        extra += struct.pack("<i", len(text)) + text
        entries.append((len(ids) - 1, 0, -1, 0, idx, 0, 0))
    entry_bytes = struct.pack("<i", len(entries)) + b"".join(struct.pack("<7i", *v) for v in entries)
    data = {"m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIdPrefixes": [_RUNTIME.rstrip("\\")],
            "m_InternalIds": ids, "m_KeyDataString": "AAAAAA==",
            "m_EntryDataString": base64.b64encode(entry_bytes).decode("ascii"),
            "m_ExtraDataString": base64.b64encode(bytes(extra)).decode("ascii")}
    return json.dumps(data, separators=(",", ":")).encode("utf-8")


def catalog_crc(catalog_bytes):
    """{bundle file name: crc} read from the catalog (to check the tests)."""
    data = json.loads(catalog_bytes.decode("utf-8-sig"))
    extra = base64.b64decode(data["m_ExtraDataString"])
    entries = base64.b64decode(data["m_EntryDataString"])
    output = {}
    for k in range(struct.unpack_from("<i", entries, 0)[0]):
        inner, _, _, _, idx, _, _ = struct.unpack_from("<7i", entries, 4 + 28 * k)
        if idx < 0:
            continue
        q = idx + 1
        q += 1 + extra[q]
        q += 1 + extra[q]
        n = struct.unpack_from("<i", extra, q)[0]
        text = extra[q + 4:q + 4 + n]
        text = text.decode("utf-16-le" if text.startswith(b"{\x00") else "utf-8")
        output[data["m_InternalIds"][inner].replace("#", "\\").split("\\")[-1]] = json.loads(text)["m_Crc"]
    return output


def dll(strings):
    """A minimal .NET DLL: MZ header and metadata (BSJB) with the code's strings in the #US heap."""
    heap = bytearray(b"\x00")
    for s in strings:
        b = s.encode("utf-16-le") + b"\x00"
        n = len(b)
        if n < 0x80:
            heap.append(n)
        elif n < 0x4000:
            heap += bytes([0x80 | (n >> 8), n & 0xFF])
        else:
            heap += bytes([0xC0 | (n >> 24), (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
        heap += b
    heap += b"\x00" * (-len(heap) % 4)
    version = b"v4.0.30319\x00\x00"
    name = b"#US\x00"
    head = b"BSJB" + struct.pack("<HHI", 1, 1, 0) + struct.pack("<I", len(version)) + version
    head += struct.pack("<HH", 0, 1)
    head += struct.pack("<II", len(head) + 8 + len(name), len(heap)) + name
    return b"MZ" + b"\x00" * 62 + head + bytes(heap)


def _write(path, byte):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(byte)


def x64_executable():
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    return bytes(pe)


def il2cpp_game(folder, bundle_aa, aa_catalog=None, extra=None):
    """Fake Unity IL2CPP game. bundle_aa: {file name: bytes} in StreamingAssets/aa/StandaloneWindows64;
    aa_catalog: (name, bytes) of the catalog in aa (catalog.json or catalog.bin); extra: {relative path: bytes}.
    Returns the bundle folder."""
    _write(os.path.join(folder, "Gioco.exe"), x64_executable())
    _write(os.path.join(folder, "UnityPlayer.dll"), b"finto")
    _write(os.path.join(folder, "GameAssembly.dll"), b"finto")
    data = os.path.join(folder, "Gioco_Data")
    _write(os.path.join(data, "globalgamemanagers"), b"\x00" * 48 + b"2022.3.62f2\x00")
    _write(os.path.join(data, "il2cpp_data", "Metadata", "global-metadata.dat"), b"\xaf\x1b\xb1\xfa finto")
    _write(os.path.join(data, "app.info"), b"Studio Finto\nGioco Finto")
    aa = os.path.join(data, "StreamingAssets", "aa")
    bundle_folder = os.path.join(aa, "StandaloneWindows64")
    os.makedirs(bundle_folder, exist_ok=True)
    for name, byte in bundle_aa.items():
        _write(os.path.join(bundle_folder, name), byte)
    if aa_catalog:
        _write(os.path.join(aa, aa_catalog[0]), aa_catalog[1])
    for rel, byte in (extra or {}).items():
        _write(os.path.join(folder, *rel.split("/")), byte)
    return bundle_folder
