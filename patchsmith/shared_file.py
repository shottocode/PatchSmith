"""JSON files written by several PatchSmith programs open at the same time: the interface (or the command line) and
the translator the game starts while it is played (xunity_service). They are a game's translation memory, its machine
translations (.machine.json) and the translation memory shared by all games (comune.<da>-<a>.json).

Before, each one rewrote the whole file with its own copy, and the last one to save erased the sentences added by the
other. Now whoever saves takes the <file>.lock lock, reads the file again, applies to it only what it changed itself
(entries added, changed or removed since it read it) and writes it to a temporary name of its own, which it then
renames: the others' changes stay.
"""
import json
import os
import threading
import time

LOCK_WAIT = 30.0  # seconds: then it saves anyway (better than getting stuck)
OPEN_WAIT = 2.0  # seconds: a .lock that another program is removing cannot be opened for a moment
RENAME_ATTEMPTS = 10  # a file opened for reading by another program cannot be replaced: try again
OLD_TEMP_AGE = 60.0  # seconds: an older .tmp was left by a program closed in the middle of a save

try:
    import msvcrt

    def _lock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

    _REMOVE_LOCK = True  # on Windows a file opened by others cannot be deleted: remove it only if it is free
except ImportError:  # not Windows (only for completeness: PatchSmith runs on Windows)
    import fcntl

    def _lock(fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)

    _REMOVE_LOCK = False


class Lock:
    """Lock between programs (and between threads) on <percorso>.lock. It is a Windows lock on the file: if the program
    holding it closes suddenly, Windows releases it by itself. After 'attesa' seconds it goes on without it
    (preso=False)."""

    def __init__(self, path, wait=LOCK_WAIT):
        self.path = path + ".lock"
        self.wait = wait
        self.acquired = False
        self._fd = None

    def __enter__(self):
        # on Windows a .lock that the other program is removing (being deleted) cannot be opened for a moment: try
        # again, otherwise the two would save together and one would lose the other's entries
        open_deadline = time.monotonic() + min(self.wait, OPEN_WAIT)
        while True:
            try:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                self._fd = os.open(self.path, os.O_RDWR | os.O_CREAT)
                break
            except OSError:
                if time.monotonic() > open_deadline:  # folder not writable: the save will fail too, and it will
                    self._fd = None  # report it
                    return self
                time.sleep(0.01)
        limit = time.monotonic() + self.wait
        while True:
            try:
                _lock(self._fd)
                self.acquired = True
                return self
            except OSError:
                if time.monotonic() > limit:
                    return self
                time.sleep(0.02)

    def __exit__(self, *_):
        if self._fd is None:
            return
        try:
            if self.acquired:
                _unlock(self._fd)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None
        if self.acquired and _REMOVE_LOCK:
            try:
                os.remove(self.path)  # fails if another one is waiting for it: it stays for that one
            except OSError:
                pass
        self.acquired = False


def read_json(path):
    """The content of a JSON file. If at that moment another program is replacing it (or the antivirus is keeping it
    open), Windows refuses to open it: try again for about a second."""
    for attempt in range(RENAME_ATTEMPTS):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except PermissionError:
            if attempt == RENAME_ATTEMPTS - 1:
                raise
            time.sleep(0.1)


def _read(path):
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("this is not a list of translations")
    return data


def _write(path, data):
    """Writes to a temporary name of this program and renames (never half-written files)."""
    temp_file = "%s.%d-%d.tmp" % (path, os.getpid(), threading.get_ident())
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        for attempt in range(RENAME_ATTEMPTS):
            try:
                os.replace(temp_file, path)
                return
            except PermissionError:  # the file is open in another program (the antivirus too): try again
                if attempt == RENAME_ATTEMPTS - 1:
                    raise
                time.sleep(0.1)
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


def save_merging(path, data, base):
    """Saves 'data' (a dictionary) to the file, keeping the changes made in the meantime by other programs.

    base: the content of the file as it was when 'data' was read (or last saved): only the entries added, changed or
    removed compared to it are written. If two programs change the same entry, the last one wins.
    'data' is updated with the others' entries. Returns the new content of the file (or None if there was nothing to
    save)."""
    changed = {k: v for k, v in data.items() if k not in base or base[k] != v}
    removed = [k for k in base if k not in data]
    if not changed and not removed:
        return None
    with Lock(path):
        try:
            merged = _read(path)
        except FileNotFoundError:
            merged = {}
        except ValueError:  # damaged file (e.g. edited by hand with a mistake): keep it aside and rewrite it
            _set_aside(path)  # with everything this program has
            merged = dict(data)
        merged.update(changed)
        for k in removed:
            merged.pop(k, None)
        _write(path, merged)
    for k in [k for k in data if k not in merged]:
        del data[k]
    data.update(merged)
    return merged


def _set_aside(path):
    """Copies the unreadable file to <file>.illeggibile (once for each damaged version): nothing is lost."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        copy_dir = path + ".illeggibile"
        if not os.path.exists(copy_dir) or os.path.getsize(copy_dir) != len(raw):
            with open(copy_dir, "wb") as f:
                f.write(raw)
    except OSError:
        pass


class MergingFile:
    """A JSON dictionary shared with other programs: remembers how the file was at the last read or write and saves
    only its own changes (see save_merging)."""

    def __init__(self, path, data):
        self.path = path
        self.base = dict(data)

    def save(self, data):
        merged = save_merging(self.path, data, self.base)
        if merged is not None:
            self.base = dict(merged)


def clean_temp_files(folder, age=OLD_TEMP_AGE):
    """Removes the .tmp files left in 'folder' by an interrupted save (program closed halfway), older than 'eta'
    seconds. Returns how many it removed."""
    dropped = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0
    now = time.time()
    for name in names:
        p = os.path.join(folder, name)
        if not name.endswith(".tmp") or not os.path.isfile(p):
            continue
        try:
            if now - os.path.getmtime(p) > age:
                os.remove(p)
                dropped += 1
        except OSError:
            pass
    return dropped
