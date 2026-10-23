"""Lingua scelta nelle opzioni salvate di un gioco GameMaker (es. Forager: %LOCALAPPDATA%\\Forager\\config.txt).

Molti giochi GameMaker salvano le opzioni con ds_map_write / ds_list_write: un JSON con dentro, in esadecimale, una
mappa e delle liste di valori. In Forager la lingua e' il primo valore della lista "options" ("english", "french"...
cioe' il nome del file di lingua senza .json). PatchSmith, dopo aver aggiunto una lingua (local/italiano.json), la
imposta li', cosi' il gioco parte gia' tradotto:
- solo se trova UN solo valore uguale al nome di una lingua del gioco (nessun dubbio su cosa cambiare);
- modifica solo quei byte (lunghezza e testo della stringa), il resto del file resta identico;
- prima controlla che la decodifica sia esatta (rileggendo si ottiene lo stesso file), poi rilegge il risultato;
- copia di sicurezza in PatchSmith (salvataggi/) e registro per il ripristino; mai con il gioco aperto.
"""
import datetime
import json
import os
import re
import shutil
import struct

from . import gamemaker

_MAPPE = (0x191, 0x192, 0x193)  # intestazioni di ds_map_write (versioni)
_LISTE = (0x12E, 0x12F)  # intestazioni di ds_list_write
_ESADECIMALE = re.compile(r"^(?:[0-9A-F]{2})+$|^(?:[0-9a-f]{2})+$")
_MASSIMO = 2 * 1024 * 1024


class ErroreFormato(ValueError):
    pass


def _valore(b, i):
    """(tipo, inizio, fine, valore) del valore che comincia in i."""
    if i + 4 > len(b):
        raise ErroreFormato("valore oltre la fine")
    tipo = struct.unpack_from("<I", b, i)[0]
    if tipo == 1:  # stringa
        n = struct.unpack_from("<I", b, i + 4)[0]
        if i + 8 + n > len(b):
            raise ErroreFormato("stringa oltre la fine")
        return tipo, i, i + 8 + n, b[i + 8:i + 8 + n].decode("utf-8")
    dimensioni = {0: 8, 5: 0, 7: 4, 10: 8, 13: 8, 12: 0}  # reale, undefined, int32, int64, bool, null
    if tipo not in dimensioni or i + 4 + dimensioni[tipo] > len(b):
        raise ErroreFormato("tipo di valore sconosciuto (%d)" % tipo)
    return tipo, i, i + 4 + dimensioni[tipo], None


def _voci(b, mappa):
    """Le voci di una mappa ([(chiave, valore)]) o di una lista ([valore]), con le posizioni nei byte."""
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
    """I byte con la stringa 'valore' (tipo, inizio, fine, testo) sostituita da 'nuovo'."""
    return b[:valore[1]] + _stringa(nuovo) + b[valore[2]:]


def _esa(b, maiuscole):
    h = b.hex()
    return h.upper() if maiuscole else h


def trova(testo_json, lingue):
    """Dove sta la lingua nel file: [(chiave JSON, chiave della mappa, indice nella lista, lingua attuale)].
    Si cercano, nei valori esadecimali del JSON, mappe che contengono liste con un valore uguale al nome di una
    delle 'lingue' del gioco."""
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
    """Il testo del file con la lingua in 'posto' (da trova) cambiata in 'nuovo'. Tocca solo quella stringa:
    tutto il resto del file resta identico (si sostituisce il solo valore esadecimale, che e' unico nel file)."""
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
    """Le cartelle in %LOCALAPPDATA% dove il gioco puo' salvare le opzioni (GameMaker usa il nome del gioco; si
    provano i nomi scritti in data.win e quelli degli eseguibili)."""
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
    """(file, posto) dell'unica lingua salvata nelle opzioni del gioco, oppure (None, motivo)."""
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
    """Imposta 'nuova' come lingua del gioco nelle opzioni salvate. Restituisce (registro per il ripristino, None)
    oppure (None, motivo). 'salvataggi': cartella dove copiare il file prima di modificarlo. 'rispetta': se la
    lingua salvata e' diversa da questa (la scelta dell'ultima volta di PatchSmith), l'ha cambiata il giocatore e
    non si tocca."""
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
    """Rimette la lingua di prima se nelle opzioni c'e' ancora quella scritta da PatchSmith. True se rimessa,
    False se nel frattempo il giocatore l'ha cambiata (si lascia la sua scelta)."""
    percorso = registro["file"]
    if not registro.get("precedente") or not os.path.isfile(percorso):
        return False  # opzioni cancellate o rifatte dal gioco: non c'e' niente da rimettere
    with open(percorso, encoding="utf-8", newline="") as f:
        testo = f.read()
    chiave_json, chiave_mappa, indice = registro["posto"]
    attuali = [p for p in trova(testo, {registro["scritto"]}) if tuple(p[:3]) == (chiave_json, chiave_mappa, indice)]
    if not attuali:
        return False
    try:
        nuovo = cambia(testo, attuali[0], registro["precedente"])
    except ErroreFormato:
        return False  # file riscritto in un modo che non riconosco: lo lascio com'e'
    from .adapter_webjson import scrivi_atomico
    scrivi_atomico(percorso, nuovo.encode("utf-8"))
    return True
