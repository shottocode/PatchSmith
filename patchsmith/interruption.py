"""Request to stop the work in progress (the interface's "Stop" button).

The work checks the request at safe points (before each group of texts and each request to the translator, and
during the waits between one attempt and the next). Interrupted derives from BaseException, like KeyboardInterrupt:
blocks that skip an error and carry on ("except Exception") do not stop it, while those that put things back in
place ("except BaseException" / finally) do so and then let it through.
"""
import threading
import time


class Interrupted(BaseException):
    """Work stopped at the user's request."""


_request = threading.Event()


def request_stop():
    _request.set()


def reset():
    _request.clear()


def request():
    return _request.is_set()


def check():
    if _request.is_set():
        raise Interrupted("stopped at your request")


def wait_for(seconds):
    """Like time.sleep, but stops at once if a stop is requested. Waits in short steps: on Windows a long wait on
    an Event would not let Ctrl+C through until the end."""
    end = time.monotonic() + max(0.0, seconds)
    while True:
        rest = end - time.monotonic()
        if rest <= 0:
            return
        if _request.wait(min(rest, 0.2)):
            raise Interrupted("stopped at your request")
