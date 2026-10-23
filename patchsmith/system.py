"""Common system functions: running processes (so as not to touch the files of an open game)."""
import ctypes
import os
import sys


class ErroreSistema(Exception):
    pass


def processi_windows():
    """Full paths of the running executables (Windows, Unicode)."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    ids = (wintypes.DWORD * 4096)()
    usati = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(ids), ctypes.sizeof(ids), ctypes.byref(usati)):
        raise OSError("EnumProcesses non riuscito")
    k32.OpenProcess.restype = wintypes.HANDLE
    percorsi = []
    for pid in ids[: usati.value // ctypes.sizeof(wintypes.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            dimensione = wintypes.DWORD(len(buffer))
            if k32.QueryFullProcessImageNameW(h, 0, buffer, ctypes.byref(dimensione)):
                percorsi.append(buffer.value)
        finally:
            k32.CloseHandle(h)
    return percorsi


def gioco_aperto(cartella):
    """True if any executable inside the game's folder is running.
    When in doubt (check impossible) raises ErroreSistema: better to stop than to take the risk."""
    if not sys.platform.startswith("win"):
        return False
    try:
        percorsi = processi_windows()
    except (OSError, AttributeError) as e:
        raise ErroreSistema("Non riesco a controllare se il gioco e' aperto (%s): chiudilo e riprova." % e) from e
    radice = os.path.normcase(os.path.abspath(cartella)).rstrip("\\/") + os.sep
    return any(os.path.normcase(p).startswith(radice) for p in percorsi)
