"""Unity Localization adapter: translates the text tables (StringTable) inside the game's AssetBundles.

Many recent Unity games use the Localization package: the texts of each language are in an Addressables bundle,
e.g. StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle.
PatchSmith translates the texts of the English table (the m_Localized field of each entry) and rewrites the bundle,
with a verified backup as for the other files. It also works with IL2CPP games, without mods: in the game English
stays selected. The rewritten bundle is not compressed (Unity reads it all the same).

A bundle's language is recognized from its name when the code is in parentheses ("english(en)"), otherwise from its
contents (the m_LocaleId code of its tables: "localization-string-tables-english_assets_all",
"localization_en_assets_all", "chinese(simplified)") and, if they cannot be read, from the language name.

Fingerprint (CRC): if the Addressables catalog (catalog.json) checks the bundle's fingerprint, a rewritten bundle
would not be loaded. So in the catalog the CRC of that bundle only is set to 0 (same length, the rest of the file
identical), with backup and restore as for the other files (see Project._remove_crc). The same with the binary
catalog (catalog.bin, Addressables 2): see _record_bin; if the bundle's record cannot be found with certainty,
nothing is changed.
"""
import base64
import json
import os
import re
import struct

from . import unityfs

_TABLE = re.compile(r"^localization-string-tables-.*\(([A-Za-z]{2,3}(?:-[A-Za-z]+)?)\)_.*\.bundle$", re.I)
# fallback name: "localization-string-tables-<language>_assets_..." or "localization_<language>_assets_..."
_LANGUAGE_NAME = re.compile(r"^locali[sz]ation[-_](?:string[-_]tables?[-_])?(.+?)_assets_.*\.bundle$", re.I)
_CODE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.I)
_ASSET_TABLES = re.compile(r"asset[-_]?tables?", re.I)  # asset tables (images, sounds): no texts
_GUID = re.compile(r"^[0-9a-f]{32}(?:\[.*\])?$")  # entries of the asset tables: references, not texts
# language names in the bundle names (in English and in the language itself) -> code
_LANGUAGE_NAMES = {
    "english": "en", "italian": "it", "italiano": "it", "german": "de", "deutsch": "de", "french": "fr",
    "français": "fr", "francais": "fr", "spanish": "es", "español": "es", "espanol": "es", "portuguese": "pt",
    "portugese": "pt", "português": "pt", "portugues": "pt", "português(brasil)": "pt-br", "portuguese(brazil)": "pt-br",
    "portugese(brazil)": "pt-br", "russian": "ru", "русский": "ru", "polish": "pl", "polski": "pl", "japanese": "ja",
    "日本語": "ja", "korean": "ko", "한국어": "ko", "chinese(simplified)": "zh-hans", "简体中文": "zh-hans",
    "chinese(traditional)": "zh-hant", "繁體中文": "zh-hant", "turkish": "tr", "türkçe": "tr", "dutch": "nl",
    "nederlands": "nl", "czech": "cs", "čeština": "cs", "hungarian": "hu", "magyar": "hu", "ukrainian": "uk",
    "українська": "uk", "swedish": "sv", "svenska": "sv", "danish": "da", "dansk": "da", "norwegian": "no",
    "norsk": "no", "finnish": "fi", "suomi": "fi", "romanian": "ro", "română": "ro", "greek": "el", "thai": "th",
    "vietnamese": "vi", "indonesian": "id", "arabic": "ar", "hebrew": "he", "bulgarian": "bg", "slovak": "sk",
}
MAX_CONTENT_BUNDLE = 64 * 1024 * 1024  # larger bundles are not opened to recognize their language
_CACHE = {}  # (path, size, date) -> language code (or None): a bundle is read only once


def _skipped_folders(name):
    return name.lower() in ("plugins", "il2cpp_data", "managed", "mono", "bepinex")


def _localization_bundle(folder):
    """[path] of the bundles with 'locali' in the name (Unity Localization), except the asset tables."""
    found = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if not _skipped_folders(d)]
        if os.path.relpath(root, folder).count(os.sep) > 6:
            dirs[:] = []
        for name in files:
            low = name.lower()
            if ("locali" in low and low.endswith(".bundle") and ".patchsmith-" not in low
                    and not _ASSET_TABLES.search(low)):
                found.append(os.path.join(root, name))
    return sorted(found)


def _table_texts(s, o):
    """(language code, number of texts) of a table object (m_LocaleId and m_TableData)."""
    d = s.object_data(o)
    code, texts = None, 0
    for path, start, end in s.strings(o):
        if start is None:
            continue
        if path[-1] == "m_Code" and "m_LocaleId" in path:
            code = d[start + 4:end].decode("utf-8", "replace").strip()
        elif path[-1] == "m_Localized" and "m_TableData" in path:
            t = d[start + 4:end].decode("utf-8", "replace")
            if any(c.isalpha() for c in t) and not _GUID.match(t):
                texts += 1
    return code, texts


