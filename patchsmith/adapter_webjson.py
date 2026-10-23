"""Adapter web-json: estrae e reinserisce i testi nei file JSON (giochi Electron/NW.js/HTML5,
RPG Maker MV/MZ e qualsiasi gioco che tenga i testi in JSON).

I campi si selezionano per "schema di percorso", per esempio rooms[].rewards[].text.
"""
import copy
import json
import os
import re

from .text import e_testo_leggibile

# Tutti i file creati da PatchSmith nella cartella del gioco contengono questo marcatore nel nome.
MARCATORE = "patchsmith-"
SUFFISSO_TMP = ".patchsmith-tmp"

# Ultime chiavi che quasi sempre contengono dati tecnici o crediti, non testo da tradurre.
CHIAVI_TECNICHE = {
    "id", "uid", "uuid", "guid", "key", "keys", "type", "kind", "class", "classname", "url", "uri",
    "href", "src", "path", "file", "filename", "image", "img", "icon", "sprite", "texture", "sound",
    "audio", "music", "bgm", "se", "font", "fontface", "color", "colour", "version", "author",
    "author_name", "email", "lang", "language", "locale", "code", "hash", "checksum", "token",
    "format", "mime", "encoding", "event", "action", "method", "script", "eval", "regex", "source",
}
# Chiavi che FINISCONO con un suffisso tecnico: achievementId, imageUrl, music_path, ruleType...
_SUFFISSO_TECNICO = re.compile(
    r"(?:[a-z0-9](?:Id|ID|Ids|IDs|Key|Keys|Url|URL|Uri|Path|File|Color|Colour|Hash|Type|Code)"
    r"|_(?:id|ids|key|keys|url|uri|path|file|color|colour|hash|type|code))$"
)


# --- JSON "tollerante": commenti // e /* */ e virgole in fondo (es. Another Door: localization/stringsEN.json) -----
# Molti giochi leggono i loro JSON con un lettore che accetta commenti e virgole prima di } o ]. PatchSmith li legge
# togliendo commenti e virgole (al loro posto spazi: le posizioni restano le stesse) e li riscrive sostituendo nel
# testo originale solo i letterali delle stringhe tradotte: commenti, spazi, a capo e tutto il resto restano identici
# byte per byte.

_STRINGA = r'"[^"\\]*(?:\\.[^"\\]*)*"'
_STRINGA_O_COMMENTO = re.compile(_STRINGA + r"|//[^\n]*|/\*.*?(?:\*/|\Z)", re.S)
_STRINGA_O_VIRGOLA = re.compile(_STRINGA + r"|,(?=\s*[}\]])", re.S)


def _spazi(pezzo):
    """Il pezzo coperto da spazi, tenendo gli a capo (stesse posizioni e stesse righe nei messaggi di errore)."""
    return re.sub(r"[^\r\n]", " ", pezzo)


def senza_commenti(testo):
    """Il testo JSON senza commenti // e /* */ e senza virgole prima di } o ] (fuori dalle stringhe), con degli spazi
    al loro posto: ogni carattere rimasto e' nella stessa posizione dell'originale."""
    testo = _STRINGA_O_COMMENTO.sub(lambda m: m.group(0) if m.group(0).startswith('"') else _spazi(m.group(0)), testo)
    return _STRINGA_O_VIRGOLA.sub(lambda m: m.group(0) if m.group(0).startswith('"') else " ", testo)


def leggi_tollerante(testo):
    """I dati di un testo JSON, anche con commenti e virgole in fondo. Solleva ValueError se non e' JSON."""
    return json.loads(senza_commenti(testo))


_SPAZI_JSON = re.compile(r"[ \t\n\r]*")
_DECODIFICATORE = json.JSONDecoder()


def posizioni_stringhe(testo):
    """{percorso: (inizio, fine)} dei letterali delle stringhe che sono valori (non chiavi) in un testo JSON gia'
    senza commenti (vedi senza_commenti). Con chiavi ripetute vale l'ultima, come per json.loads."""
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
            _, fine = _DECODIFICATORE.scan_once(testo, i)  # numeri, true, false, null
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
    """Un file JSON letto dal disco, con le informazioni per riscriverlo nello stesso formato. Un file con commenti o
    virgole in fondo (tollerante=True) si riscrive sostituendo solo i letterali delle stringhe cambiate."""

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
        """Stesso formato dell'originale. Solleva ValueError se servirebbe NaN/Infinity (JSON non valido)."""
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
        """Il testo originale con i soli letterali delle stringhe cambiate riscritti; il resto (commenti, spazi,
        virgole in fondo, numeri scritti com'erano) resta identico. Solleva ValueError se e' cambiato qualcosa che
        non e' una stringa, o se il risultato riletto non da' 'dati'."""
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
    """Rilegge con le regole di JSON.parse (niente NaN/Infinity). Solleva ValueError se non valido. tollerante: il
    file originale aveva commenti o virgole in fondo (FileJSON.tollerante), che il gioco accetta: si tolgono prima."""
    testo = dati_byte.decode("utf-8-sig")
    return json.loads(senza_commenti(testo) if tollerante else testo, parse_constant=_rifiuta_costante)


def foglie(dati, percorso=(), schema=""):
    """Genera (percorso_tupla, schema, valore) per ogni stringa del JSON."""
    if isinstance(dati, dict):
        for k, v in dati.items():
            yield from foglie(v, percorso + (k,), (schema + "." + k) if schema else k)
    elif isinstance(dati, list):
        for i, v in enumerate(dati):
            yield from foglie(v, percorso + (i,), schema + "[]")
    elif isinstance(dati, str):
        yield percorso, schema, dati


def statistiche_schemi(dati, max_esempi=3):
    """Per ogni schema: quante stringhe, quante leggibili, caratteri, esempi."""
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
    """Divide gli schemi in (includi, escludi) con regole generiche."""
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
    """[(percorso_tupla, testo)] delle stringhe da tradurre ('leggibile' decide quali valori sono testo)."""
    includi = set(includi)
    return [(p, v) for p, schema, v in foglie(dati) if schema in includi and leggibile(v)]


def applica(dati, sostituzioni):
    """Copia dei dati con le stringhe sostituite. sostituzioni: {percorso_tupla: nuovo_testo}."""
    nuovi = copy.deepcopy(dati)
    for percorso, testo in sostituzioni.items():
        nodo = nuovi
        for passo in percorso[:-1]:
            nodo = nodo[passo]
        nodo[percorso[-1]] = testo
    return nuovi


def verifica_struttura(originale, nuovo, consentiti, percorso=()):
    """Elenco dei problemi: la struttura deve essere identica, cambiano solo le stringhe consentite."""
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
    except BaseException:  # nessun temporaneo lasciato accanto al file del gioco
        if os.path.exists(temporaneo):
            try:
                os.remove(temporaneo)
            except OSError:
                pass
        raise
