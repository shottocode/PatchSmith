"""JSON files written by several PatchSmith programs open at the same time: the interface (or the command line) and
the translator the game starts while it is played (servizio_xunity). They are a game's translation memory, its machine
translations (.automatiche.json) and the translation memory shared by all games (comune.<da>-<a>.json).

Before, each one rewrote the whole file with its own copy, and the last one to save erased the sentences added by the
other. Now whoever saves takes the <file>.lock lock, reads the file again, applies to it only what it changed itself
(entries added, changed or removed since it read it) and writes it to a temporary name of its own, which it then
renames: the others' changes stay.
"""
import json
import os
import threading
import time

ATTESA_LUCCHETTO = 30.0  # seconds: then it saves anyway (better than getting stuck)
ATTESA_APERTURA = 2.0  # seconds: a .lock that another program is removing cannot be opened for a moment
TENTATIVI_RINOMINA = 10  # a file opened for reading by another program cannot be replaced: try again
VECCHIO_TEMPORANEO = 60.0  # seconds: an older .tmp was left by a program closed in the middle of a save

try:
    import msvcrt

    def _blocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _sblocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _TOGLI_LUCCHETTO = True  # on Windows a file opened by others cannot be deleted: remove it only if it is free
except ImportError:  # not Windows (only for completeness: PatchSmith runs on Windows)
    import fcntl

    def _blocca(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _sblocca(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)

    _TOGLI_LUCCHETTO = False


class Lucchetto:
    """Lock between programs (and between threads) on <percorso>.lock. It is a Windows lock on the file: if the program
    holding it closes suddenly, Windows releases it by itself. After 'attesa' seconds it goes on without it
    (preso=False)."""

    def __init__(self, percorso, attesa=ATTESA_LUCCHETTO):
        self.percorso = percorso + ".lock"
        self.attesa = attesa
        self.preso = False
        self._fd = None

    def __enter__(self):
        # on Windows a .lock that the other program is removing (being deleted) cannot be opened for a moment: try
        # again, otherwise the two would save together and one would lose the other's entries
        apertura = time.monotonic() + min(self.attesa, ATTESA_APERTURA)
        while True:
            try:
                os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
                self._fd = os.open(self.percorso, os.O_RDWR | os.O_CREAT)
                break
            except OSError:
                if time.monotonic() > apertura:  # folder not writable: the save will fail too, and it will
                    self._fd = None  # report it
                    return self
                time.sleep(0.01)
        limite = time.monotonic() + self.attesa
        while True:
            try:
                _blocca(self._fd)
                self.preso = True
                return self
            except OSError:
                if time.monotonic() > limite:
                    return self
                time.sleep(0.02)

    def __exit__(self, *_):
        if self._fd is None:
            return
        try:
            if self.preso:
                _sblocca(self._fd)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None
        if self.preso and _TOGLI_LUCCHETTO:
            try:
                os.remove(self.percorso)  # fails if another one is waiting for it: it stays for that one
            except OSError:
                pass
        self.preso = False


def leggi_json(percorso):
    """The content of a JSON file. If at that moment another program is replacing it (or the antivirus is keeping it
    open), Windows refuses to open it: try again for about a second."""
    for tentativo in range(TENTATIVI_RINOMINA):
        try:
            with open(percorso, encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if tentativo == TENTATIVI_RINOMINA - 1:
                raise
            time.sleep(0.1)


def _leggi(percorso):
    dati = leggi_json(percorso)
    if not isinstance(dati, dict):
        raise ValueError("non e' un elenco di traduzioni")
    return dati


def _scrivi(percorso, dati):
    """Writes to a temporary name of this program and renames (never half-written files)."""
    temporaneo = "%s.%d-%d.tmp" % (percorso, os.getpid(), threading.get_ident())
    try:
        with open(temporaneo, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)
            f.write("\n")
        for tentativo in range(TENTATIVI_RINOMINA):
            try:
                os.replace(temporaneo, percorso)
                return
            except PermissionError:  # the file is open in another program (the antivirus too): try again
                if tentativo == TENTATIVI_RINOMINA - 1:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(temporaneo):
            try:
                os.remove(temporaneo)
            except OSError:
                pass


def salva_unendo(percorso, dati, base):
    """Saves 'dati' (a dictionary) to the file, keeping the changes made in the meantime by other programs.

    base: the content of the file as it was when 'dati' was read (or last saved): only the entries added, changed or
    removed compared to it are written. If two programs change the same entry, the last one wins.
    'dati' is updated with the others' entries. Returns the new content of the file (or None if there was nothing to
    save)."""
    cambiate = {k: v for k, v in dati.items() if k not in base or base[k] != v}
    tolte = [k for k in base if k not in dati]
    if not cambiate and not tolte:
        return None
    with Lucchetto(percorso):
        try:
            unito = _leggi(percorso)
        except FileNotFoundError:
            unito = {}
        except ValueError:  # damaged file (e.g. edited by hand with a mistake): keep it aside and rewrite it
            _metti_da_parte(percorso)  # with everything this program has
            unito = dict(dati)
        unito.update(cambiate)
        for k in tolte:
            unito.pop(k, None)
        _scrivi(percorso, unito)
    for k in [k for k in dati if k not in unito]:
        del dati[k]
    dati.update(unito)
    return unito


def _metti_da_parte(percorso):
    """Copies the unreadable file to <file>.illeggibile (once for each damaged version): nothing is lost."""
    try:
        with open(percorso, "rb") as f:
            grezzo = f.read()
        copia = percorso + ".illeggibile"
        if not os.path.exists(copia) or os.path.getsize(copia) != len(grezzo):
            with open(copia, "wb") as f:
                f.write(grezzo)
    except OSError:
        pass


class FileUnito:
    """A JSON dictionary shared with other programs: remembers how the file was at the last read or write and saves
    only its own changes (see salva_unendo)."""

    def __init__(self, percorso, dati):
        self.percorso = percorso
        self.base = dict(dati)

    def salva(self, dati):
        unito = salva_unendo(self.percorso, dati, self.base)
        if unito is not None:
            self.base = dict(unito)


def pulisci_temporanei(cartella, eta=VECCHIO_TEMPORANEO):
    """Removes the .tmp files left in 'cartella' by an interrupted save (program closed halfway), older than 'eta'
    seconds. Returns how many it removed."""
    tolti = 0
    try:
        nomi = os.listdir(cartella)
    except OSError:
        return 0
    adesso = time.time()
    for nome in nomi:
        p = os.path.join(cartella, nome)
        if not nome.endswith(".tmp") or not os.path.isfile(p):
            continue
        try:
            if adesso - os.path.getmtime(p) > eta:
                os.remove(p)
                tolti += 1
        except OSError:
            pass
    return tolti
