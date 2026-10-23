"""Reading of .NET assemblies (exe/dll) and of .resources files, with the standard library only.

Used for XNA/FNA/MonoGame games and for .NET games in general:
- PE + CLI header + metadata (ECMA-335 II.24): Assembly, ManifestResource, CustomAttribute... tables;
- embedded resources (e.g. "RogueCastle.Resources.LocStrings.resources" inside RogueLegacy.exe);
- .resources format (System.Resources.ResourceWriter) for reading and writing, strings only;
- #US heap (strings written in the code), for analysis.
None of this runs the game's code: only bytes are read.
"""
import struct


class DotnetError(ValueError):
    pass


# --- PE + CLI ----------------------------------------------------------------------------------

# metadata tables (ECMA-335 II.22): name, columns. Column types:
#   number = fixed bytes; "s" strings heap; "g" guid heap; "b" blob heap;
#   ("t", n) simple index into table n; ("c", name) coded index
TABLES = {
    0x00: ("Module", [2, "s", "g", "g", "g"]),
    0x01: ("TypeRef", [("c", "ResolutionScope"), "s", "s"]),
    0x02: ("TypeDef", [4, "s", "s", ("c", "TypeDefOrRef"), ("t", 0x04), ("t", 0x06)]),
    0x03: ("FieldPtr", [("t", 0x04)]),
    0x04: ("Field", [2, "s", "b"]),
    0x05: ("MethodPtr", [("t", 0x06)]),
    0x06: ("MethodDef", [4, 2, 2, "s", "b", ("t", 0x08)]),
    0x07: ("ParamPtr", [("t", 0x08)]),
    0x08: ("Param", [2, 2, "s"]),
    0x09: ("InterfaceImpl", [("t", 0x02), ("c", "TypeDefOrRef")]),
    0x0A: ("MemberRef", [("c", "MemberRefParent"), "s", "b"]),
    0x0B: ("Constant", [2, ("c", "HasConstant"), "b"]),
    0x0C: ("CustomAttribute", [("c", "HasCustomAttribute"), ("c", "CustomAttributeType"), "b"]),
    0x0D: ("FieldMarshal", [("c", "HasFieldMarshal"), "b"]),
    0x0E: ("DeclSecurity", [2, ("c", "HasDeclSecurity"), "b"]),
    0x0F: ("ClassLayout", [2, 4, ("t", 0x02)]),
    0x10: ("FieldLayout", [4, ("t", 0x04)]),
    0x11: ("StandAloneSig", ["b"]),
    0x12: ("EventMap", [("t", 0x02), ("t", 0x14)]),
    0x13: ("EventPtr", [("t", 0x14)]),
    0x14: ("Event", [2, "s", ("c", "TypeDefOrRef")]),
    0x15: ("PropertyMap", [("t", 0x02), ("t", 0x17)]),
    0x16: ("PropertyPtr", [("t", 0x17)]),
    0x17: ("Property", [2, "s", "b"]),
    0x18: ("MethodSemantics", [2, ("t", 0x06), ("c", "HasSemantics")]),
    0x19: ("MethodImpl", [("t", 0x02), ("c", "MethodDefOrRef"), ("c", "MethodDefOrRef")]),
    0x1A: ("ModuleRef", ["s"]),
    0x1B: ("TypeSpec", ["b"]),
    0x1C: ("ImplMap", [2, ("c", "MemberForwarded"), "s", ("t", 0x1A)]),
    0x1D: ("FieldRVA", [4, ("t", 0x04)]),
    0x1E: ("EncLog", [4, 4]),
    0x1F: ("EncMap", [4]),
    0x20: ("Assembly", [4, 2, 2, 2, 2, 4, "b", "s", "s"]),
    0x21: ("AssemblyProcessor", [4]),
    0x22: ("AssemblyOS", [4, 4, 4]),
    0x23: ("AssemblyRef", [2, 2, 2, 2, 4, "b", "s", "s", "b"]),
    0x24: ("AssemblyRefProcessor", [4, ("t", 0x23)]),
    0x25: ("AssemblyRefOS", [4, 4, 4, ("t", 0x23)]),
    0x26: ("File", [4, "s", "b"]),
    0x27: ("ExportedType", [4, 4, "s", "s", ("c", "Implementation")]),
    0x28: ("ManifestResource", [4, 4, "s", ("c", "Implementation")]),
    0x29: ("NestedClass", [("t", 0x02), ("t", 0x02)]),
    0x2A: ("GenericParam", [2, 2, ("c", "TypeOrMethodDef"), "s"]),
    0x2B: ("MethodSpec", [("c", "MethodDefOrRef"), "b"]),
    0x2C: ("GenericParamConstraint", [("t", 0x2A), ("c", "TypeDefOrRef")]),
}

