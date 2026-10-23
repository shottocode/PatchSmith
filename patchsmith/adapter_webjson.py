"""Web-json adapter: extracts and reinserts the texts in JSON files (Electron/NW.js/HTML5 games,
RPG Maker MV/MZ and any game that keeps its texts in JSON).

Fields are selected by "path schema", for example rooms[].rewards[].text.
"""
import copy
import json
import os
import re

from .text import e_testo_leggibile

# All the files PatchSmith creates in the game's folder contain this marker in their name.
MARCATORE = "patchsmith-"
SUFFISSO_TMP = ".patchsmith-tmp"

# Last keys that almost always hold technical data or credits, not text to translate.
CHIAVI_TECNICHE = {
    "id", "uid", "uuid", "guid", "key", "keys", "type", "kind", "class", "classname", "url", "uri",
    "href", "src", "path", "file", "filename", "image", "img", "icon", "sprite", "texture", "sound",
    "audio", "music", "bgm", "se", "font", "fontface", "color", "colour", "version", "author",
    "author_name", "email", "lang", "language", "locale", "code", "hash", "checksum", "token",
    "format", "mime", "encoding", "event", "action", "method", "script", "eval", "regex", "source",
}
# Keys that END with a technical suffix: achievementId, imageUrl, music_path, ruleType...
_SUFFISSO_TECNICO = re.compile(
    r"(?:[a-z0-9](?:Id|ID|Ids|IDs|Key|Keys|Url|URL|Uri|Path|File|Color|Colour|Hash|Type|Code)"
    r"|_(?:id|ids|key|keys|url|uri|path|file|color|colour|hash|type|code))$"
)


# --- "Tolerant" JSON: // and /* */ comments and trailing commas (e.g. Another Door: localization/stringsEN.json) ---
# Many games read their JSON with a reader that accepts comments and commas before } or ]. PatchSmith reads them
# removing comments and commas (spaces in their place: positions stay the same) and rewrites them replacing in the
# original text only the literals of the translated strings: comments, spaces, newlines and everything else stay
# identical byte for byte.

_STRINGA = r'"[^"\\]*(?:\\.[^"\\]*)*"'
_STRINGA_O_COMMENTO = re.compile(_STRINGA + r"|//[^\n]*|/\*.*?(?:\*/|\Z)", re.S)
_STRINGA_O_VIRGOLA = re.compile(_STRINGA + r"|,(?=\s*[}\]])", re.S)


def _spazi(pezzo):
    """The piece covered with spaces, keeping the newlines (same positions and same lines in error messages)."""
    return re.sub(r"[^\r\n]", " ", pezzo)


def senza_commenti(testo):
    """The JSON text without // and /* */ comments and without commas before } or ] (outside strings), with spaces
    in their place: every remaining character is in the same position as in the original."""
    testo = _STRINGA_O_COMMENTO.sub(lambda m: m.group(0) if m.group(0).startswith('"') else _spazi(m.group(0)), testo)
    return _STRINGA_O_VIRGOLA.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " ", testo)


def leggi_tollerante(testo):
    """The data of a JSON text, even with comments and trailing commas. Raises ValueError if it is not JSON."""
    return json.loads(senza_commenti(testo))


_SPAZI_JSON = re.compile(r"[ \t\n\r]*")
_DECODIFICATORE = json.JSONDecoder()


