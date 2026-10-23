"""Adapter Unreal Engine 4 (archivi .pak classici): traduce i file di localizzazione .locres.

1. legge (in sola lettura) i .pak del gioco e trova i .locres della lingua originale,
   per esempio DDS/Content/Localization/Game/en/Game.locres (la lingua viene dal file .locmeta);
2. traduce le frasi (memoria + Google), proteggendo segnaposto {x}, tag <Stile>...</> e separatori;
3. scrive un NUOVO pak di patch (Content/Paks/zz_Patchsmith_<lingua>_999_P.pak) con i .locres tradotti allo
   stesso percorso: Unreal lo carica con priorita' piu' alta e mostra la traduzione ("_P" = patch,
   "_999" = numero di patch alto, cosi' vince anche sulle patch ufficiali "_1_P", "_2_P"...).
I pak originali non vengono mai modificati: per tornare indietro basta togliere il pak aggiunto.

Limiti attuali: pak versione 3-9 (UE 4.x) con indice non cifrato e file non compressi o zlib;
nel gioco deve essere selezionata la lingua originale (di solito l'inglese).
"""
import datetime
import hashlib
import json
import os
import re
import struct
import unicodedata
import zlib

from . import __version__, language as lingua, detection as rilevamento
from .adapter_webjson import SUFFISSO_TMP, scrivi_atomico
from .system import gioco_aperto

MAGIC_PAK = 0x5A6F12E1
MAGIC_LOCRES = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")
MAGIC_LOCMETA = bytes.fromhex("4fee4ca1684855836c4c46bd70da507c")
MANIFESTO = "patchsmith-unreal.json"
PREFISSO_PAK = "zz_Patchsmith_"


def nome_pak(lingua_dest):
    return "%s%s_999_P.pak" % (PREFISSO_PAK, lingua_dest)


class ErroreUnreal(Exception):
    def __init__(self, messaggio="", codice=None):
        super().__init__(messaggio)
        self.codice = codice  # esito per l'interfaccia (vedi outcome.py), es. GIOCO_APERTO


# --- FString ------------------------------------------------------------------------------------

def _leggi_fstring(dati, pos):
    n = struct.unpack_from("<i", dati, pos)[0]
    pos += 4
    if n == 0:
        return "", pos
    if n > 0:
        return dati[pos:pos + n - 1].decode("latin-1"), pos + n
    n = -n
    return dati[pos:pos + 2 * n - 2].decode("utf-16-le", "surrogatepass"), pos + 2 * n


