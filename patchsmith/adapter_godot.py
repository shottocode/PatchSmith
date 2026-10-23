"""Adapter Godot 4: giochi che usano le traduzioni di Godot (tabelle .translation importate da CSV o PO).

Il gioco non si tocca: il pacchetto dei dati (PCK, anche incorporato nell'eseguibile) si legge soltanto. PatchSmith
legge la tabella della lingua di partenza (OptimizedTranslation: chiavi sotto forma di impronte, testi compressi con
smaz; oppure Translation con il dizionario dei messaggi), traduce i testi e mette accanto all'eseguibile:
- override.cfg: le impostazioni del progetto che Godot legge all'avvio dalla cartella dell'eseguibile (il modo
  previsto da Godot per cambiarle senza toccare il gioco), con un autoload di PatchSmith;
- patchsmith-traduzione.gd: lo script dell'autoload, che all'avvio sostituisce la tabella della lingua di partenza
  con un "ponte" (prima il testo originale dalla tabella del gioco, poi la sua traduzione): con la lingua di
  partenza scelta nel gioco (di solito English) i testi compaiono tradotti; aggiunge anche la lingua di arrivo;
- patchsmith-traduzione.json: le traduzioni (testo originale -> tradotto).
Tutto e' elencato con le impronte in patchsmith-godot.json; "Ripristina originali" toglie i file (e rimette un
override.cfg del gioco, se c'era).

PCK cifrato (chiave nel motore del gioco): protezione degli sviluppatori, non si apre.
"""
import base64
import datetime
import hashlib
import json
import os
import re
import struct

from .adapter_webjson import scrivi_atomico

MANIFESTO = "patchsmith-godot.json"
OVERRIDE = "override.cfg"
SCRIPT = "patchsmith-traduzione.gd"
DIZIONARIO = "patchsmith-traduzione.json"
AUTOLOAD = "PatchsmithTraduzione"
_LINGUA_NEL_NOME = re.compile(r"\.([A-Za-z]{2,3}(?:_[A-Za-z0-9]{2,4})?)\.translation$")


class ErroreGodot(Exception):
    pass


# --- pacchetto dei dati (PCK) ---------------------------------------------------------------------------------------

class Pck:
    """Il PCK di Godot 4 (file .pck o incorporato in fondo all'eseguibile): elenco dei file e lettura."""

    def __init__(self, percorso):
        self.percorso = percorso
        with open(percorso, "rb") as f:
            inizio = 0
            if f.read(4) != b"GDPC":  # incorporato: in fondo all'eseguibile [dimensione u64]["GDPC"]
                f.seek(0, 2)
                fine = f.tell()
                if fine < 16:
                    raise ErroreGodot("non e' un pacchetto di Godot")
                f.seek(fine - 12)
                dimensione = struct.unpack("<Q", f.read(8))[0]
                if f.read(4) != b"GDPC" or dimensione + 12 > fine:
                    raise ErroreGodot("nessun pacchetto di Godot nell'eseguibile")
                inizio = fine - 12 - dimensione
                f.seek(inizio)
                if f.read(4) != b"GDPC":
                    raise ErroreGodot("pacchetto di Godot non valido")
            self.formato, self.major, self.minor, self.patch = struct.unpack("<4I", f.read(16))
            if self.formato < 2:
                raise ErroreGodot("pacchetto di Godot %d (formato %d): per ora PatchSmith legge solo Godot 4"
                                  % (self.major, self.formato))
            flags, base = struct.unpack("<IQ", f.read(12))
            self.cifrato = bool(flags & 1)
            if self.cifrato:
                raise ErroreGodot("l'elenco dei file del gioco e' cifrato")
            relativo = bool(flags & 2)
            f.read(64)
            self.file = {}
            for _ in range(struct.unpack("<I", f.read(4))[0]):
                lunghezza = struct.unpack("<I", f.read(4))[0]
                nome = f.read(lunghezza).rstrip(b"\0").decode("utf-8", "replace")
                posizione, dimensione = struct.unpack("<QQ", f.read(16))
                f.read(16)  # md5
                fl = struct.unpack("<I", f.read(4))[0]
                if nome.startswith("res://"):
                    nome = nome[6:]
                self.file[nome] = ((inizio + base if relativo else inizio) + posizione, dimensione, fl)

    def versione(self):
        return "%d.%d.%d" % (self.major, self.minor, self.patch)

    def leggi(self, nome):
        posizione, dimensione, fl = self.file[nome]
        if fl & 1:
            raise ErroreGodot("%s e' cifrato" % nome)
        with open(self.percorso, "rb") as f:
            f.seek(posizione)
            return f.read(dimensione)


