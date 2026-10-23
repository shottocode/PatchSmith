"""Unreal Engine 4 adapter (classic .pak archives): translates the .locres localization files.

1. reads (read only) the game's .pak files and finds the .locres files of the original language,
   for example DDS/Content/Localization/Game/en/Game.locres (the language comes from the .locmeta file);
2. translates the lines (translation memory + Google), protecting {x} placeholders, <Stile>...</> tags and separators;
3. writes a NEW patch pak (Content/Paks/zz_Patchsmith_<lingua>_999_P.pak) with the translated .locres files at the
   same path: Unreal loads it with a higher priority and shows the translation ("_P" = patch,
   "_999" = high patch number, so it wins even over the official patches "_1_P", "_2_P"...).
The original paks are never modified: to go back, removing the added pak is enough.

Current limits: pak version 3-9 (UE 4.x) with an unencrypted index and uncompressed or zlib files;
the original language (usually English) must be selected in the game.
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
        self.codice = codice  # outcome for the interface (see outcome.py), e.g. GIOCO_APERTO


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


# --- .pak archives ------------------------------------------------------------------------------

def nome_pieno(mount, nome):
    """The full name of a file in the pak: the mount point without the leading '../', plus the name. UnrealPak uses
    as mount point the folder shared by all the files of the pak: '../../../' for the main paks (the names are
    already complete, 'Proj/Content/...'), a deeper folder for small paks (Remnant: '../../../Remnant/
    Content/', with the names 'Localization/Game/en/Game.locres')."""
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
    """Reader of UE4 paks version 3-9 ('legacy' index)."""

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
        for larghezza in ((4, 1) if self.versione == 8 else (4,)):  # 4.22 used 1 byte for the method
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
        """The data of a file in the pak, from its name in the pak or its full name (see nome_pieno)."""
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
                base = voce.offset if self.versione >= 5 else 0  # relative blocks from version 5
                parti = []
                for inizio, fine in voce.blocchi:
                    f.seek(base + inizio)
                    parti.append(zlib.decompress(f.read(fine - inizio)))
                dati = b"".join(parti)
        if not voce.metodo and hashlib.sha1(dati).digest() != voce.sha1:
            raise ErroreUnreal("%s: impronta diversa da quella dell'indice" % nome)
        return dati


def _serie_voce(versione, offset, dati, larghezza_metodo=4):
    """FPakEntry of an uncompressed file (version 3-9)."""
    parti = struct.pack("<qqq", offset, len(dati), len(dati))
    parti += struct.pack("<B", 0) if larghezza_metodo == 1 else struct.pack("<i", 0)
    parti += hashlib.sha1(dati).digest()
    parti += struct.pack("<BI", 0, 0)  # flags (not encrypted), block size
    return parti


def scrivi_pak(percorso, modello, file, mount=None):
    """Writes an uncompressed pak with the same format as the 'modello' pak. file: [(nome, dati)]. mount: the mount
    point (usually the model's; '../../../' with full names)."""
    v, larghezza = modello.versione, getattr(modello, "larghezza_metodo", 4)
    mount = modello.mount if mount is None else mount
    corpo = bytearray()
    voci = []
    for nome, dati in file:
        offset = len(corpo)
        corpo += _serie_voce(v, 0, dati, larghezza)  # the header in front of the data has offset 0 (like UnrealPak)
        corpo += dati
        voci.append((nome, offset, dati))
    indice = _scrivi_fstring(mount) + struct.pack("<i", len(voci))
    for nome, offset, dati in voci:
        indice += _scrivi_fstring(nome) + _serie_voce(v, offset, dati, larghezza)
    coda = b""
    if v >= 7:
        coda += b"\x00" * 16  # no encryption key
    if v >= 4:
        coda += b"\x00"  # index not encrypted
    coda += struct.pack("<IIqq", MAGIC_PAK, v, len(corpo), len(indice)) + hashlib.sha1(indice).digest()
    if v >= 9:
        coda += b"\x00"  # index not frozen
    if v >= 8:
        coda += b"\x00" * 32 * modello.posti_compressione
    with open(percorso, "wb") as f:
        f.write(bytes(corpo) + indice + coda)


# --- .locres / .locmeta -------------------------------------------------------------------------

def leggi_locres(dati):
    """Structure of a .locres (versions 1-3). The translated strings are at the end of the file."""
    if dati[:16] != MAGIC_LOCRES:
        raise ErroreUnreal("Formato .locres 'legacy' (senza intestazione): non ancora supportato")
    versione = dati[16]
    if not 1 <= versione <= 3:
        raise ErroreUnreal("Versione .locres %d non supportata" % versione)
    pos = 17
    offset_stringhe = struct.unpack_from("<q", dati, pos)[0]
    pos += 8
    if versione >= 2:
        pos += 4  # number of entries
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
            pos += 4  # hash of the source text
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
    """Same .locres with the strings replaced (everything else identical byte for byte)."""
    uscita = bytearray(info["testa"]) + struct.pack("<i", len(nuove))
    for s, (_, rif) in zip(nuove, info["stringhe"]):
        uscita += _scrivi_fstring(unicodedata.normalize("NFC", s))  # accents the way Unreal expects them
        if info["versione"] >= 2:
            uscita += struct.pack("<i", rif)
    return bytes(uscita)


def cultura_nativa(dati_locmeta):
    """Original language written in the .locmeta (e.g. 'en')."""
    try:
        cultura, _ = _leggi_fstring(dati_locmeta, 17 if dati_locmeta[:16] == MAGIC_LOCMETA else 0)
        return cultura or None
    except (struct.error, UnicodeDecodeError):
        return None


# --- game analysis ------------------------------------------------------------------------------

def _paks(cartella):
    """Paks of the projects (not those of Engine/Programs), ours excluded."""
    trovati = []
    for radice, dirs, files in os.walk(cartella):
        rel = os.path.relpath(radice, cartella).replace(os.sep, "/")
        if rel.split("/")[0] == "Engine":
            dirs[:] = []
            continue
        for f in files:
            if f.lower().endswith(".pak") and not f.startswith(PREFISSO_PAK):
                trovati.append(os.path.join(radice, f))
    # like Unreal: patch paks (_P) have a higher priority, then alphabetical order
    return sorted(trovati, key=lambda p: (os.path.basename(p).lower().endswith("_p.pak"), os.path.basename(p).lower()))


def culture_presenti(cartella):
    """Languages the game is localized in ({'en', 'fr', 'es-419'...}), from the .locres files in the paks (read
    only)."""
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
    """[(pak, nome completo, target, cultura)] of the .locres files of the original language (highest-priority
    version), with full names also from paks with a deeper mount point (see nome_pieno: Pak.leggi accepts them).
    A pak that cannot be read (newer format, encrypted, corrupted) is skipped, without stopping the others: 'saltati'
    (a list) receives 'nome: motivo'."""
    letti = []
    for percorso in _paks(cartella):
        try:
            pak = Pak(percorso)
        except (ErroreUnreal, OSError, struct.error, ValueError) as e:
            if saltati is not None:
                saltati.append("%s: %s" % (os.path.basename(percorso), e))
            continue
        letti.append((pak, [nome_pieno(pak.mount, nome) for nome in pak.voci]))
    nativa = {}  # the original language of each target, from its .locmeta (even in another pak)
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
                sorgenti[nome] = (pak, nome, target, cultura)  # later paks overwrite (priority)
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


# --- manifest -----------------------------------------------------------------------------------

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


# --- installation -------------------------------------------------------------------------------

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
    """(sources, unique texts to translate, original language)."""
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


# typographic punctuation that Google may introduce: if the game does not already use it, its font
# may not have it (little boxes would appear), so it becomes ASCII
SOSTITUTI = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2026": "...",
             "\u00ab": '"', "\u00bb": '"', "\u00a0": " "}
