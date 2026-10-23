"""Official language in Unity games: which languages the game has and, if it uses I2 Localization, how to set it.

Detection (read only):
- I2 Localization: the language lists (name, code, flag) saved in the assets (resources.assets, level*,
  sharedassets*, also inside data.unity3d); a language with the "disabled" flag does not count;
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
_MASSIMO = 400 * 1024 * 1024  # maximum bytes of assets read per game


class ErroreLinguaUnity(ValueError):
    pass


# --- data folder and app.info -------------------------------------------------------------------

def cartella_dati(cartella):
    for radice, dirs, files in os.walk(cartella):
        if os.path.relpath(radice, cartella).count(os.sep) >= 2:
            dirs[:] = []
        for d in sorted(dirs):
            p = os.path.join(radice, d)
            if d.endswith("_Data") and (os.path.exists(os.path.join(p, "globalgamemanagers")) or
                                        os.path.exists(os.path.join(p, "data.unity3d"))):
                return p
    return None


def azienda_prodotto(dati):
    try:
        with open(os.path.join(dati, "app.info"), encoding="utf-8", errors="replace") as f:
            righe = f.read().split("\n")
    except OSError:
        return None, None
    return (righe[0].strip() or None), (righe[1].strip() if len(righe) > 1 and righe[1].strip() else None)


# --- UnityFS (data.unity3d) -----------------------------------------------------------------------

def _lz4(src, dim):
    try:
        return _lz4_blocco(src, dim)
    except IndexError as e:
        raise ErroreLinguaUnity("LZ4 non valido") from e


def _lz4_blocco(src, dim):
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
        lung = tok & 15
        if lung == 15:
            while True:
                b = src[i]
                i += 1
                lung += b
                if b != 255:
                    break
        lung += 4
        inizio = len(out) - off
        if off <= 0 or inizio < 0:
            raise ErroreLinguaUnity("LZ4 non valido")
        if off >= lung:
            out += out[inizio:inizio + lung]
        else:
            for k in range(lung):
                out.append(out[inizio + k])
    if len(out) != dim:
        raise ErroreLinguaUnity("LZ4: dimensione inattesa")
    return out


def _decomprimi(dati, tipo, dim):
    if tipo == 0:
        return dati
    if tipo == 1:
        props = dati[0]
        filtro = [{"id": lzma.FILTER_LZMA1, "lc": props % 9, "lp": (props // 9) % 5, "pb": props // 45,
                   "dict_size": struct.unpack_from("<I", dati, 1)[0]}]
        return lzma.decompress(dati[5:], format=lzma.FORMAT_RAW, filters=filtro)[:dim]
    if tipo in (2, 3):
        return _lz4(dati, dim)
    raise ErroreLinguaUnity("compressione %d non supportata" % tipo)


def _apri_bundle(percorso):
    f = open(percorso, "rb")
    try:
        testa = f.read(512)
        p = testa.index(b"\0")
        if testa[:p] != b"UnityFS":
            raise ErroreLinguaUnity("non e' un bundle UnityFS")
        p += 1
        versione = struct.unpack_from(">I", testa, p)[0]
        p += 4
        for _ in range(2):
            p = testa.index(b"\0", p) + 1
        totale, c_info, u_info, flags = struct.unpack_from(">qIII", testa, p)
        p += 20
        if versione >= 7:
            p = (p + 15) & ~15
        f.seek(totale - c_info if flags & 0x80 else p)
        info = _decomprimi(f.read(c_info), flags & 0x3F, u_info)
        if not flags & 0x80:
            p += c_info
            if flags & 0x200:
                p = (p + 15) & ~15
        q = 16  # hash of the uncompressed data
        quanti = struct.unpack_from(">i", info, q)[0]
        q += 4
        blocchi = []
        for _ in range(quanti):
            blocchi.append(struct.unpack_from(">IIH", info, q))
            q += 10
        quanti = struct.unpack_from(">i", info, q)[0]
        q += 4
        nodi = []
        for _ in range(quanti):
            off, dim, _flag = struct.unpack_from(">qqI", info, q)
            q += 20
            fine = info.index(b"\0", q)
            nodi.append((info[q:fine].decode("utf-8", "replace"), off, dim))
            q = fine + 1
        return f, p, blocchi, nodi
    except BaseException:
        f.close()
        raise


def _estrai(f, inizio, blocchi, off, dim):
    out, pos_c, pos_u = bytearray(), inizio, 0
    for u, c, fl in blocchi:
        if pos_u + u > off and pos_u < off + dim:
            f.seek(pos_c)
            blocco = _decomprimi(f.read(c), fl & 0x3F, u)
            out += blocco[max(off, pos_u) - pos_u:min(off + dim, pos_u + u) - pos_u]
        pos_c += c
        pos_u += u
        if pos_u >= off + dim:
            break
    return out


def _mappa(percorso):
    """The file, read only, as an mmap (the memory is not taken all at once); b"" if it is empty."""
    with open(percorso, "rb") as f:
        if os.fstat(f.fileno()).st_size == 0:
            return b""
        return mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)


def _file_asset(dati):
    """([(name, size, function that returns the bytes)], function to close) of the game's serialized files, the
    global catalog first."""
    uscita, aperti = [], []
    bundle = os.path.join(dati, "data.unity3d")
    if os.path.exists(bundle):
        try:
            f, inizio, blocchi, nodi = _apri_bundle(bundle)
            aperti.append(f)
            for nome, off, dim in nodi:
                if _ASSET.match(nome):
                    uscita.append((nome, dim, lambda off=off, dim=dim: _estrai(f, inizio, blocchi, off, dim)))
        except (ErroreLinguaUnity, OSError, struct.error, ValueError, IndexError, lzma.LZMAError):
            pass
    for nome in sorted(os.listdir(dati)):
        if _ASSET.match(nome):
            p = os.path.join(dati, nome)
            try:
                dim = os.path.getsize(p)
            except OSError:
                continue
            uscita.append((nome, dim, lambda p=p: _mappa(p)))

    def ordine(voce):
        nome = voce[0]
        if nome == "resources.assets":
            return (0, 0)
        if nome == "globalgamemanagers.assets":
            return (1, 0)
        return (2 if nome.startswith("level") else 3, int(re.sub(r"\D", "", nome) or 0))

    def chiudi():
        for f in aperti:
            f.close()
    return sorted(uscita, key=ordine), chiudi


# --- I2 Localization ------------------------------------------------------------------------------

_CODICE_I2 = re.compile(r"^[ -~]{1,24}$")


def _stringa(d, p):
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


def _prova_lista(d, p):
    if p + 4 > len(d):
        return None
    quante = struct.unpack_from("<i", d, p)[0]
    if not 1 <= quante <= 120:
        return None
    q, voci = p + 4, []
    for _ in range(quante):
        nome, q = _stringa(d, q)
        if not nome or len(nome) < 2 or not all(c.isprintable() or c in "\n\r\t" for c in nome):
            return None
        codice, q = _stringa(d, q)
        if codice is None or (codice and not _CODICE_I2.match(codice)) or q + 4 > len(d):
            return None
        flag = d[q]
        if d[q + 1:q + 4] != b"\x00\x00\x00" or flag > 0x0F:
            return None
        voci.append((nome, codice, flag))
        q += 4
    return voci, q


def liste_i2(d):
    """[[(nome, codice, flag)]] of the I2 language lists found in the bytes of an asset."""
    uscita, viste, fine_ultima = [], set(), -1
    for m in re.finditer(rb"\x02\x00\x00\x00en\x00\x00|\x05\x00\x00\x00en-[A-Z]{2}\x00\x00\x00", d):
        if m.start() < fine_ultima:
            continue  # inside a list already read
        migliore = None
        for indietro in range(m.start() - 4, max(0, m.start() - 16000), -4):
            r = _prova_lista(d, indietro)
            if r and r[1] > m.start() and (migliore is None or len(r[0]) > len(migliore[1][0])):
                migliore = (indietro, r)
        if migliore and migliore[0] not in viste:
            viste.add(migliore[0])
            uscita.append(migliore[1][0])
            fine_ultima = migliore[1][1]
    return uscita


def usa_i2(dati):
    """True if the game contains I2 Localization (from the managed libraries or the IL2CPP metadata)."""
    gestite = os.path.join(dati, "Managed")
    if os.path.isdir(gestite):
        if any(n.lower().startswith("i2") and "locali" in n.lower() for n in os.listdir(gestite)):
            return True
        for nome in ("Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll"):
            p = os.path.join(gestite, nome)
            if os.path.exists(p):
                with open(p, "rb") as f:
                    if b"I2.Loc\x00" in f.read():
                        return True
    meta = os.path.join(dati, "il2cpp_data", "Metadata", "global-metadata.dat")
    if os.path.exists(meta):
        with open(meta, "rb") as f:
            return b"I2.Loc\x00" in f.read()
    return False


def lingue_i2(dati, dopo_la_prima=2):
    """[(exact name, code)] of the ACTIVE I2 languages, or None if no lists are found.
    Reads at most _MASSIMO bytes (files that would exceed it are skipped) and, once the first list is found, only
    'dopo_la_prima' more files (the lists of the single scenes usually repeat the main one)."""
    attive, trovate, letti, dopo = {}, False, 0, None
    try:
        voci, chiudi = _file_asset(dati)
    except (OSError, ValueError):
        return None
    try:
        for _nome, dimensione, carica in voci:
            if dopo is not None:
                if dopo >= dopo_la_prima:
                    break
                dopo += 1
            if letti + dimensione > _MASSIMO:
                continue
            letti += dimensione
            byte = None
            try:
                byte = carica()
                liste = liste_i2(byte)
            except Exception:  # an unreadable file must not lose what was already found
                continue
            finally:
                if isinstance(byte, mmap.mmap):
                    byte.close()
                byte = None
            for lista in liste:
                if any(c for _, c, _ in lista):
                    trovate = True
                    for n, c, flag in lista:
                        if not flag & 1:
                            attive.setdefault(n, c)
            if trovate and dopo is None:
                dopo = 0
    finally:
        chiudi()
    return list(attive.items()) if trovate else None


def i2_personalizzato(dati):
    """True if the game modified I2 to save the language its own way (e.g. 'LastSelectedLanguage',
    'last_language.txt'): then the "I2 Language" preference is not enough and the language is chosen from the menu."""
    segni = [s for t in ("LastSelectedLanguage", "last_language.txt")
             for s in (t.encode("ascii"), t.encode("utf-16-le"))]
    file = []
    gestite = os.path.join(dati, "Managed")
    if os.path.isdir(gestite):
        file += [os.path.join(gestite, n) for n in os.listdir(gestite)
                 if n.lower().startswith("i2") and n.lower().endswith(".dll")]
    file.append(os.path.join(dati, "il2cpp_data", "Metadata", "global-metadata.dat"))
    for p in file:
        try:
            with open(p, "rb") as f:
                contenuto = f.read(64 * 1024 * 1024)
        except OSError:
            continue
        if any(s in contenuto for s in segni):
            return True
    return False


# --- Unity Localization ---------------------------------------------------------------------------

_LOCALE_TESTO = re.compile(rb"Locales/[^/\"\x00]{1,60} \(([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\)\.asset")
_LOCALE_BINARIO = re.compile(
    rb"(?:^|[/\x00-\x1f\"])[A-Za-z][^/\"\x00-\x1f]{0,60} \(([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)\)\.asset")


def lingue_unity_localization(dati):
    """{codice} of the Locales in the Addressables catalog, or None if the game does not use it."""
    aa = os.path.join(dati, "StreamingAssets", "aa")
    if not os.path.isdir(aa):
        return None
    codici = set()
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
        for m in _LOCALE_TESTO.finditer(b):
            codici.add(m.group(1).decode())
        # binary catalog (catalog.bin): the names are not necessarily under "Locales/"; they count only if the file
        # uses Unity Localization and each code also has its own "Locale-<codice>" label
        if f.endswith(".bin") and (b"Localization.Locale" in b or b"Localization/Locales" in b):
            for m in _LOCALE_BINARIO.finditer(b):
                if b"Locale-" + m.group(1) in b:
                    codici.add(m.group(1).decode())
    return codici or None


# --- PlayerPrefs (Windows registry) ---------------------------------------------------------------

def nome_pref(chiave):
    """Name of the registry value for a PlayerPrefs key: key + '_h' + djb2-xor hash of the UTF-8 bytes."""
    h = 5381
    for b in chiave.encode("utf-8"):
        h = ((h * 33) ^ b) & 0xFFFFFFFF
    return "%s_h%d" % (chiave, h)


def _chiave_registro(azienda, prodotto):
    return "Software\\%s\\%s" % (azienda, prodotto)


def leggi_pref(azienda, prodotto, chiave):
    """(tipo, byte) of the value, None if the value is missing, False if the game's key is missing."""
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _chiave_registro(azienda, prodotto), 0, winreg.KEY_READ) as k:
            try:
                valore, tipo = winreg.QueryValueEx(k, nome_pref(chiave))
            except FileNotFoundError:
                return None
    except FileNotFoundError:
        return False
    if tipo == winreg.REG_BINARY:
        return tipo, bytes(valore)
    if tipo == winreg.REG_SZ:
        return tipo, str(valore).encode("utf-8")
    return tipo, valore if isinstance(valore, (bytes, bytearray)) else str(valore).encode()