def trova_pck(cartella):
    """(percorso del PCK, cartella dell'eseguibile) del gioco: incorporato in un .exe o in un .pck accanto; o None."""
    try:
        nomi = sorted(os.listdir(cartella))
    except OSError:
        return None
    for nome in [n for n in nomi if n.lower().endswith(".pck")] + [n for n in nomi if n.lower().endswith(".exe")]:
        p = os.path.join(cartella, nome)
        try:
            Pck(p)
        except (ErroreGodot, OSError, struct.error):
            continue
        return p, cartella
    return None


# --- tabelle delle traduzioni -------------------------------------------------------------------------------------

_SMAZ = [" ", "the", "e", "t", "a", "of", "o", "and", "i", "n", "s", "e ", "r", " th", " t", "in", "he", "th", "h",
         "he ", "to", "\r\n", "l", "s ", "d", " a", "an", "er", "c", " o", "d ", "on", " of", "re", "of ", "t ", ", ",
         "is", "u", "at", "   ", "n ", "or", "which", "f", "m", "as", "it", "that", "\n", "was", "en", "  ", " w",
         "es", " an", " i", "\r", "f ", "g", "p", "nd", " s", "nd ", "ed ", "w", "ed", "http://", "for", "te", "ing",
         "y ", "The", " c", "ti", "r ", "his", "st", " in", "ar", "nt", ",", " to", "y", "ng", " h", "with", "le",
         "al", "to ", "b", "ou", "be", "were", " b", "se", "o ", "ent", "ha", "ng ", "their", "\"", "hi", "from",
         " f", "in ", "de", "ion", "me", "v", ".", "ve", "all", "re ", "ri", "ro", "is ", "co", "f t", "are", "ea",
         ". ", "her", " m", "er ", " p", "es ", "by", "they", "di", "ra", "ic", "not", "s, ", "d t", "at ", "ce",
         "la", "h ", "ne", "as ", "tio", "on ", "n t", "io", "we", " a ", "om", ", a", "s o", "ur", "li", "ll", "ch",
         "had", "this", "e t", "g ", "e\r\n", " wh", "ere", " co", "e o", "a ", "us", " d", "ss", "\n\r\n", "\r\n\r",
         "=\"", " be", " e", "s a", "ma", "one", "t t", "or ", "but", "el", "so", "l ", "e s", "s,", "no", "ter",
         " wa", "iv", "ho", "e a", " r", "hat", "s t", "ns", "ch ", "wh", "tr", "ut", "/", "have", "ly ", "ta", " ha",
         " on", "tha", "-", " l", "ati", "en ", "pe", " re", "there", "ass", "si", " fo", "wa", "ec", "our", "who",
         "its", "z", "fo", "rs", ">", "ot", "un", "<", "im", "th ", "nc", "ate", "><", "ver", "ad", " we", "ly", "ee",
         " n", "id", " cl", "ac", "il", "</", "rt", " wi", "div", "e, ", " it", "whi", " ma", "ge", "x", "e c", "men",
         ".com"]  # il dizionario di smaz (thirdparty/misc/smaz.c in Godot)


