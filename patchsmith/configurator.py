"""Configurator: changes made "from outside" to a game's settings and files, always reversible.

Each change is a "lever" (language, in the future screen...) and has:
- a record in <patchsmith>/configurations/<fingerprint of the folder>.json: what, when, which files, consents;
- for each file touched, the verified backup next to it (<file>.patchsmith-originale and .patchsmith-info.json, the
  same mechanism as translations): "Restore originals" works even without the record;
- all-or-nothing writing: if a file cannot be written, the ones already written go back to how they were.
Changes to executables are made only with the user's consent, asked once per game and saved in the record (so after a
game update they can be reapplied on their own).
"""
import datetime
import hashlib
import json
import os
import shutil
import sys

from . import adapter_webjson, legacy, project, system

SPACE_MARGIN = 100 * 1024 * 1024


class ConfiguratorError(ValueError):
    pass


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# --- record ---------------------------------------------------------------------------------------

def _log_file(folder):
    key = hashlib.sha1(os.path.normcase(os.path.abspath(folder)).encode("utf-8")).hexdigest()[:16]
    return project.data_folder("configurations", "%s.json" % key)


def read_file(folder):
    """The folder's record ({} if there is none)."""
    try:
        with open(_log_file(folder), encoding="utf-8") as f:
            data = legacy.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(folder, data):
    path = _log_file(folder)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data.setdefault("version", 1)
    data["game_folder"] = os.path.abspath(folder).replace(os.sep, "/")
    adapter_webjson.write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def has_consent(folder, kind="executables"):
    return bool(read_file(folder).get("consents", {}).get(kind))


def save_consent(folder, kind="executables"):
    data = read_file(folder)
    data.setdefault("consents", {})[kind] = _now()
    _save(folder, data)


def lever(folder, name):
    """The lever's data if it is recorded ({'state', 'value', 'file', ...}), otherwise None."""
    return read_file(folder).get("levers", {}).get(name)


def mark(folder, key, value_=True):
    """Notes a fact in the record (e.g. saves already copied)."""
    data = read_file(folder)
    data.setdefault("notes", {})[key] = value_
    _save(folder, data)


def note(folder, key):
    return read_file(folder).get("notes", {}).get(key)


# --- checks ---------------------------------------------------------------------------------------

