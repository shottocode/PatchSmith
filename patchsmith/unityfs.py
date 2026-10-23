"""Reading and writing Unity files: AssetBundles (UnityFS) and the serialized files inside them (standard library
only).

- UnityFS: header, compressed blocks (LZ4/LZ4HC in pure Python, LZMA with the standard library) and the inner
  files. It is rewritten without compression (Unity accepts it); the data of the inner files stays identical byte for
  byte except for the changed ones. Index reads a large bundle in pieces, only the blocks it needs (read only).
- Serialized file (versions 17-22): types with their field tree (type tree), object table, data.
  An object is read by following the field tree; to change some texts only those strings are rewritten
  (length, text, 4-byte alignment) and positions and sizes are updated in the object table.
"""
import lzma
import struct


class UnityError(ValueError):
    pass


class _VariableField(Exception):
    """Field with a variable structure (e.g. the data of managed references, at the end of the object)."""

    def __init__(self, path):
        Exception.__init__(self, "/".join(map(str, path)))
        self.path = path


# --- LZ4 (block format, the one used by bundles) ---------------------------------------------------------------

def lz4_decompress(src, size):
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        literals = token >> 4
        if literals == 15:
            while True:
                b = src[i]
                i += 1
                literals += b
                if b != 255:
                    break
        dst += src[i:i + literals]
        i += literals
        if i >= n:
            break
        distance = src[i] | (src[i + 1] << 8)
        i += 2
        if distance == 0:
            raise UnityError("LZ4 is invalid")
        length = token & 15
        if length == 15:
            while True:
                b = src[i]
                i += 1
                length += b
                if b != 255:
                    break
        length += 4
        start = len(dst) - distance
        if start < 0:
            raise UnityError("LZ4 is invalid")
        if distance >= length:
            dst += dst[start:start + length]
        else:
            for k in range(length):
                dst.append(dst[start + k])
    if len(dst) != size:
        raise UnityError("LZ4: Size different from expected")
    return bytes(dst)


