"""Lingua ufficiale nei giochi Unreal Engine: quali lingue ha il gioco e come impostare quella scelta.

Rilevamento: le cartelle <Progetto>/Content/Localization/<Target>/<lingua>/ nei pak (solo i testi del gioco:
quelli del motore e dei plugin non contano), con il punto di montaggio del pak davanti ai nomi. Pak v3-9 con il
lettore di adapter_unreal; v10/v11 (UE 4.26+, UE5) dall'indice delle cartelle, se non e' cifrato.

Impostazione: Unreal legge la lingua all'avvio dalla riga di comando, poi da GameUserSettings.ini
(%LOCALAPPDATA%/<Progetto>/Saved/Config/<Piattaforma>/, a volte nella cartella del gioco), sezione
[Internationalization]. PatchSmith:
- cambia solo le chiavi gia' presenti tra Language/Locale/Culture, oppure aggiunge Culture (la meno prioritaria,
  cosi' una scelta successiva nel menu del gioco vince sempre);
- non scrive se il gioco ha una sua impostazione della lingua in altre sezioni (la gestisce lui);
- a gioco chiuso, con registro e ripristino riga per riga (solo se il valore e' ancora quello scritto), lasciando
  ogni riga con il suo a capo.
"""
import glob
import os
import re
import struct

from . import adapter_unreal

MAGIC_PAK = 0x5A6F12E1
CHIAVI = ("language", "locale", "culture")
_SEZ = re.compile(r"^\s*\[(.*)\]\s*$")
_KV = re.compile(r"^(\s*)([^=;\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")
# chiavi di lingua dei giochi stessi (es. LanguageSetting=Italian, mPrimaryLanguage): la lingua la gestisce il gioco
_LINGUA_DEL_GIOCO = re.compile(r"lang|cultur|locale|locali", re.I)
# ...ma non quelle che parlano d'altro (KeyboardCulture e' la tastiera, i filtri sono per le parolacce)
_NON_LINGUA_TESTO = re.compile(r"^(keyboard|input|ime)|filter", re.I)
_LOCALIZZAZIONE = re.compile(r"^([^/]+)/Content/Localization/([^/]+)/([^/]+)/\2\.locres$")


class ErroreLinguaUnreal(ValueError):
    pass


# --- quali lingue ha il gioco -------------------------------------------------------------------

def _fstring(d, p):
    n = struct.unpack_from("<i", d, p)[0]
    p += 4
    if n == 0:
        return "", p
    if n > 0:
        return d[p:p + n - 1].decode("latin-1"), p + n
    n = -n
    return d[p:p + 2 * n - 2].decode("utf-16-le", "replace"), p + 2 * n


def _nomi_pak_v10(percorso):
    """(punto di montaggio, nomi dei file) di un pak v10/v11 (indice delle cartelle), oppure None."""
    with open(percorso, "rb") as f:
        f.seek(0, 2)
        dim = f.tell()
        f.seek(max(0, dim - 1024))
        coda = f.read()
        i = coda.rfind(struct.pack("<I", MAGIC_PAK))
        if i < 0:
            return None
        versione, off, lung = struct.unpack_from("<Iqq", coda, i + 4)
        if versione < 10 or coda[i - 1] != 0 or not 0 <= off < dim or off + lung > dim:
            return None  # versione diversa o indice cifrato
        f.seek(off)
        indice = f.read(lung)
        mount, p = _fstring(indice, 0)
        p += 4 + 8  # numero di voci, seme dell'hash
        if struct.unpack_from("<I", indice, p)[0]:
            p += 4 + 8 + 8 + 20
        else:
            p += 4
        if not struct.unpack_from("<I", indice, p)[0]:
            return None
        p += 4
        fdi_off, fdi_lung = struct.unpack_from("<qq", indice, p)
        if not 0 <= fdi_off < dim or fdi_off + fdi_lung > dim:
            return None
        f.seek(fdi_off)
        fdi = f.read(fdi_lung)
    nomi, q = [], 4
    for _ in range(struct.unpack_from("<i", fdi, 0)[0]):
        cartella, q = _fstring(fdi, q)
        n = struct.unpack_from("<i", fdi, q)[0]
        q += 4
        for _ in range(n):
            nome, q = _fstring(fdi, q)
            q += 4
            nomi.append(cartella + nome)
    return mount, nomi