def posizioni_stringhe(testo):
    """{percorso: (inizio, fine)} of the string literals that are values (not keys) in a JSON text already
    without comments (see senza_commenti). With repeated keys the last one wins, as with json.loads."""
    uscita = {}

    def spazi(i):
        return _SPAZI_JSON.match(testo, i).end()

    def valore(i, percorso):
        i = spazi(i)
        c = testo[i:i + 1]
        if c == "{":
            i = spazi(i + 1)
            if testo[i:i + 1] == "}":
                return i + 1
            while True:
                if testo[i:i + 1] != '"':
                    raise ValueError("chiave attesa alla posizione %d" % i)
                chiave, i = json.decoder.scanstring(testo, i + 1)
                i = spazi(i)
                if testo[i:i + 1] != ":":
                    raise ValueError("':' atteso alla posizione %d" % i)
                i = spazi(valore(i + 1, percorso + (chiave,)))
                if testo[i:i + 1] == ",":
                    i = spazi(i + 1)
                    continue
                if testo[i:i + 1] == "}":
                    return i + 1
                raise ValueError("',' o '}' atteso alla posizione %d" % i)
        if c == "[":
            i = spazi(i + 1)
            if testo[i:i + 1] == "]":
                return i + 1
            n = 0
            while True:
                i = spazi(valore(i, percorso + (n,)))
                n += 1
                if testo[i:i + 1] == ",":
                    i += 1
                    continue
                if testo[i:i + 1] == "]":
                    return i + 1
                raise ValueError("',' o ']' atteso alla posizione %d" % i)
        if c == '"':
            _, fine = json.decoder.scanstring(testo, i + 1)
            uscita[percorso] = (i, fine)
            return fine
        try:
            _, fine = _DECODIFICATORE.scan_once(testo, i)  # numbers, true, false, null
        except StopIteration:
            raise ValueError("valore atteso alla posizione %d" % i) from None
        return fine

    try:
        valore(0, ())
    except RecursionError:
        raise ValueError("JSON troppo annidato") from None
    return uscita


def _al_percorso(dati, percorso):
    for passo in percorso:
        dati = dati[passo]
    return dati


class FileJSON:
    """A JSON file read from disk, with the information to rewrite it in the same format. A file with comments or
    trailing commas (tollerante=True) is rewritten replacing only the literals of the changed strings."""

    def __init__(self, percorso):
        self.percorso = percorso
        with open(percorso, "rb") as f:
            grezzo = f.read()
        self.bom = grezzo.startswith(b"\xef\xbb\xbf")
        testo = grezzo.decode("utf-8-sig")
        self.tollerante = False
        try:
            self.dati = json.loads(testo)
        except ValueError as e:
            try:
                self.dati = leggi_tollerante(testo)
            except ValueError:
                raise e from None
            self.tollerante = True
            self._testo = testo
        m = re.search(r"\n([ \t]+)\S", testo)
        self.indent = (m.group(1) if m.group(1).startswith("\t") else len(m.group(1))) if m else None
        self.solo_ascii = not re.search(r"[^\x00-\x7f]", testo) and "\\u" in testo
        self.a_capo_finale = testo.endswith("\n")

    def serializza(self, dati):
        """Same format as the original. Raises ValueError if NaN/Infinity would be needed (invalid JSON)."""
        if self.tollerante:
            return self._sostituisci(dati)
        if self.indent is None:
            testo = json.dumps(dati, ensure_ascii=self.solo_ascii, separators=(",", ":"), allow_nan=False)
        else:
            testo = json.dumps(dati, ensure_ascii=self.solo_ascii, indent=self.indent, allow_nan=False)
        if self.a_capo_finale:
            testo += "\n"
        dati_byte = testo.encode("utf-8")
        return (b"\xef\xbb\xbf" + dati_byte) if self.bom else dati_byte

    def _sostituisci(self, dati):
        """The original text with only the literals of the changed strings rewritten; the rest (comments, spaces,
        trailing commas, numbers written as they were) stays identical. Raises ValueError if something that is not
        a string changed, or if the result, read back, does not give 'dati'."""
        pezzi, ultimo = [], 0
        for percorso, (inizio, fine) in sorted(posizioni_stringhe(senza_commenti(self._testo)).items(),
                                               key=lambda x: x[1][0]):
            try:
                prima, dopo = _al_percorso(self.dati, percorso), _al_percorso(dati, percorso)
            except (KeyError, IndexError, TypeError):
                raise ValueError("struttura cambiata in %s" % (percorso,)) from None
            if dopo == prima:
                continue
            if not isinstance(dopo, str):
                raise ValueError("valore non stringa in %s" % (percorso,))
            pezzi += [self._testo[ultimo:inizio], json.dumps(dopo, ensure_ascii=self.solo_ascii)]
            ultimo = fine
        testo = "".join(pezzi) + self._testo[ultimo:]
        if leggi_tollerante(testo) != dati:
            raise ValueError("il file riscritto non torna: cambiato qualcosa che non e' una stringa")
        dati_byte = testo.encode("utf-8")
        return (b"\xef\xbb\xbf" + dati_byte) if self.bom else dati_byte


def _rifiuta_costante(nome):
    raise ValueError("valore non ammesso in JSON standard: %s" % nome)


