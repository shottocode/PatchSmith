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
identical), with backup and restore as for the other files (see Progetto._togli_crc). The same with the binary
catalog (catalog.bin, Addressables 2): see _record_bin; if the bundle's record cannot be found with certainty,
nothing is changed.
"""
import base64
import json
import os
import re
import struct

from . import unityfs

_TABELLA = re.compile(r"^localization-string-tables-.*\(([A-Za-z]{2,3}(?:-[A-Za-z]+)?)\)_.*\.bundle$", re.I)
# fallback name: "localization-string-tables-<lingua>_assets_..." or "localization_<lingua>_assets_..."
_NOME_LINGUA = re.compile(r"^locali[sz]ation[-_](?:string[-_]tables?[-_])?(.+?)_assets_.*\.bundle$", re.I)
_CODICE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.I)
_TABELLE_ASSET = re.compile(r"asset[-_]?tables?", re.I)  # asset tables (images, sounds): no texts
_GUID = re.compile(r"^[0-9a-f]{32}(?:\[.*\])?$")  # entries of the asset tables: references, not texts
# language names in the bundle names (in English and in the language itself) -> code
_NOMI_LINGUE = {
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
MAX_BUNDLE_CONTENUTO = 64 * 1024 * 1024  # larger bundles are not opened to recognize their language
_CACHE = {}  # (percorso, dimensione, data) -> language code (or None): a bundle is read only once


def _cartelle_saltate(nome):
    return nome.lower() in ("plugins", "il2cpp_data", "managed", "mono", "bepinex")


def _bundle_localizzazione(cartella):
    """[percorso] of the bundles with 'locali' in the name (Unity Localization), except the asset tables."""
    trovati = []
    for radice, dirs, files in os.walk(cartella):
        dirs[:] = [d for d in dirs if not _cartelle_saltate(d)]
        if os.path.relpath(radice, cartella).count(os.sep) > 6:
            dirs[:] = []
        for nome in files:
            basso = nome.lower()
            if ("locali" in basso and basso.endswith(".bundle") and ".patchsmith-" not in basso
                    and not _TABELLE_ASSET.search(basso)):
                trovati.append(os.path.join(radice, nome))
    return sorted(trovati)


def _testi_tabella(s, o):
    """(language code, number of texts) of a table object (m_LocaleId and m_TableData)."""
    d = s.dati_oggetto(o)
    codice, testi = None, 0
    for percorso, inizio, fine in s.stringhe(o):
        if inizio is None:
            continue
        if percorso[-1] == "m_Code" and "m_LocaleId" in percorso:
            codice = d[inizio + 4:fine].decode("utf-8", "replace").strip()
        elif percorso[-1] == "m_Localized" and "m_TableData" in percorso:
            t = d[inizio + 4:fine].decode("utf-8", "replace")
            if any(c.isalpha() for c in t) and not _GUID.match(t):
                testi += 1
    return codice, testi


def _lingua_contenuto(percorso_o_dati):
    """(leggibile, codice): the language code (m_LocaleId) of the bundle's text tables, or None if it has no tables
    with texts (asset tables, the Locales, tables without the field trees); leggibile=False if the bundle cannot be
    opened (too large, damaged, not UnityFS)."""
    try:
        if isinstance(percorso_o_dati, (bytes, bytearray)):
            dati = bytes(percorso_o_dati)
        else:
            with open(percorso_o_dati, "rb") as f:
                dati = f.read(MAX_BUNDLE_CONTENUTO + 1)
            if len(dati) > MAX_BUNDLE_CONTENUTO:
                return False, None
        bundle = unityfs.Bundle(dati)
    except (OSError, ValueError, IndexError):
        return False, None
    for nome, _ in bundle.nodi:
        if nome.endswith((".resS", ".resource")):
            continue
        try:
            s = unityfs.FileSerializzato(bundle.file[nome])
            for o in s.oggetti:
                albero = s.tipi[o.tipo]["albero"]
                if albero is None or not {"m_TableData", "m_LocaleId"} <= {f.nome for f in albero.figli}:
                    continue
                codice, testi = _testi_tabella(s, o)
                if codice and testi and _CODICE.match(codice):
                    return True, codice
        except (ValueError, IndexError, struct.error):
            continue
    return True, None


def lingua_del_bundle(percorso_o_dati):
    """The language code (m_LocaleId) of the bundle's text tables, or None (see _lingua_contenuto)."""
    return _lingua_contenuto(percorso_o_dati)[1]


