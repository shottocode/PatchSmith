"""Language chosen in the saved options of a GameMaker game (e.g. Forager: %LOCALAPPDATA%\\Forager\\config.txt).

Many GameMaker games save their options with ds_map_write / ds_list_write: a JSON containing, in hexadecimal, a
map and some lists of values. In Forager the language is the first value of the "options" list ("english", "french"...
that is, the name of the language file without .json). PatchSmith, after adding a language (local/italian.json),
sets it there, so the game starts already translated:
- only if it finds exactly ONE value equal to the name of one of the game's languages (no doubt about what to change);
- it changes only those bytes (length and text of the string), the rest of the file stays identical;
- it first checks that the decoding is exact (reading it back gives the same file), then reads back the result;
- backup copy in PatchSmith (save-backups/) and a record for the restore; never with the game open.
"""
import datetime
import json
import os
import re
import shutil
import struct

from . import gamemaker

_MAPS = (0x191, 0x192, 0x193)  # ds_map_write headers (versions)
_LISTS = (0x12E, 0x12F)  # ds_list_write headers
_HEXADECIMAL = re.compile(r"^(?:[0-9A-F]{2})+$|^(?:[0-9a-f]{2})+$")
_MAXIMUM = 2 * 1024 * 1024


class FormatError(ValueError):
    pass


def _value(b, i):
    """(type, start, end, value) of the value that starts at i."""
    if i + 4 > len(b):
        raise FormatError("value beyond the end")
    kind = struct.unpack_from("<I", b, i)[0]
    if kind == 1:  # string
        n = struct.unpack_from("<I", b, i + 4)[0]
        if i + 8 + n > len(b):
            raise FormatError("string beyond the end")
        return kind, i, i + 8 + n, b[i + 8:i + 8 + n].decode("utf-8")
    sizes = {0: 8, 5: 0, 7: 4, 10: 8, 13: 8, 12: 0}  # real, undefined, int32, int64, bool, null
    if kind not in sizes or i + 4 + sizes[kind] > len(b):
        raise FormatError("unknown value type (%d)" % kind)
    return kind, i, i + 4 + sizes[kind], None


def _entries(b, mapping):
    """The entries of a map ([(key, value)]) or of a list ([value]), with their positions in the bytes."""
    if len(b) < 8:
        raise FormatError("too short")
    header, n = struct.unpack_from("<II", b, 0)
    if header not in (_MAPS if mapping else _LISTS) or n > 100000:
        raise FormatError("unknown header")
    i, output = 8, []
    for _ in range(n):
        if mapping:
            k = _value(b, i)
            v = _value(b, k[2])
            output.append((k, v))
            i = v[2]
        else:
            v = _value(b, i)
            output.append(v)
            i = v[2]
    if i != len(b):
        raise FormatError("leftover bytes")
    return output


def _string(text):
    data = text.encode("utf-8")
    return struct.pack("<II", 1, len(data)) + data


def _replace(b, value_, new):
    """The bytes with the string 'value' (type, start, end, text) replaced by 'nuovo'."""
    return b[:value_[1]] + _string(new) + b[value_[2]:]


def _to_hex(b, uppercase):
    h = b.hex()
    return h.upper() if uppercase else h


def find(json_text, languages):
    """Where the language is in the file: [(JSON key, map key, index in the list, current language)].
    It looks, in the hexadecimal values of the JSON, for maps containing lists with a value equal to the name of
    one of the game's 'languages'."""
    try:
        data = json.loads(json_text)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    found = []
    for json_key, hex_str in data.items():
        if not isinstance(hex_str, str) or len(hex_str) < 16 or not _HEXADECIMAL.match(hex_str):
            continue
        try:
            b = bytes.fromhex(hex_str)
            entries = _entries(b, mapping=True)
        except (FormatError, ValueError, struct.error, UnicodeDecodeError):
            continue
        for k, v in entries:
            if k[0] != 1 or v[0] != 1 or not _HEXADECIMAL.match(v[3] or "") or len(v[3]) < 16:
                continue
            try:
                list_ = _entries(bytes.fromhex(v[3]), mapping=False)
            except (FormatError, ValueError, struct.error, UnicodeDecodeError):
                continue
            for idx, element in enumerate(list_):
                if element[0] == 1 and element[3] in languages:
                    found.append((json_key, k[3], idx, element[3]))
    return found


