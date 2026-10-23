"""Language chosen in the saved options of a GameMaker game (e.g. Forager: %LOCALAPPDATA%\\Forager\\config.txt).

Many GameMaker games save their options with ds_map_write / ds_list_write: a JSON containing, in hexadecimal, a
map and some lists of values. In Forager the language is the first value of the "options" list ("english", "french"...
that is, the name of the language file without .json). PatchSmith, after adding a language (local/italiano.json),
sets it there, so the game starts already translated:
- only if it finds exactly ONE value equal to the name of one of the game's languages (no doubt about what to change);
- it changes only those bytes (length and text of the string), the rest of the file stays identical;
- it first checks that the decoding is exact (reading it back gives the same file), then reads back the result;
- backup copy in PatchSmith (save-backups/) and a record for the restore; never with the game open.
"""
import datetime
import json
import os
import re
import shutil
import struct

from . import gamemaker

_MAPPE = (0x191, 0x192, 0x193)  # ds_map_write headers (versions)
_LISTE = (0x12E, 0x12F)  # ds_list_write headers
_ESADECIMALE = re.compile(r"^(?:[0-9A-F]{2})+$|^(?:[0-9a-f]{2})+$")
_MASSIMO = 2 * 1024 * 1024


class ErroreFormato(ValueError):
    pass


def _valore(b, i):
    """(type, start, end, value) of the value that starts at i."""
    if i + 4 > len(b):
        raise ErroreFormato("valore oltre la fine")
    tipo = struct.unpack_from("<I", b, i)[0]
    if tipo == 1:  # string
        n = struct.unpack_from("<I", b, i + 4)[0]
        if i + 8 + n > len(b):
            raise ErroreFormato("stringa oltre la fine")
        return tipo, i, i + 8 + n, b[i + 8:i + 8 + n].decode("utf-8")
    dimensioni = {0: 8, 5: 0, 7: 4, 10: 8, 13: 8, 12: 0}  # real, undefined, int32, int64, bool, null
    if tipo not in dimensioni or i + 4 + dimensioni[tipo] > len(b):
        raise ErroreFormato("tipo di valore sconosciuto (%d)" % tipo)
    return tipo, i, i + 4 + dimensioni[tipo], None


def _voci(b, mappa):
    """The entries of a map ([(key, value)]) or of a list ([value]), with their positions in the bytes."""
    if len(b) < 8:
        raise ErroreFormato("troppo corto")
    intestazione, n = struct.unpack_from("<II", b, 0)
    if intestazione not in (_MAPPE if mappa else _LISTE) or n > 100000:
        raise ErroreFormato("intestazione sconosciuta")
    i, uscita = 8, []
    for _ in range(n):
        if mappa:
            k = _valore(b, i)
            v = _valore(b, k[2])
            uscita.append((k, v))
            i = v[2]
        else:
            v = _valore(b, i)
            uscita.append(v)
            i = v[2]
    if i != len(b):
        raise ErroreFormato("byte avanzati")
    return uscita


def _stringa(testo):
    dati = testo.encode("utf-8")
    return struct.pack("<II", 1, len(dati)) + dati


def _sostituisci(b, valore, nuovo):
    """The bytes with the string 'valore' (type, start, end, text) replaced by 'nuovo'."""
    return b[:valore[1]] + _stringa(nuovo) + b[valore[2]:]


def _esa(b, maiuscole):
    h = b.hex()
    return h.upper() if maiuscole else h


def trova(testo_json, lingue):
    """Where the language is in the file: [(JSON key, map key, index in the list, current language)].
    It looks, in the hexadecimal values of the JSON, for maps containing lists with a value equal to the name of
    one of the game's 'lingue'."""
    try:
        dati = json.loads(testo_json)
    except ValueError:
        return []
    if not isinstance(dati, dict):
        return []
    trovati = []
    for chiave_json, esa in dati.items():
        if not isinstance(esa, str) or len(esa) < 16 or not _ESADECIMALE.match(esa):
            continue
        try:
            b = bytes.fromhex(esa)
            voci = _voci(b, mappa=True)
        except (ErroreFormato, ValueError, struct.error, UnicodeDecodeError):
            continue
        for k, v in voci:
            if k[0] != 1 or v[0] != 1 or not _ESADECIMALE.match(v[3] or "") or len(v[3]) < 16:
                continue
            try:
                lista = _voci(bytes.fromhex(v[3]), mappa=False)
            except (ErroreFormato, ValueError, struct.error, UnicodeDecodeError):
                continue
            for indice, elemento in enumerate(lista):
                if elemento[0] == 1 and elemento[3] in lingue:
                    trovati.append((chiave_json, k[3], indice, elemento[3]))
    return trovati


