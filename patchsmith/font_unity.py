"""Caratteri che i font TextMeshPro di un gioco Unity sanno disegnare (sola lettura).

I testi di Unity Localization si scrivono nelle tabelle dei bundle (adapter_unity_loc): se i font del gioco non hanno
una lettera (es. un atlante "statico" fatto per l'inglese, senza à è ì ò ù), nel gioco al suo posto compare un
quadratino. Prima di scrivere una traduzione, Progetto adatta i caratteri che nessun font ha (caratteri.adatta_caratteri:
è -> e', come per GameMaker e XNA).

Dove si cercano i font: i file serializzati di <gioco>_Data (resources.assets, sharedassets, level...), data.unity3d e
i bundle in StreamingAssets (Addressables). Di ogni font TextMeshPro (TMP_FontAsset) si leggono:
- la tabella dei caratteri (m_CharacterTable, m_Unicode; nelle versioni vecchie m_glyphInfoList, id) con l'albero dei
  campi (type tree, sempre nei bundle). Nei file senza albero (quelli di _Data) la tabella si riconosce dalla forma:
  un numero N e N voci da 16 byte (tipo 1, codice, glifo, scala 1.0);
- il modo dell'atlante (m_AtlasPopulationMode): 0 statico, 1 dinamico (i caratteri si disegnano al momento dal file
  del font, il .ttf), 2 dinamico dai font di Windows.
Dei font .ttf/.otf del gioco (oggetti Font con i dati del file) si legge la tabella cmap.

Regola (caratteri_ammessi): un carattere si puo' scrivere se almeno un font lo sa disegnare, perche' TextMeshPro cerca
i caratteri mancanti nei font di ripiego (fallback). Se c'e' un font dinamico contano anche i caratteri dei file .ttf
del gioco; se non si sa (nessun font TextMeshPro, font dinamici senza il loro .ttf, font di Windows, lettura non
finita) non c'e' nessun limite: meglio una lettera giusta che una sostituzione inutile.
"""
import hashlib
import json
import os
import re
import struct
import time

from . import interruption as interruzione, unityfs
from .adapter_webjson import scrivi_atomico

MAX_SECONDI = 180  # lettura dei font: oltre, non si sa (nessun limite); il risultato poi si tiene (vedi cache)
_VERSIONE_CACHE = 1
MAX_MB_OGGETTO = 256  # un oggetto piu' grande non si legge (non e' un font)
MIN_LETTERE = 20  # un insieme di caratteri con meno lettere ASCII non e' credibile come font dei testi
_CLASSE_FONT = 128
_CLASSE_SCRIPT = 114  # MonoBehaviour (i font TextMeshPro sono ScriptableObject)
_ESTENSIONI_SALTATE = (".ress", ".resource", ".json", ".txt", ".bank", ".bnk", ".wem", ".pck", ".mp4", ".webm",
                       ".ogg", ".wav", ".mp3", ".dll", ".exe", ".pdb", ".xml", ".csv", ".info", ".config", ".hash",
                       ".manifest", ".bin")
_STATICO, _DINAMICO, _DINAMICO_OS = 0, 1, 2
# voce della tabella dei caratteri di TextMeshPro (senza albero dei campi): tipo 1 (carattere), codice, glifo, scala
_VOCE_TMP = re.compile(rb"(?s)\x01\x00\x00\x00.{8}\x00\x00\x80\x3f")
_CACHE = {}  # (cartella, impronta dei file) -> risultato di font_del_gioco


# --- file .ttf / .otf: tabella cmap ----------------------------------------------------------------------------

def caratteri_ttf(dati, inizio=0):
    """I caratteri (set di str) della tabella cmap di un font TrueType/OpenType che inizia a 'inizio', o None."""
    try:
        etichetta = dati[inizio:inizio + 4]
        if etichetta == b"ttcf":  # raccolta di font: il primo
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
                cmap = inizio + off  # (nelle raccolte gli spostamenti sono dall'inizio del file)
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
    """I caratteri del primo file .ttf/.otf dentro i dati di un oggetto Font (m_FontData), o None."""
    for magia in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"ttcf"):
        p = dati.find(magia)
        while p >= 0:
            trovati = caratteri_ttf(dati, p)
            if trovati:
                return trovati
            p = dati.find(magia, p + 1)
    return None


# --- font TextMeshPro --------------------------------------------------------------------------------------------

def _nome_oggetto(dati):
    """Il nome (m_Name) di un MonoBehaviour senza albero dei campi: dopo m_GameObject, m_Enabled e m_Script."""
    try:
        n = struct.unpack_from("<i", dati, 28)[0]
        if 0 <= n <= 200:
            return dati[32:32 + n].decode("utf-8")
    except (struct.error, UnicodeDecodeError):
        pass
    return ""