def change(json_text, spot, new):
    """The text of the file with the language at 'spot' (from trova) changed to 'nuovo'. Touches only that string:
    the whole rest of the file stays identical (only the hexadecimal value is replaced, which is unique in the file)."""
    json_key, mapping_key, idx, _current = spot
    data = json.loads(json_text)
    hex_str = data[json_key]
    b = bytes.fromhex(hex_str)
    entries = _entries(b, mapping=True)
    k, v = next((k, v) for k, v in entries if k[0] == 1 and k[3] == mapping_key)
    list_b = bytes.fromhex(v[3])
    element = _entries(list_b, mapping=False)[idx]
    new_list = _replace(list_b, element, new)
    uppercase = v[3] == v[3].upper()
    new_mapping = _replace(b, v, _to_hex(new_list, uppercase))
    new_hex = _to_hex(new_mapping, hex_str == hex_str.upper())
    if json_text.count(hex_str) != 1:
        raise FormatError("the value to change is not unique in the file")
    result = json_text.replace(hex_str, new_hex)
    checks = find(result, {new})
    if [(c, m, i) for c, m, i, _ in checks] != [(json_key, mapping_key, idx)]:
        raise FormatError("check after the change failed")
    return result


def settings_folders(root):
    """The folders in %LOCALAPPDATA% where the game may save its options (GameMaker uses the game's name; the
    names written in date.win and those of the executables are tried)."""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return []
    names = []
    data = next((os.path.join(root, n) for n in gamemaker.DATA_NAMES if os.path.exists(os.path.join(root, n))),
                None)
    if data:
        try:
            names += gamemaker.names_of_game(gamemaker.read_file(data))
        except (gamemaker.GameMakerError, OSError, struct.error):
            pass
    names += [os.path.splitext(os.path.basename(e))[0] for e in gamemaker.executables(root)]
    folders = []
    for name in dict.fromkeys(names):
        if name and not re.search(r'[\\/:*?"<>|]', name) and os.path.isdir(os.path.join(base, name)):
            folders.append(os.path.join(base, name))
    return folders


def search(root, languages):
    """(file, place) of the only language saved in the game's options, or (None, reason)."""
    folders = settings_folders(root)
    if not folders:
        return None, "the game hasn't saved its options yet (you'll need to start it once)"
    found = []
    for folder, name in [(c, n) for c in folders for n in sorted(os.listdir(c))]:
        path = os.path.join(folder, name)
        if not os.path.isfile(path) or os.path.getsize(path) > _MAXIMUM \
                or not name.lower().endswith((".txt", ".json", ".ini", ".dat", ".cfg", ".sav")):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        found += [(path, spot) for spot in find(text, languages)]
    if len(found) != 1:
        return None, ("I can't find where the game saves the language setting" if not found else
                      "the language appears in several places in the saved options: not touching anything")
    return found[0]


def set_(root, new_value, languages, saves, log=print, respect=None):
    """Sets 'nuova' as the game's language in the saved options. Returns (record for the restore, None)
    or (None, reason). 'salvataggi': folder where the file is copied before changing it. 'rispetta': if the
    saved language differs from this one (PatchSmith's choice from last time), the player changed it and it is
    left alone."""
    path, spot = search(root, set(languages) | {new_value})
    if path is None:
        return None, spot
    if spot[3] == new_value:
        return {"kind": "gamemaker_options", "file": path, "spot": list(spot[:3]), "previous": None,
                "written": new_value}, None
    if respect and spot[3] != respect:
        return None, ("in the game, you have chosen the language '%s'. I will leave it as is (the translation is in "
                      "language '%s')" % (spot[3], new_value))
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    new = change(text, spot, new_value)
    os.makedirs(saves, exist_ok=True)
    copy_dir = os.path.join(saves, "%s-%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"),
                                                 os.path.basename(path)))
    shutil.copyfile(path, copy_dir)
    from .adapter_webjson import write_atomic
    write_atomic(path, new.encode("utf-8"))
    log("  Game language set to '%s' in its options (%s; previous copy in %s)"
        % (new_value, path, copy_dir))
    return {"kind": "gamemaker_options", "file": path, "spot": list(spot[:3]), "previous": spot[3],
            "written": new_value, "copy_dir": copy_dir}, None


def restore(log_file):
    """Puts back the previous language if the options still hold the one written by PatchSmith. True if put back,
    False if the player has changed it in the meantime (their choice is kept)."""
    path = log_file["file"]
    if not log_file.get("previous") or not os.path.isfile(path):
        return False  # options deleted or recreated by the game: there is nothing to put back
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    json_key, mapping_key, idx = log_file["spot"]
    current_count = [p for p in find(text, {log_file["written"]}) if tuple(p[:3]) == (json_key, mapping_key, idx)]
    if not current_count:
        return False
    try:
        new = change(text, current_count[0], log_file["previous"])
    except FormatError:
        return False  # file rewritten in a way I do not recognize: leave it as it is
    from .adapter_webjson import write_atomic
    write_atomic(path, new.encode("utf-8"))
    return True
