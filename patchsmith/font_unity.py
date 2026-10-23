"""Characters that the TextMeshPro fonts of a Unity game can draw (read-only).

Unity Localization texts are written in the tables of the bundles (adapter_unity_loc): if the game's fonts lack
a letter (e.g. a "static" atlas made for English, without à è ì ò ù), a small square appears in its place in the
game. Before a translation is written, Progetto adapts the characters that no font has
(caratteri.adatta_caratteri: è -> e', as for GameMaker and XNA).

Where fonts are looked for: the serialized files of <gioco>_Data (resources.assets, sharedassets, level...),
data.unity3d and the bundles in StreamingAssets (Addressables). For each TextMeshPro font (TMP_FontAsset) these are
read:
- the character table (m_CharacterTable, m_Unicode; in old versions m_glyphInfoList, id) with the field tree
  (type tree, always in bundles). In files without a tree (those of _Data) the table is recognized by its shape:
  a number N and N entries of 16 bytes (type 1, code, glyph, scale 1.0);
- the atlas mode (m_AtlasPopulationMode): 0 static, 1 dynamic (characters are drawn on the fly from the font
  file, the .ttf), 2 dynamic from the Windows fonts.
For the game's .ttf/.otf fonts (Font objects with the file's data) the cmap table is read.

Rule (caratteri_ammessi): a character can be written if at least one font can draw it, because TextMeshPro looks for
missing characters in the fallback fonts. If there is a dynamic font, the characters of the game's .ttf files count
too; if it is not known (no TextMeshPro font, dynamic fonts without their .ttf, Windows fonts, reading not
finished) there is no limit: better a correct letter than a needless replacement.
"""
import hashlib
import json
import os
import re
import struct
import time

from . import interruption as interruzione, unityfs
from .adapter_webjson import scrivi_atomico

MAX_SECONDI = 180  # reading the fonts: beyond this, not known (no limit); the result is then kept (see cache)
_VERSIONE_CACHE = 1
MAX_MB_OGGETTO = 256  # a larger object is not read (it is not a font)
MIN_LETTERE = 20  # a character set with fewer ASCII letters is not credible as a font for the texts
_CLASSE_FONT = 128
_CLASSE_SCRIPT = 114  # MonoBehaviour (TextMeshPro fonts are ScriptableObjects)
_ESTENSIONI_SALTATE = (".ress", ".resource", ".json", ".txt", ".bank", ".bnk", ".wem", ".pck", ".mp4", ".webm",
                       ".ogg", ".wav", ".mp3", ".dll", ".exe", ".pdb", ".xml", ".csv", ".info", ".config", ".hash",
                       ".manifest", ".bin")
_STATICO, _DINAMICO, _DINAMICO_OS = 0, 1, 2
# entry of the TextMeshPro character table (without field tree): type 1 (character), code, glyph, scale
_VOCE_TMP = re.compile(rb"(?s)\x01\x00\x00\x00.{8}\x00\x00\x80\x3f")
_CACHE = {}  # (folder, fingerprint of the files) -> result of font_del_gioco


# --- .ttf / .otf files: cmap table -----------------------------------------------------------------------------

def caratteri_ttf(dati, inizio=0):
    """The characters (set of str) of the cmap table of a TrueType/OpenType font starting at 'inizio', or None."""
    try:
        etichetta = dati[inizio:inizio + 4]
        if etichetta == b"ttcf":  # font collection: the first one
            inizio = struct.unpack_from(">I", dati, inizio + 12)[0] + inizio
            etichetta = dati[inizio:inizio + 4]
        if etichetta not in (b"\x00\x01\x00\x00", b"OTTO", b"true"):
            return None
        ntab = struct.unpack_from(">H", dati, inizio + 4)[0]
        if not 1 <= ntab <= 100:
            return None
        cmap = None
        for k in range(ntab):
            tag, _, off, lung = struct.unpack_from(">4sIII", dati, inizio + 12 + 16 * k)
            if tag == b"cmap":
                cmap = inizio + off  # (in collections the offsets are from the start of the file)
        if cmap is None:
            return None
        sottotabelle = []
        for k in range(struct.unpack_from(">H", dati, cmap + 2)[0]):
            piattaforma, codifica, off = struct.unpack_from(">HHI", dati, cmap + 4 + 8 * k)
            formato = struct.unpack_from(">H", dati, cmap + off)[0]
            unicode = piattaforma == 0 or (piattaforma == 3 and codifica in (1, 10))
            if unicode and formato in (4, 12):
                sottotabelle.append((0 if formato == 12 else 1, cmap + off, formato))
        uscita = set()
        for _, p, formato in sorted(sottotabelle)[:1]:
            uscita = _cmap_12(dati, p) if formato == 12 else _cmap_4(dati, p)
        return {chr(c) for c in uscita if 0 < c < 0x110000 and not 0xD800 <= c <= 0xDFFF} or None
    except (struct.error, IndexError, ValueError):
        return None