def tabella_tmp(dati):
    """I codici della tabella dei caratteri di un font TextMeshPro in dati senza albero dei campi, o None: un numero
    N seguito da N voci da 16 byte (tipo 1, codice < 0x110000, glifo, scala 1.0), codici tutti diversi. La tabella piu'
    lunga dell'oggetto."""
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
    """(nome, modo, codici) di un font TextMeshPro letto con l'albero dei campi."""
    uscita = []
    try:
        unityfs._leggi(albero, dati, 0, [], uscita, {"m_Unicode", "id", "m_AtlasPopulationMode"})
    except unityfs._Variabile:
        pass  # campi variabili in fondo: quello letto fin li' vale
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
    """[(oggetto, tipo)] degli oggetti da leggere di un file serializzato: 'tmp' (font TextMeshPro con l'albero),
    'tmp?' (MonoBehaviour senza albero, da riconoscere dalla forma), 'font' (font .ttf/.otf)."""
    uscita = []
    for o in s.oggetti:
        if not 0 <= o.tipo < len(s.tipi) or o.dimensione > MAX_MB_OGGETTO * 1024 * 1024:
            continue
        tipo = s.tipi[o.tipo]
        if tipo["classe"] == _CLASSE_FONT and o.dimensione > 128:  # (con i byte di un file .ttf dentro)
            uscita.append((o, "font"))
        elif tipo["classe"] == _CLASSE_SCRIPT:
            if tipo["albero"] is not None:
                if _e_font_tmp(tipo["albero"]):
                    uscita.append((o, "tmp"))
            elif o.dimensione >= 16 * MIN_LETTERE:  # piu' piccolo non ha la tabella di un font dei testi
                uscita.append((o, "tmp?"))
    return uscita


def _estrai(pezzi, voluti):
    """I byte degli oggetti voluti [(inizio, fine, chiave)] (in ordine, senza sovrapposizioni) da un file letto a
    pezzi consecutivi [(posizione, byte)]: {chiave: byte}. Si tiene in memoria solo l'oggetto in lettura."""
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
        """Un file serializzato letto con leggi(inizio, fine) -> [(posizione, byte)] a pezzi."""
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
        # oggetti sovrapposti (file rovinato): solo il primo
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
    """File che non si leggono: immagini, suoni, testi..., i file di PatchSmith e quelli che PatchSmith ha riscritto
    (le tabelle dei testi tradotte, con il loro .patchsmith-originale accanto: cambiano a ogni Riapplica)."""
    basso = nome.lower()
    return basso.endswith(_ESTENSIONI_SALTATE) or ".patchsmith-" in nome or nome + ".patchsmith-originale" in nomi


def _file_da_leggere(cartella_dati):
    """[(percorso, 'bundle'|'file', dimensione, data)] dei file del gioco dove possono stare i font: i file di
    <gioco>_Data (non la cartella Resources di Unity: i font del motore), data.unity3d, i bundle di StreamingAssets."""
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
    """L'impronta dei file dove possono stare i font (nomi, dimensioni, date): se non cambia, i font sono gli stessi."""
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
    """{'tmp': [{nome, modo, fonte, caratteri}], 'ttf': [{fonte, caratteri}], 'completa': bool} dei font del gioco
    (sola lettura). Il risultato resta in memoria finche' i file del gioco non cambiano."""
    elenco = list(_file_da_leggere(cartella_dati) if elenco is None else elenco)
    chiave = (os.path.normcase(os.path.abspath(cartella_dati)),
              tuple((os.path.normcase(p), d, t) for p, _, d, t in elenco))
    if chiave in _CACHE:
        return _CACHE[chiave]
    raccolta = _Raccolta(time.monotonic() + secondi)
    # prima i file di _Data e i bundle con "font" o "tmp" nel nome: se il tempo finisce, i font di solito sono letti
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
    """[[primo, ultimo], ...] dei codici dei caratteri (per salvarli in poco spazio: i font CJK ne hanno decine di
    migliaia, quasi tutti di seguito)."""
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
    """(insieme dei caratteri che almeno un font del gioco sa disegnare, descrizione) oppure (None, motivo) se non si
    sa: vedi la regola all'inizio del file. cache: un file .json dove tenere il risultato finche' i file del gioco non
    cambiano (un gioco con migliaia di bundle richiede anche qualche minuto). Anche una lettura non finita in tempo
    resta (None: nessun limite, come prima), per non rifarla a ogni "Riapplica"."""
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
            pass  # e' solo una scorciatoia: la prossima volta si rilegge
    return risultato


def _regola(trovati):
    """(caratteri, descrizione) o (None, motivo) dai font trovati (vedi la regola all'inizio del file)."""
    tmp = trovati["tmp"]
    if not tmp:
        return None, "nessun font TextMeshPro"
    if any(f["modo"] == _DINAMICO_OS for f in tmp):
        return None, "un font usa i font di Windows"
    ammessi = set()
    dinamici = []
    for f in tmp:
        ammessi |= f["caratteri"]
        # dinamico, o senza albero dei campi e quasi vuoto (un dinamico svuotato alla creazione del gioco)
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
    """La cartella <gioco>_Data di un bundle (percorso relativo alla cartella del gioco), o None."""
    parti = rel_bundle.replace("\\", "/").split("/")
    for k, p in enumerate(parti):
        if p.endswith("_Data"):
            return os.path.join(cartella_gioco, *parti[:k + 1])
    return None