def smart_app_control():
    """State of Windows 11 Smart App Control: 0 off, 1 on, 2 in evaluation, None if unknown."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\CI\Policy") as k:
            return int(winreg.QueryValueEx(k, "VerifiedAndReputablePolicyState")[0])
    except (OSError, ValueError, TypeError, ImportError):
        return None


def executable_checks():
    """Reasons NOT to modify executables on this PC (empty list = it is allowed)."""
    if smart_app_control() in (1, 2):
        return ["Smart App Control for Windows is active (or under evaluation): it would block modified executables "
                "because their digital signature would no longer be valid. I am not modifying anything."]
    return []


# --- all-or-nothing apply -------------------------------------------------------------------------

def _backup_ready(path, current_data, lever_name, log):
    """Backup of the original ready to use: creates it, keeps it, or (if the game updated the file) renews it keeping
    the previous one. Returns (info, True if the backup was created now)."""
    backup = path + project.BACKUP_SUFFIX
    if not os.path.exists(backup):
        return project.create_backup(path, {"configurator": lever_name}), True
    info = project.read_info(path)
    if not info:
        raise ConfiguratorError("%s has a backup with no information: please check it manually before proceeding."
                                  % os.path.basename(path))
    if project._hash_file(backup) != info["original_hash"]:
        raise ConfiguratorError("The backup of %s doesn't match the recorded original: stopping."
                                  % os.path.basename(path))
    current = project._hash_bytes(current_data)
    if current == info["original_hash"] or current in info.get("written_hashes", []):
        return info, False
    # the file was changed by someone else (update): it is the new original, the old one stays kept
    n = len(info.get("prior", [])) + 1
    old = "%s.%d" % (os.path.basename(backup), n)
    old_path = os.path.join(os.path.dirname(backup), old)
    os.replace(backup, old_path)
    try:
        new_lines = project.create_backup(path, {"configurator": lever_name, "created": info.get("created", []),
                                                "prior": info.get("prior", []) + [old]})
    except BaseException:
        os.replace(old_path, backup)  # the old backup goes back in its place, with its info
        raise
    log("  %s has changed (game update?): it's the new original, the previous one is in %s"
        % (os.path.basename(path), old))
    return new_lines, False


def _needs_backup(path, data):
    """True if applying will need to create (or renew) the backup of this file: it needs space."""
    if not os.path.exists(path + project.BACKUP_SUFFIX):
        return True
    info = project.read_info(path) or {}
    current = project._hash_bytes(data)
    return current != info.get("original_hash") and current not in info.get("written_hashes", [])


def apply_bytes(folder, lever_name, plans, log=print, value_=None, description=None):
    """Applies byte changes to several files, all or nothing.

    piani: [{'path': assoluto, 'changes': [(offset, byte_prima, byte_dopo)]}]. A change already made (the
    'dopo' bytes are already there) is skipped; bytes different from both 'before' and 'dopo' stop everything.
    Returns how many files were written. If something goes wrong (an interruption too) it puts every file back as it
    was BEFORE this operation and raises ConfiguratorError."""
    prepared = []
    for plan in plans:
        path = plan["path"]
        with open(path, "rb") as f:
            data = f.read()
        new_items = bytearray(data)
        for offset, before, after in plan["changes"]:
            current_count = bytes(data[offset:offset + len(before)])
            if current_count == bytes(after):
                continue
            if current_count != bytes(before):
                raise ConfiguratorError("%s: unexpected bytes at 0x%X (%s instead of %s): this is not the expected "
                                        "version of the file, I'm not changing anything." % (
                                              os.path.basename(path), offset, current_count.hex(), bytes(before).hex()))
            new_items[offset:offset + len(after)] = after
        if bytes(new_items) != data:
            prepared.append((path, data, bytes(new_items)))
    rel = [os.path.relpath(p["path"], folder).replace(os.sep, "/") for p in plans]
    if not prepared:
        _record_lever(folder, lever_name, "applied", value_, rel, description)
        return 0
    for path, _, _ in prepared:
        if not os.access(path, os.W_OK):
            raise ConfiguratorError("%s is read-only: remove the read-only protection and try again. I haven't made "
                                    "any changes." % os.path.basename(path))
    needed = sum(len(d) for p, d, _ in prepared if _needs_backup(p, d))
    if shutil.disk_usage(folder).free < needed + SPACE_MARGIN:
        raise ConfiguratorError("Insufficient free space for backups (requires %.0f MB): I haven't made any changes."
                                % ((needed + SPACE_MARGIN) / 1048576))
    _game_closed(folder)
    previous = lever(folder, lever_name)
    _record_lever(folder, lever_name, "in_progress", value_, rel, description)
    new_backups, writes = [], []
    try:
        for path, data, new_items in prepared:
            info, new = _backup_ready(path, data, lever_name, log)
            if new:
                new_backups.append(path)
            fingerprint = project._hash_bytes(new_items)
            info["written_hashes"] = ([h for h in info.get("written_hashes", []) if h != fingerprint]
                                    + [fingerprint])[-project.MAX_FINGERPRINTS:]
            info["configurator"] = lever_name
            project.save_info(path, info)
        _game_closed(folder)
        for path, _, new_items in prepared:
            writes.append(path)
            adapter_webjson.write_atomic(path, new_items)
            if project._hash_file(path) != project._hash_bytes(new_items):
                raise ConfiguratorError("%s: what was written doesn't read back the same" % os.path.basename(path))
    except BaseException as e:
        remaining = _roll_back(prepared, new_backups, writes, log)
        try:
            if previous:
                _put_back_lever(folder, lever_name, previous)
            else:
                _remove_lever(folder, lever_name)
        except OSError:
            pass
        if not isinstance(e, (OSError, ConfiguratorError, RuntimeError)):
            raise  # interruption (Ctrl+C...): the files have already been put back as they were
        if remaining:
            raise ConfiguratorError("Change failed (%s). I couldn't put these back as they were: %s. Close the "
                                    "programs that use them and press 'Restore originals'."
                                      % (e, ", ".join(remaining))) from e
        raise ConfiguratorError("Change failed (%s): I've put everything back as it was." % e) from e
    _record_lever(folder, lever_name, "applied", value_, rel, description)
    return len(prepared)


def _roll_back(prepared, new_backups, writes, log):
    """Puts every file back as it was before the operation. Returns the names of the files it could not restore."""
    remaining = []
    for path, data, _ in prepared:
        name = os.path.basename(path)
        try:
            if path in new_backups:
                # backup created now: the file goes back to the original (= as it was), backup and info disappear
                if project.read_info(path) and not project.restore_file(path, lambda _m: None):
                    remaining.append(name)
            elif path in writes:
                # the file already had a backup (changed before, or updated by the game): it goes back to how it was
                if not os.path.exists(path) or project._hash_file(path) != project._hash_bytes(data):
                    adapter_webjson.write_atomic(path, data)
                    if project._hash_file(path) != project._hash_bytes(data):
                        remaining.append(name)
        except (OSError, RuntimeError) as e:
            remaining.append(name)
            log("  ! %s hasn't been restored to its original condition (%s)" % (name, e))
    return remaining


def repair_interrupted(folder, log=print):
    """If an operation was interrupted halfway (record 'in_progress'), puts back the originals of that lever."""
    for name, data in list(read_file(folder).get("levers", {}).items()):
        if isinstance(data, dict) and data.get("state") == "in_progress":
            log("  The previous operation (%s) was interrupted halfway through: I'm reverting to the original state."
                % name)
            restore(folder, name, log)


def _game_closed(folder):
    try:
        is_open = system.game_open(folder)
    except system.SysError as e:
        raise ConfiguratorError(str(e)) from e
    if is_open:
        raise ConfiguratorError("The game is open: close it and try again. I haven't made any changes.")


def _record_lever(folder, name, state, value_, file, description):
    data = read_file(folder)
    data.setdefault("levers", {})[name] = {"state": state, "value": value_, "file": file,
                                         "description": description, "date": _now()}
    _save(folder, data)


def _put_back_lever(folder, name, entry):
    data = read_file(folder)
    data.setdefault("levers", {})[name] = entry
    _save(folder, data)


def _remove_lever(folder, name):
    data = read_file(folder)
    if name in data.get("levers", {}):
        del data["levers"][name]
        _save(folder, data)


# --- restore --------------------------------------------------------------------------------------

def restore(folder, lever_name, log=print):
    """Puts the lever's files back as they were. None if the lever is not there, True if done, False if something is
    left to do (the record stays to try again). The consent given for the game stays."""
    data = lever(folder, lever_name)
    if not data:
        return None
    try:
        _game_closed(folder)
    except ConfiguratorError as e:
        log("  ! %s" % e)
        return False
    ok = True
    for rel in data.get("file", []):
        path = os.path.join(folder, *rel.split("/"))
        info = project.read_info(path)
        if not info:
            continue  # already put back (e.g. by "Restore originals") or never touched
        try:
            if not project.restore_file(path, log):
                ok = False
        except OSError as e:
            ok = False
            log("  ! %s hasn't been restored to its original condition (%s)" % (os.path.basename(path), e))
    if ok:
        _remove_lever(folder, lever_name)
    return ok


def configurator_file(path):
    """True if the file was modified by the configurator (not by a translation)."""
    info = project.read_info(path)
    return bool(info and info.get("configurator"))
