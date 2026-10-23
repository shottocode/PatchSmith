"""Official language in Unreal Engine games: which languages the game has and how to set the chosen one.

Detection: the folders <Project>/Content/Localization/<Target>/<language>/ in the paks (only the game's texts: those
of the engine and of plugins do not count), with the pak's mount point in front of the names. Paks v3-9 with the
adapter_unreal reader; v10/v11 (UE 4.26+, UE5) from the directory index, if it is not encrypted.

Setting: Unreal reads the language at startup from the command line, then from GameUserSettings.ini
(%LOCALAPPDATA%/<Project>/Saved/Config/<Platform>/, sometimes in the game's folder), section
[Internationalization]. PatchSmith:
- changes only the keys already present among Language/Local/Culture, or adds Culture (the lowest priority one,
  so a later choice in the game's menu always wins);
- does not write if the game has its own language setting in other sections (the game handles it);
- with the game closed, with a log and line-by-line restore (only if the value is still the one written), leaving
  each line with its own line ending.
"""
import glob
import os
import re
import struct

from . import adapter_unreal

MAGIC_PAK = 0x5A6F12E1
KEYS = ("language", "locale", "culture")
_SECTION_RE = re.compile(r"^\s*\[(.*)\]\s*$")
_KV = re.compile(r"^(\s*)([^=;\s][^=]*?)(\s*=\s*)(.*?)(\s*)$")
# language keys of the games themselves (e.g. LanguageSetting=Italian, mPrimaryLanguage): the game handles the language
_GAME_LANGUAGE = re.compile(r"lang|cultur|locale|locali", re.I)
# ...but not those about something else (KeyboardCulture is the keyboard, the filters are for profanity)
_NOT_TEXT_LANGUAGE = re.compile(r"^(keyboard|input|ime)|filter", re.I)
_LOCALIZATION = re.compile(r"^([^/]+)/Content/Localization/([^/]+)/([^/]+)/\2\.locres$")


class UnrealLanguageError(ValueError):
    pass


# --- which languages the game has ---------------------------------------------------------------

def _fstring(d, p):
    n = struct.unpack_from("<i", d, p)[0]
    p += 4
    if n == 0:
        return "", p
    if n > 0:
        return d[p:p + n - 1].decode("latin-1"), p + n
    n = -n
    return d[p:p + 2 * n - 2].decode("utf-16-le", "replace"), p + 2 * n


def _pak_names_v10(path):
    """(mount point, file names) of a v10/v11 pak (directory index), or None."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        dim = f.tell()
        f.seek(max(0, dim - 1024))
        tail = f.read()
        i = tail.rfind(struct.pack("<I", MAGIC_PAK))
        if i < 0:
            return None
        version, off, length = struct.unpack_from("<Iqq", tail, i + 4)
        if version < 10 or tail[i - 1] != 0 or not 0 <= off < dim or off + length > dim:
            return None  # different version or encrypted index
        f.seek(off)
        idx = f.read(length)
        mount, p = _fstring(idx, 0)
        p += 4 + 8  # number of entries, hash seed
        if struct.unpack_from("<I", idx, p)[0]:
            p += 4 + 8 + 8 + 20
        else:
            p += 4
        if not struct.unpack_from("<I", idx, p)[0]:
            return None
        p += 4
        fdi_off, fdi_len = struct.unpack_from("<qq", idx, p)
        if not 0 <= fdi_off < dim or fdi_off + fdi_len > dim:
            return None
        f.seek(fdi_off)
        fdi = f.read(fdi_len)
    names, q = [], 4
    for _ in range(struct.unpack_from("<i", fdi, 0)[0]):
        folder, q = _fstring(fdi, q)
        n = struct.unpack_from("<i", fdi, q)[0]
        q += 4
        for _ in range(n):
            name, q = _fstring(fdi, q)
            q += 4
            names.append(folder + name)
    return mount, names


def _with_mount(mount, names):
    """Full names: the mount point ('../../../Proj/Content/') without leading '../', plus the name."""
    m = (mount or "").replace("\\", "/")
    while m.startswith("../"):
        m = m[3:]
    m = m.lstrip("/")
    if m and not m.endswith("/"):
        m += "/"
    return [m + n.lstrip("/") for n in names]


def _pak_names(path):
    try:
        pak = adapter_unreal.Pak(path)
        return _with_mount(pak.mount, pak.entries)
    except (adapter_unreal.UnrealError, OSError, struct.error, ValueError):
        pass
    try:
        was_read = _pak_names_v10(path)
    except (OSError, struct.error, ValueError, IndexError):
        return None
    return _with_mount(*was_read) if was_read else None


def game_languages(folder):
    """({lingue}, progetto) of the game's texts (not the engine's or plugins'), or (None, progetto) if unknown."""
    languages, project, read_items = set(), None, 0
    for path in adapter_unreal._paks(folder):
        names = _pak_names(path)
        if names is None:
            continue
        read_items += 1
        for name in names:
            m = _LOCALIZATION.match(name)
            if m and m.group(1) not in ("Engine",):
                languages.add(m.group(3))
                project = project or m.group(1)
    # loose files (some games do not put the localization in the paks)
    for p in glob.glob(os.path.join(glob.escape(folder), "*", "Content", "Localization", "*", "*", "*.locres")):
        rel = os.path.relpath(p, folder).replace(os.sep, "/")
        m = _LOCALIZATION.match(rel)
        if m and m.group(1) != "Engine":
            languages.add(m.group(3))
            project = project or m.group(1)
            read_items += 1
    if project is None:
        project = _project_from_folders(folder)
    return (languages if read_items else None), project


