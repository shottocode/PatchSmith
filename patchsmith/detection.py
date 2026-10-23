"""Game engine detection.

Uses the SteamDB rules (FileDetectionRuleSets, MIT license, file data/rules.ini) on the file
paths, plus checks on the first bytes of the files and a second level for web games
(Electron / NW.js / HTML5). Each result has a score and the list of evidence.
"""
import json
import os
import re
import struct

try:  # regex tree (internal module of Python 3.11): only needed by the _index prefilter
    from re import _parser as _re_parser
    from re._constants import AT, AT_END, BRANCH, LITERAL, SUBPATTERN
except ImportError:
    _re_parser = None

DATA_FOLDER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SECTIONS = ("Engine", "Container", "SDK", "AntiCheat", "Launcher", "Emulator", "Evidence")
WEIGHTS = {"Engine": 3, "Container": 3, "SDK": 1, "AntiCheat": 1, "Launcher": 1, "Emulator": 3, "Evidence": 1}
MAX_FILE = 200_000
MAX_RULE_POINTS = 15  # cap on the points given by the name rules for each entry
KEY_LENGTH = 12  # maximum number of final characters used as a key by the suffix prefilter
# Dependency installers included in some copies of the game: they say nothing about the engine.
IGNORED_FOLDERS = {"_redist", "_commonredist", "redist", "redistributables", "directx", "vcredist"}

# Recommended route per engine (state of Phase 0).
ADVICE = {
    "Web": "Supported now: web-json adapter (scan and translate commands).",
    "Electron": "Supported now: web-json adapter (scan and translate commands).",
    "GemShell": "Text and code in an encrypted archive (GemShell): PatchSmith does not bypass developer protections, "
                "so this game cannot be translated.",
    "NWJS": "Supported now: web-json adapter (scan and translate commands).",
    "RPGMaker": "MV/MZ: the data is JSON (www/data), the web-json adapter works experimentally. Other versions: coming "
                "with rvpacker-txt-rs (Phase 1).",
    "Unity": "Supported (Unity Mono): PatchSmith installs and configures BepInEx + XUnity.AutoTranslator, which "
             "translates the texts inside the game. Unity IL2CPP: supported if it uses Unity Localization (text tables "
             "in bundles), otherwise coming soon.",
    "RenPy": "Coming soon (Phase 1): Ren'Py tl/ framework + unrpyc.",
    "Godot": "Supported (Godot 4) if the game uses Godot's translation tables: PatchSmith reads the English table from "
             "the data package (without modifying it) and adds the translation next to the executable (override.cfg).",
    "GameMaker": "Supported if the game has external localization files (e.g., Localization/english_*.txt): PatchSmith "
                 "will translate them with a backup. Text written in the code (data.win): coming soon.",
    "Unreal": "Supported (Unreal 4, unencrypted paks): PatchSmith translates the .locres files and adds a patch pak. "
              "Unreal 5 / IoStore / encrypted paks: coming soon.",
    "XNA": "Supported if the game has localized .NET resources (.resources with satellites): PatchSmith adds "
           "translated satellites without modifying the game files. Text written in the code: coming soon.",
    "FNA": "Like XNA: localized .NET resources supported.",
    "MonoGame": "Like XNA: localized .NET resources supported.",
    "KiriKiri": "Best case scenario (Phase 3): KirikiriTools / VNTextPatch.",
    "WolfRPGEditor": "Best case scenario (Phase 3): UberWolf + WolfTL.",
}

# Signatures in the first bytes: (regex on the path, signature, engine, description)
SIGNATURES = [
    (r"\.pck$", b"GDPC", "Godot", "Godot .pck archive (GDPC)"),
    (r"(?:^|/)(?:data\.win|game\.unx|game\.ios)$", b"FORM", "GameMaker", "data.win GameMaker (FORM)"),
    (r"\.xp3$", b"XP3\r\n", "KiriKiri", "KiriKiri .xp3 archive"),
    (r"\.rpa$", b"RPA-", "RenPy", "Ren'Py .rpa archive"),
    (r"(?:^|/)(?:globalgamemanagers|data\.unity3d|[^/]+\.bundle)$", b"UnityFS", "Unity",
     "Unity asset bundle (UnityFS)"),
    (r"\.utoc$", b"-==--==--==--==-", "Unreal", "IoStore .utoc Unreal"),
]