def _content_language(path_or_data):
    """(readable, code): the language code (m_LocaleId) of the bundle's text tables, or None if it has no tables
    with texts (asset tables, the Locales, tables without the field trees); readable=False if the bundle cannot be
    opened (too large, damaged, not UnityFS)."""
    try:
        if isinstance(path_or_data, (bytes, bytearray)):
            data = bytes(path_or_data)
        else:
            with open(path_or_data, "rb") as f:
                data = f.read(MAX_CONTENT_BUNDLE + 1)
            if len(data) > MAX_CONTENT_BUNDLE:
                return False, None
        bundle = unityfs.Bundle(data)
    except (OSError, ValueError, IndexError):
        return False, None
    for name, _ in bundle.nodes:
        if name.endswith((".resS", ".resource")):
            continue
        try:
            s = unityfs.SerializedFile(bundle.file[name])
            for o in s.objects:
                tree = s.kinds[o.kind]["tree"]
                if tree is None or not {"m_TableData", "m_LocaleId"} <= {f.name for f in tree.children}:
                    continue
                code, texts = _table_texts(s, o)
                if code and texts and _CODE.match(code):
                    return True, code
        except (ValueError, IndexError, struct.error):
            continue
    return True, None


def bundle_language(path_or_data):
    """The language code (m_LocaleId) of the bundle's text tables, or None (see _content_language)."""
    return _content_language(path_or_data)[1]


def _language_from_name(name):
    m = _LANGUAGE_NAME.match(name)
    if not m:
        return None
    part = m.group(1).lower()
    if part in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[part]
    part = re.sub(r"\(([a-z]{2,3}(?:-[a-z0-9]+)?)\)$", "", part)  # "english(en)": already seen by _TABLE
    if part in _LANGUAGE_NAMES:
        return _LANGUAGE_NAMES[part]
    return part if _CODE.match(part) and part not in ("all", "assets", "shared", "locales") else None


def _language(path):
    name = os.path.basename(path)
    m = _TABLE.match(name)
    if m:
        return m.group(1).lower()
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if key not in _CACHE:
        readable, code = _content_language(path)
        if not readable:  # the language name in the file name counts; a bundle that can be read and has no text
            code = _language_from_name(name)  # tables (e.g. the Locales, the shared data) is not a language
        _CACHE[key] = code.lower() if code else None
    return _CACHE[key]


def table_bundles(folder):
    """{language code: [relative paths]} of all the bundles with text tables (Unity Localization), including
    several bundles for the same language (tables packed separately)."""
    matches = {}
    for path in _localization_bundle(folder):
        code = _language(path)
        if code:
            rel = os.path.relpath(path, folder).replace(os.sep, "/")
            matches.setdefault(code, []).append(rel)
    return matches


def tables_by_content(folder):
    """{language code: relative path} of the bundles with text tables: the code from the name ("(en)"),
    otherwise from the contents (m_LocaleId), otherwise from the language name (e.g. "english", "italiano", "en")."""
    return {c: p[0] for c, p in table_bundles(folder).items()}


tables = tables_by_content  # previous name


# --- Addressables catalog and bundle fingerprint (CRC) ------------------------------------------------------------

def catalog_of(bundle_path):
    """The bundle's Addressables catalog (catalog.json or catalog.bin in the aa folder), or None."""
    folder = os.path.dirname(os.path.dirname(bundle_path))
    for name in ("catalog.json", "catalog.bin"):
        p = os.path.join(folder, name)
        if os.path.isfile(p):
            return p
    return None


_EXTRA = re.compile(rb'"m_ExtraDataString"\s*:\s*"([A-Za-z0-9+/=]*)"')
_CRC = re.compile(r'("m_Crc"\s*:\s*)(\d+)')


