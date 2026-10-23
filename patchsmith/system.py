"""Common system functions: running processes (so as not to touch the files of an open game)."""
import ctypes
import os
import sys


class SysError(Exception):
    pass


def windows_processes():
    """Full paths of the running executables (Windows, Unicode)."""
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    ids = (wintypes.DWORD * 4096)()
    used = wintypes.DWORD()
    if not psapi.EnumProcesses(ctypes.byref(ids), ctypes.sizeof(ids), ctypes.byref(used)):
        raise OSError("EnumProcesses failed")
    k32.OpenProcess.restype = wintypes.HANDLE
    paths = []
    for pid in ids[: used.value // ctypes.sizeof(wintypes.DWORD)]:
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if k32.QueryFullProcessImageNameW(h, 0, buffer, ctypes.byref(size)):
                paths.append(buffer.value)
        finally:
            k32.CloseHandle(h)
    return paths


def game_open(folder):
    """True if any executable inside the game's folder is running.
    When in doubt (check impossible) raises SysError: better to stop than to take the risk."""
    if not sys.platform.startswith("win"):
        return False
    try:
        paths = windows_processes()
    except (OSError, AttributeError) as e:
        raise SysError("I'm having trouble checking if the game is open (%s): close it and try again." % e) from e
    root = os.path.normcase(os.path.abspath(folder)).rstrip("\\/") + os.sep
    return any(os.path.normcase(p).startswith(root) for p in paths)