def _lingua_dal_nome(nome):
    m = _NOME_LINGUA.match(nome)
    if not m:
        return None
    parte = m.group(1).lower()
    if parte in _NOMI_LINGUE:
        return _NOMI_LINGUE[parte]
    parte = re.sub(r"\(([a-z]{2,3}(?:-[a-z0-9]+)?)\)$", "", parte)  # "english(en)": already seen by _TABELLA
    if parte in _NOMI_LINGUE:
        return _NOMI_LINGUE[parte]
    return parte if _CODICE.match(parte) and parte not in ("all", "assets", "shared", "locales") else None


def _lingua(percorso):
    nome = os.path.basename(percorso)
    m = _TABELLA.match(nome)
    if m:
        return m.group(1).lower()
    try:
        st = os.stat(percorso)
        chiave = (os.path.normcase(os.path.abspath(percorso)), st.st_size, st.st_mtime_ns)
    except OSError:
        return None
    if chiave not in _CACHE:
        leggibile, codice = _lingua_contenuto(percorso)
        if not leggibile:  # the language name in the file name counts; a bundle that can be read and has no text
            codice = _lingua_dal_nome(nome)  # tables (e.g. the Locales, the shared data) is not a language
        _CACHE[chiave] = codice.lower() if codice else None
    return _CACHE[chiave]


def bundle_tabelle(cartella):
    """{codice lingua: [percorsi relativi]} of all the bundles with text tables (Unity Localization), including
    several bundles for the same language (tables packed separately)."""
    trovate = {}
    for percorso in _bundle_localizzazione(cartella):
        codice = _lingua(percorso)
        if codice:
            rel = os.path.relpath(percorso, cartella).replace(os.sep, "/")
            trovate.setdefault(codice, []).append(rel)
    return trovate


def tabelle_per_contenuto(cartella):
    """{codice lingua: percorso relativo} of the bundles with text tables: the code from the name ("(en)"),
    otherwise from the contents (m_LocaleId), otherwise from the language name (e.g. "english", "italiano", "en")."""
    return {c: p[0] for c, p in bundle_tabelle(cartella).items()}


tabelle = tabelle_per_contenuto  # previous name


# --- Addressables catalog and bundle fingerprint (CRC) ------------------------------------------------------------

def catalogo_di(percorso_bundle):
    """The bundle's Addressables catalog (catalog.json or catalog.bin in the aa folder), or None."""
    cartella = os.path.dirname(os.path.dirname(percorso_bundle))
    for nome in ("catalog.json", "catalog.bin"):
        p = os.path.join(cartella, nome)
        if os.path.isfile(p):
            return p
    return None


_EXTRA = re.compile(rb'"m_ExtraDataString"\s*:\s*"([A-Za-z0-9+/=]*)"')
_CRC = re.compile(r'("m_Crc"\s*:\s*)(\d+)')


def _opzioni(byte_catalogo, nomi):
    """(dati extra, [(inizio, fine, codifica)]): where, in the catalog's extra data (m_ExtraDataString), the options
    (AssetBundleRequestOptions, JSON text) of the bundles with these file names are. The catalog's entries
    (m_EntryDataString, 7 integers each) say which id (the bundle's path) uses which extra data."""
    dati = json.loads(byte_catalogo.decode("utf-8-sig"))
    m = _EXTRA.search(byte_catalogo)
    extra = base64.b64decode(dati["m_ExtraDataString"])
    if not m or base64.b64decode(m.group(1)) != extra:
        raise ValueError("dati extra del catalogo non trovati")
    voci = base64.b64decode(dati["m_EntryDataString"])
    ids = dati["m_InternalIds"]
    cercati = {n.lower() for n in nomi}
    trovate = []
    for k in range(struct.unpack_from("<i", voci, 0)[0]):
        interno, _, _, _, indice = struct.unpack_from("<5i", voci, 4 + 28 * k)
        if indice < 0 or not 0 <= interno < len(ids):
            continue
        nome = re.split(r"[\\/#]", str(ids[interno]))[-1].lower()
        if nome not in cercati or extra[indice] != 7:  # 7 = JSON object
            continue
        q = indice + 1
        q += 1 + extra[q]  # assembly name
        classe = extra[q + 1:q + 1 + extra[q]]
        q += 1 + extra[q]
        lunghezza = struct.unpack_from("<i", extra, q)[0]
        q += 4
        testo = extra[q:q + lunghezza]
        if b"AssetBundleRequestOptions" not in classe or lunghezza <= 0 or len(testo) != lunghezza:
            continue
        codifica = "utf-16-le" if testo.startswith(b"{\x00") else "utf-8" if testo.startswith(b"{") else None
        if codifica:
            trovate.append((nome, q, q + lunghezza, codifica))
    return extra, trovate