def _cmap_4(dati, p):
    n = struct.unpack_from(">H", dati, p + 6)[0] // 2
    fine = struct.unpack_from(">%dH" % n, dati, p + 14)
    inizio = struct.unpack_from(">%dH" % n, dati, p + 16 + 2 * n)
    delta = struct.unpack_from(">%dh" % n, dati, p + 16 + 4 * n)
    p_offset = p + 16 + 6 * n
    offset = struct.unpack_from(">%dH" % n, dati, p_offset)
    codici = set()
    for i in range(n):
        if inizio[i] == 0xFFFF:
            continue
        for c in range(inizio[i], fine[i] + 1):
            if offset[i] == 0:
                glifo = (c + delta[i]) & 0xFFFF
            else:
                q = p_offset + 2 * i + offset[i] + 2 * (c - inizio[i])
                glifo = struct.unpack_from(">H", dati, q)[0]
                if glifo:
                    glifo = (glifo + delta[i]) & 0xFFFF
            if glifo:
                codici.add(c)
    return codici


def _cmap_12(dati, p):
    gruppi = struct.unpack_from(">I", dati, p + 12)[0]
    codici = set()
    for k in range(min(gruppi, 100000)):
        a, b, _ = struct.unpack_from(">III", dati, p + 16 + 12 * k)
        if b >= a and b - a < 0x110000:
            codici.update(range(a, b + 1))
    return codici


def _ttf_in(dati):
    """The characters of the first .ttf/.otf file inside the data of a Font object (m_FontData), or None."""
    for magia in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
        p = dati.find(magia)
        while p >= 0:
            trovati = caratteri_ttf(dati, p)
            if trovati:
                return trovati
            p = dati.find(magia, p + 1)
    return None


# --- TextMeshPro fonts -------------------------------------------------------------------------------------------

def _nome_oggetto(dati):
    """The name (m_Name) of a MonoBehaviour without field tree: after m_GameObject, m_Enabled and m_Script."""
    try:
        n = struct.unpack_from("<i", dati, 28)[0]
        if 0 <= n <= 200:
            return dati[32:32 + n].decode("utf-8")
    except (struct.error, UnicodeDecodeError):
        pass
    return ""


def tabella_tmp(dati):
    """The codes of the character table of a TextMeshPro font in data without field tree, or None: a number N
    followed by N entries of 16 bytes (type 1, code < 0x110000, glyph, scale 1.0), all codes different. The longest
    table of the object."""
    migliore = None
    visti = set()
    for m in _VOCE_TMP.finditer(dati):
        p = m.start()
        if p < 4 or p in visti:
            continue
        n = struct.unpack_from("<i", dati, p - 4)[0]
        if not 1 <= n <= 200000 or p + 16 * n > len(dati) or (migliore and n <= len(migliore)):
            continue
        codici = []
        for k in range(n):
            tipo, codice, glifo, scala = struct.unpack_from("<IIIf", dati, p + 16 * k)
            if tipo != 1 or codice >= 0x110000 or glifo > 10_000_000 or not 0 < scala < 1000:
                break
            codici.append(codice)
            visti.add(p + 16 * k)
        else:
            if len(set(codici)) == n:
                migliore = codici
    return migliore


def _font_da_albero(albero, dati):
    """(nome, modo, codici) of a TextMeshPro font read with the field tree."""
    uscita = []
    try:
        unityfs._leggi(albero, dati, 0, [], uscita, {"m_Unicode", "id", "m_AtlasPopulationMode"})
    except unityfs._Variabile:
        pass  # variable fields at the end: what was read up to there is valid
    except (struct.error, IndexError, ValueError):
        return None
    nome, modo, codici = "", None, []
    for percorso, a, b in uscita:
        if a == "valore":
            if percorso[-1] == "m_Unicode" and "m_CharacterTable" in percorso:
                codici.append(b)
            elif percorso[-1] == "id" and "m_glyphInfoList" in percorso:
                codici.append(b)
            elif percorso[-1] == "m_AtlasPopulationMode" and len(percorso) == 2:
                modo = b
        elif a is not None and percorso[-1] == "m_Name" and len(percorso) == 2:
            nome = dati[a + 4:b].decode("utf-8", "replace")
    return nome, modo, codici


