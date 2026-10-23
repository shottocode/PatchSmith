"""Report for asking for help ("Create report"): a .zip file on the Desktop with what is needed to understand what
happened with a game, without having to dig through folders.

It contains: the logs of the latest operations (logs/ folder of the data), the game profile, the settings
WITHOUT the DeepL key and email, the state of the translators, the engine detection, the BepInEx logs and the
XUnity configuration, the end of the log and of the sentence diary of the live translator, the logs of
Unity (Player.log, Player-prev.log and the last crash) and of Unreal (<progetto>.log), PatchSmith's manifests in the
game, the list of texts left untranslated with the reason (problems.json), the fixes not yet written to the game
(Unity: to_write.json), the letters of the fonts read (Unity Localization: font.json), the result of the after-play
check (<gioco>.checks.json) and the PatchSmith version.
It contains no game files and no saves. In the texts the key and the email (even if they ended up in a log by chance)
become "<tolto>" and the Windows user folder becomes %USERPROFILE%.
"""
import datetime
import glob
import json
import os
import platform
import re
import sys
import tempfile
import urllib.parse
import zipfile

from . import __version__, legacy

SECRET_KEYS = ("key", "email", "password", "token", "key")  # parts of the setting names to remove
MAX_BYTES = 512 * 1024  # of each log the end is kept
MAX_LOGS = 5  # operation logs (the game's most recent)
# logs that do not belong to a game: window errors, installation of the local translator, check at startup
GENERAL_LOGS = ("interfaccia", "traduttore-locale", "controllo")
REMOVED = "<tolto>"


def desktop_folder():
    """The user's Desktop, even if Windows has moved it (e.g. into OneDrive); if it cannot be found, PatchSmith's
    logs folder."""
    from .project import data_folder
    if sys.platform.startswith("win"):
        try:
            import ctypes
            buffer = ctypes.create_unicode_buffer(1024)
            # CSIDL_DESKTOPDIRECTORY: the user's real Desktop folder
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buffer) == 0 and \
                    os.path.isdir(buffer.value):
                return buffer.value
        except (AttributeError, OSError):
            pass
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    return desktop if os.path.isdir(desktop) else data_folder("logs")


def is_secret(key):
    return any(s in str(key).lower() for s in SECRET_KEYS)


def clean_settings(data):
    """The settings without keys, emails and the like."""
    return {k: v for k, v in data.items() if not is_secret(k)} if isinstance(data, dict) else {}


def _secrets():
    """The values that must never get out: DeepL key, email (from the settings and from the environment variables)."""
    from .project import data_folder
    values = set()
    try:
        with open(data_folder("settings.json"), encoding="utf-8") as f:
            data = legacy.load(f)
    except (OSError, ValueError):
        data = {}
    if isinstance(data, dict):
        values |= {v.strip() for k, v in data.items() if is_secret(k) and isinstance(v, str)}
    values |= {os.environ.get(v, "").strip() for v in ("PATCHSMITH_DEEPL", "PATCHSMITH_EMAIL")}
    return sorted((v for v in values if len(v) >= 4), key=len, reverse=True)


def clean(text, secrets):
    """Text without the secrets and with the user folder written as %USERPROFILE%."""
    for s in secrets:
        # also as it appears in a web address (e.g. the MyMemory email: 'de=nome%40dominio.it')
        for shape in dict.fromkeys((s, urllib.parse.quote(s, safe=""), urllib.parse.quote_plus(s))):
            text = re.sub(re.escape(shape), REMOVED, text, flags=re.I)
    home = os.path.expanduser("~").rstrip("\\/")
    if len(home) > 3:
        for shape in {home, home.replace("\\", "/")}:
            text = re.sub(re.escape(shape), "%USERPROFILE%", text, flags=re.I)
            text = text.replace(json.dumps(shape)[1:-1], "%USERPROFILE%")  # as it appears inside a JSON
    return text