# coded indexes: tag bits, tables (None = unused value)
CODED_INDEXES = {
    "TypeDefOrRef": (2, [0x02, 0x01, 0x1B]),
    "HasConstant": (2, [0x04, 0x08, 0x17]),
    "HasCustomAttribute": (5, [0x06, 0x04, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x00, 0x0E, 0x17, 0x14, 0x11, 0x1A,
                               0x1B, 0x20, 0x23, 0x26, 0x27, 0x28, 0x2A, 0x2C, 0x2B]),
    "HasFieldMarshal": (1, [0x04, 0x08]),
    "HasDeclSecurity": (2, [0x02, 0x06, 0x20]),
    "MemberRefParent": (3, [0x02, 0x01, 0x1A, 0x06, 0x1B]),
    "HasSemantics": (1, [0x14, 0x17]),
    "MethodDefOrRef": (1, [0x06, 0x0A]),
    "MemberForwarded": (1, [0x04, 0x06]),
    "Implementation": (2, [0x26, 0x23, 0x27]),
    "CustomAttributeType": (3, [None, None, 0x06, 0x0A, None]),
    "ResolutionScope": (2, [0x00, 0x1A, 0x23, 0x01]),
    "TypeOrMethodDef": (1, [0x02, 0x06]),
}


def _cstr(data, pos):
    end = data.index(b"\x00", pos)
    return data[pos:end].decode("utf-8", "replace")


def read_compressed(data, pos):
    """Compressed integer of the ECMA-335 blobs (1, 2 or 4 bytes). Returns (value, new position)."""
    b = data[pos]
    if b & 0x80 == 0:
        return b, pos + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | data[pos + 1], pos + 2
    if b & 0xE0 == 0xC0:
        return ((b & 0x1F) << 24) | (data[pos + 1] << 16) | (data[pos + 2] << 8) | data[pos + 3], pos + 4
    raise DotnetError("invalid compressed integer")