# languages with a non-Latin script: their texts say nothing about the accented letters of Latin fonts
_NON_LATINE = {"ja", "zh", "ko", "ru", "uk", "be", "bg", "mk", "sr", "kk", "ky", "mn", "el", "ar", "he", "fa", "ur",
               "th", "hi", "bn", "ta", "te", "ka", "hy", "km", "lo", "my"}
_LATINO_1 = {chr(c) for c in range(0xA0, 0x100)}


def caratteri_culture(cartella, escluse=()):
    """(characters of the texts of the game's other languages written in Latin script, [those languages]) from the
    .locres files in the paks (read only): a letter the game uses in its official translations, its fonts can draw."""
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
    """The characters that can be written in the translated .locres files, or None if unknown (no limit): ASCII,
    those of the original texts ('presenti') and those of the official translations in Latin-script languages.
    Unreal's fonts are .ttf/.otf files: if the official translations use accented Latin-1 letters
    (\u00e9, \u00fc, \u00f1...), the font has the whole block (including \u00ec and \u00f2, which French or Spanish do
    not use). Without official translations in Latin-script languages, or if they use no accented letter, there is no
    clue: no limit."""
    altre, culture = caratteri_culture(cartella, native)
    if not culture or not any(c.isalpha() and not c.isascii() for c in altre):
        return None
    from .characters import ASCII
    ammessi = set(ASCII) | set(presenti) | altre
    if any(c in _LATINO_1 and c.isalpha() for c in altre):
        ammessi |= _LATINO_1
    return ammessi