def _smaz(dati):
    uscita, i = bytearray(), 0
    while i < len(dati):
        c = dati[i]
        if c == 254:
            uscita.append(dati[i + 1])
            i += 2
        elif c == 255:
            n = dati[i + 1] + 1
            uscita += dati[i + 2:i + 2 + n]
            i += 2 + n
        else:
            uscita += _SMAZ[c].encode("latin-1")
            i += 1
    return bytes(uscita)


class _Lettore:
    def __init__(self, dati, o=0):
        self.d, self.o = dati, o

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def u64(self):
        v = struct.unpack_from("<Q", self.d, self.o)[0]
        self.o += 8
        return v

    def stringa(self):
        n = self.u32() & 0x7FFFFFFF
        s = self.d[self.o:self.o + n].rstrip(b"\0").decode("utf-8", "replace")
        self.o += n
        return s

    def variante(self):
        """Solo i tipi che servono per le tabelle delle traduzioni; None per gli altri (ValueError se non si sa
        quanto sono lunghi)."""
        tipo = self.u32()
        if tipo == 1:  # nil
            return None
        if tipo == 2:  # bool
            return bool(self.u32())
        if tipo == 3:  # int
            return struct.unpack("<i", struct.pack("<I", self.u32()))[0]
        if tipo == 40:  # int64
            return self.u64()
        if tipo in (5, 44):  # String, StringName
            return self.stringa()
        if tipo == 26:  # Dictionary
            n = self.u32() & 0x7FFFFFFF
            return {self.variante(): self.variante() for _ in range(n)}
        if tipo == 30:  # Array
            n = self.u32() & 0x7FFFFFFF
            return [self.variante() for _ in range(n)]
        if tipo == 31:  # PackedByteArray (allineato a 4)
            n = self.u32()
            v = self.d[self.o:self.o + n]
            self.o += n + (4 - n % 4) % 4
            return v
        if tipo == 32:  # PackedInt32Array
            n = self.u32()
            v = list(struct.unpack_from("<%di" % n, self.d, self.o))
            self.o += 4 * n
            return v
        if tipo == 34:  # PackedStringArray
            return [self.stringa() for _ in range(self.u32())]
        raise ValueError("tipo %d non previsto" % tipo)


def leggi_risorsa(dati):
    """(tipo, {proprieta': valore}) dell'ultima risorsa interna di un file .translation binario di Godot 4."""
    if dati[:4] != b"RSRC":
        raise ErroreGodot("non e' una risorsa binaria di Godot")
    r = _Lettore(dati, 4)
    if r.u32():
        raise ErroreGodot("risorsa big-endian: non prevista")
    r.u32()  # real64
    major = r.u32()
    r.u32()  # minor
    r.u32()  # formato
    r.stringa()  # tipo principale
    r.u64()  # importmd
    flags = r.u32()
    r.u64()  # uid
    if flags & 4:  # FORMAT_FLAG_HAS_SCRIPT_CLASS
        r.stringa()
    r.o += 4 * 11  # campi riservati
    nomi = [r.stringa() for _ in range(r.u32())]
    for _ in range(r.u32()):  # risorse esterne
        r.stringa()
        r.stringa()
        if major >= 4:
            r.stringa()  # uid
    interne = [(r.stringa(), r.u64()) for _ in range(r.u32())]
    if not interne:
        raise ErroreGodot("nessuna risorsa nel file")
    r.o = interne[-1][1]
    tipo = r.stringa()
    proprieta = {}
    for _ in range(r.u32()):
        nome = nomi[r.u32() & 0x7FFFFFFF]
        proprieta[nome] = r.variante()
    return tipo, proprieta