class Assembly:
    """.NET assembly read from a file (read only)."""

    def __init__(self, path=None, data=None):
        self.path = path
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.data = data
        self._pe()
        self._metadata()

    # PE ------------------------------------------------------------------------------------------
    def _pe(self):
        d = self.data
        if d[:2] != b"MZ":
            raise DotnetError("it's not a Windows executable")
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] != b"PE\x00\x00":
            raise DotnetError("missing PE header")
        self.machine, nsect, _, _, _, opt_size, self.characteristics = struct.unpack_from("<HHIIIHH", d, pe + 4)
        opt = pe + 24
        magic = struct.unpack_from("<H", d, opt)[0]
        if magic == 0x10B:
            data_dirs = opt + 96
        elif magic == 0x20B:
            data_dirs = opt + 112
        else:
            raise DotnetError("unknown PE optional header")
        self.pe32_plus = magic == 0x20B
        n_dir = struct.unpack_from("<I", d, data_dirs - 4)[0]
        if n_dir <= 14:
            raise DotnetError("this is not a .NET assembly (it's missing the CLI header)")
        self.sections = []
        tab = opt + opt_size
        for k in range(nsect):
            name = d[tab + 40 * k:tab + 40 * k + 8].rstrip(b"\x00").decode("ascii", "replace")
            vdim, vrva, rdim, rpos = struct.unpack_from("<IIII", d, tab + 40 * k + 8)
            self.sections.append((name, vrva, vdim, rpos, rdim))
        cli_rva, cli_size = struct.unpack_from("<II", d, data_dirs + 14 * 8)
        if not cli_rva:
            raise DotnetError("this is not a .NET assembly (empty CLI header)")
        c = self.offset(cli_rva)
        (_, self.cli_major, self.cli_minor, self.meta_rva, self.meta_size, self.cli_flags, self.entry_token,
         self.resources_rva, self.resources_size, self.signature_rva,
         self.signature_size) = struct.unpack_from("<IHHIIIIIIII", d, c)

    def offset(self, rva):
        for _, vrva, vdim, rpos, rdim in self.sections:
            if vrva <= rva < vrva + max(vdim, rdim):
                return rva - vrva + rpos
        raise DotnetError("RVA 0x%x outside of sections" % rva)

    @property
    def only_32bit(self):
        """True if the assembly requires a 32-bit process (COMIMAGE_FLAGS_32BITREQUIRED without 32BITPREFERRED)."""
        return bool(self.cli_flags & 0x2) and not (self.cli_flags & 0x20000)

    @property
    def signed(self):
        """True if it has a strong name signature."""
        return bool(self.cli_flags & 0x8) and self.signature_size > 0

    # metadata ------------------------------------------------------------------------------------
    def _metadata(self):
        d = self.data
        base = self.offset(self.meta_rva)
        if struct.unpack_from("<I", d, base)[0] != 0x424A5342:
            raise DotnetError("invalid metadata signature")
        length = struct.unpack_from("<I", d, base + 12)[0]
        self.runtime_version = d[base + 16:base + 16 + length].split(b"\x00")[0].decode("ascii", "replace")
        pos = base + 16 + length + 2
        n_streams = struct.unpack_from("<H", d, pos)[0]
        pos += 2
        self.streams = {}
        for _ in range(n_streams):
            off, dim = struct.unpack_from("<II", d, pos)
            name = _cstr(d, pos + 8)
            pos += 8 + ((len(name) + 4) & ~3)
            self.streams[name] = (base + off, dim)
        tables = self.streams.get("#~") or self.streams.get("#-")
        if not tables:
            raise DotnetError("metadata tables stream missing")
        self._tables(*tables)

    def _heap(self, name):
        if name not in self.streams:
            return b""
        pos, dim = self.streams[name]
        return self.data[pos:pos + dim]

    def _tables(self, pos, _size):
        d = self.data
        heap_size = d[pos + 6]
        valid_mask = struct.unpack_from("<Q", d, pos + 8)[0]
        p = pos + 24
        self.lines = {}
        for t in range(64):
            if valid_mask >> t & 1:
                self.lines[t] = struct.unpack_from("<I", d, p)[0]
                p += 4
        if heap_size & 0x40:  # extra data (rare)
            p += 4
        self._str_size = 4 if heap_size & 1 else 2
        self._guid_size = 4 if heap_size & 2 else 2
        self._blob_size = 4 if heap_size & 4 else 2
        unknowns = [t for t in self.lines if t not in TABLES]
        if unknowns:
            raise DotnetError("unknown metadata tables: %s" % unknowns)
        self._columns, self._start, self._row_size = {}, {}, {}
        for t in sorted(self.lines):
            widths = [self._width(c) for c in TABLES[t][1]]
            self._columns[t] = widths
            self._row_size[t] = sum(widths)
            self._start[t] = p
            p += self._row_size[t] * self.lines[t]
        self._str = self._heap("#Strings")
        self._blob = self._heap("#Blob")
        self._us = self._heap("#US")

    def _width(self, column):
        if isinstance(column, int):
            return column
        if column == "s":
            return self._str_size
        if column == "g":
            return self._guid_size
        if column == "b":
            return self._blob_size
        kind, val = column
        if kind == "t":
            return 2 if self.lines.get(val, 0) < 0x10000 else 4
        bit, tab = CODED_INDEXES[val]
        maximum = max(self.lines.get(t, 0) for t in tab if t is not None)
        return 2 if maximum < (1 << (16 - bit)) else 4

    def line(self, table, idx):
        """Raw values of row 'indice' (from 1) of the table."""
        if not 1 <= idx <= self.lines.get(table, 0):
            raise DotnetError("row %d outside of table %s" % (idx, TABLES[table][0]))
        p = self._start[table] + (idx - 1) * self._row_size[table]
        values = []
        for width in self._columns[table]:
            values.append(int.from_bytes(self.data[p:p + width], "little"))
            p += width
        return values

    def rows_of(self, table):
        return [self.line(table, i) for i in range(1, self.lines.get(table, 0) + 1)]

    def read_string(self, idx):
        return _cstr(self._str, idx) if idx else ""

    def blob(self, idx):
        if not idx:
            return b""
        n, p = read_compressed(self._blob, idx)
        return self._blob[p:p + n]

    @staticmethod
    def decode(encoded, kind):
        """Coded index -> (table, row)."""
        bit, tab = CODED_INDEXES[kind]
        return tab[encoded & ((1 << bit) - 1)], encoded >> bit

    # high-level information ----------------------------------------------------------------------
    def identity(self):
        """Name, version, culture and public key of the assembly."""
        if not self.lines.get(0x20):
            raise DotnetError("the Assembly table is missing (it's a module, not an assembly)")
        _, ma, mi, bu, re, flags, key, name, culture = self.line(0x20, 1)
        return {"name": self.read_string(name), "version": "%d.%d.%d.%d" % (ma, mi, bu, re),
                "culture": self.read_string(culture), "public_key": self.blob(key).hex(), "flags": flags}

    def references(self):
        """Referenced assemblies: [(name, version)]."""
        return [(self.read_string(r[6]), "%d.%d.%d.%d" % tuple(r[:4])) for r in self.rows_of(0x23)]

    def resources(self):
        """Embedded resources: {name: (offset of the data in the file, length)}."""
        output = {}
        for offset, _flags, name, impl in self.rows_of(0x28):
            if impl:  # resource in another file/assembly
                continue
            if not self.resources_rva:
                raise DotnetError("declared resources, but resource directory is missing")
            p = self.offset(self.resources_rva) + offset
            n = struct.unpack_from("<I", self.data, p)[0]
            if offset + 4 + n > self.resources_size:
                raise DotnetError("resource %s beyond the resource directory" % self.read_string(name))
            output[self.read_string(name)] = (p + 4, n)
        return output

    def read_resource(self, name):
        p, n = self.resources()[name]
        return self.data[p:p + n]

    def _type_name(self, table, line):
        if table == 0x01:
            _, name, space = self.line(0x01, line)
        elif table == 0x02:
            _, name, space, _, _, _ = self.line(0x02, line)
        else:
            return ""
        return (self.read_string(space) + "." if space else "") + self.read_string(name)

    def assembly_attributes(self):
        """Attributes of the assembly: [(full name of the type, blob of the value)]."""
        output = []
        for parent, kind, value_ in self.rows_of(0x0C):
            tab, r = self.decode(parent, "HasCustomAttribute")
            if tab != 0x20:
                continue
            tab_c, r_c = self.decode(kind, "CustomAttributeType")
            name = ""
            if tab_c == 0x0A:
                class_, _, _ = self.line(0x0A, r_c)
                name = self._type_name(*self.decode(class_, "MemberRefParent"))
            elif tab_c == 0x06:  # constructor defined in the same assembly: look for the owning type
                name = self._declaring_type(r_c)
            output.append((name, self.blob(value_)))
        return output

    def _declaring_type(self, method):
        kinds = self.rows_of(0x02)
        for i, t in enumerate(kinds):
            start = t[5]
            end = kinds[i + 1][5] if i + 1 < len(kinds) else self.lines.get(0x06, 0) + 1
            if start <= method < end:
                return self._type_name(0x02, i + 1)
        return ""

    def neutral_language(self):
        """Value of [assembly: NeutralResourcesLanguage("..")] and fallback location (0 = MainAssembly)."""
        for name, value_ in self.assembly_attributes():
            if name == "System.Resources.NeutralResourcesLanguageAttribute" and value_[:2] == b"\x01\x00":
                if value_[2] == 0xFF:
                    return None, 0
                n, p = read_compressed(value_, 2)
                culture = value_[p:p + n].decode("utf-8")
                p += n
                position = struct.unpack_from("<i", value_, p)[0] if len(value_) >= p + 4 + 2 else 0
                return culture, position
        return None, None

    def satellite_version(self):
        """Version the satellites must have: [SatelliteContractVersion] if present, otherwise its own."""
        for name, value_ in self.assembly_attributes():
            if (name == "System.Resources.SatelliteContractVersionAttribute" and value_[:2] == b"\x01\x00"
                    and len(value_) > 2 and value_[2] != 0xFF):
                n, p = read_compressed(value_, 2)
                parts = value_[p:p + n].decode("utf-8", "replace").strip().split(".")
                # like csc for AssemblyVersion: "1.0" becomes "1.0.0.0"
                if 2 <= len(parts) <= 4 and all(x.isdigit() and int(x) < 65535 for x in parts):
                    return ".".join(str(int(x)) for x in parts + ["0"] * (4 - len(parts)))
        return self.identity()["version"]

    def code_strings(self):
        """Strings of the #US heap (literals written in the code), in order of appearance."""
        us, output, p = self._us, [], 1
        while p < len(us):
            n, q = read_compressed(us, p)
            if n == 0:
                p = q
                continue
            output.append(us[q:q + n - 1].decode("utf-16-le", "replace"))
            p = q + n
        return output


