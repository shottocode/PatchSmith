"""Test of the XNA/.NET adapter with a fake game built here (Windows' csc.exe + a fake XNB font).

Usage: python -I test/test_xna.py <empty_temp_folder>
The "game" and the probe are minimal .NET programs created by the test: no real game is started.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_xna, dotnet  # noqa: E402

OUTCOMES = []
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def csc(*arguments, cwd):
    outcome = subprocess.run([adapter_xna.compiler("v4.0.30319"), "/nologo"] + list(arguments), cwd=cwd,
                           capture_output=True, text=True, encoding="oem", errors="replace",
                           creationflags=NO_WINDOW)
    if outcome.returncode:
        raise SystemExit("csc: " + outcome.stdout + outcome.stderr)


def resources(entries):
    """.resources with only strings, in ResourceWriter's format (written with the minimal info)."""
    info = {"reader": dotnet.DEFAULT_READER, "item_set": dotnet.DEFAULT_RESOURCE_SET, "version": 2, "kinds": [],
            "entries": [(k, dotnet.KIND_STRING, v) for k, v in sorted(entries.items())]}
    return dotnet.write_resources(info)


def font_xnb(chars):
    """Uncompressed XNB v5 SpriteFont with the given characters (1x1 texture, fake glyphs)."""
    def s7(n):
        b = bytearray()
        while True:
            x, n = n & 0x7F, n >> 7
            b.append(x | (0x80 if n else 0))
            if not n:
                return bytes(b)

    def read_string(t):
        return s7(len(t.encode())) + t.encode()

    readers = ["Microsoft.Xna.Framework.Content.SpriteFontReader", "Microsoft.Xna.Framework.Content.Texture2DReader",
               "Microsoft.Xna.Framework.Content.ListReader`1[[Microsoft.Xna.Framework.Rectangle]]",
               "Microsoft.Xna.Framework.Content.ListReader`1[[System.Char]]",
               "Microsoft.Xna.Framework.Content.ListReader`1[[Microsoft.Xna.Framework.Vector3]]"]
    body = s7(len(readers)) + b"".join(read_string(l) + struct.pack("<i", 0) for l in readers) + s7(0)
    body += s7(1) + s7(2) + struct.pack("<iIII", 0, 1, 1, 1) + struct.pack("<I", 4) + b"\xff" * 4
    n = len(chars)
    for _ in range(2):
        body += s7(3) + struct.pack("<I", n) + struct.pack("<iiii", 0, 0, 1, 1) * n
    body += s7(4) + struct.pack("<I", n) + "".join(chars).encode("utf-8")
    body += struct.pack("<if", 12, 0.0) + s7(5) + struct.pack("<I", n) + struct.pack("<fff", 0, 1, 0) * n + b"\x00"
    return b"XNBw\x05\x00" + struct.pack("<I", 10 + len(body)) + body


TEXTS_EN = {"Menu_Start": "Start the adventure", "Menu_Quit": "Quit game", "Tip_1": "Press [Input:12] to jump\\nfast",
            "Count": "{0} heroes", "Only_Number": "100", "Lang_En": "English"}


def create_game(base, work):
    os.makedirs(os.path.join(base, "de"))
    os.makedirs(os.path.join(base, "Content", "Fonts"))
    neutral_resources = os.path.join(work, "neutral.resources")
    open(neutral_resources, "wb").write(resources(TEXTS_EN))
    open(os.path.join(work, "Game.cs"), "w").write(
        "[assembly: System.Reflection.AssemblyVersion(\"1.2.0.0\")]\n"
        "class Game { static void Main() {} }\n")
    csc("/target:exe", "/platform:x86", "/out:" + os.path.join(base, "Game.exe"),
        "/resource:%s,Game.Texts.resources" % neutral_resources, "Game.cs", cwd=work)
    german_res = os.path.join(work, "de.resources")
    open(german_res, "wb").write(resources({"Menu_Start": "Das Abenteuer beginnen"}))
    open(os.path.join(work, "Sat.cs"), "w").write(
        "[assembly: System.Reflection.AssemblyCulture(\"de\")]\n[assembly: System.Reflection.AssemblyVersion(\"1.2.0.0\")]\n")
    csc("/target:library", "/out:" + os.path.join(base, "de", "Game.resources.dll"),
        "/resource:%s,Game.Texts.de.resources" % german_res, "Sat.cs", cwd=work)
    ascii_ = [chr(c) for c in range(32, 127)]
    open(os.path.join(base, "Content", "Fonts", "Pixel.xnb"), "wb").write(font_xnb(ascii_))
    open(os.path.join(base, "Content", "Fonts", "Numbers.xnb"), "wb").write(font_xnb(list("0123456789")))
    # probe: uses the real .NET ResourceManager on the copy of the game
    open(os.path.join(work, "Probe.cs"), "w").write(
        "using System; using System.Globalization; using System.Reflection; using System.Resources;\n"
        "class Probe { static void Main(string[] a) {\n"
        "  Console.OutputEncoding = System.Text.Encoding.UTF8;\n"
        "  var rm = new ResourceManager(\"Game.Texts\", Assembly.LoadFrom(a[0]));\n"
        "  foreach (var c in new[] {\"en-US\", \"it-IT\", \"de-DE\", \"fr-FR\"})\n"
        "    Console.WriteLine(c + \"=\" + rm.GetString(\"Menu_Start\", new CultureInfo(c)));\n"
        "}}\n")
    os.makedirs(os.path.join(work, "probe"))
    csc("/target:exe", "/platform:x86", "/out:" + os.path.join(work, "probe", "Probe.exe"), "Probe.cs", cwd=work)


