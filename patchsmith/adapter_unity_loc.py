"""Adapter Unity Localization: traduce le tabelle dei testi (StringTable) dentro gli AssetBundle del gioco.

Molti giochi Unity recenti usano il pacchetto Localization: i testi di ogni lingua stanno in un bundle di
Addressables, es. StreamingAssets/aa/StandaloneWindows64/localization-string-tables-english(en)_assets_all.bundle.
PatchSmith traduce i testi della tabella inglese (campo m_Localized di ogni voce) e riscrive il bundle, con il
backup verificato come per gli altri file. Funziona anche con i giochi IL2CPP, senza mod: nel gioco si lascia
selezionato English. Il bundle riscritto non e' compresso (Unity lo legge lo stesso).

La lingua di un bundle si riconosce dal nome quando c'e' il codice tra parentesi ("english(en)"), altrimenti dal
contenuto (il codice m_LocaleId delle sue tabelle: "localization-string-tables-english_assets_all",
"localization_en_assets_all", "chinese(simplified)") e, se non si legge, dal nome della lingua.

Impronta (CRC): se il catalogo di Addressables (catalog.json) controlla l'impronta del bundle, un bundle riscritto
non verrebbe caricato. Allora nel catalogo si mette 0 al CRC di quel bundle soltanto (stessa lunghezza, il resto del
file identico), con backup e ripristino come per gli altri file (vedi Progetto._togli_crc). Lo stesso con il
catalogo binario (catalog.bin, Addressables 2): vedi _record_bin; se il record del bundle non si trova con certezza,
non si modifica nulla.
"""
import base64
import json
import os
import re
import struct

from . import unityfs

_TABELLA = re.compile(r"^localization-string-tables-.*\(([A-Za-z]{2,3}(?:-[A-Za-z]+)?)\)_.*\.bundle$", re.I)
# nome di ripiego: "localization-string-tables-<lingua>_assets_..." o "localization_<lingua>_assets_..."
_NOME_LINGUA = re.compile(r"^locali[sz]ation[-_](?:string[-_]tables?[-_])?(.+?)_assets_.*\.bundle$", re.I)
_CODICE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", re.I)
_TABELLE_ASSET = re.compile(r"asset[-_]?tables?", re.I)  # tabelle degli asset (immagini, suoni): niente testi
_GUID = re.compile(r"^[0-9a-f]{32}(?:\[.*\])?$")  # voci delle tabelle degli asset: riferimenti, non testi
# nomi delle lingue nei nomi dei bundle (in inglese e nella lingua stessa) -> codice
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
MAX_BUNDLE_CONTENUTO = 64 * 1024 * 1024  # bundle piu' grandi non si aprono per riconoscerne la lingua
_CACHE = {}  # (percorso, dimensione, data) -> codice della lingua (o None): un bundle si legge una volta sola


def _cartelle_saltate(nome):
    return nome.lower() in ("plugins", "il2cpp_data", "managed", "mono", "bepinex")


def _bundle_localizzazione(cartella):
    """[percorso] dei bundle con 'locali' nel nome (Unity Localization), tranne le tabelle degli asset."""
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
    """(codice della lingua, numero di testi) di un oggetto tabella (m_LocaleId e m_TableData)."""
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
    """(leggibile, codice): il codice della lingua (m_LocaleId) delle tabelle dei testi del bundle, o None se non ha
    tabelle con dei testi (tabelle degli asset, i Locales, tabelle senza gli alberi dei campi); leggibile=False se il
    bundle non si apre (troppo grande, rovinato, non UnityFS)."""
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
    """Il codice della lingua (m_LocaleId) delle tabelle dei testi del bundle, o None (vedi _lingua_contenuto)."""
    return _lingua_contenuto(percorso_o_dati)[1]


def _lingua_dal_nome(nome):
    m = _NOME_LINGUA.match(nome)
    if not m:
        return None
    parte = m.group(1).lower()
    if parte in _NOMI_LINGUE:
        return _NOMI_LINGUE[parte]
    parte = re.sub(r"\(([a-z]{2,3}(?:-[a-z0-9]+)?)\)$", "", parte)  # "english(en)": gia' visto da _TABELLA
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
        if not leggibile:  # vale il nome della lingua nel nome del file; un bundle che si legge e non ha tabelle
            codice = _lingua_dal_nome(nome)  # dei testi (es. i Locales, i dati condivisi) non e' una lingua
        _CACHE[chiave] = codice.lower() if codice else None
    return _CACHE[chiave]


def bundle_tabelle(cartella):
    """{codice lingua: [percorsi relativi]} di tutti i bundle con le tabelle dei testi (Unity Localization), anche
    piu' bundle per la stessa lingua (tabelle impacchettate separatamente)."""
    trovate = {}
    for percorso in _bundle_localizzazione(cartella):
        codice = _lingua(percorso)
        if codice:
            rel = os.path.relpath(percorso, cartella).replace(os.sep, "/")
            trovate.setdefault(codice, []).append(rel)
    return trovate


def tabelle_per_contenuto(cartella):
    """{codice lingua: percorso relativo} dei bundle con le tabelle dei testi: il codice dal nome ("(en)"), se no
    dal contenuto (m_LocaleId), se no dal nome della lingua (es. "english", "italiano", "en")."""
    return {c: p[0] for c, p in bundle_tabelle(cartella).items()}


tabelle = tabelle_per_contenuto  # nome di prima