def scrivi_pref_stringa(azienda, prodotto, chiave, testo):
    """Writes a PlayerPrefs string (REG_BINARY UTF-8 + NUL) to an ALREADY existing key of the game."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _chiave_registro(azienda, prodotto), 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, nome_pref(chiave), 0, winreg.REG_BINARY, testo.encode("utf-8") + b"\x00")
    letto = leggi_pref(azienda, prodotto, chiave)
    if not letto or letto[1] != testo.encode("utf-8") + b"\x00":
        raise ErroreLinguaUnity("la preferenza scritta non si rilegge uguale")


def ripristina_pref(azienda, prodotto, chiave, precedente, scritto):
    """Puts back the previous value ((tipo, hex) or None = absent), only if the one we wrote is still there."""
    import winreg
    attuale = leggi_pref(azienda, prodotto, chiave)
    if not attuale or attuale[1] != scritto:
        return False
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _chiave_registro(azienda, prodotto), 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as k:
        if precedente is None:
            winreg.DeleteValue(k, nome_pref(chiave))
        else:
            tipo, byte = int(precedente[0]), bytes.fromhex(precedente[1])
            winreg.SetValueEx(k, nome_pref(chiave), 0, tipo, _valore_registro(tipo, byte))
    return True


def _valore_registro(tipo, byte):
    """The value in the form winreg wants for that type (leggi_pref always gives it as bytes)."""
    import winreg
    if tipo in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
        return byte.decode("utf-8")
    if tipo in (winreg.REG_DWORD, winreg.REG_QWORD):
        return int(byte.decode("ascii"))
    return byte


def voce_i2(voci, destinazione="it"):
    """The exact name of the I2 entry [(nome, codice)] for the language 'destinazione', or None: first by code
    (same code, then the language without region, then a variant; never the other Chinese script), then, for
    Italian without a suitable code, by name."""
    from .language import migliore
    scelto = migliore([c for _, c in voci if c], destinazione)
    if scelto is not None:
        return next(n for n, c in voci if c == scelto)
    if destinazione.split("-")[0].lower() == "it":
        return next((n for n, _ in voci if "itali" in (n or "").lower()), None)
    return None


def e_italiano(nome, codice, destinazione="it"):
    return voce_i2([(nome, codice)], destinazione) is not None
