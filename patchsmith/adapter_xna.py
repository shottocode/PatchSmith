"""XNA / FNA / MonoGame adapter, and .NET games in general: translates the .NET resources (.resources).

Many .NET games keep their texts in localized resources: a "neutral" version inside the executable
(e.g. RogueCastle.Resources.LocStrings.resources in RogueLegacy.exe) and a "satellite assembly" per language
(de/RogueLegacy.resources.dll, fr/...). .NET first looks for the satellite of the requested language and only if
it is missing uses the texts inside the executable.

So PatchSmith does NOT modify the game's files:
1. it reads the neutral resources from the executable, read-only;
2. it translates them (translation memory + Google, with the placeholders protected);
3. it adapts the characters to those the game's fonts can draw (in XNA a character missing
   from the font makes the game crash);
4. it creates NEW satellites (e.g. en/RogueLegacy.resources.dll and it/RogueLegacy.resources.dll) with the
   C# compiler that every Windows has (.NET Framework), and verifies them by reading them back.
To go back, it is enough to remove the added satellites (listed with their fingerprints in patchsmith-xna.json).

Limits: assemblies signed with a strong name and non-string resources are not handled.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import tempfile

from . import __version__, dotnet, language, detection, xnb
from .characters import ASCII, adapt_chars  # noqa: F401 (also used by the tests)
from .adapter_webjson import TMP_SUFFIX, write_atomic
from .system import game_open

MANIFEST = "patchsmith-xna.json"
MARKER = "Patchsmith"  # [AssemblyProduct] of the satellites we create: a game's satellite does not have it
_REGION = {"en": "US", "ja": "JP", "zh": "CN", "ko": "KR", "pt": "BR", "sv": "SE", "da": "DK", "cs": "CZ",
            "el": "GR", "uk": "UA", "he": "IL", "ar": "SA", "hi": "IN", "vi": "VN"}
_CULTURE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class XnaError(Exception):
    def __init__(self, message="", code=None):
        super().__init__(message)
        self.code = code  # outcome for the interface (see outcome.py), e.g. GIOCO_APERTO


# --- analysis ------------------------------------------------------------------------------------

def _assembly(path):
    try:
        return dotnet.Assembly(path)
    except (dotnet.DotnetError, struct.error, IndexError, ValueError, OSError):
        return None


def _satellite_name(assembly_name):
    return assembly_name + ".resources.dll"


def _culture_folders(folder):
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    return sorted(v for v in entries if _CULTURE.match(v) and os.path.isdir(os.path.join(folder, v)))


def find_resources(folder):
    """The game's localized resources: one entry per assembly that has resources with satellites in other languages.

    The satellites created by PatchSmith (internal marker) do not count as languages of the game."""
    cultures = _culture_folders(folder)
    found = []
    for file_name in sorted(os.listdir(folder)):
        if not file_name.lower().endswith((".exe", ".dll")) or file_name.lower().endswith(".resources.dll"):
            continue
        path = os.path.join(folder, file_name)
        a = _assembly(path)
        if a is None:
            continue
        try:
            identity = a.identity()
            resources = a.resources()
        except dotnet.DotnetError:
            continue
        if identity["cultura"]:
            continue
        bases = {}
        for name in resources:
            if not name.endswith(".resources"):
                continue
            base = name[:-len(".resources")]
            present = []
            for c in cultures:
                sat = _assembly(os.path.join(folder, c, _satellite_name(identity["nome"])))
                try:
                    if sat and not _has_marker(sat) and "%s.%s.resources" % (base, c) in sat.resources():
                        present.append(c)
                except (dotnet.DotnetError, ValueError):
                    continue
            if present and _strings(a, base):
                bases[base] = present
        if bases:
            neutral, _ = a.neutral_language()
            found.append({"file": file_name, "assembly": a, "nome": identity["nome"],
                            "versione": a.satellite_version(), "firmato": bool(identity["chiave_pubblica"]),
                            "runtime": a.runtime_version, "basi": bases, "lingua_neutra": neutral})
    return found


def _strings(assembly, base):
    """{nome: testo} of the neutral resource, or {} if it cannot be read."""
    try:
        return dotnet.resources_strings(dotnet.read_resources(assembly.read_resource(base + ".resources")))
    except (dotnet.DotnetError, ValueError):
        return {}




def _findable(chars):
    """Characters that XNA really finds: it looks them up with a binary search, so the order of the list matters."""
    def findable(c):
        low, high = 0, len(chars) - 1
        while low <= high:
            mid = low + ((high - low) >> 1)
            if chars[mid] == c:
                return True
            low, high = (mid + 1, high) if chars[mid] < c else (low, mid - 1)
        return False
    return {c for c in chars if findable(c)}


def font_chars(folder, log=None):
    """Characters that ALL the game's XNB text fonts can draw.

    - fonts with almost no letters (only digits, for numbers) do not count;
    - an uppercase-only font (the game converts to uppercase) also counts for lowercase;
    - if an .xnb cannot be read (e.g. compressed) it might be a font: to be safe, ASCII only;
    - None (no limit) only if the game has no .xnb file at all."""
    common, unreadable, seen = None, 0, 0
    root = os.path.join(folder, "Content")
    for current_folder, _, files in os.walk(root):
        for name in files:
            if not name.lower().endswith(".xnb"):
                continue
            seen += 1
            try:
                with open(os.path.join(current_folder, name), "rb") as f:
                    data = f.read()
                if xnb.main_kind(data) != "SpriteFontReader":
                    continue
                font = xnb.read_spritefont(data)
            except (xnb.XnbError, struct.error, IndexError, UnicodeDecodeError, ValueError, OSError):
                unreadable += 1
                continue
            chars = _findable(font["caratteri"])
            letters = [c for c in chars if c.isalpha()]
            if len(letters) < 20:
                continue
            if not any(c.islower() for c in letters):
                chars |= {c.lower() for c in chars}
            elif not any(c.isupper() for c in letters):
                chars |= {c.upper() for c in chars}
            common = chars if common is None else common & chars
    if unreadable:
        common = ASCII if common is None else common & ASCII
        if log:
            log("  ! %d file .xnb non leggibili (compressi?): per sicurezza uso solo caratteri ASCII" % unreadable)
    elif common is None and seen and log:
        log("  ! Nessun font XNB riconosciuto: per sicurezza uso solo caratteri ASCII")
    if common is None and seen:
        common = ASCII
    return common


def analyze(folder):
    """{"insiemi": usable resources, "problemi": [...]}. Assemblies signed with a strong name are excluded."""
    resource_sets, problems = [], []
    for i in find_resources(folder):
        if i["firmato"]:
            problems.append("%s e' firmato con nome sicuro: non ancora supportato (lo salto)" % i["file"])
        else:
            resource_sets.append(i)
    return {"insiemi": resource_sets, "problemi": problems}


def present_languages(resource_sets):
    """Languages the game already has (satellites and declared neutral language), as base codes ('de', 'fr'...)."""
    present = {c.split("-")[0].lower() for i in resource_sets for cultures in i["basi"].values() for c in cultures}
    return present | {(i["lingua_neutra"] or "").split("-")[0].lower() for i in resource_sets if i["lingua_neutra"]}


# --- manifest -----------------------------------------------------------------------------------

def _valid_rel(rel):
    parts = rel.split("/") if isinstance(rel, str) else []
    return (len(parts) == 2 and _CULTURE.match(parts[0]) is not None and parts[1].endswith(".resources.dll")
            and ":" not in rel and "\\" not in rel and parts[1] not in (".", ".."))


def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return None

    def ours(entries):
        return isinstance(entries, dict) and all(_valid_rel(r) and isinstance(h, str) for r, h in entries.items())

    if not (isinstance(m, dict) and m.get("patchsmith") is True and ours(m.get("file"))
            and ours(m.get("precedenti", {})) and set(m.get("precedenti", {})) <= set(m["file"])):
        return None
    folders = m.get("cartelle_create", [])
    if not (isinstance(folders, list) and all(isinstance(c, str) and _CULTURE.match(c) for c in folders)):
        return None
    return m


def manifest_present(folder):
    return os.path.exists(os.path.join(folder, MANIFEST))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _has_marker(assembly):
    expected = b"\x01\x00" + bytes([len(MARKER)]) + MARKER.encode("ascii")
    return any(n == "System.Reflection.AssemblyProductAttribute" and v.startswith(expected)
               for n, v in assembly.assembly_attributes())


def _is_ours(path):
    """True if the file is a satellite created by PatchSmith (it has the internal marker)."""
    if not os.path.isfile(path):
        return False
    a = _assembly(path)
    return a is not None and _has_marker(a)


def our_satellites(folder):
    """Relative paths ('en/X.resources.dll') of the satellites created by PatchSmith, and of their temporary files."""
    found, temp_files = [], []
    for c in _culture_folders(folder):
        try:
            names = os.listdir(os.path.join(folder, c))
        except OSError:
            continue
        for name in names:
            if name.lower().endswith(".resources.dll") and _is_ours(os.path.join(folder, c, name)):
                found.append(c + "/" + name)
            elif name.lower().endswith(".resources.dll" + TMP_SUFFIX):
                temp_files.append(c + "/" + name)
    return found, temp_files


def installed(folder):
    """True if there is something of PatchSmith in the folder (manifest or satellites), even without a manifest."""
    return manifest_present(folder) or any(our_satellites(folder))


# --- building the satellites --------------------------------------------------------------------

def compiler(runtime):
    """The .NET Framework csc.exe suited to the game's runtime (v4.x or v2.x/3.5)."""
    windir = os.environ.get("WINDIR", r"C:\Windows")
    versions = ["v4.0.30319"] if runtime.startswith("v4") else ["v3.5", "v2.0.50727"]
    for framework in ("Framework64", "Framework"):
        for v in versions:
            p = os.path.join(windir, "Microsoft.NET", framework, v, "csc.exe")
            if os.path.exists(p):
                return p
    raise XnaError("Non trovo il compilatore C# di .NET Framework %s (csc.exe), che serve a creare i file "
                    "di traduzione. Attiva .NET Framework nelle funzionalita' di Windows." % runtime)