WEB_FRAMEWORKS = [
    ("Angular", re.compile(r"(?:^|/)polyfills[-.][\w-]*\.js$|(?:^|/)main[-.][A-Z0-9]{6,}\.js$"), b"ng-version|\xc9\xb5\xc9\xb5|@angular"),
    ("React", None, b"react-dom|__REACT_DEVTOOLS"),
    ("Vue", None, b"__VUE__|vue.runtime"),
    ("Phaser", re.compile(r"(?:^|/)phaser[\w.-]*\.js$", re.I), b"Phaser.Game"),
    ("PixiJS", re.compile(r"(?:^|/)pixi[\w.-]*\.js$", re.I), None),
    ("RPG Maker MV/MZ", re.compile(r"(?:^|/)js/(?:rpg|rmmz)_core\.js$", re.I), None),
    ("TyranoScript", re.compile(r"(?:^|/)tyrano\.js$", re.I), None),
    ("Construct", re.compile(r"(?:^|/)c[23]runtime\.js$|(?:^|/)c3main\.js$", re.I), None),
]


def load_rules(path=None):
    path = path or os.path.join(DATA_FOLDER, "rules.ini")
    rules = {s: [] for s in SECTIONS}
    section = None
    line_re = re.compile(r"^([^=\[;]+?)(?:\[\])?\s*=\s*(.+?)(?:\s+;.*)?$")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                continue
            m = line_re.match(line)
            if not m or section not in rules:
                continue
            try:
                rules[section].append((m.group(1).strip(), re.compile(m.group(2), re.I)))
            except re.error:
                pass  # PCRE rule not supported by Python: skip it
    return rules


def _sequences(tree, limit=256):
    """Expands alternatives and simple groups: each sequence is a possible path of a match."""
    sequences = [[]]
    for op, av in tree:
        if op == BRANCH:
            variants = [s for branch in av[1] for s in _sequences(branch, limit)]
        elif op == SUBPATTERN and not av[1] and not av[2]:  # group without its own flags
            variants = _sequences(av[3], limit)
        else:
            variants = [[(op, av)]]  # opaque element: quantifiers, classes, anchors, lookarounds...
        sequences = [s + v for s in sequences for v in variants]
        if len(sequences) > limit:
            raise ValueError("too many options")
    return sequences


def _keys(rx):
    """Necessary conditions for rx.search() to find something in an ASCII path without newlines.

    Set of pairs ("end", s) / ("contains", s), with s in lowercase: if the regex finds something,
    at least one is true for the lowercased path. None if they cannot be deduced: a regex to always try.
    """
    if _re_parser is None or rx.flags & re.MULTILINE:
        return None
    try:
        sequences = _sequences(_re_parser.parse(rx.pattern, rx.flags))
    except (re.error, ValueError, RecursionError):
        return None
    keys = set()
    for seq in sequences:
        if seq and seq[-1] == (AT, AT_END):  # ends with $: the literal right before it counts
            tail = ""
            for op, av in reversed(seq[:-1]):
                if op != LITERAL or av > 127:
                    break
                tail = chr(av).lower() + tail
            if tail:
                keys.add(("end", tail[-KEY_LENGTH:]))
                continue
        stretch = best = ""  # otherwise the longest literal stretch
        for op, av in seq:
            stretch = stretch + chr(av).lower() if op == LITERAL and av <= 127 else ""
            best = max(best, stretch, key=len)
        if not best:
            return None
        keys.add(("contains", best))
    # The weakest condition is enough: "fine x.dll" makes "fine /x.dll" redundant.
    return {a for a in keys if not any(b != a and _implies(a, b) for b in keys)}


def _implies(a, b):
    """True if every path that satisfies key a also satisfies key b."""
    (kind_a, text_a), (kind_b, text_b) = a, b
    if kind_b == "end":
        return kind_a == "end" and text_a.endswith(text_b)
    return text_b in text_a


def _index(expressions):
    """Prefilter to try many regexes on many paths without calling all of them on each one.

    Returns a function that, given a path, lists in ascending order the indexes of the regexes
    that might find something in it (the others surely not): they must then be verified with search().
    """
    by_ending, by_content, always = {}, {}, []
    for i, rx in enumerate(expressions):
        keys = _keys(rx)
        if keys is None:
            always.append(i)
            continue
        for kind, text in keys:
            (by_ending if kind == "end" else by_content).setdefault(text, []).append(i)
    lengths = sorted({len(t) for t in by_ending})
    contents = list(by_content.items())
    filter_ = re.compile("|".join(map(re.escape, by_content))) if by_content else None
    all_items = list(range(len(expressions)))

    def candidates_for(path):
        # The keys hold for ASCII upper/lower case and with $ only at the end of the string: in the
        # other (very rare) cases all regexes are tried.
        if not path.isascii() or "\n" in path:
            return all_items
        p = path.lower()
        matches = list(always)
        for n in lengths:
            matches += by_ending.get(p[-n:], ())
        if filter_ and filter_.search(p):
            for text, indices in contents:
                if text in p:
                    matches += indices
        return sorted(set(matches))

    return candidates_for