def _read(path, tail=True):
    with open(path, "rb") as f:
        if tail:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - MAX_BYTES))
            data = f.read()
            if size > MAX_BYTES:
                data = ("[... only the last %d KB of %d KB kept ...]\n" % (MAX_BYTES // 1024, size // 1024)
                        ).encode("utf-8") + data
        else:
            data = f.read()
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    return data.decode("utf-8-sig", "replace")


def game_logs(names, maximum=MAX_LOGS, general=GENERAL_LOGS):
    """The logs of that game's most recent operations, recognized by the name of the game profile or of the folder
    at the end of the file name (if there are none: the most recent overall), plus the latest of each of the
    general logs (window errors, installation of the local translator, check at startup): so the
    general logs do not push out the game's, and vice versa."""
    from .project import data_folder
    names = [n for n in ([names] if isinstance(names, str) else names) if n and n not in general]
    all_ = sorted(glob.glob(os.path.join(glob.escape(data_folder("logs")), "*.log")), key=_date, reverse=True)

    def logs_of(n):
        return [p for p in all_ if os.path.basename(p)[:-4].endswith("-" + n)]

    its_reports = [p for p in all_ if any(os.path.basename(p)[:-4].endswith("-" + n) for n in names)]
    selected = (its_reports or all_)[:maximum]
    for g in general:
        selected += [p for p in logs_of(g)[:1] if p not in selected]
    return selected


def _date(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0


def _unity(root):
    """[(name in the zip, path)] of the Unity logs: Player.log and Player-prev.log (in LocalLow) and the Player.log
    of the last crash (in %TEMP%), found with the company and product written in <gioco>_Data/app.info."""
    from . import adapter_unity
    try:
        data = adapter_unity.analyze(root).get("data_folder")
        with open(os.path.join(data, "app.info"), encoding="utf-8", errors="replace") as f:
            lines = [r.strip() for r in f.read().splitlines()]
    except Exception:  # noqa: BLE001 - not Unity or unreadable: the report is made anyway, without these
        return []
    if len(lines) < 2 or not lines[0] or not lines[1]:
        return []
    company, product = lines[0], lines[1]
    user = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    locallow = os.path.join(user, "AppData", "LocalLow", company, product)
    entries = [("unity/" + n, os.path.join(locallow, n)) for n in ("Player.log", "Player-prev.log")]
    crash = os.path.join(os.environ.get("TEMP") or tempfile.gettempdir(), company, product, "Crashes")
    try:
        folders = sorted((os.path.join(crash, d) for d in os.listdir(crash)), key=_date, reverse=True)
    except OSError:
        folders = []
    for d in folders:
        if os.path.isfile(os.path.join(d, "Player.log")):
            entries.append(("unity/crash-%s-Player.log" % os.path.basename(d), os.path.join(d, "Player.log")))
            break
    return entries


def create(folder, dest_lang=None, outcome=None, logs=None, extra=None):
    """Creates the zip and returns its path. cartella: the game's folder (even empty: then only the general
    data); destinazione: where to write it (default: the Desktop); esito: the last outcome (outcome.Outcome);
    registri: the operation logs to include (default: the game's latest); extra: extra lines for
    the summary."""
    from . import adapter_unity, auto as automatic, ollama, detection
    from .project import data_folder, find_info
    from .text import slug
    secrets = _secrets()
    folder = os.path.abspath(folder) if folder and os.path.isdir(folder) else None
    found_profile_path = automatic.find_profile(folder) if folder else None
    profile = {}
    if found_profile_path:
        try:
            with open(found_profile_path, encoding="utf-8") as f:
                profile = legacy.load(f)
        except (OSError, ValueError):
            profile = {}
    root = profile.get("game_folder") or folder
    root = os.path.abspath(root) if root and os.path.isdir(root) else folder
    name = (os.path.splitext(os.path.basename(found_profile_path))[0] if found_profile_path else
            slug(os.path.basename(folder)) if folder else "")
    texts = {}  # name in the zip -> text
    problems = []

    def add(zip_name, path, tail=True):
        if not path or not os.path.isfile(path):
            return
        try:
            texts[zip_name] = _read(path, tail)
        except OSError as e:
            problems.append("%s: %s" % (zip_name, e))

    # the logs of the game, of the window errors outside an operation, of the installation of the local
    # translator and of the after-play check done at startup
    names = [name] + [slug(os.path.basename(d)) for d in (folder, root) if d]
    for p in logs if logs is not None else game_logs(names):
        add("registri/" + os.path.basename(p), p)
    if found_profile_path:
        add("profilo/" + os.path.basename(found_profile_path), found_profile_path, tail=False)
        # the after-play check: signals seen, fixes already tried and their outcome
        checks = os.path.splitext(found_profile_path)[0] + ".controllo.json"
        add("profilo/" + os.path.basename(checks), checks, tail=False)
    try:
        with open(data_folder("settings.json"), encoding="utf-8") as f:
            texts["settings.json"] = json.dumps(clean_settings(legacy.load(f)), ensure_ascii=False, indent=2)
    except (OSError, ValueError) as e:
        problems.append("settings.json: %s" % e)
    add("services.json", data_folder("services.json"), tail=False)
    for n in ("servizio-xunity.log", "server.log"):
        add("traduttore-locale/" + n, os.path.join(ollama.folder(), n))
    if name:  # the sentences requested while playing (only game texts)
        from .xunity_service import journal_path
        add("traduttore-locale/" + os.path.basename(journal_path(name)), journal_path(name))
    if profile.get("name") and profile.get("target_language"):  # the texts left untranslated, with the reason
        from .project import problems_path
        text_problems = problems_path(profile)
        add("traduzioni/" + os.path.basename(text_problems), text_problems, tail=False)
        # Unity: the fixes from "Fix translations" not yet written to the XUnity cache
        from .corrections import to_write_path
        add("traduzioni/" + os.path.basename(to_write_path(profile)), to_write_path(profile),
                 tail=False)
    if profile.get("name"):  # Unity Localization: the letters the game's fonts can draw (font_unity.py)
        add("traduzioni/%s.font.json" % profile["name"],
                 data_folder("memory", "%s.unity" % profile["name"], "font.json"), tail=False)
    modified = []
    if root:
        try:
            lines = []
            detection.print_summary(detection.detect(root), lines.append)
            texts["rilevamento.txt"] = "\n".join(lines)
        except Exception as e:  # the report is made anyway: it is exactly when something goes wrong that it is needed
            problems.append("detection: %s: %s" % (type(e).__name__, e))
        unity = adapter_unity.find_root(root) or root
        for rel in (adapter_unity.BEPINEX_LOG, adapter_unity.CONFIG_XUNITY, adapter_unity.CONFIG_BEPINEX,
                    adapter_unity.SERVICE_CONFIG, adapter_unity.SERVICE_STATE):
            add("gioco/" + rel, os.path.join(unity, *rel.split("/")))
        for d in dict.fromkeys((root, unity)):
            for manifest in ("patchsmith-unity.json", "patchsmith-unreal.json", "patchsmith-xna.json"):
                add("gioco/" + manifest, os.path.join(d, manifest), tail=False)
        for zip_name, p in _unity(unity):
            add(zip_name, p)
        # Unreal: the game's log, from which the after-play check understands whether the translation pak was
        # loaded and which language is chosen (its problems propose the report itself)
        try:
            from . import adapter_unreal, checks
            manifest = adapter_unreal.read_manifest(root)
            for p in checks._unreal_logs(root, manifest) if manifest else []:
                add("unreal/" + os.path.basename(p), p)
        except Exception as e:  # noqa: BLE001 - the report is made anyway, without these
            problems.append("Unreal log: %s: %s" % (type(e).__name__, e))
        try:
            modified = [os.path.relpath(p, root).replace(os.sep, "/") for p in find_info(root)]
        except OSError:
            pass
    overview = [
        "PatchSmith Report %s" % __version__,
        "Created: %s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "System: %s, Python %s" % (platform.platform(), platform.python_version()),
        "Selected folder: %s" % (folder or "(none)"),
        "Game folder: %s" % (root or "(none)"),
        "Profile: %s (engine %s)" % (name or "(none)", profile.get("engine") or "-"),
        "Game files modified by PatchSmith: %s" % (", ".join(modified[:50]) or "none"),
    ]
    if outcome is not None:
        overview.append("Last result: %s" % json.dumps(outcome.as_dict(), ensure_ascii=False, default=str))
    overview += list(extra or [])
    if problems:
        overview.append("Not included: " + "; ".join(problems))
    overview.append("Content: " + ", ".join(sorted(texts)))
    texts["riepilogo.txt"] = "\n".join(overview)

    dest_lang = dest_lang or desktop_folder()
    os.makedirs(dest_lang, exist_ok=True)
    now = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(dest_lang, "PatchSmith-report-%s%s.zip" % (name + "-" if name else "", now))
    temp_file = path + ".tmp"
    try:
        with zipfile.ZipFile(temp_file, "w", zipfile.ZIP_DEFLATED) as z:
            for zip_name, text in sorted(texts.items()):
                z.writestr(zip_name, clean(text, secrets).encode("utf-8"))
        os.replace(temp_file, path)
    except BaseException:
        if os.path.exists(temp_file):
            os.remove(temp_file)
        raise
    return path