def probe(work, game):
    outcome = subprocess.run([os.path.join(work, "probe", "Probe.exe"), os.path.join(game, "Game.exe")],
                           capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW)
    return dict(r.split("=", 1) for r in outcome.stdout.splitlines() if "=" in r)


def fingerprints(folder):
    output = {}
    for root, _, files in os.walk(folder):
        for f in files:
            p = os.path.join(root, f)
            output[os.path.relpath(p, folder)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return output


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    adapter_xna.game_open = lambda c: False
    game, work = os.path.join(base, "game"), os.path.join(base, "work")
    os.makedirs(work)
    create_game(game, work)
    before = fingerprints(game)

    # satellite version: [SatelliteContractVersion("1.0")] means 1.0.0.0, as for csc
    open(os.path.join(work, "Contract.cs"), "w").write(
        "[assembly: System.Resources.SatelliteContractVersion(\"1.0\")]\nclass C {}\n")
    csc("/target:library", "/out:" + os.path.join(work, "Contract.dll"), "Contract.cs", cwd=work)
    check("SatelliteContractVersion \"1.0\" -> 1.0.0.0",
              dotnet.Assembly(os.path.join(work, "Contract.dll")).satellite_version() == "1.0.0.0")
    # names with the same hash: they are rewritten identical and all read back
    entries = {"K!!!": "one", "K!>^": "two", "K\"B!": "three", "Other": "four"}
    check("names with the same hash (valid test)", len({dotnet.hash_name(k) for k in list(entries)[:3]}) == 1)
    data = resources(entries)
    was_read = dotnet.read_resources(data)
    check("names with the same hash: identical rewrite and right texts",
              dotnet.write_resources(was_read) == data and dotnet.resources_strings(was_read) == entries)

    resource_sets = adapter_xna.find_resources(game)
    check("finds the localized resources (base Game.Texts, satellite de)",
              len(resource_sets) == 1 and resource_sets[0]["bases"] == {"Game.Texts": ["de"]}
              and resource_sets[0]["version"] == "1.2.0.0")
    _, texts = adapter_xna.texts_to_translate(game, silent)
    check("texts to translate (without the numbers)", "Start the adventure" in texts and "100" not in texts)
    allowed = adapter_xna.font_chars(game)
    check("font characters: ASCII only (the digits-only font does not count)",
              allowed == {chr(c) for c in range(32, 127)})
    check("accents -> apostrophe for Italian", adapter_xna.adapt_chars("Perché è così", allowed, "it")
              == "Perche' e' cosi'")
    check("accents removed for the other languages", adapter_xna.adapt_chars("Été", allowed, "fr") == "Ete")
    check("impossible character -> discarded", adapter_xna.adapt_chars("漢字", allowed, "it") is None)
    check("typographic punctuation -> ASCII", adapter_xna.adapt_chars("L’eroe…", allowed, "it") == "L'eroe...")
    check("tab -> space, invisible characters removed", adapter_xna.adapt_chars("a\tb​c", allowed, "it") == "a bc")
    fake = {"neutral_language": None, "bases": {"X": ["de", "fr"]}}
    check("cultures: en and it if the game does not declare the neutral language",
              adapter_xna.cultures_to_create(fake, "en", "it") == ["en", "it"])
    check("cultures: en-US if the game declares the neutral language 'en'",
              adapter_xna.cultures_to_create(dict(fake, neutral_language="en"), "en", "it") == ["en-US", "it"])
    check("cultures: no English slot if the neutral language is already 'en-US'; no 'it' if the game has it",
              adapter_xna.cultures_to_create(dict(fake, neutral_language="en-US", bases={"X": ["it"]}),
                                             "en", "it") == [])

    memory = {"Start the adventure": "Inizia l'avventura è qui", "Quit game": "Esci dal gioco",
               "Press [Input:12] to jump\\nfast": "Premi [Input:12] per saltare\\nveloce", "{0} heroes": "{0} eroi"}
    n = adapter_xna.install(game, "en", "it", "test", memory, silent)
    from patchsmith import __version__
    check("manifest with the PatchSmith version (for the check after playing)",
              adapter_xna.read_manifest(game).get("patchsmith_version") == __version__)
    check("en and it satellites created (4 translated texts + language entry)", n == 5
              and os.path.exists(os.path.join(game, "en", "Game.resources.dll"))
              and os.path.exists(os.path.join(game, "it", "Game.resources.dll")))
    sat = dotnet.Assembly(os.path.join(game, "en", "Game.resources.dll"))
    translated_count = dotnet.resources_strings(dotnet.read_resources(sat.read_resource("Game.Texts.en.resources")))
    check("language menu entry 'English' -> 'Italiano'; placeholders intact", translated_count["Lang_En"] == "Italiano"
              and translated_count["Tip_1"] == "Premi [Input:12] per saltare\\nveloce"
              and translated_count["Only_Number"] == "100")
    responses = probe(work, game)
    check("real ResourceManager: en-US and it-IT give Italian (accents adapted)",
              responses.get("en-US") == "Inizia l'avventura e' qui"
              and responses.get("it-IT") == "Inizia l'avventura e' qui")
    check("real ResourceManager: German stays German, French stays English",
              responses.get("de-DE") == "Das Abenteuer beginnen" and responses.get("fr-FR") == "Start the adventure")
    after = fingerprints(game)
    check("original game files not touched", all(after.get(k) == v for k, v in before.items()))

    lines = []
    adapter_xna.state(game, lines.append)
    check("status: satellites present and intact", sum(r.strip().startswith("OK") for r in lines) == 2)

    # reinstallation (reapply): same files, no error
    adapter_xna.install(game, "en", "it", "test", memory, silent)
    check("reapply: no extra files", set(fingerprints(game)) == set(after))

    adapter_xna.uninstall(game, silent)
    check("uninstall: folder identical to before", fingerprints(game) == before
              and not os.path.exists(os.path.join(game, "en")))
    check("uninstall: the game is back in English", probe(work, game).get("en-US") == "Start the adventure")

    # en satellite already present and not ours: it is not overwritten
    os.makedirs(os.path.join(game, "en"))
    open(os.path.join(game, "en", "Game.resources.dll"), "wb").write(b"someone else's")
    try:
        adapter_xna.install(game, "en", "it", "test", memory, silent)
        check("foreign satellite: not overwritten", False)
    except adapter_xna.XnaError:
        check("foreign satellite: not overwritten", open(os.path.join(game, "en", "Game.resources.dll"), "rb").read()
                  == b"someone else's" and not os.path.exists(os.path.join(game, adapter_xna.MANIFEST)))
    os.remove(os.path.join(game, "en", "Game.resources.dll"))
    os.rmdir(os.path.join(game, "en"))

    # tampered manifest: nothing deleted
    sat_de = os.path.join(game, "de", "Game.resources.dll")
    forged = {"patchsmith": True,
              "file": {"de/Game.resources.dll": hashlib.sha256(open(sat_de, "rb").read()).hexdigest()},
              "created_folders": ["de"]}
    open(os.path.join(game, adapter_xna.MANIFEST), "w").write(json.dumps(forged))
    adapter_xna.uninstall(game, silent)
    check("manifest pointing to the game's satellite with the right fingerprint: intact (it lacks the marker)",
              os.path.exists(sat_de) and fingerprints(game) == before)
    forged = {"patchsmith": True, "file": {"../Game.exe": "0" * 64}}
    open(os.path.join(game, adapter_xna.MANIFEST), "w").write(json.dumps(forged))
    try:
        adapter_xna.uninstall(game, silent)
        check("manifest with an out-of-place path: rejected", False)
    except adapter_xna.XnaError:
        check("manifest with an out-of-place path: rejected", os.path.exists(os.path.join(game, "Game.exe")))
    os.remove(os.path.join(game, adapter_xna.MANIFEST))

    # installation interrupted before moving the files into place: the restore removes everything
    real_replace = os.replace

    def replace_interrupted(a, b):
        if b.endswith(".resources.dll"):
            raise KeyboardInterrupt
        return real_replace(a, b)

    adapter_xna.os.replace = replace_interrupted
    try:
        adapter_xna.install(game, "en", "it", "test", memory, silent)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_xna.os.replace = real_replace
    adapter_xna.uninstall(game, silent)
    check("interrupted installation: the restore removes everything", fingerprints(game) == before)

    # --- scenarios that came up in the review ---------------------------------------------------
    # manifest lost but our satellites present: they do not count as game languages, status and restore see them
    adapter_xna.install(game, "en", "it", "test", memory, silent)
    os.remove(os.path.join(game, adapter_xna.MANIFEST))
    check("without manifest: our satellites do not count as game languages",
              adapter_xna.find_resources(game)[0]["bases"] == {"Game.Texts": ["de"]})
    lines = []
    adapter_xna.state(game, lines.append)
    check("without manifest: the status reports them", any("without a manifest" in r for r in lines))
    check("without manifest: the restore removes them",
              adapter_xna.uninstall(game, silent) and fingerprints(game) == before)

    # replacement interrupted, then language change, then restore: nothing is left behind
    adapter_xna.os.replace = replace_interrupted
    try:
        adapter_xna.install(game, "en", "it", "test", memory, silent)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_xna.os.replace = real_replace
    adapter_xna.install(game, "en", "es", "test", {"Start the adventure": "Empezar"}, silent)
    check("language change after an interruption: only the es satellite is there (besides en)",
              os.path.exists(os.path.join(game, "es", "Game.resources.dll"))
              and not os.path.exists(os.path.join(game, "it")))
    adapter_xna.uninstall(game, silent)
    check("... and the restore brings everything back as it was", fingerprints(game) == before)

    # manifest that lists the official satellite with its fingerprint: a new installation does not touch it
    forged = {"patchsmith": True,
              "file": {"de/Game.resources.dll": hashlib.sha256(open(sat_de, "rb").read()).hexdigest()},
              "created_folders": ["de"]}
    open(os.path.join(game, adapter_xna.MANIFEST), "w").write(json.dumps(forged))
    adapter_xna.install(game, "en", "it", "test", memory, silent)
    check("install with a tampered manifest: the official de satellite stays", os.path.exists(sat_de)
              and fingerprints(game)["de" + os.sep + "Game.resources.dll"]
              == before["de" + os.sep + "Game.resources.dll"])
    adapter_xna.uninstall(game, silent)

    # invalid hand-typed language: error before writing anything
    try:
        adapter_xna.install(game, "English", "Italian", "test", memory, silent)
        check("invalid language: rejected", False)
    except adapter_xna.XnaError:
        check("invalid language: rejected without writing anything", fingerprints(game) == before)

    # write error halfway: no empty folder is left behind
    real_open = open

    def broken_open(path, mode="r", *a, **k):
        if str(path).endswith(adapter_xna.TMP_SUFFIX) and "w" in mode:
            raise OSError("disk full (simulated)")
        return real_open(path, mode, *a, **k)

    adapter_xna.open = broken_open
    try:
        adapter_xna.install(game, "en", "it", "test", memory, silent)
    except OSError:
        pass
    finally:
        del adapter_xna.open
    adapter_xna.uninstall(game, silent)
    check("write error: no empty folder left behind", fingerprints(game) == before
              and not os.path.exists(os.path.join(game, "en")) and not os.path.exists(os.path.join(game, "it")))

    # fonts: folder with [..] in its name, compressed XNB, unsorted list, uppercase-only font
    fonts = os.path.join(base, "font [v1.0]")
    os.makedirs(os.path.join(fonts, "Content", "Fonts"))
    italian = sorted(set(chr(c) for c in range(32, 127)) | set("àèéìòù"))
    open(os.path.join(fonts, "Content", "Fonts", "A.xnb"), "wb").write(font_xnb(italian))
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(italian))
    check("font: folder with [..] in its name read, accents allowed", "è" in (adapter_xna.font_chars(fonts) or ()))
    compressed = bytearray(font_xnb(italian))
    compressed[5] = 0x80
    open(os.path.join(fonts, "Content", "Fonts", "C.xnb"), "wb").write(bytes(compressed))
    check("font: a compressed XNB (unreadable) -> ASCII only", adapter_xna.font_chars(fonts) == adapter_xna.ASCII)
    os.remove(os.path.join(fonts, "Content", "Fonts", "C.xnb"))
    unordered = italian[:-6] + list("ùòìéèà")
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(unordered))
    found = adapter_xna.font_chars(fonts)
    check("font: unsorted list -> only the characters XNA really finds",
              "a" in found and not set("àèéìòù") <= found)
    uppercase = [chr(c) for c in range(32, 97)] + list("{|}~")
    open(os.path.join(fonts, "Content", "Fonts", "B.xnb"), "wb").write(font_xnb(uppercase))
    found = adapter_xna.font_chars(fonts)
    check("uppercase-only font: covers lowercase too, but not accents",
              "a" in found and "A" in found and "è" not in found)

    # game update: the status reports it
    adapter_xna.install(game, "en", "it", "test", memory, silent)
    open(os.path.join(game, "Game.exe"), "ab").write(b"\x00")
    lines = []
    adapter_xna.state(game, lines.append)
    check("game updated: reported", any("has been updated" in r for r in lines))
    adapter_xna.uninstall(game, silent)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
