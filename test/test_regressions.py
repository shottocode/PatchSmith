"""Safety net against problems already solved on real games coming back, without games and without network.

Usage: python -I test/test_regressions.py <empty_temp_folder>
Covers:
- Unity 6 (One Million Coins): BepInEx starts at the first MonoBehaviour (bepinex_entrypoint), the other versions are
  not touched, an existing configuration is kept;
- Unity games with the interface in IMGUI (uses_imgui) and keys written in XUnity's configuration (_config_values);
- Unity Localization: catalog that checks the hash (CRC) of the bundles (checked_crc);
- the local translator's number check: '2.5' does not make the check crash (numbers in words only for integers);
- web games: key names (<kbd>, class="key") are left untranslated, words like Delete are translated elsewhere
  (with node.js, if present, also the real injector; without it, only the check of the code);
- the real logs in test/data/signals: present, with their signal, anonymous.
"""
import base64
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from patchsmith import adapter_unity, adapter_unity_loc, adapter_webui, backend, project  # noqa: E402

OUTCOMES = []
SIGNALS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "signals")


def check(name, condition):
    OUTCOMES.append((name, bool(condition)))
    print(("OK    " if condition else "ERROR ") + "  " + name)


def read_file(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# --- Unity 6: BepInEx entry point -------------------------------------------------------------------

# as BepInEx 5 writes it at first start (reduced): the entry point section is in the middle of the others
CFG_BEPINEX = """[Caching]

## Enable/disable assembly metadata cache
# Setting type: Boolean
# Default value: true
EnableAssemblyCache = true

[Preloader.Entrypoint]

## The local filename of the assembly to target.
# Setting type: String
# Default value: UnityEngine.dll
Assembly = UnityEngine.CoreModule.dll

## The name of the type in the entrypoint assembly to search for the entrypoint method.
# Setting type: String
# Default value: Application
Type = Application

## The name of the method in the specified entrypoint assembly and type to hook and load Chainloader from.
# Setting type: String
# Default value: .cctor
Method = .cctor

[Logging.Disk]

# Setting type: Boolean
# Default value: true
Enabled = true

[Other]
Type = DoNotTouch
"""


def _section(text, name):
    lines, inside = [], False
    for line in text.splitlines():
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if m:
            inside = m.group(1).strip() == name
        elif inside:
            lines.append(line.strip())
    return lines


def test_entrypoint(base):
    cfg = lambda g: os.path.join(g, *adapter_unity.CONFIG_BEPINEX.split("/"))  # noqa: E731

    new = os.path.join(base, "unity6-new")
    os.makedirs(new)
    check("Unity 6 without configuration: set", adapter_unity.bepinex_entrypoint(new, "6000.6.0f1"))
    entries = _section(read_file(cfg(new)), "Preloader.Entrypoint")
    check("Unity 6 without configuration: Type = MonoBehaviour with Assembly and Method",
              "Type = MonoBehaviour" in entries and "Assembly = UnityEngine.CoreModule.dll" in entries
              and "Method = .cctor" in entries)

    existing = os.path.join(base, "unity6-existing")
    write(cfg(existing), CFG_BEPINEX)
    check("Unity 6 with BepInEx configuration: set",
              adapter_unity.bepinex_entrypoint(existing, "6000.3.12f1"))
    text = read_file(cfg(existing))
    entries = _section(text, "Preloader.Entrypoint")
    check("existing configuration: a single Type, = MonoBehaviour",
              [v for v in entries if v.startswith("Type")] == ["Type = MonoBehaviour"])
    check("existing configuration: the other lines stay (comments, Assembly, Method, other sections)",
              "# Default value: Application" in entries and "Assembly = UnityEngine.CoreModule.dll" in entries
              and "Method = .cctor" in entries and "EnableAssemblyCache = true" in _section(text, "Caching")
              and "Enabled = true" in _section(text, "Logging.Disk")
              and _section(text, "Other") == ["Type = DoNotTouch"])
    adapter_unity.bepinex_entrypoint(existing, "6000.3.12f1")
    check("existing configuration: repeating changes nothing", read_file(cfg(existing)) == text)

    without_section = os.path.join(base, "unity6-without-section")
    write(cfg(without_section), "[Caching]\nEnableAssemblyCache = true\n")
    adapter_unity.bepinex_entrypoint(without_section, "6000.0.23f1")
    text = read_file(cfg(without_section))
    check("configuration without the section: added at the end, the rest stays",
              "Type = MonoBehaviour" in _section(text, "Preloader.Entrypoint")
              and _section(text, "Caching") == ["EnableAssemblyCache = true", ""])

    old = os.path.join(base, "unity2022")
    write(cfg(old), CFG_BEPINEX)
    check("Unity 2022: not touched", not adapter_unity.bepinex_entrypoint(old, "2022.3.10f1")
              and read_file(cfg(old)) == CFG_BEPINEX)
    empty_game = os.path.join(base, "unity2022-empty")
    os.makedirs(empty_game)
    check("Unity 2022 without configuration: no file created",
              not adapter_unity.bepinex_entrypoint(empty_game, "2022.3.10f1") and not os.path.exists(cfg(empty_game)))
    check("unknown version: not touched", not adapter_unity.bepinex_entrypoint(empty_game, None)
              and not adapter_unity.bepinex_entrypoint(empty_game, "x.y") and not os.path.exists(cfg(empty_game)))


# --- Unity: IMGUI and XUnity configuration ---------------------------------------------------------

def unity_game(folder, code):
    """Fake Unity Mono game: Game.exe, Game_Data/Managed/Assembly-CSharp.dll with these bytes (None = without)."""
    managed = os.path.join(folder, "Game_Data", "Managed")
    os.makedirs(managed)
    with open(os.path.join(folder, "Game.exe"), "wb") as f:
        f.write(b"MZ")
    if code is not None:
        with open(os.path.join(managed, "Assembly-CSharp.dll"), "wb") as f:
            f.write(b"MZ\x00" + code + b"\x00end")
    return folder


def test_imgui(base):
    imgui = unity_game(os.path.join(base, "imgui"), b"OnGUI\x00GUIStyle\x00GUILayout")
    check("IMGUI: OnGUI + GUIStyle -> yes", adapter_unity.uses_imgui(imgui))
    check("IMGUI: OnGUI + GUIContent -> yes",
              adapter_unity.uses_imgui(unity_game(os.path.join(base, "imgui-content"), b"OnGUI\x00GUIContent")))
    check("IMGUI: OnGUI alone (just a method with that name) -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "ongui-only"), b"OnGUI\x00Update")))
    check("IMGUI: UGUI/TextMeshPro interface -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "ugui"), b"TextMeshProUGUI\x00GUIStyle")))
    check("IMGUI: without Assembly-CSharp.dll -> no",
              not adapter_unity.uses_imgui(unity_game(os.path.join(base, "without-code"), None)))
    check("IMGUI: folder that is not a Unity game -> no", not adapter_unity.uses_imgui(base))
    # the interface in a module of the game (asmdef, like SunHaven.Core.dll or Mouse.dll), not in Assembly-CSharp
    module = unity_game(os.path.join(base, "imgui-module"), b"Update\x00TextMeshProUGUI")
    with open(os.path.join(module, "Game_Data", "Managed", "Game.Interface.dll"), "wb") as f:
        f.write(b"MZ\x00OnGUI\x00GUILayout\x00end")
    check("IMGUI: OnGUI + GUILayout in a module of the game (not Assembly-CSharp) -> yes",
              adapter_unity.uses_imgui(module))
    third_party = unity_game(os.path.join(base, "imgui-third-party"), b"Update\x00TextMeshProUGUI")
    for name in ("Sirenix.Utilities.dll", "UnityEngine.IMGUIModule.dll", "QFSW.QC.dll", "UnityGLTFScripts.dll"):
        with open(os.path.join(third_party, "Game_Data", "Managed", name), "wb") as f:
            f.write(b"MZ\x00OnGUI\x00GUIStyle\x00end")
    check("IMGUI: OnGUI only in the engine or in third-party libraries (Sirenix, console, GLTF) -> no",
              not adapter_unity.uses_imgui(third_party))
    managed_folder = os.path.join(third_party, "Game_Data", "Managed")
    for name in ("Assembly-CSharp-firstpass.dll", "SunHaven.Core.dll", "Mouse.dll", "Newtonsoft.Json.dll",
                 "System.Core.dll", "mscorlib.dll", "I2Localization.dll", "Rewired_Core.dll", "Game.Editor.dll"):
        with open(os.path.join(managed_folder, name), "wb") as f:
            f.write(b"MZ")
    check("game DLLs: Assembly-CSharp first, then the game's modules; engine, .NET, third-party and editor ones: no",
              [os.path.basename(p) for p in adapter_unity.game_assembly(os.path.join(third_party, "Game_Data"))]
              == ["Assembly-CSharp.dll", "Assembly-CSharp-firstpass.dll", "Mouse.dll", "SunHaven.Core.dll"])
    # old Unity games written in UnityScript: their code is Assembly-UnityScript (almost always with OnGUI);
    # UnityScript.Lang.dll and Boo.Lang.dll are the language's libraries
    old = unity_game(os.path.join(base, "imgui-unityscript"), None)
    managed_folder = os.path.join(old, "Game_Data", "Managed")
    for name, byte in (("Assembly-UnityScript.dll", b"MZ\x00OnGUI\x00GUIStyle\x00end"),
                       ("Assembly-UnityScript-firstpass.dll", b"MZ"), ("UnityScript.Lang.dll", b"MZ"),
                       ("Boo.Lang.dll", b"MZ\x00OnGUI\x00GUIStyle\x00end")):
        with open(os.path.join(managed_folder, name), "wb") as f:
            f.write(byte)
    check("game DLLs: Assembly-UnityScript (old games) yes, the UnityScript and Boo libraries no; IMGUI "
              "in Assembly-UnityScript -> yes",
              [os.path.basename(p) for p in adapter_unity.game_assembly(os.path.join(old, "Game_Data"))]
              == ["Assembly-UnityScript.dll", "Assembly-UnityScript-firstpass.dll"]
              and adapter_unity.uses_imgui(old))
    text = read_file(adapter_unity.configure(imgui, "en", "it"))
    check("configure: IMGUI turned on automatically for an IMGUI game",
              "[TextFrameworks]" in text and "EnableIMGUI=True" in text)
    ugui = os.path.join(base, "ugui")
    check("configure: no IMGUI key for the other games",
              "EnableIMGUI" not in read_file(adapter_unity.configure(ugui, "en", "it")))


# keys that PatchSmith forces on XUnity: {section: {key: value}} (add new ones here)
GOOGLE_KEYS = {
    "Service": {"Endpoint": "GoogleTranslateV2", "FallbackEndpoint": ""},
    "General": {"Language": "it", "FromLanguage": "en"},
    "Behaviour": {"MaxCharactersPerTranslation": "500", "EnableBatching": "False",
                  # XUnity's debug options always off (point 2: the after-play check turns them back on)
                  "EnableTextPathLogging": "False", "EnableTranslationHelper": "False", "CopyToClipboard": "False"},
}
# with PatchSmith's translator (point 3): no XUnity anti-spam check, texts up to 2500 characters
CUSTOM_KEYS = {"EnableShortDelay": "True", "DisableSpamChecks": "True"}


def test_config_values(base):
    v = adapter_unity._config_values("en", "it")
    check("XUnity with Google: expected keys", v == GOOGLE_KEYS)
    check("XUnity: EnableBatching=False (batched requests to Google fail and double the wait)",
              v["Behaviour"]["EnableBatching"] == "False")
    v = adapter_unity._config_values("en", "it", imgui=True)
    check("XUnity with IMGUI: EnableIMGUI=True", v.get("TextFrameworks") == {"EnableIMGUI": "True"})
    url = "http://127.0.0.1:47123/translate"
    v = adapter_unity._config_values("ja", "it", url=url)
    check("XUnity with PatchSmith's translator: CustomTranslate, address, no waits or anti-spam, "
              "long texts, no fallback to Google",
              v["Service"] == {"Endpoint": "CustomTranslate", "FallbackEndpoint": ""}
              and v["Custom"] == dict(CUSTOM_KEYS, Url=url)
              and v["Behaviour"]["MaxCharactersPerTranslation"] == "2500"
              and v["General"] == {"Language": "it", "FromLanguage": "ja"} and "TextFrameworks" not in v)
    v = adapter_unity._config_values("en", "it", google=False)
    check("XUnity without PatchSmith's translator and with Google paused: empty Endpoint (no translator)",
              v["Service"]["Endpoint"] == "" and "Custom" not in v)
    # in the file: all the keys, only once, in their section
    game = os.path.join(base, "config-xunity")
    os.makedirs(game)
    text = read_file(adapter_unity.configure(game, "en", "it", imgui=True, url=url))
    expectations = adapter_unity._config_values("en", "it", imgui=True, url=url)
    check("configure: every key written once in its section", all(
        _section(text, sec).count("%s=%s" % (k, val)) == 1 for sec, keys in expectations.items()
        for k, val in keys.items()))


# --- Unity Localization: bundle CRC ------------------------------------------------------------------

def _catalog(folder, extra):
    """Addressables catalog.json next to the bundle folder; extra: bytes of m_ExtraDataString."""
    write(os.path.join(folder, "catalog.json"), json.dumps({
        "m_LocatorId": "AddressablesMainContentCatalog", "m_InternalIds": [],
        "m_ExtraDataString": base64.b64encode(extra).decode("ascii")}))
    bundle = os.path.join(folder, "StandaloneWindows64", "localization-string-tables-english(en)_assets_all.bundle")
    write(bundle, "")
    return bundle


def test_crc(base):
    options = '{"m_Hash":"8f2c","m_Crc":%d,"m_Timeout":0,"m_ChunkedTransfer":false}'
    with_crc = _catalog(os.path.join(base, "crc-utf16"), b"\x07\x00" + (options % 2737410034).encode("utf-16-le"))
    check("CRC: hash checked (UTF-16, as Unity writes it) -> yes",
              adapter_unity_loc.checked_crc(with_crc) is True)
    odd = _catalog(os.path.join(base, "crc-odd"), b"\x07" + (options % 2737410034).encode("utf-16-le"))
    check("CRC: hash checked also with the UTF-16 text at an odd position -> yes",
              adapter_unity_loc.checked_crc(odd) is True)
    utf8 = _catalog(os.path.join(base, "crc-utf8"), (options % 12345).encode("utf-8"))
    check("CRC: hash checked (plain text) -> yes", adapter_unity_loc.checked_crc(utf8) is True)
    zero = _catalog(os.path.join(base, "crc-zero"), (options % 0).encode("utf-16-le") + (options % 0).encode("utf-8"))
    check("CRC: m_Crc 0 (not checked) -> no", adapter_unity_loc.checked_crc(zero) is False)
    unlabeled = _catalog(os.path.join(base, "crc-no-options"), b"")
    check("CRC: no options -> no", adapter_unity_loc.checked_crc(unlabeled) is False)
    no_live = os.path.join(base, "crc-no-catalog", "StandaloneWindows64", "x.bundle")
    write(no_live, "")
    check("CRC: missing catalog -> unknown (None)", adapter_unity_loc.checked_crc(no_live) is None)
    broken = _catalog(os.path.join(base, "crc-broken"), b"")
    write(os.path.join(base, "crc-broken", "catalog.json"), "{not json")
    check("CRC: unreadable catalog -> unknown (None)", adapter_unity_loc.checked_crc(broken) is None)
    broken_base64 = _catalog(os.path.join(base, "crc-base64"), b"")
    write(os.path.join(base, "crc-base64", "catalog.json"), json.dumps({"m_ExtraDataString": "@@@ not base64 @@@"}))
    check("CRC: extra data not in base64 -> unknown (None)",
              adapter_unity_loc.checked_crc(broken_base64) is None)


# --- number check: '2.5' -----------------------------------------------------------------------------

def test_decimals():
    from patchsmith import corrections
    from patchsmith.project import _to_translate
    check("numbers with k/M/B: '50k' = '50.000', '1.5M' = '1.500.000' (no warning); '5m' is not millions; a "
              "different number says which one",
              not backend.translation_problems("Delivers 50k iridium", "Consegna 50.000 iridio", "it")
              and not corrections.warnings("Delivers 50k iridium\nto win", "Consegna 50.000 iridio\nper vincere")
              and not corrections.warnings("Speed 1.5M", "Velocita' 1.500.000")
              and not corrections.warnings("Run 5m", "Corri 5 metri")
              and corrections.warnings("Delivers 50k iridium", "Consegna 5.000 iridio")
              == ["different numbers from the original (missing 50k; extra 5.000)"])
    check("numbers with symbols and units ('$5k', '+$0,01/s', 'x 2', 'v1.0') are not translated; words are "
              "('2/3 artifact set pieces', 'OK', 'Level 1')",
              not any(_to_translate(t) for t in ("$5k", "+$0,01/s", "x 2", "1920 x 1080", "v1.0", "$200m"))
              and all(_to_translate(t) for t in ("2/3 artifact set pieces", "OK", "Go", "Level 1")))
    try:
        missing_one = backend._numbers_in_words("Wait 2.5 seconds", "Aspetta due secondi e mezzo", "it")
        additional = backend._numbers_in_words("Wait two seconds", "Aspetta 2.5 secondi", "it")
        problems = backend.translation_problems("Wait 2.5 seconds", "Aspetta due secondi e mezzo", "it")
        error = None
    except ValueError as e:  # int('2.5'): One Million Coins stopped here
        missing_one = additional = problems = None
        error = e
    check("'2.5' spelled out: no error, it does not count as a number in words",
              error is None and missing_one is False and additional is False)
    check("'2.5' lost in the translation: rejected as different numbers", problems and "different numbers" in problems)
    check("'2.5' with the Italian decimal comma: good translation",
              not backend.translation_problems("Speed x2.5 for 10 seconds", "Velocità x2,5 per 10 secondi", "it"))
    check("integers in words: still recognized",
              not backend.translation_problems("You have 1 or more mods", "Hai uno o più mod", "it"))


# --- web games: key names -----------------------------------------------------------------------------

def _js_function(source, name):
    """The text of 'function name(...) {...}' (braces counted: in the injector's functions there are none inside
    strings)."""
    start = source.index("function %s(" % name)
    level, i = 0, source.index("{", start)
    while True:
        if source[i] == "{":
            level += 1
        elif source[i] == "}":
            level -= 1
            if level == 0:
                return source[start:i + 1]
        i += 1


def _node():
    located = shutil.which("node")
    if located:
        return located
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base and os.path.exists(os.path.join(base, "nodejs", "node.exe")):
            return os.path.join(base, "nodejs", "node.exe")
    return None


def test_keys(base):
    source = read_file(os.path.join(os.path.dirname(adapter_webui.__file__), "injector.js"))
    classes = re.search(r"var KEY_CLASSES = (\[[^\]]*\]);", source)
    check("injector: key classes 'key' and 'kbd'",
              classes and {"key", "kbd"} <= set(json.loads(classes.group(1))))
    context = _js_function(source, "keyContext")
    tree_node = _js_function(source, "textNode")
    check("injector: <kbd> and key classes up to the grandparent",
              'nodeName === "KBD"' in context and "KEY_CLASSES" in context and "i < 3" in context)
    check("injector: a text inside a key stays in the original", "keyContext(p)" in tree_node)
    check("Delete, Enter, Space, Home stay translatable (elsewhere they are interface words)",
              all(adapter_webui.is_ui_text(t) for t in ("Delete", "Enter", "Space", "Home")))
    check("Esc, Ctrl, Shift, F5: never translated",
              not any(adapter_webui.is_ui_text(t) for t in ("Esc", "Ctrl", "Shift", "F5")))

    node = _node()
    if not node:
        print("(node.js not found: the injector is checked only in the code)")
        return
    ignore = re.search(r"var IGNORE = \{[^}]*\};", source).group(0)
    script = "\n".join([
        'var map = new Map([["Delete", "Elimina"], ["Start Game", "Inizia partita"]]);',
        "var writtenText = new WeakMap();", ignore, classes.group(0),
        _js_function(source, "translate"), context, tree_node,
        "function el(name, classes, parent) { return {nodeType: 1, nodeName: name, parentNode: parent || null,",
        "  isContentEditable: false, hasAttribute: function () { return true; },",
        "  classList: {contains: function (c) { return classes.indexOf(c) >= 0; }}}; }",
        "function probe(text, parent) { var n = {nodeType: 3, nodeValue: text, parentNode: parent}; textNode(n);",
        "  return n.nodeValue; }",
        'var body = el("BODY", []);',
        "console.log(JSON.stringify({",
        '  kbd: probe("Delete", el("KBD", [], body)),',
        '  key_class: probe("Delete", el("SPAN", ["key"], body)),',
        '  inside_kbd: probe("Delete", el("B", [], el("KBD", [], body))),',
        '  button: probe("Delete", el("BUTTON", [], body)),',
        '  similar_class: probe("Delete", el("SPAN", ["keyboard-help"], body)),',
        '  menu: probe("Start Game", el("BUTTON", [], body))}));',
    ])
    path = os.path.join(base, "keys.js")
    write(path, script)
    r = subprocess.run([node, path], capture_output=True, timeout=60)
    try:
        outcome = json.loads(r.stdout.decode("utf-8"))
    except ValueError:
        outcome = {}
        print("   node:", r.stderr.decode("utf-8", "replace")[-500:])
    check("injector (node): 'Delete' in <kbd>, in class=\"key\" and inside a <kbd> stays in the original",
              (outcome.get("kbd"), outcome.get("key_class"), outcome.get("inside_kbd")) == ("Delete",) * 3)
    check("injector (node): 'Delete' on a button or in a merely similar class is translated",
              (outcome.get("button"), outcome.get("similar_class"), outcome.get("menu"))
              == ("Elimina", "Elimina", "Inizia partita"))


# --- real logs for the after-play check ---------------------------------------------------------------

# file -> signals it must contain (regular expressions)
EXPECTED_SIGNALS = {
    "unity-player.log": [r"Mono path\[0\] = '.+_Data/Managed'", r"Chainloader startup complete",
                         r"Loading \[Patchsmith traduttore locale", r"Initialize engine version: 6000\."],
    "unity-player-google-error.log": [r"NullReferenceException", r"GoogleTranslateEndpointV2",
                                       r"\[Error  :XUnity\.AutoTranslator\] Failed: 'MASTER'"],
    "unity-crash-player.log": [r"^Crash!!!$", r"Application:\.cctor", r"GameObject:AddComponent",
                               r"BepInEx\.Bootstrap\.Chainloader:Start", r"Mono path\[0\]"],
    "bepinex-logoutput.log": [r"Chainloader startup complete", r"Loading \[XUnity Auto Translator"],
    # LogOutput.log of the session that ended in the crash: BepInEx started but never reached "startup complete"
    "bepinex-logoutput-crash.log": [r"Chainloader started", r"Loading \[XUnity Auto Translator",
                                    r"\A(?![\s\S]*startup complete)"],
    "xunity-service.log": [r"in ascolto su http://127\.0\.0\.1:\d+/translate \(gioco: ",
                            r"! traduzione interrotta", r"Il gioco si e' chiuso"],
    "unreal-dds.log": [r"Mounting pak file .*zz_Patchsmith_it_999_P\.pak",
                       r"Overriding language with game user settings language configuration option \(en\)"],
    # characters the TextMeshPro fonts lack (also outside the basic plane), IL2CPP game without "Mono path"
    "unity-tmp-missing-chars.log": [r"Unicode value \\u00D9 was not found in the \[slkscr\] font asset",
                                         r"Unicode value \\U0001F41A was not found", r"Discovering subsystems at path",
                                         r"\A(?![\s\S]*Mono path)"],
}
# personal data that must not be there: user name in paths, PC name, hardware data
PERSONAL = re.compile(r"Users[\\/](?!giocatore[\\/])|UserName: '(?!giocatore')|DESKTOP-|LogInit: (User|Computer):|"
                       r"VRAM:|Renderer:|7656119\d{10}", re.I)


def test_signals():
    present = sorted(f for f in os.listdir(SIGNALS) if f.endswith(".log"))
    check("real logs: all present", present == sorted(EXPECTED_SIGNALS))
    for name, expected_list in EXPECTED_SIGNALS.items():
        try:
            text = read_file(os.path.join(SIGNALS, name))
        except OSError:
            text = ""
        missing = [a for a in expected_list if not re.search(a, text, re.M)]
        check("%s: signals present%s" % (name, " (missing: %s)" % missing if missing else ""), not missing)
        personal = PERSONAL.findall(text)
        check("%s: anonymous%s" % (name, " (found: %s)" % personal[:3] if personal else ""),
                  text and not personal)
    # the other real signals of the after-play check: Windows event log entries and the translator's journal
    for name in ("event-crash-1000.xml", "event-crash-1026.xml", "journal-requests.jsonl"):
        try:
            with open(os.path.join(SIGNALS, name), "rb") as f:
                text = f.read().decode("utf-8", "replace")
        except OSError:
            text = ""
        personal = PERSONAL.findall(text) + re.findall(r"S-1-5-21-[\d-]+", text)
        check("%s: present and anonymous%s" % (name, " (found: %s)" % personal[:3] if personal else ""),
                  text and not personal)


def main(base):
    base = os.path.abspath(base)
    if os.path.exists(base) and os.listdir(base):
        raise SystemExit("The test folder must be empty: %s" % base)
    os.makedirs(base, exist_ok=True)
    project.DATA_ROOT = os.path.join(base, "data_folder")  # none of these tests should write data, to be safe
    test_entrypoint(base)
    test_imgui(base)
    test_config_values(base)
    test_crc(base)
    test_decimals()
    test_keys(base)
    test_signals()
    failed = [n for n, ok in OUTCOMES if not ok]
    print("\nRESULT: %d/%d checks passed" % (len(OUTCOMES) - len(failed), len(OUTCOMES)))
    return not failed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(0 if main(sys.argv[1]) else 1)