def _crc(testo):
    m = _CRC.search(testo)
    return int(m.group(2)) if m else 0


def _binario(byte_catalogo):
    """True for catalog.bin (Addressables 2), False for catalog.json."""
    return not byte_catalogo.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{")


def _record_bin(byte_catalogo, nome_bundle):
    """Positions of the bundle's CRC in the binary catalog (catalog.bin), or None if they cannot be found with
    certainty.

    In catalog.bin the options of each bundle (AssetBundleRequestOptions) are a record of 5 32-bit numbers: position
    of the Hash128 fingerprint (16 bytes), position of the bundle's name (a string with its length in the 4 bytes
    before it), CRC, size, position of the common data. The fingerprint is the one written in the name of the
    bundle's entry ("<nome>_<32 cifre esadecimali>.bundle"): from there the record is found. Each fingerprint must
    lead to a single record."""
    radice = re.escape(os.path.splitext(nome_bundle)[0].encode("utf-8"))
    impronte = set(re.findall(rb"(?<![^\x00-\x20/\\])" + radice + rb"_([0-9a-f]{32})\.bundle", byte_catalogo))
    if not impronte:
        return None
    posizioni = []
    for esa in impronte:
        dove = [m.start() for m in re.finditer(re.escape(bytes.fromhex(esa.decode("ascii"))), byte_catalogo)]
        trovati = []
        for h in dove:
            for m in re.finditer(re.escape(struct.pack("<I", h)), byte_catalogo):
                o = m.start()
                if o + 20 > len(byte_catalogo):
                    continue
                nome = struct.unpack_from("<I", byte_catalogo, o + 4)[0]
                if not 4 <= nome <= len(byte_catalogo) - 1:
                    continue
                lunghezza = struct.unpack_from("<I", byte_catalogo, nome - 4)[0]
                if 1 <= lunghezza <= 512 and nome + lunghezza <= len(byte_catalogo):
                    trovati.append(o + 8)
        if len(trovati) != 1:
            return None
        posizioni.append(trovati[0])
    return posizioni


def crc_del_bundle(percorso_bundle, leggi_da=None):
    """(stato, catalogo): how the Addressables catalog checks this bundle (read only).
    'assente': no catalog; 'binario': catalog.bin in which the bundle cannot be found with certainty; 'controllato':
    its CRC is not 0 (it must be removed from the catalog to rewrite the bundle); 'libero': CRC 0; 'sconosciuto': the
    catalog cannot be read or does not name the bundle. leggi_da: another file to read the catalog from (e.g. its
    backup)."""
    catalogo = catalogo_di(percorso_bundle)
    if catalogo is None:
        return "assente", None
    if catalogo.lower().endswith(".bin"):
        try:
            with open(leggi_da or catalogo, "rb") as f:
                byte = f.read()
            posizioni = _record_bin(byte, os.path.basename(percorso_bundle))
        except (OSError, ValueError, struct.error):
            posizioni = None
        if not posizioni:
            return "binario", catalogo
        crc = [struct.unpack_from("<I", byte, p)[0] for p in posizioni]
        return ("controllato" if any(crc) else "libero"), catalogo
    try:
        with open(leggi_da or catalogo, "rb") as f:
            byte = f.read()
        extra, trovate = _opzioni(byte, [os.path.basename(percorso_bundle)])
    except (OSError, ValueError, KeyError, TypeError, IndexError, struct.error):
        return "sconosciuto", catalogo
    if not trovate:
        return "sconosciuto", catalogo
    crc = [_crc(extra[a:b].decode(cod)) for _, a, b, cod in trovate]
    return ("controllato" if any(crc) else "libero"), catalogo


def togli_crc(byte_catalogo, nomi_bundle):
    """The catalog (catalog.json) with CRC 0 for the bundles with these file names, or None if one is missing or the
    catalog cannot be read. The number becomes "0" followed by spaces (the JSON stays valid and the same length: the
    positions of the other extra data do not change); in the file only the base64 text of the extra data changes.
    With catalog.bin the 4 CRC bytes of those bundles' records are set to 0 (see _record_bin), nothing else."""
    if _binario(byte_catalogo):
        nuovo = bytearray(byte_catalogo)
        for nome in nomi_bundle:
            try:
                posizioni = _record_bin(byte_catalogo, nome)
            except (ValueError, struct.error):
                posizioni = None
            if not posizioni:
                return None
            for p in posizioni:
                nuovo[p:p + 4] = b"\x00\x00\x00\x00"
        return bytes(nuovo)
    try:
        extra, trovate = _opzioni(byte_catalogo, nomi_bundle)
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return None
    if {n.lower() for n in nomi_bundle} - {t[0] for t in trovate}:
        return None
    nuovo = bytearray(extra)
    for _, a, b, cod in trovate:
        testo = extra[a:b].decode(cod)
        m = _CRC.search(testo)
        if m and int(m.group(2)):
            testo = testo[:m.start(2)] + "0" + " " * (len(m.group(2)) - 1) + testo[m.end(2):]
            byte = testo.encode(cod)
            if len(byte) != b - a:
                return None
            nuovo[a:b] = byte
    m = _EXTRA.search(byte_catalogo)
    codificato = base64.b64encode(bytes(nuovo))
    if len(codificato) != len(m.group(1)):
        return None
    risultato = byte_catalogo[:m.start(1)] + codificato + byte_catalogo[m.end(1):]
    # check: it is read again, the bundles have CRC 0 and the rest of the extra data is identical
    try:
        extra2, trovate2 = _opzioni(risultato, nomi_bundle)
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return None
    if len(extra2) != len(extra) or any(_crc(extra2[a:b].decode(cod)) for _, a, b, cod in trovate2):
        return None
    return risultato


def crc_controllato(percorso_bundle):
    """True if the Addressables catalog next to it checks the fingerprint (CRC) of some bundle (fallback when the
    catalog says nothing about the bundle). None if the catalog cannot be found or read."""
    cartella = os.path.dirname(os.path.dirname(percorso_bundle))
    catalogo = os.path.join(cartella, "catalog.json")
    try:
        with open(catalogo, encoding="utf-8") as f:
            dati = json.load(f)
        extra = base64.b64decode(dati.get("m_ExtraDataString", ""))
    except (OSError, ValueError):
        return None
    # the UTF-16 JSON texts can start at an even or odd position
    testo = extra.decode("utf-16-le", "ignore") + extra[1:].decode("utf-16-le", "ignore") + extra.decode("latin-1")
    return any(v != "0" for v in re.findall(r"m_Crc\W{0,4}(\d+)", testo))


# --- text tables --------------------------------------------------------------------------------------------------

class FileTabelle:
    """A bundle's text tables: testi() to translate and serializza(traduzioni) to rewrite it. The asset tables
    (entries that are references, not texts) stay as they are."""

    def __init__(self, percorso_o_dati):
        if isinstance(percorso_o_dati, (bytes, bytearray)):
            dati = bytes(percorso_o_dati)
        else:
            with open(percorso_o_dati, "rb") as f:
                dati = f.read()
        self.bundle = unityfs.Bundle(dati)
        self.serializzati = {}
        self._unita = []
        for nome, _ in self.bundle.nodi:
            if nome.endswith((".resS", ".resource")):
                continue
            try:
                s = unityfs.FileSerializzato(self.bundle.file[nome])
            except unityfs.ErroreUnity:
                continue
            self.serializzati[nome] = s
            for o in s.oggetti:
                albero = s.tipi[o.tipo]["albero"]
                if albero is None or not any(f.nome == "m_TableData" for f in albero.figli):
                    continue
                d = s.dati_oggetto(o)
                unita, riferimenti = [], 0
                for percorso, inizio, fine in s.stringhe(o):
                    if inizio is not None and percorso[-1] == "m_Localized" and "m_TableData" in percorso:
                        testo = d[inizio + 4:fine].decode("utf-8", "replace")
                        if _GUID.match(testo):
                            riferimenti += 1
                        elif any(c.isalpha() for c in testo):
                            unita.append(((nome, o.path_id, inizio, fine), testo))
                if riferimenti and not unita:  # asset table
                    continue
                self._unita += unita

    def testi(self):
        """[(posizione, testo)] of the tables' texts."""
        return list(self._unita)

    def serializza(self, traduzioni):
        """The bundle with the translated texts ({posizione: testo}), not compressed."""
        per_file = {}
        for (nome, path_id, inizio, fine), testo in traduzioni.items():
            per_file.setdefault(nome, {}).setdefault(path_id, {})[(inizio, fine)] = testo
        for nome, nuovi in per_file.items():
            self.bundle.file[nome] = self.serializzati[nome].con_testi_nuovi(nuovi)
        return self.bundle.serializza()