def _project_from_folders(folder):
    try:
        for name in sorted(os.listdir(folder)):
            if name != "Engine" and os.path.isdir(os.path.join(folder, name, "Content", "Paks")):
                return name
    except OSError:
        pass
    return None


def target_tag(languages, dest_lang="it"):
    """The exact name of the language folder (e.g. 'it' or 'it-IT'): Unreal does not fall back from 'it' to 'it-IT'.
    For Chinese the script matters: whoever wants zh-TW is never given zh-Hans."""
    from .language import best
    return best(sorted(languages), dest_lang)


# --- setting in GameUserSettings.ini ------------------------------------------------------------

def settings_file(project, folder=None):
    """The player's GameUserSettings.ini for the project (the most recent one), or None. It looks only where
    Unreal puts it: %LOCALAPPDATA%/<Project>, the game's folder, Documents/My Games/<Project>."""
    if not project:
        return None
    bases = []
    if os.environ.get("LOCALAPPDATA"):
        bases.append(os.path.join(os.environ["LOCALAPPDATA"], project))
    if folder:
        bases.append(os.path.join(folder, project))
    bases.append(os.path.join(os.path.expanduser("~"), "Documents", "My Games", project))
    candidates = []
    for b in bases:
        candidates += glob.glob(os.path.join(glob.escape(os.path.join(b, "Saved", "Config")), "*",
                                            "GameUserSettings.ini"))
    dates = []
    for c in candidates:
        try:
            dates.append((os.path.getmtime(c), c))
        except OSError:
            continue
    return max(dates)[1] if dates else None


def _read(path):
    with open(path, "rb") as f:
        raw = f.read()
    try:
        if raw[:2] == b"\xff\xfe":
            return raw[2:].decode("utf-16-le"), "utf-16-le", b"\xff\xfe"
        if raw[:2] == b"\xfe\xff":
            raise UnrealLanguageError("file delle impostazioni in una codifica non prevista (UTF-16 BE)")
        if raw[:3] == b"\xef\xbb\xbf":
            return raw[3:].decode("utf-8"), "utf-8", b"\xef\xbb\xbf"
    except UnicodeDecodeError as e:
        raise UnrealLanguageError("file delle impostazioni illeggibile (%s)" % e) from e
    return raw.decode("utf-8", "surrogateescape"), "utf-8", b""


def _write(path, text, encoding, bom):
    data = bom + text.encode(encoding, "surrogateescape" if encoding == "utf-8" else "strict")
    temp_file = path + ".patchsmith-tmp"
    try:
        with open(temp_file, "wb") as f:
            f.write(data)
        os.replace(temp_file, path)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)


def _lines(text):
    """[[contenuto, a capo]]: each line with its line ending ('\\r\\n', '\\n', '\\r' or '' for the last one)."""
    lines, pos = [], 0
    for m in re.finditer(r"\r\n|\r|\n", text):
        lines.append([text[pos:m.start()], m.group()])
        pos = m.end()
    if pos < len(text):
        lines.append([text[pos:], ""])
    return lines


def _merge(lines):
    return "".join(c + a for c, a in lines)


def _line_break(text):
    return "\r\n" if "\r\n" in text else "\r" if "\r" in text and "\n" not in text else "\n"


def _is_internationalization(section):
    return section is not None and section.lower() == "internationalization"


def read_internationalization(path):
    """({lowercase key: value} of [Internationalization] (the last one wins), [the game's language keys in
    other sections of the game: /Script/... and /Game/...])."""
    text, _, _ = _read(path)
    values, other_ones, section = {}, [], None
    for content, _ in _lines(text):
        m = _SECTION_RE.match(content)
        if m:
            section = m.group(1).strip()
            continue
        kv = _KV.match(content)
        if not kv or section is None:
            continue
        key = kv.group(2).strip()
        if _is_internationalization(section):
            values[key.lower()] = kv.group(4)
        elif section.lower() != "internationalization.assetgroupcultures" and \
                section.startswith(("/Script/", "/Game/")) and _GAME_LANGUAGE.search(key) and \
                not _NOT_TEXT_LANGUAGE.search(key):
            other_ones.append("[%s] %s" % (section, key))
    return values, other_ones