def testi_tabella(dati):
    """(lingua, [testi]) di una tabella delle traduzioni (OptimizedTranslation o Translation)."""
    tipo, p = leggi_risorsa(dati)
    lingua = p.get("locale") or "en"  # (en e' il valore predefinito: Godot non lo scrive)
    if tipo == "OptimizedTranslation":
        ht, bt, st = p.get("hash_table") or [], p.get("bucket_table") or [], p.get("strings") or b""
        testi = []
        for b in ht:
            if b < 0 or b >= len(bt):
                continue
            for j in range(bt[b]):
                _chiave, inizio, compresso, intero = bt[b + 2 + 4 * j:b + 6 + 4 * j]
                grezzo = st[inizio:inizio + compresso]
                testo = grezzo if compresso == intero else _smaz(grezzo)
                testi.append(testo.split(b"\0", 1)[0].decode("utf-8", "replace"))
        return lingua, testi
    if tipo == "Translation":
        messaggi = p.get("messages") or {}
        return lingua, [v for v in messaggi.values() if isinstance(v, str)]
    raise ErroreGodot("tabella di tipo %s non prevista" % tipo)


def tabelle(pck):
    """{lingua: percorso nel PCK} delle tabelle delle traduzioni (dal nome, es. translations.en.translation)."""
    uscita = {}
    for nome in pck.file:
        m = _LINGUA_NEL_NOME.search(nome)
        if m and not nome.startswith(".godot/"):
            uscita.setdefault(m.group(1).replace("_", "-"), nome)
    return uscita


# --- installazione ---------------------------------------------------------------------------------------------------

SCRIPT_GD = '''extends Node
## Patchsmith: traduzione di questo gioco (aggiunta da Patchsmith: "Ripristina originali" in Patchsmith la toglie).
## Prende il posto della tabella della lingua indicata in patchsmith-traduzione.json ("lingua", di solito "en"):
## con quella lingua scelta nel gioco i testi compaiono tradotti. Aggiunge anche le lingue in "aggiunte".

class Ponte extends Translation:
	var originale: Translation
	var testi := {}

	func _traduci(sorgente: String, chiave: StringName) -> StringName:
		if sorgente.is_empty():
			sorgente = String(chiave)
		var t = testi.get(sorgente)
		return StringName(t) if t != null else StringName(sorgente)

	func _get_message(src_message: StringName, context: StringName) -> StringName:
		return _traduci(String(originale.get_message(src_message, context)), src_message)

	func _get_plural_message(src_message: StringName, src_plural_message: StringName, n: int, context: StringName) -> StringName:
		return _traduci(String(originale.get_plural_message(src_message, src_plural_message, n, context)), src_message)

var _fatto := false

func _enter_tree() -> void:
	_sostituisci()

func _ready() -> void:
	if not _fatto:
		_sostituisci.call_deferred()

func _sostituisci() -> void:
	if _fatto:
		return
	var f := FileAccess.open("res://patchsmith-traduzione.json", FileAccess.READ)
	if f == null:
		return
	var dati = JSON.parse_string(f.get_as_text())
	if typeof(dati) != TYPE_DICTIONARY:
		return
	var lingua: String = dati.get("lingua", "en")
	var originale := TranslationServer.get_translation_object(lingua)
	if originale == null or originale is Ponte:
		return
	_fatto = true
	var testi: Dictionary = dati.get("testi", {})
	TranslationServer.remove_translation(originale)
	var lingue: Array = [lingua]
	lingue.append_array(dati.get("aggiunte", []))
	for l in lingue:
		var ponte := Ponte.new()
		ponte.originale = originale
		ponte.testi = testi
		ponte.locale = l
		TranslationServer.add_translation(ponte)
	TranslationServer.set_locale(TranslationServer.get_locale())
'''


