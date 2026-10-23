"""Reading of Steam (read-only, standard library): libraries, installed games, official languages.

- libraryfolders.vdf and appmanifest_<appid>.acf are "KeyValues" text files (VDF);
- appcache/appinfo.vdf is the binary cache of the games' information: it also contains the supported
  languages (common/supported_languages), so we know offline whether a game has official Italian.
The language a Steam game uses is the one chosen for that game (Properties > General > Language), saved
in the appmanifest (UserConfig/language): the game asks Steam for it at startup.
"""
import bisect
import os
import re
import struct
import sys

# PatchSmith codes -> language names in the Steam API
STEAM_NAMES = {
    "it": "italian", "en": "english", "fr": "french", "de": "german", "es": "spanish", "pt": "portuguese",
    "pt-BR": "brazilian", "ru": "russian", "pl": "polish", "ja": "japanese", "ko": "koreana", "zh-CN": "schinese",
    "zh-TW": "tchinese", "nl": "dutch", "sv": "swedish", "da": "danish", "no": "norwegian", "fi": "finnish",
    "cs": "czech", "hu": "hungarian", "ro": "romanian", "el": "greek", "tr": "turkish", "uk": "ukrainian",
    "th": "thai", "vi": "vietnamese", "ar": "arabic", "id": "indonesian", "es-419": "latam", "bg": "bulgarian",
    "ms": "malay",
}


class SteamError(ValueError):
    pass


# --- text KeyValues (VDF) ------------------------------------------------------------------------

