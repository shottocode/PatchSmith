"""Names of the game not to translate: the studio and the title ("Cup Dog Games" does not become
"Giochi di cani da coppa").

They are read without changing anything:
- from Unity: app.info in the _Data folder (first line the company, second the product);
- from the version information of the game's executables (CompanyName and ProductName, as Windows shows them
  in Properties > Details): they are read as data, the program is not started;
- from Steam: the game's name in its manifest, if the game is installed from Steam.

They end up in the game profile (profilo["non_tradurre"]) and Project protects them like the game's codes
(text.protect).
"""
import os
import re
import sys

# company forms at the end of the name: the game usually writes the name without them ("Cup Dog Games", not "... LLC")
_LEGAL_FORM = re.compile(r"[\s,]+(?:llc|l\.l\.c\.|inc\.?|ltd\.?|limited|gmbh|s\.?r\.?l\.?|s\.?p\.?a\.?|"
                               r"co\.?|corp\.?|corporation|ab|oy|kk|k\.k\.|pty\.? ltd\.?|bv|b\.v\.|sas|s\.a\.)$", re.I)
# names that say nothing about the game (default values of Unity or of the tools)
_GENERIC = {"defaultcompany", "unity", "unity technologies", "unity technologies aps", "microsoft corporation",
             "game", "gioco", "my project", "new unity project", "unknown", "none",
             "bootstrappackagedgame", "epic games", "epic games, inc.", "unreal engine", "nw.js", "nwjs", "electron",
             "github, inc.", "the nw.js community", "yoyo games ltd", "gamemaker", "mono", "monogame",
             # utility programs next to the game (Epic Online Services, Xbox/Microsoft Store, Blizzard)
             "epic online services bootstrapper", "microsoft game launch helper", "blizzarderror"}
_GENERIC = {g.rstrip(".") for g in _GENERIC}  # compared without the final period (as _good cleans them)
MAX_EXECUTABLES = 3


def _good(name):
    """The cleaned name, or None if it is of no use (empty, generic, too short or long, only digits)."""
    if not isinstance(name, str):
        return None
    name = " ".join(name.replace("\x00", " ").split()).strip(" .,;:-©®™")
    if not 2 <= len(name) <= 60 or not re.search(r"[^\W\d_]", name) or name.lower() in _GENERIC:
        return None
    if " " not in name and len(name) < 4:  # short acronyms ("GM", "UE"): too easy to find inside the texts
        return None
    return name


def _variants(name):
    """The name and, if any, the same without the company form ('Cup Dog Games LLC' -> also 'Cup Dog Games')."""
    name = _good(name)
    if not name:
        return []
    output = [name]
    short = name
    for _ in range(3):  # several forms in a row too: 'SQUARE ENIX CO., LTD.' -> 'SQUARE ENIX'
        stripped = _LEGAL_FORM.sub("", short).rstrip(" .,")
        if stripped == short:
            break
        short = stripped
    short = _good(short)
    if short and short != name:
        output.append(short)
    return output


def exe_version(path):
    """{'CompanyName': ..., 'ProductName': ...} from the version information of a Windows executable (read
    as data with the Windows functions: the program is not started). Empty dictionary if there is none."""
    if not sys.platform.startswith("win"):
        return {}
    try:
        import ctypes
        from ctypes import wintypes
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                            ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(path, None)
        if not size:
            return {}
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(path, 0, size, data):
            return {}
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        codes = []
        if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(pointer),
                                   ctypes.byref(length)) and pointer.value and length.value >= 4:
            words = (wintypes.WORD * (length.value // 2)).from_address(pointer.value)
            codes = ["%04x%04x" % (words[i], words[i + 1]) for i in range(0, len(words) - 1, 2)]
        output = {}
        for code in list(dict.fromkeys(codes + ["040904b0", "040904e4", "000004b0"])):
            for key in ("CompanyName", "ProductName"):
                if key in output:
                    continue
                if version.VerQueryValueW(data, "\\StringFileInfo\\%s\\%s" % (code, key),
                                           ctypes.byref(pointer), ctypes.byref(length)) \
                        and pointer.value and length.value:
                    value_ = ctypes.wstring_at(pointer.value, length.value).split("\x00")[0].strip()
                    if value_:
                        output[key] = value_
        return output
    except (OSError, AttributeError, ValueError):
        return {}


def _executables(folder):
    """The game's executables in its folder (not installers, crash reporters and the like), largest first."""
    from .steam import _GENERIC_EXECUTABLES
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(".exe") and not _GENERIC_EXECUTABLES.match(n)]
    except OSError:
        return []
    paths = [os.path.join(folder, n) for n in names if os.path.isfile(os.path.join(folder, n))]

    def size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return 0
    return sorted(paths, key=size, reverse=True)[:MAX_EXECUTABLES]


def names_of_game(folder, log=None):
    """The names not to translate for the game in the folder (see above), without duplicates. Raises no errors: a
    name that cannot be read is skipped."""
    found = []
    try:
        from . import language_unity
        data = language_unity.data_folder(folder)
        if data:
            found += [n for n in language_unity.company_product(data) if n]
    except (OSError, ValueError):
        pass
    for exe in _executables(folder):
        info = exe_version(exe)
        found += [info.get("CompanyName"), info.get("ProductName")]
    try:
        from . import steam
        installed = steam.game_installed(folder)
        if installed:
            found.append(installed.get("nome"))
    except Exception:  # noqa: BLE001 - Steam missing or unreadable: do without it
        pass
    output = []
    for name in found:
        for variant in _variants(name):
            if variant not in output:
                output.append(variant)
    if log and output:
        log("  Game names kept as they are in the translation: %s" % ", ".join(output))
    return output


def common_words(texts):
    """The words written in lowercase in the texts: a one-word name that also appears that way ('Control', 'mine')
    is an ordinary word of the game, and must be translated."""
    words = set()
    for t in texts:
        if isinstance(t, str):
            words.update(re.findall(r"\b[^\W\d_A-Z]+\b", t))
    return words


def to_protect(names, common):
    """The game profile's names that really need protecting: multi-word ones always, one-word ones only if they do
    not also appear in lowercase in the game's texts (see common_words)."""
    output = []
    for name in names or ():
        name = _good(name)
        if not name:
            continue
        if " " not in name and name.lower() in common and name.lower() != name:
            continue
        if name not in output:
            output.append(name)
    return output