def _con_mount(mount, nomi):
    """Nomi completi: il punto di montaggio ('../../../Proj/Content/') senza '../' iniziali, piu' il nome."""
    m = (mount or "").replace("\\", "/")
    while m.startswith("../"):
        m = m[3:]
    m = m.lstrip("/")
    if m and not m.endswith("/"):
        m += "/"
    return [m + n.lstrip("/") for n in nomi]


def _nomi_pak(percorso):
    try:
        pak = adapter_unreal.Pak(percorso)
        return _con_mount(pak.mount, pak.voci)
    except (adapter_unreal.ErroreUnreal, OSError, struct.error, ValueError):
        pass
    try:
        letto = _nomi_pak_v10(percorso)
    except (OSError, struct.error, ValueError, IndexError):
        return None
    return _con_mount(*letto) if letto else None


def lingue_del_gioco(cartella):
    """({lingue}, progetto) dei testi del gioco (non del motore o dei plugin), oppure (None, progetto) se non si sa."""
    lingue, progetto, letti = set(), None, 0
    for percorso in adapter_unreal._paks(cartella):
        nomi = _nomi_pak(percorso)
        if nomi is None:
            continue
        letti += 1
        for nome in nomi:
            m = _LOCALIZZAZIONE.match(nome)
            if m and m.group(1) not in ("Engine",):
                lingue.add(m.group(3))
                progetto = progetto or m.group(1)
    # file sciolti (alcuni giochi non mettono la localizzazione nei pak)
    for p in glob.glob(os.path.join(glob.escape(cartella), "*", "Content", "Localization", "*", "*", "*.locres")):
        rel = os.path.relpath(p, cartella).replace(os.sep, "/")
        m = _LOCALIZZAZIONE.match(rel)
        if m and m.group(1) != "Engine":
            lingue.add(m.group(3))
            progetto = progetto or m.group(1)
            letti += 1
    if progetto is None:
        progetto = _progetto_da_cartelle(cartella)
    return (lingue if letti else None), progetto


def _progetto_da_cartelle(cartella):
    try:
        for nome in sorted(os.listdir(cartella)):
            if nome != "Engine" and os.path.isdir(os.path.join(cartella, nome, "Content", "Paks")):
                return nome
    except OSError:
        pass
    return None


def tag_italiano(lingue, destinazione="it"):
    """Il nome esatto della cartella della lingua (es. 'it' o 'it-IT'): Unreal non scende da 'it' a 'it-IT'.
    Per il cinese conta la scrittura: a chi vuole zh-TW non si da' mai zh-Hans."""
    from .language import migliore
    return migliore(sorted(lingue), destinazione)


# --- impostazione in GameUserSettings.ini -------------------------------------------------------

def file_impostazioni(progetto, cartella=None):
    """Il GameUserSettings.ini del giocatore per il progetto (il piu' recente), oppure None. Cerca solo dove
    Unreal lo mette: %LOCALAPPDATA%/<Progetto>, la cartella del gioco, Documenti/My Games/<Progetto>."""
    if not progetto:
        return None
    basi = []
    if os.environ.get("LOCALAPPDATA"):
        basi.append(os.path.join(os.environ["LOCALAPPDATA"], progetto))
    if cartella:
        basi.append(os.path.join(cartella, progetto))
    basi.append(os.path.join(os.path.expanduser("~"), "Documents", "My Games", progetto))
    candidati = []
    for b in basi:
        candidati += glob.glob(os.path.join(glob.escape(os.path.join(b, "Saved", "Config")), "*",
                                            "GameUserSettings.ini"))
    date = []
    for c in candidati:
        try:
            date.append((os.path.getmtime(c), c))
        except OSError:
            continue
    return max(date)[1] if date else None


def _leggi(percorso):
    with open(percorso, "rb") as f:
        grezzo = f.read()
    try:
        if grezzo[:2] == b"\xff\xfe":
            return grezzo[2:].decode("utf-16-le"), "utf-16-le", b"\xff\xfe"
        if grezzo[:2] == b"\xfe\xff":
            raise ErroreLinguaUnreal("file delle impostazioni in una codifica non prevista (UTF-16 BE)")
        if grezzo[:3] == b"\xef\xbb\xbf":
            return grezzo[3:].decode("utf-8"), "utf-8", b"\xef\xbb\xbf"
    except UnicodeDecodeError as e:
        raise ErroreLinguaUnreal("file delle impostazioni illeggibile (%s)" % e) from e
    return grezzo.decode("utf-8", "surrogateescape"), "utf-8", b""