def _opts(catalog_bytes, names):
    """(extra data, [(name, start, end, encoding)]): where, in the catalog's extra data (m_ExtraDataString), the
    options (AssetBundleRequestOptions, JSON text) of the bundles with these file names are. The catalog's
    entries (m_EntryDataString, 7 integers each) say which id (the bundle's path) uses which extra data."""
    data = json.loads(catalog_bytes.decode("utf-8-sig"))
    m = _EXTRA.search(catalog_bytes)
    extra = base64.b64decode(data["m_ExtraDataString"])
    if not m or base64.b64decode(m.group(1)) != extra:
        raise ValueError("extra data from the catalog not found")
    entries = base64.b64decode(data["m_EntryDataString"])
    ids = data["m_InternalIds"]
    wanted = {n.lower() for n in names}
    matches = []
    for k in range(struct.unpack_from("<i", entries, 0)[0]):
        inner, _, _, _, idx = struct.unpack_from("<5i", entries, 4 + 28 * k)
        if idx < 0 or not 0 <= inner < len(ids):
            continue
        name = re.split(r"[\\/#]", str(ids[inner]))[-1].lower()
        if name not in wanted or extra[idx] != 7:  # 7 = JSON object
            continue
        q = idx + 1
        q += 1 + extra[q]  # assembly name
        class_ = extra[q + 1:q + 1 + extra[q]]
        q += 1 + extra[q]
        length = struct.unpack_from("<i", extra, q)[0]
        q += 4
        text = extra[q:q + length]
        if b"AssetBundleRequestOptions" not in class_ or length <= 0 or len(text) != length:
            continue
        encoding = "utf-16-le" if text.startswith(b"{\x00") else "utf-8" if text.startswith(b"{") else None
        if encoding:
            matches.append((name, q, q + length, encoding))
    return extra, matches


def _crc(text):
    m = _CRC.search(text)
    return int(m.group(2)) if m else 0