def _token(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == '"':
            j, pieces = i + 1, []
            while j < n and text[j] != '"':
                if text[j] == "\\" and j + 1 < n:
                    pieces.append({"n": "\n", "t": "\t", "\\": "\\", '"': '"'}.get(text[j + 1], text[j + 1]))
                    j += 2
                else:
                    pieces.append(text[j])
                    j += 1
            yield ("s", "".join(pieces))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '{}"':
                j += 1
            yield ("s", text[i:j])
            i = j


def read_vdf(text):
    """Text KeyValues -> nested dictionaries (the keys stay as they are written)."""
    stack = [{}]
    key = None
    for t in _token(text):
        if t == "{":
            if key is None:
                raise SteamError("Invalid VDF: '{' without key")
            new = {}
            stack[-1][key] = new
            stack.append(new)
            key = None
        elif t == "}":
            if len(stack) == 1:
                raise SteamError("Invalid VDF: extra '}'")
            stack.pop()
        elif key is None:
            key = t[1]
        else:
            stack[-1][key] = t[1]
            key = None
    return stack[0]


def _get_ignore_case(d, key):
    for k, v in d.items():
        if k.lower() == key.lower():
            return v
    return None


# --- installation and libraries -----------------------------------------------------------------

def steam_folder():
    """Steam's folder (from the registry, then the usual paths) or None."""
    if sys.platform.startswith("win"):
        try:
            import winreg
            for root, key, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                         (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(root, key) as k:
                        value_ = winreg.QueryValueEx(k, name)[0]
                    if value_ and os.path.isdir(value_):
                        return os.path.normpath(value_)
                except OSError:
                    continue
        except ImportError:
            pass
    for p in (r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam"):
        if os.path.isdir(p):
            return p
    return None


def libraries(steam=None):
    """Folders of the Steam libraries (with 'steamapps')."""
    steam = steam or steam_folder()
    if not steam:
        return []
    output = [steam]
    try:
        with open(os.path.join(steam, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
            data = read_vdf(f.read())
        folders = _get_ignore_case(data, "libraryfolders") or {}
        for v in folders.values():
            path = v.get("path") if isinstance(v, dict) else v
            if isinstance(path, str) and os.path.isdir(path):
                output.append(os.path.normpath(path))
    except (OSError, SteamError):
        pass
    seen, distinct = set(), []
    for p in output:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            distinct.append(os.path.normpath(p))
    return distinct


def _path_keys(p):
    """The comparable forms of a path: as written and resolved (junctions, links, 8.3 short names)."""
    variants = {os.path.normcase(os.path.abspath(p)).rstrip("\\/")}
    try:
        variants.add(os.path.normcase(os.path.realpath(p)).rstrip("\\/"))
    except (OSError, ValueError):
        pass
    return variants


def game_installed(game_folder, steam=None):
    """If the folder is a game installed by Steam: {'appid', 'name', 'manifest', 'language'}; otherwise None."""
    game = _path_keys(game_folder)
    for library in libraries(steam):
        app = os.path.join(library, "steamapps")
        try:
            names = os.listdir(app)
        except OSError:
            continue
        for name in names:
            if not (name.startswith("appmanifest_") and name.endswith(".acf")):
                continue
            path = os.path.join(app, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    state = _get_ignore_case(read_vdf(f.read()), "AppState") or {}
            except (OSError, SteamError):
                continue
            installdir = state.get("installdir") if isinstance(state, dict) else None
            if not isinstance(installdir, str) or not installdir:
                continue
            folder = os.path.join(app, "common", installdir)
            if any(g == d or g.startswith(d + os.sep) for g in game for d in _path_keys(folder)):
                user = state.get("UserConfig") or {}
                mounted_config = state.get("MountedConfig") or {}
                return {"appid": state.get("appid"), "name": state.get("name"), "manifest": path,
                        "folder": os.path.normcase(os.path.abspath(folder)),
                        "language": user.get("language") or mounted_config.get("language")}
    return None


def is_running():
    """True if Steam is open (steam.exe), None if it cannot be known."""
    try:
        from .system import windows_processes
        return any(os.path.basename(p).lower() == "steam.exe" for p in windows_processes())
    except (OSError, AttributeError, ImportError):
        return None


# --- appinfo.vdf (binary) -----------------------------------------------------------------------

_V27, _V28, _V29 = 0x07564427, 0x07564428, 0x07564429
_CACHE = {}  # only one cache read: {'identity': (path, mtime, size), 'value': (...)}


def _binary_kv(data, pos, keys, depth=0):
    """Binary KeyValues: type (0 section, 1 string, 2 int32, 3 float, 7 uint64, 8/11 end)."""
    if depth > 64:
        raise SteamError("KeyValues nested too deeply")
    output = {}
    while True:
        kind = data[pos]
        pos += 1
        if kind in (8, 11):
            return output, pos
        if keys is not None:
            idx = struct.unpack_from("<i", data, pos)[0]
            if not 0 <= idx < len(keys):
                raise SteamError("KeyValues key outside the table: %d" % idx)
            name = keys[idx]
            pos += 4
        else:
            end = data.index(b"\x00", pos)
            name = data[pos:end].decode("utf-8", "replace")
            pos = end + 1
        if kind == 0:
            output[name], pos = _binary_kv(data, pos, keys, depth + 1)
        elif kind == 1:
            end = data.index(b"\x00", pos)
            output[name] = data[pos:end].decode("utf-8", "replace")
            pos = end + 1
        elif kind in (2, 4, 6):
            output[name] = struct.unpack_from("<i", data, pos)[0]
            pos += 4
        elif kind == 3:
            output[name] = struct.unpack_from("<f", data, pos)[0]
            pos += 4
        elif kind == 7:
            output[name] = struct.unpack_from("<Q", data, pos)[0]
            pos += 8
        elif kind == 10:
            output[name] = struct.unpack_from("<q", data, pos)[0]
            pos += 8
        else:
            raise SteamError("unknown KeyValues type: %d" % kind)


def _build_index(data):
    """(keys of the table or None, {appid: (start of the KeyValues, end of the record)}) of appinfo.vdf."""
    if len(data) < 16:
        raise SteamError("appinfo.vdf is too short")
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic not in (_V27, _V28, _V29):
        raise SteamError("unknown appinfo.vdf format (0x%08x)" % magic)
    pos, keys, limit = 8, None, len(data)
    if magic >= _V29:
        table = struct.unpack_from("<q", data, 8)[0]
        if not 16 <= table <= len(data) - 4:
            raise SteamError("appinfo.vdf: key table outside the file")
        pos, limit = 16, table
        n = struct.unpack_from("<I", data, table)[0]
        keys, q = [], table + 4
        for _ in range(n):
            end = data.index(b"\x00", q)
            keys.append(data[q:end].decode("utf-8", "replace"))
            q = end + 1
    # after appid and size: info_state, last_updated, token, sha1, change_number, (from v28) binary sha1
    head = 4 + 4 + 8 + 20 + 4 + (20 if magic >= _V28 else 0)
    record = {}
    while pos + 8 <= limit:
        app, size = struct.unpack_from("<II", data, pos)
        if app == 0:
            break
        end = pos + 8 + size
        if end > limit or size <= head:
            raise SteamError("appinfo.vdf: record %d truncated" % app)
        record[app] = (pos + 8 + head, end)
        pos = end
    return keys, record


def _appinfo(steam=None):
    """(bytes, keys, records, already read) of the Steam cache, read again only if the file changes; None if missing."""
    steam = steam or steam_folder()
    if not steam:
        return None
    path = os.path.join(steam, "appcache", "appinfo.vdf")
    try:
        st = os.stat(path)
        identity = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns, st.st_size)
        if _CACHE.get("identity") == identity:
            return _CACHE["value"]
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    try:
        keys, record = _build_index(data)
    except SteamError:
        raise
    except (struct.error, IndexError, ValueError, OverflowError) as e:
        raise SteamError("appinfo.vdf is unreadable (%s)" % e) from e
    _CACHE.clear()
    _CACHE.update(identity=identity, value=(data, keys, record, {}))
    return _CACHE["value"]


def _record_info(cache, app):
    data, keys, record, read_items = cache
    if app not in read_items:
        start, end = record[app]
        try:
            kv, _ = _binary_kv(data[start:end], 0, keys)
        except SteamError:
            raise
        except (struct.error, IndexError, ValueError, RecursionError) as e:
            raise SteamError("appinfo.vdf: game %d unreadable (%s)" % (app, e)) from e
        read_items[app] = kv.get("appinfo", kv)
    return read_items[app]


def app_info(appid, steam=None):
    """Information about a game from the Steam cache (appinfo.vdf), or None. Raises SteamError if the
    cache is unreadable."""
    cache = _appinfo(steam)
    if cache is None:
        return None
    try:
        app = int(appid)
    except (TypeError, ValueError):
        return None
    if app not in cache[2]:
        return None
    return _record_info(cache, app)


# --- game outside Steam recognized from its executables -----------------------------------------

# executables that do not identify a game: component installers, generic launchers, anti-cheat, crash reporters
_GENERIC_EXECUTABLES = re.compile(
    r"^(vc_?redist.*|visualcppredist.*|dotnetfx.*|ndp\d.*|dxsetup|dxwebsetup|oalinst|physx.*|.*prereq.*|"
    r".*redist.*|.*setup.*|.*install.*|unins\d*|.*crashhandler.*|.*crashreport.*|.*crashpad.*|"
    r"start_protected_game|launcher|game|play|start|run|main|app|client|bootstrap|easyanticheat.*|battleye.*|"
    r"be_service.*|eac_?launcher|notification_helper|.*webhelper.*|cefprocess|dowser|.*updater.*)\.exe$", re.I)
_NON_GAME_FOLDERS = {"_redist", "_commonredist", "redist", "redistributables", "directx", "vcredist", "support",
                       "install", "__installer", "installer", "installers", "prereqs", "prerequisites",
                       "easyanticheat", "battleye", "commonredist"}
_NOT_GAME = re.compile(r"\b(demo|prologue|playtest|trial|benchmark|dedicated server|sdk)\b", re.I)


def _folder_executables(folder):
    """{lowercase relative path with '/'} of the executables up to two subfolders deep (without installers)."""
    output = set()
    for root, dirs, files in os.walk(folder):
        rel = os.path.relpath(root, folder)
        level = 0 if rel == "." else rel.count(os.sep) + 1
        dirs[:] = [] if level >= 2 else [d for d in dirs if d.lower() not in _NON_GAME_FOLDERS]
        for f in files:
            if f.lower().endswith(".exe") and not _GENERIC_EXECUTABLES.match(f):
                output.add((f if rel == "." else os.path.join(rel, f)).replace(os.sep, "/").lower())
    return output


def _launch_path(value_):
    p = str(value_ or "").strip().strip('"').replace("\\", "/").lower()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _simplify(text):
    return re.sub(r"[^0-9a-z]", "", str(text or "").lower())


def similar_names(a, b):
    """True if two folder/game names look alike (equal or one inside the other, letters and digits only)."""
    a, b = _simplify(a), _simplify(b)
    if not a or not b:
        return False
    if min(len(a), len(b)) < 4:
        return a == b
    return a == b or a in b or b in a


def identify_from_executables(game_folder, steam=None):
    """Game NOT installed by Steam but known to Steam: compares the folder's executables with the launch paths
    of the games in the Steam cache (config/launch/*/executable), ignoring case.
    Cautious: no installers or generic names, an executable launched by several games does not count, no
    winner on a tie. {'appid', 'name', 'executable', 'installdir', 'similar_folder'} or None."""
    executables = _folder_executables(game_folder)
    if not executables:
        return None
    cache = _appinfo(steam)
    if cache is None:
        return None
    data, _, record, _ = cache
    names = {e.rsplit("/", 1)[-1] for e in executables}
    ordered = sorted((start, end, app) for app, (start, end) in record.items())
    starts = [x[0] for x in ordered]
    low = data.lower()
    candidates, too_common = set(), set()
    for name in names:
        needle = name.encode("utf-8") + b"\x00"
        i, repeats = low.find(needle), 0
        while i >= 0:
            repeats += 1
            if repeats > 200:
                too_common.add(name)
                break
            k = bisect.bisect_right(starts, i) - 1
            if k >= 0 and i < ordered[k][1]:
                candidates.add(ordered[k][2])
            i = low.find(needle, i + 1)
    del low
    names -= too_common
    launched_by = {}        # executable name -> {appids of the games that launch it}
    matches = {}  # appid -> {launch paths that exist in the folder}
    descriptions = {}
    base = os.path.basename(os.path.normpath(os.path.abspath(game_folder))).lower()
    deeper = {e.split("/", 1)[1] for e in executables if "/" in e}  # chosen folder = the one above the game
    for app in candidates:
        try:
            info = _record_info(cache, app)
        except SteamError:
            continue
        shared = info.get("common") or {}
        if not isinstance(shared, dict):
            continue
        kind = str(shared.get("type", "")).lower()
        app_name = str(shared.get("name", ""))
        if kind not in ("game", "") or _NOT_GAME.search(app_name):
            continue
        config = info.get("config") or {}
        launches = config.get("launch") if isinstance(config, dict) else None
        if not isinstance(launches, dict):
            continue
        for entry in launches.values():
            path = _launch_path(entry.get("executable") if isinstance(entry, dict) else None)
            name = path.rsplit("/", 1)[-1]
            if not path.endswith(".exe") or name not in names:
                continue
            launched_by.setdefault(name, set()).add(app)
            if (path in executables or path in deeper or
                    (path.startswith(base + "/") and path[len(base) + 1:] in executables)):
                matches.setdefault(app, set()).add(path)
                descriptions[app] = (app_name, config.get("installdir"))
    valid_spots = {}
    for app, paths in matches.items():
        unique = {p for p in paths if len(launched_by.get(p.rsplit("/", 1)[-1], ())) == 1}
        if unique:
            valid_spots[app] = unique
    if not valid_spots:
        return None
    ranking = sorted(valid_spots.items(), key=lambda x: len(x[1]), reverse=True)
    if len(ranking) > 1 and len(ranking[0][1]) == len(ranking[1][1]):
        return None  # tie: better not to guess
    app, paths = ranking[0]
    app_name, installdir = descriptions[app]
    return {"appid": str(app), "name": app_name or None, "executable": sorted(paths)[0],
            "installdir": installdir if isinstance(installdir, str) else None,
            "similar_folder": similar_names(base, installdir) or similar_names(base, app_name)}


# --- languages ------------------------------------------------------------------------------------

def selectable_from_info(info):
    """Languages that can be chosen in Steam (Properties > General > Language): the depots' 'baselanguages' plus
    the languages of the per-language depots. The other official languages are chosen in the game."""
    depots = info.get("depots") or {}
    if not isinstance(depots, dict):
        return set()
    base = depots.get("baselanguages")
    languages = {x.strip().lower() for x in str(base).split(",") if x.strip()} if base else set()
    for depot in depots.values():
        if isinstance(depot, dict):
            config = depot.get("config")
            depot_language = config.get("language") if isinstance(config, dict) else None
            if depot_language:
                languages.add(str(depot_language).lower())
    return languages


def official_from_info(info):
    """{'italian': {'supported': True, 'subtitles': ..., 'full_audio': ...}, ...} or None if unknown."""
    shared = info.get("common") or {}
    if not isinstance(shared, dict):
        return None
    supported = shared.get("supported_languages")
    if isinstance(supported, dict) and supported:
        output = {}
        for name, details in supported.items():
            if isinstance(details, dict):
                output[name.lower()] = {k: str(v).lower() in ("1", "true") for k, v in details.items()}
        return output
    items = shared.get("languages")
    if isinstance(items, dict) and items:
        return {name.lower(): {"supported": str(v) == "1"} for name, v in items.items()}
    return None


def selectable_languages(appid, steam=None):
    info = app_info(appid, steam)
    return selectable_from_info(info) if info else None


def official_languages(appid, steam=None):
    info = app_info(appid, steam)
    return official_from_info(info) if info else None