# --- catalogo di Addressables e impronta (CRC) dei bundle --------------------------------------------------------

def catalogo_di(percorso_bundle):
    """Il catalogo di Addressables del bundle (catalog.json o catalog.bin nella cartella aa), o None."""
    cartella = os.path.dirname(os.path.dirname(percorso_bundle))
    for nome in ("catalog.json", "catalog.bin"):
        p = os.path.join(cartella, nome)
        if os.path.isfile(p):
            return p
    return None


_EXTRA = re.compile(rb'"m_ExtraDataString"\s*:\s*"([A-Za-z0-9+/=]*)"')
_CRC = re.compile(r'("m_Crc"\s*:\s*)(\d+)')


def _opzioni(byte_catalogo, nomi):
    """(dati extra, [(inizio, fine, codifica)]): dove stanno, nei dati extra del catalogo (m_ExtraDataString), le
    opzioni (AssetBundleRequestOptions, testo JSON) dei bundle con questi nomi di file. Le voci del catalogo
    (m_EntryDataString, 7 interi ciascuna) dicono quale id (il percorso del bundle) usa quali dati extra."""
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
        if nome not in cercati or extra[indice] != 7:  # 7 = oggetto JSON
            continue
        q = indice + 1
        q += 1 + extra[q]  # nome dell'assembly
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
    """True per catalog.bin (Addressables 2), False per catalog.json."""
    return not byte_catalogo.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"{")


def _record_bin(byte_catalogo, nome_bundle):
    """Posizioni del CRC del bundle nel catalogo binario (catalog.bin), o None se non si trovano con certezza.

    Nel catalog.bin le opzioni di ogni bundle (AssetBundleRequestOptions) sono un record di 5 numeri a 32 bit:
    posizione dell'impronta Hash128 (16 byte), posizione del nome del bundle (stringa con la lunghezza nei 4 byte
    prima), CRC, dimensione, posizione dei dati comuni. L'impronta e' quella scritta nel nome della voce del bundle
    ("<nome>_<32 cifre esadecimali>.bundle"): da li' si trova il record. Ogni impronta deve portare a un record solo."""
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
    """(stato, catalogo): come il catalogo di Addressables controlla questo bundle (sola lettura).
    'assente': nessun catalogo; 'binario': catalog.bin in cui il bundle non si trova con certezza; 'controllato': il
    suo CRC non e' 0 (va tolto dal catalogo per riscrivere il bundle); 'libero': CRC 0; 'sconosciuto': il catalogo
    non si legge o non nomina il bundle. leggi_da: un altro file da cui leggere il catalogo (es. il suo backup)."""
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
    """Il catalogo (catalog.json) con CRC 0 per i bundle con questi nomi di file, o None se uno non c'e' o il
    catalogo non si legge. Il numero diventa "0" seguito da spazi (il JSON resta valido e lungo uguale: le posizioni
    degli altri dati extra non cambiano); nel file cambia solo il testo base64 dei dati extra.
    Con catalog.bin si mettono a 0 i 4 byte del CRC dei record di quei bundle (vedi _record_bin), nient'altro."""
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
    # controllo: si rilegge, i bundle hanno CRC 0 e il resto dei dati extra e' identico
    try:
        extra2, trovate2 = _opzioni(risultato, nomi_bundle)
    except (ValueError, KeyError, TypeError, IndexError, struct.error):
        return None
    if len(extra2) != len(extra) or any(_crc(extra2[a:b].decode(cod)) for _, a, b, cod in trovate2):
        return None
    return risultato


def crc_controllato(percorso_bundle):
    """True se il catalogo di Addressables accanto controlla l'impronta (CRC) di qualche bundle (ripiego quando il
    catalogo non dice nulla del bundle). None se il catalogo non si trova o non si legge."""
    cartella = os.path.dirname(os.path.dirname(percorso_bundle))
    catalogo = os.path.join(cartella, "catalog.json")
    try:
        with open(catalogo, encoding="utf-8") as f:
            dati = json.load(f)
        extra = base64.b64decode(dati.get("m_ExtraDataString", ""))
    except (OSError, ValueError):
        return None
    # i testi JSON in UTF-16 possono iniziare a una posizione pari o dispari
    testo = extra.decode("utf-16-le", "ignore") + extra[1:].decode("utf-16-le", "ignore") + extra.decode("latin-1")
    return any(v != "0" for v in re.findall(r"m_Crc\W{0,4}(\d+)", testo))


# --- tabelle dei testi --------------------------------------------------------------------------------------------

class FileTabelle:
    """Le tabelle dei testi di un bundle: testi() da tradurre e serializza(traduzioni) per riscriverlo. Le tabelle
    degli asset (voci che sono riferimenti, non testi) restano come sono."""

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
                if riferimenti and not unita:  # tabella degli asset
                    continue
                self._unita += unita

    def testi(self):
        """[(posizione, testo)] dei testi delle tabelle."""
        return list(self._unita)

    def serializza(self, traduzioni):
        """Il bundle con i testi tradotti ({posizione: testo}), non compresso."""
        per_file = {}
        for (nome, path_id, inizio, fine), testo in traduzioni.items():
            per_file.setdefault(nome, {}).setdefault(path_id, {})[(inizio, fine)] = testo
        for nome, nuovi in per_file.items():
            self.bundle.file[nome] = self.serializzati[nome].con_testi_nuovi(nuovi)
        return self.bundle.serializza()