def cambia(testo_json, posto, nuovo):
    """The text of the file with the language at 'posto' (from trova) changed to 'nuovo'. Touches only that string:
    the whole rest of the file stays identical (only the hexadecimal value is replaced, which is unique in the file)."""
    chiave_json, chiave_mappa, indice, _attuale = posto
    dati = json.loads(testo_json)
    esa = dati[chiave_json]
    b = bytes.fromhex(esa)
    voci = _voci(b, mappa=True)
    k, v = next((k, v) for k, v in voci if k[0] == 1 and k[3] == chiave_mappa)
    lista_b = bytes.fromhex(v[3])
    elemento = _voci(lista_b, mappa=False)[indice]
    nuova_lista = _sostituisci(lista_b, elemento, nuovo)
    maiuscole = v[3] == v[3].upper()
    nuova_mappa = _sostituisci(b, v, _esa(nuova_lista, maiuscole))
    nuovo_esa = _esa(nuova_mappa, esa == esa.upper())
    if testo_json.count(esa) != 1:
        raise ErroreFormato("il valore da cambiare non e' unico nel file")
    risultato = testo_json.replace(esa, nuovo_esa)
    controllo = trova(risultato, {nuovo})
    if [(c, m, i) for c, m, i, _ in controllo] != [(chiave_json, chiave_mappa, indice)]:
        raise ErroreFormato("controllo dopo la modifica non riuscito")
    return risultato


def cartelle_impostazioni(radice):
    """The folders in %LOCALAPPDATA% where the game may save its options (GameMaker uses the game's name; the
    names written in data.win and those of the executables are tried)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    nomi = []
    dati = next((os.path.join(radice, n) for n in gamemaker.NOMI_DATI if os.path.exists(os.path.join(radice, n))),
                None)
    if dati:
        try:
            nomi += gamemaker.nomi_del_gioco(gamemaker.leggi(dati))
        except (gamemaker.ErroreGameMaker, OSError, struct.error):
            pass
    nomi += [os.path.splitext(os.path.basename(e))[0] for e in gamemaker.eseguibili(radice)]
    cartelle = []
    for nome in dict.fromkeys(nomi):
        if nome and not re.search(r'[\\/:*?"<>|]', nome) and os.path.isdir(os.path.join(base, nome)):
            cartelle.append(os.path.join(base, nome))
    return cartelle


def cerca(radice, lingue):
    """(file, place) of the only language saved in the game's options, or (None, reason)."""
    cartelle = cartelle_impostazioni(radice)
    if not cartelle:
        return None, "il gioco non ha ancora salvato le sue opzioni (avvialo una volta)"
    trovati = []
    for cartella, nome in [(c, n) for c in cartelle for n in sorted(os.listdir(c))]:
        percorso = os.path.join(cartella, nome)
        if not os.path.isfile(percorso) or os.path.getsize(percorso) > _MASSIMO \
                or not nome.lower().endswith((".txt", ".json", ".ini", ".dat", ".cfg", ".sav")):
            continue
        try:
            with open(percorso, encoding="utf-8") as f:
                testo = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        trovati += [(percorso, posto) for posto in trova(testo, lingue)]
    if len(trovati) != 1:
        return None, ("non trovo dove il gioco salva la lingua" if not trovati else
                      "la lingua compare in piu' punti delle opzioni salvate: non tocco nulla")
    return trovati[0]


def imposta(radice, nuova, lingue, salvataggi, log=print, rispetta=None):
    """Sets 'nuova' as the game's language in the saved options. Returns (record for the restore, None)
    or (None, reason). 'salvataggi': folder where the file is copied before changing it. 'rispetta': if the
    saved language differs from this one (PatchSmith's choice from last time), the player changed it and it is
    left alone."""
    percorso, posto = cerca(radice, set(lingue) | {nuova})
    if percorso is None:
        return None, posto
    if posto[3] == nuova:
        return {"tipo": "gamemaker_opzioni", "file": percorso, "posto": list(posto[:3]), "precedente": None,
                "scritto": nuova}, None
    if rispetta and posto[3] != rispetta:
        return None, "nel gioco hai scelto la lingua '%s': la lascio (la traduzione e' la lingua '%s')" % (
            posto[3], nuova)
    with open(percorso, encoding="utf-8", newline="") as f:
        testo = f.read()
    nuovo = cambia(testo, posto, nuova)
    os.makedirs(salvataggi, exist_ok=True)
    copia = os.path.join(salvataggi, "%s-%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"),
                                                 os.path.basename(percorso)))
    shutil.copyfile(percorso, copia)
    from .adapter_webjson import scrivi_atomico
    scrivi_atomico(percorso, nuovo.encode("utf-8"))
    log("  Lingua del gioco impostata su '%s' nelle sue opzioni (%s; copia di prima in %s)" % (nuova, percorso, copia))
    return {"tipo": "gamemaker_opzioni", "file": percorso, "posto": list(posto[:3]), "precedente": posto[3],
            "scritto": nuova, "copia": copia}, None


def ripristina(registro):
    """Puts back the previous language if the options still hold the one written by PatchSmith. True if put back,
    False if the player has changed it in the meantime (their choice is kept)."""
    percorso = registro["file"]
    if not registro.get("precedente") or not os.path.isfile(percorso):
        return False  # options deleted or recreated by the game: there is nothing to put back
    with open(percorso, encoding="utf-8", newline="") as f:
        testo = f.read()
    chiave_json, chiave_mappa, indice = registro["posto"]
    attuali = [p for p in trova(testo, {registro["scritto"]}) if tuple(p[:3]) == (chiave_json, chiave_mappa, indice)]
    if not attuali:
        return False
    try:
        nuovo = cambia(testo, attuali[0], registro["precedente"])
    except ErroreFormato:
        return False  # file rewritten in a way I do not recognize: leave it as it is
    from .adapter_webjson import scrivi_atomico
    scrivi_atomico(percorso, nuovo.encode("utf-8"))
    return True