# --- .resources files (System.Resources.ResourceWriter) ----------------------------------------

MAGIC_RESOURCES = 0xBEEFCACE
DEFAULT_READER = ("System.Resources.ResourceReader, mscorlib, Version=4.0.0.0, Culture=neutral, "
                       "PublicKeyToken=b77a5c561934e089")
DEFAULT_RESOURCE_SET = "System.Resources.RuntimeResourceSet"
KIND_STRING = 1
KIND_NULL = 0


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
            raise DotnetError("invalid 7-bit integer")


def _write_7bit(n):
    output = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            output.append(b | 0x80)
        else:
            output.append(b)
            return bytes(output)


def _string_bw(data, pos, encoding="utf-8"):
    """String as BinaryWriter writes it: length in bytes (7 bit) + text."""
    n, pos = _read_7bit(data, pos)
    return data[pos:pos + n].decode(encoding, "surrogatepass"), pos + n


def _write_string_bw(text, encoding="utf-8"):
    b = text.encode(encoding, "surrogatepass")
    return _write_7bit(len(b)) + b


def hash_name(name):
    """FastResourceComparer.HashFunction of .NET (djb2 with xor, on the UTF-16 characters)."""
    h = 5381
    for units in struct.unpack("<%dH" % (len(name.encode("utf-16-le", "surrogatepass")) // 2),
                               name.encode("utf-16-le", "surrogatepass")):
        h = (((h << 5) + h) & 0xFFFFFFFF) ^ units
    return struct.unpack("<i", struct.pack("<I", h))[0]


def read_resources(data):
    """.resources file -> {"reader", "item_set", "version", "kinds", "entries": [(name, type_code, value)]}.

    The entries are in the order of the data section. Non-string values stay raw bytes (untouched)."""
    try:
        return _read_resources(data)
    except (struct.error, IndexError, UnicodeDecodeError) as e:
        raise DotnetError(".resources file is invalid (%s)" % e) from e


def _read_resources(data):
    if len(data) < 12 or struct.unpack_from("<I", data, 0)[0] != MAGIC_RESOURCES:
        raise DotnetError("this is not a .resources file")
    header_version, skip_bytes = struct.unpack_from("<ii", data, 4)
    if header_version != 1:
        raise DotnetError(".resources header version %d not supported" % header_version)
    reader, p = _string_bw(data, 12)
    item_set, p = _string_bw(data, p)
    if p != 12 + skip_bytes:
        raise DotnetError(".resources header is inconsistent")
    version, n, n_types = struct.unpack_from("<iii", data, p)
    p += 12
    if version not in (1, 2):
        raise DotnetError(".resources version %d is not supported" % version)
    kinds = []
    for _ in range(n_types):
        t, p = _string_bw(data, p)
        kinds.append(t)
    pad_start = p
    while p % 8:
        p += 1
    if data[pad_start:p] != b"PADPADP"[:p - pad_start]:
        raise DotnetError("unexpected .resources padding")
    hash_ = struct.unpack_from("<%di" % n, data, p)
    p += 4 * n
    positions = struct.unpack_from("<%di" % n, data, p)
    p += 4 * n
    data_start = struct.unpack_from("<i", data, p)[0]
    names_start = p + 4
    entries = []
    for h, pos_name in zip(hash_, positions):
        name, q = _string_bw(data, names_start + pos_name, "utf-16-le")
        if hash_name(name) != h:
            raise DotnetError("hash for name '%s' does not match" % name)
        off_data = struct.unpack_from("<i", data, q)[0]
        entries.append((off_data, name))
    hash_order = [name for _, name in entries]  # order of the hash list (matters for names with the same hash)
    entries.sort()
    result = []
    for k, (off_data, name) in enumerate(entries):
        q = data_start + off_data
        end = data_start + entries[k + 1][0] if k + 1 < len(entries) else len(data)
        if version == 2:
            code, q2 = _read_7bit(data, q)
        else:
            v1_code, q2 = _read_7bit(data, q)  # v1: index into the types table (-1 = null)
            code = KIND_STRING if 0 <= v1_code < len(kinds) and kinds[v1_code].startswith("System.String") \
                else ("v1", v1_code)
        if code == KIND_STRING:
            value_, q3 = _string_bw(data, q2)
            if q3 != end:
                raise DotnetError("the resource string '%s' is inconsistent" % name)
        else:
            value_ = data[q2:end]
        result.append((name, code, value_))
    return {"reader": reader, "item_set": item_set, "version": version, "kinds": kinds,
            "entries": result, "hash_order": hash_order}


def write_resources(info, new_lines=None):
    """Rewrites a version 2 .resources like ResourceWriter. nuove: {name: text} replaces the strings.

    Non-string entries are copied identically. Order and headers as in the file read."""
    if info["version"] != 2:
        raise DotnetError("writing supported only for .resources version 2")
    new_lines = new_lines or {}
    head = _write_string_bw(info["reader"]) + _write_string_bw(info["item_set"])
    output = bytearray(struct.pack("<Iii", MAGIC_RESOURCES, 1, len(head)) + head)
    entries = info["entries"]
    output += struct.pack("<iii", 2, len(entries), len(info["kinds"]))
    for t in info["kinds"]:
        output += _write_string_bw(t)
    k = 0
    while len(output) % 8:
        output += b"PAD"[k % 3:k % 3 + 1]
        k += 1
    names, section_data, items = bytearray(), bytearray(), []
    rank = {name: k for k, name in enumerate(info.get("hash_order", []))}
    for k, (name, code, value_) in enumerate(entries):
        items.append((hash_name(name), rank.get(name, len(rank) + k), len(names)))
        names += _write_string_bw(name, "utf-16-le") + struct.pack("<i", len(section_data))
        if code == KIND_STRING:
            text = new_lines.get(name, value_)
            section_data += _write_7bit(KIND_STRING) + _write_string_bw(text)
        else:
            section_data += _write_7bit(code) + value_
    # hashes in ascending order; for equal hashes the order of the file read is kept (any order is valid:
    # ResourceReader checks all the names with the same hash)
    items.sort(key=lambda v: (v[0], v[1]))
    output += struct.pack("<%di" % len(items), *[h for h, _, _ in items])
    output += struct.pack("<%di" % len(items), *[p for _, _, p in items])
    output += struct.pack("<i", len(output) + 4 + len(names))
    return bytes(output + names + section_data)


def resources_strings(info):
    """{name: text} of the string resources only."""
    return {name: value_ for name, code, value_ in info["entries"] if code == KIND_STRING}