def _binary(catalog_bytes):
    """True for catalog.bin (Addressables 2), False for catalog.json."""
    return not catalog_bytes.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{")


def _record_bin(catalog_bytes, bundle_name):
    """Positions of the bundle's CRC in the binary catalog (catalog.bin), or None if they cannot be found with
    certainty.

    In catalog.bin the options of each bundle (AssetBundleRequestOptions) are a record of 5 32-bit numbers: position
    of the Hash128 fingerprint (16 bytes), position of the bundle's name (a string with its length in the 4 bytes
    before it), CRC, size, position of the common data. The fingerprint is the one written in the name of the
    bundle's entry ("<name>_<32 hex digits>.bundle"): from there the record is found. Each fingerprint must
    lead to a single record."""
    root = re.escape(os.path.splitext(bundle_name)[0].encode("utf-8"))
    fingerprints = set(re.findall(rb"(?<![^\x00-\x20/\\])" + root + rb"_([0-9a-f]{32})\.bundle", catalog_bytes))
    if not fingerprints:
        return None
    positions = []
    for hex_str in fingerprints:
        where = [m.start() for m in re.finditer(re.escape(bytes.fromhex(hex_str.decode("ascii"))), catalog_bytes)]
        found = []
        for h in where:
            for m in re.finditer(re.escape(struct.pack("<I", h)), catalog_bytes):
                o = m.start()
                if o + 20 > len(catalog_bytes):
                    continue
                name = struct.unpack_from("<I", catalog_bytes, o + 4)[0]
                if not 4 <= name <= len(catalog_bytes) - 1:
                    continue
                length = struct.unpack_from("<I", catalog_bytes, name - 4)[0]
                if 1 <= length <= 512 and name + length <= len(catalog_bytes):
                    found.append(o + 8)
        if len(found) != 1:
            return None
        positions.append(found[0])
    return positions


def bundle_crc(bundle_path, read_from=None):
    """(state, catalog): how the Addressables catalog checks this bundle (read only).
    'absent': no catalog; 'binary': catalog.bin in which the bundle cannot be found with certainty; 'checked':
    its CRC is not 0 (it must be removed from the catalog to rewrite the bundle); 'unlocked': CRC 0; 'unknown': the
    catalog cannot be read or does not name the bundle. read_from: another file to read the catalog from (e.g. its
    backup)."""
    catalog = catalog_of(bundle_path)
    if catalog is None:
        return "absent", None
    if catalog.lower().endswith(".bin"):
        try:
            with open(read_from or catalog, "rb") as f:
                byte = f.read()
            positions = _record_bin(byte, os.path.basename(bundle_path))
        except (OSError, ValueError, struct.error):
            positions = None
        if not positions:
            return "binary", catalog
        crc = [struct.unpack_from("<I", byte, p)[0] for p in positions]
        return ("checked" if any(crc) else "unlocked"), catalog
    try:
        with open(read_from or catalog, "rb") as f:
            byte = f.read()
        extra, matches = _opts(byte, [os.path.basename(bundle_path)])
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
        return "unknown", catalog
    if not matches:
        return "unknown", catalog
    crc = [_crc(extra[a:b].decode(cod)) for _, a, b, cod in matches]
    return ("checked" if any(crc) else "unlocked"), catalog


def remove_crc(catalog_bytes, bundle_names):
    """The catalog (catalog.json) with CRC 0 for the bundles with these file names, or None if one is missing or the
    catalog cannot be read. The number becomes "0" followed by spaces (the JSON stays valid and the same length: the
    positions of the other extra data do not change); in the file only the base64 text of the extra data changes.
    With catalog.bin the 4 CRC bytes of those bundles' records are set to 0 (see _record_bin), nothing else."""
    if _binary(catalog_bytes):
        new = bytearray(catalog_bytes)
        for name in bundle_names:
            try:
                positions = _record_bin(catalog_bytes, name)
            except (ValueError, struct.error):
                positions = None
            if not positions:
                return None
            for p in positions:
                new[p:p + 4] = b"\x00\x00\x00\x00"
        return bytes(new)
    try:
        extra, matches = _opts(catalog_bytes, bundle_names)
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return None
    if {n.lower() for n in bundle_names} - {t[0] for t in matches}:
        return None
    new = bytearray(extra)
    for _, a, b, cod in matches:
        text = extra[a:b].decode(cod)
        m = _CRC.search(text)
        if m and int(m.group(2)):
            text = text[:m.start(2)] + "0" + " " * (len(m.group(2)) - 1) + text[m.end(2):]
            byte = text.encode(cod)
            if len(byte) != b - a:
                return None
            new[a:b] = byte
    m = _EXTRA.search(catalog_bytes)
    encoded = base64.b64encode(bytes(new))
    if len(encoded) != len(m.group(1)):
        return None
    result = catalog_bytes[:m.start(1)] + encoded + catalog_bytes[m.end(1):]
    # check: it is read again, the bundles have CRC 0 and the rest of the extra data is identical
    try:
        extra2, matches2 = _opts(result, bundle_names)
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return None
    if len(extra2) != len(extra) or any(_crc(extra2[a:b].decode(cod)) for _, a, b, cod in matches2):
        return None
    return result


def checked_crc(bundle_path):
    """True if the Addressables catalog next to it checks the fingerprint (CRC) of some bundle (fallback when the
    catalog says nothing about the bundle). None if the catalog cannot be found or read."""
    folder = os.path.dirname(os.path.dirname(bundle_path))
    catalog = os.path.join(folder, "catalog.json")
    try:
        with open(catalog, encoding="utf-8") as f:
            data = json.load(f)
        extra = base64.b64decode(data.get("m_ExtraDataString", ""))
    except (OSError, ValueError):
        return None
    # the UTF-16 JSON texts can start at an even or odd position
    text = extra.decode("utf-16-le", "ignore") + extra[1:].decode("utf-16-le", "ignore") + extra.decode("latin-1")
    return any(v != "0" for v in re.findall(r"m_Crc\W{0,4}(\d+)", text))


# --- text tables --------------------------------------------------------------------------------------------------

class TablesFile:
    """A bundle's text tables: texts() to translate and serialize(translations) to rewrite it. The asset tables
    (entries that are references, not texts) stay as they are."""

    def __init__(self, path_or_data):
        if isinstance(path_or_data, (bytes, bytearray)):
            data = bytes(path_or_data)
        else:
            with open(path_or_data, "rb") as f:
                data = f.read()
        self.bundle = unityfs.Bundle(data)
        self.serialized = {}
        self._units = []
        for name, _ in self.bundle.nodes:
            if name.endswith((".resS", ".resource")):
                continue
            try:
                s = unityfs.SerializedFile(self.bundle.file[name])
            except unityfs.UnityError:
                continue
            self.serialized[name] = s
            for o in s.objects:
                tree = s.kinds[o.kind]["tree"]
                if tree is None or not any(f.name == "m_TableData" for f in tree.children):
                    continue
                d = s.object_data(o)
                units, references = [], 0
                for path, start, end in s.strings(o):
                    if start is not None and path[-1] == "m_Localized" and "m_TableData" in path:
                        text = d[start + 4:end].decode("utf-8", "replace")
                        if _GUID.match(text):
                            references += 1
                        elif any(c.isalpha() for c in text):
                            units.append(((name, o.path_id, start, end), text))
                if references and not units:  # asset table
                    continue
                self._units += units

    def texts(self):
        """[(position, text)] of the tables' texts."""
        return list(self._units)

    def serialize(self, translations):
        """The bundle with the translated texts ({position: text}), not compressed."""
        by_file = {}
        for (name, path_id, start, end), text in translations.items():
            by_file.setdefault(name, {}).setdefault(path_id, {})[(start, end)] = text
        for name, new_items in by_file.items():
            self.bundle.file[name] = self.serialized[name].with_new_texts(new_items)
        return self.bundle.serialize()
