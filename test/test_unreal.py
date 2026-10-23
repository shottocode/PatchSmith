"""Test of the Unreal adapter with a fake game (pak v9 built here, independently of the adapter).

Usage: python -I test/test_unreal.py <empty_temp_folder>
"""
import hashlib
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_unreal  # noqa: E402

OUTCOMES = []
MAGIC_LOCRES = bytes.fromhex("0e147475674a03fc4a15909dc3377f1b")


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def silent(_):
    pass


def fs(s):  # FString, written here independently
    if not s:
        return struct.pack("<i", 0)
    try:
        b = s.encode("ascii")
        return struct.pack("<i", len(b) + 1) + b + b"\0"
    except UnicodeEncodeError:
        b = s.encode("utf-16-le")
        return struct.pack("<i", -(len(b) // 2 + 1)) + b + b"\0\0"


def locres_v2(entries):
    """entries: [(namespace, key, text)] -> .locres version 2 with deduplicated strings."""
    strings, idx = [], {}
    by_ns = {}
    for ns, k, t in entries:
        if t not in idx:
            idx[t] = len(strings)
            strings.append([t, 0])
        strings[idx[t]][1] += 1
        by_ns.setdefault(ns, []).append((k, t))
    body = struct.pack("<I", len(entries)) + struct.pack("<I", len(by_ns))
    for ns, keys in by_ns.items():
        body += struct.pack("<I", 0x1234) + fs(ns) + struct.pack("<I", len(keys))
        for k, t in keys:
            body += struct.pack("<I", 0x5678) + fs(k) + struct.pack("<Ii", 0x9ABC, idx[t])
    offset = 16 + 1 + 8 + len(body)
    head = MAGIC_LOCRES + b"\x02" + struct.pack("<q", offset) + body
    tail = struct.pack("<i", len(strings)) + b"".join(fs(t) + struct.pack("<i", rc) for t, rc in strings)
    return head + tail


def locmeta(culture):
    return bytes.fromhex("4fee4ca1684855836c4c46bd70da507c") + b"\x00" + fs(culture) + fs(culture + "/Game.locres")


def pak_v9(file):
    """Uncompressed pak v9, built independently of the adapter."""
    body, entries = b"", []
    for name, data in file:
        h = hashlib.sha1(data).digest()
        entry = lambda off, d=data, h=h: (struct.pack("<qqqI", off, len(d), len(d), 0) + h + b"\x00"
                                          + struct.pack("<I", 0))
        entries.append((name, len(body), entry))
        body += entry(0) + data
    idx = fs("../../../") + struct.pack("<i", len(entries)) + b"".join(fs(n) + v(o) for n, o, v in entries)
    tail = (b"\x00" * 16 + b"\x00" + struct.pack("<IIqq", 0x5A6F12E1, 9, len(body), len(idx))
            + hashlib.sha1(idx).digest() + b"\x00" + b"\x00" * 160)
    return body + idx + tail


def create_game(base):
    paks = os.path.join(base, "Proj", "Content", "Paks")
    os.makedirs(paks)
    os.makedirs(os.path.join(base, "Engine", "Binaries"))
    entries_en = [("UI", "Start", "Start Game"), ("UI", "Quit", "Quit"),
                  ("ST_Help", "Tip1", "Press {key} to <Bold>jump</>"),
                  ("ST_Help", "Tip2", "Can be interrupted;-;Knocks back enemies"), ("", "Num", "100"),
                  ("UI", "Start2", "Start Game")]
    file = [("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
            ("Proj/Content/Localization/Game/en/Game.locres", locres_v2(entries_en)),
            ("Proj/Content/Localization/Game/de/Game.locres", locres_v2([("UI", "Start", "Spiel starten")])),
            ("Proj/Content/Maps/Level.umap", b"map" * 100)]
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(pak_v9(file))
    return paks


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    adapter_unreal.game_open = lambda c: False
    game = os.path.join(base, "game")
    paks = create_game(game)
    before = sorted(os.listdir(paks)) + sorted(os.listdir(game))

    info = adapter_unreal.analyze(game)
    check("analysis: one v9 pak, no problems", info["versions"] == [9] and not info["problems"])
    sources = adapter_unreal.find_locres(game)
    check("finds only the .locres of the original language (en)", [s[1] for s in sources]
              == ["Proj/Content/Localization/Game/en/Game.locres"])
    _, texts, culture = adapter_unreal.texts_to_translate(game, silent)
    check("texts to translate, language (.locmeta)", culture == "en" and "Start Game" in texts and "100" not in texts)

    original = sources[0][0].read_file(sources[0][1])
    locres_info = adapter_unreal.read_locres(original)
    check("identical .locres round-trip",
          adapter_unreal.write_locres(locres_info, [s for s, _ in locres_info["strings"]]) == original)

    memory = {"Start Game": "Inizia partita", "Quit": "Esci",
              "Press {key} to <Bold>jump</>": "Premi {key} per <Bold>saltare</>",
              "Can be interrupted;-;Knocks back enemies": "Può essere interrotto;-;Respinge i nemici"}
    n = adapter_unreal.install(game, "it", "test", memory, silent)
    new_pak = os.path.join(paks, adapter_unreal.pak_name("it"))
    check("patch pak written (4 phrases translated)", n == 4 and os.path.exists(new_pak))
    was_read = adapter_unreal.Pak(new_pak)
    translated = adapter_unreal.read_locres(was_read.read_file("Proj/Content/Localization/Game/en/Game.locres"))
    translated_texts = [s for s, _ in translated["strings"]]
    check("translated locres: identical keys and header", translated["keys"] == locres_info["keys"]
              and translated["head"] == locres_info["head"])
    check("translated locres: texts, placeholders and accents", "Inizia partita" in translated_texts
              and "100" in translated_texts and "Premi {key} per <Bold>saltare</>" in translated_texts
              and "Può essere interrotto;-;Respinge i nemici" in translated_texts)
    check("original pak not touched", sorted(os.listdir(paks)) == sorted(before[:1] + [adapter_unreal.pak_name("it")]))

    lines = []
    adapter_unreal.state(game, lines.append)
    check("state: pak present and intact", any(r.strip().startswith("OK") for r in lines))
    check("new typographic punctuation -> ASCII, truncating characters -> discarded",
              adapter_unreal._adapt("L’arma…", set()) == "L'arma..."
              and adapter_unreal._adapt("L’arma", {"’"}) == "L’arma"
              and adapter_unreal._adapt("a\x00b", set()) is None and adapter_unreal._adapt("a￿b", set()) is None)

    # language change: the old pak is replaced
    adapter_unreal.install(game, "es", "test", {"Start Game": "Iniciar"}, silent)
    check("language change: pak it removed, pak es written",
          os.path.exists(os.path.join(paks, adapter_unreal.pak_name("es"))) and not os.path.exists(new_pak))

    # uninstall: everything as before
    adapter_unreal.uninstall(game, silent)
    check("uninstall: folders identical to before", sorted(os.listdir(paks)) + sorted(os.listdir(game)) == before)

    # pak with the same name not created by us: not overwritten
    open(new_pak, "wb").write(b"someone else's")
    try:
        adapter_unreal.install(game, "it", "test", memory, silent)
        check("someone else's pak: not overwritten", False)
    except adapter_unreal.UnrealError:
        check("someone else's pak: not overwritten", open(new_pak, "rb").read() == b"someone else's")
    os.remove(new_pak)

    # pak changed by others after the install: left in place
    adapter_unreal.install(game, "it", "test", memory, silent)
    from patchsmith import __version__
    check("manifest with the PatchSmith version (for the check after playing)",
              adapter_unreal.read_manifest(game).get("patchsmith_version") == __version__)
    open(new_pak, "ab").write(b"x")
    adapter_unreal.uninstall(game, silent)
    check("pak changed by others: left in place", os.path.exists(new_pak))
    os.remove(new_pak)

    # tampered manifest: nothing deleted
    forged = {"patchsmith": True, "file": {"Proj/Content/Paks/Proj-WindowsNoEditor.pak": "0" * 64}}
    open(os.path.join(game, adapter_unreal.MANIFEST), "w").write(json.dumps(forged))
    try:
        adapter_unreal.uninstall(game, silent)
        check("tampered manifest: refused", False)
    except adapter_unreal.UnrealError:
        check("tampered manifest: original pak intact",
              os.path.exists(os.path.join(paks, "Proj-WindowsNoEditor.pak")))
    os.remove(os.path.join(game, adapter_unreal.MANIFEST))

    # install interrupted after the manifest and before putting the pak in place: everything is restored
    adapter_unreal.install(game, "it", "test", memory, silent)
    real_replace = os.replace

    def replace_interrupted(a, b):
        if b.endswith(".pak"):
            raise KeyboardInterrupt
        return real_replace(a, b)

    adapter_unreal.os.replace = replace_interrupted
    try:
        adapter_unreal.install(game, "it", "test", dict(memory, Quit="Abbandona"), silent)
    except KeyboardInterrupt:
        pass
    finally:
        adapter_unreal.os.replace = real_replace
    adapter_unreal.uninstall(game, silent)
    check("installation interrupted: the restore removes everything",
          sorted(os.listdir(paks)) + sorted(os.listdir(game)) == before)

    # signed paks (.sig): refused
    sig = os.path.join(paks, "Proj-WindowsNoEditor.sig")
    open(sig, "wb").write(b"signature")
    try:
        adapter_unreal.install(game, "it", "test", memory, silent)
        check("signed paks: refused", False)
    except adapter_unreal.UnrealError:
        check("signed paks: refused", not os.path.exists(new_pak))
    os.remove(sig)

    # game update: the status reports it
    adapter_unreal.install(game, "it", "test", memory, silent)
    open(os.path.join(paks, "Proj-WindowsNoEditor.pak"), "wb").write(pak_v9([
        ("Proj/Content/Localization/Game/Game.locmeta", locmeta("en")),
        ("Proj/Content/Localization/Game/en/Game.locres",
         locres_v2([("UI", "Start", "Start Game"), ("UI", "New", "New Quest")]))]))
    lines = []
    adapter_unreal.state(game, lines.append)
    check("game updated: reported", any("has been updated" in r for r in lines))
    adapter_unreal.uninstall(game, silent)

    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
