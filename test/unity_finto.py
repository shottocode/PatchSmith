"""Pezzi di giochi Unity finti per le prove (nessun file di giochi veri):
- bundle(): AssetBundle UnityFS con i file interni dati, senza compressione, LZ4 o LZMA, a blocchi;
- file_tabelle(): file serializzato (versione 22, con gli alberi dei campi) con tabelle dei testi di Unity
  Localization (StringTable: nome, m_LocaleId, voci m_Localized);
- stringhe_serializzate(): byte con stringhe "lunghezza + UTF-8" come nei file degli asset;
- catalogo(): catalog.json di Addressables con le opzioni (e il CRC) di ogni bundle;
- dll(): DLL .NET minima con le stringhe del codice (heap #US);
- gioco_il2cpp(): gioco Unity IL2CPP con i bundle in StreamingAssets/aa/StandaloneWindows64.
"""
import base64
import json
import lzma
import os
import struct

# albero dei campi di una StringTable (ridotto: senza metadati e riferimenti gestiti, che stanno in fondo)
_ALBERO_TABELLA = [
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


def _stringa(testo):
    b = testo.encode("utf-8")
    return struct.pack("<i", len(b)) + b + b"\x00" * (-len(b) % 4)


def stringhe_serializzate(testi, riempimento=b"\x07\x00\x00\x00"):
    """Byte come in un file degli asset: ogni testo con la sua lunghezza (4 byte) e l'allineamento a 4."""
    return riempimento + b"".join(_stringa(t) + riempimento for t in testi)


def _dati_tabella(nome, codice, voci):
    b = struct.pack("<iq", 0, 0) + b"\x01\x00\x00\x00" + struct.pack("<iq", 0, 0) + _stringa(nome) + _stringa(codice)
    b += struct.pack("<i", len(voci))
    for k, testo in enumerate(voci):
        b += struct.pack("<q", 1000 + k) + _stringa(testo)
    return b


def file_tabelle(tabelle, versione_unity="2022.3.62f2"):
    """File serializzato con le tabelle [(nome, codice della lingua, [testi])]."""
    buffer, posizioni = bytearray(), {}

    def posizione(s):
        if s not in posizioni:
            posizioni[s] = len(buffer)
            buffer.extend(s.encode("ascii") + b"\x00")
        return posizioni[s]
    righe = b""
    for k, (livello, tipo, nome, dimensione, allinea) in enumerate(_ALBERO_TABELLA):
        righe += struct.pack("<HBBIIiii", 1, livello, 0, posizione(tipo), posizione(nome), dimensione, k,
                             0x4000 if allinea else 0) + struct.pack("<Q", 0)
    tipo = struct.pack("<iBh", 114, 0, 0) + b"\x00" * 32 + struct.pack("<ii", len(_ALBERO_TABELLA), len(buffer))
    tipo += righe + bytes(buffer) + struct.pack("<i", 0)
    oggetti = [_dati_tabella(*t) for t in tabelle]
    meta = versione_unity.encode("ascii") + b"\x00" + struct.pack("<i", 19) + b"\x01" + struct.pack("<i", 1) + tipo
    meta += struct.pack("<i", len(oggetti))
    corpo, voci = b"", []
    for k, d in enumerate(oggetti):
        corpo += b"\x00" * (-len(corpo) % 8)
        voci.append((k + 1, len(corpo), len(d)))
        corpo += d
    for path_id, inizio, dimensione in voci:
        meta += b"\x00" * (-(48 + len(meta)) % 4)
        meta += struct.pack("<qqIi", path_id, inizio, dimensione, 0)
    meta += struct.pack("<iii", 0, 0, 0) + b"\x00"
    inizio_dati = 48 + len(meta)
    inizio_dati += -inizio_dati % 16
    totale = inizio_dati + len(corpo)
    testa = struct.pack(">IIII", 0, 0, 22, 0) + b"\x00" * 4 + struct.pack(">IqqQ", len(meta), totale, inizio_dati, 0)
    return testa + meta + b"\x00" * (inizio_dati - 48 - len(meta)) + corpo


# albero dei campi di un font TextMeshPro (TMP_FontAsset, ridotto: nome, modo dell'atlante, tabella dei caratteri)
_ALBERO_FONT_TMP = _ALBERO_TABELLA[:12] + [
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
# albero ridotto di un font .ttf (Font: nome e i byte del file)
_ALBERO_FONT = [
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


def dati_font_tmp(nome, caratteri, modo=0):
    """I byte di un font TextMeshPro: m_GameObject, m_Enabled, m_Script, m_Name, il modo dell'atlante (0 statico, 1
    dinamico) e la tabella dei caratteri (tipo 1, codice, glifo, scala 1.0)."""
    codici = sorted({ord(c) for c in caratteri})
    b = struct.pack("<iq", 0, 0) + b"\x01\x00\x00\x00" + struct.pack("<iq", 0, 0) + _stringa(nome)
    b += struct.pack("<ii", modo, len(codici))
    for k, c in enumerate(codici):
        b += struct.pack("<IIIf", 1, c, k + 1, 1.0)
    return b


def ttf(caratteri):
    """Un font TrueType minimo: solo la tabella cmap (formato 4) con questi caratteri."""
    codici = sorted({ord(c) for c in caratteri if ord(c) < 0xFFFF}) + [0xFFFF]
    n = len(codici)
    fine = b"".join(struct.pack(">H", c) for c in codici)
    inizio = fine
    delta = b"".join(struct.pack(">H", ((k + 1) - c) & 0xFFFF) if c != 0xFFFF else struct.pack(">H", 1)
                     for k, c in enumerate(codici))
    sotto = struct.pack(">HHHHHHH", 4, 16 + 8 * n, 0, 2 * n, 0, 0, 0) + fine + b"\x00\x00" + inizio + delta + \
        b"\x00\x00" * n
    cmap = struct.pack(">HH", 0, 1) + struct.pack(">HHI", 3, 1, 12) + sotto
    return struct.pack(">IHHHH", 0x00010000, 1, 16, 0, 0) + struct.pack(">4sIII", b"cmap", 0, 28, len(cmap)) + cmap


def dati_font(nome, byte_ttf):
    return _stringa(nome) + struct.pack("<i", len(byte_ttf)) + byte_ttf + b"\x00" * (-len(byte_ttf) % 4)


def _tipo(classe, albero, alberi):
    """Un tipo della tabella dei tipi (versione 22): classe, (script), impronta, albero dei campi."""
    t = struct.pack("<iBh", classe, 0, 0) + b"\x00" * (32 if classe == 114 else 16)
    if not alberi:
        return t
    buffer, posizioni = bytearray(), {}

    def posizione(s):
        if s not in posizioni:
            posizioni[s] = len(buffer)
            buffer.extend(s.encode("ascii") + b"\x00")
        return posizioni[s]
    righe = b""
    for k, (livello, tipo, nome, dimensione, allinea) in enumerate(albero):
        righe += struct.pack("<HBBIIiii", 1, livello, 0, posizione(tipo), posizione(nome), dimensione, k,
                             0x4000 if allinea else 0) + struct.pack("<Q", 0)
    return t + struct.pack("<ii", len(albero), len(buffer)) + righe + bytes(buffer) + struct.pack("<i", 0)


def file_serializzato(oggetti, alberi=True, versione_unity="2022.3.62f2"):
    """File serializzato (versione 22) con gli oggetti [(classe, albero, byte)]: un tipo per ogni (classe, albero);
    alberi=False come i file di <gioco>_Data (senza gli alberi dei campi)."""
    tipi, indici = [], {}
    for classe, albero, _ in oggetti:
        chiave = (classe, id(albero))
        if chiave not in indici:
            indici[chiave] = len(tipi)
            tipi.append(_tipo(classe, albero, alberi))
    meta = versione_unity.encode("ascii") + b"\x00" + struct.pack("<i", 19) + (b"\x01" if alberi else b"\x00")
    meta += struct.pack("<i", len(tipi)) + b"".join(tipi) + struct.pack("<i", len(oggetti))
    corpo, voci = b"", []
    for k, (classe, albero, d) in enumerate(oggetti):
        corpo += b"\x00" * (-len(corpo) % 8)
        voci.append((k + 1, len(corpo), len(d), indici[(classe, id(albero))]))
        corpo += d
    for path_id, inizio, dimensione, tipo in voci:
        meta += b"\x00" * (-(48 + len(meta)) % 4)
        meta += struct.pack("<qqIi", path_id, inizio, dimensione, tipo)
    meta += struct.pack("<iii", 0, 0, 0) + b"\x00"
    inizio_dati = 48 + len(meta)
    inizio_dati += -inizio_dati % 16
    totale = inizio_dati + len(corpo)
    testa = struct.pack(">IIII", 0, 0, 22, 0) + b"\x00" * 4 + struct.pack(">IqqQ", len(meta), totale, inizio_dati, 0)
    return testa + meta + b"\x00" * (inizio_dati - 48 - len(meta)) + corpo


def file_font(font_tmp=(), font_ttf=(), alberi=True):
    """File serializzato con i font TextMeshPro [(nome, caratteri, modo)] e i font .ttf [(nome, caratteri)]."""
    oggetti = [(114, _ALBERO_FONT_TMP, dati_font_tmp(*f)) for f in font_tmp]
    oggetti += [(128, _ALBERO_FONT, dati_font(nome, ttf(caratteri))) for nome, caratteri in font_ttf]
    return file_serializzato(oggetti, alberi)


def _lz4_letterali(b):
    """Un blocco LZ4 valido fatto di soli letterali."""
    n = len(b)
    if n < 15:
        return bytes([n << 4]) + b
    uscita, resto = bytearray([0xF0]), n - 15
    while resto >= 255:
        uscita.append(255)
        resto -= 255
    uscita.append(resto)
    return bytes(uscita) + b


def _lzma(b):
    filtri = [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 20, "lc": 3, "lp": 0, "pb": 2}]
    return bytes([(2 * 5 + 0) * 9 + 3]) + struct.pack("<I", 1 << 20) + lzma.compress(
        b, format=lzma.FORMAT_RAW, filters=filtri)


def bundle(nodi, compressione=None, blocco=1 << 17, versione_unity="2022.3.62f2"):
    """AssetBundle UnityFS (versione 7) con i file interni [(nome, byte)]; compressione None, 'lz4' o 'lzma'."""
    dati = b"".join(b for _, b in nodi)
    blocchi, corpo = [], b""
    for i in range(0, max(len(dati), 1), blocco):
        pezzo = dati[i:i + blocco]
        if compressione == "lz4":
            c, tipo = _lz4_letterali(pezzo), 2
        elif compressione == "lzma":
            c, tipo = _lzma(pezzo), 1
        else:
            c, tipo = pezzo, 0
        blocchi.append((len(pezzo), len(c), tipo))
        corpo += c
    info = b"\x00" * 16 + struct.pack(">i", len(blocchi)) + b"".join(struct.pack(">IIH", *b) for b in blocchi)
    info += struct.pack(">i", len(nodi))
    posizione = 0
    for nome, b in nodi:
        info += struct.pack(">qqI", posizione, len(b), 4 if not nome.endswith((".resS", ".resource")) else 0)
        info += nome.encode("utf-8") + b"\x00"
        posizione += len(b)
    testa = b"UnityFS\x00" + struct.pack(">I", 7) + b"5.x.x\x00" + versione_unity.encode("ascii") + b"\x00"
    lunghezza = len(testa) + 20
    lunghezza += -lunghezza % 16
    testa += struct.pack(">qIII", lunghezza + len(info) + len(corpo), len(info), len(info), 0x40)
    return testa + b"\x00" * (lunghezza - len(testa)) + info + corpo


def bundle_tabelle(tabelle, **opzioni):
    return bundle([("CAB-%08x" % (hash(str(tabelle)) & 0xFFFFFFFF), file_tabelle(tabelle))], **opzioni)


_RUNTIME = "{UnityEngine.AddressableAssets.Addressables.RuntimePath}\\StandaloneWindows64\\"


def catalogo(bundle_crc, utf8=False):
    """Byte di un catalog.json di Addressables: per ogni (nome del file del bundle, crc) le sue opzioni
    (AssetBundleRequestOptions in JSON, UTF-16 come le scrive Unity), piu' un asset senza opzioni."""
    ids, extra, voci = ["Assets/Localization/Locales/English (en).asset"], bytearray(), [(0, 1, -1, 0, -1, 0, 0)]
    assembly = b"Unity.ResourceManager, Version=0.0.0.0, Culture=neutral, PublicKeyToken=null"
    classe = b"UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
    for k, (nome, crc) in enumerate(bundle_crc):
        ids.append(("0#\\" if k % 2 else _RUNTIME) + nome)  # anche con il prefisso abbreviato (m_InternalIdPrefixes)
        testo = json.dumps({"m_Hash": "%032x" % (k + 1), "m_Crc": crc, "m_Timeout": 0, "m_ChunkedTransfer": False,
                            "m_BundleName": "%032x" % (k + 99), "m_BundleSize": 1234 + k},
                           separators=(",", ":")).encode("utf-8" if utf8 else "utf-16-le")
        indice = len(extra)
        extra += bytes([7, len(assembly)]) + assembly + bytes([len(classe)]) + classe
        extra += struct.pack("<i", len(testo)) + testo
        voci.append((len(ids) - 1, 0, -1, 0, indice, 0, 0))
    voci_byte = struct.pack("<i", len(voci)) + b"".join(struct.pack("<7i", *v) for v in voci)
    dati = {"m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIdPrefixes": [_RUNTIME.rstrip("\\")],
            "m_InternalIds": ids, "m_KeyDataString": "AAAAAA==",
            "m_EntryDataString": base64.b64encode(voci_byte).decode("ascii"),
            "m_ExtraDataString": base64.b64encode(bytes(extra)).decode("ascii")}
    return json.dumps(dati, separators=(",", ":")).encode("utf-8")


def crc_nel_catalogo(byte_catalogo):
    """{nome del file del bundle: crc} letti dal catalogo (per controllare le prove)."""
    dati = json.loads(byte_catalogo.decode("utf-8-sig"))
    extra = base64.b64decode(dati["m_ExtraDataString"])
    voci = base64.b64decode(dati["m_EntryDataString"])
    uscita = {}
    for k in range(struct.unpack_from("<i", voci, 0)[0]):
        interno, _, _, _, indice, _, _ = struct.unpack_from("<7i", voci, 4 + 28 * k)
        if indice < 0:
            continue
        q = indice + 1
        q += 1 + extra[q]
        q += 1 + extra[q]
        n = struct.unpack_from("<i", extra, q)[0]
        testo = extra[q + 4:q + 4 + n]
        testo = testo.decode("utf-16-le" if testo.startswith(b"{\x00") else "utf-8")
        uscita[dati["m_InternalIds"][interno].replace("#", "\\").split("\\")[-1]] = json.loads(testo)["m_Crc"]
    return uscita


def dll(stringhe):
    """Una DLL .NET minima: intestazione MZ e metadati (BSJB) con le stringhe del codice nello heap #US."""
    heap = bytearray(b"\x00")
    for s in stringhe:
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
    versione = b"v4.0.30319\x00\x00"
    nome = b"#US\x00"
    testa = b"BSJB" + struct.pack("<HHI", 1, 1, 0) + struct.pack("<I", len(versione)) + versione
    testa += struct.pack("<HH", 0, 1)
    testa += struct.pack("<II", len(testa) + 8 + len(nome), len(heap)) + nome
    return b"MZ" + b"\x00" * 62 + testa + bytes(heap)


def _scrivi(percorso, byte):
    os.makedirs(os.path.dirname(percorso), exist_ok=True)
    with open(percorso, "wb") as f:
        f.write(byte)


def eseguibile_x64():
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    struct.pack_into("<I", pe, 0x3C, 0x80)
    pe[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", pe, 0x84, 0x8664)
    return bytes(pe)


def gioco_il2cpp(cartella, bundle_aa, catalogo_aa=None, extra=None):
    """Gioco Unity IL2CPP finto. bundle_aa: {nome del file: byte} in StreamingAssets/aa/StandaloneWindows64;
    catalogo_aa: (nome, byte) del catalogo in aa (catalog.json o catalog.bin); extra: {percorso relativo: byte}.
    Restituisce la cartella del bundle."""
    _scrivi(os.path.join(cartella, "Gioco.exe"), eseguibile_x64())
    _scrivi(os.path.join(cartella, "UnityPlayer.dll"), b"finto")
    _scrivi(os.path.join(cartella, "GameAssembly.dll"), b"finto")
    dati = os.path.join(cartella, "Gioco_Data")
    _scrivi(os.path.join(dati, "globalgamemanagers"), b"\x00" * 48 + b"2022.3.62f2\x00")
    _scrivi(os.path.join(dati, "il2cpp_data", "Metadata", "global-metadata.dat"), b"\xaf\x1b\xb1\xfa finto")
    _scrivi(os.path.join(dati, "app.info"), b"Studio Finto\nGioco Finto")
    aa = os.path.join(dati, "StreamingAssets", "aa")
    cartella_bundle = os.path.join(aa, "StandaloneWindows64")
    os.makedirs(cartella_bundle, exist_ok=True)
    for nome, byte in bundle_aa.items():
        _scrivi(os.path.join(cartella_bundle, nome), byte)
    if catalogo_aa:
        _scrivi(os.path.join(aa, catalogo_aa[0]), catalogo_aa[1])
    for rel, byte in (extra or {}).items():
        _scrivi(os.path.join(cartella, *rel.split("/")), byte)
    return cartella_bundle
