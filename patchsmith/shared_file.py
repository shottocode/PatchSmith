"""File JSON scritti da piu' programmi PatchSmith aperti insieme: l'interfaccia (o la riga di comando) e il traduttore
che il gioco avvia mentre si gioca (servizio_xunity). Sono la memoria delle traduzioni di un gioco, le sue traduzioni
automatiche (.automatiche.json) e la memoria comune a tutti i giochi (comune.<da>-<a>.json).

Prima ognuno riscriveva il file intero con la sua copia, e l'ultimo che salvava cancellava le frasi aggiunte
dall'altro. Ora chi salva prende il lucchetto <file>.lock, rilegge il file, ci applica solo quello che ha cambiato lui
(voci aggiunte, cambiate o tolte rispetto a quando l'aveva letto) e lo riscrive su un nome temporaneo tutto suo, che
poi rinomina: le modifiche degli altri restano.
"""
import json
import os
import threading
import time

ATTESA_LUCCHETTO = 30.0  # secondi: poi si salva comunque (meglio che bloccarsi)
ATTESA_APERTURA = 2.0  # secondi: un .lock che un altro programma sta togliendo non si apre per un attimo
TENTATIVI_RINOMINA = 10  # un file aperto in lettura da un altro programma non si sostituisce: si riprova
VECCHIO_TEMPORANEO = 60.0  # secondi: un .tmp piu' vecchio e' rimasto da un programma chiuso a meta' salvataggio

try:
    import msvcrt

    def _blocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _sblocca(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _TOGLI_LUCCHETTO = True  # su Windows un file aperto da altri non si cancella: si toglie solo se e' libero
except ImportError:  # non Windows (solo per completezza: PatchSmith gira su Windows)
    import fcntl

    def _blocca(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _sblocca(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)

    _TOGLI_LUCCHETTO = False


class Lucchetto:
    """Lucchetto tra programmi (e tra thread) su <percorso>.lock. E' un lucchetto di Windows sul file: se il programma
    che lo tiene si chiude di colpo, Windows lo libera da solo. Dopo 'attesa' secondi si procede senza (preso=False)."""

    def __init__(self, percorso, attesa=ATTESA_LUCCHETTO):
        self.percorso = percorso + ".lock"
        self.attesa = attesa
        self.preso = False
        self._fd = None

    def __enter__(self):
        # su Windows un .lock che l'altro programma sta togliendo (in cancellazione) per un attimo non si apre: si
        # riprova, se no i due salverebbero insieme e uno perderebbe le voci dell'altro
        apertura = time.monotonic() + min(self.attesa, ATTESA_APERTURA)
        while True:
            try:
                os.makedirs(os.path.dirname(self.percorso), exist_ok=True)
                self._fd = os.open(self.percorso, os.O_RDWR | os.O_CREAT)
                break
            except OSError:
                if time.monotonic() > apertura:  # cartella non scrivibile: anche il salvataggio non riuscira', e
                    self._fd = None  # lo dira' lui
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
                os.remove(self.percorso)  # non riesce se un altro lo sta aspettando: resta a lui
            except OSError:
                pass
        self.preso = False


def leggi_json(percorso):
    """Il contenuto di un file JSON. Se in quel momento un altro programma lo sta sostituendo (o l'antivirus lo
    tiene aperto), Windows rifiuta l'apertura: si riprova per circa un secondo."""
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
    """Scrittura su un nome temporaneo di questo programma e rinomina (mai file scritti a meta')."""
    temporaneo = "%s.%d-%d.tmp" % (percorso, os.getpid(), threading.get_ident())
    try:
        with open(temporaneo, "w", encoding="utf-8") as f:
            json.dump(dati, f, ensure_ascii=False, indent=2)
            f.write("\n")
        for tentativo in range(TENTATIVI_RINOMINA):
            try:
                os.replace(temporaneo, percorso)
                return
            except PermissionError:  # il file e' aperto da un altro programma (anche l'antivirus): si riprova
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
    """Salva nel file 'dati' (un dizionario) tenendo le modifiche fatte nel frattempo da altri programmi.

    base: il contenuto del file com'era quando 'dati' e' stato letto (o salvato l'ultima volta): si scrivono solo le
    voci aggiunte, cambiate o tolte rispetto a lei. Se due programmi cambiano la stessa voce vale l'ultimo.
    'dati' viene aggiornato con le voci degli altri. Restituisce il nuovo contenuto del file (o None se non c'era
    nulla da salvare)."""
    cambiate = {k: v for k, v in dati.items() if k not in base or base[k] != v}
    tolte = [k for k in base if k not in dati]
    if not cambiate and not tolte:
        return None
    with Lucchetto(percorso):
        try:
            unito = _leggi(percorso)
        except FileNotFoundError:
            unito = {}
        except ValueError:  # file rovinato (es. corretto a mano con un errore): si conserva a parte e si riscrive
            _metti_da_parte(percorso)  # con tutto quello che ha questo programma
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
    """Copia il file illeggibile in <file>.illeggibile (una volta per ogni versione rovinata): non si perde nulla."""
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
    """Un dizionario JSON condiviso con altri programmi: ricorda com'era il file all'ultima lettura o scrittura e
    salva solo le proprie modifiche (vedi salva_unendo)."""

    def __init__(self, percorso, dati):
        self.percorso = percorso
        self.base = dict(dati)

    def salva(self, dati):
        unito = salva_unendo(self.percorso, dati, self.base)
        if unito is not None:
            self.base = dict(unito)


def pulisci_temporanei(cartella, eta=VECCHIO_TEMPORANEO):
    """Toglie i .tmp lasciati in 'cartella' da un salvataggio interrotto (programma chiuso a meta'), piu' vecchi di
    'eta' secondi. Restituisce quanti ne ha tolti."""
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