def _e_font_tmp(albero):
    return albero is not None and any(f.nome in ("m_CharacterTable", "m_glyphInfoList") for f in albero.figli)


def _oggetti_utili(s):
    """[(oggetto, tipo)] of the objects to read in a serialized file: 'tmp' (TextMeshPro font with the tree),
    'tmp?' (MonoBehaviour without tree, to recognize by its shape), 'font' (.ttf/.otf font)."""
    uscita = []
    for o in s.oggetti:
        if not 0 <= o.tipo < len(s.tipi) or o.dimensione > MAX_MB_OGGETTO * 1024 * 1024:
            continue
        tipo = s.tipi[o.tipo]
        if tipo["classe"] == _CLASSE_FONT and o.dimensione > 128:  # (with the bytes of a .ttf file inside)
            uscita.append((o, "font"))
        elif tipo["classe"] == _CLASSE_SCRIPT:
            if tipo["albero"] is not None:
                if _e_font_tmp(tipo["albero"]):
                    uscita.append((o, "tmp"))
            elif o.dimensione >= 16 * MIN_LETTERE:  # a smaller one does not have the table of a text font
                uscita.append((o, "tmp?"))
    return uscita


def _estrai(pezzi, voluti):
    """The bytes of the wanted objects [(inizio, fine, chiave)] (in order, without overlaps) from a file read in
    consecutive pieces [(posizione, byte)]: {chiave: byte}. Only the object being read is kept in memory."""
    uscita, i, buffer, da = {}, 0, bytearray(), None
    for posizione, pezzo in pezzi:
        while i < len(voluti):
            a, b, chiave = voluti[i]
            fine_pezzo = posizione + len(pezzo)
            if a >= fine_pezzo:
                break
            if da is None:
                da = a
                buffer = bytearray()
            x, y = max(a, posizione) - posizione, min(b, fine_pezzo) - posizione
            if y > x:
                buffer += pezzo[x:y]
            if b <= fine_pezzo:
                uscita[chiave] = bytes(buffer)
                i, da, buffer = i + 1, None, bytearray()
            else:
                break
        if i >= len(voluti):
            break
    return uscita


class _Raccolta:
    def __init__(self, scadenza):
        self.tmp, self.ttf, self.scadenza, self.completa = [], [], scadenza, True

    def scaduta(self):
        if time.monotonic() > self.scadenza:
            self.completa = False
        return not self.completa

    def file_serializzato(self, leggi, dimensione, etichetta):
        """A serialized file read with leggi(inizio, fine) -> [(posizione, byte)] in pieces."""
        testa = b"".join(p for _, p in leggi(0, min(dimensione, 65536)))
        try:
            _ms, _fs, versione, inizio_dati = struct.unpack_from(">IIII", testa, 0)
            if versione >= 22:
                inizio_dati = struct.unpack_from(">q", testa, 32)[0]
        except struct.error:
            return
        if not 9 <= versione <= 22 or not 0 < inizio_dati <= min(dimensione, 64 * 1024 * 1024):
            return
        if inizio_dati > len(testa):
            testa = b"".join(p for _, p in leggi(0, inizio_dati))
        try:
            s = unityfs.FileSerializzato(testa)
        except (ValueError, IndexError, struct.error, KeyError):
            return
        utili = _oggetti_utili(s)
        if not utili:
            return
        voluti = sorted((s.inizio_dati + o.inizio, s.inizio_dati + o.inizio + o.dimensione, k)
                        for k, (o, _) in enumerate(utili))
        # overlapping objects (damaged file): only the first one
        puliti, fine = [], -1
        for a, b, k in voluti:
            if a >= fine and b <= dimensione:
                puliti.append((a, b, k))
                fine = b
        if not puliti:
            return
        letti = _estrai(leggi(puliti[0][0], puliti[-1][1]), puliti)
        for k, dati in letti.items():
            o, tipo = utili[k]
            if tipo == "font":
                caratteri = _ttf_in(dati)
                if caratteri:
                    self.ttf.append({"fonte": etichetta, "caratteri": caratteri})
            elif tipo == "tmp":
                letto = _font_da_albero(s.tipi[o.tipo]["albero"], dati)
                if letto:
                    nome, modo, codici = letto
                    self.tmp.append({"nome": nome, "modo": modo, "fonte": etichetta,
                                     "caratteri": {chr(c) for c in codici if 0 < c < 0x110000}})
            else:
                codici = tabella_tmp(dati)
                if codici:
                    self.tmp.append({"nome": _nome_oggetto(dati), "modo": None, "fonte": etichetta,
                                     "caratteri": {chr(c) for c in codici if 0 < c < 0x110000}})

    def bundle(self, percorso, etichetta):
        try:
            with unityfs.Indice(percorso) as indice:
                for nome, inizio, dimensione, _ in indice.nodi:
                    if self.scaduta():
                        return
                    interruzione.controlla()
                    if nome.lower().endswith((".ress", ".resource")):
                        continue

                    def leggi(a, b, inizio=inizio):
                        return ((p - inizio, x) for p, x in indice.pezzi(inizio + a, inizio + b))
                    self.file_serializzato(leggi, dimensione, "%s/%s" % (etichetta, nome))
        except (OSError, ValueError, MemoryError):
            return

    def file(self, percorso, etichetta):
        try:
            dimensione = os.path.getsize(percorso)
            with open(percorso, "rb") as f:
                def leggi(a, b):
                    f.seek(a)
                    p = a
                    while p < b:
                        pezzo = f.read(min(16 * 1024 * 1024, b - p))
                        if not pezzo:
                            return
                        yield p, pezzo
                        p += len(pezzo)
                self.file_serializzato(leggi, dimensione, etichetta)
        except (OSError, ValueError, MemoryError):
            return