def _scrivi_fstring(s):
    if not s:
        return struct.pack("<i", 0)
    if all(ord(c) < 128 for c in s):
        return struct.pack("<i", len(s) + 1) + s.encode("ascii") + b"\x00"
    codificato = s.encode("utf-16-le", "surrogatepass")
    return struct.pack("<i", -(len(codificato) // 2 + 1)) + codificato + b"\x00\x00"


# --- archivi .pak -------------------------------------------------------------------------------

def nome_pieno(mount, nome):
    """Il nome completo di un file del pak: il punto di montaggio senza i '../' iniziali, piu' il nome. UnrealPak usa
    come punto di montaggio la cartella comune a tutti i file del pak: '../../../' per i pak principali (i nomi sono
    gia' completi, 'Proj/Content/...'), una cartella piu' profonda per i pak piccoli (Remnant: '../../../Remnant/
    Content/', con i nomi 'Localization/Game/en/Game.locres')."""
    m = (mount or "").replace("\\", "/")
    while m.startswith("../"):
        m = m[3:]
    m = m.lstrip("/")
    if m and not m.endswith("/"):
        m += "/"
    return m + nome.lstrip("/")


class VocePak:
    def __init__(self, nome, offset, dimensione, dimensione_orig, metodo, blocchi, flags, dim_blocco, sha1, serie):
        self.nome, self.offset, self.dimensione, self.dimensione_orig = nome, offset, dimensione, dimensione_orig
        self.metodo, self.blocchi, self.flags, self.dim_blocco, self.sha1 = metodo, blocchi, flags, dim_blocco, sha1
        self.lunghezza_intestazione = len(serie)


class Pak:
    """Lettore di pak UE4 versione 3-9 (indice 'legacy')."""

    def __init__(self, percorso):
        self.percorso = percorso
        dimensione = os.path.getsize(percorso)
        with open(percorso, "rb") as f:
            f.seek(max(0, dimensione - 1024))
            coda = f.read()
        i = coda.rfind(struct.pack("<I", MAGIC_PAK))
        if i < 0:
            raise ErroreUnreal("%s non e' un pak Unreal valido" % os.path.basename(percorso))
        self.versione, self.indice_offset, self.indice_dim = struct.unpack_from("<Iqq", coda, i + 4)
        if not 3 <= self.versione <= 9:
            raise ErroreUnreal("Pak versione %d: non ancora supportata (supportate 3-9, Unreal 4.x)" % self.versione)
        self.indice_cifrato = self.versione >= 4 and coda[i - 1] != 0
        self.guid_chiave = coda[i - 17:i - 1] if self.versione >= 7 else b"\x00" * 16
        if self.indice_cifrato:
            raise ErroreUnreal("L'indice del pak e' cifrato (serve la chiave AES del gioco): non supportato")
        resto = coda[i + 4 + 4 + 8 + 8 + 20:]
        self.congelato = resto[0] if self.versione >= 9 else None
        nomi = resto[1:] if self.versione >= 9 else resto
        self.posti_compressione = len(nomi) // 32 if self.versione >= 8 else 0
        self.metodi = [nomi[k * 32:(k + 1) * 32].split(b"\x00")[0].decode("ascii", "replace")
                       for k in range(self.posti_compressione)]
        with open(percorso, "rb") as f:
            f.seek(self.indice_offset)
            indice = f.read(self.indice_dim)
        for larghezza in ((4, 1) if self.versione == 8 else (4,)):  # 4.22 usava 1 byte per il metodo
            try:
                self.mount, self.voci = self._leggi_indice(indice, larghezza)
                self.larghezza_metodo = larghezza
                break
            except (struct.error, ErroreUnreal, UnicodeDecodeError):
                continue
        else:
            raise ErroreUnreal("Indice del pak non leggibile")
        self.prefisso = nome_pieno(self.mount, "")

    def _leggi_indice(self, indice, larghezza):
        mount, pos = _leggi_fstring(indice, 0)
        n = struct.unpack_from("<i", indice, pos)[0]
        pos += 4
        voci = {}
        for _ in range(n):
            nome, pos = _leggi_fstring(indice, pos)
            inizio = pos
            offset, dim, dim_orig = struct.unpack_from("<qqq", indice, pos)
            pos += 24
            metodo = struct.unpack_from("<I" if larghezza == 4 else "<B", indice, pos)[0]
            pos += larghezza
            sha1 = indice[pos:pos + 20]
            pos += 20
            blocchi = []
            if metodo:
                nb = struct.unpack_from("<i", indice, pos)[0]
                pos += 4
                for _ in range(nb):
                    blocchi.append(struct.unpack_from("<qq", indice, pos))
                    pos += 16
            flags = indice[pos]
            dim_blocco = struct.unpack_from("<I", indice, pos + 1)[0]
            pos += 5
            voci[nome] = VocePak(nome, offset, dim, dim_orig, metodo, blocchi, flags, dim_blocco, sha1, indice[inizio:pos])
        if pos != len(indice):
            raise ErroreUnreal("indice non coerente")
        return mount, voci

    def nome_metodo(self, voce):
        if not voce.metodo:
            return None
        if self.versione >= 8:
            return self.metodi[voce.metodo - 1] if voce.metodo - 1 < len(self.metodi) else "?"
        return {1: "Zlib", 2: "Gzip"}.get(voce.metodo, "Custom")

    def leggi(self, nome):
        """I dati di un file del pak, dal nome nel pak o dal nome completo (vedi nome_pieno)."""
        voce = self.voci.get(nome)
        if voce is None and self.prefisso and nome.startswith(self.prefisso):
            voce = self.voci.get(nome[len(self.prefisso):])
        if voce is None:
            raise KeyError(nome)
        if voce.flags & 1:
            raise ErroreUnreal("%s e' cifrato: non supportato" % nome)
        with open(self.percorso, "rb") as f:
            if not voce.metodo:
                f.seek(voce.offset + voce.lunghezza_intestazione)
                dati = f.read(voce.dimensione)
            else:
                if self.nome_metodo(voce).lower() != "zlib":
                    raise ErroreUnreal("%s e' compresso con %s: non supportato" % (nome, self.nome_metodo(voce)))
                base = voce.offset if self.versione >= 5 else 0  # blocchi relativi dalla versione 5
                parti = []
                for inizio, fine in voce.blocchi:
                    f.seek(base + inizio)
                    parti.append(zlib.decompress(f.read(fine - inizio)))
                dati = b"".join(parti)
        if not voce.metodo and hashlib.sha1(dati).digest() != voce.sha1:
            raise ErroreUnreal("%s: impronta diversa da quella dell'indice" % nome)
        return dati


def _serie_voce(versione, offset, dati, larghezza_metodo=4):
    """FPakEntry di un file non compresso (versione 3-9)."""
    parti = struct.pack("<qqq", offset, len(dati), len(dati))
    parti += struct.pack("<B", 0) if larghezza_metodo == 1 else struct.pack("<i", 0)
    parti += hashlib.sha1(dati).digest()
    parti += struct.pack("<BI", 0, 0)  # flags (non cifrato), dimensione blocco
    return parti


def scrivi_pak(percorso, modello, file, mount=None):
    """Scrive un pak non compresso con lo stesso formato del pak 'modello'. file: [(nome, dati)]. mount: il punto di
    montaggio (di solito quello del modello; '../../../' con i nomi completi)."""
    v, larghezza = modello.versione, getattr(modello, "larghezza_metodo", 4)
    mount = modello.mount if mount is None else mount
    corpo = bytearray()
    voci = []
    for nome, dati in file:
        offset = len(corpo)
        corpo += _serie_voce(v, 0, dati, larghezza)  # l'intestazione davanti ai dati ha offset 0 (come UnrealPak)
        corpo += dati
        voci.append((nome, offset, dati))
    indice = _scrivi_fstring(mount) + struct.pack("<i", len(voci))
    for nome, offset, dati in voci:
        indice += _scrivi_fstring(nome) + _serie_voce(v, offset, dati, larghezza)
    coda = b""
    if v >= 7:
        coda += b"\x00" * 16  # nessuna chiave di cifratura
    if v >= 4:
        coda += b"\x00"  # indice non cifrato
    coda += struct.pack("<IIqq", MAGIC_PAK, v, len(corpo), len(indice)) + hashlib.sha1(indice).digest()
    if v >= 9:
        coda += b"\x00"  # indice non congelato
    if v >= 8:
        coda += b"\x00" * 32 * modello.posti_compressione
    with open(percorso, "wb") as f:
        f.write(bytes(corpo) + indice + coda)


# --- .locres / .locmeta -------------------------------------------------------------------------

def leggi_locres(dati):
    """Struttura di un .locres (versioni 1-3). Le stringhe tradotte sono in fondo al file."""
    if dati[:16] != MAGIC_LOCRES:
        raise ErroreUnreal("Formato .locres 'legacy' (senza intestazione): non ancora supportato")
    versione = dati[16]
    if not 1 <= versione <= 3:
        raise ErroreUnreal("Versione .locres %d non supportata" % versione)
    pos = 17
    offset_stringhe = struct.unpack_from("<q", dati, pos)[0]
    pos += 8
    if versione >= 2:
        pos += 4  # numero di voci
    nns = struct.unpack_from("<I", dati, pos)[0]
    pos += 4
    chiavi = []
    for _ in range(nns):
        if versione >= 2:
            pos += 4
        ns, pos = _leggi_fstring(dati, pos)
        nk = struct.unpack_from("<I", dati, pos)[0]
        pos += 4
        for _ in range(nk):
            if versione >= 2:
                pos += 4
            chiave, pos = _leggi_fstring(dati, pos)
            pos += 4  # impronta del testo sorgente
            indice = struct.unpack_from("<i", dati, pos)[0]
            pos += 4
            chiavi.append((ns, chiave, indice))
    if pos != offset_stringhe:
        raise ErroreUnreal(".locres non coerente (sezione chiavi)")
    n = struct.unpack_from("<i", dati, pos)[0]
    pos += 4
    stringhe = []
    for _ in range(n):
        s, pos = _leggi_fstring(dati, pos)
        rif = None
        if versione >= 2:
            rif = struct.unpack_from("<i", dati, pos)[0]
            pos += 4
        stringhe.append((s, rif))
    if pos != len(dati):
        raise ErroreUnreal(".locres non coerente (stringhe)")
    if any(not 0 <= i < n for _, _, i in chiavi):
        raise ErroreUnreal(".locres non coerente (indici delle stringhe)")
    return {"versione": versione, "testa": dati[:offset_stringhe], "chiavi": chiavi, "stringhe": stringhe}


def scrivi_locres(info, nuove):
    """Stesso .locres con le stringhe sostituite (tutto il resto identico byte per byte)."""
    uscita = bytearray(info["testa"]) + struct.pack("<i", len(nuove))
    for s, (_, rif) in zip(nuove, info["stringhe"]):
        uscita += _scrivi_fstring(unicodedata.normalize("NFC", s))  # accenti come Unreal se li aspetta
        if info["versione"] >= 2:
            uscita += struct.pack("<i", rif)
    return bytes(uscita)


def cultura_nativa(dati_locmeta):
    """Lingua originale scritta nel .locmeta (es. 'en')."""
    try:
        cultura, _ = _leggi_fstring(dati_locmeta, 17 if dati_locmeta[:16] == MAGIC_LOCMETA else 0)
        return cultura or None
    except (struct.error, UnicodeDecodeError):
        return None


# --- analisi del gioco --------------------------------------------------------------------------

def _paks(cartella):
    """Pak dei progetti (non quelli di Engine/Programs), nostri esclusi."""
    trovati = []
    for radice, dirs, files in os.walk(cartella):
        rel = os.path.relpath(radice, cartella).replace(os.sep, "/")
        if rel.split("/")[0] == "Engine":
            dirs[:] = []
            continue
        for f in files:
            if f.lower().endswith(".pak") and not f.startswith(PREFISSO_PAK):
                trovati.append(os.path.join(radice, f))
    # come Unreal: i pak di patch (_P) hanno priorita' piu' alta, poi l'ordine alfabetico
    return sorted(trovati, key=lambda p: (os.path.basename(p).lower().endswith("_p.pak"), os.path.basename(p).lower()))


def culture_presenti(cartella):
    """Lingue in cui il gioco e' localizzato ({'en', 'fr', 'es-419'...}), dai .locres nei pak (sola lettura)."""
    culture = set()
    for percorso in _paks(cartella):
        try:
            pak = Pak(percorso)
        except (ErroreUnreal, OSError, struct.error):
            continue
        for nome in pak.voci:
            pieno = nome_pieno(pak.mount, nome)
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", pieno)
            if m and not pieno.startswith("Engine/"):
                culture.add(m.group(3))
    return culture


def trova_locres(cartella, saltati=None):
    """[(pak, nome completo, target, cultura)] dei .locres della lingua originale (versione piu' prioritaria), con i
    nomi completi anche dai pak con un punto di montaggio piu' profondo (vedi nome_pieno: Pak.leggi li accetta).
    Un pak che non si legge (formato piu' nuovo, cifrato, rovinato) si salta, senza fermare gli altri: 'saltati'
    (una lista) riceve 'nome: motivo'."""
    letti = []
    for percorso in _paks(cartella):
        try:
            pak = Pak(percorso)
        except (ErroreUnreal, OSError, struct.error, ValueError) as e:
            if saltati is not None:
                saltati.append("%s: %s" % (os.path.basename(percorso), e))
            continue
        letti.append((pak, [nome_pieno(pak.mount, nome) for nome in pak.voci]))
    nativa = {}  # la lingua originale di ogni target, dal suo .locmeta (anche in un altro pak)
    for pak, nomi in letti:
        for nome in nomi:
            m = re.match(r"^(.*/Localization/([^/]+))/\2\.locmeta$", nome)
            if m and not nome.startswith("Engine/"):
                try:
                    nativa[m.group(1)] = cultura_nativa(pak.leggi(nome))
                except (ErroreUnreal, OSError, struct.error, ValueError) as e:
                    if saltati is not None:
                        saltati.append("%s (%s): %s" % (os.path.basename(pak.percorso), nome, e))
    sorgenti = {}
    for pak, nomi in letti:
        for nome in nomi:
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", nome)
            if not m or nome.startswith("Engine/"):
                continue
            radice_target, target, cultura = m.group(1), m.group(2), m.group(3)
            if cultura == (nativa.get(radice_target) or "en"):
                sorgenti[nome] = (pak, nome, target, cultura)  # i pak successivi sovrascrivono (priorita')
    return list(sorgenti.values())


def analizza(cartella):
    paks = _paks(cartella)
    info = {"paks": paks, "versioni": [], "iostore": False, "firmati": False, "problemi": []}
    for p in paks:
        if os.path.exists(p[:-4] + ".utoc"):
            info["iostore"] = True
        if os.path.exists(p[:-4] + ".sig"):
            info["firmati"] = True
        try:
            info["versioni"].append(Pak(p).versione)
        except ErroreUnreal as e:
            info["problemi"].append("%s: %s" % (os.path.basename(p), e))
    if info["firmati"]:
        info["problemi"].append("i pak del gioco sono firmati (.sig): un pak aggiunto verrebbe rifiutato")
    return info


# --- manifesto ----------------------------------------------------------------------------------

def leggi_manifesto(cartella):
    try:
        with open(os.path.join(cartella, MANIFESTO), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    def nostri(voci):
        return isinstance(voci, dict) and all(
            isinstance(r, str) and r.endswith(".pak") and ".." not in r.split("/") and not os.path.isabs(r)
            and ":" not in r and os.path.basename(r).startswith(PREFISSO_PAK) and isinstance(h, str)
            for r, h in voci.items())
    valido = (isinstance(m, dict) and m.get("patchsmith") is True and nostri(m.get("file"))
              and nostri(m.get("precedenti", {})) and set(m.get("precedenti", {})) <= set(m["file"]))
    return m if valido else None


def manifesto_presente(cartella):
    return os.path.exists(os.path.join(cartella, MANIFESTO))


def _sha256(percorso):
    h = hashlib.sha256()
    with open(percorso, "rb") as f:
        for blocco in iter(lambda: f.read(1 << 20), b""):
            h.update(blocco)
    return h.hexdigest()


# --- installazione ------------------------------------------------------------------------------

def _controlli(cartella):
    if gioco_aperto(cartella):
        raise ErroreUnreal("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    if analizza(cartella)["firmati"]:
        raise ErroreUnreal("I pak del gioco sono firmati (file .sig): il gioco rifiuterebbe un pak aggiunto.",
                           "NON_SUPPORTATO")
    esito = rilevamento.rileva(cartella)
    nomi = [r["nome"] for r in esito["risultati"] if r["sezione"] == "AntiCheat"]
    if nomi:
        raise ErroreUnreal("Questo gioco ha un anti-cheat (%s): non aggiungo pak modificati (rischio di ban)."
                           % ", ".join(nomi), "ANTICHEAT")


def testi_da_tradurre(cartella, log=print):
    """(sorgenti, testi unici da tradurre, lingua originale)."""
    sorgenti = trova_locres(cartella)
    if not sorgenti:
        raise ErroreUnreal("Non ho trovato file di localizzazione (.locres) nei pak del gioco.", "NON_SUPPORTATO")
    testi, culture = {}, set()
    for pak, nome, target, cultura in sorgenti:
        info = leggi_locres(pak.leggi(nome))
        log("  %s (%s): %d frasi" % (nome, cultura, len(info["stringhe"])))
        culture.add(cultura)
        for s, _ in info["stringhe"]:
            if any(c.isalpha() for c in s):
                testi.setdefault(s, None)
    return sorgenti, list(testi), sorted(culture)[0]


# punteggiatura tipografica che Google puo' introdurre: se il gioco non la usa gia', il suo font
# potrebbe non averla (comparirebbero quadratini), quindi diventa ASCII
SOSTITUTI = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
             "\u00ab": '"', "\u00bb": '"', "\u00a0": " "}
# lingue con una scrittura diversa dal latino: i loro testi non dicono nulla delle lettere accentate dei font latini
_NON_LATINE = {"ja", "zh", "ko", "ru", "uk", "be", "bg", "mk", "sr", "kk", "ky", "mn", "el", "ar", "he", "fa", "ur",
               "th", "hi", "bn", "ta", "te", "ka", "hy", "km", "lo", "my"}
_LATINO_1 = {chr(c) for c in range(0xA0, 0x100)}


def caratteri_culture(cartella, escluse=()):
    """(caratteri dei testi delle altre lingue del gioco scritte in latino, [quelle lingue]) dai .locres nei pak
    (sola lettura): una lettera che il gioco usa nelle sue traduzioni ufficiali i suoi font la sanno disegnare."""
    caratteri, culture = set(), set()
    escluse = {c.lower() for c in escluse}
    for percorso in _paks(cartella):
        try:
            pak = Pak(percorso)
        except (ErroreUnreal, OSError, struct.error, ValueError):
            continue
        for nome in pak.voci:
            pieno = nome_pieno(pak.mount, nome)
            m = re.match(r"^(.*/Localization/([^/]+))/([^/]+)/\2\.locres$", pieno)
            if not m or pieno.startswith("Engine/"):
                continue
            cultura = m.group(3)
            if cultura.lower() in escluse or cultura.split("-")[0].lower() in _NON_LATINE:
                continue
            try:
                info = leggi_locres(pak.leggi(nome))
            except (ErroreUnreal, OSError, struct.error, ValueError, UnicodeDecodeError):
                continue
            culture.add(cultura)
            for s, _ in info["stringhe"]:
                caratteri.update(s)
    return caratteri, sorted(culture)


def caratteri_ammessi(cartella, presenti, native=()):
    """I caratteri che si possono scrivere nei .locres tradotti, oppure None se non si sa (nessun limite): l'ASCII,
    quelli dei testi originali ('presenti') e quelli delle traduzioni ufficiali in lingue latine. I font di Unreal sono
    file .ttf/.otf: se le traduzioni ufficiali usano lettere accentate del Latino-1 (\u00e9, \u00fc, \u00f1...), il font ha tutto il
    blocco (anche \u00ec e \u00f2, che il francese o lo spagnolo non usano). Senza traduzioni ufficiali in lingue latine, o se
    non usano nessuna lettera accentata, non c'e' nessun indizio: nessun limite."""
    altre, culture = caratteri_culture(cartella, native)
    if not culture or not any(c.isalpha() and not c.isascii() for c in altre):
        return None
    from .characters import ASCII
    ammessi = set(ASCII) | set(presenti) | altre
    if any(c in _LATINO_1 and c.isalpha() for c in altre):
        ammessi |= _LATINO_1
    return ammessi


def _adatta(traduzione, presenti, ammessi=None, lingua_dest="it"):
    """Traduzione pronta per il .locres, oppure None se va scartata (U+0000 e U+FFFF troncano il testo; un carattere
    che i font del gioco non sanno disegnare e che non si puo' sostituire: vedi caratteri_ammessi)."""
    if "\x00" in traduzione or "\uffff" in traduzione:
        return None
    pronta = "".join(SOSTITUTI[c] if c in SOSTITUTI and c not in presenti else c for c in traduzione)
    if ammessi is not None:
        from .characters import adatta_caratteri
        pronta = adatta_caratteri(pronta, ammessi, lingua_dest)
    return pronta


def installa(cartella, a, nome_profilo, memoria, log=print, respinte=None):
    """Scrive il pak di patch con i .locres tradotti usando le traduzioni in 'memoria' {originale: tradotto}.
    Restituisce il numero di frasi tradotte inserite. I caratteri che i font del gioco probabilmente non hanno si
    adattano (vedi caratteri_ammessi: è -> e'); una traduzione che non si puo' adattare resta in originale e va in
    'respinte' (se c'e'), {originale: caratteri}, per problemi.json."""
    cartella = os.path.abspath(cartella)
    _controlli(cartella)
    sorgenti = trova_locres(cartella)
    if not sorgenti:
        raise ErroreUnreal("Non ho trovato file di localizzazione (.locres) nei pak del gioco.", "NON_SUPPORTATO")
    manifesto = leggi_manifesto(cartella) if manifesto_presente(cartella) else None
    if manifesto_presente(cartella) and manifesto is None:
        raise ErroreUnreal("Il file %s non e' valido: non lo uso. Controlla la cartella a mano." % MANIFESTO)

    letti = [(pak, nome, pak.leggi(nome)) for pak, nome, _, _ in sorgenti]
    letti = [(pak, nome, originale, leggi_locres(originale)) for pak, nome, originale in letti]
    presenti = {c for _, _, _, info in letti for s, _ in info["stringhe"] for c in s}
    ammessi = caratteri_ammessi(cartella, presenti, {cultura for _, _, _, cultura in sorgenti})
    per_pak, tradotte, totale, adattate, scartate = {}, 0, 0, 0, {}
    for pak, nome, originale, info in letti:
        nuove = []
        for s, _ in info["stringhe"]:
            grezza = memoria.get(s)
            t = _adatta(grezza, presenti, ammessi, a) if grezza else None
            if grezza and t is None and ammessi is not None and _adatta(grezza, presenti) is not None:
                from .characters import Filtro, non_disegnabili
                scartate[s] = non_disegnabili(grezza, Filtro(ammessi, aggiunti=s), a)
            elif t and _adatta(grezza, presenti) != t:
                adattate += 1
            nuove.append(t if t else s)
            tradotte += bool(t) and t != s
            totale += 1
        nuovo = scrivi_locres(info, nuove)
        controllo = leggi_locres(nuovo)  # deve rileggersi con la stessa struttura
        if controllo["chiavi"] != info["chiavi"] or controllo["testa"] != info["testa"]:
            raise ErroreUnreal("Controllo del .locres tradotto fallito per %s: non scrivo nulla" % nome)
        per_pak.setdefault(os.path.dirname(pak.percorso), (pak, []))[1].append((nome, nuovo, originale))
    if adattate:
        log("  %d frasi adattate ai caratteri dei font del gioco (lettere che le sue lingue non usano, es. e')"
            % adattate)
    if scartate:
        log("  ! %d traduzioni hanno caratteri che i font del gioco probabilmente non hanno e restano in originale, "
            "es. %r (quelle scritte da PatchSmith le rifaccio senza quei caratteri)" % (
                len(scartate), next(iter(scartate))[:60]))
        if respinte is not None:
            respinte.update(scartate)

    # 1) ogni pak nuovo viene scritto accanto con nome temporaneo (Unreal carica solo i .pak) e verificato
    pronti, vecchi = {}, (manifesto or {}).get("file", {})
    try:
        for cartella_paks, (modello, file) in per_pak.items():
            destinazione = os.path.join(cartella_paks, nome_pak(a))
            rel = os.path.relpath(destinazione, cartella).replace(os.sep, "/")
            if os.path.exists(destinazione) and rel not in vecchi:
                raise ErroreUnreal("Esiste gia' %s non creato da PatchSmith: non lo sovrascrivo." % rel)
            temporaneo = destinazione + SUFFISSO_TMP
            # nomi completi con il punto di montaggio della radice: vale anche per i .locres di un pak montato in
            # una cartella piu' profonda (per i pak principali e' lo stesso punto di montaggio, '../../../')
            scrivi_pak(temporaneo, modello, [(n, d) for n, d, _ in file], mount="../../../")
            letto = Pak(temporaneo)  # verifica: il pak scritto deve rileggersi con gli stessi file
            if any(letto.leggi(n) != d for n, d, _ in file) or set(letto.voci) != {n for n, _, _ in file}:
                raise ErroreUnreal("Verifica del pak scritto fallita: non installo nulla")
            pronti[rel] = (temporaneo, destinazione, _sha256(temporaneo))
    except BaseException:
        for temporaneo, _, _ in pronti.values():
            if os.path.exists(temporaneo):
                os.remove(temporaneo)
        raise

    # 2) manifesto scritto PRIMA di mettere i pak al loro posto: se ci si ferma a meta', il ripristino
    #    riconosce sia il pak nuovo sia quello che c'era prima (campo "precedenti")
    def scrivi_manifesto(file, precedenti):
        scrivi_atomico(os.path.join(cartella, MANIFESTO), json.dumps({
            "versione": 1, "patchsmith": True, "patchsmith_versione": __version__, "lingua": a,
            "profilo": nome_profilo,
            "data": datetime.datetime.now().isoformat(timespec="seconds"),
            "file": file, "precedenti": precedenti,
            "sorgenti": {n: hashlib.sha1(o).hexdigest() for _, (_, f) in per_pak.items() for n, _, o in f},
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    nuovi_file = {rel: impronta for rel, (_, _, impronta) in pronti.items()}
    scrivi_manifesto(dict(vecchi, **nuovi_file), {rel: h for rel, h in vecchi.items() if rel in nuovi_file})
    for temporaneo, destinazione, _ in pronti.values():
        os.replace(temporaneo, destinazione)
    # 3) pak di una lingua precedente (es. cambio da it a es): li tolgo
    for rel, impronta in vecchi.items():
        p = os.path.join(cartella, *rel.split("/"))
        if rel not in nuovi_file and os.path.exists(p) and _sha256(p) == impronta:
            os.remove(p)
    scrivi_manifesto(nuovi_file, {})
    log("  Pak di traduzione scritto: %s (%d frasi tradotte su %d)" % (", ".join(nuovi_file), tradotte, totale))
    return tradotte


def disinstalla(cartella, log=print):
    cartella = os.path.abspath(cartella)
    if not manifesto_presente(cartella):
        return False
    manifesto = leggi_manifesto(cartella)
    if manifesto is None:
        raise ErroreUnreal("Il file %s non e' valido: per sicurezza non cancello nulla." % MANIFESTO)
    if gioco_aperto(cartella):
        raise ErroreUnreal("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    for rel, impronta in manifesto["file"].items():
        p = os.path.join(cartella, *rel.split("/"))
        if os.path.exists(p + SUFFISSO_TMP):
            os.remove(p + SUFFISSO_TMP)
        if os.path.exists(p):
            if _sha256(p) in (impronta, manifesto.get("precedenti", {}).get(rel)):
                os.remove(p)
                log("  Rimosso %s" % rel)
            else:
                log("  ! %s e' stato modificato da altri: lo lascio" % rel)
    if os.path.exists(os.path.join(cartella, MANIFESTO + SUFFISSO_TMP)):
        os.remove(os.path.join(cartella, MANIFESTO + SUFFISSO_TMP))
    os.remove(os.path.join(cartella, MANIFESTO))
    log("  Traduzione rimossa: il gioco usa di nuovo i suoi file originali.")
    return True


def stato(cartella, log=print):
    info = analizza(cartella)
    log("  Pak del gioco: %d (versioni %s)%s" % (len(info["paks"]), sorted(set(info["versioni"])),
                                                ", IoStore presente" if info["iostore"] else ""))
    for p in info["problemi"]:
        log("  ! " + p)
    manifesto = leggi_manifesto(cartella)
    if not manifesto:
        log("  Traduzione non installata.")
        return False
    for rel, impronta in manifesto["file"].items():
        p = os.path.join(cartella, *rel.split("/"))
        ok = os.path.exists(p) and _sha256(p) == impronta
        log("  %s %s (lingua %s, %s)" % ("OK " if ok else "NO ", rel, manifesto.get("lingua"), manifesto.get("data")))
    try:
        attuali = {n: hashlib.sha1(pak.leggi(n)).hexdigest() for pak, n, _, _ in trova_locres(cartella)}
        if attuali != manifesto.get("sorgenti"):
            log("  ! Il gioco e' stato aggiornato: premi 'Analizza e traduci' per tradurre anche i testi nuovi "
                "(quelli gia' tradotti restano).")
    except ErroreUnreal as e:
        log("  ! " + str(e))
    return True