def rileggi_rigoroso(dati_byte, tollerante=False):
    """Reads back with the rules of JSON.parse (no NaN/Infinity). Raises ValueError if invalid. tollerante: the
    original file had comments or trailing commas (FileJSON.tollerante), which the game accepts: removed first."""
    testo = dati_byte.decode("utf-8-sig")
    return json.loads(senza_commenti(testo) if tollerante else testo, parse_constant=_rifiuta_costante)


def foglie(dati, percorso=(), schema=""):
    """Yields (percorso_tupla, schema, valore) for every string of the JSON."""
    if isinstance(dati, dict):
        for k, v in dati.items():
            yield from foglie(v, percorso + (k,), (schema + "." + k) if schema else k)
    elif isinstance(dati, list):
        for i, v in enumerate(dati):
            yield from foglie(v, percorso + (i,), schema + "[]")
    elif isinstance(dati, str):
        yield percorso, schema, dati


def statistiche_schemi(dati, max_esempi=3):
    """For each schema: how many strings, how many readable, characters, examples."""
    stat = {}
    for _, schema, valore in foglie(dati):
        s = stat.setdefault(schema, {"stringhe": 0, "leggibili": 0, "caratteri": 0, "esempi": []})
        s["stringhe"] += 1
        if e_testo_leggibile(valore):
            s["leggibili"] += 1
            s["caratteri"] += len(valore)
            if len(s["esempi"]) < max_esempi:
                s["esempi"].append(valore[:90].replace("\n", " / "))
    return stat


def ultima_chiave(schema):
    return schema.replace("[]", "").rsplit(".", 1)[-1]


def chiave_tecnica(schema):
    chiave = ultima_chiave(schema)
    return chiave.lower() in CHIAVI_TECNICHE or bool(_SUFFISSO_TECNICO.search(chiave))


def proponi_schemi(stat):
    """Splits the schemas into (includi, escludi) with generic rules."""
    includi, escludi = [], []
    for schema, s in sorted(stat.items()):
        if s["leggibili"] == 0:
            continue
        if chiave_tecnica(schema):
            escludi.append(schema)
        else:
            includi.append(schema)
    return includi, escludi


def estrai(dati, includi, leggibile=e_testo_leggibile):
    """[(percorso_tupla, testo)] of the strings to translate ('leggibile' decides which values are text)."""
    includi = set(includi)
    return [(p, v) for p, schema, v in foglie(dati) if schema in includi and leggibile(v)]


def applica(dati, sostituzioni):
    """Copy of the data with the strings replaced. sostituzioni: {percorso_tupla: nuovo_testo}."""
    nuovi = copy.deepcopy(dati)
    for percorso, testo in sostituzioni.items():
        nodo = nuovi
        for passo in percorso[:-1]:
            nodo = nodo[passo]
        nodo[percorso[-1]] = testo
    return nuovi


def verifica_struttura(originale, nuovo, consentiti, percorso=()):
    """List of problems: the structure must be identical, only the allowed strings change."""
    problemi = []
    if type(originale) is not type(nuovo):
        return ["tipo diverso in %s" % (percorso,)]
    if isinstance(originale, dict):
        if list(originale.keys()) != list(nuovo.keys()):
            return ["chiavi diverse in %s" % (percorso,)]
        for k in originale:
            problemi += verifica_struttura(originale[k], nuovo[k], consentiti, percorso + (k,))
    elif isinstance(originale, list):
        if len(originale) != len(nuovo):
            return ["lunghezza diversa in %s" % (percorso,)]
        for i, (a, b) in enumerate(zip(originale, nuovo)):
            problemi += verifica_struttura(a, b, consentiti, percorso + (i,))
    elif originale != nuovo and percorso not in consentiti:
        problemi.append("valore cambiato senza permesso in %s" % (percorso,))
    return problemi[:20]


def scrivi_atomico(percorso, dati_byte):
    temporaneo = percorso + SUFFISSO_TMP
    try:
        with open(temporaneo, "wb") as f:
            f.write(dati_byte)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporaneo, percorso)
    except BaseException:  # no temporary file left next to the game's file
        if os.path.exists(temporaneo):
            try:
                os.remove(temporaneo)
            except OSError:
                pass
        raise