def _da_saltare(nome, nomi):
    """Files that are not read: images, sounds, texts..., PatchSmith's files and those that PatchSmith rewrote
    (the translated text tables, with their .patchsmith-originale next to them: they change at every Reapply)."""
    basso = nome.lower()
    return basso.endswith(_ESTENSIONI_SALTATE) or ".patchsmith-" in nome or nome + ".patchsmith-originale" in nomi


def _file_da_leggere(cartella_dati):
    """[(percorso, 'bundle'|'file', dimensione, data)] of the game files where fonts can be: the files of
    <gioco>_Data (not Unity's Resources folder: the engine's fonts), data.unity3d, the bundles of StreamingAssets."""
    uscita = []
    try:
        voci = sorted(os.scandir(cartella_dati), key=lambda e: e.name.lower())
    except OSError:
        return uscita
    nomi = {e.name for e in voci}
    for e in voci:
        try:
            if not e.is_file() or _da_saltare(e.name, nomi):
                continue
            st = e.stat()
            uscita.append((e.path, "bundle" if e.name.lower() == "data.unity3d" else "file", st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    pila = [os.path.join(cartella_dati, "StreamingAssets")]
    while pila:
        d = pila.pop()
        try:
            voci = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError:
            continue
        nomi = {e.name for e in voci}
        for e in voci:
            try:
                if e.is_dir(follow_symlinks=False):
                    pila.append(e.path)
                elif not _da_saltare(e.name, nomi):
                    st = e.stat()
                    uscita.append((e.path, "bundle", st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    return uscita


def impronta(cartella_dati, elenco=None):
    """The fingerprint of the files where fonts can be (names, sizes, dates): if it does not change, the fonts are
    the same."""
    elenco = _file_da_leggere(cartella_dati) if elenco is None else elenco
    h = hashlib.sha1()
    for percorso, _, dimensione, data in sorted(elenco):
        h.update(("%s|%d|%d\n" % (os.path.relpath(percorso, cartella_dati).lower(), dimensione, data)).encode("utf-8"))
    return h.hexdigest()


def _e_bundle(percorso):
    try:
        with open(percorso, "rb") as f:
            return f.read(8) == b"UnityFS\x00"
    except OSError:
        return False


def font_del_gioco(cartella_dati, log=None, secondi=MAX_SECONDI, elenco=None):
    """{'tmp': [{nome, modo, fonte, caratteri}], 'ttf': [{fonte, caratteri}], 'completa': bool} of the game's fonts
    (read-only). The result stays in memory until the game's files change."""
    elenco = list(_file_da_leggere(cartella_dati) if elenco is None else elenco)
    chiave = (os.path.normcase(os.path.abspath(cartella_dati)),
              tuple((os.path.normcase(p), d, t) for p, _, d, t in elenco))
    if chiave in _CACHE:
        return _CACHE[chiave]
    raccolta = _Raccolta(time.monotonic() + secondi)
    # first the _Data files and the bundles with "font" or "tmp" in their name: if time runs out, the fonts are
    # usually already read
    elenco.sort(key=lambda x: (0 if x[1] == "file" or re.search(r"(?i)font|tmp|textmesh", os.path.basename(x[0]))
                               else 1))
    for percorso, tipo, _, _ in elenco:
        if raccolta.scaduta():
            break
        interruzione.controlla()
        etichetta = os.path.relpath(percorso, cartella_dati).replace(os.sep, "/")
        if _e_bundle(percorso):
            raccolta.bundle(percorso, etichetta)
        elif tipo == "file":
            raccolta.file(percorso, etichetta)
    risultato = {"tmp": raccolta.tmp, "ttf": raccolta.ttf, "completa": raccolta.completa}
    if raccolta.completa:
        _CACHE[chiave] = risultato
    elif log:
        log("  ! Lettura dei font del gioco non finita in %d secondi: non limito i caratteri" % secondi)
    return risultato


def _lettere(caratteri):
    return sum(1 for c in caratteri if c.isascii() and c.isalpha())


def intervalli(caratteri):
    """[[primo, ultimo], ...] of the character codes (to save them in little space: CJK fonts have tens of
    thousands of them, almost all consecutive)."""
    uscita = []
    for c in sorted(ord(x) for x in caratteri):
        if uscita and c == uscita[-1][1] + 1:
            uscita[-1][1] = c
        else:
            uscita.append([c, c])
    return uscita


def da_intervalli(elenco):
    return {chr(c) for a, b in elenco for c in range(a, b + 1) if 0 < c < 0x110000 and not 0xD800 <= c <= 0xDFFF}


def _leggi_cache(percorso, chiave):
    try:
        with open(percorso, encoding="utf-8") as f:
            dati = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(dati, dict) or dati.get("impronta") != chiave or dati.get("versione") != _VERSIONE_CACHE:
        return None
    try:
        return (da_intervalli(dati["intervalli"]) if dati.get("intervalli") is not None else None), \
            str(dati.get("motivo") or "")
    except (TypeError, ValueError, KeyError):
        return None


def caratteri_ammessi(cartella_dati, log=None, secondi=MAX_SECONDI, cache=None):
    """(set of the characters that at least one of the game's fonts can draw, descrizione) or (None, motivo) if it is
    not known: see the rule at the start of the file. cache: a .json file where the result is kept until the game's
    files change (a game with thousands of bundles can take a few minutes). A reading not finished in time is kept
    too (None: no limit, as before), so as not to redo it at every "Reapply"."""
    elenco = _file_da_leggere(cartella_dati)
    chiave = impronta(cartella_dati, elenco)
    if cache:
        letto = _leggi_cache(cache, chiave)
        if letto is not None:
            return letto
    if log and len(elenco) > 100:
        log("  Controllo quali lettere sanno disegnare i font del gioco (%d file, sola lettura; solo la prima volta, "
            "puo' richiedere qualche minuto)..." % len(elenco))
    trovati = font_del_gioco(cartella_dati, log, secondi, elenco)
    risultato = _regola(trovati) if trovati["completa"] else (None, "lettura dei font non finita in tempo")
    if cache:
        try:
            os.makedirs(os.path.dirname(cache), exist_ok=True)
            scrivi_atomico(cache, json.dumps({
                "versione": _VERSIONE_CACHE, "impronta": chiave, "motivo": risultato[1],
                "intervalli": intervalli(risultato[0]) if risultato[0] is not None else None},
                separators=(",", ":")).encode("utf-8"))
        except OSError:
            pass  # it is only a shortcut: next time it is read again
    return risultato


def _regola(trovati):
    """(caratteri, descrizione) or (None, motivo) from the fonts found (see the rule at the start of the file)."""
    tmp = trovati["tmp"]
    if not tmp:
        return None, "nessun font TextMeshPro"
    if any(f["modo"] == _DINAMICO_OS for f in tmp):
        return None, "un font usa i font di Windows"
    ammessi = set()
    dinamici = []
    for f in tmp:
        ammessi |= f["caratteri"]
        # dynamic, or without field tree and almost empty (a dynamic one emptied when the game was built)
        if f["modo"] == _DINAMICO or (f["modo"] is None and _lettere(f["caratteri"]) < MIN_LETTERE):
            dinamici.append(f)
    if dinamici:
        if not trovati["ttf"]:
            return None, "font dinamici senza i loro file .ttf"
        for t in trovati["ttf"]:
            ammessi |= t["caratteri"]
    if _lettere(ammessi) < MIN_LETTERE:
        return None, "font senza le lettere dei testi"
    descrizione = "%d font TextMeshPro%s" % (len(tmp), (" (%d dinamici, con %d file .ttf)" % (
        len(dinamici), len(trovati["ttf"]))) if dinamici else "")
    return ammessi, descrizione


def dati_del_bundle(cartella_gioco, rel_bundle):
    """The <gioco>_Data folder of a bundle (path relative to the game folder), or None."""
    parti = rel_bundle.replace("\\", "/").split("/")
    for k, p in enumerate(parti):
        if p.endswith("_Data"):
            return os.path.join(cartella_gioco, *parti[:k + 1])
    return None