def _filter(expressions, items):
    """For each regex, the paths of elenco (in the same order) in which search() finds something."""
    candidates_for = _index(expressions)
    found = [[] for _ in expressions]
    for path in items:
        for i in candidates_for(path):
            if expressions[i].search(path):
                found[i].append(path)
    return found


def list_files(folder):
    """Relative paths with / as separator."""
    items = []
    for root, dirs, files in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d.lower() not in IGNORED_FOLDERS)
        rel = os.path.relpath(root, folder).replace(os.sep, "/")
        prefix = "" if rel == "." else rel + "/"
        for d in dirs:
            items.append(prefix + d + "/")
        for f in files:
            items.append(prefix + f)
            if len(items) >= MAX_FILE:
                return items, True
    return items, False


def _read_start(path, n=16):
    try:
        with open(path, "rb") as f:
            return f.read(n)
    except OSError:
        return b""


def _read_end(path, n=4):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size < n:
                return b""
            f.seek(size - n)
            return f.read(n)
    except OSError:
        return b""


def asar_entries(path):
    """List of the files contained in an Electron .asar archive (header only)."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
            size = struct.unpack("<I", head[12:16])[0]
            header = json.loads(f.read(size).decode("utf-8"))
    except (OSError, ValueError, struct.error, UnicodeDecodeError):
        return []
    entries = []

    def _visit(tree_node, prefix):
        for name, child in tree_node.get("files", {}).items():
            if "files" in child:
                _visit(child, prefix + name + "/")
            else:
                entries.append(prefix + name)

    _visit(header, "")
    return entries


def _web_framework(folder, items):
    """Recognizes the web frameworks in the folders that contain index.html (also inside .asar)."""
    found = {}
    roots = [p for p in items if p.lower().endswith("index.html") and "node_modules/" not in p]
    for asar in (p for p in items if p.lower().endswith(".asar")):
        for v in asar_entries(os.path.join(folder, asar)):
            if v.lower().endswith("index.html") and "node_modules/" not in v:
                roots.append(asar + "!/" + v)
    by_file = iter(_filter([file_re for _, file_re, _ in WEB_FRAMEWORKS if file_re], items))
    for name, file_re, content_signature in WEB_FRAMEWORKS:
        evidence = [p for p in next(by_file) if "node_modules/" not in p] if file_re else []
        if not evidence and content_signature:
            signature = re.compile(content_signature)
            for p in items:
                if not p.lower().endswith(".js") or "node_modules/" in p:
                    continue
                try:
                    if os.path.getsize(os.path.join(folder, p)) > 8_000_000:
                        continue
                    with open(os.path.join(folder, p), "rb") as f:
                        if signature.search(f.read()):
                            evidence.append(p)
                            break
                except OSError:
                    continue
        if evidence:
            found[name] = evidence[:3]
    return roots, found


def detect(folder):
    """Returns a dictionary with results, web frameworks, web roots and recommendations."""
    rules = load_rules()
    items, truncated = list_files(folder)
    results = {}  # (sezione, nome) -> {"score", "evidence"}

    def _add(section, name, evidence, weight, from_rule=False):
        r = results.setdefault((section, name), {"score": 0, "evidence": [], "from_rules": 0})
        if from_rule:
            weight = max(0, min(weight, MAX_RULE_POINTS - r["from_rules"]))
            r["from_rules"] += weight
        r["score"] += weight
        if len(r["evidence"]) < 4 and evidence not in r["evidence"]:
            r["evidence"].append(evidence)

    all_items = [(s, n, rx) for s in SECTIONS for n, rx in rules[s]]
    candidates_for = _index([rx for _, _, rx in all_items])
    for path in items:
        for i in candidates_for(path):  # same (path, rule) order as comparing with all of them
            section, name, rx = all_items[i]
            if rx.search(path):
                _add(section, name, path, WEIGHTS[section], from_rule=True)

    by_signature = _filter([re.compile(path_re, re.I) for path_re, _, _, _ in SIGNATURES], items)
    for (_, signature, engine, description), paths in zip(SIGNATURES, by_signature):
        for path in paths:
            if _read_start(os.path.join(folder, path)).startswith(signature):
                _add("Engine", engine, "%s: %s" % (description, path), 10)
                break

    # Godot with the data package embedded in the executable ("embed pck" export): "GDPC" at the end
    for path in items:
        if (path.lower().endswith(".exe") and path.count("/") <= 1
                and _read_end(os.path.join(folder, path)) == b"GDPC"):
            _add("Engine", "Godot", "%s (built-in data package)" % path, 10)
            break

    # Unity: Mono or IL2CPP
    if any(p.lower().endswith("gameassembly.dll") for p in items):
        _add("SDK", "UnityIL2CPP", "GameAssembly.dll", 5)
    if any(p.lower().endswith("managed/assembly-csharp.dll") for p in items):
        _add("SDK", "UnityMono", "Managed/Assembly-CSharp.dll", 5)

    # Electron even without LICENSE.electron.txt (often removed): the Chromium files next to resources/app
    lowercase = {p.lower() for p in items}
    if any(p.endswith("chrome_100_percent.pak") for p in lowercase) and \
            any(p.endswith("v8_context_snapshot.bin") or p.endswith("snapshot_blob.bin") for p in lowercase) and \
            any("resources/app" in p for p in lowercase):
        _add("Container", "Electron", "Chromium files and resources/app", 10)
    # encrypted archives: the texts cannot be read without removing a developers' protection (we do not do that)
    for p in items:
        if p.lower().endswith("resources/app/gemshell-assets"):
            _add("Container", "GemShell", "%s (encrypted)" % p, 10)

    web_roots, framework = _web_framework(folder, items)
    if web_roots:
        _add("Engine", "Web", "index.html: %s" % web_roots[0], 4 + len(framework))

    section_order = {s: i for i, s in enumerate(("Engine", "Container", "Emulator", "AntiCheat", "SDK", "Launcher",
                                                 "Evidence"))}
    ordered = sorted(
        ({"section": s, "name": n, "score": v["score"], "evidence": v["evidence"]}
         for (s, n), v in results.items()),
        key=lambda r: (section_order.get(r["section"], 9), -r["score"], r["name"]),
    )
    engines = [r for r in ordered if r["section"] in ("Engine", "Container", "Emulator")]
    best = max((r["score"] for r in engines), default=0)
    advice = []
    for r in engines:  # recommendations only for engines with at least half of the best score
        if r["score"] * 2 >= best and r["name"] in ADVICE and ADVICE[r["name"]] not in advice:
            advice.append(ADVICE[r["name"]])
    return {
        "main_engine": engines[0]["name"] if engines else None,
        "files_examined": len(items),
        "truncated": truncated,
        "results": ordered,
        "web_roots": web_roots,
        "framework_web": framework,
        "advice": advice,
    }


ENCRYPTED = ("GemShell",)  # containers that encrypt the game's files: PatchSmith does not open them


def encrypted_container(outcome):
    """The name of the container that encrypts the game's files (e.g. 'GemShell'), or None."""
    return next((r["name"] for r in outcome.get("results", ()) if r["section"] == "Container"
                 and r["name"] in ENCRYPTED), None)