def set_culture(path, tag):
    """Sets the language 'tag' in [Internationalization]. Returns the changes made (for the restore):
    ['riga', n, key, old value] for each language line changed (n = position among the section's language
    lines), ['aggiunta', 'Culture'] for the added line, ['sezione', [added lines], line ending added]
    if the section was not there. Empty list if everything was already in place."""
    if not os.access(path, os.W_OK):
        raise UnrealLanguageError("il file delle impostazioni e' di sola lettura: non lo tocco")
    text, encoding, bom = _read(path)
    line_break = _line_break(text)
    lines = _lines(text)
    section, start, end, language_lines = None, None, None, []
    for i, (content, _) in enumerate(lines):
        m = _SECTION_RE.match(content)
        if m:
            if _is_internationalization(section) and end is None:
                end = i
            section = m.group(1).strip()
            if _is_internationalization(section) and start is None:
                start = i
            continue
        if _is_internationalization(section):
            kv = _KV.match(content)
            if kv and kv.group(2).strip().lower() in KEYS:
                language_lines.append((i, kv))
    if start is not None and end is None:
        end = len(lines)
    changes = []
    if language_lines:
        for n, (i, kv) in enumerate(language_lines):
            if kv.group(4) != tag:
                lines[i][0] = kv.group(1) + kv.group(2) + kv.group(3) + tag + kv.group(5)
                changes.append(["riga", n, kv.group(2).strip(), kv.group(4)])
    elif start is not None:
        where = end
        while where > start + 1 and lines[where - 1][0].strip() == "":
            where -= 1
        if lines[where - 1][1]:
            lines.insert(where, ["Culture=" + tag, line_break])
        else:  # last line of the file without a line ending: the new one takes its place at the end
            lines[where - 1][1] = line_break
            lines.insert(where, ["Culture=" + tag, ""])
        changes.append(["aggiunta", "Culture"])
    else:
        line_break_added = bool(lines) and lines[-1][1] == ""
        if line_break_added:
            lines[-1][1] = line_break
        new_lines = ([[""]] if lines and lines[-1][0].strip() else []) + [["[Internationalization]"],
                                                                          ["Culture=" + tag]]
        lines += [[c[0], line_break] for c in new_lines]
        changes += [["aggiunta", "Culture"], ["sezione", [c[0] for c in new_lines], line_break_added]]
    if changes:
        _write(path, _merge(lines), encoding, bom)
    return changes


def _from_old_log(changes):
    """0.8.0 log ([(chiave, vecchio)], ('[sezione]', testo)) in the current format."""
    new_lines = []
    for key, old in changes:
        if key == "[sezione]":
            new_lines.append(["sezione", [r for r in old.split("\n") if r], False])
        elif old is None:
            new_lines.append(["aggiunta", key])
        else:
            new_lines.append(["riga", None, key, old])
    return new_lines


def restore_culture(path, changes, tag):
    """Puts the changed lines back as they were, only if they are still 'tag'. Returns how many."""
    if not os.path.exists(path):
        return 0
    if not os.access(path, os.W_OK):
        raise UnrealLanguageError("il file delle impostazioni e' di sola lettura")
    changes = [list(m) for m in changes]
    if changes and changes[0][0] not in ("riga", "aggiunta", "sezione"):
        changes = _from_old_log(changes)
    changed_lines = {}
    for m in changes:
        if m[0] == "riga":
            changed_lines.setdefault(m[1], []).append((m[2], m[3]))
    additions = {m[1].lower() for m in changes if m[0] == "aggiunta"}
    added_section = next((m for m in changes if m[0] == "sezione"), None)

    text, encoding, bom = _read(path)
    lines = _lines(text)
    section, n, redone, to_remove = None, -1, 0, []
    for i, (content, _) in enumerate(lines):
        m = _SECTION_RE.match(content)
        if m:
            section = m.group(1).strip()
            continue
        if not _is_internationalization(section):
            continue
        kv = _KV.match(content)
        if not kv or kv.group(2).strip().lower() not in KEYS:
            continue
        n += 1
        key = kv.group(2).strip()
        if kv.group(4) != tag:
            continue
        old = next((v for k, v in changed_lines.get(n, []) + changed_lines.get(None, []) if k == key or
                        (n not in changed_lines and k.lower() == key.lower())), None)
        if old is not None:
            lines[i][0] = kv.group(1) + kv.group(2) + kv.group(3) + old + kv.group(5)
            redone += 1
        elif key.lower() in additions:
            to_remove.append(i)
            additions.discard(key.lower())
            redone += 1
    for i in reversed(to_remove):
        if lines[i][1] == "" and i > 0 and i == len(lines) - 1:
            lines[i - 1][1] = ""  # it was the last line without a line ending: back as it was
        del lines[i]
    # section added by PatchSmith and left empty: removed, as it was before
    if to_remove and added_section:
        tail = [r for r in added_section[1] if not r.lower().startswith("culture=")]
        if tail and [c for c, _ in lines[-len(tail):]] == tail:
            del lines[-len(tail):]
            if added_section[2] and lines:
                lines[-1][1] = ""
    if redone:
        _write(path, _merge(lines), encoding, bom)
    return redone
