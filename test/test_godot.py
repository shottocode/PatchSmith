"""Godot 4 games with Godot's translation tables (adapter_godot).

Usage: python -I test/test_godot.py <test_folder_that_does_not_exist>
- reading: PCK embedded in the executable (format 2, relative offsets), binary RSRC resources, OptimizedTranslation
  (whole texts and texts compressed with smaz), Translation with the message dictionary;
- flow: Analyze and translate with the fake translator -> override.cfg with the autoload, script and translations next
  to the executable; Reapply; Restore originals with the folder identical (also with a game's own override.cfg);
- Italian table already present: official language, no file written.
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_godot, auto as automatic, project  # noqa: E402

OUTCOMES = []


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def _s(text):
    b = text.encode("utf-8") + b"\0"
    return struct.pack("<I", len(b)) + b


def _variant(v):
    if isinstance(v, str):
        return struct.pack("<I", 5) + _s(v)
    if isinstance(v, dict):
        return struct.pack("<II", 26, len(v)) + b"".join(_variant(k) + _variant(x) for k, x in v.items())
    if isinstance(v, bytes):
        return struct.pack("<II", 31, len(v)) + v + b"\0" * ((4 - len(v) % 4) % 4)
    if isinstance(v, list):
        return struct.pack("<II", 32, len(v)) + struct.pack("<%di" % len(v), *v)
    raise TypeError(v)


def resource(kind, properties):
    """Binary .translation file as Godot 4 writes it (format 6, one internal resource)."""
    names = list(properties)
    head = b"RSRC" + struct.pack("<5I", 0, 0, 4, 4, 6) + _s(kind) + struct.pack("<QIQ", 0, 2, 12345) + b"\0" * 44
    head += struct.pack("<I", len(names)) + b"".join(_s(n) for n in names) + struct.pack("<I", 0)
    internal = _s("local://%s_1" % kind)
    body = _s(kind) + struct.pack("<I", len(names)) + b"".join(
        struct.pack("<I", i) + _variant(properties[n]) for i, n in enumerate(names))
    position = len(head) + 4 + len(internal) + 8
    return head + struct.pack("<I", 1) + internal + struct.pack("<Q", position) + body


def optimized(texts, local=None):
    """Minimal OptimizedTranslation: one bucket with all the texts; the first compressed with smaz ("the" = code 1)."""
    strings, elements = b"", []
    for i, t in enumerate(texts):
        whole = t.encode("utf-8") + b"\0"
        data = (b"\x01" + b"\xff" + bytes([len(t[3:].encode()) - 1]) + t[3:].encode()) \
            if i == 0 and t.startswith("the") else whole
        elements += [i + 1000, len(strings), len(data), len(whole)]
        strings += data
    properties = {"hash_table": [0], "bucket_table": [len(texts), 0] + elements, "strings": strings}
    if local:
        properties["locale"] = local
    return resource("OptimizedTranslation", properties)


def executable_with_pck(file):
    """A fake .exe with the PCK embedded at the end (like Godot's export with "embed pck")."""
    entries, file_data = b"", b""
    for name, data in file.items():
        n = name.encode() + b"\0" * (4 - len(name) % 4)
        entries += struct.pack("<I", len(n)) + n + struct.pack("<QQ", len(file_data), len(data)) + b"\0" * 16 + \
            struct.pack("<I", 0)
        file_data += data
    header_length = 4 + 16 + 12 + 64 + 4
    base = header_length + len(entries)
    pck = b"GDPC" + struct.pack("<4I", 2, 4, 4, 1) + struct.pack("<IQ", 2, base) + b"\0" * 64 + \
        struct.pack("<I", len(file)) + entries + file_data
    return b"MZ" + b"\0" * 998 + pck + struct.pack("<Q", len(pck)) + b"GDPC"


def fingerprints(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for n in files:
            with open(os.path.join(root, n), "rb") as f:
                output[os.path.relpath(os.path.join(root, n), folder)] = hashlib.sha256(f.read()).hexdigest()
    return output


def game(folder, with_italian=False, override=None):
    file = {"project.binary": b"ECFG",
            "assets/translations/translations.en.translation": optimized(
                ["the drill is ready", "Upgrade", "Unlock [color=40ff40]overdrive[/color] mode"]),
            "assets/translations/translations.de.translation": resource("Translation", {
                "locale": "de", "messages": {"UPGRADE": "Verbessern", "READY": "Der Bohrer ist bereit"}})}
    if with_italian:
        file["assets/translations/translations.it.translation"] = optimized(["Pronto"], local="it")
    os.makedirs(folder)
    with open(os.path.join(folder, "drill.exe"), "wb") as f:
        f.write(executable_with_pck(file))
    if override is not None:
        with open(os.path.join(folder, "override.cfg"), "w", encoding="utf-8") as f:
            f.write(override)


def main(base):
    base = os.path.abspath(base)
    os.makedirs(base)
    project.DATA_ROOT = os.path.join(base, "data_folder")
    automatic.PROFILES_FOLDER = os.path.join(project.DATA_ROOT, "profiles")

    # reading
    folder = os.path.join(base, "Drill")
    game(folder)
    pck = adapter_godot.Pck(os.path.join(folder, "drill.exe"))
    tables = adapter_godot.tables(pck)
    en = adapter_godot.table_texts(pck.read_file(tables["en"]))
    de = adapter_godot.table_texts(pck.read_file(tables["de"]))
    check("PCK incorporato, tabelle per lingua; OptimizedTranslation (anche smaz) e Translation lette (%s, %s)"
              % (ascii(en), ascii(de)), pck.version() == "4.4.1" and sorted(tables) == ["de", "en"]
              and en == ("en", ["the drill is ready", "Upgrade", "Unlock [color=40ff40]overdrive[/color] mode"])
              and de[0] == "de" and sorted(de[1]) == ["Der Bohrer ist bereit", "Verbessern"])

    # full flow with the fake translator
    before = fingerprints(folder)
    lines = []
    path, completed, outcome = automatic.analyze_and_translate(folder, "auto", "it", "fake", lines.append)
    profile = json.load(open(path, encoding="utf-8")) if path else {}
    with open(os.path.join(folder, "override.cfg"), encoding="utf-8") as f:
        override = f.read()
    with open(os.path.join(folder, adapter_godot.DICTIONARY), encoding="utf-8") as f:
        dictionary = json.load(f)
    check("Analizza e traduci: profilo godot, override.cfg con l'autoload, script e traduzioni accanto all'eseguibile",
              completed and profile.get("engine") == "godot" and outcome["outcome"].code == "OK"
              and '[autoload]' in override and 'PatchsmithTraduzione="*res://patchsmith-traduzione.gd"' in override
              and os.path.exists(os.path.join(folder, adapter_godot.SCRIPT))
              and dictionary["language"] == "en" and dictionary["additions"] == ["it"]
              and dictionary["texts"].get("Upgrade", "").startswith("[it]")
              and "[color=40ff40]" in dictionary["texts"].get("Unlock [color=40ff40]overdrive[/color] mode", ""))
    check("il pacchetto del gioco non e' cambiato", fingerprints(folder)["drill.exe"] == before["drill.exe"])
    automatic.reapply(folder, lines.append, test=True, outcome={})
    automatic.restore(folder, lines.append, outcome={})
    check("Riapplica e poi Ripristina originali: cartella identica a prima", fingerprints(folder) == before)

    # the game's own override.cfg: kept and put back
    another = os.path.join(base, "DrillConOverride")
    game(another, override="[display]\nwindow/size/mode=3\n")
    before = fingerprints(another)
    automatic.analyze_and_translate(another, "auto", "it", "fake", lines.append)
    with open(os.path.join(another, "override.cfg"), encoding="utf-8") as f:
        merged = f.read()
    automatic.restore(another, lines.append, outcome={})
    check("override.cfg del gioco: le sue impostazioni restano, poi rimesso identico",
              "window/size/mode=3" in merged and "PatchsmithTraduzione" in merged and fingerprints(another) == before)

    # Italian already in the game
    ita = os.path.join(base, "DrillItaliano")
    game(ita, with_italian=True)
    before = fingerprints(ita)
    path, completed, outcome = automatic.analyze_and_translate(ita, "auto", "it", "fake", lines.append)
    check("tabella in italiano gia' nel gioco: lingua ufficiale, nessun file scritto",
              not completed and outcome["outcome"].code == "OFFICIAL" and fingerprints(ita) == before)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRISULTATO: %d/%d controlli superati" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return 1 if failed else 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    sys.exit(main(sys.argv[1]))