def _decompress(data, kind, size):
    if kind == 0:
        return data
    if kind in (2, 3):
        return lz4_decompress(data, size)
    if kind == 1:  # Unity's LZMA: 5 bytes of properties, then the stream without a size
        props = data[0]
        lc, lp, pb = props % 9, (props // 9) % 5, (props // 9) // 5
        dictionary = struct.unpack_from("<I", data, 1)[0]
        filters = [{"id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb, "dict_size": dictionary}]
        return lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(data[5:])[:size]
    raise UnityError("unsupported compression (%d)" % kind)


def _cstring(data, p):
    end = data.index(b"\x00", p)
    return data[p:end].decode("utf-8", "replace"), end + 1


# --- UnityFS ----------------------------------------------------------------------------------------------------

def _header(read_file, file_size):
    """Header of an AssetBundle: (version, player version, Unity version, flags, blocks, nodes, start of the
    data). read_file(posizione, quanti) returns the file's bytes; blocks = [(decompresso, compresso, segni)],
    nodes = [(nome, inizio, dimensione, segni)] with the positions in the decompressed data."""
    head = read_file(0, 1024)
    if not head.startswith(b"UnityFS\x00"):
        raise UnityError("this is not a UnityFS AssetBundle")
    try:
        p = 8
        version = struct.unpack_from(">I", head, p)[0]
        p += 4
        player_version, p = _cstring(head, p)
        unity_version, p = _cstring(head, p)
        _readable_size, compressed, decompressed, markers = struct.unpack_from(">qIII", head, p)
    except (struct.error, ValueError) as e:
        raise UnityError("bundle header is invalid") from e
    p += 20
    if version >= 7:
        p = (p + 15) // 16 * 16
    if markers & 0x80:  # block information at the end of the file
        info = read_file(file_size - compressed, compressed)
        data_start = p
    else:
        info = read_file(p, compressed)
        data_start = p + compressed
    if len(info) != compressed:
        raise UnityError("truncated bundle")
    try:
        info = _decompress(info, markers & 0x3F, decompressed)
    except (lzma.LZMAError, IndexError, struct.error) as e:
        raise UnityError("invalid bundle index (%s)" % type(e).__name__) from e
    if markers & 0x200:
        data_start = (data_start + 15) // 16 * 16
    try:
        q = 16  # hash (not used)
        nblocks = struct.unpack_from(">i", info, q)[0]
        q += 4
        blocks = []
        for _ in range(nblocks):
            blocks.append(struct.unpack_from(">IIH", info, q))
            q += 10
        nnodes = struct.unpack_from(">i", info, q)[0]
        q += 4
        nodes = []
        for _ in range(nnodes):
            shift, size, node_flags = struct.unpack_from(">qqI", info, q)
            q += 20
            name, q = _cstring(info, q)
            nodes.append((name, shift, size, node_flags))
    except (struct.error, ValueError) as e:
        raise UnityError("invalid bundle index") from e
    return version, player_version, unity_version, markers, blocks, nodes, data_start


class Bundle:
    """An AssetBundle: self.file = {name: bytes} of the inner files, in the original order."""

    def __init__(self, data):
        (self.version, self.player_version, self.unity_version, self.markers, blocks, nodes,
         data_start) = _header(lambda p, n: data[p:p + n], len(data))
        pieces, p = [], data_start
        try:
            for decompressed_b, compressed_b, flags_b in blocks:
                pieces.append(_decompress(data[p:p + compressed_b], flags_b & 0x3F, decompressed_b))
                p += compressed_b
        except (lzma.LZMAError, IndexError, struct.error) as e:
            raise UnityError("invalid bundle block (%s)" % type(e).__name__) from e
        whole = b"".join(pieces)
        self.nodes = [(name, markers) for name, _, _, markers in nodes]
        self.file = {name: whole[s:s + d] for name, s, d, _ in nodes}

    def serialize(self):
        """The bundle without compression (one block), with the same inner files."""
        data = b"".join(self.file[name] for name, _ in self.nodes)
        info = bytearray(16)
        info += struct.pack(">i", 1) + struct.pack(">IIH", len(data), len(data), 0x40)
        info += struct.pack(">i", len(self.nodes))
        p = 0
        for name, markers in self.nodes:
            d = len(self.file[name])
            info += struct.pack(">qqI", p, d, markers) + name.encode("utf-8") + b"\x00"
            p += d
        markers = 0x40 | (self.markers & 0x200)  # information right after the header, no compression
        head = b"UnityFS\x00" + struct.pack(">I", self.version) + self.player_version.encode() + b"\x00" + \
            self.unity_version.encode() + b"\x00"
        head_length = len(head) + 20
        if self.version >= 7:
            head_length = (head_length + 15) // 16 * 16
        data_start = head_length + len(info)
        if markers & 0x200:
            data_start = (data_start + 15) // 16 * 16
        total = data_start + len(data)
        head += struct.pack(">qIII", total, len(info), len(info), markers)
        output = head + b"\x00" * (head_length - len(head)) + bytes(info)
        output += b"\x00" * (data_start - len(output)) + data
        return output


MAX_BLOCK = 256 * 1024 * 1024  # larger LZ4 blocks (decompressed) are not read: too much memory at once
# (uncompressed blocks and large LZMA ones are read in pieces of _LZMA_PIECE)
_LZMA_PIECE = 16 * 1024 * 1024


class Index:
    """An AssetBundle read from the file in pieces, without decompressing all of it (e.g. a date.unity3d of hundreds of
    MB): only the index of the blocks and of the inner files (self.nodes = [(nome, inizio, dimensione, segni)]);
    pieces() returns the decompressed bytes of a part little by little."""

    def __init__(self, path):
        self._f = open(path, "rb")
        try:
            size = self._f.seek(0, 2)
            (self.version, _player, self.unity_version, self.markers, self.blocks, self.nodes,
             self.data_start) = _header(self._read, size)
        except BaseException:
            self._f.close()
            raise

    def _read(self, position, count):
        self._f.seek(max(0, position))
        return self._f.read(count)

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _uncompressed(self, position, count):
        """The bytes of an uncompressed block, in pieces of 16 MB: a single block can be as large as the bundle
        (even GBs). A truncated file gives what is there."""
        read_items = 0
        while read_items < count:
            piece = self._read(position + read_items, min(_LZMA_PIECE, count - read_items))
            if not piece:
                return
            read_items += len(piece)
            yield piece

    def _block(self, position, compressed, decompressed, kind):
        """The decompressed bytes of a block, in one or more pieces (LZMA in pieces of 16 MB)."""
        if kind == 0:
            yield from self._uncompressed(position, compressed)
            return
        if kind == 1 and decompressed > _LZMA_PIECE:  # large LZMA (old Unity versions): in pieces, reading too
            head = self._read(position, 5)
            if len(head) < 5:
                raise UnityError("truncated bundle")
            props = head[0]
            filters = [{"id": lzma.FILTER_LZMA1, "lc": props % 9, "lp": (props // 9) % 5, "pb": (props // 9) // 5,
                       "dict_size": struct.unpack_from("<I", head, 1)[0]}]
            d = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters)
            read_items, rest = 5, decompressed
            while rest > 0 and not d.eof:  # stream ended early: another decompress() would raise EOFError
                incoming = b""
                if d.needs_input:
                    incoming = self._read(position + read_items, min(4 * 1024 * 1024, compressed - read_items))
                    read_items += len(incoming)
                    if not incoming:
                        break
                piece = d.decompress(incoming, max_length=min(_LZMA_PIECE, rest))
                if piece:
                    rest -= len(piece)
                    yield piece
                elif not incoming and not d.needs_input:
                    break
            return
        if decompressed > MAX_BLOCK:
            raise UnityError("block too large (%d MB)" % (decompressed // (1024 * 1024)))
        yield _decompress(self._read(position, compressed), kind, decompressed)

    def pieces(self, start=0, end=None):
        """Decompressed [(posizione, byte)] covering the data from 'inizio' to 'end' (positions in the decompressed
        data, e.g. an inner file: inizio, inizio + dimensione). Only the blocks needed are read."""
        if end is None:
            end = sum(b[0] for b in self.blocks)
        pos_c, pos_u = self.data_start, 0
        for decompressed, compressed, markers in self.blocks:
            if pos_u >= end:
                break
            if pos_u + decompressed > start and markers & 0x3F == 0:  # uncompressed: only the part needed
                a = max(start, pos_u)
                for piece in self._uncompressed(pos_c + a - pos_u,
                                                      min(end, pos_u + min(decompressed, compressed)) - a):
                    yield a, piece
                    a += len(piece)
            elif pos_u + decompressed > start:
                p = pos_u
                try:
                    for piece in self._block(pos_c, compressed, decompressed, markers & 0x3F):
                        a, b = max(start, p), min(end, p + len(piece))
                        if a < b:
                            yield a, piece[a - p:b - p]
                        p += len(piece)
                except (lzma.LZMAError, IndexError, struct.error) as e:  # corrupted data
                    raise UnityError("invalid bundle block (%s)" % type(e).__name__) from e
            pos_c += compressed
            pos_u += decompressed


# --- serialized file --------------------------------------------------------------------------------------------

_SIMPLE_ESCAPES = {"SInt8": "b", "UInt8": "B", "char": "B", "bool": "?", "SInt16": "h", "UInt16": "H", "short": "h",
             "unsigned short": "H", "SInt32": "i", "UInt32": "I", "int": "i", "unsigned int": "I", "Type*": "I",
             "SInt64": "q", "UInt64": "Q", "long long": "q", "unsigned long long": "Q", "FileSize": "Q",
             "float": "f", "double": "d"}


class Node:
    __slots__ = ("kind", "name", "size", "level", "align", "children")

    def __init__(self, kind, name, size, level, align):
        self.kind, self.name, self.size, self.level, self.align = kind, name, size, level, align
        self.children = []


_COMMON_STRINGS = None


def _common_strings():
    """The built-in strings of type trees (when the offset's high bit is 1)."""
    global _COMMON_STRINGS
    if _COMMON_STRINGS is None:
        text = ("AABB\x00AnimationClip\x00AnimationCurve\x00AnimationState\x00Array\x00Base\x00BitField\x00bitset\x00"
                 "bool\x00char\x00ColorRGBA\x00Component\x00data\x00deque\x00double\x00dynamic_array\x00FastPropertyName"
                 "\x00first\x00float\x00Font\x00GameObject\x00Generic Mono\x00GradientNEW\x00GUID\x00GUIStyle\x00int\x00"
                 "list\x00long long\x00map\x00Matrix4x4f\x00MdFour\x00MonoBehaviour\x00MonoScript\x00m_ByteSize\x00"
                 "m_Curve\x00m_EditorClassIdentifier\x00m_EditorHideFlags\x00m_Enabled\x00m_ExtensionPtr\x00"
                 "m_GameObject\x00m_Index\x00m_IsArray\x00m_IsStatic\x00m_MetaFlag\x00m_Name\x00m_ObjectHideFlags\x00"
                 "m_PrefabInternal\x00m_PrefabParentObject\x00m_Script\x00m_StaticEditorFlags\x00m_Type\x00m_Version\x00"
                 "Object\x00pair\x00PPtr<Component>\x00PPtr<GameObject>\x00PPtr<Material>\x00PPtr<MonoBehaviour>\x00"
                 "PPtr<MonoScript>\x00PPtr<Object>\x00PPtr<Prefab>\x00PPtr<Sprite>\x00PPtr<TextAsset>\x00PPtr<Texture>"
                 "\x00PPtr<Texture2D>\x00PPtr<Transform>\x00Prefab\x00Quaternionf\x00Rectf\x00RectInt\x00RectOffset\x00"
                 "second\x00set\x00short\x00size\x00SInt16\x00SInt32\x00SInt64\x00SInt8\x00staticvector\x00string\x00"
                 "TextAsset\x00TextMesh\x00Texture\x00Texture2D\x00Transform\x00TypelessData\x00UInt16\x00UInt32\x00"
                 "UInt64\x00UInt8\x00unsigned int\x00unsigned long long\x00unsigned short\x00vector\x00Vector2f\x00"
                 "Vector3f\x00Vector4f\x00m_ScriptingClassIdentifier\x00Gradient\x00Type*\x00int2_storage\x00"
                 "int3_storage\x00BoundsInt\x00m_CorrespondingSourceObject\x00m_PrefabInstance\x00m_PrefabAsset\x00"
                 "FileSize\x00Hash128\x00RenderingLayerMask\x00")
        _COMMON_STRINGS, p = {}, 0
        for part in text.split("\x00")[:-1]:
            _COMMON_STRINGS[p] = part
            p += len(part.encode("utf-8")) + 1
    return _COMMON_STRINGS


def _tree_text(buffer, offset):
    if offset & 0x80000000:
        return _common_strings().get(offset & 0x7FFFFFFF, "?")
    return _cstring(buffer, offset)[0]


class ObjectInfo:
    __slots__ = ("path_id", "start", "size", "kind", "pos_start", "pos_size")


class SerializedFile:
    """Unity serialized file (versions 17-22, little endian, with type tree)."""

    def __init__(self, data):
        self.data = data
        ms, fs, version, data_start = struct.unpack_from(">IIII", data, 0)
        self.version = version
        if not 17 <= version <= 22:
            raise UnityError("unsupported serialized file version (%d)" % version)
        if data[16] != 0:
            raise UnityError("big-endian serialized file not supported")
        p = 20
        if version >= 22:
            ms, fs, data_start, _ = struct.unpack_from(">IqqQ", data, p)
            p += 28
        self.data_start = data_start
        self.unity_version, p = _cstring(data, p)
        p += 4  # platform
        self.with_trees = data[p] != 0
        p += 1
        ntypes = struct.unpack_from("<i", data, p)[0]
        p += 4
        self.kinds = []
        for _ in range(ntypes):
            kind, p = self._read_type(p, False)
            self.kinds.append(kind)
        nobjs = struct.unpack_from("<i", data, p)[0]
        p += 4
        self.objects = []
        for _ in range(nobjs):
            p = (p + 3) // 4 * 4
            o = ObjectInfo()
            o.path_id = struct.unpack_from("<q", data, p)[0]
            p += 8
            o.pos_start = p
            if version >= 22:
                o.start = struct.unpack_from("<q", data, p)[0]
                p += 8
            else:
                o.start = struct.unpack_from("<I", data, p)[0]
                p += 4
            o.pos_size = p
            o.size, o.kind = struct.unpack_from("<Ii", data, p)
            p += 8
            self.objects.append(o)

    def _read_type(self, p, reference):
        d = self.data
        class_ = struct.unpack_from("<i", d, p)[0]
        p += 4 + 1 + 2  # class, stripped, script index
        if (reference and True) or class_ == 114:
            p += 16
        p += 16  # hash
        root = None
        if self.with_trees:
            nnodes, lbuffer = struct.unpack_from("<ii", d, p)
            p += 8
            node_size = 32 if self.version >= 19 else 24
            raw_texts = [struct.unpack_from("<HBBIIiii", d, p + k * node_size) for k in range(nnodes)]
            p += nnodes * node_size
            buffer = d[p:p + lbuffer]
            p += lbuffer
            stack = []
            for _ver, level, _flags, off_type, off_name, size, _index, half in raw_texts:
                tree_node = Node(_tree_text(buffer, off_type), _tree_text(buffer, off_name), size, level,
                            bool(half & 0x4000))
                while stack and stack[-1].level >= level:
                    stack.pop()
                if stack:
                    stack[-1].children.append(tree_node)
                else:
                    root = tree_node
                stack.append(tree_node)
            if self.version >= 21:
                if reference:
                    for _ in range(3):
                        _, p = _cstring(d, p)
                else:
                    ndeps = struct.unpack_from("<i", d, p)[0]
                    p += 4 + 4 * ndeps
        return {"class": class_, "tree": root}, p

    def object_data(self, o):
        return self.data[self.data_start + o.start:self.data_start + o.start + o.size]

    def strings(self, o, values=()):
        """[(field path, start, end)] of all the object's strings (positions in its data). With 'valori' (names of
        numeric fields, e.g. 'm_Unicode') also [(percorso, 'value', numero)] of those fields."""
        tree = self.kinds[o.kind]["tree"]
        if tree is None:
            raise UnityError("the file does not contain the field trees (type tree)")
        output = []
        try:
            _read(tree, self.object_data(o), 0, [], output, set(values))
        except _VariableField as e:
            # fields with a variable structure (e.g. 'references' of managed references, always at the end): the
            # strings read up to there count; those inside these fields are left untouched
            output.append((("...",) + tuple(e.path), None, None))
        return output

    def with_new_texts(self, new_items):
        """The file with the strings changed: nuovi = {path_id: {(inizio, fine): testo}}. Only those strings are
        rewritten; the objects move (aligned to 8 as Unity does) and the object table is updated."""
        head = bytearray(self.data[:self.data_start])
        body = bytearray()
        for o in sorted(self.objects, key=lambda x: x.start):
            old = self.object_data(o)
            changes = new_items.get(o.path_id)
            if changes:
                new, last = bytearray(), 0
                for (a, b), text in sorted(changes.items()):
                    new += old[last:a]
                    t = text.encode("utf-8")
                    new += struct.pack("<i", len(t)) + t
                    new += b"\x00" * (-len(new) % 4)
                    old_end = b + (-b % 4)  # the old string with its alignment
                    last = old_end
                new += old[last:]
                old = bytes(new)
            body += b"\x00" * (-len(body) % 8)
            start = len(body)
            body += old
            if self.version >= 22:
                struct.pack_into("<q", head, o.pos_start, start)
            else:
                struct.pack_into("<I", head, o.pos_start, start)
            struct.pack_into("<I", head, o.pos_size, len(old))
        total = len(head) + len(body)
        if self.version >= 22:
            struct.pack_into(">q", head, 24, total)
        else:
            struct.pack_into(">I", head, 4, total)
        return bytes(head + body)


def _read(tree_node, d, p, path, output, values=frozenset()):
    """Reads the field 'nodo' from position p; records the strings; returns the position after the field."""
    path = path + [tree_node.name]
    if tree_node.kind == "string":
        n = struct.unpack_from("<i", d, p)[0]
        output.append((tuple(path), p, p + 4 + n))
        p += 4 + n
        if tree_node.align or (tree_node.children and tree_node.children[0].align):
            p += -p % 4
        return p
    if not tree_node.children:
        if tree_node.kind in _SIMPLE_ESCAPES:
            if tree_node.name in values:
                output.append((tuple(path), "value",
                               struct.unpack_from("<" + _SIMPLE_ESCAPES[tree_node.kind], d, p)[0]))
            p += struct.calcsize("<" + _SIMPLE_ESCAPES[tree_node.kind])
        elif tree_node.size > 0:
            p += tree_node.size
        else:
            raise _VariableField(path)
    elif tree_node.children[0].kind == "Array" or tree_node.kind == "Array":
        vector = tree_node if tree_node.kind == "Array" else tree_node.children[0]
        n = struct.unpack_from("<i", d, p)[0]
        p += 4
        element = vector.children[1]
        if not element.children and element.kind in _SIMPLE_ESCAPES:
            p += n * struct.calcsize("<" + _SIMPLE_ESCAPES[element.kind])
        else:
            for k in range(n):
                p = _read(element, d, p, path + [k], output, values)
        if vector.align and vector is not tree_node:
            p += -p % 4
    else:
        for child in tree_node.children:
            p = _read(child, d, p, path, output, values)
    if tree_node.align:
        p += -p % 4
    return p