def print_summary(outcome, log=print):
    log("Files examined: %d%s" % (outcome["files_examined"], " (truncated list)" if outcome["truncated"] else ""))
    if not outcome["results"]:
        log("No engine detected.")
    labels = {"Engine": "Engine", "Container": "Container", "SDK": "Component", "AntiCheat": "ANTI-CHEAT",
                 "Launcher": "Launcher", "Emulator": "Emulator", "Evidence": "Evidence"}
    if outcome["main_engine"]:
        log("Main engine: %s" % outcome["main_engine"])
    elif outcome["results"]:
        log("Engine not recognized (probably proprietary or not in the list).")
    has_engine = bool(outcome["main_engine"])
    components = 0
    for r in outcome["results"]:
        if r["section"] == "Evidence" and has_engine:
            continue  # the clues are only needed if the engine was not recognized
        if r["section"] in ("SDK", "Launcher"):
            components += 1
            if components > 8:
                continue
        log("  [%3d] %-12s %s" % (r["score"], labels.get(r["section"], r["section"]), r["name"]))
        for e in r["evidence"][:2]:
            log("          - %s" % e)
    if outcome["framework_web"]:
        log("Web framework: " + ", ".join(outcome["framework_web"]))
    if any(r["section"] == "AntiCheat" for r in outcome["results"]):
        log("WARNING: anti-cheat detected. Don't use injection tools on this game.")
    if outcome["advice"]:
        log("Recommended route:")
        for c in outcome["advice"]:
            log("  * " + c)
