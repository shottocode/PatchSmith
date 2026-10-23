"""Richiesta di fermare il lavoro in corso (tasto "Ferma" dell'interfaccia).

Il lavoro controlla la richiesta nei punti sicuri (prima di ogni gruppo di testi e di ogni richiesta al
traduttore, e durante le attese tra un tentativo e l'altro). Interrotto deriva da BaseException, come
KeyboardInterrupt: i blocchi che saltano un errore e vanno avanti ("except Exception") non lo fermano, mentre
quelli che rimettono le cose a posto ("except BaseException" / finally) lo fanno e poi lo lasciano passare.
"""
import threading
import time


class Interrotto(BaseException):
    """Lavoro fermato su richiesta dell'utente."""


_richiesta = threading.Event()


def richiedi():
    _richiesta.set()


def azzera():
    _richiesta.clear()


def richiesta():
    return _richiesta.is_set()


def controlla():
    if _richiesta.is_set():
        raise Interrotto("fermato su tua richiesta")


def attendi(secondi):
    """Come time.sleep, ma si interrompe subito se viene chiesto di fermarsi. Aspetta a passi brevi: su Windows
    un'attesa lunga su un Event non lascerebbe passare Ctrl+C fino alla fine."""
    fine = time.monotonic() + max(0.0, secondi)
    while True:
        resto = fine - time.monotonic()
        if resto <= 0:
            return
        if _richiesta.wait(min(resto, 0.2)):
            raise Interrotto("fermato su tua richiesta")
