"""Common system functions: running processes (so as not to touch the files of an open game), the HTTPS connections."""
import ctypes
import functools
import os
import ssl
import sys


class SysError(Exception):
    pass


@functools.lru_cache(maxsize=None)
def https_context():
    """The SSL context of PatchSmith's HTTPS connections (downloads, online translators): Python's default one, with the
    Windows certificates, that also accepts a chain as soon as it reaches one of them, as Python does from 3.13
    (VERIFY_X509_PARTIAL_CHAIN). With an older Python (OpenSSL 1.1.1) a cross-signed copy of a root among the
    Windows intermediate certificates (USERTrust ECC signed by AAA Certificate Services, which was not installed)
    made github.com fail with "unable to get issuer certificate"."""
    context = ssl.create_default_context()
    context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return context


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