def _scrivi(percorso, testo, codifica, bom):
    dati = bom + testo.encode(codifica, "surrogateescape" if codifica == "utf-8" else "strict")
    temporaneo = percorso + ".patchsmith-tmp"
    try:
        with open(temporaneo, "wb") as f:
            f.write(dati)
        os.replace(temporaneo, percorso)
    finally:
        if os.path.exists(temporaneo):
            os.remove(temporaneo)


def _righe(testo):
    """[[contenuto, a capo]]: ogni riga con il suo a capo ('\\r\\n', '\\n', '\\r' o '' per l'ultima)."""
    righe, pos = [], 0
    for m in re.finditer(r"\r\n|\r|\n", testo):
        righe.append([testo[pos:m.start()], m.group()])
        pos = m.end()
    if pos < len(testo):
        righe.append([testo[pos:], ""])
    return righe


def _unisci(righe):
    return "".join(c + a for c, a in righe)


def _a_capo(testo):
    return "\r\n" if "\r\n" in testo else "\r" if "\r" in testo and "\n" not in testo else "\n"


def _e_internazionalizzazione(sezione):
    return sezione is not None and sezione.lower() == "internationalization"


def leggi_internazionalizzazione(percorso):
    """({chiave minuscola: valore} di [Internationalization] (vale l'ultima), [chiavi di lingua del gioco in
    altre sezioni del gioco: /Script/... e /Game/...])."""
    testo, _, _ = _leggi(percorso)
    valori, altre, sezione = {}, [], None
    for contenuto, _ in _righe(testo):
        m = _SEZ.match(contenuto)
        if m:
            sezione = m.group(1).strip()
            continue
        kv = _KV.match(contenuto)
        if not kv or sezione is None:
            continue
        chiave = kv.group(2).strip()
        if _e_internazionalizzazione(sezione):
            valori[chiave.lower()] = kv.group(4)
        elif sezione.lower() != "internationalization.assetgroupcultures" and \
                sezione.startswith(("/Script/", "/Game/")) and _LINGUA_DEL_GIOCO.search(chiave) and \
                not _NON_LINGUA_TESTO.search(chiave):
            altre.append("[%s] %s" % (sezione, chiave))
    return valori, altre


def imposta_cultura(percorso, tag):
    """Imposta la lingua 'tag' in [Internationalization]. Restituisce le modifiche fatte (per il ripristino):
    ['riga', n, chiave, valore vecchio] per ogni riga di lingua cambiata (n = posizione tra le righe di lingua
    della sezione), ['aggiunta', 'Culture'] per la riga aggiunta, ['sezione', [righe aggiunte], a capo aggiunto]
    se la sezione non c'era. Lista vuota se era gia' tutto a posto."""
    if not os.access(percorso, os.W_OK):
        raise ErroreLinguaUnreal("il file delle impostazioni e' di sola lettura: non lo tocco")
    testo, codifica, bom = _leggi(percorso)
    a_capo = _a_capo(testo)
    righe = _righe(testo)
    sezione, inizio, fine, di_lingua = None, None, None, []
    for i, (contenuto, _) in enumerate(righe):
        m = _SEZ.match(contenuto)
        if m:
            if _e_internazionalizzazione(sezione) and fine is None:
                fine = i
            sezione = m.group(1).strip()
            if _e_internazionalizzazione(sezione) and inizio is None:
                inizio = i
            continue
        if _e_internazionalizzazione(sezione):
            kv = _KV.match(contenuto)
            if kv and kv.group(2).strip().lower() in CHIAVI:
                di_lingua.append((i, kv))
    if inizio is not None and fine is None:
        fine = len(righe)
    modifiche = []
    if di_lingua:
        for n, (i, kv) in enumerate(di_lingua):
            if kv.group(4) != tag:
                righe[i][0] = kv.group(1) + kv.group(2) + kv.group(3) + tag + kv.group(5)
                modifiche.append(["riga", n, kv.group(2).strip(), kv.group(4)])
    elif inizio is not None:
        dove = fine
        while dove > inizio + 1 and righe[dove - 1][0].strip() == "":
            dove -= 1
        if righe[dove - 1][1]:
            righe.insert(dove, ["Culture=" + tag, a_capo])
        else:  # ultima riga del file senza a capo: la nuova prende il suo posto in fondo
            righe[dove - 1][1] = a_capo
            righe.insert(dove, ["Culture=" + tag, ""])
        modifiche.append(["aggiunta", "Culture"])
    else:
        a_capo_aggiunto = bool(righe) and righe[-1][1] == ""
        if a_capo_aggiunto:
            righe[-1][1] = a_capo
        nuove = ([[""]] if righe and righe[-1][0].strip() else []) + [["[Internationalization]"], ["Culture=" + tag]]
        righe += [[c[0], a_capo] for c in nuove]
        modifiche += [["aggiunta", "Culture"], ["sezione", [c[0] for c in nuove], a_capo_aggiunto]]
    if modifiche:
        _scrivi(percorso, _unisci(righe), codifica, bom)
    return modifiche