def build_satellite(runtime, assembly_name, version, culture, resources, work_folder):
    """Creates <cultura>/<nome>.resources.dll with the resources {nome_risorsa: byte} and verifies it. Returns the
    bytes."""
    cs = os.path.join(work_folder, "satellite.cs")
    with open(cs, "w", encoding="utf-8") as f:
        f.write('[assembly: System.Reflection.AssemblyCulture("%s")]\n' % culture)
        f.write('[assembly: System.Reflection.AssemblyVersion("%s")]\n' % version)
        f.write('[assembly: System.Reflection.AssemblyProduct("%s")]\n' % MARKER)
    arguments = [compiler(runtime), "/nologo", "/noconfig", "/target:library", "/optimize+",
                 "/out:" + os.path.join(work_folder, _satellite_name(assembly_name))]
    for k, (name, data) in enumerate(sorted(resources.items())):
        res_file = os.path.join(work_folder, "r%d.resources" % k)
        with open(res_file, "wb") as f:
            f.write(data)
        arguments.append("/resource:%s,%s" % (res_file, name))
    arguments.append(cs)
    # (the compiler writes in the console encoding: from Python 3.15 the text would be read as UTF-8)
    outcome = subprocess.run(arguments, capture_output=True, text=True, cwd=work_folder,
                           encoding="oem" if os.name == "nt" else None, errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    output = os.path.join(work_folder, _satellite_name(assembly_name))
    if outcome.returncode != 0 or not os.path.exists(output):
        raise XnaError("Il compilatore C# non e' riuscito a creare il satellite: %s"
                        % (outcome.stdout + outcome.stderr).strip()[:500])
    with open(output, "rb") as f:
        data = f.read()
    # check: identity and resources exactly the expected ones
    sat = dotnet.Assembly(data=data)
    identity = sat.identity()
    if (identity["nome"] != assembly_name + ".resources" or identity["cultura"] != culture
            or identity["versione"] != version or identity["chiave_pubblica"] or not _has_marker(sat)):
        raise XnaError("Satellite creato con identita' inattesa: %s" % identity)
    if set(sat.resources()) != set(resources) or any(sat.read_resource(n) != d for n, d in resources.items()):
        raise XnaError("Satellite creato con risorse diverse da quelle previste")
    os.remove(output)
    return data


# --- installation -------------------------------------------------------------------------------

def _checks(folder):
    if game_open(folder):
        raise XnaError("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    outcome = detection.detect(folder)
    names = [r["nome"] for r in outcome["risultati"] if r["sezione"] == "AntiCheat"]
    if names:
        raise XnaError("Questo gioco ha un anti-cheat (%s): non aggiungo file (rischio di ban)." % ", ".join(names),
                        "ANTICHEAT")


def texts_to_translate(folder, log=print):
    """(insiemi, unique texts to translate). Only strings with letters."""
    resource_sets = analyze(folder)["insiemi"]
    if not resource_sets:
        raise XnaError("Non ho trovato risorse di testo localizzate (.resources con satelliti) utilizzabili nel gioco.")
    texts = {}
    for i in resource_sets:
        for base in i["basi"]:
            strings = _strings(i["assembly"], base)
            log("  %s: %s (%d testi; lingue gia' presenti: %s)" % (i["file"], base, len(strings),
                                                                   ", ".join(i["basi"][base])))
            for text in strings.values():
                if text and any(c.isalpha() for c in text):
                    texts.setdefault(text, None)
    return resource_sets, list(texts)


def cultures_to_create(item_set, origin, dest_lang):
    """Cultures of the satellites to add: the original language (games that choose it explicitly,
    e.g. "English" in the menu) and the target one (games that follow the Windows language)."""
    cultures = []
    neutral = (item_set["lingua_neutra"] or "").lower()
    present = {b.split("-")[0].lower() for cultures_b in item_set["basi"].values() for b in cultures_b}
    for c in (origin.split("-")[0].lower(), dest_lang.split("-")[0].lower()):
        if c in present:
            continue  # the game already has that language
        if neutral.split("-")[0] == c:
            # .NET does not look for the satellite of the declared neutral language ([NeutralResourcesLanguage]):
            # if it is generic ("en") I use the most common variant ("en-US"), which is looked for instead
            if neutral != c:
                continue
            c = "%s-%s" % (c, _REGION.get(c, c.upper()))
        if c not in cultures:
            cultures.append(c)
    return cultures


def install(folder, origin, dest_lang, profile_name, memory, log=print):
    """Creates the translated satellites using 'memoria' {originale: traduzione}. Returns the number of translated
    texts."""
    folder = os.path.abspath(folder)
    _checks(folder)
    manifest = read_manifest(folder) if manifest_present(folder) else None
    if manifest_present(folder) and manifest is None:
        raise XnaError("Il file %s non e' valido: non lo uso. Controlla la cartella a mano." % MANIFEST)
    game_info = analyze(folder)
    for problem in game_info["problemi"]:
        log("  ! " + problem)
    resource_sets = game_info["insiemi"]
    if not resource_sets:
        raise XnaError("Non ho trovato risorse di testo localizzate utilizzabili nel gioco.")
    cultures_by_set = {}
    for i in resource_sets:
        cultures = cultures_to_create(i, origin, dest_lang)
        for c in cultures:
            if not _CULTURE.match(c):
                raise XnaError("Codice di lingua non valido: '%s'. Scegli la lingua dall'elenco." % c)
        cultures_by_set[i["file"]] = cultures
    allowed = font_chars(folder, log)
    if allowed is not None:
        log("  Font del gioco: %d caratteri disegnabili da tutti i font (gli altri vengono adattati)" % len(allowed))

    plan, translated_count, total, adapted, rejected = {}, 0, 0, 0, 0
    work = tempfile.mkdtemp(prefix="patchsmith-xna-")
    try:
        for i in resource_sets:
            cultures = cultures_by_set[i["file"]]
            if not cultures:
                log("  %s: niente da aggiungere (lingua gia' presente nel gioco)" % i["file"])
                continue
            by_culture = {c: {} for c in cultures}
            for base in i["basi"]:
                original = i["assembly"].read_resource(base + ".resources")
                info = dotnet.read_resources(original)
                new_lines = {}
                for name, text in dotnet.resources_strings(info).items():
                    total += 1
                    t = memory.get(text)
                    if text.strip() == language.endonym(origin) and language.endonym(dest_lang):
                        t = language.endonym(dest_lang)  # the menu's "English" entry now shows Italian
                    if not t or t == text:
                        continue
                    ready_text = adapt_chars(t, None if allowed is None else allowed | set(text), dest_lang)
                    if ready_text is None:
                        rejected += 1
                        continue
                    adapted += ready_text != t
                    new_lines[name] = ready_text
                    translated_count += 1
                data = dotnet.write_resources(info, new_lines)
                checks = dotnet.resources_strings(dotnet.read_resources(data))
                expected = dict(dotnet.resources_strings(info), **new_lines)
                if checks != expected:
                    raise XnaError("Controllo delle risorse tradotte fallito per %s: non scrivo nulla" % base)
                for c in cultures:
                    by_culture[c]["%s.%s.resources" % (base, c)] = data
            for c, resources in by_culture.items():
                subfolder = os.path.join(work, c)
                os.makedirs(subfolder)
                rel = c + "/" + _satellite_name(i["nome"])
                if not _valid_rel(rel):
                    raise XnaError("Percorso del satellite non valido: %s" % rel)
                plan[rel] = build_satellite(i["runtime"], i["nome"], i["versione"], c, resources, subfolder)
    finally:
        shutil.rmtree(work, ignore_errors=True)

    # a file already there is replaced only if it is ours (marker)
    for rel in plan:
        target_file = os.path.join(folder, *rel.split("/"))
        if os.path.exists(target_file) and not _is_ours(target_file):
            raise XnaError("Esiste gia' %s non creato da PatchSmith: non lo sovrascrivo." % rel)
    ours_before, _ = our_satellites(folder)
    created_folders = list((manifest or {}).get("cartelle_create", []))
    for rel in plan:
        c = rel.split("/")[0]
        if not os.path.isdir(os.path.join(folder, c)) and c not in created_folders:
            created_folders.append(c)
    new_items = {rel: hashlib.sha256(data).hexdigest() for rel, data in plan.items()}

    def write_manifest(file):
        write_atomic(os.path.join(folder, MANIFEST), json.dumps({
            "versione": 1, "patchsmith": True, "patchsmith_versione": __version__, "lingua": dest_lang,
            "profilo": profile_name,
            "data": datetime.datetime.now().isoformat(timespec="seconds"),
            "file": file, "cartelle_create": created_folders,
            "sorgenti": {i["file"]: _sha256(os.path.join(folder, i["file"])) for i in resource_sets},
        }, ensure_ascii=False, indent=2).encode("utf-8"))

    # 1) manifest BEFORE creating folders and files: an interruption can always be restored
    write_manifest(new_items)
    # 2) temporary files next to them (they do not have the right name, so .NET does not load them)
    ready = {}
    try:
        for rel, data in plan.items():
            target_file = os.path.join(folder, *rel.split("/"))
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            ready[rel] = target_file
            with open(target_file + TMP_SUFFIX, "wb") as f:
                f.write(data)
    except BaseException:
        for p in ready.values():
            if os.path.exists(p + TMP_SUFFIX):
                os.remove(p + TMP_SUFFIX)
        _remove_empty_folders(folder, created_folders)
        raise
    # 3) into their place
    for rel, p in ready.items():
        os.replace(p + TMP_SUFFIX, p)
    # 4) our satellites no longer needed (e.g. change of language) and leftover temporary files
    ours_after, temp_files = our_satellites(folder)
    for rel in set(ours_before + ours_after) - set(new_items):
        p = os.path.join(folder, *rel.split("/"))
        if _is_ours(p):
            os.remove(p)
    for rel in temp_files:
        os.remove(os.path.join(folder, *rel.split("/")))
    _remove_empty_folders(folder, created_folders, keep={rel.split("/")[0] for rel in new_items})
    created_folders = [c for c in created_folders if os.path.isdir(os.path.join(folder, c))]
    write_manifest(new_items)
    log("  Satelliti di traduzione scritti: %s" % ", ".join(sorted(new_items)))
    log("  %d testi tradotti su %d%s%s" % (translated_count, total,
                                          "; %d adattati ai caratteri dei font" % adapted if adapted else "",
                                          "; %d lasciati in originale (caratteri non disegnabili)" % rejected
                                          if rejected else ""))
    return translated_count


def _remove_empty_folders(folder, folders, keep=()):
    for c in folders:
        p = os.path.join(folder, c)
        if c not in keep and os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)


def uninstall(folder, log=print):
    """Removes the satellites created by PatchSmith (recognized by the internal marker), even without a manifest."""
    folder = os.path.abspath(folder)
    if not installed(folder):
        return False
    manifest = read_manifest(folder) if manifest_present(folder) else None
    if manifest_present(folder) and manifest is None:
        raise XnaError("Il file %s non e' valido: per sicurezza non cancello nulla." % MANIFEST)
    if game_open(folder):
        raise XnaError("Il gioco e' aperto: chiudilo e riprova.", "GIOCO_APERTO")
    ours, temp_files = our_satellites(folder)
    for rel in ours:
        os.remove(os.path.join(folder, *rel.split("/")))
        log("  Rimosso %s" % rel)
    for rel in temp_files:
        os.remove(os.path.join(folder, *rel.split("/")))
    folders = set((manifest or {}).get("cartelle_create", [])) | {rel.split("/")[0] for rel in ours + temp_files}
    _remove_empty_folders(folder, sorted(folders))
    for name in (MANIFEST + TMP_SUFFIX, MANIFEST):
        if os.path.exists(os.path.join(folder, name)):
            os.remove(os.path.join(folder, name))
    log("  Traduzione rimossa: il gioco usa di nuovo solo i suoi file originali.")
    return True


def state(folder, log=print):
    manifest = read_manifest(folder)
    if not manifest:
        ours, _ = our_satellites(folder)
        if ours:
            log("  ! Satelliti di PatchSmith presenti senza manifesto: %s ('Ripristina originali' li toglie)"
                % ", ".join(ours))
            return True
        log("  Traduzione non installata.")
        return False
    for rel, fingerprint in manifest["file"].items():
        p = os.path.join(folder, *rel.split("/"))
        ok = os.path.exists(p) and _sha256(p) == fingerprint
        log("  %s %s (lingua %s, %s)" % ("OK " if ok else "NO ", rel, manifest.get("lingua"), manifest.get("data")))
    for name, fingerprint in manifest.get("sorgenti", {}).items():
        p = os.path.join(folder, name)
        if not os.path.exists(p) or _sha256(p) != fingerprint:
            log("  ! Il gioco e' stato aggiornato (%s): premi 'Analizza e traduci' per tradurre anche i testi nuovi."
                % name)
    return True
