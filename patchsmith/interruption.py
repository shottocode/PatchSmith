"""Request to stop the work in progress (the interface's "Stop" button).

The work checks the request at safe points (before each group of texts and each request to the translator, and
during the waits between one attempt and the next). Interrotto derives from BaseException, like KeyboardInterrupt:
blocks that skip an error and carry on ("except Exception") do not stop it, while those that put things back in
place ("except BaseException" / finally) do so and then let it through.
"""
import threading
import time


class Interrotto(BaseException):
    """Work stopped at the user's request."""


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
    """Like time.sleep, but stops at once if a stop is requested. Waits in short steps: on Windows a long wait on
    an Event would not let Ctrl+C through until the end."""
    fine = time.monotonic() + max(0.0, secondi)
    while True:
        resto = fine - time.monotonic()
        if resto <= 0:
            return
        if _richiesta.wait(min(resto, 0.2)):
            raise Interrotto("fermato su tua richiesta")