def _adatta(traduzione, presenti, ammessi=None, lingua_dest="it"):
    """Translation ready for the .locres, or None if it must be rejected (U+0000 and U+FFFF truncate the text; a
    character the game's fonts cannot draw and that cannot be replaced: see caratteri_ammessi)."""
    if "\x00" in traduzione or "\uffff" in traduzione:
        return None
    pronta = "".join(SOSTITUTI[c] if c in SOSTITUTI and c not in presenti else c for c in traduzione)
    if ammessi is not None:
        from .characters import adatta_caratteri
        pronta = adatta_caratteri(pronta, ammessi, lingua_dest)
    return pronta


def installa(cartella, a, nome_profilo, memoria, log=print, respinte=None):
    """Writes the patch pak with the translated .locres files using the translations in 'memoria' {originale:
    tradotto}. Returns the number of translated lines inserted. Characters the game's fonts probably lack are
    adapted (see caratteri_ammessi: è -> e'); a translation that cannot be adapted stays in the original and goes into
    'respinte' (if given), {originale: caratteri}, for problemi.json."""
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
        controllo = leggi_locres(nuovo)  # it must read back with the same structure
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

    # 1) each new pak is written alongside with a temporary name (Unreal only loads .pak files) and verified
    pronti, vecchi = {}, (manifesto or {}).get("file", {})
    try:
        for cartella_paks, (modello, file) in per_pak.items():
            destinazione = os.path.join(cartella_paks, nome_pak(a))
            rel = os.path.relpath(destinazione, cartella).replace(os.sep, "/")
            if os.path.exists(destinazione) and rel not in vecchi:
                raise ErroreUnreal("Esiste gia' %s non creato da PatchSmith: non lo sovrascrivo." % rel)
            temporaneo = destinazione + SUFFISSO_TMP
            # full names with the root mount point: this also works for the .locres files of a pak mounted in a
            # deeper folder (for the main paks it is the same mount point, '../../../')
            scrivi_pak(temporaneo, modello, [(n, d) for n, d, _ in file], mount="../../../")
            letto = Pak(temporaneo)  # verification: the written pak must read back with the same files
            if any(letto.leggi(n) != d for n, d, _ in file) or set(letto.voci) != {n for n, _, _ in file}:
                raise ErroreUnreal("Verifica del pak scritto fallita: non installo nulla")
            pronti[rel] = (temporaneo, destinazione, _sha256(temporaneo))
    except BaseException:
        for temporaneo, _, _ in pronti.values():
            if os.path.exists(temporaneo):
                os.remove(temporaneo)
        raise

    # 2) manifest written BEFORE putting the paks in place: if things stop halfway, restore
    #    recognizes both the new pak and the one that was there before ("precedenti" field)
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
    # 3) paks of a previous language (e.g. switching from it to es): remove them
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