def _sha256(percorso):
    with open(percorso, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def leggi_manifesto(cartella):
    try:
        with open(os.path.join(cartella, MANIFESTO), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None
    return m if isinstance(m, dict) and m.get("patchsmith") else None


def manifesto_presente(cartella):
    return os.path.exists(os.path.join(cartella, MANIFESTO))


def _originale(manifesto):
    """Il testo dell'override.cfg del gioco (se c'era), o None."""
    if manifesto.get("override_originale") is None:
        return None
    return base64.b64decode(manifesto["override_originale"]).decode("utf-8", "replace")


def _testo_override(precedente, autoload):
    """override.cfg con l'autoload di PatchSmith (aggiunto a quello del gioco, se c'era)."""
    riga = '%s="*res://%s"' % (AUTOLOAD, autoload)
    righe = (precedente or "").replace("\r\n", "\n").split("\n") if precedente else []
    righe = [r for r in righe if not r.strip().startswith(AUTOLOAD + "=")]
    if "[autoload]" in [r.strip() for r in righe]:
        i = [r.strip() for r in righe].index("[autoload]")
        righe.insert(i + 1, riga)
    else:
        righe += (["", "[autoload]"] if righe and righe[-1].strip() else ["[autoload]"]) + [riga]
    return "\n".join(righe).strip("\n") + "\n"


def installa(cartella, lingua_tabella, traduzioni, destinazione, log=print):
    """Scrive (o aggiorna) override.cfg, lo script e le traduzioni accanto all'eseguibile, con il manifesto."""
    manifesto = leggi_manifesto(cartella) or {"patchsmith": True, "versione": 1, "file": {}}
    override = os.path.join(cartella, OVERRIDE)
    if os.path.exists(override) and OVERRIDE not in manifesto["file"]:  # override.cfg del gioco: si conserva
        with open(override, "rb") as f:  # (i byte esatti: si rimette identico)
            manifesto["override_originale"] = base64.b64encode(f.read()).decode("ascii")
        log("  override.cfg del gioco: conservato, aggiungo solo la riga di PatchSmith")
    dizionario = {k: v for k, v in traduzioni.items() if isinstance(v, str) and v and v != k}
    contenuti = {
        OVERRIDE: _testo_override(_originale(manifesto), SCRIPT).encode("utf-8"),
        SCRIPT: SCRIPT_GD.encode("utf-8"),
        DIZIONARIO: json.dumps({"lingua": lingua_tabella.replace("-", "_"),
                                "aggiunte": [destinazione.replace("-", "_")],
                                "testi": dizionario}, ensure_ascii=False, indent=0, sort_keys=True).encode("utf-8"),
    }
    manifesto["data"] = datetime.datetime.now().isoformat(timespec="seconds")
    scrivi_atomico(os.path.join(cartella, MANIFESTO), json.dumps(dict(manifesto, in_corso=True), indent=2).encode())
    for nome, dati in contenuti.items():
        scrivi_atomico(os.path.join(cartella, nome), dati)
        manifesto["file"][nome] = hashlib.sha256(dati).hexdigest()
    scrivi_atomico(os.path.join(cartella, MANIFESTO), json.dumps(manifesto, indent=2).encode())
    log("  Traduzione per Godot accanto all'eseguibile: %d testi (override.cfg, %s, %s)" % (
        len(dizionario), SCRIPT, DIZIONARIO))
    return len(dizionario)


def disinstalla(cartella, log=print):
    """Toglie i file di PatchSmith (quelli cambiati a mano restano) e rimette l'override.cfg del gioco."""
    manifesto = leggi_manifesto(cartella)
    if manifesto is None:
        return False
    for nome, impronta in manifesto.get("file", {}).items():
        p = os.path.join(cartella, nome)
        if os.path.exists(p):
            if _sha256(p) == impronta:
                os.remove(p)
            else:
                log("  ! %s e' stato cambiato dopo la traduzione: lo lascio" % nome)
    if manifesto.get("override_originale") is not None:
        scrivi_atomico(os.path.join(cartella, OVERRIDE), base64.b64decode(manifesto["override_originale"]))
        log("  override.cfg del gioco rimesso com'era")
    os.remove(os.path.join(cartella, MANIFESTO))
    log("  Traduzione per Godot tolta dalla cartella del gioco")
    return True