def _da_registro_vecchio(modifiche):
    """Registro della 0.8.0 ([(chiave, vecchio)], ('[sezione]', testo)) nel formato attuale."""
    nuove = []
    for chiave, vecchio in modifiche:
        if chiave == "[sezione]":
            nuove.append(["sezione", [r for r in vecchio.split("\n") if r], False])
        elif vecchio is None:
            nuove.append(["aggiunta", chiave])
        else:
            nuove.append(["riga", None, chiave, vecchio])
    return nuove


def ripristina_cultura(percorso, modifiche, tag):
    """Rimette com'erano le righe cambiate, solo se valgono ancora 'tag'. Restituisce quante."""
    if not os.path.exists(percorso):
        return 0
    if not os.access(percorso, os.W_OK):
        raise ErroreLinguaUnreal("il file delle impostazioni e' di sola lettura")
    modifiche = [list(m) for m in modifiche]
    if modifiche and modifiche[0][0] not in ("riga", "aggiunta", "sezione"):
        modifiche = _da_registro_vecchio(modifiche)
    righe_cambiate = {}
    for m in modifiche:
        if m[0] == "riga":
            righe_cambiate.setdefault(m[1], []).append((m[2], m[3]))
    aggiunte = {m[1].lower() for m in modifiche if m[0] == "aggiunta"}
    sezione_aggiunta = next((m for m in modifiche if m[0] == "sezione"), None)

    testo, codifica, bom = _leggi(percorso)
    righe = _righe(testo)
    sezione, n, rifatte, da_togliere = None, -1, 0, []
    for i, (contenuto, _) in enumerate(righe):
        m = _SEZ.match(contenuto)
        if m:
            sezione = m.group(1).strip()
            continue
        if not _e_internazionalizzazione(sezione):
            continue
        kv = _KV.match(contenuto)
        if not kv or kv.group(2).strip().lower() not in CHIAVI:
            continue
        n += 1
        chiave = kv.group(2).strip()
        if kv.group(4) != tag:
            continue
        vecchio = next((v for k, v in righe_cambiate.get(n, []) + righe_cambiate.get(None, []) if k == chiave or
                        (n not in righe_cambiate and k.lower() == chiave.lower())), None)
        if vecchio is not None:
            righe[i][0] = kv.group(1) + kv.group(2) + kv.group(3) + vecchio + kv.group(5)
            rifatte += 1
        elif chiave.lower() in aggiunte:
            da_togliere.append(i)
            aggiunte.discard(chiave.lower())
            rifatte += 1
    for i in reversed(da_togliere):
        if righe[i][1] == "" and i > 0 and i == len(righe) - 1:
            righe[i - 1][1] = ""  # era l'ultima riga senza a capo: torna com'era
        del righe[i]
    # sezione aggiunta da PatchSmith e rimasta vuota: via, come era prima
    if da_togliere and sezione_aggiunta:
        coda = [r for r in sezione_aggiunta[1] if not r.lower().startswith("culture=")]
        if coda and [c for c, _ in righe[-len(coda):]] == coda:
            del righe[-len(coda):]
            if sezione_aggiunta[2] and righe:
                righe[-1][1] = ""
    if rifatte:
        _scrivi(percorso, _unisci(righe), codifica, bom)
    return rifatte
